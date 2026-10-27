import json
import random
import copy
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

INPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "algorithm_input"
    / "new_exp.json"
)

OUTPUT_DIR = (
    PROJECT_ROOT
    / "data"
    / "algorithm_input"
)


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------

def load_json(file_path: Path) -> dict:
    if not file_path.exists():
        raise FileNotFoundError(
            f"Input file not found: {file_path}"
        )

    try:
        with file_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Invalid JSON file: {file_path}\n{exc}"
        ) from exc

    required_keys = [
        "horizon",
        "period_start",
        "machines",
        "break_times",
        "jobs",
    ]

    for key in required_keys:
        if key not in data:
            raise ValueError(
                f"Missing required key '{key}' in {file_path}"
            )

    if not isinstance(data["jobs"], list) or not data["jobs"]:
        raise ValueError(
            "'jobs' must be a non-empty list"
        )

    return data


def save_json(data: dict, output_file: Path) -> None:
    output_file.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_file.open(
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            data,
            f,
            indent=4,
            ensure_ascii=False,
        )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_input(data: dict) -> None:
    """
    Check consistency between:
        machines
        break_times
        jobs.operations
        operation durations
    """

    machines = data["machines"]
    break_times = data["break_times"]
    jobs = data["jobs"]

    operation_ids = set()

    for machine_code, machine_data in machines.items():
        operation_id = machine_data.get("operation_id")

        if not operation_id:
            raise ValueError(
                f"Machine '{machine_code}' has no operation_id"
            )

        operation_ids.add(operation_id)

    # Every machine operation should have break_times
    for operation_id in operation_ids:
        if operation_id not in break_times:
            raise ValueError(
                f"Missing break_times for operation "
                f"'{operation_id}'"
            )

    # Validate every job
    for job in jobs:
        job_code = job.get("job_code", "UNKNOWN")

        operations = job.get("operations", [])

        if not operations:
            raise ValueError(
                f"Job '{job_code}' has no operations"
            )

        for operation in operations:
            operation_id = operation.get("operation_id")

            if operation_id not in operation_ids:
                raise ValueError(
                    f"Job '{job_code}' uses unknown operation "
                    f"'{operation_id}'"
                )

            durations = operation.get("durations", {})

            if not durations:
                raise ValueError(
                    f"Job '{job_code}', operation "
                    f"'{operation_id}' has no durations"
                )

            for machine_code in durations:
                if machine_code not in machines:
                    raise ValueError(
                        f"Job '{job_code}', operation "
                        f"'{operation_id}' references unknown "
                        f"machine '{machine_code}'"
                    )

                machine_operation = (
                    machines[machine_code]["operation_id"]
                )

                if machine_operation != operation_id:
                    raise ValueError(
                        f"Machine '{machine_code}' belongs to "
                        f"'{machine_operation}', but job "
                        f"'{job_code}' uses it for "
                        f"'{operation_id}'"
                    )


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

def generate_jobs(
    num_jobs: int = 30,
    seed: int | None = 42,
) -> dict:
    """
    Generate N jobs from templates in new_exp.json.

    machines:
        copied unchanged

    break_times:
        copied unchanged

    job template:
        random from jobs in new_exp.json

    copied unchanged from template:
        parent_code
        deadline
        quantity
        operations
        operation deadlines
        machine durations

    regenerated:
        job_code
        job_name
    """

    if num_jobs <= 0:
        raise ValueError(
            "num_jobs must be greater than 0"
        )

    data = load_json(INPUT_FILE)

    # Validate source before generating
    validate_input(data)

    templates = data["jobs"]

    rng = random.Random(seed)

    generated_jobs = []

    for i in range(1, num_jobs + 1):
        template = rng.choice(templates)

        # Deep copy is important because operations/durations
        # are nested dictionaries.
        job = copy.deepcopy(template)

        job["job_code"] = f"SO_Job_{i}"
        job["job_name"] = f"Job {i}"

        # Keep:
        # parent_code
        # deadline
        # quantity
        # operations
        # durations
        #
        # exactly as defined in selected template.

        generated_jobs.append(job)

    # Deep copy full source so machines / break_times remain unchanged.
    output_data = copy.deepcopy(data)

    output_data["jobs"] = generated_jobs

    # Final consistency check
    validate_input(output_data)

    return output_data


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    NUM_JOBS = 30

    # Same seed => same generated dataset
    RANDOM_SEED = 42

    OUTPUT_FILE = (
        OUTPUT_DIR
        / f"dump_generated_{NUM_JOBS}_jobs.json"
    )

    try:
        print(f"Input file : {INPUT_FILE}")
        print(f"Output file: {OUTPUT_FILE}")
        print(f"Jobs       : {NUM_JOBS}")
        print(f"Seed       : {RANDOM_SEED}")

        generated_data = generate_jobs(
            num_jobs=NUM_JOBS,
            seed=RANDOM_SEED,
        )

        save_json(
            generated_data,
            OUTPUT_FILE,
        )

        print()
        print("[OK] Generate successful")
        print(
            f"[OK] Machines   : "
            f"{len(generated_data['machines'])}"
        )
        print(
            f"[OK] Operations : "
            f"{list(generated_data['break_times'].keys())}"
        )
        print(
            f"[OK] Jobs       : "
            f"{len(generated_data['jobs'])}"
        )
        print(
            f"[OK] Saved      : "
            f"{OUTPUT_FILE}"
        )

    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}")
        raise