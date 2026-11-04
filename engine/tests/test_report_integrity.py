"""Reporting regression checks using synthetic rows/journals, never suite data or model calls."""
import json
import unittest

from tests.test_handoff_tools import arm, journal_task, row
import climb
import wins
from tests import test_solve_suite_runner as runner_tests


class RescoreCoverage(unittest.TestCase):
    def test_missing_rescore_is_unknown_with_an_as_run_control(self):
        folder = arm([row('t', True, 2, 2)], [{'task': 't', 'rescored': 'missing libraries'}])
        alone = {'t': row('t', False, 1, 2, 'delivered')}
        rescored, _ = wins.load(folder, rescored=True)
        table = wins.compare(rescored, alone)
        self.assertEqual(table[0]['outcome'], 'UNKNOWN')
        self.assertIsNone(table[0]['with']['wrong_delivered'])
        self.assertEqual(table[0]['with']['hidden'], 'not rescored')
        self.assertEqual(wins.totals(table)['unscored_with'], 1)
        self.assertEqual(wins.totals(table)['wins'], 0)
        as_run, _ = wins.load(folder)
        self.assertEqual(wins.compare(as_run, alone)[0]['outcome'], 'WIN')

    def test_a_missing_entry_is_also_unknown(self):
        folder = arm([row('t', True, 1, 1)], [])
        actual, _ = wins.load(folder, rescored=True)
        self.assertIsNone(actual['t']['solved'])

    def test_no_delivery_remains_unsolved_and_known_failure_remains_wrong(self):
        no_code = dict(row('none', False, 0, 2, 'incomplete'), source=None)
        folder = arm([no_code, row('wrong', True, 2, 2)],
                     [{'task': 'wrong', 'passed_rescored': 1, 'solved_rescored': False}])
        actual, _ = wins.load(folder, rescored=True)
        self.assertIs(actual['none']['solved'], False)
        self.assertIs(actual['wrong']['solved'], False)
        self.assertIs(wins.wrong_delivered(actual['wrong']), True)

    def test_unverified_unknown_does_not_retain_old_success(self):
        r = dict(row('t', False, 0, 2, 'incomplete'), source=None,
                 unverified_source='def run(x): return x', solved_unverified=True, unverified_passed=2)
        folder = arm([r], [{'task': 't', 'unverified_rescored': 'missing libraries'}])
        actual, _ = wins.load(folder, rescored=True)
        table = wins.compare(actual, {'t': row('t', False, 0, 2, 'delivered')})
        self.assertEqual(table[0]['outcome'], 'NEITHER')
        self.assertEqual(table[0]['outcome_with_unverified'], 'UNKNOWN')
        self.assertEqual(wins.totals(table)['with_unverified']['unknown'], 1)

    def test_known_success_still_wins_when_an_unverified_source_is_unknown(self):
        self.assertIs(wins.either_score(True, None), True)
        self.assertEqual(wins.outcome(wins.either_score(True, None), False), 'WIN')


class ClimbIdentity(unittest.TestCase):
    def test_all_rounds_belong_to_one_task_and_do_not_shift_next_task(self):
        folder = arm([dict(row('one', True, 1, 1), rounds=2), row('two', True, 1, 1)])
        journal_task(folder, 1, 'one', [('incomplete', None, 1, False)])
        journal_task(folder, 2, 'one', [('checked', 'a', 1, False)])
        journal_task(folder, 3, 'two', [('incomplete', None, 1, False), ('checked', 'b', 1, False)])
        result = climb.report(folder)['tasks']
        self.assertEqual([(r['attempts'], r['class']) for r in result], [(2, 'CLIMBED'), (2, 'CLIMBED')])
        self.assertEqual(result[0]['methods'], 2)
        self.assertEqual(len(result[0]['journals']), 2)
        self.assertEqual(len(result[1]['journals']), 1)

    def test_missing_journal_does_not_take_a_later_tasks_journal(self):
        folder = arm([row('missing', True, 1, 1), row('present', True, 1, 1)])
        journal_task(folder, 1, 'present', [('checked', 'a', 1, False)])
        result = climb.report(folder)['tasks']
        self.assertEqual([r['class'] for r in result], ['NOT_READ', 'FIRST_TRY'])

    def test_unknown_rescore_is_not_a_first_try_success(self):
        folder = arm([row('t', True, 1, 1)], [{'task': 't', 'rescored': 'missing dependencies'}])
        journal_task(folder, 1, 't', [('checked', 'a', 1, False)])
        result = climb.report(folder, rescored=True)['tasks'][0]
        self.assertEqual(result['class'], 'NOT_SCORED')
        self.assertIsNone(result['solved'])
        self.assertEqual(climb.report(folder)['tasks'][0]['class'], 'FIRST_TRY')

    def test_incomplete_round_set_is_not_classified(self):
        folder = arm([dict(row('t', True, 1, 1), rounds=2)])
        journal_task(folder, 1, 't', [('checked', 'a', 1, False)])
        self.assertEqual(climb.report(folder)['tasks'][0]['class'], 'NOT_READ')

    def test_missing_attempt_journal_does_not_assume_one_method_written(self):
        folder = arm([row('t', True, 1, 1)])
        journal_task(folder, 1, 't', [('checked', 'a', 1, False)])
        for path in folder.glob('workspace/runs/*/events.jsonl'):
            path.unlink()
        result = climb.report(folder)['tasks'][0]
        self.assertEqual(result['class'], 'NOT_READ')
        self.assertIsNone(result['methods'])

    def test_later_failed_attempt_is_a_failed_climb_not_an_unperceived_failure(self):
        folder = arm([row('t', False, 0, 1)])
        journal_task(folder, 1, 't', [('checked', 'a', 1, False), ('incomplete', None, 1, False)])
        self.assertEqual(climb.report(folder)['tasks'][0]['class'], 'CLIMB_FAILED')

    def test_answer_withheld_after_pass_is_not_a_false_stop(self):
        folder = arm([dict(row('t', False, 0, 1, 'incomplete'), source=None)])
        journal_task(folder, 1, 't', [('checked', 'a', 1, False)])
        self.assertEqual(climb.report(folder)['tasks'][0]['class'], 'CLIMB_FAILED')

    def test_legacy_contract_match_must_be_unique(self):
        folder = arm([dict(row('a', True, 1, 1), contract='Double.'),
                      dict(row('b', True, 1, 1), contract='Double.')])
        journal_task(folder, 1, 'some prompt', [('checked', 'a', 1, False)])
        path = folder / 'workspace/runs/solve-1/solve.jsonl'
        with path.open('a') as handle:
            handle.write(json.dumps({'kind': 'solve_check_contract', 'body': {'contract': 'Double.'}}) + '\n')
        self.assertEqual([r['class'] for r in climb.report(folder)['tasks']], ['NOT_READ', 'NOT_READ'])

    def test_explicit_paths_keep_round_order_and_ignore_builder_workspace_prefix(self):
        paths = ['/workspace/checkout/out/workspace/runs/solve-2/solve.jsonl',
                 '/workspace/checkout/out/workspace/runs/solve-1/solve.jsonl']
        folder = arm([dict(row('id', True, 1, 1), request='Some request', rounds=2, solve_journals=paths)])
        journal_task(folder, 1, 'Some request', [('checked', 'a', 1, False)])
        journal_task(folder, 2, 'Some request', [('incomplete', None, 1, False)])
        result = climb.report(folder)['tasks'][0]
        self.assertEqual(result['first'], 'failed')
        self.assertEqual(result['class'], 'CLIMBED')
        self.assertEqual(result['journals'], ['workspace/runs/solve-2/solve.jsonl', 'workspace/runs/solve-1/solve.jsonl'])

    def test_explicit_journal_for_another_request_is_rejected(self):
        folder = arm([dict(row('id', True, 1, 1), request='Wanted', rounds=1,
                          solve_journals=['workspace/runs/solve-1/solve.jsonl'])])
        journal_task(folder, 1, 'Different', [('checked', 'a', 1, False)])
        self.assertEqual(climb.report(folder)['tasks'][0]['class'], 'NOT_READ')

    def test_fresh_workspace_identifies_task_without_request_or_contract_in_row(self):
        folder = arm([row('id', True, 1, 1)])
        journal_task(folder, 1, 'A prompt without the task id', [('checked', 'a', 1, False)])
        (folder / 'workspaces').mkdir()
        (folder / 'workspace').rename(folder / 'workspaces/id')
        path = folder / 'workspaces/id/runs/solve-1/solve.jsonl'
        path.write_text(path.read_text().replace('/workspace/runs/', '/workspaces/id/runs/'))
        self.assertEqual(climb.report(folder)['tasks'][0]['class'], 'FIRST_TRY')

    def test_journal_paths_cannot_escape_the_arm(self):
        folder = arm([])
        self.assertIsNone(climb.local_path(folder, 'workspace/runs/../../../../elsewhere'))


class RunnerJournalIdentity(unittest.TestCase):
    def setUp(self):
        runner_tests.RunnerRoundsTests.setUp(self)

    def test_round_journal_identity_survives_an_outage(self):
        runner_tests.RunnerRoundsTests.test_an_outage_in_a_later_round_resumes_with_the_earlier_rounds_counted(self)
        result = json.loads((self.out / 'rows.jsonl').read_text().splitlines()[0])
        self.assertEqual(result['request'], 'Triple a number.')
        self.assertEqual(len(result['solve_journals']), 2)
        report = climb.report(self.out)['tasks'][0]
        self.assertEqual(report['journal_match'], 'explicit journal paths')
        self.assertEqual(len(report['journals']), 2)
        self.assertEqual(report['class'], 'CLIMBED')


if __name__ == '__main__':
    unittest.main()
