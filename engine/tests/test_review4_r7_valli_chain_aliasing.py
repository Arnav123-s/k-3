"""R7. solve_valli's chain (_chain) hands the same answer object to every caller and keeps it after combine
has changed it. A reduction whose combine updates the left answer in place (an ordinary Python idiom) is
right as a plain recursion and wrong as Kuttaka assembles it. When the reference cannot run on the
requested input (it is slow by contract), nothing stands in the way and a wrong answer is delivered.
The reduction, reference and models are written for this test; the local executor runs them."""
import json
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime import solve_valli
from kuttaka_runtime.solve import Metered, Solver, Workspace
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model, check, store
from tests.test_solve_review_three import fast_executor

REDUCTION = '''def measure(s):
    return len(s)


def small(s):
    return len(s) <= 2


def reduce(s):
    m = len(s) // 2
    return [s[:m], s[m:]]


def combine(s, answers):
    left, right = answers
    for k, v in right.items():
        left[k] = left.get(k, 0) + v
    return left
'''
# Exact, and slow beyond small inputs (as a reference may be): it cannot run on the requested input.
REFERENCE = ('def reference(s):\n    import time\n    if len(s) > 5:\n        time.sleep(30)\n'
             '    out = {}\n    for ch in s:\n        out[ch] = out.get(ch, 0) + 1\n    return out\n')


def counts(s):
    return {c: s.count(c) for c in sorted(set(s))}


def plain(s):   # the same reduction, as a plain recursion with the same bottom
    space = {}
    exec(REDUCTION, space)
    if space['small'](s):
        return counts(s)
    return space['combine'](s, [plain(p) for p in space['reduce'](s)])


class R7ValliChainAliasing(unittest.TestCase):
    def test_unit_the_assembled_recursion_disagrees_with_the_plain_recursion(self):
        source = solve_valli.assemble(REDUCTION, REFERENCE.replace('    if len(s) > 5:\n        time.sleep(30)\n', ''))
        ran = json.loads(local_executor({'source': source, 'input': 'aaaaab'})['output'])['result']
        print('\nplain recursion:', plain('aaaaab'), '| valli:', ran)
        self.assertEqual(plain('aaaaab'), counts('aaaaab'))          # control: the reduction is right
        self.assertEqual(ran, counts('aaaaab'), 'the chain returned an answer that combine had already changed')

    def test_end_to_end_a_wrong_answer_is_delivered(self):
        plan = lambda c: {'contract': 'run(s) returns how many times each letter occurs in s', 'input': 'aaaaab',
                          'checks': [check(s, counts(s)) for s in ('ab', 'abab', 'aabb', 'abcab')],
                          'reference': REFERENCE}
        model = Model(plan, lambda r: store('def run(s):\n    return {}\n'), split=lambda c: {'reduction': REDUCTION})
        m = Metered(model, {'used': 0, 'limit': 30})
        result = Solver(Workspace(Path(tempfile.mkdtemp())), m, m, fast_executor, splitter=m, auditor=m, sketcher=m,
                        max_executions=256, say=lambda _: None).solve('Count the letters of a word.')
        print('status', result['status'], '| delivered_by', result.get('delivered_by'), '| answer', result.get('answer'),
              '| right', counts('aaaaab'))
        if result['status'] == 'checked':
            self.assertEqual(result['answer'], counts('aaaaab'), 'a wrong answer was delivered by valli')


if __name__ == '__main__':
    unittest.main()
