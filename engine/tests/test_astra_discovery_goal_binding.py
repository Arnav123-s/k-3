"""No-Docker tests for the authored discovery fixture's goal-consent binding."""

from pathlib import Path
import tempfile
import unittest

from experiments.astra_discovery.run import goal_matches_clarification
from kuttaka_runtime.goals import GoalLedger
from kuttaka_runtime.store import Journal


CLARIFICATION = {
    'text': 'I need to predict readings on new inputs, not maximize the reading.',
    'attribution': 'scripted user fixture',
}


class DiscoveryGoalBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.journal = Journal(Path(self.tmp.name) / 'events.jsonl')
        self.goals = GoalLedger('Help me understand this device well enough to use it.', self.journal)
        self.clarification_id = self.goals.record_signal(CLARIFICATION, source='user')

    def tearDown(self):
        self.journal.close()
        self.tmp.cleanup()

    def proposal(self, *, based_on, criteria=None):
        return self.goals.propose({
            'description': 'Predict device readings',
            'success_criteria': ['checked predictions on new inputs'] if criteria is None else criteria,
            'unknowns': ['device response law'],
            'tradeoffs': ['measurement cost versus confidence'],
            'parent': None,
            'based_on': based_on,
        })['goal']

    def test_original_broad_request_id_cannot_authorize_narrow_reading_goal(self):
        goal_id = self.proposal(based_on=[self.goals.request_evidence])
        answer = {'goal': goal_id, 'user_evidence': self.goals.request_evidence}
        self.assertFalse(goal_matches_clarification(self.goals, answer))

    def test_forged_success_criteria_are_rejected_even_with_real_clarification(self):
        goal_id = self.proposal(based_on=[self.clarification_id],
                                criteria=['maximize the reading'])
        answer = {'goal': goal_id, 'user_evidence': self.clarification_id}
        self.assertFalse(goal_matches_clarification(self.goals, answer))

    def test_matching_interpretation_citing_clarification_is_accepted(self):
        goal_id = self.proposal(based_on=[self.clarification_id])
        answer = {'goal': goal_id, 'user_evidence': self.clarification_id}
        self.assertTrue(goal_matches_clarification(self.goals, answer))


if __name__ == '__main__':
    unittest.main()
