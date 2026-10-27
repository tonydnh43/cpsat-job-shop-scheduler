import os
import json
import traceback

from src.scheduling.scheduling_batch_scheduler import scheduling_batch_scheduler
from src.utils.logger import logger
from src.utils.component_transform_results import transform_results_to_csv
from src.visualize.component_vis import visualize_machine_view_schedule_chart
from src.utils.input_scheduling_preprocess import preprocess_scheduling_input
from src.utils.utils import save_schedule
from src.utils.output_files import get_output_timestamp
from config import AlgorithmsConfig, GeneralConfig

# vertical: all operations of each job batch.
# horizontal: each operation across all job batches before the next operation.
# hybrid: horizontal seed, then repair related complete job routes.
BATCH_DIRECTION = "hybrid"  # "vertical", "horizontal" or "hybrid"
JOBS_PER_BATCH = AlgorithmsConfig.SCHEDULING_BATCH_SIZE

def run_scheduling(input_data=None):
    """Main function to schedule jobs by batch."""
    # Load input data
    if input_data is None:
        input_data_path = 'data/algorithm_input/exp_30_jobs_schedule.json'
        try:
            with open(input_data_path, 'r') as f:
                input_data = json.load(f)
        except Exception as e:
            logger.error(f"Error loading input data: {str(e)}")
            return None
    
    # Run the batch scheduler
    logger.info("Starting batch scheduling...")
    input_scheduling_data = preprocess_scheduling_input(input_data)
    results = scheduling_batch_scheduler(
        input_scheduling_data,
        jobs_per_batch=JOBS_PER_BATCH,
        batch_direction=BATCH_DIRECTION,
    )
    
    if results:
        output_timestamp = get_output_timestamp()
        # Save the output
        save_schedule(results, output_file='schedule.json', output_timestamp=output_timestamp)
        try:
            # Transform results to CSV
            schedule_df = transform_results_to_csv(
                results, save_csv_to='schedule.csv', output_timestamp=output_timestamp
            )

            # Visualize the schedule
            visualize_machine_view_schedule_chart(
                schedule_df, 
                operations_order=GeneralConfig.SCHEDULING_OPERATION_ORDER, 
                name_of_chart="Schedule",
                output_timestamp=output_timestamp,
            )
            logger.info("Scheduling completed!")
            return schedule_df
        except Exception as e:
            logger.warning(f"Visualization failed: {str(e)}")
            traceback.print_exc()
    else:
        logger.error("Failed to create a schedule!")
        return None


if __name__ == "__main__":
    run_scheduling()
