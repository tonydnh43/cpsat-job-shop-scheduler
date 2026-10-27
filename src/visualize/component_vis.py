import pandas as pd
import plotly.express as px

from src.utils.logger import logger
from src.utils.output_files import reserve_output_path
from config import OutputAlgorithm, InputAlgorithm, GeneralConfig, PathsConfig

def insert_hover_text(schedule_df):
    """
    Adds a 'hover_text' column to the schedule_df for richer hover information in Plotly.
    """
    def create_hover_string(row):
        text_parts = []
        # Format start and end times
        start_str = row[f'{OutputAlgorithm.SOLUTION_START_KEY}_dt'].strftime('%Y-%m-%d %H:%M:%S')
        end_str = row[f'{OutputAlgorithm.SOLUTION_END_KEY}_dt'].strftime('%Y-%m-%d %H:%M:%S')
        start_algo_str = row[OutputAlgorithm.SOLUTION_START_KEY]
        end_algo_str = row[OutputAlgorithm.SOLUTION_END_KEY]
        machine_str = f"Machine: {row[InputAlgorithm.MACHINE_ID_KEY]}"

        if row[OutputAlgorithm.VISUALIZATION_LABEL_KEY] == 'Downtime': 
            text_parts.append("<b>Type: Downtime</b>")
            text_parts.append(f"<b>Operation ID: {row[InputAlgorithm.OPERATION_ID_KEY]}</b>")
            text_parts.append(f"Duration: {row[OutputAlgorithm.SOLUTION_DURATION_KEY]} mins")
            text_parts.append(f"Start: {start_algo_str}")
            text_parts.append(f"End: {end_algo_str}")
            text_parts.append(f"Start Time: {start_str}")
            text_parts.append(f"End Time: {end_str}")
            text_parts.append(machine_str)
        elif row[OutputAlgorithm.VISUALIZATION_LABEL_KEY] == 'BreakTime':
            text_parts.append("<b>Type: Break Time</b>")
            text_parts.append(f"<b>Operation ID: {row[InputAlgorithm.OPERATION_ID_KEY]}</b>")
            text_parts.append(f"Duration: {row[OutputAlgorithm.SOLUTION_DURATION_KEY]} mins")
            text_parts.append(f"Start: {start_algo_str}")
            text_parts.append(f"End: {end_algo_str}")
            text_parts.append(f"Start Time: {start_str}")
            text_parts.append(f"End Time: {end_str}")
            text_parts.append(machine_str)
        else:
            text_parts.append(f"<b>Job Code: {row[InputAlgorithm.JOB_CODE_KEY]}</b>")
            text_parts.append(f"<b>Operation ID: {row[InputAlgorithm.OPERATION_ID_KEY]}</b>")
            text_parts.append(f"Job Name: {row[InputAlgorithm.JOB_NAME_KEY]}")
            text_parts.append(f"Parent Code: {row[InputAlgorithm.JOB_PARENT_CODE_KEY]}")
            text_parts.append(f"Quantity: {row[InputAlgorithm.JOB_QUANTITY_KEY]}")
            text_parts.append(f"Unit Duration: {row[InputAlgorithm.OPERATION_UNIT_DURATION_KEY]} mins")
            text_parts.append(f"Duration: {row[OutputAlgorithm.SOLUTION_DURATION_KEY]} mins")
            text_parts.append(f"Start: {start_algo_str}")
            text_parts.append(f"End: {end_algo_str}")
            text_parts.append(f"Start Time: {start_str}")
            text_parts.append(f"End Time: {end_str}")
            text_parts.append(machine_str)
        
        # for stator
        if pd.notnull(row[InputAlgorithm.SCHEDULING_CODE_KEY]):
            text_parts.append(f"<b>Scheduling Code: {row[InputAlgorithm.SCHEDULING_CODE_KEY]}</b>")
        if pd.notnull(row[InputAlgorithm.VARNISHING_TIME_KEY]):
            text_parts.append(f"<b>Varnishing Time: {row[InputAlgorithm.VARNISHING_TIME_KEY]} mins</b>")
        return "<br>".join(text_parts)

    schedule_df[OutputAlgorithm.VISUALIZATION_HOVER_TEXT_KEY] = schedule_df.apply(create_hover_string, axis=1)
    return schedule_df

def visualize_machine_view_schedule_chart(schedule_df, operations_order, name_of_chart, *, output_timestamp=None):
    """
    Create and display a machine timeline visualization from CSV data.
    
    Parameters:
    -----------
    csv_data : dict
        Dictionary containing DataFrame and file paths from transform_results_to_csv
    
    Returns:
    --------
    go.Figure
        Plotly figure object (already displayed)
    """
    # Add hover text
    schedule_df = insert_hover_text(schedule_df)
    
    # Extract operation type from operation_id for sorting
    schedule_df['operation_type'] = schedule_df[InputAlgorithm.OPERATION_ID_KEY].apply(
        lambda x: next((op for op in operations_order if op in x), "Other")
    )
    
    # Create a categorical type with the specified order
    schedule_df['operation_type'] = pd.Categorical(
        schedule_df['operation_type'],
        categories=operations_order + ["Other"],
        ordered=True
    )
    # Sort by operation_type and then by machine_id
    schedule_df = schedule_df.sort_values(by=['operation_type', InputAlgorithm.MACHINE_ID_KEY])
    
    # Create a categorical type for operation_type to maintain the sorted order
    machine_order = schedule_df[InputAlgorithm.MACHINE_ID_KEY].unique()
    schedule_df[InputAlgorithm.MACHINE_ID_KEY] = pd.Categorical(
        schedule_df[InputAlgorithm.MACHINE_ID_KEY],
        categories=machine_order,
        ordered=True
    )

    fig = px.timeline(
        schedule_df,
        x_start=f'{OutputAlgorithm.SOLUTION_START_KEY}_dt',
        x_end=f'{OutputAlgorithm.SOLUTION_END_KEY}_dt',
        y=InputAlgorithm.MACHINE_ID_KEY,
        color=InputAlgorithm.JOB_CODE_KEY,
        text=OutputAlgorithm.VISUALIZATION_LABEL_KEY,
        pattern_shape=OutputAlgorithm.VISUALIZATION_PATTERN_KEY,
        pattern_shape_map={'': '', '/': '/'},
        custom_data=[OutputAlgorithm.VISUALIZATION_HOVER_TEXT_KEY],
        hover_data={
             OutputAlgorithm.VISUALIZATION_LABEL_KEY: False,
             f'{OutputAlgorithm.SOLUTION_START_KEY}_dt': False,
             f'{OutputAlgorithm.SOLUTION_END_KEY}_dt': False,
             InputAlgorithm.MACHINE_ID_KEY: False,
             OutputAlgorithm.VISUALIZATION_PATTERN_KEY: False
         },
        category_orders={InputAlgorithm.MACHINE_ID_KEY: machine_order.tolist()},
        color_discrete_map={'downtime': 'grey', 'break_time': 'black'},
        color_discrete_sequence=px.colors.qualitative.Alphabet
    )

    fig.update_layout(
        xaxis_title='Time',
        yaxis_title='Machine',
        title=f'{name_of_chart}'
    )

    fig.update_traces(
        textposition='inside', 
        hovertemplate='%{customdata[0]}<extra></extra>'
    )

    # Processing intervals hold the machine across breaks, so calendar bars
    # must overlay them regardless of the order Plotly groups the input rows.
    calendar_layers = {'downtime': 1, 'break_time': 2}
    for trace in fig.data:
        # Plotly combines color and pattern values in the legend group. Split
        # only the final separator so commas in job codes remain intact.
        job_code = trace.legendgroup.rsplit(', ', 1)[0]
        trace.zorder = calendar_layers.get(job_code, 0)

    # Display the figure
    if GeneralConfig.IS_SHOW_COMPONENT_VISUALIZE:
        fig.show()

    # Save the figure
    if GeneralConfig.IS_SAVE_COMPONENT_VISUALIZE:
        save_name_of_chart = name_of_chart.lower()
        save_name_of_chart = save_name_of_chart.replace(' ', '_')
        output_path = reserve_output_path(
            PathsConfig.VISUALIZE_OUTPUT_DIR, f'{save_name_of_chart}.html',
            output_timestamp=output_timestamp,
        )
        fig.write_html(output_path)
        logger.info(f"Schedule chart saved to {output_path}")

    return fig
