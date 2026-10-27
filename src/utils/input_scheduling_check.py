"""Validate scheduling identifiers and machine alternatives before modeling."""

from config import InputAlgorithm


def check_scheduling_input(data):
    """Raise ValueError for invalid jobs, duplicate identifiers or unknown machines.

    Operation IDs must be unique within a job, but can repeat across jobs.
    This function does not modify the input.
    """
    if not isinstance(data, dict):
        raise ValueError("Scheduling input must be a dictionary")
    machines = data.get(InputAlgorithm.MACHINES_KEY)
    jobs = data.get(InputAlgorithm.JOBS_KEY)
    if not isinstance(machines, dict):
        raise ValueError("machines must be a dictionary")
    if not isinstance(jobs, list):
        raise ValueError("jobs must be a list")
    for machine_id, machine_info in machines.items():
        if not isinstance(machine_info, dict):
            raise ValueError(f"Machine {machine_id!r}: configuration must be a dictionary")

    job_codes = set()
    for index, job in enumerate(jobs):
        if not isinstance(job, dict):
            raise ValueError(f"Job at index {index} must be a dictionary")
        job_code = job.get(InputAlgorithm.JOB_CODE_KEY)
        if not isinstance(job_code, str) or not job_code:
            raise ValueError(f"Job at index {index}: job_code must be a nonempty string")
        if job_code in job_codes:
            raise ValueError(f"Duplicate job_code: {job_code!r}")
        job_codes.add(job_code)

        operations = job.get(InputAlgorithm.OPERATIONS_KEY)
        if not isinstance(operations, list) or not operations:
            raise ValueError(f"Job {job_code!r}: operations must be a nonempty list")
        operation_ids = set()
        for operation in operations:
            if not isinstance(operation, dict):
                raise ValueError(f"Job {job_code!r}: each operation must be a dictionary")
            operation_id = operation.get(InputAlgorithm.OPERATION_ID_KEY)
            if not isinstance(operation_id, str) or not operation_id:
                raise ValueError(f"Job {job_code!r}: operation_id must be a nonempty string")
            if operation_id in operation_ids:
                raise ValueError(f"Job {job_code!r}: duplicate operation_id {operation_id!r}")
            operation_ids.add(operation_id)

            alternatives = operation.get(InputAlgorithm.OPERATION_DURATIONS_KEY)
            context = f"Job {job_code!r}, operation {operation_id!r}"
            if not isinstance(alternatives, dict) or not alternatives:
                raise ValueError(f"{context}: durations must contain at least one machine")
            for machine_id, machine_info in alternatives.items():
                if machine_id not in machines:
                    raise ValueError(f"{context}: unknown machine ID {machine_id!r}")
                if not isinstance(machine_info, dict):
                    raise ValueError(f"{context}, machine {machine_id!r}: duration info must be a dictionary")
