"""Regression coverage for the Hybrid performance review."""
import time
import unittest
from copy import deepcopy
from unittest.mock import patch

from ortools.sat.python import cp_model

from config import AlgorithmsConfig as C
from src.scheduling import scheduling_algorithm as algorithm
from src.scheduling import scheduling_hybrid_scheduler as hybrid
from tests.test_scheduling import input_data, job


class HybridReviewTests(unittest.TestCase):
    def setUp(self):
        for name, value in {
            'NUM_WORKERS': 1, 'MINUTES_TIMEOUT': 2 / 60,
            'SCHEDULING_HYBRID_TOTAL_SECONDS': 3,
            'SCHEDULING_HYBRID_ROUND_SECONDS': 1,
            'SCHEDULING_HYBRID_MAX_ROUNDS': 3,
            'SCHEDULING_HYBRID_GROUP_SIZE': 6,
            'SCHEDULING_COMPACT_HORIZONTAL': False,
            'SCHEDULING_RECOVER_HORIZONTAL': False,
            'SCHEDULING_COMPACT_HYBRID': False,
        }.items():
            context = patch.object(C, name, value)
            context.start()
            self.addCleanup(context.stop)
        for module in (hybrid, algorithm):
            context = patch.object(module, 'logger')
            context.start()
            self.addCleanup(context.stop)

    def test_secondary_phase_caps_time_and_uses_gap_only_after_primary_priorities(self):
        solve = cp_model.CpSolver.Solve
        phases = []

        def record(solver, model, callback):
            phases.append((solver.parameters.max_time_in_seconds, solver.parameters.relative_gap_limit))
            return solve(solver, model, callback)

        with patch.object(C, 'SCHEDULING_HYBRID_SECONDARY_SECONDS', 0.05, create=True), \
             patch.object(C, 'SCHEDULING_HYBRID_SECONDARY_FRACTION', 0.25, create=True), \
             patch.object(C, 'SCHEDULING_HYBRID_SECONDARY_GAP_LIMIT', 0.02, create=True), \
             patch.object(cp_model.CpSolver, 'Solve', record):
            result = algorithm.jobshop_scheduling(input_data([job('a')]), optional_jobs=True,
                                                   lexicographic=True, time_limit_seconds=1)
        self.assertEqual(len(result['solution']['jobs']), 1)
        self.assertEqual(len(phases), 3)
        self.assertEqual([gap for _, gap in phases], [0, 0, 0.02])
        self.assertLessEqual(phases[2][0], 0.05)

    def test_secondary_cap_scales_to_short_shared_budget(self):
        solve = cp_model.CpSolver.Solve
        limits = []

        def record(solver, model, callback):
            limits.append(solver.parameters.max_time_in_seconds)
            return solve(solver, model, callback)

        with patch.object(C, 'SCHEDULING_HYBRID_SECONDARY_SECONDS', 5, create=True), \
             patch.object(C, 'SCHEDULING_HYBRID_SECONDARY_FRACTION', 0.25, create=True), \
             patch.object(cp_model.CpSolver, 'Solve', record):
            result = algorithm.jobshop_scheduling(input_data([job('a')]), optional_jobs=True,
                                                   lexicographic=True, time_limit_seconds=5,
                                                   solve_deadline=time.monotonic() + 0.4)
        self.assertEqual(len(result['solution']['jobs']), 1)
        self.assertEqual(len(limits), 3)
        self.assertLessEqual(limits[-1], 0.1)

    def test_gap_based_optimal_without_matching_bound_is_only_feasible(self):
        original_bound = cp_model.CpSolver.best_objective_bound

        def approximate_bound(solver):
            bound = original_bound.fget(solver)
            return bound - 1 if solver.parameters.relative_gap_limit else bound

        # Solve all phases for real; simulate the secondary phase reporting
        # OPTIMAL on gap tolerance while its lower bound is still one below.
        with patch.object(C, 'SCHEDULING_HYBRID_SECONDARY_GAP_LIMIT', 0.02), \
             patch.object(cp_model.CpSolver, 'best_objective_bound', property(approximate_bound)):
            result, status = algorithm.jobshop_scheduling(input_data([job('a'), job('b')]),
                                                          optional_jobs=True, lexicographic=True,
                                                          return_status=True)
        self.assertEqual(len(result['solution']['jobs']), 2)
        self.assertEqual(status, 'FEASIBLE')

    def test_invalid_secondary_settings_raise_value_error(self):
        invalid = {
            'SCHEDULING_HYBRID_SECONDARY_SECONDS': [0, -1, None, '5', True, float('inf'), float('nan')],
            'SCHEDULING_HYBRID_SECONDARY_FRACTION': [0, 1.1, None, '0.25', True, float('nan')],
            'SCHEDULING_HYBRID_SECONDARY_GAP_LIMIT': [-0.1, 1, None, '0.02', True, float('nan')],
        }
        for name, values in invalid.items():
            for value in values:
                with self.subTest(setting=name, value=value), patch.object(C, name, value):
                    with self.assertRaises(ValueError):
                        algorithm.jobshop_scheduling(input_data([job('a')]),
                                                     optional_jobs=True, lexicographic=True)

    def test_seed_schedules_urgent_job_even_when_input_lists_it_last(self):
        data = input_data([job('loose', {'m1': 10}), job('urgent', deadline=4)])
        original = deepcopy(data)
        with patch.object(hybrid, 'jobshop_scheduling', return_value=(None, 'UNKNOWN')):
            result = hybrid.schedule_hybrid(data, 1)
        self.assertEqual([item['job_code'] for item in result['solution']], ['loose', 'urgent'])
        self.assertEqual(result['solution'][1]['operations'][0]['operations_solution']['start'], 0)
        self.assertEqual(data, original)

    def test_no_deadline_selects_local_blocker_instead_of_long_late_task(self):
        target = job('target', deadline=None)
        near, late = job('near'), job('late', {'m1': 50})
        for item, start, duration in ((near, 0, 5), (late, 40, 50)):
            item['operations'][0]['operations_solution'] = {
                'machine_id': 'm1', 'start': start, 'duration': duration, 'end': start + duration}
        data = input_data([target, late, near])
        self.assertEqual(hybrid.select_neighborhood(data, [late, near], target, 2), ['target', 'near'])

    def test_scheduled_target_without_deadline_uses_incumbent_window(self):
        target, early, blocker = job('target', deadline=None), job('early', {'m1': 20}), job('blocker')
        for item, start, duration in ((target, 45, 5), (early, 0, 20), (blocker, 40, 5)):
            item['operations'][0]['operations_solution'] = {
                'machine_id': 'm1', 'start': start, 'duration': duration, 'end': start + duration}
        data = input_data([target, early, blocker])
        self.assertEqual(hybrid.select_neighborhood(data, [target, early, blocker], target, 2),
                         ['target', 'blocker'])

    def test_failed_small_neighborhood_expands_before_giving_up_target(self):
        target = job('target', deadline=4)
        incumbents = [job(str(index)) for index in range(5)]
        for index, item in enumerate(incumbents):
            start = 10 + index * 5
            item['operations'][0]['operations_solution'] = {
                'machine_id': 'm1', 'start': start, 'duration': 5, 'end': start + 5}
        data = input_data([target] + incumbents)
        sizes = []

        def constrained_repair(repair_data, **kwargs):
            sizes.append(len(repair_data['jobs']))
            # Deliberately exhaust the smaller attempts; the expanded attempt
            # uses the real CP-SAT model to recover the missing route.
            if len(repair_data['jobs']) < 6:
                return None, 'UNKNOWN'
            return algorithm.jobshop_scheduling(repair_data, **kwargs)

        with patch.object(C, 'SCHEDULING_HYBRID_INITIAL_GROUP_SIZE', 2, create=True), \
             patch.object(hybrid, 'scheduling_batch_scheduler', return_value={'solution': incumbents}), \
             patch.object(hybrid, 'jobshop_scheduling', side_effect=constrained_repair):
            result = hybrid.schedule_hybrid(data, 1)
        self.assertEqual(sizes, [2, 4, 6])
        self.assertEqual(result['total_jobs'], 6)
        self.assertEqual(result['solution'][0]['operations'][0]['operations_solution']['start'], 0)

    def test_break_model_uses_at_most_three_booleans_per_break(self):
        solve = cp_model.CpSolver.Solve
        counts = []

        def record(solver, model, callback):
            counts.append(sum(list(variable.domain) == [0, 1] for variable in model.proto.variables))
            return solve(solver, model, callback)

        with patch.object(cp_model.CpSolver, 'Solve', record):
            baseline = algorithm.jobshop_scheduling(input_data([job('a')]))
            with_breaks = algorithm.jobshop_scheduling(
                input_data([job('a')], breaks=[(1, 3), (6, 8), (12, 14)]))
        self.assertIsNotNone(baseline)
        self.assertIsNotNone(with_breaks)
        self.assertLessEqual(counts[1] - counts[0], 9)


if __name__ == '__main__':
    unittest.main()
