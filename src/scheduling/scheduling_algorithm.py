import collections
import os
import time
from copy import deepcopy
from ortools.sat.python import cp_model

from src.utils.logger import logger
from config import InputAlgorithm, OutputAlgorithm, AlgorithmsConfig

class SolutionPrinter(cp_model.CpSolverSolutionCallback):
    """Print intermediate solutions."""
    def __init__(self, variables):
        cp_model.CpSolverSolutionCallback.__init__(self)
        self.variables = variables
        self.__solution_count = 0

    def on_solution_callback(self):
        """Called at each new solution."""
        solution_print = f'Solution {self.__solution_count}: '
        solution_print += f'time = {self.WallTime():.2f}s, '
        solution_print += f'objective = {self.ObjectiveValue():.2f}, '
        solution_print += f'makespan = {self.Value(self.variables[0]):.2f}, '
        solution_print += f'machine_idle_costs = {self.Value(self.variables[1]):.2f}, '
        solution_print += f'makespan_operation_cost = {self.Value(self.variables[2]):.2f}, '
        solution_print += f'machine_balance_cost = {self.Value(self.variables[3]):.2f}, '
        solution_print += f'job_wait_cost = {self.Value(self.variables[4]):.2f}, '
        logger.info(solution_print)
        self.__solution_count += 1

def jobshop_scheduling(data, *, return_status=False, earliest_starts=None, prior_machine_loads=None,
                      previous_operation_ends=None, compact_schedule=False, optional_jobs=False,
                      lexicographic=False, minimum_jobs=0, fixed_schedule=(),
                      metric_machines=None, time_limit_seconds=None, solve_deadline=None):
    """Solve the schedule; optionally return (result, solver status name).

    Input must already be checked and preprocessed by the caller.
    The default return value remains the scheduled data or None.
    earliest_starts maps job codes to the earliest start of their first operation.
    prior_machine_loads maps (operation_id, machine_id) to processing time
    committed in prior batches, excluding breaks and maintenance.
    previous_operation_ends maps job codes to committed predecessor end times
    for precedence and waiting cost across horizontal operation stages.
    compact_schedule fixes existing machine assignments, caps start/end at their
    previous values, and minimizes total starts/ends instead of normal weights.
    optional_jobs selects or omits each complete route with one shared presence.
    lexicographic optimizes job count, then global makespan, then secondary costs.
    Secondary time is capped by the configured seconds/fraction of this call's
    effective budget; its gap tolerance does not apply to primary priorities.
    fixed_schedule contributes outside allocations to global metrics; callers
    must also reserve its intervals in machines and include its raw processing
    in prior_machine_loads. metric_machines supplies original maintenance only.
    time_limit_seconds caps the entire call, including all priority phases;
    solve_deadline is an optional shared absolute time.monotonic() deadline.
    """
    if compact_schedule and optional_jobs:
        raise ValueError("Compaction requires mandatory jobs")
    started = time.monotonic()
    deadline = started + min(
        AlgorithmsConfig.MINUTES_TIMEOUT * 60,
        time_limit_seconds if time_limit_seconds is not None else float("inf"),
    )
    if solve_deadline is not None:
        deadline = min(deadline, solve_deadline)
    if time.monotonic() >= deadline:
        return (None, "UNKNOWN") if return_status else None
    if lexicographic:
        secondary_seconds = AlgorithmsConfig.SCHEDULING_HYBRID_SECONDARY_SECONDS
        secondary_fraction = AlgorithmsConfig.SCHEDULING_HYBRID_SECONDARY_FRACTION
        secondary_gap = AlgorithmsConfig.SCHEDULING_HYBRID_SECONDARY_GAP_LIMIT
        numeric_settings = (secondary_seconds, secondary_fraction, secondary_gap)
        if not (all(isinstance(value, (int, float)) and not isinstance(value, bool)
                    for value in numeric_settings) and
                0 < secondary_seconds < float("inf") and 0 < secondary_fraction <= 1
                and 0 <= secondary_gap < 1):
            raise ValueError("Invalid Hybrid secondary time budget or gap limit")
        secondary_budget = min(secondary_seconds, (deadline - started) * secondary_fraction)
    data = deepcopy(data)
    model = cp_model.CpModel()

    # Extract data from the input
    horizon = data[InputAlgorithm.HORIZON_KEY]
    machines_data = data[InputAlgorithm.MACHINES_KEY]
    jobs_data = data[InputAlgorithm.JOBS_KEY]
    break_time_data = data[InputAlgorithm.BREAK_TIMES_KEY]

    # Global storage
    intervals_per_resources = collections.defaultdict(list) # indexed by alt_id.
    end_intervals_per_resources = collections.defaultdict(list) # indexed by (operation_id, alt_id).
    presences_per_machine = collections.defaultdict(list) # indexed by machine_id.
    durations_per_machine = collections.defaultdict(list) # indexed by machine_id.
    ends_per_machine = collections.defaultdict(list) # indexed by machine_id.
    processing_per_resource = collections.defaultdict(list) # indexed by (operation_id, machine_id).

    starts = {}  # indexed by (job_code, operation_id).
    durations = {}  # indexed by (job_code, operation_id).
    ends = {}  # indexed by (job_code, operation_id).
    presences = {}  # indexed by (job_code, operation_id, alt_id).
    active_status_per_machine = {}  # indexed by machine_id.
    job_ends = []
    job_wait_terms = []
    job_presences = {}

    for job in jobs_data:
        job_code = job[InputAlgorithm.JOB_CODE_KEY]
        selected = model.new_bool_var(f"job_selected_{job_code}") if optional_jobs else model.new_constant(1)
        job_presences[job_code] = selected
        operations = job.get(InputAlgorithm.OPERATIONS_KEY, [])
        
        previous_end = None
        for operation in operations:
            operation_id = operation.get(InputAlgorithm.OPERATION_ID_KEY)
            operation_deadline = operation.get(InputAlgorithm.OPERATION_DEADLINE_KEY)
            all_alternatives = operation.get(InputAlgorithm.OPERATION_DURATIONS_KEY, {})
            committed = operation.get(OutputAlgorithm.SOLUTION_OPERATIONS_KEY)
            if compact_schedule:
                chosen_machine = committed[InputAlgorithm.MACHINE_ID_KEY]
                all_alternatives = {chosen_machine: all_alternatives[chosen_machine]}

            # Get information from each machine
            num_alternatives = len(all_alternatives)
            min_duration = min(
                machine_info[InputAlgorithm.OPERATION_MACHINE_DURATION_KEY]
                for machine_info in all_alternatives.values()
            )
            
            # Create main interval for the task
            suffix_name = f'_{job_code}_{operation_id}'

            start = model.new_int_var(0, horizon, "start" + suffix_name)
            duration = model.new_int_var(
                0 if optional_jobs else min_duration, horizon, "duration" + suffix_name
            )
            end = model.new_int_var(0, horizon, "end" + suffix_name)
            if optional_jobs:
                interval = model.new_optional_interval_var(start, duration, end, selected, "interval" + suffix_name)
                for variable in (start, duration, end):
                    model.add(variable == 0).only_enforce_if(~selected)
            else:
                interval = model.new_interval_var(start, duration, end, "interval" + suffix_name)
            if compact_schedule:
                model.add(start <= committed[OutputAlgorithm.SOLUTION_START_KEY])
                model.add(end <= committed[OutputAlgorithm.SOLUTION_END_KEY])
                model.add_hint(start, committed[OutputAlgorithm.SOLUTION_START_KEY])
                model.add_hint(duration, committed[OutputAlgorithm.SOLUTION_DURATION_KEY])
                model.add_hint(end, committed[OutputAlgorithm.SOLUTION_END_KEY])

            # Add Constraints: Cross Break Time
            extented_break_time = 0
            break_time_before_deadline = 0
            for bt_interval in break_time_data.get(operation_id, []):
                bt_start, bt_end = bt_interval
                bt_duration = bt_end - bt_start

                across = model.new_bool_var(f"across_{job_code}_{operation_id}_{bt_start}")
                start_before = model.new_bool_var(f"start_before_{job_code}_{operation_id}_{bt_start}")
                end_before = model.new_bool_var(f"end_before_{job_code}_{operation_id}_{bt_start}")

                # Preserve the calendar's boundary rules: start < break_start
                # or >= break_end; end <= break_start or > break_end.
                model.add(start < bt_start).only_enforce_if([selected, start_before])
                model.add(start >= bt_end).only_enforce_if([selected, ~start_before])
                model.add(end <= bt_start).only_enforce_if([selected, end_before])
                model.add(end > bt_end).only_enforce_if([selected, ~end_before])

                # A selected interval crosses the break iff it starts before
                # and ends after it. Omitted jobs add no break extension.
                model.add_bool_and([selected, start_before, ~end_before]).only_enforce_if(across)
                model.add_bool_or([~selected, ~start_before, end_before]).only_enforce_if(~across)

                # Total break time
                extented_break_time += across * bt_duration
                if operation_deadline is not None:
                    # Starts/ends cannot lie inside a break, so a crossed break
                    # contributes this fixed portion before the deadline.
                    before_deadline = max(0, min(bt_end, operation_deadline) - bt_start)
                    break_time_before_deadline += across * before_deadline

            # Store the start for the solution
            starts[(job_code, operation_id)] = start
            durations[(job_code, operation_id)] = duration
            ends[(job_code, operation_id)] = end

            # Constraint: Add precedence with previous task in the same job
            predecessor_end = previous_end
            if predecessor_end is None and previous_operation_ends is not None:
                predecessor_end = previous_operation_ends.get(job_code)
            if predecessor_end is not None:
                model.add(start >= predecessor_end).only_enforce_if(selected)
                if AlgorithmsConfig.SCHEDULING_JOB_WAIT_WEIGHT > 0:
                    wait = model.new_int_var(0, horizon, "wait" + suffix_name)
                    model.add(wait == start - predecessor_end).only_enforce_if(selected)
                    model.add(wait == 0).only_enforce_if(~selected)
                    job_wait_terms.append(wait)
            if previous_end is None and earliest_starts is not None:
                model.add(start >= earliest_starts.get(job_code, 0)).only_enforce_if(selected)
            
            # Store previous end
            previous_end = end
            
            # At least 70% of processing (excluding breaks) must be done by
            # the deadline. Cap elapsed time at end for already finished tasks.
            if operation_deadline is not None:
                model.add(start <= operation_deadline).only_enforce_if(selected)
                end_by_deadline = model.new_int_var(min(0, operation_deadline), horizon, "end_by_deadline" + suffix_name)
                model.add_min_equality(end_by_deadline, [end, operation_deadline])
                completed_work = cp_model.LinearExpr.sum([end_by_deadline, -start, -break_time_before_deadline])
                processing_time = cp_model.LinearExpr.sum([duration, -extented_break_time])
                model.add(10 * completed_work >= 7 * processing_time).only_enforce_if(selected)
            
            # Constraint: Flexible machine assignment
            if num_alternatives > 1:
                l_presences = []
                for alt_id, alt_info in all_alternatives.items():
                    alt_suffix = f"_{job_code}_{operation_id}_{alt_id}"
                    l_presence = model.new_bool_var("presence" + alt_suffix)
                    raw_duration = alt_info.get(InputAlgorithm.OPERATION_MACHINE_DURATION_KEY)
                    l_duration = model.new_int_var(0, horizon, "duration" + alt_suffix)
                    assigned_end = model.new_int_var(0, horizon, "assigned_end" + alt_suffix)
                    l_presences.append(l_presence)

                    # Duration includes extended break time when assigned to this machine
                    model.add(l_duration == raw_duration + extented_break_time).only_enforce_if(l_presence)
                    model.add(l_duration == 0).only_enforce_if(l_presence.Not())

                    # Optional intervals share the operation's timing. Metric
                    # contributions remain zero on machines that are not chosen.
                    model.add(duration == l_duration).only_enforce_if(l_presence)
                    model.add(assigned_end == end).only_enforce_if(l_presence)
                    model.add(assigned_end == 0).only_enforce_if(~l_presence)

                    l_interval = model.new_optional_interval_var(
                        start, duration, end, l_presence, "interval" + alt_suffix
                    )

                    # Add the local interval to the right machine
                    intervals_per_resources[alt_id].append(l_interval)

                    # Store the solution
                    end_intervals_per_resources[(operation_id, alt_id)].append(assigned_end)
                    durations_per_machine[alt_id].append(l_duration)
                    ends_per_machine[alt_id].append(assigned_end)

                    # Store the presences for the solution
                    presences[(job_code, operation_id, alt_id)] = l_presence
                    presences_per_machine[alt_id].append(l_presence)
                    processing_per_resource[(operation_id, alt_id)].append(raw_duration * l_presence)

                # Select exactly one presence variable
                model.add(sum(l_presences) == selected)
            else:
                alt_id = list(all_alternatives.keys())[0]
                alt_duration = all_alternatives[alt_id].get(InputAlgorithm.OPERATION_MACHINE_DURATION_KEY)
                one_l_presence = selected
                model.add(duration == alt_duration + extented_break_time).only_enforce_if(selected)

                # Store the solution
                end_intervals_per_resources[(operation_id, alt_id)].append(end)
                durations_per_machine[alt_id].append(duration)
                ends_per_machine[alt_id].append(end)

                intervals_per_resources[alt_id].append(interval)

                # Store the presences for the solution
                presences[(job_code, operation_id, alt_id)] = one_l_presence
                presences_per_machine[alt_id].append(one_l_presence)
                processing_per_resource[(operation_id, alt_id)].append(alt_duration * selected)
        
        if previous_end is not None:
            job_ends.append(previous_end)
    
    fixed_machines = set()
    for fixed_job in fixed_schedule:
        previous_end = None
        for operation in fixed_job[InputAlgorithm.OPERATIONS_KEY]:
            task = operation[OutputAlgorithm.SOLUTION_OPERATIONS_KEY]
            machine_id = task[InputAlgorithm.MACHINE_ID_KEY]
            fixed_machines.add(machine_id)
            durations_per_machine[machine_id].append(task[OutputAlgorithm.SOLUTION_DURATION_KEY])
            ends_per_machine[machine_id].append(task[OutputAlgorithm.SOLUTION_END_KEY])
            end_intervals_per_resources[(operation[InputAlgorithm.OPERATION_ID_KEY], machine_id)].append(
                task[OutputAlgorithm.SOLUTION_END_KEY])
            if previous_end is not None and AlgorithmsConfig.SCHEDULING_JOB_WAIT_WEIGHT > 0:
                job_wait_terms.append(task[OutputAlgorithm.SOLUTION_START_KEY] - previous_end)
            previous_end = task[OutputAlgorithm.SOLUTION_END_KEY]
        if previous_end is not None:
            job_ends.append(previous_end)

    # Add Constraint: Downtime in machines
    for machine_id, machine_info in machines_data.items():
        downtime = machine_info.get(InputAlgorithm.DOWNTIME_KEY, [])
        for idx, downtime_info in enumerate(downtime):
            downtime_start = downtime_info[0]
            downtime_end = downtime_info[1]
            downtime_duration = downtime_end - downtime_start
            _start = model.new_constant(downtime_start)
            _end = model.new_constant(downtime_end)
            _duration = model.new_constant(downtime_duration)

            planned_duration = model.new_interval_var(
                _start, _duration, _end, f"planned_duration_{machine_id}_{idx}"
            )
            intervals_per_resources[machine_id].append(planned_duration)
                
    # Add Constraint: No Overlap on Machines
    for machine_id, intervals in intervals_per_resources.items():
        model.add_no_overlap(intervals)

    for machine_id in machines_data.keys():
        presences_list = presences_per_machine.get(machine_id, [])
        active_status = model.new_bool_var(f"active_{machine_id}")
        if machine_id in fixed_machines:
            model.add(active_status == 1)
        elif presences_list:
            model.add(sum(presences_list) >= 1).only_enforce_if(active_status)
            model.add(sum(presences_list) == 0).only_enforce_if(active_status.Not())
        else:
            model.add(active_status == 0)
        active_status_per_machine[machine_id] = active_status

    # Calculate machine idle cost
    machine_idle_costs = model.new_constant(0)
    for machine_id in machines_data.keys():
        list_duration = durations_per_machine.get(machine_id, [])
        list_end = ends_per_machine.get(machine_id, [])
        active_status = active_status_per_machine[machine_id]

        if list_duration:
            max_end = model.new_int_var(0, horizon, f"max_end_machine_{machine_id}")
            model.add_max_equality(max_end, list_end + [0])
            
            # Prior batches and maintenance already block the machine. Only
            # count their union within [0, max_end], not future blocked time.
            blocked_times = []
            for index, (blocked_start, blocked_end) in enumerate(
                (metric_machines if metric_machines is not None else machines_data)[machine_id].get(InputAlgorithm.DOWNTIME_KEY, [])
            ):
                blocked_start = max(0, blocked_start)
                blocked_end = min(horizon, blocked_end)
                if blocked_start >= blocked_end:
                    continue
                clipped_end = model.new_int_var(0, blocked_end, f"blocked_end_{machine_id}_{index}")
                model.add_min_equality(clipped_end, [max_end, blocked_end])
                blocked_time = model.new_int_var(
                    0, blocked_end - blocked_start, f"blocked_time_{machine_id}_{index}"
                )
                model.add_max_equality(blocked_time, [clipped_end - blocked_start, 0])
                blocked_times.append(blocked_time)

            total_work = sum(list_duration)
            idle_time = cp_model.LinearExpr.sum([max_end, -total_work, -sum(blocked_times)])
            machine_idle_cost = model.new_int_var(0, horizon, f"idle_cost_{machine_id}")
            model.add(machine_idle_cost == idle_time).only_enforce_if(active_status)
            model.add(machine_idle_cost == 0).only_enforce_if(active_status.Not())
            machine_idle_costs += machine_idle_cost
    
    # Calculate makespan operation cost
    makespan_operation_cost = model.new_constant(0)
    for (operation_id, machine_id), end_list in end_intervals_per_resources.items():
        if end_list:
            max_end = model.new_int_var(0, horizon, f"max_end_op_{operation_id}_{machine_id}")
            model.add_max_equality(max_end, end_list + [0])
            makespan_operation_cost += max_end

    # Makespan objective
    makespan = model.new_int_var(0, horizon, AlgorithmsConfig.TIME_LINE_OPTIMIZE)
    model.add_max_equality(makespan, job_ends + [0])

    # Balance cumulative processing time within each operation's machine pool.
    # Include unused eligible machines, but never count breaks or downtime as work.
    machine_balance_cost = model.new_constant(0)
    if AlgorithmsConfig.SCHEDULING_MACHINE_BALANCE_WEIGHT > 0:
        prior_machine_loads = prior_machine_loads or {}
        current_operations = {operation_id for operation_id, _ in processing_per_resource}
        resources = dict.fromkeys(processing_per_resource)
        resources.update({key: None for key in prior_machine_loads if lexicographic or key[0] in current_operations})
        loads_by_operation = collections.defaultdict(list)
        for operation_id, machine_id in resources:
            resource = (operation_id, machine_id)
            load = model.new_int_var(0, horizon, f"processing_load_{operation_id}_{machine_id}")
            model.add(load == prior_machine_loads.get(resource, 0) + sum(processing_per_resource.get(resource, [])))
            loads_by_operation[operation_id].append(load)
        for operation_id, loads in loads_by_operation.items():
            if len(loads) < 2:
                continue
            max_load = model.new_int_var(0, horizon, f"max_processing_load_{operation_id}")
            min_load = model.new_int_var(0, horizon, f"min_processing_load_{operation_id}")
            model.add_max_equality(max_load, loads)
            model.add_min_equality(min_load, loads)
            machine_balance_cost += max_load - min_load

    job_wait_cost = cp_model.LinearExpr.sum(job_wait_terms) if job_wait_terms else model.new_constant(0)
    secondary_cost = (
        AlgorithmsConfig.SCHEDULING_MACHINE_IDLE_WEIGHT * machine_idle_costs +
        AlgorithmsConfig.SCHEDULING_MAKESPAN_OPERATION_WEIGHT * makespan_operation_cost +
        AlgorithmsConfig.SCHEDULING_MACHINE_BALANCE_WEIGHT * machine_balance_cost +
        AlgorithmsConfig.SCHEDULING_JOB_WAIT_WEIGHT * job_wait_cost
    )
    job_count = sum(job_presences.values())
    if lexicographic:
        model.add(job_count >= minimum_jobs)
        objectives = [("jobs", job_count, True), ("makespan", makespan, False),
                      ("secondary", secondary_cost, False)]
    else:
        objective = (cp_model.LinearExpr.sum(list(starts.values()) + list(ends.values()))
                     if compact_schedule else AlgorithmsConfig.SCHEDULING_MAKESPAN_WEIGHT * makespan + secondary_cost)
        objectives = [("schedule", objective, False)]

    if optional_jobs:
        # Start repair from the surviving schedule. Failed jobs are hinted absent
        # but can become selected during the count priority.
        for job in jobs_data:
            code = job[InputAlgorithm.JOB_CODE_KEY]
            committed_route = all(OutputAlgorithm.SOLUTION_OPERATIONS_KEY in op
                                  for op in job[InputAlgorithm.OPERATIONS_KEY])
            model.add_hint(job_presences[code], int(committed_route))
            for operation in job[InputAlgorithm.OPERATIONS_KEY]:
                op_id = operation[InputAlgorithm.OPERATION_ID_KEY]
                task = operation.get(OutputAlgorithm.SOLUTION_OPERATIONS_KEY) if committed_route else None
                for variables, key in ((starts, OutputAlgorithm.SOLUTION_START_KEY),
                                       (durations, OutputAlgorithm.SOLUTION_DURATION_KEY),
                                       (ends, OutputAlgorithm.SOLUTION_END_KEY)):
                    model.add_hint(variables[(code, op_id)], task[key] if task else 0)
                for machine in operation[InputAlgorithm.OPERATION_DURATIONS_KEY]:
                    presence = presences[(code, op_id, machine)]
                    if presence.index != job_presences[code].index:
                        model.add_hint(presence, int(bool(task and task[InputAlgorithm.MACHINE_ID_KEY] == machine)))

    solver = cp_model.CpSolver()
    solver.parameters.relative_gap_limit = 0 if compact_schedule or lexicographic else AlgorithmsConfig.SCHEDULING_RELATIVE_GAP_LIMIT
    solver.parameters.num_workers = getattr(AlgorithmsConfig, 'NUM_WORKERS', os.cpu_count() or 8)
    solution_printer = SolutionPrinter([
        makespan, machine_idle_costs, makespan_operation_cost, machine_balance_cost, job_wait_cost
    ])

    def extract_solution():
        solution_jobs = []
        for job in deepcopy(jobs_data):
            code = job[InputAlgorithm.JOB_CODE_KEY]
            if not solver.boolean_value(job_presences[code]):
                continue
            for operation in job[InputAlgorithm.OPERATIONS_KEY]:
                op_id = operation[InputAlgorithm.OPERATION_ID_KEY]
                alternatives = operation[InputAlgorithm.OPERATION_DURATIONS_KEY]
                if compact_schedule:
                    chosen = operation[OutputAlgorithm.SOLUTION_OPERATIONS_KEY][InputAlgorithm.MACHINE_ID_KEY]
                    alternatives = {chosen: alternatives[chosen]}
                machine = next(m for m in alternatives if solver.boolean_value(presences[(code, op_id, m)]))
                operation[OutputAlgorithm.SOLUTION_OPERATIONS_KEY] = {
                    InputAlgorithm.MACHINE_ID_KEY: machine,
                    OutputAlgorithm.SOLUTION_START_KEY: solver.value(starts[(code, op_id)]),
                    OutputAlgorithm.SOLUTION_DURATION_KEY: solver.value(durations[(code, op_id)]),
                    OutputAlgorithm.SOLUTION_END_KEY: solver.value(ends[(code, op_id)]),
                }
            solution_jobs.append(job)
        result = deepcopy(data)
        result[OutputAlgorithm.SOLUTION_KEY] = {InputAlgorithm.JOBS_KEY: solution_jobs}
        return result

    best_result = None
    best_status = "UNKNOWN"
    priorities_optimal = True
    for phase_index, (phase, objective, maximize) in enumerate(objectives):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if best_result is not None and lexicographic:
                best_status = "FEASIBLE"
            break
        if maximize:
            model.maximize(objective)
        else:
            model.minimize(objective)
        # Share the remaining time across unfinished priorities so a hard count
        # phase cannot consume the whole neighborhood budget.
        phases_left = len(objectives) - phase_index
        solver.parameters.max_time_in_seconds = remaining / phases_left
        if phase == "secondary":
            solver.parameters.max_time_in_seconds = min(remaining, secondary_budget)
            solver.parameters.relative_gap_limit = secondary_gap
        status = solver.Solve(model, solution_printer)
        if status == cp_model.MODEL_INVALID and lexicographic:
            raise RuntimeError("Invalid scheduling model")
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            logger.warning("Scheduling phase %s returned %s; retaining last feasible solution", phase, solver.StatusName(status))
            if best_result is not None and lexicographic:
                best_status = "FEASIBLE"
            elif best_result is None:
                best_status = solver.StatusName(status)
            break
        best_result = extract_solution()
        # CP-SAT may report OPTIMAL after satisfying a nonzero relative gap.
        # Only an objective matching its bound certifies an exact optimum.
        priorities_optimal = (priorities_optimal and status == cp_model.OPTIMAL and
                              abs(solver.objective_value - solver.best_objective_bound) < 1e-6)
        best_status = "OPTIMAL" if priorities_optimal else "FEASIBLE"
        if lexicographic:
            value = solver.value(objective)
            logger.info("Hybrid priority %s: best found %s (%s)", phase, value, best_status)
            model.add(objective == value)
            # Carry an entire feasible assignment into the next priority.
            model.clear_hints()
            for index in range(len(model.proto.variables)):
                variable = model.get_int_var_from_proto_index(index)
                model.add_hint(variable, solver.value(variable))
    if best_result is not None:
        return (best_result, best_status) if return_status else best_result
    return (None, best_status) if return_status else None
