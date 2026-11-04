"""S3 (wrong answer delivered as checked; not a regression vs d330165, where text inputs had no inputs around them
(the twelfth review's R1); e7cb51d's _text_near closes R1 except here). In _question_literals the auditor's reply
is "no answer" when it is a word of UNDEFINED (kuttaka_runtime/solve.py:334-335, used at 2096-2100: 'invalid',
'none', 'error', 'unknown', 'n/a', '-', ...) unless the method answered the same word. So an auditor reply in that
list can never dispute anything. For a task whose RIGHT outputs include such a word, a wrong rule patched at a
check stands: around the patch the method answers 'valid' where the right auditor says 'invalid', and that is
dropped as no answer.

Scenario: IPv4 check, returns "valid" / "invalid". The model's rule forgets the 0..255 range and patches the check
"256.1.1.1" -> "invalid" (a named constant: the text reading misses it; the substitution test finds it). All checks
and the auditor are right. Requested input "300.1.1.1" (right "invalid"; the patched rule answers "valid").
Control: the same task and method with outputs "yes" / "no": disputed around the patch, the general rule is
delivered, "no".
Scripted models; local executor; every method is written here. Run: python3 <this file>."""
import os
import sys
import tempfile
import unittest
from pathlib import Path


from kuttaka_runtime.check_memorization import memorized_checks   # noqa: E402
from kuttaka_runtime.solve import Workspace   # noqa: E402
from tests.test_solve_review_regressions import Model, check, events, make_solver, store   # noqa: E402


def sources(good, bad):
    head = 'GOOD = %r\nBAD = %r\nKNOWN_BAD = "256.1.1.1"\n\n\ndef run(s):\n    parts = s.split(".")\n' % (good, bad)
    patched = head + ('    if s == KNOWN_BAD:\n        return BAD\n'
                      '    if len(parts) != 4 or not all(p.isdigit() for p in parts):\n        return BAD\n'
                      '    return GOOD\n')
    general = head + ('    if len(parts) != 4 or not all(p.isdigit() and int(p) <= 255 for p in parts):\n'
                      '        return BAD\n    return GOOD\n')
    return patched, general


def job(good, bad):
    task = 'IPv4 check: run(s) returns "%s" if s is four dot-separated parts each 0 to 255, else "%s".' % (good, bad)
    patched, general = sources(good, bad)

    def right(s):
        parts = s.split('.') if isinstance(s, str) else []
        return good if len(parts) == 4 and all(p.isdigit() and int(p) <= 255 for p in parts) else bad
    checks = [check('192.168.0.1', good), check('10.0.0.255', good), check('256.1.1.1', bad), check('1.2.3', bad)]
    plan = lambda c: {'contract': 'run(s) returns %r for a valid IPv4 address, else %r' % (good, bad),
                      'input': '300.1.1.1', 'checks': checks}
    act = lambda request: store(general if 'literally' in request else patched)   # the patch first
    audit = lambda c: {'expected': [right(v) for v in c['inputs']]}   # right
    solver, _ = make_solver(Model(plan, act, audit=audit), ws=Workspace(Path(tempfile.mkdtemp())), max_calls=16)
    result = solver.solve(task)
    print('\n%s/%s: %s, answer %r (right %r) | question %s' % (
        good, bad, result['status'], result.get('answer'), bad,
        [(e.get('outcome'), e.get('auditor')) for e in events(result, 'solve_literal_questioned')]), file=sys.stderr)
    return result, checks


class S3UndefinedWordsNeverDispute(unittest.TestCase):
    def test_precondition_text_reading_misses_the_patch(self):
        self.assertEqual(memorized_checks(sources('valid', 'invalid')[0], [check('256.1.1.1', 'invalid')]), [])

    def test_control_yes_no(self):
        result, _ = job('yes', 'no')
        self.assertEqual((result['status'], result.get('answer')), ('checked', 'no'))

    def test_valid_invalid(self):
        result, _ = job('valid', 'invalid')
        self.assertNotEqual(result.get('answer'), 'valid', 'the patched rule is delivered as checked')


if __name__ == '__main__':
    unittest.main()
