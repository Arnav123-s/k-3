"""The substitution test (kuttaka_runtime/check_substitution.py): a literal written for one input, found by running
the method with each constant changed, whatever its form. The builder's tests, after the ninth review showed the
text reading could always be written around (its P-c forms are here, end to end).

Every method is written for this test and run only by the local executor (the sandbox's runner in a subprocess)."""
import json
import unittest

from kuttaka_runtime import check_substitution
from kuttaka_runtime.execution import PythonExecutor, preflight
from kuttaka_runtime.check_memorization import memorized_checks
from tests.review11_triple import jobs
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import check

CHECKS = [check(4, 12), check(5, 15), check(7, 22)]
UNSEEN = {   # forms the text reading does not see
    'named literal': 'SPECIAL = 22\n\n\ndef run(n):\n    if n == 7:\n        return SPECIAL\n    return 3 * n\n',
    'table filled by assignment': 'FIX = {}\nFIX[7] = 22\n\n\ndef run(n):\n    return FIX.get(n, 3 * n)\n',
    'list of pairs': 'FIX = [(7, 22)]\n\n\ndef run(n):\n    for k, v in FIX:\n        if n == k:\n            return v\n    return 3 * n\n',
    'and-or': 'def run(n):\n    return n == 7 and 22 or 3 * n\n',
    'lambda': 'def run(n):\n    pick = lambda m: 22 if m == 7 else 3 * m\n    return pick(n)\n',
    'arithmetic on a guard': 'def run(n):\n    return 3 * n + (n == 7)\n',
}


def test(source, checks, known=(), supported=(), executor=local_executor):
    prepared = check_substitution.prepare({'': source}, checks, known)
    if prepared is None:
        return None
    response = executor({'source': check_substitution.RUN, 'input': prepared['payload']})
    ok = response.get('status') == 'completed' and response.get('exit_code') == 0
    rows = json.loads(response['output'])['result'] if ok else None
    return check_substitution.read(prepared, rows, checks, supported)


class SubstitutionTest(unittest.TestCase):
    def test_forms_the_text_misses_are_found_by_running(self):
        for name, source in UNSEEN.items():
            with self.subTest(form=name):
                self.assertEqual(memorized_checks(source, CHECKS), [], 'precondition: the text reading misses it')
                found = test(source, CHECKS)
                self.assertIsNotNone(found)
                self.assertIn(7, found['judged'])
                self.assertEqual([c['input'] for c, _ in found['special']], [7])

    def test_rules_base_cases_and_memberships_are_not_special(self):
        stairs = ('def run(n):\n    if n == 0:\n        return 1\n    if n == 1:\n        return 1\n    if n == 2:\n'
                  '        return 2\n    if n == 3:\n        return 4\n    return run(n - 1) + run(n - 2) + run(n - 3)\n')
        months = 'def run(m):\n    if m in (4, 6, 9, 11):\n        return 30\n    if m == 2:\n        return 28\n    return 31\n'
        cases = {'rule': ('def run(n):\n    return 3 * n\n', CHECKS),
                 'base case': (stairs, [check(3, 4), check(5, 13)]),
                 'membership': (months, [check(4, 30), check(1, 31), check(12, 31)])}
        for name, (source, checks) in cases.items():
            with self.subTest(case=name):
                found = test(source, checks)
                self.assertTrue(found is None or found['special'] == [], found)

    def test_a_whole_table_is_logic_only_with_support(self):
        table = 'def run(move):\n    return {"rock": "paper", "paper": "scissors", "scissors": "rock"}[move]\n'
        checks = [check('rock', 'paper'), check('paper', 'scissors'), check('scissors', 'rock')]
        self.assertEqual(test(table, checks, supported=[c['input'] for c in checks])['special'], [])
        self.assertEqual(len(test(table, checks)['special']), 3, 'without support it is a table of the checks')

    def test_an_input_the_method_does_not_repeat_itself_on_is_not_used(self):
        noisy = 'import random\n\n\ndef run(n):\n    if n == 7:\n        return 22\n    return 3 * n + random.random()\n'
        found = test(noisy, CHECKS)   # every other input changes between two runs: too little to judge 7 by
        self.assertIsNotNone(found)
        self.assertNotIn(7, found['judged'])


class EndToEnd(unittest.TestCase):
    def test_each_unseen_form_is_not_delivered_and_a_later_task_answers_right(self):
        for name, source in UNSEEN.items():
            with self.subTest(form=name):
                # Adapted after the tenth review: what only the substitution test finds is put to the blind
                # auditor; here it is right (3 * n), disputes 7 -> 22, and the method is set aside.
                first, later = jobs(source, CHECKS, 7, [check(4, 12), check(5, 15)],
                                    audit=lambda context: {'expected': [3 * n for n in context['inputs']]})
                self.assertNotEqual(first['status'], 'checked')
                self.assertEqual((later['status'], later.get('answer')), ('checked', 21))



class InTheSandbox(unittest.TestCase):
    """The same test in the pinned Docker image the solver uses (skipped where Docker or the image is missing):
    the run script's timers, its payload within the job limit, and its verdicts, as on the owner's machine."""

    @unittest.skipUnless(preflight()['ready'], 'Docker or the pinned image is unavailable')
    def test_the_substitution_test_runs_in_the_docker_sandbox(self):
        executor = PythonExecutor(timeout=20, trace='distinct')
        found = test(UNSEEN['named literal'], CHECKS, executor=executor)
        self.assertIsNotNone(found, 'the run was cut short or rejected by the sandbox')
        self.assertEqual([c['input'] for c, _ in found['special']], [7])
        # A rule has no constant equal to a check value: nothing to substitute, so no test (None) and no hit.
        # (Corrected 2026-11-01: this line indexed the None and failed on the owner's machine; the local
        # twin, test_rules_base_cases_and_memberships_are_not_special, already allowed None.)
        rule = test('def run(n):\n    return 3 * n\n', CHECKS, executor=executor)
        self.assertTrue(rule is None or rule['special'] == [], rule)
        slow = 'def run(n):\n    while n == 7:\n        pass\n    return 3 * n\n'   # a call that never ends is cut
        self.assertIsNotNone(test(slow, CHECKS, executor=executor))


if __name__ == '__main__':
    unittest.main()
