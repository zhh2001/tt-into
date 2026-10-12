"""独立穷举核对原目标、依赖语义、路径容量与虚拟状态。"""
from itertools import product
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from orchestration.assignment_reference import (Dependency, Flow, Item, expired,
    IntoppGroup, native_intopp_dependencies, feasible_assignments, solve_dinto, solve_intopp, variation_bytes)


def oracle(items, flows, previous, queue=None, tradeoff=None, dependencies=(), distinct=False):
    """不调用被测代码的可行性、目标或依赖函数。"""
    candidates = []
    for choices in product(*[[None] + [f.flow for f in flows if i.node in f.path] for i in items]):
        if any(sum(i.size_bytes for i, chosen in zip(items, choices) if chosen == f.flow) > f.capacity_bytes for f in flows):
            continue
        a = {i.key: chosen for i, chosen in zip(items, choices) if chosen is not None}
        delta = 0
        for i in items:
            for f in flows:
                delta += i.size_bytes * abs(int(a.get(i.key) == f.flow) - int(previous.get(i.key) == f.flow))
        if queue is not None:
            value = queue * delta - tradeoff * sum(i.gain for i in items if i.key in a)
        else:
            score = 0
            for d in dependencies:
                picked = [(n, k) for (n, k), f in a.items() if n in d.nodes and k in d.kinds and (d.flows is None or f in d.flows)]
                count = len({k for _, k in picked}) if distinct and not d.spatial else len(picked)
                score += d.weight * (d.active and count >= len(d.kinds))
            value = -score
        candidates.append((value, delta, tuple(a.get(i.key, 0) for i in sorted(items, key=lambda x: x.key)), a))
    return min(candidates, key=lambda row: row[:3])


class AssignmentTests(unittest.TestCase):
    def test_flow_order_only_selects_among_primary_optima(self):
        items, flows = [Item(1, 1, 2, 5)], [Flow(1, (1,), 2), Flow(2, (1,), 2)]
        deps = [Dependency('s', (1,), (1,), True)]
        for method in ('milp', 'enumeration'):
            d = solve_dinto(items, flows, method=method, flow_order=(2, 1))
            t = solve_intopp(items, flows, deps, method=method, flow_order=(2, 1))
            self.assertEqual(d['assignment'], {(1, 1): 2})
            self.assertEqual(t['assignment'], {(1, 1): 2})
            self.assertEqual(d['p3_objective'], -5)
            self.assertEqual(t['dependency_score'], 1)
        with self.assertRaises(ValueError):
            solve_dinto(items, flows, flow_order=(1, 1))

    def test_native_temporal_filter_and_repeated_node_weight(self):
        deps = native_intopp_dependencies([IntoppGroup('m', (1, 2), True)], (1, 2), (1, 2),
                                          {('m', 1): 10, ('m', 2): 11}, {('m', 1): 10, ('m', 2): 10})
        self.assertEqual([d.weight for d in deps], [1, 1, 2])
        self.assertEqual(deps[-1].flows, (2,))
        items = [Item(1, 1, 2), Item(1, 2, 2)]
        flows = [Flow(1, (1,), 4), Flow(2, (2,), 4)]
        # 空间依赖可以由未到期流满足，它不能计入原式的时间完成数。
        for method in ('milp', 'enumeration'):
            result = solve_intopp(items, flows, deps, method=method)
            self.assertEqual(result['dependency_score'], 1)
        deps = native_intopp_dependencies([IntoppGroup('m', (1, 2), True)], (1, 2), (1, 2),
                                          {('m', 1): None, ('m', 2): 0}, {('m', 1): 10, ('m', 2): 10})
        self.assertEqual(solve_intopp(items, flows, deps)['dependency_score'], 3)

    def test_temporal_filter_applies_to_distinct_strengthening(self):
        deps = [Dependency('t', (1,), (1, 2), False, flows=(2,))]
        items, flows = [Item(1, 1, 2), Item(1, 2, 2)], [Flow(1, (1,), 4), Flow(2, (1,), 0)]
        for semantics in ('count', 'distinct'):
            self.assertEqual(solve_intopp(items, flows, deps, temporal=semantics)['dependency_score'], 0)
        with self.assertRaises(ValueError):
            native_intopp_dependencies([IntoppGroup('m', (1,), True)], (1,), (1,), {}, {})

    def test_exact_objective_lock_with_zero_capacity_and_negative_cost(self):
        items = [Item(1, 1, 3, .5), Item(1, 2, 2, .5), Item(2, 1, 2, 0), Item(2, 2, 2, .5)]
        flows = [Flow(1, (1, 2), 3), Flow(2, (2, 3), 0)]
        result = solve_dinto(items, flows, queue=3, tradeoff=50)
        expected = oracle(items, flows, {}, 3, 50)
        self.assertEqual(result['assignment'], expected[3])
        self.assertEqual(result['p3_objective'], -19)

    def test_dinto_gain_and_negative_objective(self):
        answer = solve_dinto([Item(1, 1, 2, 3), Item(2, 1, 3, 4)], [Flow(1, (1, 2), 3)], tradeoff=2)
        self.assertEqual(answer['assignment'], {(2, 1): 1})
        self.assertEqual(answer['p3_objective'], -8)

    def test_change_of_flow_costs_remove_and_add(self):
        items = [Item(1, 1, 12)]
        self.assertEqual(variation_bytes(items, {(1, 1): 2}, {(1, 1): 1}), 24)
        answer = solve_dinto(items, [Flow(1, (1,), 0), Flow(2, (1,), 12)],
                             {(1, 1): 1}, queue=1, tradeoff=100, average_change_bytes=5)
        self.assertEqual(answer['next_queue'], 20)

    def test_queue_can_prefer_drop_or_retention(self):
        item, flows = [Item(1, 1, 12)], [Flow(1, (1,), 12)]
        self.assertEqual(solve_dinto(item, flows, queue=2)['assignment'], {})
        self.assertEqual(solve_dinto(item, flows, {(1, 1): 1}, queue=2)['assignment'], {(1, 1): 1})

    def test_capacity_is_per_flow_and_route_is_required(self):
        answer = solve_dinto([Item(1, 1, 4), Item(2, 1, 4)], [Flow(1, (1,), 3), Flow(2, (2,), 5)])
        self.assertEqual(answer['assignment'], {(2, 1): 2})

    def test_uniqueness_and_deterministic_tie(self):
        answer = solve_dinto([Item(1, 1, 4)], [Flow(3, (1,), 4), Flow(2, (1,), 4)])
        self.assertEqual(answer['assignment'], {(1, 1): 2})

    def test_empty_qualified_set_and_zero_capacity(self):
        for flows in ([Flow(1, (2,), 12)], [Flow(1, (1,), 0)]):
            self.assertEqual(solve_dinto([Item(1, 1, 12)], flows)['assignment'], {})
            dep = [Dependency('missing', (1,), (1,), True)]
            self.assertEqual(solve_intopp([Item(1, 1, 12)], flows, dep)['dependency_score'], 0)

    def test_spatial_dependency_requires_same_node(self):
        items = [Item(1, 1, 2), Item(2, 2, 2)]
        answer = solve_intopp(items, [Flow(1, (1, 2), 4)], [Dependency('same', (1,), (1, 2), True)])
        self.assertEqual(answer['dependency_score'], 0)

    def test_native_temporal_count_and_distinct_strengthening(self):
        items = [Item(1, 1, 2), Item(2, 1, 2), Item(3, 2, 5)]
        flows, deps = [Flow(1, (1, 2, 3), 4)], [Dependency('group', (1, 2, 3), (1, 2), False, weight=3)]
        self.assertEqual(solve_intopp(items, flows, deps)['dependency_score'], 3)
        self.assertEqual(solve_intopp(items, flows, deps, temporal='distinct')['dependency_score'], 0)

    def test_weight_and_inactive_temporal_group(self):
        items, flows = [Item(1, 1, 2), Item(1, 2, 2)], [Flow(1, (1,), 2)]
        deps = [Dependency('cheap', (1,), (1,), True), Dependency('high', (1,), (2,), False, weight=3)]
        self.assertEqual(solve_intopp(items, flows, deps)['fulfilled_dependencies'], ['high'])
        deps[1] = Dependency('off', (1,), (2,), False, active=False, weight=3)
        self.assertEqual(solve_intopp(items, flows, deps)['fulfilled_dependencies'], ['cheap'])

    def test_expiry_is_strict_and_unknown_not_fresh(self):
        self.assertFalse(expired(10, 10))
        self.assertTrue(expired(10.01, 10))
        self.assertTrue(expired(None, 10))
        self.assertTrue(expired(8, 10, 3))

    def test_queue_recursion_has_no_physical_queue_claim(self):
        items, flows, q, previous = [Item(1, 1, 2, 10)], [Flow(1, (1,), 2)], 0, {}
        for expected in (1, 0, 0):
            result = solve_dinto(items, flows, previous, queue=q, average_change_bytes=1)
            previous, q = result['assignment'], result['next_queue']
            self.assertEqual(q, expected)

    def test_dinto_random_instances_match_independent_brute_force(self):
        rng = random.Random(271)
        for _ in range(24):
            items = [Item(n, 1, rng.randrange(1, 5), rng.choice((0., 0.5, 3., 8.))) for n in (1, 2, 3)]
            flows = [Flow(1, (1, 2), rng.randrange(0, 8)), Flow(2, (2, 3), rng.randrange(0, 8))]
            previous = {(2, 1): rng.choice((1, 2))} if rng.random() < .5 else {}
            q, v = rng.choice((0., .5, 10.)), rng.choice((1., 4.))
            expected = oracle(items, flows, previous, q, v)
            for method in ('milp', 'enumeration'):
                result = solve_dinto(items, flows, previous, q, v, method=method)
                self.assertEqual(result['assignment'], expected[3])
                self.assertEqual(result['p3_objective'], expected[0])

    def test_intopp_random_dependency_models_match_independent_brute_force(self):
        rng = random.Random(811)
        for _ in range(16):
            items = [Item(n, k, rng.randrange(1, 4)) for n, k in ((1, 1), (1, 2), (2, 1), (2, 2))]
            flows = [Flow(1, (1, 2), rng.randrange(0, 8)), Flow(2, (2,), rng.randrange(0, 5))]
            previous = {(2, 1): 2} if rng.random() < .5 else {}
            deps = [Dependency('s', (1,), (1, 2), True, weight=2),
                    Dependency('t', (1, 2), (1, 2), False, active=rng.choice((True, False)), weight=4)]
            for distinct in (False, True):
                expected = oracle(items, flows, previous, dependencies=deps, distinct=distinct)
                for method in ('milp', 'enumeration'):
                    result = solve_intopp(items, flows, deps, previous, temporal='distinct' if distinct else 'count', method=method)
                    self.assertEqual(result['assignment'], expected[3])
                    self.assertEqual(result['dependency_score'], -expected[0])

    def test_invalid_model_parameters_are_rejected(self):
        items, flows = [Item(1, 1, 2)], [Flow(1, (1,), 2)]
        for params in ({'queue': -1}, {'tradeoff': 0}, {'queue': float('nan')}, {'previous': {(2, 1): 1}}):
            with self.assertRaises(ValueError):
                solve_dinto(items, flows, **params)
        for deps in ([Dependency('a', (1, 2), (1,), True)], [Dependency('a', (1,), (1,), False, weight=-1)]):
            with self.assertRaises(ValueError):
                solve_intopp(items, flows, deps)
        with self.assertRaises(ValueError):
            list(feasible_assignments(items, flows, maximum=1))
        with self.assertRaises(ValueError):
            expired(-1, 10)


if __name__ == '__main__':
    unittest.main()
