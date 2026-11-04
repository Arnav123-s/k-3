import unittest

from kuttaka_runtime.solve_progress import (
    EvidenceFrontier,
    candidate_fingerprint,
    subproblem_key,
)


class CandidateFingerprintTests(unittest.TestCase):
    def test_ast_fingerprint_ignores_locations_comments_and_dependency_order(self):
        first = "def solve(x):\n    # comment\n    return x + 1\n"
        second = "\n\ndef solve(x):\n\n    return x + 1  # another comment\n"
        self.assertEqual(candidate_fingerprint(first, ['dep-b', 'dep-a', 'dep-a']),
                         candidate_fingerprint(second, ['dep-a', 'dep-b']))
        self.assertNotEqual(candidate_fingerprint(first, ['dep-a']),
                            candidate_fingerprint(first, ['dep-b']))

    def test_parse_failure_fallback_uses_raw_text_without_execution(self):
        self.assertNotEqual(candidate_fingerprint('def broken(:'),
                            candidate_fingerprint('def broken(: '))

    def test_bad_source_or_dependency_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            candidate_fingerprint(None)
        for dependencies in ([''], [object()], 'dependency'):
            with self.subTest(dependencies=dependencies):
                with self.assertRaises(ValueError):
                    candidate_fingerprint('pass', dependencies)


class EvidenceFrontierTests(unittest.TestCase):
    def test_incomparable_pass_sets_are_both_retained(self):
        frontier = EvidenceFrontier(['a', 'b', 'c'])
        first = frontier.observe('candidate-a', 'artifact-a', ['a', 'b'])
        second = frontier.observe('candidate-b', 'artifact-b', ['a', 'c'])
        self.assertEqual(first['classification'], 'new_evidence')
        self.assertEqual(second['classification'], 'tradeoff')
        self.assertEqual({row['candidate_id'] for row in second['frontier']},
                         {'candidate-a', 'candidate-b'})
        self.assertEqual(second['best_count'], 2)

    def test_dominated_candidate_does_not_remove_a_better_frontier_member(self):
        frontier = EvidenceFrontier(['a', 'b', 'c'])
        frontier.observe('small', 'artifact-small', ['a'])
        improved = frontier.observe('large', 'artifact-large', ['a', 'b'])
        self.assertEqual(improved['classification'], 'improved')
        self.assertEqual([row['candidate_id'] for row in improved['frontier']], ['large'])

        later = frontier.observe('later-small', 'artifact-later-small', ['a'])
        self.assertEqual(later['classification'], 'regression')
        self.assertEqual([row['candidate_id'] for row in later['frontier']], ['large'])
        self.assertEqual(later['best_count'], 2)

    def test_equal_coverage_from_a_new_candidate_is_labeled_and_retained(self):
        frontier = EvidenceFrontier(['a', 'b'])
        frontier.observe('one', 'artifact-one', ['a'])
        receipt = frontier.observe('two', 'artifact-two', ['a'])
        self.assertEqual(receipt['classification'], 'same_coverage')
        self.assertEqual({row['candidate_id'] for row in receipt['frontier']}, {'one', 'two'})

    def test_dominated_candidate_repetition_is_detected(self):
        frontier = EvidenceFrontier(['a', 'b'])
        frontier.observe('dominated', 'artifact-d', ['a'])
        frontier.observe('dominator', 'artifact-x', ['a', 'b'])
        repeated = frontier.observe('dominated', 'artifact-d', ['a'])
        self.assertEqual(repeated['classification'], 'repeated')
        self.assertEqual(len(frontier.records), 3)

    def test_dominated_candidate_conflict_is_detected(self):
        frontier = EvidenceFrontier(['a', 'b', 'c'])
        frontier.observe('candidate', 'artifact-c', ['a'])
        frontier.observe('dominator', 'artifact-d', ['a', 'b'])
        conflict = frontier.observe('candidate', 'artifact-c', ['c'])
        self.assertEqual(conflict['classification'], 'inconsistent')
        self.assertEqual(frontier.records[-1]['passed_ids'], ['c'])

    def test_inconsistent_dominator_releases_previously_dominated_candidate(self):
        frontier = EvidenceFrontier(['a', 'b', 'c'])
        frontier.observe('small', 'artifact-small', ['a'])
        frontier.observe('large', 'artifact-large', ['a', 'b'])
        frontier.observe('other', 'artifact-other', ['c'])
        conflict = frontier.observe('large', 'artifact-large', ['b'])
        self.assertEqual(conflict['classification'], 'inconsistent')
        self.assertEqual({row['candidate_id'] for row in conflict['frontier']}, {'small', 'other'})
        self.assertEqual(conflict['best_count'], 1)

    def test_same_candidate_conflict_is_inconsistent_and_preserves_both_records(self):
        frontier = EvidenceFrontier(['a', 'b'])
        first = frontier.observe('candidate', 'artifact', ['a'])
        conflict = frontier.observe('candidate', 'artifact', ['b'])
        self.assertEqual(first['classification'], 'new_evidence')
        self.assertEqual(conflict['classification'], 'inconsistent')
        self.assertTrue(conflict['inconsistent'])
        self.assertEqual(len(frontier.records), 2)
        self.assertEqual(frontier.records[0]['passed_ids'], ['a'])
        self.assertEqual(frontier.records[1]['passed_ids'], ['b'])
        self.assertEqual(frontier.frontier, [])
        self.assertEqual(frontier.best_count, 0)

    def test_identical_completed_result_is_repeated(self):
        frontier = EvidenceFrontier(['a', 'b'])
        frontier.observe('candidate', 'artifact', ['a'])
        repeated = frontier.observe('candidate', 'artifact', ['a'])
        self.assertEqual(repeated['classification'], 'repeated')
        self.assertEqual(len(frontier.records), 2)
        self.assertEqual(frontier.best_count, 1)

    def test_incomplete_observation_does_not_advance_completed_score(self):
        frontier = EvidenceFrontier(['a', 'b'])
        frontier.observe('complete', 'artifact-complete', ['a'])
        partial = frontier.observe('partial', 'artifact-partial', ['a', 'b'], completed=False)
        self.assertEqual(partial['classification'], 'incomplete')
        self.assertEqual(partial['best_count'], 1)
        self.assertEqual({row['candidate_id'] for row in partial['frontier']}, {'complete'})
        self.assertEqual(len(frontier.records), 2)

    def test_unknown_ids_and_malformed_identifiers_are_rejected(self):
        frontier = EvidenceFrontier(['a', 'b'])
        for args in [('', 'artifact', []), ('candidate', '', []),
                     ('candidate', 'artifact', ['outside'])]:
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    frontier.observe(args[0], args[1], args[2])
        with self.assertRaises(ValueError):
            EvidenceFrontier(['a', 'a'])

    def test_frontier_receipts_and_properties_are_detached_snapshots(self):
        frontier = EvidenceFrontier(['a'])
        receipt = frontier.observe('candidate', 'artifact', ['a'])
        receipt['frontier'][0]['passed_ids'].clear()
        receipt['check_ids'].clear()
        records = frontier.records
        records[0]['passed_ids'].clear()
        visible = frontier.frontier
        visible[0]['passed_ids'].clear()
        self.assertEqual(frontier.frontier[0]['passed_ids'], ['a'])
        self.assertEqual(frontier.records[0]['passed_ids'], ['a'])
        self.assertEqual(frontier.check_ids, ['a'])

    def test_record_limit_fails_explicitly(self):
        frontier = EvidenceFrontier(['a'], max_records=1)
        frontier.observe('candidate', 'artifact', ['a'])
        with self.assertRaises(OverflowError):
            frontier.observe('candidate', 'artifact', ['a'])


class SubproblemKeyTests(unittest.TestCase):
    def test_request_and_contract_text_are_exact(self):
        left = subproblem_key('sum   values', 'return a number', [{'input': 'a  b', 'expected': True}])
        right = subproblem_key('sum values', 'return\na number', [{'input': 'a  b', 'expected': True}])
        self.assertNotEqual(left, right)
        changed_check_text = subproblem_key('sum values', 'return a number',
                                            [{'input': 'a b', 'expected': True}])
        self.assertNotEqual(right, changed_check_text)
        self.assertNotEqual(subproblem_key('request', 'contract', []),
                            subproblem_key('request ', 'contract', []))
        self.assertNotEqual(subproblem_key('request', 'return "a b"', []),
                            subproblem_key('request', 'return "a  b"', []))

    def test_check_json_types_and_order_remain_exact(self):
        self.assertNotEqual(subproblem_key('r', 'c', [True]), subproblem_key('r', 'c', [1]))
        self.assertNotEqual(subproblem_key('r', 'c', [1, 2]), subproblem_key('r', 'c', [2, 1]))
        with self.assertRaises(ValueError):
            subproblem_key('r', 'c', [float('nan')])


if __name__ == '__main__':
    unittest.main()
