import time
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from config import AlgorithmsConfig
from ortools.sat.python import cp_model
from src.scheduling import scheduling_hybrid_scheduler as hybrid
from src.scheduling import scheduling_algorithm
from src.scheduling.scheduling_batch_scheduler import update_machine_downtime, _update_machine_loads
from src.scheduling.scheduling_algorithm import jobshop_scheduling
from src.scheduling.scheduling_batch_scheduler import scheduling_batch_scheduler
from tests.test_scheduling import job, input_data


class HybridSchedulingTests(unittest.TestCase):
    def setUp(self):
        for name, value in {
            "MINUTES_TIMEOUT": 2 / 60, "NUM_WORKERS": 1,
            "SCHEDULING_MACHINE_IDLE_WEIGHT": 0,
            "SCHEDULING_MACHINE_BALANCE_WEIGHT": 0,
            "SCHEDULING_MAKESPAN_OPERATION_WEIGHT": 0,
            "SCHEDULING_JOB_WAIT_WEIGHT": 0,
            "SCHEDULING_COMPACT_HORIZONTAL": True,
            "SCHEDULING_RECOVER_HORIZONTAL": False,
            "SCHEDULING_HYBRID_TOTAL_SECONDS": 3,
            "SCHEDULING_HYBRID_SEED_FRACTION": 0.2,
            "SCHEDULING_HYBRID_GROUP_SIZE": 2,
            "SCHEDULING_HYBRID_ROUND_SECONDS": 1,
            "SCHEDULING_HYBRID_MAX_ROUNDS": 3,
            "SCHEDULING_COMPACT_HYBRID": True,
        }.items():
            context = patch.object(AlgorithmsConfig, name, value, create=True)
            context.start()
            self.addCleanup(context.stop)

    def test_count_priority_selects_complete_jobs_and_can_omit_impossible_job(self):
        data = input_data([job("short", {"m1": 5}), job("long", {"m1": 20}), job("bad", deadline=0)], breaks=[(0, 1)])
        data["horizon"] = 30
        result = jobshop_scheduling(data, optional_jobs=True, lexicographic=True)
        self.assertEqual({j["job_code"] for j in result["solution"]["jobs"]}, {"short", "long"})

    def test_optional_expired_job_does_not_block_other_jobs(self):
        data = input_data([job("good"), job("expired", deadline=-1)])
        result = jobshop_scheduling(data, optional_jobs=True, lexicographic=True)
        self.assertEqual([j["job_code"] for j in result["solution"]["jobs"]], ["good"])

    def test_global_model_cost_matches_acceptance_score_with_fixed_outside_routes(self):
        a = job("a", {"m1": 5, "m2": 5})
        fixed = job("fixed", {"m2": 7})
        second = deepcopy(fixed["operations"][0])
        second.update(operation_id="op2", durations={"m1": {"machine_duration": 3}, "m2": {"machine_duration": 3}})
        fixed["operations"].append(second)
        for op, machine, start, duration in zip(fixed["operations"], ["m2", "m1"], [0, 7], [7, 3]):
            op["operations_solution"] = {"machine_id": machine, "start": start, "duration": duration, "end": start + duration}
        data = input_data([a, fixed], downtime=[[30, 40]])
        repair_data = deepcopy(data)
        repair_data["jobs"] = [a]
        update_machine_downtime(repair_data["machines"], [fixed])
        loads = {("op", "m1"): 0, ("op", "m2"): 0, ("op2", "m1"): 0, ("op2", "m2"): 0}
        _update_machine_loads(loads, [fixed])
        metrics = []

        def record(callback):
            metrics.append([callback.Value(variable) for variable in callback.variables])

        with patch.object(scheduling_algorithm.SolutionPrinter, "on_solution_callback", record), \
             patch.object(AlgorithmsConfig, "SCHEDULING_MACHINE_IDLE_WEIGHT", 2), \
             patch.object(AlgorithmsConfig, "SCHEDULING_MACHINE_BALANCE_WEIGHT", 3), \
             patch.object(AlgorithmsConfig, "SCHEDULING_JOB_WAIT_WEIGHT", 4), \
             patch.object(AlgorithmsConfig, "SCHEDULING_MAKESPAN_OPERATION_WEIGHT", 5):
            result = jobshop_scheduling(repair_data, optional_jobs=True, lexicographic=True,
                                       prior_machine_loads=loads, fixed_schedule=[fixed], metric_machines=data["machines"])
            score = hybrid.schedule_score(data, result["solution"]["jobs"] + [fixed])
        makespan, idle, operation_end, balance, wait = metrics[-1]
        self.assertEqual(score, (-2, makespan, 2 * idle + 3 * balance + 4 * wait + 5 * operation_end))

    def test_expired_deadline_returns_unknown(self):
        result, status = jobshop_scheduling(input_data([job("a")]), return_status=True,
                                           solve_deadline=time.monotonic() - 1)
        self.assertIsNone(result)
        self.assertEqual(status, "UNKNOWN")

    def test_later_phase_timeout_keeps_complete_solution(self):
        solve = cp_model.CpSolver.Solve
        calls = []

        def solve_first_only(solver, model, callback):
            calls.append(solver.parameters.max_time_in_seconds)
            return solve(solver, model, callback) if len(calls) == 1 else cp_model.UNKNOWN

        with patch.object(cp_model.CpSolver, "Solve", new=solve_first_only):
            result, status = jobshop_scheduling(input_data([job("a"), job("b")]),
                                               return_status=True, optional_jobs=True, lexicographic=True,
                                               time_limit_seconds=0.3)
        self.assertEqual(len(result["solution"]["jobs"]), 2)
        self.assertEqual(status, "FEASIBLE")
        self.assertTrue(all(limit <= 0.3 for limit in calls))

    def test_repair_group_selects_relevant_blocker_over_input_neighbor(self):
        target, unrelated, blocker, late = [job(code) for code in ("target", "unrelated", "blocker", "late")]
        target["operations"][0]["deadline"] = 10
        for j, machine, start in ((unrelated, "m2", 0), (blocker, "m1", 0), (late, "m1", 50)):
            j["operations"][0]["durations"] = {machine: {"machine_duration": 5}}
            j["operations"][0]["operations_solution"] = {
                "machine_id": machine, "start": start, "duration": 5, "end": start + 5}
        data = input_data([target, unrelated, late, blocker])
        group = hybrid.select_neighborhood(data, [unrelated, late, blocker], target, 2)
        self.assertEqual(group, ["target", "blocker"])

    def test_hybrid_rejects_worse_proposal_and_marks_missing_unscheduled(self):
        a, b = job("a"), job("b")
        a["operations"][0]["operations_solution"] = {
            "machine_id": "m1", "start": 0, "duration": 5, "end": 5}
        data = input_data([a, b])
        seed = {"solution": [deepcopy(a)]}
        worse = deepcopy(a)
        worse["operations"][0]["operations_solution"].update(start=20, end=25)
        with patch.object(hybrid, "scheduling_batch_scheduler", return_value=seed), \
             patch.object(hybrid, "jobshop_scheduling", return_value=({"solution": {"jobs": [worse]}}, "FEASIBLE")):
            result = hybrid.schedule_hybrid(data, 1)
        self.assertEqual(result["solution"], [a])
        self.assertEqual([j["job_code"] for j in result["unscheduled_jobs"]], ["b"])
        self.assertFalse(result["infeasible_jobs"])

    def test_hybrid_deadline_covers_seed_and_repair(self):
        data = input_data([job("a")])
        deadline = time.monotonic() - 1
        with patch.object(cp_model.CpSolver, "Solve", side_effect=AssertionError("Expired run must not solve")):
            self.assertIsNone(scheduling_batch_scheduler(data, 1, batch_direction="hybrid", solve_deadline=deadline))

    def test_makespan_priority_dominates_wait_weight(self):
        data = input_data([job("a")], downtime=[(0, 20)])
        data["machines"]["m2"]["downtime"] = []
        op = data["jobs"][0]["operations"][0]
        op["durations"] = {"m1": {"machine_duration": 5}, "m2": {"machine_duration": 20}}
        with patch.object(AlgorithmsConfig, "SCHEDULING_JOB_WAIT_WEIGHT", 10000):
            result = jobshop_scheduling(data, optional_jobs=True, lexicographic=True,
                                       earliest_starts={"a": 10}, previous_operation_ends={"a": 10})
        self.assertEqual(result["solution"]["jobs"][0]["operations"][0]["operations_solution"]["machine_id"], "m1")

    def test_fixed_outside_makespan_allows_wait_improvement(self):
        data = input_data([job("a", {"m1": 5, "m2": 20})], downtime=[(0, 20)])
        data["machines"]["m2"]["downtime"] = []
        data["machines"]["m3"] = {"downtime": [[39, 40]]}
        metric_machines = deepcopy(data["machines"])
        metric_machines["m3"]["downtime"] = []
        outside = job("fixed", {"m3": 1})
        outside["operations"][0]["operations_solution"] = {
            "machine_id": "m3", "start": 39, "duration": 1, "end": 40}
        with patch.object(AlgorithmsConfig, "SCHEDULING_JOB_WAIT_WEIGHT", 10000):
            result = jobshop_scheduling(data, optional_jobs=True, lexicographic=True,
                                       earliest_starts={"a": 10}, previous_operation_ends={"a": 10},
                                       fixed_schedule=[outside], metric_machines=metric_machines)
        self.assertEqual(result["solution"]["jobs"][0]["operations"][0]["operations_solution"]["machine_id"], "m2")

    def test_hybrid_repairs_blocking_routes_without_mutating_input(self):
        a, b = job("a"), job("b")
        for j in (a, b):
            second = deepcopy(j["operations"][0])
            second["operation_id"] = "op2"
            second["deadline"] = 9 if j is a else 100
            j["operations"].append(second)
        data = input_data([a, b])
        original = deepcopy(data)
        seed = scheduling_batch_scheduler(data, 1, batch_direction="horizontal")
        self.assertEqual(seed["total_jobs"], 1)
        result = scheduling_batch_scheduler(data, 1, batch_direction="hybrid")
        self.assertEqual(result["total_jobs"], 2)
        self.assertEqual(result["status"], "success")
        self.assertEqual(data, original)
        tasks = sorted((op["operations_solution"]["start"], op["operations_solution"]["end"])
                       for j in result["solution"] for op in j["operations"])
        self.assertTrue(all(left[1] <= right[0] for left, right in zip(tasks, tasks[1:])))
        for j in result["solution"]:
            first, second = [op["operations_solution"] for op in j["operations"]]
            self.assertLessEqual(first["end"], second["start"])
        self.assertFalse(result["infeasible_jobs"])

    def test_hybrid_compaction_pulls_first_operation_when_next_machine_is_blocked(self):
        a, b = job("a"), job("b")
        for j, first_start, second_start in ((a, 0, 5), (b, 20, 25)):
            second = deepcopy(j["operations"][0])
            second.update(operation_id="op2", durations={"m2": {"machine_duration": 5}})
            j["operations"].append(second)
            for op, machine, start in ((j["operations"][0], "m1", first_start), (second, "m2", second_start)):
                op["operations_solution"] = {"machine_id": machine, "start": start, "duration": 5, "end": start + 5}
        data = input_data([a, b])
        data["machines"]["m2"]["downtime"] = [[10, 25]]
        original = deepcopy(data)
        with patch.object(hybrid, "scheduling_batch_scheduler", return_value={"solution": [a, b]}), \
             patch.object(AlgorithmsConfig, "SCHEDULING_MACHINE_IDLE_WEIGHT", 1), \
             patch.object(AlgorithmsConfig, "SCHEDULING_JOB_WAIT_WEIGHT", 100), \
             patch.object(AlgorithmsConfig, "SCHEDULING_HYBRID_MAX_ROUNDS", 1):
            result = hybrid.schedule_hybrid(data, 1)
        starts = [j["operations"][0]["operations_solution"]["start"] for j in result["solution"]]
        self.assertEqual(sorted(starts), [0, 5])
        self.assertEqual(result["total_jobs"], 2)
        self.assertEqual(max(j["operations"][-1]["operations_solution"]["end"] for j in result["solution"]), 30)
        self.assertEqual([j["operations"][1] for j in result["solution"]], [a["operations"][1], b["operations"][1]])
        self.assertEqual(data, original)

    def test_terminal_compaction_pulls_all_four_operations_with_precedence(self):
        a = job("a")
        a["operations"] = []
        for index, start in enumerate([20, 40, 60, 80], 1):
            a["operations"].append({
                "operation_id": f"op{index}", "deadline": 100,
                "durations": {f"m{index}": {"machine_duration": 5}},
                "operations_solution": {"machine_id": f"m{index}", "start": start, "duration": 5, "end": start + 5},
            })
        data = input_data([a])
        data["machines"].update({"m3": {"downtime": []}, "m4": {"downtime": []}})
        original = deepcopy(data)
        packed = hybrid._compact_hybrid_schedule(data, [a], solve_deadline=time.monotonic() + 1)
        tasks = [op["operations_solution"] for op in packed[0]["operations"]]
        self.assertEqual([task["start"] for task in tasks], [0, 5, 10, 15])
        self.assertEqual([task["end"] for task in tasks], [5, 10, 15, 20])
        self.assertEqual([task["machine_id"] for task in tasks], ["m1", "m2", "m3", "m4"])
        self.assertEqual(data, original)

    def test_route_compaction_handles_shared_physical_machine(self):
        jobs = [job("a"), job("b")]
        for j, first_start, second_start in zip(jobs, [20, 10], [25, 15]):
            second = deepcopy(j["operations"][0])
            second["operation_id"] = "op2"
            j["operations"].append(second)
            for op, start in zip(j["operations"], [first_start, second_start]):
                op["operations_solution"] = {"machine_id": "m1", "start": start, "duration": 5, "end": start + 5}
        data = input_data(jobs, downtime=[[0, 5]])
        original = deepcopy(data)
        packed = hybrid._compact_hybrid_schedule(data, jobs, solve_deadline=time.monotonic() + 1)
        tasks = sorted((op["operations_solution"]["start"], op["operations_solution"]["end"])
                       for j in packed for op in j["operations"])
        self.assertEqual(tasks, [(5, 10), (10, 15), (15, 20), (20, 25)])
        for old, new in zip(jobs, packed):
            first, second = [op["operations_solution"] for op in new["operations"]]
            self.assertLessEqual(first["end"], second["start"])
            for old_op, new_op in zip(old["operations"], new["operations"]):
                self.assertEqual(new_op["operations_solution"]["machine_id"], "m1")
                self.assertLessEqual(new_op["operations_solution"]["start"], old_op["operations_solution"]["start"])
                self.assertLessEqual(new_op["operations_solution"]["end"], old_op["operations_solution"]["end"])
        self.assertEqual(data, original)

    def test_route_compaction_recalculates_break_duration_and_pulls_successor(self):
        a = job("a", deadline=16)
        a["operations"][0]["operations_solution"] = {
            "machine_id": "m1", "start": 8, "duration": 7, "end": 15}
        second = deepcopy(a["operations"][0])
        second.update(operation_id="op2", deadline=50, durations={"m2": {"machine_duration": 5}},
                      operations_solution={"machine_id": "m2", "start": 15, "duration": 5, "end": 20})
        a["operations"].append(second)
        data = input_data([a], breaks=[[10, 12]], downtime=[[0, 3]])
        packed = hybrid._compact_hybrid_schedule(data, [a], solve_deadline=time.monotonic() + 1)
        self.assertEqual(packed[0]["operations"][0]["operations_solution"],
                         {"machine_id": "m1", "start": 3, "duration": 5, "end": 8})
        self.assertEqual(packed[0]["operations"][1]["operations_solution"],
                         {"machine_id": "m2", "start": 8, "duration": 5, "end": 13})

    def test_route_compaction_timeout_keeps_incumbent(self):
        a = job("a")
        a["operations"][0]["operations_solution"] = {
            "machine_id": "m1", "start": 20, "duration": 5, "end": 25}
        original = deepcopy(a)
        with patch.object(cp_model.CpSolver, "Solve", return_value=cp_model.UNKNOWN):
            packed = hybrid._compact_hybrid_schedule(input_data([a]), [a], solve_deadline=time.monotonic() + 1)
        self.assertEqual(packed, [original])

    def test_hybrid_can_disable_terminal_packing(self):
        a = job("a")
        a["operations"][0]["operations_solution"] = {
            "machine_id": "m1", "start": 20, "duration": 5, "end": 25}
        data = input_data([a])
        with patch.object(hybrid, "scheduling_batch_scheduler", return_value={"solution": [a]}), \
             patch.object(hybrid, "jobshop_scheduling", return_value=(None, "UNKNOWN")), \
             patch.object(AlgorithmsConfig, "SCHEDULING_COMPACT_HYBRID", False):
            result = hybrid.schedule_hybrid(data, 1)
        self.assertEqual(result["solution"], [a])

    def test_hybrid_reserves_packing_time_when_repairs_exhaust_their_budget(self):
        a = job("a")
        a["operations"][0]["operations_solution"] = {
            "machine_id": "m1", "start": 20, "duration": 5, "end": 25}
        clock = [time.monotonic()]
        fake_time = SimpleNamespace(monotonic=lambda: clock[0])

        def repair_times_out(data, **kwargs):
            if kwargs.get("compact_schedule"):
                return jobshop_scheduling(data, **kwargs)
            # Simulate a repair consuming its entire assigned budget. Final
            # packing still exercises the real model and returns a usable route.
            clock[0] = kwargs["solve_deadline"] + 0.0001
            return None, "UNKNOWN"

        with patch.object(hybrid, "time", fake_time), \
             patch.object(hybrid, "scheduling_batch_scheduler", return_value={"solution": [a]}), \
             patch.object(hybrid, "jobshop_scheduling", side_effect=repair_times_out):
            result = hybrid.schedule_hybrid(input_data([a]), 1)
        self.assertEqual(result["solution"][0]["operations"][0]["operations_solution"]["start"], 0)


if __name__ == "__main__":
    unittest.main()
