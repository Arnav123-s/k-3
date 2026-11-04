"""R11. solve_valli.assemble promises the bottom is computed by the planner's reference, not by model code.
A reduction whose small() is never true and whose reduce() returns [] passes the measure check vacuously:
combine(input, []) computes every answer and the reference is never run. Local executor only."""
import json
import unittest

from kuttaka_runtime import solve_valli
from tests.test_kuttaka_campaign import local_executor

BYPASS = ('def measure(n):\n    return n\n\n\ndef small(n):\n    return False\n\n\n'
          'def reduce(n):\n    return []\n\n\ndef combine(n, answers):\n    return n * (n + 1) // 2\n')
REFERENCE = 'def reference(n):\n    raise RuntimeError("the reference was run")\n'


class R11ValliBottomBypass(unittest.TestCase):
    def test_the_reference_is_never_the_bottom(self):
        source = solve_valli.assemble(BYPASS, REFERENCE)
        self.assertIsNotNone(source)
        ran = local_executor({'source': source, 'input': 10})
        print('\nexit', ran['exit_code'], ran['output'])
        self.assertNotEqual(ran['exit_code'], 0, 'combine answered with no smaller input and no bottom')


if __name__ == '__main__':
    unittest.main()
