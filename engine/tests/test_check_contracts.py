import unittest

from kuttaka_runtime.check_contracts import CheckBook, CheckConflict, MAX_CHECKS


class CheckBookTests(unittest.TestCase):
    def test_same_values_from_different_sources_do_not_share_frontier_scope(self):
        checks = [{'input': 1, 'expected': 2}]
        inherited = CheckBook('task', 'contract', checks, [], supplied_origin='model')
        proposed = CheckBook('task', 'contract', [], checks)
        self.assertEqual(inherited.checks, proposed.checks)
        self.assertNotEqual(inherited.records[0]['id'], proposed.records[0]['id'])
        # Otherwise an existing frontier would receive foreign check IDs when
        # a replanned contract has the same values but different provenance.
        self.assertNotEqual(inherited.scope_id, proposed.scope_id)

    def test_user_anchors_over_model_limit_are_all_retained(self):
        supplied = [{'input': i, 'expected': i + 1} for i in range(20)]
        book = CheckBook('increment', 'run(x) returns x+1', supplied,
                         [{'input': 100, 'expected': 101}], model_limit=12)
        self.assertEqual(len(book.checks), 21)
        self.assertEqual(book.checks[:20], supplied)
        self.assertEqual(book.metadata['admitted_model_checks'], 1)

    def test_more_than_programtask_limit_user_anchors_is_rejected(self):
        supplied = [{'input': i, 'expected': i} for i in range(MAX_CHECKS + 1)]
        with self.assertRaisesRegex(ValueError, 'at most 64'):
            CheckBook('echo', 'run(x) returns x', supplied, [])

    def test_conflicting_user_anchor_and_model_proposal_keeps_anchor(self):
        book = CheckBook('double', 'run(x) returns 2*x',
                         [{'input': 3, 'expected': 6}],
                         [{'input': 3, 'expected': 7}, {'input': 4, 'expected': 8}])
        self.assertEqual(book.checks, [
            {'input': 3, 'expected': 6}, {'input': 4, 'expected': 8}])
        self.assertEqual(book.conflicts[0]['kind'], 'user_anchor_rejected_model_proposal')
        self.assertEqual(book.conflicts[0]['rejected_expected'], 7)

    def test_model_proposal_conflicts_are_checked_even_beyond_admission_limit(self):
        proposals = [{'input': i, 'expected': i} for i in range(12)]
        proposals.extend([{'input': 'late', 'expected': 1}, {'input': 'late', 'expected': 2}])
        with self.assertRaises(CheckConflict):
            CheckBook('task', 'contract', [], proposals, model_limit=1)

    def test_model_origin_supplied_checks_conflict_with_model_proposal(self):
        with self.assertRaises(CheckConflict):
            CheckBook('subtask', 'contract', [{'input': [1], 'expected': [2]}],
                      [{'input': [1], 'expected': [3]}], supplied_origin='model')

    def test_identical_duplicates_collapse_and_false_differs_from_zero(self):
        book = CheckBook('typed', 'contract',
                         [{'input': False, 'expected': False}, {'input': False, 'expected': False}],
                         [{'input': False, 'expected': False}, {'input': 0, 'expected': 0}])
        self.assertEqual(book.checks, [
            {'input': False, 'expected': False}, {'input': 0, 'expected': 0}])
        self.assertEqual(book.records[0]['origin'], 'user')
        self.assertEqual(book.records[1]['origin'], 'model')

    def test_inputs_and_every_public_json_property_are_detached(self):
        supplied = [{'input': {'x': [1]}, 'expected': {'y': [2]}}]
        proposed = [{'input': {'x': [3]}, 'expected': [4]}]
        book = CheckBook('task', 'contract', supplied, proposed)
        supplied[0]['input']['x'][0] = 99
        proposed[0]['expected'][0] = 99
        checks = book.checks
        records = book.records
        conflicts = book.conflicts
        revisions = book.revisions
        checks[0]['input']['x'][0] = -1
        records[0]['expected']['y'][0] = -1
        conflicts.append({'changed': True})
        revisions.append({'changed': True})
        self.assertEqual(book.checks[0]['input'], {'x': [1]})
        self.assertEqual(book.checks[0]['expected'], {'y': [2]})
        self.assertEqual(book.checks[1]['expected'], [4])
        self.assertEqual(book.conflicts, [])
        self.assertEqual(book.revisions, [])

    def test_recursive_model_checks_keep_model_provenance(self):
        book = CheckBook('subtask', 'contract', [{'input': 2, 'expected': 4}], [],
                         supplied_origin='model')
        self.assertEqual(book.checks_by, 'model')
        self.assertEqual(book.records[0]['origin'], 'model')
        self.assertEqual(book.records[0]['source'], 'supplied')

    def test_revision_records_before_after_scopes_and_is_heuristic(self):
        book = CheckBook('double', 'run(x) returns 2*x', [],
                         [{'input': 3, 'expected': 7}])
        old_records = book.records
        old_scope = book.scope_id
        self.assertTrue(book.revise(3, 6, evidence={'auditor_expected': 6, 'method_got': 6}))
        revision = book.revisions[0]
        self.assertEqual(old_records[0]['expected'], 7)
        self.assertEqual(revision['before'], old_records[0])
        self.assertEqual(revision['before_check_id'], old_records[0]['id'])
        self.assertEqual(revision['after']['expected'], 6)
        self.assertEqual(revision['scope_before'], old_scope)
        self.assertEqual(revision['scope_after'], book.scope_id)
        self.assertEqual(revision['evidence']['method_got'], 6)
        self.assertEqual(revision['support'], 'heuristic_not_verified')
        self.assertNotEqual(revision['before_check_id'], revision['after_check_id'])

    def test_user_check_cannot_be_revised_and_same_revision_is_noop(self):
        book = CheckBook('echo', 'contract', [{'input': 1, 'expected': 1}], [])
        with self.assertRaises(CheckConflict):
            book.revise(1, 2, evidence={'reason': 'x'})
        model_book = CheckBook('echo', 'contract', [], [{'input': 1, 'expected': 1}])
        self.assertFalse(model_book.revise(1, 1, evidence={'reason': 'same'}))
        self.assertEqual(model_book.revisions, [])

    def test_revision_support_is_restricted_and_host_support_keeps_model_origin(self):
        book = CheckBook('double', 'contract', [], [{'input': 1, 'expected': 3}])
        before = (book.scope_id, book.records, book.revisions)
        with self.assertRaisesRegex(ValueError, 'support label'):
            book.revise(1, 2, evidence={'oracle': 2}, support='verified')
        self.assertEqual((book.scope_id, book.records, book.revisions), before)
        self.assertTrue(book.revise(1, 2, evidence={'oracle': 2},
                                    support='host_oracle_supported'))
        self.assertEqual(book.records[0]['origin'], 'model')
        self.assertEqual(book.revisions[0]['support'], 'host_oracle_supported')

    def test_revision_projection_failure_is_atomic(self):
        large = 'x' * 65536
        book = CheckBook('task', 'contract', [], [
            {'input': 0, 'expected': large},
            {'input': 1, 'expected': large},
            {'input': 2, 'expected': large},
            {'input': 3, 'expected': 0},
        ])
        before = (book.scope_id, book.checks, book.records, book.revisions)
        with self.assertRaisesRegex(ValueError, 'byte limit'):
            book.revise(3, large, evidence={'oracle': True})
        self.assertEqual((book.scope_id, book.checks, book.records, book.revisions), before)

    def test_revision_history_has_a_fixed_limit(self):
        book = CheckBook('task', 'contract', [], [{'input': 1, 'expected': 0}])
        for expected in range(1, 129):
            self.assertTrue(book.revise(1, expected, evidence={'expected': expected}))
        before = (book.scope_id, book.checks, book.records, book.revisions)
        with self.assertRaisesRegex(ValueError, 'limited to 128'):
            book.revise(1, 129, evidence={'expected': 129})
        self.assertEqual((book.scope_id, book.checks, book.records, book.revisions), before)

    def test_oversized_scalar_values_are_rejected_before_serialization(self):
        with self.assertRaisesRegex(ValueError, 'bit limit'):
            CheckBook('task', 'contract', [{'input': 1 << 8192, 'expected': 1}], [])
        with self.assertRaisesRegex(ValueError, 'character limit'):
            CheckBook('task', 'contract', [{'input': 1, 'expected': 'x' * 65537}], [])

    def test_false_and_zero_expectations_are_typed_distinct(self):
        book = CheckBook('task', 'contract', [{'input': 1, 'expected': False}],
                         [{'input': 1, 'expected': 0}])
        self.assertEqual(book.checks, [{'input': 1, 'expected': False}])
        self.assertEqual(book.conflicts[0]['rejected_expected'], 0)

    def test_nonfinite_values_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'finite JSON'):
            CheckBook('task', 'contract', [{'input': 1, 'expected': float('nan')}], [])


if __name__ == '__main__':
    unittest.main()
