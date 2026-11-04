"""N4 (robustness of RUN; new in 556adfd; no wrong answer: a run cut short judges nothing). RUN's time limits do not
hold.

  (a) check_substitution.py RUN `_timed` (line 80) arms a one-shot ITIMER_REAL and the alarm raises `_Stop(BaseException)`.
      A method that catches BaseException (or a bare `except:`) inside a loop swallows the one alarm; nothing
      re-arms it, so when a substituted constant makes the loop endless the job runs into the executor's 20 s
      limit (the Docker sandbox's, tests' local_executor's): 20 s of wall time and one execution spent, and the test
      judges nothing.
  (b) The 8 s BUDGET is checked only before each variant (line 94, `if time.monotonic() > deadline: break`); one variant
      runs every input to its 0.5 s limit, up to MAX_PROBES * PER_CALL = 16 s, so a run can take BUDGET + 16 s,
      more than the 20 s the sandbox allows (the module says "the sandbox allows 20").

Scenario (a): total seconds of a list of "mm:ss" laps, parsed by a tolerant loop (`except BaseException: continue`);
the hand check ["01:00"] -> 60 makes 60 a candidate constant; changed, the counting loop never meets its end.
Control (a): the same method catching ValueError only: the alarm stops each endless call and the run completes
within its budget. Scenario (b): a counting loop that never ends on any input once 60 is changed.
Every method is written here; RUN is run by the local executor (the sandbox's runner in a subprocess)."""
import json
import subprocess
import sys
import time
import unittest

from kuttaka_runtime import check_substitution
from tests.test_kuttaka_campaign import local_executor


def c(i, e):
    return {'input': i, 'expected': e}


LAPS = '''def run(laps):
    total = 0
    for lap in laps:
        try:
            seconds = 0
            while seconds != 60 * int(lap[:2]):
                seconds += 60
            total += seconds + int(lap[3:])
        except BaseException:
            continue
    return total
'''
LAP_CHECKS = [c(['01:00'], 60), c(['01:30', '02:00'], 210), c(['03:15'], 195), c(['00:45', '10:00', '01:00'], 705)]
COUNT = 'def run(n):\n    k = 0\n    while k != n * 60:\n        k += 60\n    return k\n'
COUNT_CHECKS = [c(60, 3600), c(90, 5400), c(7, 420)]
SLACK = 1.5


def timed_run(source, checks):
    prepared = check_substitution.prepare({'': source}, checks)
    started = time.monotonic()
    try:
        response = local_executor({'source': check_substitution.RUN, 'input': prepared['payload']})
        rows = json.loads(response['output'])['result'] if response['exit_code'] == 0 else None
    except subprocess.TimeoutExpired:
        rows = None
    took = time.monotonic() - started
    found = check_substitution.read(prepared, rows, checks)
    print('\n%.1f s (budget %.1f s), judged %s' % (took, check_substitution.BUDGET,
                                                   None if found is None else len(found['judged'])), file=sys.stderr)
    return took, found


class N4RunOverrunsItsBudget(unittest.TestCase):
    def test_control_alarm_not_swallowed(self):
        took, found = timed_run(LAPS.replace('except BaseException:', 'except ValueError:'), LAP_CHECKS)
        self.assertLessEqual(took, check_substitution.BUDGET + SLACK)
        self.assertIsNotNone(found)

    def test_a_method_catching_base_exception_swallows_the_alarm(self):
        took, found = timed_run(LAPS, LAP_CHECKS)
        self.assertLessEqual(took, check_substitution.BUDGET + SLACK, 'the run ignores its budget and its per-call '
                             'limit, and is killed at the executor limit (20 s)')
        # Adapted after the fix: whether a judgement comes back depends on how often the method swallows the stop
        # under the machine's load; one that swallows it repeatedly ends the test by design (it judges nothing, the
        # text reading stands). The finding was the overrun, asserted above.

    def test_the_budget_is_checked_only_between_variants(self):
        took, _ = timed_run(COUNT, COUNT_CHECKS)
        self.assertLessEqual(took, check_substitution.BUDGET + SLACK, 'one variant ran past the whole budget')


if __name__ == '__main__':
    unittest.main()
