"""Inputs varied in form, not only in number (pramana.varied_inputs), for the tests beyond the checks. The unsolved
tasks of the recorded hard runs (h-c1-regex, h-c2-substrings, h-c3-maxflow) were all methods delivered as checked that
were right on every check and wrong beyond them; the regex method handled only lower-case letters, and the inputs
generated then moved only numbers. Scripted models; local executor; every method and the reference are written here."""
import unittest

from kuttaka_runtime.pramana import varied_inputs
from tests.test_solve_review_regressions import Model, check, events, make_solver, store

TASK = 'Full match: run({"pattern", "text"}) returns whether the whole text matches the pattern (. * + ? | ( )).'
REFERENCE = 'import re\n\n\ndef reference(i):\n    return re.fullmatch(i["pattern"], i["text"]) is not None\n'
RIGHT = 'import re\n\n\ndef run(i):\n    return re.fullmatch(i["pattern"], i["text"]) is not None\n'
LOWER_ONLY = ('import re\n\n\ndef run(i):\n    text = "".join(c for c in i["text"] if "a" <= c <= "z")\n'
              '    return re.fullmatch(i["pattern"], text) is not None\n')   # skips every other character
CHECKS = [check({'pattern': 'a*b', 'text': 'aab'}, True), check({'pattern': 'a|b', 'text': 'c'}, False),
          check({'pattern': '(ab)+', 'text': 'abab'}, True), check({'pattern': 'a.c', 'text': 'abc'}, True)]


def truth(i):
    import re
    try:
        return re.fullmatch(i['pattern'], i['text']) is not None
    except re.error:
        return None


class VariedInputs(unittest.TestCase):
    def test_texts_lists_and_fields_are_varied(self):
        made = varied_inputs([{'pattern': 'a*b', 'text': 'aab'}, [3, 1, 2]])
        self.assertIn({'pattern': 'a*b', 'text': 'Aab'}, made)
        self.assertIn([1, 2, 3], made)
        self.assertTrue(all(isinstance(v, (dict, list)) for v in made))

    def test_a_method_right_on_lower_case_only_is_met_and_rewritten(self):
        writes = []

        def act(request):
            writes.append(request)
            return store(LOWER_ONLY if len(writes) == 1 else RIGHT)
        plan = lambda c: {'contract': 'run(i) returns whether i["text"] fully matches the regular expression i["pattern"]',
                          'input': {'pattern': 'a+', 'text': 'aaa'}, 'checks': CHECKS, 'reference': REFERENCE,
                          'small_inputs': [{'pattern': 'a', 'text': 'a'}]}
        solver, ws = make_solver(Model(plan, act, audit=lambda c: {'expected': [truth(i) for i in c['inputs']]}),
                                 max_calls=24)
        result = solver.solve(TASK)
        self.assertTrue(any(e.get('outcome') == 'disagree' for e in events(result, 'solve_differential')))
        self.assertEqual((result['status'], result.get('answer')), ('checked', True))
        self.assertEqual(len(writes), 2, 'the lower-case-only method was met beyond the checks and rewritten')

    def test_control_without_varied_inputs_the_lower_case_method_is_delivered(self):
        from unittest import mock
        with mock.patch('kuttaka_runtime.pramana.varied_inputs', lambda inputs, limit=16: []):
            writes = []

            def act(request):
                writes.append(request)
                return store(LOWER_ONLY if len(writes) == 1 else RIGHT)
            plan = lambda c: {'contract': 'run(i) returns whether i["text"] fully matches i["pattern"]',
                              'input': {'pattern': 'a+', 'text': 'aaa'}, 'checks': CHECKS, 'reference': REFERENCE,
                              'small_inputs': [{'pattern': 'a', 'text': 'a'}]}
            solver, ws = make_solver(Model(plan, act, audit=lambda c: {'expected': [truth(i) for i in c['inputs']]}),
                                     max_calls=24)
            result = solver.solve(TASK)
        self.assertEqual(len(writes), 1, 'nothing met the lower-case-only method: it was delivered')


if __name__ == '__main__':
    unittest.main()
