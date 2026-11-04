"""R3 (crash; regression vs d5dc8ce). The literal question's journal event now records the auditor's reply verbatim
(`'auditor': audited`, kuttaka_runtime/solve.py:1827-1829 at d330165). The journal writes canonical JSON with
allow_nan=False (store.py:10-11, 89-91), and a model reply is parsed with json.loads (models.py:62), which accepts
NaN, Infinity and -Infinity. An auditor that writes one of them for any input asked (now: inputs around the
check, where an undefined value is likelier, e.g. a ratio whose denominator a near input makes zero) makes
Journal.append raise ValueError, and the whole solve crashes instead of treating it as a disagreement.

At d5dc8ce the event held only check inputs, the reply was compared by same_value (NaN equals nothing: a
dispute) and the job went on. Here the scripted auditor answers float('nan') -- exactly what json.loads gives for
the text NaN -- on one input around the check ([4, 4, 4, 19], made from the check [3, 4, 4, 19] by the
source's own constant 4): a vertical line, whose slope it judges undefined although the contract says "vertical".
Controls: the same auditor answering null there (no answer), or "vertical": the job finishes.
Scripted models; local executor; every method is written here."""
import json
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Workspace
from tests.test_solve_review_regressions import Model, check, make_solver, store

TASK = 'Slope: run([x1, y1, x2, y2]) returns the slope (y2 - y1) / (x2 - x1) of the line through two points, or "vertical".'
CONTRACT = 'run([x1, y1, x2, y2]) returns (y2 - y1) / (x2 - x1) as a number, the text "vertical" when x1 == x2'
# A correct method holding a named constant equal to one check's answer, used for that check alone (the
# substitution test's find; the text reading reads it as a name, not a literal).
METHOD = ('STEEP = 15.0\n\n\ndef run(p):\n    x1, y1, x2, y2 = p\n    if x1 == x2:\n        return "vertical"\n'
          '    if [x1, y1, x2, y2] == [3, 4, 4, 19]:\n        return STEEP\n    return (y2 - y1) / (x2 - x1)\n')
CHECKS = [check([3, 4, 4, 19], 15.0), check([0, 0, 4, 2], 0.5), check([6, 1, 9, 10], 3.0)]


def slope(p, vertical):   # right everywhere, but writes `vertical` for a vertical line
    x1, y1, x2, y2 = p
    return vertical if x1 == x2 else (y2 - y1) / (x2 - x1)


def solve(undefined):
    def plan(context):
        return {'contract': CONTRACT, 'input': [1, 1, 3, 7], 'checks': CHECKS}
    audit = lambda context: {'expected': [slope(v, undefined) for v in context['inputs']]}
    solver, _ = make_solver(Model(plan, lambda r: store(METHOD), audit=audit), ws=Workspace(Path(tempfile.mkdtemp())),
                            max_calls=12)
    return solver.solve(TASK)


class R3NanReplyCrashes(unittest.TestCase):
    def test_precondition_json_loads_gives_nan(self):
        value = json.loads('{"expected": [NaN]}')['expected'][0]
        self.assertNotEqual(value, value)

    def test_control_null_for_vertical(self):
        self.assertIn('status', solve(None))

    def test_control_text_for_vertical(self):
        self.assertIn('status', solve('vertical'))

    def test_nan_for_vertical(self):
        try:
            result = solve(float('nan'))
        except ValueError as error:
            self.fail('the solve crashes on the auditor\'s NaN: %s' % error)
        self.assertIn('status', result)


if __name__ == '__main__':
    unittest.main()
