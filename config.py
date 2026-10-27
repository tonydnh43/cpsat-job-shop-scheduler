import os

class ProjectConfig:
    PROJECT_NAME = "CPSAT_Job_Shop_Scheduler"

class PathsConfig:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.path.join(BASE_DIR, 'data')
    LOG_DIR = os.path.join(BASE_DIR, 'logs')
    OUTPUT_DIR = os.path.join(BASE_DIR, 'output')
    VISUALIZE_OUTPUT_DIR = os.path.join(BASE_DIR, 'templates')

class GeneralConfig:
    SCHEDULING_OPERATION_ORDER = ["OP1", "OP2", "OP3", "OP4", "OP5", "OP6"]

    IS_SHOW_COMPONENT_VISUALIZE = True
    IS_SAVE_COMPONENT_VISUALIZE = True
    IS_SHOW_FINAL_VISUALIZE = True
    IS_SAVE_FINAL_VISUALIZE = True

class InputAlgorithm:
    HORIZON_KEY = 'horizon'
    PERIOD_START_KEY = 'period_start'
    BREAK_TIMES_KEY = 'break_times'

    # machines configuration
    MACHINES_KEY = 'machines'
    DOWNTIME_KEY = 'downtime'
    MACHINE_ID_KEY = "machine_id"

    # jobs configuration
    JOBS_KEY = 'jobs'
    JOB_CODE_KEY = 'job_code'
    JOB_NAME_KEY = 'job_name'
    JOB_PARENT_CODE_KEY = 'parent_code'
    JOB_DEADLINE_KEY = 'deadline'
    JOB_QUANTITY_KEY = 'quantity'
    VARNISHING_TIME_KEY = 'varnishing_time'
    SCHEDULING_CODE_KEY = 'scheduling_code'
    SCHEDULING_SCHEDULED_END_TIME_KEY = 'scheduling_scheduled_end_time'
    
    # operations configuration
    OPERATIONS_KEY = 'operations'
    OPERATION_DEADLINE_KEY = 'deadline'
    OPERATION_ID_KEY = 'operation_id'
    OPERATION_DURATIONS_KEY = 'durations'
    OPERATION_MACHINE_DURATION_KEY = 'machine_duration'
    OPERATION_UNIT_DURATION_KEY = 'unit_duration'

class OutputAlgorithm:
    SOLUTION_KEY = 'solution'
    SOLUTION_OPERATIONS_KEY = 'operations_solution'
    SOLUTION_START_KEY = 'start'
    SOLUTION_DURATION_KEY = 'duration'
    SOLUTION_END_KEY = 'end'
    STATUS_KEY = 'status'
    TOTAL_JOBS_KEY = 'total_jobs'
    INFEASIBLE_JOBS_KEY = 'infeasible_jobs'
    UNSCHEDULED_JOBS_KEY = 'unscheduled_jobs'
    INPUT_DATA_KEY = 'input_data'

    # visualization
    VISUALIZATION_LABEL_KEY = 'label'
    VISUALIZATION_PATTERN_KEY = 'pattern'
    VISUALIZATION_COLOR_KEY = 'color'
    VISUALIZATION_HOVER_TEXT_KEY = 'hover_text'

class AlgorithmsConfig:
    # Motor
    MOTOR_BATCH_SIZE = 10
    MOTOR_MAKESPAN_WEIGHT = 0
    MOTOR_BREAK_TIME_WEIGHT = 100
    MOTOR_MAKESPAN_OPERATION_WEIGHT = 1
    MOTOR_RELATIVE_GAP_LIMIT = 0

    # Scheduling
    SCHEDULING_BATCH_SIZE = 10  # Preserve the runner's validated seed batch size
    SCHEDULING_MAKESPAN_WEIGHT = 100
    SCHEDULING_MACHINE_IDLE_WEIGHT = 10
    SCHEDULING_MACHINE_BALANCE_WEIGHT = 10  # 0 disables processing-load balancing
    SCHEDULING_JOB_WAIT_WEIGHT = 10  # 0 disables inter-operation waiting cost
    SCHEDULING_COMPACT_HORIZONTAL = True  # Pull surviving operations earlier after all stages
    SCHEDULING_RECOVER_HORIZONTAL = True  # Retry failed jobs as complete routes after compaction
    # Hybrid: one shared time budget for seed and local repairs.
    SCHEDULING_HYBRID_TOTAL_SECONDS = 60*5
    SCHEDULING_HYBRID_SEED_FRACTION = 0.25
    SCHEDULING_HYBRID_GROUP_SIZE = 10
    SCHEDULING_HYBRID_INITIAL_GROUP_SIZE = 5  # Grow failed neighborhoods up to GROUP_SIZE
    SCHEDULING_HYBRID_ROUND_SECONDS = 15
    SCHEDULING_HYBRID_SECONDARY_SECONDS = 5
    SCHEDULING_HYBRID_SECONDARY_FRACTION = 0.25  # Cap secondary by a fraction of each call's budget
    SCHEDULING_HYBRID_SECONDARY_GAP_LIMIT = 0.02  # Primary priorities still require zero gap
    SCHEDULING_HYBRID_MAX_ROUNDS = 12
    SCHEDULING_COMPACT_HYBRID = True  # Pack all operations after repairs, preserving jobs and chosen machines
    SCHEDULING_MAKESPAN_OPERATION_WEIGHT = 0
    SCHEDULING_RELATIVE_GAP_LIMIT = 0.01

    MINUTES_TIMEOUT = 3
    NUM_WORKERS = os.cpu_count() or 8
    TIME_LINE_OPTIMIZE = "time_line_optimize"
