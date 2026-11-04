"""Strict adapter tests for instrumented isolated-executor receipts."""

import unittest

from kuttaka_runtime.receipts import MAX_METADATA_BYTES, split_execution_receipt


def command_receipt(status='completed'):
    return {'status': status, 'return_code': 0 if status == 'completed' else None,
            'timed_out': status == 'timeout', 'elapsed_seconds': 0.25,
            'stdout': '', 'stderr': ''}


def actual_executor_shape():
    return {
        'status': 'completed', 'exit_code': 0, 'output': '{"result":7}', 'diagnostics': '',
        'container_name': 'astra-exec-0123456789abcdef',
        'timings': {
            'stdin_complete_seconds': 0.001,
            'first_output_seconds': 0.012,
            'process_exit_seconds': 0.013,
            'process_exit_forced': False,
            'total_wall_seconds': 0.014,
        },
        'inspect_state': None,
        'cleanup': command_receipt(),
        'dependency_calls': ['a' * 64, '0' * 64],
    }


class ExecutionReceiptTests(unittest.TestCase):
    def test_accepts_current_executor_receipt_and_splits_known_metadata(self):
        core, metadata = split_execution_receipt(actual_executor_shape())
        self.assertEqual(core, {'status': 'completed', 'exit_code': 0,
                                'output': '{"result":7}', 'diagnostics': ''})
        self.assertEqual(set(metadata), {'container_name', 'timings', 'inspect_state',
                                         'cleanup', 'dependency_calls'})
        self.assertEqual(metadata['cleanup']['status'], 'completed')
        self.assertEqual(metadata['dependency_calls'], ['a' * 64, '0' * 64])

    def test_accepts_core_only_and_incomplete_none_exit_code(self):
        self.assertEqual(split_execution_receipt(
            {'status': 'completed', 'exit_code': 0, 'output': 'ok'}),
            ({'status': 'completed', 'exit_code': 0, 'output': 'ok'}, {}))
        core, metadata = split_execution_receipt(
            {'status': 'timeout', 'exit_code': None, 'output': ''})
        self.assertEqual(core['exit_code'], None)
        self.assertEqual(metadata, {})

    def test_core_and_metadata_are_detached(self):
        response = actual_executor_shape()
        core, metadata = split_execution_receipt(response)
        response['timings']['total_wall_seconds'] = 100
        response['dependency_calls'].clear()
        response['output'] = 'changed'
        self.assertEqual(metadata['timings']['total_wall_seconds'], 0.014)
        self.assertEqual(metadata['dependency_calls'], ['a' * 64, '0' * 64])
        self.assertEqual(core['output'], '{"result":7}')
        metadata['timings']['process_exit_forced'] = True
        self.assertFalse(response['timings']['process_exit_forced'])

    def test_rejects_unknown_top_level_and_bad_core_types(self):
        cases = []
        unknown = actual_executor_shape()
        unknown['future_field'] = 1
        cases.append(unknown)
        for patch in ({'status': []}, {'exit_code': True}, {'exit_code': None},
                      {'output': 7}, {'diagnostics': None}):
            candidate = actual_executor_shape()
            candidate.update(patch)
            cases.append(candidate)
        for candidate in cases:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                split_execution_receipt(candidate)

    def test_rejects_malformed_known_metadata_and_nested_extensions(self):
        cases = []
        candidate = actual_executor_shape()
        candidate['container_name'] = 3
        cases.append(candidate)
        candidate = actual_executor_shape()
        candidate['timings']['unexpected'] = 1
        cases.append(candidate)
        candidate = actual_executor_shape()
        candidate['timings']['process_exit_forced'] = 1
        cases.append(candidate)
        candidate = actual_executor_shape()
        candidate['cleanup']['unknown'] = 'x'
        cases.append(candidate)
        candidate = actual_executor_shape()
        candidate['inspect_state'] = []
        cases.append(candidate)
        candidate = actual_executor_shape()
        candidate['dependency_calls'] = ['not-an-artifact-id']
        cases.append(candidate)
        for candidate in cases:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                split_execution_receipt(candidate)

    def test_rejects_nonfinite_or_negative_metadata_numbers(self):
        for value in (float('nan'), float('inf'), -0.01):
            candidate = actual_executor_shape()
            candidate['timings']['total_wall_seconds'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                split_execution_receipt(candidate)
        candidate = actual_executor_shape()
        candidate['cleanup']['elapsed_seconds'] = float('nan')
        with self.assertRaises(ValueError):
            split_execution_receipt(candidate)

    def test_rejects_metadata_larger_than_64_kib(self):
        candidate = actual_executor_shape()
        candidate['container_name'] = 'x' * (MAX_METADATA_BYTES + 1)
        with self.assertRaises(ValueError):
            split_execution_receipt(candidate)

    def test_metadata_limit_counts_utf8_bytes_for_non_ascii_values(self):
        candidate = actual_executor_shape()
        # 20,000 characters but 80,000 UTF-8 bytes: a character-count limit misses this.
        candidate['container_name'] = '💠' * 20_000
        with self.assertRaises(ValueError):
            split_execution_receipt(candidate)

    def test_accepts_safe_timeout_inspect_cleanup_and_dependency_metadata(self):
        candidate = actual_executor_shape()
        candidate.update({'status': 'timeout', 'exit_code': -9,
                          'inspect_state': {**command_receipt(), 'stdout': '{"Status":"created"}'},
                          'cleanup': {**command_receipt('timeout'), 'timed_out': True,
                                      'return_code': -9}})
        core, metadata = split_execution_receipt(candidate)
        self.assertEqual(core['exit_code'], -9)
        self.assertEqual(metadata['inspect_state']['stdout'], '{"Status":"created"}')
        self.assertEqual(metadata['cleanup']['status'], 'timeout')

        candidate['inspect_state'] = {**command_receipt('error'), 'error_type': 'OSError'}
        _, metadata = split_execution_receipt(candidate)
        self.assertEqual(metadata['inspect_state']['error_type'], 'OSError')


if __name__ == '__main__':
    unittest.main()
