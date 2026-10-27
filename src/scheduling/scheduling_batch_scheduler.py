from copy import deepcopy
from graphlib import CycleError, TopologicalSorter

from src.scheduling.scheduling_algorithm import jobshop_scheduling
from src.utils.logger import logger
from src.utils.input_scheduling_preprocess import merge_intervals
from config import AlgorithmsConfig, InputAlgorithm, OutputAlgorithm

def split_jobs_into_batches(jobs, num_jobs_per_batch=1):
    """
    Split jobs into batches for processing.
    
    Parameters:
    -----------
    jobs : list
        List of job dictionaries to be processed
    num_jobs_per_batch : int, default=1
        Number of jobs to include in each batch
    
    Returns:
    --------
    batch_data : dict
        Dictionary containing the batch data with the same structure as input_data
    remaining_jobs : list
        List of remaining jobs to be processed in future batches
    num_jobs_left : int
        Number of jobs left to process
    """
    # Take only the first N jobs for this batch
    batch_jobs = jobs[:num_jobs_per_batch]
    remaining_jobs = jobs[num_jobs_per_batch:]
    num_jobs_left = len(remaining_jobs)
    
    # Create a new data structure for this batch
    batch_data = {
        "jobs": batch_jobs
    }
    
    return batch_data, remaining_jobs, num_jobs_left

def update_machine_downtime(machines, schedule):
    """
    Update machine downtime based on the latest batch schedule.
    Adds occupied time slots as downtime for the next batch.
    
    Parameters:
    -----------
    machines : dict
        Dictionary of machines and their current downtime
    schedule : list
        List of scheduled tasks from the last batch
    """
    # Create machine-based index of scheduled tasks
    machine_schedules = {}
    for job in schedule:
        for operation in job[InputAlgorithm.OPERATIONS_KEY]:
            operation_solution = operation[OutputAlgorithm.SOLUTION_OPERATIONS_KEY]
            machine_id = operation_solution[InputAlgorithm.MACHINE_ID_KEY]

            if machine_id not in machine_schedules:
                machine_schedules[machine_id] = []

            machine_schedules[machine_id].append({
                "start": operation_solution.get(OutputAlgorithm.SOLUTION_START_KEY),
                "end": operation_solution.get(OutputAlgorithm.SOLUTION_END_KEY)
            })
    
    # Update downtime for each machine
    for machine_id, tasks in machine_schedules.items():
        # Add each scheduled task as a downtime for future batches
        for task in tasks:
            if task["start"] != task["end"]:
                machines[machine_id]["downtime"].append([task["start"], task["end"]])
        
        machines[machine_id]["downtime"] = merge_intervals(machines[machine_id]["downtime"])
        logger.debug(f"Updated machine {machine_id} downtime, now has {len(machines[machine_id]['downtime'])} periods")
            

def scheduling_batch_scheduler(input_data, jobs_per_batch=5, *, batch_direction="vertical", solve_deadline=None):
    """Schedule complete job batches vertically, or operation batches horizontally.

    Input must be checked/preprocessed. Horizontal operation order is derived
    from job routes; conflicting routes cannot be scheduled in global stages.
    """
    if batch_direction not in ("vertical", "horizontal", "hybrid"):
        raise ValueError("batch_direction must be 'vertical', 'horizontal' or 'hybrid'")
    if isinstance(jobs_per_batch, bool) or not isinstance(jobs_per_batch, int) or jobs_per_batch <= 0:
        raise ValueError("jobs_per_batch must be a positive integer")

    if batch_direction == "hybrid":
        from src.scheduling.scheduling_hybrid_scheduler import schedule_hybrid
        return schedule_hybrid(input_data, jobs_per_batch, solve_deadline=solve_deadline)

    machine_loads = {
        (operation[InputAlgorithm.OPERATION_ID_KEY], machine_id): 0
        for job in input_data[InputAlgorithm.JOBS_KEY]
        for operation in job[InputAlgorithm.OPERATIONS_KEY]
        for machine_id in operation[InputAlgorithm.OPERATION_DURATIONS_KEY]
    }
    if batch_direction == "horizontal":
        result = _schedule_horizontal(input_data, jobs_per_batch, machine_loads, solve_deadline=solve_deadline)
    else:
        result = _schedule_job_batches(input_data, jobs_per_batch, prior_machine_loads=machine_loads, solve_deadline=solve_deadline)
    if (batch_direction == "horizontal" and AlgorithmsConfig.SCHEDULING_COMPACT_HORIZONTAL
            and result[OutputAlgorithm.TOTAL_JOBS_KEY]):
        compact_data = deepcopy(input_data)
        compact_data[InputAlgorithm.JOBS_KEY] = result[OutputAlgorithm.SOLUTION_KEY]
        logger.info("Compacting the final horizontal schedule with fixed machine assignments")
        compact_result, status = jobshop_scheduling(compact_data, return_status=True, compact_schedule=True, solve_deadline=solve_deadline)
        if status == "MODEL_INVALID":
            raise RuntimeError("Invalid model while compacting the horizontal schedule")
        if compact_result is not None:
            result[OutputAlgorithm.SOLUTION_KEY] = compact_result[OutputAlgorithm.SOLUTION_KEY][InputAlgorithm.JOBS_KEY]
        else:
            logger.warning("Compaction returned %s; keeping the existing feasible schedule", status)
    if batch_direction == "horizontal" and AlgorithmsConfig.SCHEDULING_RECOVER_HORIZONTAL:
        _recover_horizontal_jobs(input_data, result, machine_loads, solve_deadline=solve_deadline)
    if not result[OutputAlgorithm.TOTAL_JOBS_KEY]:
        logger.warning("No jobs were successfully scheduled!")
        return None
    return result


def _recover_horizontal_jobs(input_data, result, eligible_machine_loads, *, solve_deadline=None):
    """Retry failed complete routes once, preserving every surviving allocation."""
    failed_codes = {
        job[InputAlgorithm.JOB_CODE_KEY]
        for key in (OutputAlgorithm.INFEASIBLE_JOBS_KEY, OutputAlgorithm.UNSCHEDULED_JOBS_KEY)
        for job in result[key]
    }
    if not failed_codes:
        return
    retry_data = deepcopy(input_data)
    retry_data[InputAlgorithm.JOBS_KEY] = [
        job for job in retry_data[InputAlgorithm.JOBS_KEY]
        if job[InputAlgorithm.JOB_CODE_KEY] in failed_codes
    ]
    update_machine_downtime(retry_data[InputAlgorithm.MACHINES_KEY], result[OutputAlgorithm.SOLUTION_KEY])
    machine_loads = dict.fromkeys(eligible_machine_loads, 0)
    _update_machine_loads(machine_loads, result[OutputAlgorithm.SOLUTION_KEY])
    logger.info("Retrying %s failed horizontal jobs as complete routes", len(failed_codes))
    retried = _schedule_job_batches(retry_data, 1, prior_machine_loads=machine_loads, solve_deadline=solve_deadline)
    scheduled_by_code = {
        job[InputAlgorithm.JOB_CODE_KEY]: job
        for job in result[OutputAlgorithm.SOLUTION_KEY] + retried[OutputAlgorithm.SOLUTION_KEY]
    }
    result[OutputAlgorithm.SOLUTION_KEY] = [
        scheduled_by_code[job[InputAlgorithm.JOB_CODE_KEY]]
        for job in input_data[InputAlgorithm.JOBS_KEY]
        if job[InputAlgorithm.JOB_CODE_KEY] in scheduled_by_code
    ]
    for key in (OutputAlgorithm.INFEASIBLE_JOBS_KEY, OutputAlgorithm.UNSCHEDULED_JOBS_KEY):
        result[key] = retried[key]
    result[OutputAlgorithm.TOTAL_JOBS_KEY] = len(scheduled_by_code)
    result[OutputAlgorithm.STATUS_KEY] = (
        "partial" if result[OutputAlgorithm.INFEASIBLE_JOBS_KEY] or result[OutputAlgorithm.UNSCHEDULED_JOBS_KEY]
        else "success"
    )
    logger.info("Recovered %s jobs; final total is %s",
                len(retried[OutputAlgorithm.SOLUTION_KEY]), result[OutputAlgorithm.TOTAL_JOBS_KEY])


def _update_machine_loads(machine_loads, schedule):
    """Accumulate actual processing, excluding calendar extensions."""
    for job in schedule:
        for operation in job[InputAlgorithm.OPERATIONS_KEY]:
            machine_id = operation[OutputAlgorithm.SOLUTION_OPERATIONS_KEY][InputAlgorithm.MACHINE_ID_KEY]
            resource = (operation[InputAlgorithm.OPERATION_ID_KEY], machine_id)
            processing_time = operation[InputAlgorithm.OPERATION_DURATIONS_KEY][machine_id][
                InputAlgorithm.OPERATION_MACHINE_DURATION_KEY
            ]
            machine_loads[resource] = machine_loads.get(resource, 0) + processing_time


def _release_job_resources(machines, machine_loads, job):
    """Subtract this job's reservations from merged calendars and load totals.

    Committed operations do not overlap other reservations or maintenance, so
    subtracting their intervals preserves all other blocked time, even when
    adjacent intervals have already been merged.
    """
    for operation in job[InputAlgorithm.OPERATIONS_KEY]:
        solution = operation.get(OutputAlgorithm.SOLUTION_OPERATIONS_KEY)
        if solution is None:
            continue
        machine_id = solution[InputAlgorithm.MACHINE_ID_KEY]
        start = solution[OutputAlgorithm.SOLUTION_START_KEY]
        end = solution[OutputAlgorithm.SOLUTION_END_KEY]
        if start < end:
            remaining = []
            for blocked_start, blocked_end in machines[machine_id][InputAlgorithm.DOWNTIME_KEY]:
                if blocked_end <= start or blocked_start >= end:
                    remaining.append([blocked_start, blocked_end])
                else:
                    if blocked_start < start:
                        remaining.append([blocked_start, start])
                    if blocked_end > end:
                        remaining.append([end, blocked_end])
            machines[machine_id][InputAlgorithm.DOWNTIME_KEY] = remaining
        resource = (operation[InputAlgorithm.OPERATION_ID_KEY], machine_id)
        processing_time = operation[InputAlgorithm.OPERATION_DURATIONS_KEY][machine_id][
            InputAlgorithm.OPERATION_MACHINE_DURATION_KEY
        ]
        machine_loads[resource] -= processing_time


def _schedule_horizontal(input_data, jobs_per_batch, machine_loads, *, solve_deadline=None):
    jobs = deepcopy(input_data[InputAlgorithm.JOBS_KEY])
    jobs_by_code = {job[InputAlgorithm.JOB_CODE_KEY]: job for job in jobs}
    original_jobs = {
        job[InputAlgorithm.JOB_CODE_KEY]: job for job in input_data[InputAlgorithm.JOBS_KEY]
    }
    predecessors = {}
    operations_by_job = {}
    for job in jobs:
        code = job[InputAlgorithm.JOB_CODE_KEY]
        operations_by_job[code] = {}
        previous_id = None
        for operation in job[InputAlgorithm.OPERATIONS_KEY]:
            operation_id = operation[InputAlgorithm.OPERATION_ID_KEY]
            operations_by_job[code][operation_id] = operation
            predecessors.setdefault(operation_id, set())
            if previous_id is not None:
                predecessors[operation_id].add(previous_id)
            previous_id = operation_id
    try:
        operation_order = list(TopologicalSorter(predecessors).static_order())
    except CycleError as exc:
        raise ValueError("Conflicting operation order across jobs in horizontal mode") from exc

    machines = deepcopy(input_data[InputAlgorithm.MACHINES_KEY])
    earliest_starts = {}
    failed_codes = set()
    infeasible_jobs = []
    unscheduled_jobs = []

    def release_failed_job(failed_job, batch_machines, batch_loads):
        code = failed_job[InputAlgorithm.JOB_CODE_KEY]
        _release_job_resources(machines, machine_loads, jobs_by_code[code])
        _release_job_resources(batch_machines, batch_loads, jobs_by_code[code])
        earliest_starts.pop(code, None)
        logger.info("Released previous operation reservations for failed job %s", code)

    for operation_id in operation_order:
        for offset in range(0, len(jobs), jobs_per_batch):
            job_group = jobs[offset:offset + jobs_per_batch]
            stage_jobs = []
            for job in job_group:
                code = job[InputAlgorithm.JOB_CODE_KEY]
                if code in failed_codes or operation_id not in operations_by_job[code]:
                    continue
                stage_job = deepcopy(job)
                stage_job[InputAlgorithm.OPERATIONS_KEY] = [deepcopy(operations_by_job[code][operation_id])]
                stage_jobs.append(stage_job)
            if not stage_jobs:
                continue
            logger.info("Scheduling horizontal operation %s", operation_id)
            stage_data = {
                InputAlgorithm.HORIZON_KEY: input_data[InputAlgorithm.HORIZON_KEY],
                InputAlgorithm.MACHINES_KEY: machines,
                InputAlgorithm.BREAK_TIMES_KEY: input_data[InputAlgorithm.BREAK_TIMES_KEY],
                InputAlgorithm.JOBS_KEY: stage_jobs,
            }
            stage_result = _schedule_job_batches(
                stage_data, jobs_per_batch,
                earliest_starts=earliest_starts, prior_machine_loads=machine_loads,
                previous_operation_ends=earliest_starts, on_job_failure=release_failed_job,
                solve_deadline=solve_deadline,
            )
            for key, failures in ((OutputAlgorithm.INFEASIBLE_JOBS_KEY, infeasible_jobs),
                                  (OutputAlgorithm.UNSCHEDULED_JOBS_KEY, unscheduled_jobs)):
                for failed in stage_result[key]:
                    code = failed[InputAlgorithm.JOB_CODE_KEY]
                    failed_codes.add(code)
                    failures.append(deepcopy(original_jobs[code]))
            scheduled = stage_result[OutputAlgorithm.SOLUTION_KEY]
            for scheduled_job in scheduled:
                code = scheduled_job[InputAlgorithm.JOB_CODE_KEY]
                operation = scheduled_job[InputAlgorithm.OPERATIONS_KEY][0]
                solution = operation[OutputAlgorithm.SOLUTION_OPERATIONS_KEY]
                operations_by_job[code][operation_id][OutputAlgorithm.SOLUTION_OPERATIONS_KEY] = solution
                earliest_starts[code] = solution[OutputAlgorithm.SOLUTION_END_KEY]
            update_machine_downtime(machines, scheduled)
            _update_machine_loads(machine_loads, scheduled)

    completion_schedule = [job for job in jobs if job[InputAlgorithm.JOB_CODE_KEY] not in failed_codes]
    return {
        OutputAlgorithm.STATUS_KEY: "partial" if failed_codes else "success",
        OutputAlgorithm.TOTAL_JOBS_KEY: len(completion_schedule),
        OutputAlgorithm.INPUT_DATA_KEY: input_data,
        OutputAlgorithm.SOLUTION_KEY: completion_schedule,
        OutputAlgorithm.INFEASIBLE_JOBS_KEY: infeasible_jobs,
        OutputAlgorithm.UNSCHEDULED_JOBS_KEY: unscheduled_jobs,
    }


def _schedule_job_batches(input_data, jobs_per_batch, *, earliest_starts=None, prior_machine_loads=None,
                          previous_operation_ends=None, on_job_failure=None, solve_deadline=None):
    """
    Schedule already checked/preprocessed input using a positive batch size.
    
    Parameters:
    -----------
    input_data : dict
        Dictionary containing horizon, machines, and jobs data
    jobs_per_batch : int, default=5
        Number of jobs to include in each batch
    
    Returns:
    --------
    completion_schedule : dict
        Dictionary containing the complete schedule
    """
    current_data = deepcopy(input_data)
    current_horizon = current_data[InputAlgorithm.HORIZON_KEY]
    current_machines = current_data[InputAlgorithm.MACHINES_KEY]
    current_break_times = current_data[InputAlgorithm.BREAK_TIMES_KEY]
    current_jobs = current_data[InputAlgorithm.JOBS_KEY]
    machine_loads = dict(prior_machine_loads or {})

    # Initialize variables
    completion_schedule = []
    infeasible_jobs = []
    unscheduled_jobs = []

    # Process batches until all jobs are scheduled
    current_batch_size = jobs_per_batch

    while current_jobs:
        # Split jobs into batches
        batch_data, remaining_jobs, num_jobs_left = split_jobs_into_batches(current_jobs, current_batch_size)

        # Add machines with current downtime status to the batch data
        batch_data[InputAlgorithm.HORIZON_KEY] = current_horizon
        batch_data[InputAlgorithm.MACHINES_KEY] = current_machines
        batch_data[InputAlgorithm.BREAK_TIMES_KEY] = current_break_times
        
        logger.info(f"Number of jobs left: {num_jobs_left}")
        # Schedule this batch
        batch_result, solver_status = jobshop_scheduling(
            batch_data, return_status=True, earliest_starts=earliest_starts,
            prior_machine_loads=machine_loads,
            previous_operation_ends=previous_operation_ends, solve_deadline=solve_deadline,
        )

        if batch_result is None:
            if solver_status == "MODEL_INVALID":
                raise RuntimeError("Scheduling scheduling model is invalid; jobs cannot be classified as infeasible")

            if len(batch_data[InputAlgorithm.JOBS_KEY]) > 1:
                current_batch_size = max(1, len(batch_data[InputAlgorithm.JOBS_KEY]) // 2)
                logger.warning(f"Change the batch size to {current_batch_size}: {solver_status}")
            else:
                failed_job = batch_data[InputAlgorithm.JOBS_KEY][0]
                if on_job_failure is not None:
                    on_job_failure(failed_job, current_machines, machine_loads)
                if solver_status == "INFEASIBLE":
                    infeasible_jobs.append(failed_job)
                else:
                    # UNKNOWN (e.g. timeout) does not prove infeasibility.
                    unscheduled_jobs.append(failed_job)
                logger.warning("Skipping job %s: %s", failed_job[InputAlgorithm.JOB_CODE_KEY], solver_status)
                current_jobs = remaining_jobs
                current_batch_size = jobs_per_batch
            continue

        # Process the batch results
        if "solution" in batch_result and "jobs" in batch_result["solution"]:
            # Add the job schedules to the completion schedule
            completion_schedule.extend(batch_result["solution"]["jobs"])
            
            # Update the current jobs
            current_jobs = remaining_jobs
            current_batch_size = jobs_per_batch

            # Update the downtime for the next batch based on this batch's schedule
            update_machine_downtime(current_machines, batch_result["solution"]["jobs"])
            _update_machine_loads(machine_loads, batch_result["solution"]["jobs"])
        else:
            raise RuntimeError("Batch returned incomplete solution")

    
    logger.info(f"Successfully scheduled {len(completion_schedule)} jobs")
    
    # Create final output with complete schedule
    result = {
        OutputAlgorithm.STATUS_KEY: "partial" if infeasible_jobs or unscheduled_jobs else "success",
        OutputAlgorithm.TOTAL_JOBS_KEY: len(completion_schedule),
        OutputAlgorithm.INPUT_DATA_KEY: input_data,
        OutputAlgorithm.SOLUTION_KEY: completion_schedule,
        OutputAlgorithm.INFEASIBLE_JOBS_KEY: infeasible_jobs,
        OutputAlgorithm.UNSCHEDULED_JOBS_KEY: unscheduled_jobs,
    }

    return result
