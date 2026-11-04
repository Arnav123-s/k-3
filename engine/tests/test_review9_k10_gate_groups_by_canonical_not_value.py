"""K10 (PLAUSIBLE end to end; the counting is shown directly). _answer_gate (solve.py:1681-1686 at 3af0808) decides
whether a voice agrees with the answer by same_value (numbers equal within 1e-9, int and float alike), but groups
the other voices by canonical(), which keeps 820 and 820.0 apart. Two voices for the same rival value, one an int
(a blind re-derivation) and one a float (a rival or reference-like method computing n*(n+1)/2), are then counted
as two different values of one voice each, and an answer with one agreeing voice (2) beats them (1 and 1), where
the same record written in one number form is a 2-2 dispute. Numbers in both forms are common here: _like exists
because references return 37.0 where hand values say 37.

Control: the same record with both dissenting voices written 820 is disputed. Direct call of the gate on a solver
with no job; no execution (the requested input is not declared small, so no reference runs)."""
import tempfile
import unittest
from pathlib import Path

from kuttaka_runtime.solve import Solver, Workspace, canonical
from tests.test_kuttaka_campaign import local_executor
from tests.test_solve_review_regressions import Model


def gate(voices):
    solver = Solver(Workspace(Path(tempfile.mkdtemp())), Model(None, None), Model(None, None), local_executor,
                    max_executions=4, say=lambda _: None)
    spec = {'input': 40, 'small_inputs': [3, 4], 'reference': None}
    solver._voices[solver._voice_key('T', spec)] = voices   # adapted: voices are kept per task and input (K8)
    return solver._answer_gate('T', spec, 800, 'artifact')


class K10GateGroupsByCanonical(unittest.TestCase):
    def test_control_one_number_form_is_a_dispute(self):
        self.assertIsNotNone(gate([('auditor', 800, None), ('auditor', 820, None), ('rival', 820, 'r')]))

    def test_int_and_float_forms_split_the_dissent(self):
        record = gate([('auditor', 800, None), ('auditor', 820, None), ('rival', 820.0, 'r')])
        print('\ngate on [800, 820, 820.0] for answer 800:', record)
        self.assertIsNotNone(record, '820 and 820.0 are one value for same_value but two for the count')


if __name__ == '__main__':
    unittest.main()
