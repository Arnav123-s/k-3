"""Others' work as a witness (the owner, 2026-11-02: "knowing isn't the same as learning; if you can't verify your own
work then you look for others' works that can verify your work"). In first-hand sessions n to q the builder's
answers were shown right only by published sequences, read afterwards; Kuttaka never looked.

Scenario: run({"n": n}) returns the n-th triangular number, n(n+1)/2; requested input {"n": 1000}. The published
table (a stand-in for the OEIS: the same JSON search and b-file formats, no network) holds A000217. A method right
on every small check but wrong above 100 (an off-by-one on a fast path) passes every check; the table contradicts
it at the larger indices; the actor, shown the published values, fixes it; checked, and the testimony is kept.
Controls: a right method is confirmed and not asked again; with no witness nothing is asked (the default); a task
that is not a sequence is not asked; a method the table still contradicts is not delivered as checked."""
import json
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Metered, Solver, Workspace
from kuttaka_runtime.witness import OEISWitness
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, events, store

RIGHT = 'def run(x):\n    n = x["n"]\n    return n * (n + 1) // 2\n'
WRONG_ABOVE_100 = 'def run(x):\n    n = x["n"]\n    return n * (n + 1) // 2 if n <= 100 else n * (n - 1) // 2\n'
CHECKS = [check({'n': 0}, 0), check({'n': 1}, 1), check({'n': 4}, 10), check({'n': 10}, 55)]


def published(url):
    """The OEIS's answers for A000217, in its own formats."""
    if '/search?' in url:
        data = ','.join(str(n * (n + 1) // 2) for n in range(40))
        return json.dumps([{'number': 217, 'name': 'Triangular numbers: a(n) = binomial(n+1,2) = n*(n+1)/2.',
                            'offset': '0,3', 'data': data}])
    if url.endswith('/A000217/b000217.txt'):
        return '\n'.join('%d %d' % (n, n * (n + 1) // 2) for n in range(10001))
    raise OSError('not found')


def solve(first, fixes=True, witness=True, checks=CHECKS, task='The n-th triangular number, for n = 1000.'):
    requests = []

    def act(request):
        requests.append(request)
        return store(RIGHT if fixes and 'published sequence' in request else first)
    plan = lambda c: {'contract': 'run({"n": n}) returns n(n+1)/2 for 0 <= n <= 10^6', 'input': {'n': 1000},
                      'checks': checks}
    m = Metered(Model(plan, act, audit=lambda c: {}), {'used': 0, 'limit': 10})
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, local_executor, splitter=m, auditor=m, sketcher=m,
                    max_executions=256, say=lambda _: None, witness=OEISWitness(fetch=published) if witness else None)
    return solver.solve(task), requests


class PublishedWitness(unittest.TestCase):
    def test_a_method_the_table_contradicts_is_shown_the_published_values_and_fixed(self):
        result, requests = solve(WRONG_ABOVE_100)
        self.assertEqual((result['status'], result.get('answer')), ('checked', 500500))
        self.assertIn('witness', result['strategies'])
        self.assertIn('A000217', requests[1])
        self.assertEqual((result['witness']['sequence'], result['witness']['outcome']), ('A000217', 'agrees'))
        self.assertEqual(result['witness']['at_requested'], 500500)

    def test_a_method_the_table_still_contradicts_is_not_delivered_as_checked(self):
        result, _ = solve(WRONG_ABOVE_100, fixes=False)
        self.assertNotEqual(result['status'], 'checked')
        self.assertIn('contradicted by the published sequence A000217', result['reason'])

    def test_control_a_right_method_is_confirmed_and_not_asked_again(self):
        result, requests = solve(RIGHT)
        self.assertEqual((result['status'], len(requests)), ('checked', 1))
        self.assertEqual(result['witness']['outcome'], 'agrees')
        self.assertGreaterEqual(result['witness']['compared_beyond'], 2)

    def test_control_without_a_witness_nothing_is_asked(self):
        result, requests = solve(WRONG_ABOVE_100, witness=False)
        self.assertEqual((result['status'], len(requests)), ('checked', 1))   # the old outcome: wrong above 100
        self.assertNotIn('witness', result)
        self.assertEqual(events(result, 'solve_witness'), [])

    def test_control_a_task_that_is_not_a_sequence_is_not_asked(self):
        checks = [check({'n': 0, 'k': 1}, 0), check({'n': 1, 'k': 1}, 1), check({'n': 4, 'k': 1}, 10)]
        result, _ = solve(RIGHT, checks=checks)
        self.assertEqual(events(result, 'solve_witness'), [])


class Finding(unittest.TestCase):
    def test_a_sequence_must_hold_every_term_at_the_same_indices(self):
        witness = OEISWitness(fetch=published)
        values = {n: n * (n + 1) // 2 for n in range(10)}
        self.assertEqual(witness.find(values)[0], 'A000217')
        shifted = {n: (n + 1) * (n + 2) // 2 for n in range(10)}   # the same terms, one index later
        self.assertIsNone(witness.find(shifted))
        self.assertIsNone(witness.find({n: 1 for n in range(10)}), 'a constant says nothing')
        self.assertIsNone(witness.find({n: n * (n + 1) // 2 for n in range(5)}), 'too few terms')
        self.assertEqual(OEISWitness(fetch=lambda url: 1 / 0).find(values), None)   # no network: no witness


if __name__ == '__main__':
    unittest.main()
