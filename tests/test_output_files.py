import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import run_scheduling as runner
from config import GeneralConfig, PathsConfig
from src.utils import component_transform_results, utils
from src.visualize import component_vis


class OutputFileTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        # 20:54 UTC is 03:54 in Bangkok on the next local day.
        self.timestamp = datetime(2026, 10, 5, 20, 54, tzinfo=timezone.utc)
        self.results = {
            "input_data": {
                "period_start": "2026-10-05 12:00:00",
                "machines": {"m1": {"operation_id": "OP1", "downtime": []}},
                "break_times": {},
            },
            "solution": [{
                "job_code": "a", "job_name": "a", "quantity": 1,
                "operations": [{
                    "operation_id": "OP1", "durations": {"m1": {"machine_duration": 5}},
                    "operations_solution": {"machine_id": "m1", "start": 0, "duration": 5, "end": 5},
                }],
            }],
        }

    def test_json_creates_local_date_directory_and_preserves_same_minute_files(self):
        first = utils.save_schedule({"run": 1}, output_dir=self.root / "algorithm_output",
                                    output_file="schedule.json", output_timestamp=self.timestamp)
        second = utils.save_schedule({"run": 2}, output_dir=self.root / "algorithm_output",
                                     output_file="schedule.json", output_timestamp=self.timestamp)
        self.assertEqual(Path(first), self.root / "algorithm_output/261006/0354_schedule.json")
        self.assertEqual(Path(second), self.root / "algorithm_output/261006/0354_schedule_2.json")
        self.assertEqual(json.loads(Path(first).read_text()), {"run": 1})
        self.assertEqual(json.loads(Path(second).read_text()), {"run": 2})

    def test_csv_creates_date_directory_without_overwriting_previous_export(self):
        output_dir = self.root / "csv_output"
        for _ in range(2):
            frame = component_transform_results.transform_results_to_csv(
                self.results, output_dir=output_dir, save_csv_to="schedule.csv",
                output_timestamp=self.timestamp,
            )
        self.assertEqual(sorted(path.name for path in (output_dir / "261006").iterdir()),
                         ["0354_schedule.csv", "0354_schedule_2.csv"])
        self.assertEqual(len(frame), 1)
        self.assertIn("a", (output_dir / "261006/0354_schedule.csv").read_text())

    def test_html_creates_date_directory_and_uses_chart_name(self):
        frame = component_transform_results.transform_results_to_csv(self.results, output_dir=self.root)
        with (
            patch.object(PathsConfig, "VISUALIZE_OUTPUT_DIR", str(self.root / "templates")),
            patch.object(GeneralConfig, "IS_SHOW_COMPONENT_VISUALIZE", False),
            patch.object(GeneralConfig, "IS_SAVE_COMPONENT_VISUALIZE", True),
        ):
            for _ in range(2):
                component_vis.visualize_machine_view_schedule_chart(
                    frame.copy(), operations_order=["OP1"], name_of_chart="My Schedule",
                    output_timestamp=self.timestamp,
                )
        directory = self.root / "templates/261006"
        self.assertEqual(sorted(path.name for path in directory.iterdir()),
                         ["0354_my_schedule.html", "0354_my_schedule_2.html"])
        self.assertIn("My Schedule", (directory / "0354_my_schedule.html").read_text())

    def test_runner_uses_one_timestamp_for_all_three_exports(self):
        timestamps = []
        with (
            patch.object(runner, "preprocess_scheduling_input", return_value={}),
            patch.object(runner, "scheduling_batch_scheduler", return_value=self.results),
            patch.object(runner, "save_schedule", side_effect=lambda *args, **kwargs: timestamps.append(kwargs.get("output_timestamp"))),
            patch.object(runner, "transform_results_to_csv", side_effect=lambda *args, **kwargs: timestamps.append(kwargs.get("output_timestamp")) or "exported"),
            patch.object(runner, "visualize_machine_view_schedule_chart", side_effect=lambda *args, **kwargs: timestamps.append(kwargs.get("output_timestamp"))),
        ):
            runner.run_scheduling({})
        self.assertEqual(len(timestamps), 3)
        self.assertIsInstance(timestamps[0], datetime)
        self.assertEqual(timestamps, [timestamps[0]] * 3)


if __name__ == "__main__":
    unittest.main()
