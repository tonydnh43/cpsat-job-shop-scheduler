import json
import pandas as pd
import os
from datetime import datetime, timedelta

from src.utils.logger import logger
from src.utils.output_files import reserve_output_path
from config import OutputAlgorithm, InputAlgorithm

def set_datetime(df, period_start, columns_name):
    """
    Converts numeric values in the specified columns to datetime objects based on period_start.

    Parameters:
    - df: pandas DataFrame
    - period_start: string representing the start time (format 'YYYY-MM-DD HH:MM:SS')
    - columns_name: list of column names to convert

    Returns:
    - df: DataFrame updated with datetime columns
    """
    period_start_dt = datetime.strptime(period_start, '%Y-%m-%d %H:%M:%S')
    
    for col in columns_name:
        df[f"{col}_dt"] = df[col].apply(lambda x: period_start_dt + timedelta(minutes=x))

    return df

def transform_results_to_csv(results, output_dir='data/csv_output', save_csv_to=None, *, output_timestamp=None):
    """
    Transform scheduling results to CSV files.
    
    Parameters:
    -----------
    results : dict
        The scheduling results dictionary
    output_dir : str, optional
        Directory to save CSV files (default: data/csv_output)
        
    Returns:
    --------
    dict
        Dictionary with DataFrames and paths to the created CSV files
    """
    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)
    
    # Get input data
    input_data = results.get(OutputAlgorithm.INPUT_DATA_KEY, [])
    machines = input_data.get(InputAlgorithm.MACHINES_KEY, [])
    period_start = input_data.get(InputAlgorithm.PERIOD_START_KEY, "")
    break_times = input_data.get(InputAlgorithm.BREAK_TIMES_KEY, {})

    # Add machine using by operation
    machine_using_by_op = {}
    for machine_id, machine_info in machines.items():
        operation_id = machine_info.get(InputAlgorithm.OPERATION_ID_KEY, "")
        if operation_id:
            if operation_id not in machine_using_by_op.keys():
                machine_using_by_op[operation_id] = [machine_id]
            else:
                machine_using_by_op[operation_id].append(machine_id)

    # 1. Transform jobs schedule
    jobs_data = []
    if OutputAlgorithm.SOLUTION_KEY in results:
        for job in results[OutputAlgorithm.SOLUTION_KEY]:
            job_code = job.get(InputAlgorithm.JOB_CODE_KEY, "")
            job_name = job.get(InputAlgorithm.JOB_NAME_KEY, "")
            job_parent_code = job.get(InputAlgorithm.JOB_PARENT_CODE_KEY, "")
            job_quantity = job.get(InputAlgorithm.JOB_QUANTITY_KEY, 1)
            operations = job.get(InputAlgorithm.OPERATIONS_KEY, [])

            # for stator
            scheduling_code = job.get(InputAlgorithm.SCHEDULING_CODE_KEY, None)
            varnishing_time = job.get(InputAlgorithm.VARNISHING_TIME_KEY, None)
            
            for operation in operations:
                operation_solution = operation.get(OutputAlgorithm.SOLUTION_OPERATIONS_KEY, {})
                operation_id = operation.get(InputAlgorithm.OPERATION_ID_KEY, "")
                machine_id = operation_solution.get(InputAlgorithm.MACHINE_ID_KEY, "")
                start = operation_solution.get(OutputAlgorithm.SOLUTION_START_KEY, "")
                duration = operation_solution.get(OutputAlgorithm.SOLUTION_DURATION_KEY, "")
                end = operation_solution.get(OutputAlgorithm.SOLUTION_END_KEY, "")
                unit_machine_duration = operation.get(InputAlgorithm.OPERATION_DURATIONS_KEY, {}).get(machine_id, {}).get(InputAlgorithm.OPERATION_UNIT_DURATION_KEY, 0)
                
                job_data = {
                    InputAlgorithm.JOB_CODE_KEY: job_code,
                    InputAlgorithm.JOB_NAME_KEY: job_name,
                    InputAlgorithm.JOB_PARENT_CODE_KEY: job_parent_code,
                    InputAlgorithm.JOB_QUANTITY_KEY: job_quantity,
                    InputAlgorithm.OPERATION_ID_KEY: operation_id,
                    InputAlgorithm.MACHINE_ID_KEY: machine_id,
                    OutputAlgorithm.SOLUTION_START_KEY: start,
                    OutputAlgorithm.SOLUTION_DURATION_KEY: duration,
                    OutputAlgorithm.SOLUTION_END_KEY: end,
                    InputAlgorithm.OPERATION_UNIT_DURATION_KEY: unit_machine_duration,
                    # for stator
                    InputAlgorithm.SCHEDULING_CODE_KEY: scheduling_code,
                    InputAlgorithm.VARNISHING_TIME_KEY: varnishing_time,
                    # for visualization
                    OutputAlgorithm.VISUALIZATION_LABEL_KEY: job_code,
                    OutputAlgorithm.VISUALIZATION_PATTERN_KEY: '',
                }
                jobs_data.append(job_data)
        
        jobs_df = pd.DataFrame(jobs_data)

    # 2. Transform downtime data
    downtime_data = []
    for machine_id, machine_info in machines.items():
        # get operation id from machine_using_by_op
        operation_id = next((op_id for op_id, machine_id_list in machine_using_by_op.items() 
                           if machine_id in machine_id_list), "")
        # Add downtime data
        if InputAlgorithm.DOWNTIME_KEY in machine_info:
            for downtime in machine_info[InputAlgorithm.DOWNTIME_KEY]:
                downtime_data.append({
                    InputAlgorithm.JOB_CODE_KEY: 'downtime',
                    InputAlgorithm.MACHINE_ID_KEY: machine_id,
                    InputAlgorithm.OPERATION_ID_KEY: operation_id,
                    OutputAlgorithm.SOLUTION_START_KEY: downtime[0],
                    OutputAlgorithm.SOLUTION_DURATION_KEY: downtime[1] - downtime[0],
                    OutputAlgorithm.SOLUTION_END_KEY: downtime[1],
                    # for visualization
                    OutputAlgorithm.VISUALIZATION_LABEL_KEY: 'Downtime',
                    OutputAlgorithm.VISUALIZATION_PATTERN_KEY: '/',
                })
    if downtime_data:
        # Sort downtime data by start time
        downtime_data.sort(key=lambda x: x['start'])
    downtime_df = pd.DataFrame(downtime_data)
    
    # 3. Transform break time data
    break_time_data = []
    for op_id, machine_id_list in machine_using_by_op.items():
        if op_id in break_times:
            for machine_id in machine_id_list:
                for bt in break_times[op_id]:
                    break_time_data.append({
                        InputAlgorithm.JOB_CODE_KEY: 'break_time',
                        InputAlgorithm.MACHINE_ID_KEY: machine_id,
                        InputAlgorithm.OPERATION_ID_KEY: op_id,
                        OutputAlgorithm.SOLUTION_START_KEY: bt[0],
                        OutputAlgorithm.SOLUTION_END_KEY: bt[1],
                        OutputAlgorithm.SOLUTION_DURATION_KEY: bt[1] - bt[0],
                        # for visualization
                        OutputAlgorithm.VISUALIZATION_LABEL_KEY: 'BreakTime',
                        OutputAlgorithm.VISUALIZATION_PATTERN_KEY: '/',
                    })
    if break_time_data:
        # Sort break time data by start time
        break_time_data.sort(key=lambda x: x['start'])
    break_time_df = pd.DataFrame(break_time_data)
    
    # Combine data
    schedule_df = pd.concat([jobs_df, downtime_df, break_time_df], ignore_index=True)
    
    # Columns to convert
    columns_to_convert = [OutputAlgorithm.SOLUTION_START_KEY, OutputAlgorithm.SOLUTION_END_KEY]
    # Convert 
    schedule_df = set_datetime(schedule_df, period_start, columns_to_convert)
    
    # Save to CSV
    if save_csv_to:
        output_path = reserve_output_path(output_dir, save_csv_to, output_timestamp=output_timestamp)
        schedule_df.to_csv(output_path, index=False)
        logger.info(f"Schedule CSV saved to {output_path}")
    
    return schedule_df
