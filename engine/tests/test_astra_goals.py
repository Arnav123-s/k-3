from pathlib import Path
import tempfile
import unittest
from kuttaka_runtime.goals import GoalLedger
from kuttaka_runtime.store import Journal


class GoalLedgerTests(unittest.TestCase):
    def test_interpretations_do_not_replace_request_or_authorize_themselves(self):
        with tempfile.TemporaryDirectory() as d, Journal(Path(d)/'events') as journal:
            goals = GoalLedger('Help me get somewhere useful.', journal)
            proposal = {'description': 'Find the shortest exit route', 'success_criteria': ['reach an exit'],
                        'unknowns': ['whether exit or exploration matters'], 'tradeoffs': ['distance versus information'],
                        'parent': None, 'based_on': [goals.request_evidence]}
            first = goals.propose(proposal)['goal']
            observation = goals.record_signal({'exit_found': True}, source='observation')
            with self.assertRaises(ValueError):
                goals.authorize(first, user_evidence=observation)
            self.assertIsNone(goals.status({})['active'])
            self.assertEqual(set(goals.tools()), {'goal_propose', 'goal_status'})
            correction = goals.record_signal('I want to explore, not leave.', source='user')
            second = goals.propose({**proposal, 'description': 'Explore accessible rooms',
                'success_criteria': ['report a map'], 'parent': first, 'based_on': [correction]})['goal']
            goals.authorize(second, user_evidence=correction)
            status = goals.status({})
            self.assertEqual(status['active'], second)
            self.assertIn(first, status['interpretations'])
            self.assertEqual(status['original_request'], 'Help me get somewhere useful.')
            status['interpretations'].clear()
            self.assertEqual(len(goals.status({})['interpretations']), 2)
