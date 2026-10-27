"""Calculate/normalize durations and merge calendars without changing input."""

import math
from copy import deepcopy

from config import InputAlgorithm
from src.utils.input_scheduling_check import check_scheduling_input


def _ceil_time(value, context, *, nonnegative=False):
    try:
        numeric = float(value)
        rounded = math.ceil(numeric)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{context}: expected a finite numeric time, got {value!r}") from exc
    if nonnegative and numeric < 0:
        raise ValueError(f"{context}: must be nonnegative")
    return rounded


def merge_downtime(intervals, *, context="downtime"):
    """Return sorted integer intervals, merging overlaps and touching boundaries."""
    if not isinstance(intervals, (list, tuple)):
        raise ValueError(f"{context}: expected a list of [start, end] intervals")
    normalized = []
    for index, interval in enumerate(intervals):
        location = f"{context}[{index}]"
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            raise ValueError(f"{location}: expected [start, end]")
        start = _ceil_time(interval[0], f"{location}.start")
        end = _ceil_time(interval[1], f"{location}.end")
        if start >= end:
            raise ValueError(f"{location}: start must be less than end, got [{start}, {end}]")
        normalized.append([start, end])

    return merge_intervals(normalized)


def merge_intervals(intervals):
    """Merge already valid integer intervals; no validation or rounding is done."""
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def preprocess_scheduling_input(data):
    """Check input and return a copy with integer durations and merged calendars.

    When unit_duration is supplied, machine_duration is calculated from it and
    job quantity before rounding up. Otherwise, normalize the supplied
    machine_duration. Time units remain unchanged. The runner prepares data
    once; scheduling modules assume this step is already complete.
    """
    check_scheduling_input(data)
    normalized = deepcopy(data)
    for job in normalized[InputAlgorithm.JOBS_KEY]:
        quantity = job.get(InputAlgorithm.JOB_QUANTITY_KEY, 0)
        for operation in job[InputAlgorithm.OPERATIONS_KEY]:
            for machine_id, machine_info in operation[InputAlgorithm.OPERATION_DURATIONS_KEY].items():
                context = (
                    f"Job {job[InputAlgorithm.JOB_CODE_KEY]!r}, "
                    f"operation {operation[InputAlgorithm.OPERATION_ID_KEY]!r}, "
                    f"machine {machine_id!r}: machine_duration"
                )
                raw_duration = machine_info.get(InputAlgorithm.OPERATION_MACHINE_DURATION_KEY)
                if InputAlgorithm.OPERATION_UNIT_DURATION_KEY in machine_info:
                    try:
                        raw_duration = float(machine_info[InputAlgorithm.OPERATION_UNIT_DURATION_KEY]) * float(quantity)
                    except (TypeError, ValueError, OverflowError) as exc:
                        raise ValueError(f"{context}: unit_duration and quantity must be numeric") from exc
                duration = _ceil_time(raw_duration, context, nonnegative=True)
                machine_info[InputAlgorithm.OPERATION_MACHINE_DURATION_KEY] = duration

    for machine_id, machine_info in normalized[InputAlgorithm.MACHINES_KEY].items():
        machine_info[InputAlgorithm.DOWNTIME_KEY] = merge_downtime(
            machine_info.get(InputAlgorithm.DOWNTIME_KEY, []),
            context=f"Machine {machine_id!r}: downtime",
        )
    breaks = normalized.setdefault(InputAlgorithm.BREAK_TIMES_KEY, {})
    if not isinstance(breaks, dict):
        raise ValueError("break_times must be a dictionary")
    for operation_id, intervals in breaks.items():
        breaks[operation_id] = merge_downtime(intervals, context=f"Operation {operation_id!r}: break_times")
    return normalized
