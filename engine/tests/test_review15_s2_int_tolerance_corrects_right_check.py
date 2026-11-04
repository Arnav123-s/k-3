"""S2 (wrong answer delivered as checked, a right check overwritten and kept as confirmed; regression vs d330165).
_test_set_aside (kuttaka_runtime/solve.py:914-939) corrects a set-aside hand check to the METHOD's value when the
method is _close to the blind value (solve.py:211-212, _close at 338-350: 0.5% relative, integers included). An
auditor and a method that are each wrong by a little, and differ from each other, become "two derivations": the
right hand value is replaced by the method's, labelled two derivations, and remembered as confirmed for the task
(paramparā carries it into later rounds and replans).

Scenario: count of primes <= n. Hand checks all right: 10 -> 4, 20 -> 8, 100 -> 25, 10000 -> 1229. The blind
auditor is wrong at 10000 (1228); the model's method is wrong from 1000 on (one too many: 1230 at 10000).
Requested input 5000 (right 669; the method answers 670).
  e7cb51d: 10000 is set aside before code; the method, written without it, is "corrected" in (1230, two
           derivations, confirmed); 670 is delivered as checked.
  d330165: the right check stays; the wrong method fails it; nothing is delivered.
Control: an auditor further off (1250): the method is contradicted and refuted; nothing wrong is delivered.
Scripted models; local executor; every method is written here. Run: python3 <this file>."""
import os
import sys
import tempfile
import unittest
from pathlib import Path


from kuttaka_runtime.solve import Workspace   # noqa: E402
from tests.test_solve_review_regressions import Model, check, events, make_solver, store   # noqa: E402

TASK = 'Prime count: run(n) returns how many primes are <= n.'
WRONG = ('def run(n):\n    sieve = [True] * (n + 1)\n    count = 0\n    for k in range(2, n + 1):\n'
         '        if sieve[k]:\n            count += 1\n            for j in range(k * k, n + 1, k):\n'
         '                sieve[j] = False\n    return count + (1 if n >= 1000 else 0)\n')
CHECKS = [check(10, 4), check(20, 8), check(100, 25), check(10000, 1229)]   # all right


def pi(n):
    return sum(1 for k in range(2, n + 1) if all(k % d for d in range(2, int(k ** 0.5) + 1)))


def solve(at_10000):
    plan = lambda c: {'contract': 'run(n) returns the number of primes p <= n', 'input': 5000, 'checks': CHECKS}
    audit = lambda c: {'expected': [at_10000 if n == 10000 else pi(n) for n in c['inputs']]}
    ws = Workspace(Path(tempfile.mkdtemp()))
    solver, _ = make_solver(Model(plan, lambda r: store(WRONG), audit=audit), ws=ws, max_calls=20)
    result = solver.solve(TASK)
    print('\nauditor %d at 10000: %s, answer %r (right 669) | set aside tested %s | confirmed %s' % (
        at_10000, result['status'], result.get('answer'), events(result, 'solve_set_aside_tested'),
        [(c['input'], c['expected']) for c in ws._read(ws.confirmed_path)]), file=sys.stderr)
    return result, ws


class S2IntegerToleranceCorrectsARightCheck(unittest.TestCase):
    def test_control_auditor_far_off(self):
        result, ws = solve(1250)
        self.assertNotEqual(result.get('answer'), 670)
        self.assertFalse(ws._read(ws.confirmed_path))

    def test_auditor_and_method_each_off_by_one(self):
        result, ws = solve(1228)
        self.assertNotIn(check(10000, 1230), result['checks'], 'a right hand check is replaced by the method value')
        self.assertFalse([c for c in ws._read(ws.confirmed_path) if c['expected'] == 1230],
                         'the wrong value is remembered as confirmed for the task')
        self.assertNotEqual(result.get('answer'), 670, 'the wrong answer is delivered as checked')


if __name__ == '__main__':
    unittest.main()
