import unittest
from copy import deepcopy
from unittest.mock import patch

from ortools.sat.python import cp_model

import run_scheduling as runner
from config import AlgorithmsConfig
from src.scheduling import scheduling_algorithm, scheduling_batch_scheduler
from src.utils.input_scheduling_preprocess import preprocess_scheduling_input


def job(code, durations=None, deadline=100):
    return {
        "job_code": code,
        "job_name": code,
        "deadline": deadline,
        "operations": [{
            "operation_id": "op",
            "deadline": deadline,
            "durations": {
                machine: {"machine_duration": duration}
                for machine, duration in (durations or {"m1": 5}).items()
            },
        }],
    }


def input_data(jobs, breaks=(), downtime=()):
    return {
        "horizon": 100,
        "machines": {
            "m1": {"downtime": list(downtime)},
            "m2": {"downtime": list(downtime)},
        },
        "break_times": {"op": list(breaks)},
        "jobs": jobs,
    }


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        for name, value in {
            "MINUTES_TIMEOUT": 2 / 60,
            "NUM_WORKERS": 1,
            "SCHEDULING_RELATIVE_GAP_LIMIT": 0,
            "SCHEDULING_MACHINE_BALANCE_WEIGHT": 0,
            "SCHEDULING_JOB_WAIT_WEIGHT": 0,
            "SCHEDULING_COMPACT_HORIZONTAL": False,
            "SCHEDULING_RECOVER_HORIZONTAL": False,
        }.items():
            context = patch.object(AlgorithmsConfig, name, value, create=True)
            context.start()
            self.addCleanup(context.stop)
        for module in (scheduling_algorithm, scheduling_batch_scheduler, runner):
            context = patch.object(module, "logger")
            context.start()
            self.addCleanup(context.stop)

    def test_runner_preprocesses_once_after_calculating_machine_duration(self):
        data = input_data([job("a")], downtime=[(25, 35), (20, 30)])
        data["jobs"][0]["quantity"] = 1
        data["jobs"][0]["operations"][0]["durations"]["m1"]["unit_duration"] = 1.5
        saved = []
        with (
            patch.object(runner, "preprocess_scheduling_input", wraps=preprocess_scheduling_input, create=True) as prepare,
            patch.object(runner, "save_schedule", side_effect=lambda result, **kwargs: saved.append(deepcopy(result))),
            patch.object(runner, "transform_results_to_csv", return_value="exported"),
            patch.object(runner, "visualize_machine_view_schedule_chart"),
            patch.object(runner, "logger"),
        ):
            self.assertEqual(runner.run_scheduling(data), "exported")
        self.assertEqual(prepare.call_count, 1)
        op = saved[0]["solution"][0]["operations"][0]
        self.assertEqual(op["durations"]["m1"]["machine_duration"], 2)
        self.assertEqual(op["operations_solution"]["duration"], 2)
        self.assertEqual(saved[0]["input_data"]["machines"]["m1"]["downtime"], [[20, 35]])

    def test_schedulers_do_not_revalidate_prepared_input(self):
        prepared = preprocess_scheduling_input(input_data([job("a"), job("b")]))
        with patch("src.utils.input_scheduling_preprocess.check_scheduling_input",
                   side_effect=AssertionError("Validation belongs in the runner")):
            direct = scheduling_algorithm.jobshop_scheduling(deepcopy(prepared))
            batched = scheduling_batch_scheduler.scheduling_batch_scheduler(deepcopy(prepared), jobs_per_batch=1)
        self.assertEqual(len(direct["solution"]["jobs"]), 2)
        self.assertEqual(batched["total_jobs"], 2)

    def schedule(self, durations, deadline, breaks=(), downtime=()):
        return scheduling_algorithm.jobshop_scheduling(
            input_data([job("a", durations, deadline)], breaks, downtime)
        )

    def test_balance_weight_spreads_processing_load_and_zero_disables_it(self):
        data = input_data([job(code, {"m1": 5, "m2": 5}) for code in ("a", "b", "c", "d")])
        loads_by_weight = {}
        for weight in (0, 1000):
            with (
                patch.object(AlgorithmsConfig, "SCHEDULING_MAKESPAN_WEIGHT", 1),
                patch.object(AlgorithmsConfig, "SCHEDULING_MACHINE_IDLE_WEIGHT", 100),
                patch.object(AlgorithmsConfig, "SCHEDULING_MAKESPAN_OPERATION_WEIGHT", 0),
                patch.object(AlgorithmsConfig, "SCHEDULING_MACHINE_BALANCE_WEIGHT", weight),
            ):
                result = scheduling_algorithm.jobshop_scheduling(
                    data, earliest_starts={code: 10 for code in ("a", "b", "c", "d")}
                )
            loads = {"m1": 0, "m2": 0}
            for item in result["solution"]["jobs"]:
                solution = item["operations"][0]["operations_solution"]
                loads[solution["machine_id"]] += solution["duration"]
            loads_by_weight[weight] = sorted(loads.values())
        self.assertEqual(loads_by_weight[0], [0, 20])
        self.assertEqual(loads_by_weight[1000], [10, 10])

    def test_balance_includes_processing_load_from_previous_batches(self):
        for direction in ("vertical", "horizontal"):
            for first_machine, second_machine in (("m1", "m2"), ("m2", "m1")):
                with (
                    self.subTest(direction=direction, first_machine=first_machine),
                    patch.object(AlgorithmsConfig, "SCHEDULING_MAKESPAN_WEIGHT", 0),
                    patch.object(AlgorithmsConfig, "SCHEDULING_MACHINE_IDLE_WEIGHT", 0),
                    patch.object(AlgorithmsConfig, "SCHEDULING_MAKESPAN_OPERATION_WEIGHT", 0),
                    patch.object(AlgorithmsConfig, "SCHEDULING_MACHINE_BALANCE_WEIGHT", 100),
                ):
                    data = input_data([job("a", {first_machine: 10}), job("b", {"m1": 10, "m2": 10})])
                    original = deepcopy(data)
                    result = scheduling_batch_scheduler.scheduling_batch_scheduler(
                        data, jobs_per_batch=1, batch_direction=direction
                    )
                    self.assertEqual(result["solution"][1]["operations"][0]["operations_solution"]["machine_id"],
                                     second_machine)
                    self.assertEqual(data, original)

    def test_balance_excludes_breaks_and_maintenance_from_processing_load(self):
        data = input_data([job("a", {"m1": 10}), job("b", {"m2": 10})], breaks=[(1, 11)])
        data["machines"]["m2"]["downtime"] = [[0, 20]]
        original_solve = cp_model.CpSolver.Solve
        costs = []

        def record_balance(solver, model, *args):
            status = original_solve(solver, model, *args)
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                self.assertGreaterEqual(len(args[0].variables), 4, "Balance cost must be available in solver logging")
                costs.append(solver.Value(args[0].variables[3]))
            return status

        with (
            patch.object(AlgorithmsConfig, "SCHEDULING_MACHINE_BALANCE_WEIGHT", 10),
            patch.object(cp_model.CpSolver, "Solve", record_balance),
        ):
            result = scheduling_algorithm.jobshop_scheduling(data)
        self.assertEqual(costs, [0])
        self.assertEqual(result["solution"]["jobs"][0]["operations"][0]["operations_solution"]["duration"], 20)

    def test_balance_compares_only_machines_of_the_same_operation(self):
        data = input_data([job("a", {"m1": 10}), job("b", {"m2": 30})])
        data["jobs"][1]["operations"][0]["operation_id"] = "other"
        original_solve = cp_model.CpSolver.Solve
        costs = []

        def record_balance(solver, model, *args):
            status = original_solve(solver, model, *args)
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                self.assertGreaterEqual(len(args[0].variables), 4, "Balance cost must be available in solver logging")
                costs.append(solver.Value(args[0].variables[3]))
            return status

        with (
            patch.object(AlgorithmsConfig, "SCHEDULING_MACHINE_BALANCE_WEIGHT", 10),
            patch.object(cp_model.CpSolver, "Solve", record_balance),
        ):
            self.assertIsNotNone(scheduling_algorithm.jobshop_scheduling(data))
        self.assertEqual(costs, [0])

    def test_job_wait_weight_reduces_wait_after_committed_operation(self):
        data = input_data([job("a", {"m1": 5, "m2": 10})])
        data["machines"]["m1"]["downtime"] = [[0, 20]]
        for mode in ("direct", "horizontal"):
            waits = {}
            for weight in (0, 10):
                with (
                    self.subTest(mode=mode, weight=weight),
                    patch.object(AlgorithmsConfig, "SCHEDULING_MAKESPAN_WEIGHT", 0),
                    patch.object(AlgorithmsConfig, "SCHEDULING_MACHINE_IDLE_WEIGHT", 1),
                    patch.object(AlgorithmsConfig, "SCHEDULING_MAKESPAN_OPERATION_WEIGHT", 0),
                    patch.object(AlgorithmsConfig, "SCHEDULING_JOB_WAIT_WEIGHT", weight),
                ):
                    if mode == "direct":
                        result = scheduling_algorithm.jobshop_scheduling(
                            data, earliest_starts={"a": 10}, previous_operation_ends={"a": 10}
                        )
                        solution = result["solution"]["jobs"][0]["operations"][0]["operations_solution"]
                    else:
                        horizontal_data = deepcopy(data)
                        horizontal_data["machines"]["m3"] = {"downtime": []}
                        first = {"operation_id": "first", "deadline": 100,
                                 "durations": {"m3": {"machine_duration": 10}}}
                        horizontal_data["jobs"][0]["operations"].insert(0, first)
                        result = scheduling_batch_scheduler.scheduling_batch_scheduler(
                            horizontal_data, jobs_per_batch=1, batch_direction="horizontal"
                        )
                        solution = result["solution"][0]["operations"][1]["operations_solution"]
                    waits[weight] = solution["start"] - 10
            self.assertEqual(waits, {0: 10, 10: 0})

    def test_horizontal_compaction_moves_survivor_into_gap_left_by_failed_job(self):
        data = self.two_operation_data("m2")
        data["jobs"][0]["operations"][1]["deadline"] = 0
        original = deepcopy(data)
        schedules = {}
        for enabled in (False, True):
            with patch.object(AlgorithmsConfig, "SCHEDULING_COMPACT_HORIZONTAL", enabled):
                schedules[enabled] = scheduling_batch_scheduler.scheduling_batch_scheduler(
                    data, jobs_per_batch=1, batch_direction="horizontal"
                )
        before = schedules[False]["solution"][0]["operations"]
        after = schedules[True]["solution"][0]["operations"]
        self.assertEqual(before[0]["operations_solution"]["start"], 5)
        self.assertEqual(after[0]["operations_solution"]["start"], 0)
        self.assertEqual(after[1]["operations_solution"]["start"], 5)
        for old, new in zip(before, after):
            self.assertEqual(old["operations_solution"]["machine_id"], new["operations_solution"]["machine_id"])
            self.assertLessEqual(new["operations_solution"]["start"], old["operations_solution"]["start"])
            self.assertLessEqual(new["operations_solution"]["end"], old["operations_solution"]["end"])
        self.assertEqual(schedules[True]["infeasible_jobs"], [original["jobs"][0]])
        self.assertEqual(schedules[True]["total_jobs"], 1)
        self.assertEqual(data, original)

    def test_compaction_recalculates_breaks_and_preserves_machines_and_maintenance(self):
        data = self.two_operation_data("m2")
        data["jobs"] = data["jobs"][:1]
        first, second = data["jobs"][0]["operations"]
        first["durations"] = {"m1": {"machine_duration": 10}, "m2": {"machine_duration": 1}}
        first["deadline"] = 35
        second["deadline"] = 54
        first["operations_solution"] = {"machine_id": "m1", "start": 30, "duration": 10, "end": 40}
        second["operations_solution"] = {"machine_id": "m2", "start": 50, "duration": 5, "end": 55}
        data["break_times"] = {"OP1": [[1, 11]]}
        data["machines"]["m1"]["downtime"] = [[20, 30]]
        data["machines"]["m2"]["downtime"] = [[0, 25]]
        original = deepcopy(data)
        result = scheduling_algorithm.jobshop_scheduling(data, compact_schedule=True)
        first, second = result["solution"]["jobs"][0]["operations"]
        self.assertEqual(first["operations_solution"], {"machine_id": "m1", "start": 0, "duration": 20, "end": 20})
        self.assertEqual(second["operations_solution"], {"machine_id": "m2", "start": 25, "duration": 5, "end": 30})
        self.assertEqual(first["durations"], original["jobs"][0]["operations"][0]["durations"])
        self.assertEqual(data, original)

    def test_horizontal_keeps_feasible_schedule_when_compaction_times_out(self):
        data = self.two_operation_data("m2")
        data["jobs"][0]["operations"][1]["deadline"] = 0
        original_solve = cp_model.CpSolver.Solve

        def timeout_compaction(solver, model, *args):
            if model.Proto().solution_hint.vars:
                return cp_model.UNKNOWN
            return original_solve(solver, model, *args)

        with (
            patch.object(AlgorithmsConfig, "SCHEDULING_COMPACT_HORIZONTAL", True),
            patch.object(cp_model.CpSolver, "Solve", timeout_compaction),
        ):
            result = scheduling_batch_scheduler.scheduling_batch_scheduler(
                data, jobs_per_batch=1, batch_direction="horizontal"
            )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["total_jobs"], 1)
        self.assertEqual(result["solution"][0]["job_code"], "b")
        self.assertEqual(result["solution"][0]["operations"][0]["operations_solution"]["start"], 5)
        self.assertEqual([item["job_code"] for item in result["infeasible_jobs"]], ["a"])

    def test_horizontal_recovery_uses_alternative_machine_and_preserves_survivor(self):
        data = self.two_operation_data("m2")
        a, b = data["jobs"]
        a["operations"][0]["durations"] = {"m1": {"machine_duration": 1}, "m2": {"machine_duration": 2}}
        a["operations"][1]["durations"] = {"m1": {"machine_duration": 1}}
        a["operations"][1]["deadline"] = 6
        original = deepcopy(data)
        schedules = {}
        for recover in (False, True):
            with (
                patch.object(AlgorithmsConfig, "SCHEDULING_COMPACT_HORIZONTAL", True),
                patch.object(AlgorithmsConfig, "SCHEDULING_RECOVER_HORIZONTAL", recover),
            ):
                schedules[recover] = scheduling_batch_scheduler.scheduling_batch_scheduler(
                    data, jobs_per_batch=1, batch_direction="horizontal"
                )
        self.assertEqual([item["job_code"] for item in schedules[False]["solution"]], ["b"])
        recovered = schedules[True]
        self.assertEqual(recovered["status"], "success")
        self.assertEqual(recovered["total_jobs"], 2)
        self.assertEqual([item["job_code"] for item in recovered["solution"]], ["a", "b"])
        self.assertEqual(recovered["solution"][1], schedules[False]["solution"][0])
        self.assertEqual(recovered["solution"][0]["operations"][0]["operations_solution"]["machine_id"], "m2")
        self.assertEqual(recovered["infeasible_jobs"], [])
        self.assertEqual(recovered["unscheduled_jobs"], [])
        self.assertEqual(data, original)

    def test_horizontal_recovery_runs_even_when_initially_no_job_is_complete(self):
        data = self.two_operation_data("m2")
        data["jobs"] = data["jobs"][:1]
        first, second = data["jobs"][0]["operations"]
        first["durations"] = {"m1": {"machine_duration": 1}, "m2": {"machine_duration": 5}}
        second["durations"] = {"m2": {"machine_duration": 1}}
        second["deadline"] = 7
        data["machines"]["m1"]["downtime"] = [[0, 10]]
        with (
            patch.object(AlgorithmsConfig, "SCHEDULING_MAKESPAN_WEIGHT", 0),
            patch.object(AlgorithmsConfig, "SCHEDULING_MACHINE_IDLE_WEIGHT", 0),
            patch.object(AlgorithmsConfig, "SCHEDULING_MAKESPAN_OPERATION_WEIGHT", 0),
            patch.object(AlgorithmsConfig, "SCHEDULING_MACHINE_BALANCE_WEIGHT", 100),
            patch.object(AlgorithmsConfig, "SCHEDULING_RECOVER_HORIZONTAL", True),
        ):
            result = scheduling_batch_scheduler.scheduling_batch_scheduler(
                data, jobs_per_batch=1, batch_direction="horizontal"
            )
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["total_jobs"], 1)
        self.assertEqual(result["solution"][0]["operations"][0]["operations_solution"]["machine_id"], "m2")

    def test_horizontal_recovery_timeout_is_unscheduled_in_final_result(self):
        data = self.two_operation_data("m2")
        data["jobs"][0]["operations"][1]["deadline"] = 0
        original_solve = cp_model.CpSolver.Solve

        def timeout_full_route(solver, model, *args):
            names = {var.name for var in model.Proto().variables}
            if {"start_a_OP1", "start_a_OP2"}.issubset(names):
                return cp_model.UNKNOWN
            return original_solve(solver, model, *args)

        with (
            patch.object(AlgorithmsConfig, "SCHEDULING_COMPACT_HORIZONTAL", True),
            patch.object(AlgorithmsConfig, "SCHEDULING_RECOVER_HORIZONTAL", True),
            patch.object(cp_model.CpSolver, "Solve", timeout_full_route),
        ):
            result = scheduling_batch_scheduler.scheduling_batch_scheduler(
                data, jobs_per_batch=1, batch_direction="horizontal"
            )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["total_jobs"], 1)
        self.assertEqual(result["infeasible_jobs"], [])
        self.assertEqual(result["unscheduled_jobs"], [data["jobs"][0]])
        self.assertEqual([item["job_code"] for item in result["solution"]], ["b"])

    def test_job_wait_cost_counts_precedence_gaps_but_not_initial_release(self):
        data = self.two_operation_data("m2")
        data["jobs"] = data["jobs"][:1]
        data["machines"]["m2"]["downtime"] = [[0, 20]]
        costs = []
        original_solve = cp_model.CpSolver.Solve

        def record_wait(solver, model, *args):
            status = original_solve(solver, model, *args)
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                self.assertGreaterEqual(len(args[0].variables), 5, "Job wait cost must be logged")
                costs.append(solver.Value(args[0].variables[4]))
            return status

        with (
            patch.object(AlgorithmsConfig, "SCHEDULING_JOB_WAIT_WEIGHT", 10),
            patch.object(AlgorithmsConfig, "SCHEDULING_MACHINE_IDLE_WEIGHT", 100),
            patch.object(cp_model.CpSolver, "Solve", record_wait),
        ):
            result = scheduling_algorithm.jobshop_scheduling(data, earliest_starts={"a": 5})
        first, second = [op["operations_solution"] for op in result["solution"]["jobs"][0]["operations"]]
        self.assertEqual(first["start"], 5)
        self.assertEqual(second["start"], 20)
        self.assertEqual(costs, [10])

    def test_horizontal_releases_failed_job_before_solving_remaining_group(self):
        for batch_size in (1, 2):
            for status in ("INFEASIBLE", "UNKNOWN"):
                with self.subTest(batch_size=batch_size, status=status):
                    data = self.two_operation_data("m1")
                    a, b = data["jobs"]
                    a["operations"][0]["durations"] = {"m1": {"machine_duration": 10}}
                    a["operations"][1]["durations"] = {"m2": {"machine_duration": 5}}
                    a["operations"][1]["deadline"] = 0 if status == "INFEASIBLE" else 100
                    b["operations"][0]["durations"] = {"m3": {"machine_duration": 8}}
                    b["operations"][1]["deadline"] = 12
                    data["machines"]["m1"]["downtime"] = [[4, 8]]
                    data["machines"]["m3"] = {"downtime": []}
                    original = deepcopy(data)
                    original_solve = cp_model.CpSolver.Solve
                    observed = []
                    original_schedule = scheduling_batch_scheduler.jobshop_scheduling

                    def timeout_failed_job(solver, model, *args):
                        if status == "UNKNOWN" and any(var.name == "start_a_OP2" for var in model.Proto().variables):
                            return cp_model.UNKNOWN
                        return original_solve(solver, model, *args)

                    def record_resources(batch, **kwargs):
                        if [item["job_code"] for item in batch["jobs"]] == ["b"] and batch["jobs"][0]["operations"][0]["operation_id"] == "OP2":
                            observed.append((deepcopy(batch["machines"]["m1"]["downtime"]),
                                             dict(kwargs.get("prior_machine_loads", {}))))
                        return original_schedule(batch, **kwargs)

                    with (
                        patch.object(cp_model.CpSolver, "Solve", timeout_failed_job),
                        patch.object(scheduling_batch_scheduler, "jobshop_scheduling", side_effect=record_resources),
                    ):
                        result = scheduling_batch_scheduler.scheduling_batch_scheduler(
                            data, jobs_per_batch=batch_size, batch_direction="horizontal"
                        )
                    self.assertIsNotNone(result, "Job b must reuse the reservation released by failed job a")
                    self.assertEqual([item["job_code"] for item in result["solution"]], ["b"])
                    self.assertEqual(result["solution"][0]["operations"][1]["operations_solution"]["start"], 8)
                    failures = result["infeasible_jobs" if status == "INFEASIBLE" else "unscheduled_jobs"]
                    self.assertEqual(failures, [original["jobs"][0]])
                    self.assertEqual(observed[0][0], [[4, 8]])
                    self.assertEqual(observed[0][1][("OP1", "m1")], 0)
                    self.assertEqual(observed[0][1][("OP1", "m3")], 8)
                    self.assertEqual(data, original)

    def test_horizontal_release_preserves_surviving_reservations_in_merged_calendar(self):
        data = self.two_operation_data("m2")
        third = deepcopy(data["jobs"][0])
        third["job_code"] = "c"
        data["jobs"].append(third)
        data["jobs"][1]["operations"][0]["durations"] = {"m1": {"machine_duration": 10}}
        data["jobs"][1]["operations"][1]["deadline"] = 0
        observed = []
        original_schedule = scheduling_batch_scheduler.jobshop_scheduling

        def record_resources(batch, **kwargs):
            if batch["jobs"][0]["job_code"] == "c" and batch["jobs"][0]["operations"][0]["operation_id"] == "OP2":
                observed.append((deepcopy(batch["machines"]), dict(kwargs["prior_machine_loads"])))
            return original_schedule(batch, **kwargs)

        with patch.object(scheduling_batch_scheduler, "jobshop_scheduling", side_effect=record_resources):
            result = scheduling_batch_scheduler.scheduling_batch_scheduler(
                data, jobs_per_batch=1, batch_direction="horizontal"
            )
        self.assertEqual([item["job_code"] for item in result["solution"]], ["a", "c"])
        self.assertEqual(observed[0][0]["m1"]["downtime"], [[0, 5], [15, 20]])
        self.assertEqual(observed[0][0]["m2"]["downtime"], [[5, 10]])
        self.assertEqual(observed[0][1][("OP1", "m1")], 10)
        self.assertEqual(observed[0][1][("OP2", "m2")], 5)

    def two_operation_data(self, second_machine="m1"):
        jobs = [job("a"), job("b")]
        for item in jobs:
            item["operations"][0]["operation_id"] = "OP1"
            second = deepcopy(item["operations"][0])
            second["operation_id"] = "OP2"
            second["durations"] = {second_machine: {"machine_duration": 5}}
            item["operations"].append(second)
        return input_data(jobs)

    def test_horizontal_schedules_operation_across_all_job_batches(self):
        data = self.two_operation_data()
        original = deepcopy(data)
        horizontal = scheduling_batch_scheduler.scheduling_batch_scheduler(
            data, jobs_per_batch=1, batch_direction="horizontal"
        )
        vertical = scheduling_batch_scheduler.scheduling_batch_scheduler(data, jobs_per_batch=1)
        self.assertEqual(horizontal["status"], "success")
        self.assertEqual(horizontal["total_jobs"], 2)
        a, b = horizontal["solution"]
        self.assertEqual(b["operations"][0]["operations_solution"]["start"], 5)
        self.assertEqual(a["operations"][1]["operations_solution"]["start"], 10)
        self.assertEqual(vertical["solution"][0]["operations"][1]["operations_solution"]["start"], 5)
        self.assertEqual(data, original)

    def test_horizontal_preserves_precedence_without_global_time_barrier(self):
        result = scheduling_batch_scheduler.scheduling_batch_scheduler(
            self.two_operation_data("m2"), jobs_per_batch=1, batch_direction="horizontal"
        )
        a, b = result["solution"]
        for item in (a, b):
            first, second = [op["operations_solution"] for op in item["operations"]]
            self.assertGreaterEqual(second["start"], first["end"])
        self.assertLess(a["operations"][1]["operations_solution"]["start"],
                        b["operations"][0]["operations_solution"]["end"])

    def test_horizontal_handles_jobs_missing_operations(self):
        data = self.two_operation_data()
        data["jobs"][0]["operations"] = data["jobs"][0]["operations"][1:]
        result = scheduling_batch_scheduler.scheduling_batch_scheduler(
            data, jobs_per_batch=1, batch_direction="horizontal"
        )
        a, b = result["solution"]
        self.assertEqual([op["operation_id"] for op in a["operations"]], ["OP2"])
        self.assertEqual(b["operations"][0]["operations_solution"]["start"], 0)
        self.assertGreaterEqual(b["operations"][1]["operations_solution"]["start"],
                                b["operations"][0]["operations_solution"]["end"])

    def test_horizontal_keeps_original_job_groups_when_operations_are_missing(self):
        data = self.two_operation_data()
        third = deepcopy(data["jobs"][1])
        third["job_code"] = "c"
        data["jobs"].append(third)
        data["jobs"][0]["operations"] = data["jobs"][0]["operations"][1:]
        solved_groups = []
        original_schedule = scheduling_batch_scheduler.jobshop_scheduling

        def record_groups(batch, **kwargs):
            solved_groups.append(tuple(item["job_code"] for item in batch["jobs"]))
            return original_schedule(batch, **kwargs)

        with patch.object(scheduling_batch_scheduler, "jobshop_scheduling", side_effect=record_groups):
            result = scheduling_batch_scheduler.scheduling_batch_scheduler(
                data, jobs_per_batch=2, batch_direction="horizontal"
            )
        self.assertEqual(solved_groups, [("b",), ("c",), ("a", "b"), ("c",)])
        self.assertEqual(result["total_jobs"], 3)

    def test_horizontal_later_failure_returns_only_complete_jobs(self):
        data = self.two_operation_data()
        data["jobs"][0]["operations"][1]["deadline"] = 0
        result = scheduling_batch_scheduler.scheduling_batch_scheduler(
            data, jobs_per_batch=2, batch_direction="horizontal"
        )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["total_jobs"], 1)
        self.assertEqual([item["job_code"] for item in result["solution"]], ["b"])
        self.assertEqual(result["infeasible_jobs"], [data["jobs"][0]])
        self.assertTrue(all("operations_solution" in op for op in result["solution"][0]["operations"]))

    def test_horizontal_rejects_conflicting_operation_orders(self):
        data = self.two_operation_data()
        data["jobs"][1]["operations"].reverse()
        with self.assertRaisesRegex(ValueError, "operation order"):
            scheduling_batch_scheduler.scheduling_batch_scheduler(data, batch_direction="horizontal")

    def test_horizontal_timeout_at_later_operation_is_unscheduled(self):
        data = self.two_operation_data()
        original_solve = cp_model.CpSolver.Solve

        def timeout_second_operation(solver, model, *args):
            if any(var.name == "start_a_OP2" for var in model.Proto().variables):
                return cp_model.UNKNOWN
            return original_solve(solver, model, *args)

        with patch.object(cp_model.CpSolver, "Solve", timeout_second_operation):
            result = scheduling_batch_scheduler.scheduling_batch_scheduler(
                data, jobs_per_batch=2, batch_direction="horizontal"
            )
        self.assertEqual(result["status"], "partial")
        self.assertEqual([item["job_code"] for item in result["solution"]], ["b"])
        self.assertEqual(result["unscheduled_jobs"], [data["jobs"][0]])
        self.assertEqual(result["infeasible_jobs"], [])

    def test_horizontal_returns_none_if_no_job_has_complete_schedule(self):
        data = self.two_operation_data()
        for item in data["jobs"]:
            item["operations"][1]["deadline"] = 0
        self.assertIsNone(scheduling_batch_scheduler.scheduling_batch_scheduler(
            data, jobs_per_batch=2, batch_direction="horizontal"
        ))

    def test_batch_options_reject_invalid_direction_and_size(self):
        for options in ({"batch_direction": "invalid"}, {"jobs_per_batch": 0},
                        {"jobs_per_batch": -1}, {"jobs_per_batch": 1.5}, {"jobs_per_batch": True}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                scheduling_batch_scheduler.scheduling_batch_scheduler(input_data([job("a")]), **options)

    def test_runner_uses_selected_horizontal_direction(self):
        saved = []
        data = self.two_operation_data()
        data["jobs"][0]["operations"][1]["deadline"] = 9
        with (
            patch.object(runner, "BATCH_DIRECTION", "horizontal", create=True),
            patch.object(runner, "JOBS_PER_BATCH", 1, create=True),
            patch.object(runner, "save_schedule", side_effect=lambda result, **kwargs: saved.append(deepcopy(result))),
            patch.object(runner, "transform_results_to_csv", return_value="exported"),
            patch.object(runner, "visualize_machine_view_schedule_chart"),
        ):
            self.assertEqual(runner.run_scheduling(data), "exported")
        self.assertEqual([item["job_code"] for item in saved[0]["solution"]], ["b"])
        self.assertEqual(saved[0]["solution"][0]["operations"][0]["operations_solution"]["start"], 5)

    def test_batch_continues_after_infeasible_job(self):
        for jobs in (
            [job("before"), job("bad", deadline=0), job("after")],
            [job("bad", deadline=0), job("after")],
        ):
            with self.subTest(order=[j["job_code"] for j in jobs]):
                data = input_data(jobs)
                original = deepcopy(data)
                result = scheduling_batch_scheduler.scheduling_batch_scheduler(data, jobs_per_batch=1)
                self.assertIsNotNone(result)
                self.assertEqual(
                    [j["job_code"] for j in result["solution"]],
                    [j["job_code"] for j in jobs if j["job_code"] != "bad"],
                )
                self.assertEqual([j["job_code"] for j in result["infeasible_jobs"]], ["bad"])
                self.assertEqual(result["status"], "partial")
                self.assertEqual(data, original)
                if len(result["solution"]) == 2:
                    before, after = result["solution"]
                    self.assertGreaterEqual(
                        after["operations"][0]["operations_solution"]["start"],
                        before["operations"][0]["operations_solution"]["end"],
                    )

    def test_failed_large_batch_falls_back_without_losing_jobs(self):
        result = scheduling_batch_scheduler.scheduling_batch_scheduler(
            input_data([job("a"), job("bad", deadline=0), job("b"), job("c")]),
            jobs_per_batch=4,
        )
        self.assertEqual([j["job_code"] for j in result["solution"]], ["a", "b", "c"])
        self.assertEqual([j["job_code"] for j in result["infeasible_jobs"]], ["bad"])

    def test_unknown_status_is_not_reported_as_infeasible(self):
        # Force a deterministic timeout without replacing model or batch logic.
        original_solve = cp_model.CpSolver.Solve

        def timeout_first(solver, model, *args):
            if any("start_timeout_op" == var.name for var in model.Proto().variables):
                return cp_model.UNKNOWN
            return original_solve(solver, model, *args)

        with patch.object(cp_model.CpSolver, "Solve", timeout_first):
            result = scheduling_batch_scheduler.scheduling_batch_scheduler(
                input_data([job("timeout"), job("after")]), jobs_per_batch=1
            )
        self.assertIsNotNone(result)
        self.assertEqual([j["job_code"] for j in result["solution"]], ["after"])
        self.assertEqual(result["infeasible_jobs"], [])
        self.assertEqual([j["job_code"] for j in result["unscheduled_jobs"]], ["timeout"])
        self.assertEqual(result["status"], "partial")

    def test_invalid_model_is_not_reported_as_infeasible(self):
        with patch.object(cp_model.CpSolver, "Solve", return_value=cp_model.MODEL_INVALID):
            with self.assertRaises(RuntimeError):
                scheduling_batch_scheduler.scheduling_batch_scheduler(input_data([job("a")]))

    def test_rejects_forty_percent_progress_despite_elapsed_seventy_percent(self):
        self.assertIsNone(self.schedule({"m1": 10}, 14, [(1, 11)]))

    def test_accepts_seventy_percent_processing_before_future_break(self):
        result = self.schedule({"m1": 10}, 7, [(8, 18)])
        self.assertIsNotNone(result)
        self.assertEqual(result["solution"]["jobs"][0]["operations"][0]["operations_solution"],
                         {"machine_id": "m1", "start": 0, "duration": 20, "end": 20})

    def test_deadline_inside_break_uses_only_processing_before_break(self):
        self.assertIsNotNone(self.schedule({"m1": 10}, 12, [(8, 18)]))

    def test_selected_machine_processing_time_controls_threshold(self):
        result = self.schedule({"m1": 10, "m2": 20}, 7, [(8, 18)])
        self.assertIsNotNone(result)
        schedule = result["solution"]["jobs"][0]["operations"][0]["operations_solution"]
        self.assertEqual(schedule["machine_id"], "m1")
        self.assertEqual(schedule["start"], 0)

    def test_progress_boundary_cases(self):
        for duration, deadline, breaks, downtime, feasible in (
            (10, 7, [], [], True),
            (10, 6, [], [], False),
            (3, 2, [], [], False),
            (10, 30, [(20, 25)], [], True),
            (10, 17, [(1, 11)], [], True),
            (20, 19, [(2, 5), (9, 12)], [], False),
            (20, 20, [(2, 5), (9, 12)], [], True),
            (10, 27, [(1, 11)], [(0, 20)], True),
            (10, None, [(1, 11)], [], True),
        ):
            with self.subTest(duration=duration, deadline=deadline, breaks=breaks, downtime=downtime):
                result = self.schedule({"m1": duration}, deadline, breaks, downtime)
                self.assertEqual(result is not None, feasible)

    def test_prior_batch_work_is_not_idle(self):
        objectives = []
        original_solve = cp_model.CpSolver.Solve

        def record_objective(solver, model, *args):
            status = original_solve(solver, model, *args)
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                objectives.append(solver.Value(args[0].variables[1]))
            return status

        with (
            patch.object(cp_model.CpSolver, "Solve", record_objective),
        ):
            result = scheduling_batch_scheduler.scheduling_batch_scheduler(
                input_data([job("a", {"m1": 10}), job("b", {"m1": 5})]),
                jobs_per_batch=1,
            )
        self.assertEqual(objectives, [0, 0])
        self.assertEqual(result["solution"][1]["operations"][0]["operations_solution"]["end"], 15)

    def test_batch_reuses_busy_fast_machine_instead_of_slow_machine(self):
        result = scheduling_batch_scheduler.scheduling_batch_scheduler(
            input_data([job("a", {"m1": 10}), job("b", {"m1": 1, "m2": 20})]),
            jobs_per_batch=1,
        )
        schedule = result["solution"][1]["operations"][0]["operations_solution"]
        self.assertEqual(schedule, {"machine_id": "m1", "start": 10, "duration": 1, "end": 11})

    def test_idle_excludes_only_blocked_time_before_completion(self):
        for duration, breaks, downtime, expected_idle in (
            (5, [], [(0, 10), (20, 30)], 0),
            (15, [], [(0, 10), (20, 30)], 10),
            (5, [], [(-10, 10), (110, 120)], 0),
            (10, [(1, 11)], [(20, 30)], 0),
        ):
            with self.subTest(duration=duration, breaks=breaks, downtime=downtime):
                objectives = []
                original_solve = cp_model.CpSolver.Solve

                def record_objective(solver, model, *args):
                    status = original_solve(solver, model, *args)
                    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                        objectives.append(solver.Value(args[0].variables[1]))
                    return status

                with (
                    patch.object(cp_model.CpSolver, "Solve", record_objective),
                ):
                    self.assertIsNotNone(self.schedule({"m1": duration}, 100, breaks, downtime))
                self.assertEqual(objectives, [expected_idle])

    def test_duration_rounding_is_consistent_for_single_and_alternative_machines(self):
        for durations in ({"m1": 1.5}, {"m1": "1.5", "m2": 2.1}):
            with self.subTest(durations=durations):
                data = input_data([job("a", durations)])
                original = deepcopy(data)
                try:
                    result = scheduling_algorithm.jobshop_scheduling(preprocess_scheduling_input(data))
                except Exception as exc:
                    self.fail(f"Duration normalization must happen before model construction: {exc}")
                self.assertIsNotNone(result)
                op = result["solution"]["jobs"][0]["operations"][0]
                self.assertEqual(op["durations"]["m1"]["machine_duration"], 2)
                self.assertEqual(op["operations_solution"]["duration"], 2)
                if "m2" in durations:
                    self.assertEqual(op["durations"]["m2"]["machine_duration"], 3)
                self.assertEqual(data, original)

    def test_overlapping_downtime_is_merged_before_first_batch(self):
        for scheduler in (scheduling_algorithm.jobshop_scheduling, scheduling_batch_scheduler.scheduling_batch_scheduler):
            with self.subTest(scheduler=scheduler.__name__):
                data = input_data([job("a")], downtime=[(25, 35), (20, 30), (35, 40)])
                original = deepcopy(data)
                result = scheduler(preprocess_scheduling_input(data))
                self.assertIsNotNone(result)
                jobs = result["solution"]["jobs"] if scheduler is scheduling_algorithm.jobshop_scheduling else result["solution"]
                self.assertEqual(jobs[0]["operations"][0]["operations_solution"]["end"], 5)
                self.assertEqual(data, original)

    def test_invalid_identifiers_and_alternatives_raise_clear_errors_before_solving(self):
        empty = input_data([job("empty")])
        empty["jobs"][0]["operations"][0]["durations"] = {}
        unknown = input_data([job("unknown", {"missing_machine": 5})])
        duplicate_jobs = input_data([job("duplicate"), job("duplicate")])
        duplicate_ops = input_data([job("duplicate_op")])
        duplicate_ops["jobs"][0]["operations"] *= 2
        original_solve = cp_model.CpSolver.Solve

        for data, identifier in (
            (empty, "empty"),
            (unknown, "missing_machine"),
            (duplicate_jobs, "duplicate"),
            (duplicate_ops, "duplicate_op"),
        ):
            for scheduler in (preprocess_scheduling_input, runner.run_scheduling):
                with self.subTest(identifier=identifier, scheduler=scheduler.__name__):
                    solved = []

                    def record_solve(solver, model, *args):
                        solved.append(True)
                        return original_solve(solver, model, *args)

                    with patch.object(cp_model.CpSolver, "Solve", record_solve):
                        try:
                            scheduler(deepcopy(data))
                        except Exception as exc:
                            self.assertIsInstance(exc, ValueError)
                            self.assertIn(identifier, str(exc))
                        else:
                            self.fail("Invalid input must raise ValueError")
                    self.assertEqual(solved, [])

    def test_same_operation_id_in_different_jobs_is_valid(self):
        result = scheduling_batch_scheduler.scheduling_batch_scheduler(
            input_data([job("a"), job("b")]), jobs_per_batch=1
        )
        self.assertEqual(result["total_jobs"], 2)

    def test_zero_duration_does_not_create_invalid_reserved_downtime(self):
        result = scheduling_batch_scheduler.scheduling_batch_scheduler(
            input_data([job("zero", {"m1": 0}), job("after")]), jobs_per_batch=1
        )
        self.assertEqual(result["total_jobs"], 2)

    def test_preprocess_merges_and_rounds_without_mutating_input(self):
        from src.utils.input_scheduling_preprocess import preprocess_scheduling_input

        data = input_data([job("a", {"m1": "1.5", "m2": 3})],
                          breaks=[("8.1", "18.1"), (15, 20)],
                          downtime=[("25", "35"), (20, 30), (35, 40), (50, 55)])
        original = deepcopy(data)
        normalized = preprocess_scheduling_input(data)
        self.assertEqual(normalized["machines"]["m1"]["downtime"], [[20, 40], [50, 55]])
        self.assertEqual(normalized["break_times"]["op"], [[9, 20]])
        self.assertEqual(normalized["jobs"][0]["operations"][0]["durations"]["m1"]["machine_duration"], 2)
        self.assertEqual(normalized["jobs"][0]["operations"][0]["durations"]["m2"]["machine_duration"], 3)
        self.assertEqual(data, original)
        self.assertEqual(preprocess_scheduling_input(normalized), normalized)

    def test_preprocess_calculates_machine_duration_before_rounding(self):
        data = input_data([job("a")])
        data["jobs"][0]["quantity"] = 3
        durations = data["jobs"][0]["operations"][0]["durations"]
        durations["m1"] = {"unit_duration": 1.5}
        durations["m2"] = {"unit_duration": "2.1", "machine_duration": 999}
        original = deepcopy(data)
        try:
            normalized = preprocess_scheduling_input(data)
        except ValueError as exc:
            self.fail(f"Preprocessing must calculate durations from unit_duration and quantity: {exc}")
        prepared = normalized["jobs"][0]["operations"][0]["durations"]
        self.assertEqual(prepared["m1"]["machine_duration"], 5)
        self.assertEqual(prepared["m2"]["machine_duration"], 7)
        self.assertEqual(data, original)
        self.assertEqual(preprocess_scheduling_input(normalized), normalized)

    def test_preprocess_rejects_invalid_duration_with_job_context(self):
        from src.utils.input_scheduling_preprocess import preprocess_scheduling_input

        for duration in (None, "invalid", float("nan"), float("inf"), -1, -0.5):
            with self.subTest(duration=duration):
                with self.assertRaisesRegex(ValueError, "Job 'a'.*machine_duration"):
                    preprocess_scheduling_input(input_data([job("a", {"m1": duration})]))

    def test_preprocess_rejects_malformed_downtime(self):
        from src.utils.input_scheduling_preprocess import preprocess_scheduling_input

        for downtime in ([(20, 10)], [(20, 20)], [(20,)], [("invalid", 30)]):
            with self.subTest(downtime=downtime):
                with self.assertRaisesRegex(ValueError, "downtime"):
                    preprocess_scheduling_input(input_data([job("a")], downtime=downtime))


if __name__ == "__main__":
    unittest.main()
