"""Horizontal seed followed by bounded repair of related complete job routes."""

import math
import time
from collections import defaultdict
from copy import deepcopy

from config import AlgorithmsConfig as C
from src.scheduling.scheduling_algorithm import jobshop_scheduling
from src.scheduling.scheduling_batch_scheduler import (
    _update_machine_loads, scheduling_batch_scheduler, update_machine_downtime,
)
from src.utils.input_scheduling_preprocess import merge_intervals
from src.utils.logger import logger


def schedule_score(input_data, schedule):
    """Compare full schedules: complete jobs, makespan, weighted secondary cost."""
    loads = {
        (op["operation_id"], machine): 0
        for job in input_data["jobs"] for op in job["operations"]
        for machine in op["durations"]
    }
    _update_machine_loads(loads, schedule)
    machine_tasks = defaultdict(list)
    operation_ends = defaultdict(list)
    wait = 0
    makespan = 0
    for job in schedule:
        previous_end = None
        for op in job["operations"]:
            task = op["operations_solution"]
            machine_tasks[task["machine_id"]].append(task)
            operation_ends[(op["operation_id"], task["machine_id"])].append(task["end"])
            makespan = max(makespan, task["end"])
            if previous_end is not None:
                wait += task["start"] - previous_end
            previous_end = task["end"]
    idle = 0
    for machine, tasks in machine_tasks.items():
        end = max(task["end"] for task in tasks)
        blocked = sum(max(0, min(end, b) - max(0, a))
                      for a, b in merge_intervals(input_data["machines"][machine]["downtime"]))
        idle += end - sum(task["duration"] for task in tasks) - blocked
    operation_loads = defaultdict(list)
    for (operation, _), load in loads.items():
        operation_loads[operation].append(load)
    balance = sum(max(values) - min(values) for values in operation_loads.values())
    secondary = (C.SCHEDULING_MACHINE_IDLE_WEIGHT * idle + C.SCHEDULING_JOB_WAIT_WEIGHT * wait +
                 C.SCHEDULING_MACHINE_BALANCE_WEIGHT * balance +
                 C.SCHEDULING_MAKESPAN_OPERATION_WEIGHT * sum(max(ends) for ends in operation_ends.values()))
    return -len(schedule), makespan, secondary


def _job_wait(job):
    tasks = [op["operations_solution"] for op in job["operations"]]
    return sum(right["start"] - left["end"] for left, right in zip(tasks, tasks[1:]))


def _seed_priority(job):
    """Rank the tightest 70%-processing deadline along the entire route first.

    Minimum processing on each operation gives an optimistic slack estimate;
    calendars remain the solver's responsibility. Stable sorting preserves
    input order for ties; routes without deadlines follow constrained routes.
    """
    elapsed = 0
    slack = due = float("inf")
    for operation in job["operations"]:
        processing = min(info["machine_duration"] for info in operation["durations"].values())
        deadline = operation.get("deadline")
        if deadline is not None:
            slack = min(slack, deadline - elapsed - (7 * processing + 9) // 10)
            due = min(due, deadline)
        elapsed += processing
    return slack, due, -elapsed


def select_neighborhood(input_data, schedule, target, group_size):
    """Select the target and jobs occupying its candidate machines/time windows."""
    incumbent = {job["job_code"]: job for job in schedule}
    windows = []
    earliest = 0
    target_route = incumbent.get(target["job_code"], target)
    for op in target_route["operations"]:
        min_processing = min(info["machine_duration"] for info in op["durations"].values())
        # Deadline allows 30% of work to finish later. Broad windows avoid
        # excluding a blocker solely because its processing straddles the limit.
        task = op.get("operations_solution")
        if op.get("deadline") is not None:
            latest = min(input_data["horizon"], op["deadline"] + min_processing)
        elif task:
            earliest = max(0, task["start"] - min_processing)
            latest = min(input_data["horizon"], task["end"] + min_processing)
        else:
            latest = min(input_data["horizon"], earliest + 2 * min_processing)
        windows.append((set(op["durations"]), earliest, latest))
        earliest = task["end"] if task else earliest + min_processing

    def relatedness(job):
        overlap = 0
        shared = 0
        for op in job["operations"]:
            task = op.get("operations_solution")
            for machines, start, end in windows:
                candidates = {task["machine_id"]} if task else set(op["durations"])
                if candidates & machines:
                    shared += 1
                    if task:
                        overlap += max(0, min(end, task["end"]) - max(start, task["start"]))
        return overlap, shared

    others = [incumbent.get(job["job_code"], job) for job in input_data["jobs"]
              if job["job_code"] != target["job_code"]]
    # Stable input order breaks equal scores. Successful blockers are preferred
    # over merely sharing an eligible machine with another unscheduled job.
    others.sort(key=relatedness, reverse=True)
    return [target["job_code"]] + [job["job_code"] for job in others[:group_size - 1]]


def _settings():
    total, fraction, size, round_seconds, rounds = (
        C.SCHEDULING_HYBRID_TOTAL_SECONDS, C.SCHEDULING_HYBRID_SEED_FRACTION,
        C.SCHEDULING_HYBRID_GROUP_SIZE, C.SCHEDULING_HYBRID_ROUND_SECONDS,
        C.SCHEDULING_HYBRID_MAX_ROUNDS,
    )
    if not all(isinstance(value, (int, float)) and not isinstance(value, bool) and
               math.isfinite(value) and value > 0 for value in (total, round_seconds)):
        raise ValueError("Hybrid time budgets must be finite positive numbers")
    if not isinstance(fraction, (int, float)) or isinstance(fraction, bool) or not 0 < fraction < 1:
        raise ValueError("Hybrid seed fraction must be between 0 and 1")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < minimum
           for value, minimum in ((size, 2), (rounds, 1))):
        raise ValueError("Hybrid group size must be >= 2 and max rounds must be >= 1")
    initial_size = C.SCHEDULING_HYBRID_INITIAL_GROUP_SIZE
    if isinstance(initial_size, bool) or not isinstance(initial_size, int) or initial_size < 2:
        raise ValueError("Hybrid initial group size must be >= 2")
    return total, fraction, size, round_seconds, rounds, min(initial_size, size)


def _compact_hybrid_schedule(input_data, schedule, *, solve_deadline):
    """Pack complete routes together, preserving every job and chosen machine.

    Every operation's start/end is capped at its incumbent value. The model
    enforces route precedence, machine calendars, breaks and processing deadlines
    while minimizing the total starts plus ends across all operations. The
    makespan cannot increase; waiting remains dependent on machine availability.
    """
    if not schedule or time.monotonic() >= solve_deadline:
        return schedule
    compact_data = deepcopy(input_data)
    compact_data["jobs"] = deepcopy(schedule)
    result, status = jobshop_scheduling(
        compact_data, compact_schedule=True, return_status=True,
        time_limit_seconds=C.SCHEDULING_HYBRID_ROUND_SECONDS, solve_deadline=solve_deadline,
    )
    if status == "MODEL_INVALID":
        raise RuntimeError("Invalid model while compacting the hybrid schedule")
    if result is None:
        logger.info("Hybrid compaction returned %s; keeping the existing schedule", status)
        return schedule
    candidate = result["solution"]["jobs"]

    def compaction_cost(jobs):
        return sum(op["operations_solution"][key] for job in jobs
                   for op in job["operations"] for key in ("start", "end"))

    before, after = compaction_cost(schedule), compaction_cost(candidate)
    if after < before:
        logger.info("Compacted hybrid routes: total starts/ends %s -> %s; complete jobs and chosen machines preserved",
                    before, after)
        return candidate
    return schedule


def schedule_hybrid(input_data, jobs_per_batch, *, solve_deadline=None):
    total, fraction, size, round_seconds, max_rounds, initial_size = _settings()
    started = time.monotonic()
    deadline = min(started + total, solve_deadline if solve_deadline is not None else float("inf"))
    budget = max(0, deadline - started)
    compact_final_schedule = C.SCHEDULING_COMPACT_HYBRID
    # Reserve at most one round or 10% of the budget for final packing. This
    # remains part of the shared deadline, even when repair hits its time cap.
    reserved = min(round_seconds, budget * 0.1) if compact_final_schedule else 0
    repair_deadline = deadline - reserved
    seed_deadline = min(repair_deadline, started + budget * fraction)
    seed_data = deepcopy(input_data)
    seed_data["jobs"].sort(key=_seed_priority)
    logger.info("Hybrid starting: %s jobs, seed batch size %s, workers %s, total budget %.2fs",
                len(input_data["jobs"]), jobs_per_batch, C.NUM_WORKERS, budget)
    seed_started = time.monotonic()
    seed = scheduling_batch_scheduler(seed_data, jobs_per_batch, batch_direction="horizontal",
                                     solve_deadline=seed_deadline)
    schedule = seed["solution"] if seed else []
    seeded = {job["job_code"]: job for job in schedule}
    schedule = [seeded[job["job_code"]] for job in input_data["jobs"] if job["job_code"] in seeded]
    logger.info("Hybrid seed finished in %.2fs: %s/%s complete jobs",
                time.monotonic() - seed_started, len(schedule), len(input_data["jobs"]))
    score = schedule_score(input_data, schedule)
    attempted = {}
    for round_index in range(max_rounds):
        if time.monotonic() >= repair_deadline:
            break
        by_code = {job["job_code"]: job for job in schedule}
        pending = [job for job in input_data["jobs"] if job["job_code"] not in by_code]
        critical = sorted(schedule, key=lambda job: (job["operations"][-1]["operations_solution"]["end"],
                                                     _job_wait(job)), reverse=True)
        target = next((job for job in pending + critical if attempted.get(job["job_code"], 0) < size), None)
        if target is None:
            break
        previous_size = attempted.get(target["job_code"], 0)
        group_size = min(size, previous_size * 2) if previous_size else initial_size
        # Do not retry an identical group when the input has fewer jobs than
        # the configured maximum. Every expansion consumes one round.
        attempted[target["job_code"]] = size if group_size >= len(input_data["jobs"]) else group_size
        group = set(select_neighborhood(input_data, schedule, target, group_size))
        fixed = [job for job in schedule if job["job_code"] not in group]
        repair_data = deepcopy(input_data)
        repair_data["jobs"] = [deepcopy(by_code.get(job["job_code"], job))
                               for job in input_data["jobs"] if job["job_code"] in group]
        update_machine_downtime(repair_data["machines"], fixed)
        loads = {(op["operation_id"], machine): 0 for job in input_data["jobs"]
                 for op in job["operations"] for machine in op["durations"]}
        _update_machine_loads(loads, fixed)
        result, status = jobshop_scheduling(
            repair_data, return_status=True, optional_jobs=True, lexicographic=True,
            minimum_jobs=sum(code in group for code in by_code), prior_machine_loads=loads,
            fixed_schedule=fixed, metric_machines=input_data["machines"],
            time_limit_seconds=round_seconds, solve_deadline=repair_deadline,
        )
        if result is None:
            logger.info("Hybrid repair %s kept incumbent: %s", round_index + 1, status)
            continue
        proposed = {job["job_code"]: job for job in fixed + result["solution"]["jobs"]}
        candidate = [proposed[job["job_code"]] for job in input_data["jobs"] if job["job_code"] in proposed]
        candidate_score = schedule_score(input_data, candidate)
        if candidate_score < score:
            logger.info("Hybrid repair %s improved score %s -> %s", round_index + 1, score, candidate_score)
            schedule, score = candidate, candidate_score
            attempted.clear()
    else:
        logger.info("Hybrid reached repair round cap %s; %.2fs of repair budget remains",
                    max_rounds, max(0, repair_deadline - time.monotonic()))

    if compact_final_schedule:
        schedule = _compact_hybrid_schedule(input_data, schedule, solve_deadline=deadline)
        score = schedule_score(input_data, schedule)

    selected = {job["job_code"] for job in schedule}
    missing = [deepcopy(job) for job in input_data["jobs"] if job["job_code"] not in selected]
    logger.info("Hybrid finished in %.2fs: %s/%s complete jobs, makespan %s",
                time.monotonic() - started, len(schedule), len(input_data["jobs"]), score[1])
    # Failure against a frozen neighborhood is not a proof of infeasibility
    # against the full input. Preserve that distinction in the public result.
    return {
        "status": "partial" if missing else "success", "total_jobs": len(schedule),
        "input_data": input_data, "solution": schedule,
        "infeasible_jobs": [], "unscheduled_jobs": missing,
    } if schedule else None
