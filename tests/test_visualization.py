import tempfile
import unittest
from unittest.mock import patch

from config import GeneralConfig
from src.utils.component_transform_results import transform_results_to_csv
from src.visualize.component_vis import visualize_machine_view_schedule_chart


class VisualizationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        results = {
            'input_data': {
                'period_start': '2026-10-06 08:00:00',
                'machines': {'OP1-MC1': {'operation_id': 'OP1', 'downtime': [[20, 25]]}},
                'break_times': {'OP1': [[5, 10]]},
            },
            'solution': [{
                'job_code': 'SO_Job_1', 'job_name': 'Job 1', 'quantity': 1,
                'operations': [{
                    'operation_id': 'OP1',
                    'durations': {'OP1-MC1': {'machine_duration': 10}},
                    'operations_solution': {'machine_id': 'OP1-MC1', 'start': 0, 'duration': 15, 'end': 15},
                }],
            }],
        }
        self.frame = transform_results_to_csv(results, output_dir=directory.name)
        for name in ('IS_SHOW_COMPONENT_VISUALIZE', 'IS_SAVE_COMPONENT_VISUALIZE'):
            context = patch.object(GeneralConfig, name, False)
            context.start()
            self.addCleanup(context.stop)

    def test_calendar_bars_stay_above_jobs_regardless_of_input_row_order(self):
        for calendar_first in (False, True):
            with self.subTest(calendar_first=calendar_first):
                frame = self.frame.iloc[::-1].copy() if calendar_first else self.frame.copy()
                fig = visualize_machine_view_schedule_chart(frame, ['OP1'], 'Schedule')
                traces = {trace.legendgroup.split(', ', 1)[0]: trace for trace in fig.data}
                job_layer = traces['SO_Job_1'].zorder or 0
                self.assertGreater(traces['break_time'].zorder or 0, job_layer)
                self.assertGreater(traces['downtime'].zorder or 0, job_layer)
                self.assertEqual(fig.layout.barmode, 'overlay')
                self.assertEqual(list(traces['break_time'].y), ['OP1-MC1'])
                self.assertEqual(list(traces['break_time'].base), [frame.loc[2, 'start_dt']])
                self.assertEqual(list(traces['break_time'].x), [300000])
                self.assertIn('Type: Break Time', traces['break_time'].customdata[0][0])

    def test_calendar_patterns_do_not_depend_on_which_row_comes_first(self):
        frame = self.frame.iloc[::-1].copy()
        fig = visualize_machine_view_schedule_chart(frame, ['OP1'], 'Schedule')
        traces = {trace.legendgroup.split(', ', 1)[0]: trace for trace in fig.data}
        self.assertEqual(traces['break_time'].marker.pattern.shape, '/')
        self.assertEqual(traces['downtime'].marker.pattern.shape, '/')
        self.assertEqual(traces['SO_Job_1'].marker.pattern.shape, '')


if __name__ == '__main__':
    unittest.main()
