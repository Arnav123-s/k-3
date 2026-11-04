from fractions import Fraction
import itertools
import unittest

from experiments.astra_distribution_navigation.evaluate import disagreement_mass


class TinyWorld:
    def __init__(self, transitions, outputs, initial='s0', actions=('a', 'b', 'c')):
        self.transitions = transitions
        self.outputs = outputs
        self.initial = initial
        self.actions = list(actions)
        self.reset_calls = self.step_calls = 0

    def reset(self):
        self.reset_calls += 1
        return self.outputs[self.initial]

    def step(self, action):
        self.step_calls += 1
        return self.outputs[self.transitions[self.initial][action]]


def one_state_model(actions=('a', 'b', 'c'), output='0'):
    return {"format": "active-moore-machine-v1", "actions": list(actions),
        "initial": 0, "outputs": [output],
        "transitions": [{action: 0 for action in actions}],
        "access": [[]], "suffixes": [[]]}


def trace_world(world, word):
    state = world.initial
    result = [world.outputs[state]]
    for action in word:
        state = world.transitions[state][action]
        result.append(world.outputs[state])
    return result


def brute_mass(world, model, max_length, persistence):
    actions = list(world.actions)
    k = len(actions)
    length_masses = []
    for length in range(max_length+1):
        mismatch_mass = Fraction(0, 1)
        if length == 0:
            words = [((), Fraction(1, 1))]
        else:
            words = []
            for word in itertools.product(actions, repeat=length):
                probability = Fraction(1, k)
                for index in range(1, length):
                    previous, action = word[index-1], word[index]
                    if persistence:
                        probability *= (Fraction(k+1, 2*k) if action == previous
                                        else Fraction(1, 2*k))
                    else:
                        probability *= Fraction(1, k)
                words.append((word, probability))
        for word, probability in words:
            observed = trace_world(world, word)
            predicted = ['0']
            state = model['initial']
            for action in word:
                state = model['transitions'][state][action]
                predicted.append(model['outputs'][state])
            if observed != predicted:
                mismatch_mass += probability
        length_masses.append(mismatch_mass)
    return sum(length_masses, Fraction(0, 1)) / (max_length+1)


def rare_bba_world():
    actions = ('a', 'b', 'c')
    transitions = {
        's0': {'a': 's0', 'b': 's1', 'c': 's0'},
        's1': {'a': 's0', 'b': 's2', 'c': 's0'},
        's2': {'a': 'goal', 'b': 's0', 'c': 's0'},
        'goal': {'a': 's0', 'b': 's0', 'c': 's0'},
    }
    outputs = {'s0': '0', 's1': '0', 's2': '0', 'goal': 'G'}
    return TinyWorld(transitions, outputs, actions=actions)


def delayed_a_world(delay):
    actions = ('a', 'b', 'c')
    states = [f's{i}' for i in range(delay+1)] + ['goal']
    transitions = {state: {action: 's0' for action in actions} for state in states}
    for index in range(delay):
        transitions[f's{index}']['a'] = f's{index+1}'
    transitions[f's{delay}'] = {action: 's0' for action in actions}
    transitions[f's{delay}']['a'] = 'goal'
    outputs = {state: '0' for state in states}
    outputs['goal'] = 'G'
    transitions['goal'] = {action: 's0' for action in actions}
    return TinyWorld(transitions, outputs, actions=actions)


class DistributionEvaluatorTests(unittest.TestCase):
    def test_exact_dp_matches_brute_force_for_multiple_worlds_and_policies(self):
        model = one_state_model()
        worlds = [
            TinyWorld({'s0': {'a': 'g', 'b': 's0', 'c': 's0'},
                       'g': {'a': 'g', 'b': 'g', 'c': 'g'}},
                      {'s0': '0', 'g': 'G'}),
            rare_bba_world(),
            TinyWorld({'s0': {'a': 's1', 'b': 's0', 'c': 's0'},
                       's1': {'a': 's0', 'b': 'g', 'c': 's0'},
                       'g': {'a': 's0', 'b': 's0', 'c': 's0'}},
                      {'s0': '0', 's1': '0', 'g': 'G'}),
        ]
        for world in worlds:
            for max_length in range(6):
                for persistence in (False, True):
                    with self.subTest(length=max_length, persistence=persistence,
                                      states=len(world.transitions)):
                        result = disagreement_mass(world, model,
                            max_length=max_length, persistence=persistence)
                        expected = brute_mass(world, model, max_length, persistence)
                        self.assertEqual(Fraction(result['numerator'], result['denominator']), expected)
                        self.assertAlmostEqual(result['value'], float(expected))
                        self.assertEqual(result['persistence'], persistence)

    def test_rare_error_mass_and_beyond_support_are_exact(self):
        model = one_state_model()
        rare = disagreement_mass(rare_bba_world(), model, max_length=4)
        self.assertEqual(Fraction(rare['numerator'], rare['denominator']), Fraction(8, 405))

        delayed = delayed_a_world(4)
        outside = disagreement_mass(delayed, model, max_length=4)
        inside = disagreement_mass(delayed, model, max_length=5)
        self.assertEqual(Fraction(outside['numerator'], outside['denominator']), Fraction(0, 1))
        self.assertEqual(Fraction(inside['numerator'], inside['denominator']), Fraction(1, 1458))
        self.assertIn('no claim outside support', outside['scope'])

    def test_persistence_probability_matches_copy_or_uniform_definition(self):
        world = rare_bba_world()
        model = one_state_model()
        result = disagreement_mass(world, model, max_length=3, persistence=True)
        # Only bba first reaches G; its persistent probability is 1/3 * 2/3 * 1/6.
        self.assertEqual(Fraction(result['numerator'], result['denominator']), Fraction(1, 108))

    def test_initial_output_mismatch_has_unit_mass_and_never_calls_world(self):
        world = TinyWorld({'s0': {'a': 's0', 'b': 's0', 'c': 's0'}}, {'s0': 'G'})
        before = (world.reset_calls, world.step_calls)
        result = disagreement_mass(world, one_state_model(), max_length=8)
        self.assertEqual(Fraction(result['numerator'], result['denominator']), Fraction(1, 1))
        self.assertEqual((world.reset_calls, world.step_calls), before)

    def test_bounds_model_and_alphabet_are_checked(self):
        world = TinyWorld({'s0': {'a': 's0', 'b': 's0', 'c': 's0'}}, {'s0': '0'})
        for value in (True, -1, 33):
            with self.assertRaises(ValueError):
                disagreement_mass(world, one_state_model(), max_length=value)
        with self.assertRaises(ValueError):
            disagreement_mass(world, one_state_model(('a', 'b')), max_length=2)
        with self.assertRaises(ValueError):
            disagreement_mass(world, {'not': 'a model'})
        oversized = TinyWorld(
            {f's{i}': {'a': f's{i}', 'b': f's{i}', 'c': f's{i}'} for i in range(129)},
            {f's{i}': '0' for i in range(129)})
        with self.assertRaises(ValueError):
            disagreement_mass(oversized, one_state_model(), max_length=1)


if __name__ == '__main__':
    unittest.main()
