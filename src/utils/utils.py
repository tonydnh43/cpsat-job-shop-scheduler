import json

from src.utils.logger import logger
from src.utils.output_files import reserve_output_path
from config import InputAlgorithm, OutputAlgorithm

def save_schedule(schedule_data, output_dir="data/algorithm_output", output_file=None, *, output_timestamp=None):
    """
    Save schedule data to a JSON file.
    
    Parameters:
    -----------
    schedule_data : dict
        The schedule data to save
    output_file : str, optional
        Base filename (default: 'schedule.json'); saved under YYMMDD/HHMM_name.
        
    Returns:
    --------
    str
        Path to the saved file
    """
    try:
        output_file = reserve_output_path(
            output_dir, output_file or "schedule.json", output_timestamp=output_timestamp
        )
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(schedule_data, f, indent=4)
        logger.info(f"Schedule saved to {output_file}")
        return str(output_file)
    except Exception as e:
        logger.error(f"Error saving schedule data: {str(e)}")
        return None

def map_schedule_to_stator_input(schedule_df, stator_input_json):
    """
    Map schedule to stator input.
    """
    mapped_stator_jobs = []
    stator_jobs = stator_input_json.get(InputAlgorithm.JOBS_KEY, [])
    for _idx_job, job in enumerate(stator_jobs):
        scheduling_code = job.get(InputAlgorithm.SCHEDULING_CODE_KEY, "")

        # find schedule_df.job_code == scheduling_code
        schedule_df_row = schedule_df[schedule_df[InputAlgorithm.JOB_CODE_KEY] == scheduling_code]
        # sort by end_time
        schedule_df_row = schedule_df_row.sort_values(by=OutputAlgorithm.SOLUTION_END_KEY, ascending=False)

        if schedule_df_row.empty:
            logger.error(f"Scheduling code {scheduling_code} not found in schedule. Continue to next job.")
            continue
        else:
            # get end_time value
            end_time = schedule_df_row.iloc[0][OutputAlgorithm.SOLUTION_END_KEY]

        # Add scheduling_scheduled_end_time to job
        job[InputAlgorithm.SCHEDULING_SCHEDULED_END_TIME_KEY] = int(float(end_time))

        # Add mapped_stator_jobs to mapped_stator_jobs
        mapped_stator_jobs.append(job)

    # Add mapped_stator_jobs to stator_input_json
    stator_input_json[InputAlgorithm.JOBS_KEY] = mapped_stator_jobs

    return stator_input_json
        
def split_jobs_from_input_motor_follow_operation_id(input_motor_data):
    horizon = input_motor_data.get(InputAlgorithm.HORIZON_KEY, 0)
    period_start = input_motor_data.get(InputAlgorithm.PERIOD_START_KEY, 0)
    machines = input_motor_data.get(InputAlgorithm.MACHINES_KEY, [])
    break_times = input_motor_data.get(InputAlgorithm.BREAK_TIMES_KEY, {})
    jobs = input_motor_data.get(InputAlgorithm.JOBS_KEY, [])

    input_by_operation_id = {}
    for machine_id, machine_info in machines.items():
        machine_operations = machine_info.get(InputAlgorithm.OPERATION_ID_KEY, "")
        if not machine_operations:
            logger.error(f"Machine {machine_id} has no operation ID. Skipping.")
            continue
        input_by_operation_id[machine_operations] = {}
        
        # Add data
        input_by_operation_id[machine_operations][InputAlgorithm.HORIZON_KEY] = horizon
        input_by_operation_id[machine_operations][InputAlgorithm.PERIOD_START_KEY] = period_start
        input_by_operation_id[machine_operations][InputAlgorithm.MACHINES_KEY] = {machine_id: machine_info}
        input_by_operation_id[machine_operations][InputAlgorithm.BREAK_TIMES_KEY] = {machine_operations: break_times[machine_operations]}

        # Filter jobs by operation ID
        input_by_operation_id[machine_operations][InputAlgorithm.JOBS_KEY] = []
        for job in jobs:
            job_operations = job.get(InputAlgorithm.OPERATIONS_KEY, "")

            if len(job_operations) == 0:
                logger.error(f"Job {job} has no operation ID. Skipping.")
            elif len(job_operations) > 1:
                logger.error(f"Job {job} has more than one operation ID. Skipping.")
            else:
                for operation in job_operations:
                    operation_id = operation.get(InputAlgorithm.OPERATION_ID_KEY, "")
                    if operation_id == machine_operations:
                        input_by_operation_id[machine_operations][InputAlgorithm.JOBS_KEY].append(job)
                        break
        
    return input_by_operation_id

def combine_schedule_from_results_by_operation_id(input_motor_data, results_by_operation_id):
    """
    Combine schedule from results by operation ID.
    """
    results = {
        OutputAlgorithm.STATUS_KEY: "success",
        OutputAlgorithm.TOTAL_JOBS_KEY: 0,
        OutputAlgorithm.INPUT_DATA_KEY: input_motor_data,
        OutputAlgorithm.SOLUTION_KEY: [],
        OutputAlgorithm.INFEASIBLE_JOBS_KEY: [],
    }
    for operation_id, operation_id_results in results_by_operation_id.items():
        # Add the results to the final results
        results[OutputAlgorithm.TOTAL_JOBS_KEY] += len(operation_id_results[OutputAlgorithm.SOLUTION_KEY])
        results[OutputAlgorithm.SOLUTION_KEY].extend(operation_id_results[OutputAlgorithm.SOLUTION_KEY])
        results[OutputAlgorithm.INFEASIBLE_JOBS_KEY].extend(operation_id_results[OutputAlgorithm.INFEASIBLE_JOBS_KEY])
    
    return results
