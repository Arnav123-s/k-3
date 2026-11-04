import itertools
import unittest
from kuttaka_runtime.planning_memory import compile_model, plan, observe_transition


def row(goal, edges):
    return {'observation': {'goal': goal}, 'transitions': {
        a: {'to': target, 'cost': cost} for a, target, cost in edges}}


class PlanningMemoryTests(unittest.TestCase):
    def test_exhaustive_three_state_one_action(self):
        # Independent reachability/path-cost oracle, including cycles and goals.
        for successors in itertools.product(range(3), repeat=3):
            for goals in itertools.product((False, True), repeat=3):
                for costs in itertools.product((0, 2), repeat=3):
                    model = {str(i): row(goals[i], [('step', str(successors[i]), costs[i])]) for i in range(3)}
                    record, encoder, _ = compile_model(model, complete=True, context='finite')
                    for start in range(3):
                        state, total, seen = start, 0, set()
                        while not goals[state] and state not in seen:
                            seen.add(state)
                            total += costs[state]
                            state = successors[state]
                        answer = plan(record, encoder[str(start)], context='finite')
                        self.assertEqual(answer['status'], 'plan' if goals[state] else 'no_path')
                        if goals[state]:
                            self.assertEqual(answer['cost'], total)

    def test_hidden_consequence_and_weighted_choice(self):
        model = {'start': row(False, [('cheap', 'trap', 0), ('safe', 'bridge', 2)]),
                 'trap': row(False, [('next', 'trap', 1)]),
                 'bridge': row(False, [('next', 'goal', 1)]),
                 'goal': row(True, [])}
        record, encoder, audit = compile_model(model, complete=True, context='v1')
        self.assertEqual(audit['class_counts'][0], 2)
        self.assertNotEqual(encoder['trap'], encoder['bridge'])
        answer = plan(record, encoder['start'], context='v1')
        self.assertEqual(answer['actions'], ['safe', 'next'])
        self.assertEqual(answer['cost'], 3)
        self.assertEqual(plan(record, encoder['start'], context='v2')['status'], 'context_mismatch')

    def test_missing_edge_and_negative_cost_rejected(self):
        for target, cost in [('missing', 1), ('a', -1), ('a', True)]:
            with self.assertRaises(ValueError):
                compile_model({'a': row(False, [('go', target, cost)])}, complete=True, context=None)

    def test_changed_goal_requires_recompilation(self):
        model = {'a': row(False, [('go', 'b', 1)]), 'b': row(False, [('go', 'a', 1)])}
        record, encoder, _ = compile_model(model, complete=True, context='old goal')
        self.assertEqual(len(record['nodes']), 1)
        model['b']['observation']['goal'] = True
        revised, revised_encoder, _ = compile_model(model, complete=True, context='new goal')
        self.assertEqual(len(revised['nodes']), 2)
        self.assertEqual(plan(record, encoder['a'], context='old goal')['status'], 'no_path')
        self.assertEqual(plan(revised, revised_encoder['a'], context='new goal')['cost'], 1)

    def test_partial_model_not_certified(self):
        with self.assertRaisesRegex(ValueError, 'complete-model'):
            compile_model({'a': row(False, [])}, context=None, complete=False)

    def test_monitor_rejects_surprise_without_inventing_state(self):
        model = {'a': row(False, [('go', 'b', 1)]), 'b': row(True, [])}
        record, encoder, _ = compile_model(model, context='v1', complete=True)
        good = observe_transition(record, encoder['a'], 'go', observation={'goal': True}, cost=1, context='v1')
        self.assertEqual(good['state'], encoder['b'])
        bad = observe_transition(record, encoder['a'], 'go', observation={'goal': False}, cost=1, context='v1')
        self.assertEqual(bad['status'], 'model_mismatch')
        self.assertIsNone(bad['state'])
        bad_cost = observe_transition(record, encoder['a'], 'go', observation={'goal': True}, cost=2, context='v1')
        self.assertEqual(bad_cost['status'], 'model_mismatch')

    def test_ceiling_does_not_publish_unstable_partition(self):
        model = {str(i): row(i == 4, [] if i == 4 else [('go', str(i+1), 1)]) for i in range(5)}
        with self.assertRaisesRegex(ValueError, 'ceiling'):
            compile_model(model, context=None, complete=True, max_refinements=1)

    def test_two_action_models_against_bellman_ford(self):
        import random
        rng = random.Random(20261028)
        for _ in range(100):
            model = {str(i): row(rng.randrange(5) == 0,
                     [(a, str(rng.randrange(8)), rng.randrange(5)) for a in ('left', 'right')])
                     for i in range(8)}
            distance = {s: 0 if r['observation']['goal'] else float('inf') for s, r in model.items()}
            for _ in range(len(model)):
                distance = {s: min([distance[s]] + [e['cost'] + distance[e['to']]
                            for e in r['transitions'].values()]) for s, r in model.items()}
            record, encoder, _ = compile_model(model, context=None, complete=True)
            for start in model:
                result = plan(record, encoder[start], context=None)
                if distance[start] == float('inf'):
                    self.assertEqual(result['status'], 'no_path')
                    continue
                self.assertEqual(result['cost'], distance[start])
                state, actual_cost = start, 0
                for action in result['actions']:
                    edge = model[state]['transitions'][action]
                    actual_cost += edge['cost']
                    state = edge['to']
                self.assertTrue(model[state]['observation']['goal'])
                self.assertEqual(actual_cost, result['cost'])

    def test_distinct_observations_prevent_false_compression(self):
        model = {str(i): {'observation': {'goal': False, 'identity_matters': i},
                         'transitions': {}} for i in range(32)}
        _, _, audit = compile_model(model, context=None, complete=True)
        self.assertEqual(audit['retained_states'], 32)
        self.assertGreater(audit['quotient_json_bytes'], audit['source_json_bytes'])
