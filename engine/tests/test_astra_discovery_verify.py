"""Tamper controls for the authored discovery lifecycle verifier; no Docker calls."""

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from experiments.astra_discovery.verify import verify
from kuttaka_runtime.store import digest


RECORDED = Path(__file__).resolve().parents[1] / 'experiments/astra_discovery/recorded'


def _replace_refs(value, mapping):
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [_replace_refs(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: _replace_refs(item, mapping) for key, item in value.items()}
    return value


def _rewrite_journal(path, target_kind, mutate, *, target_seq=None):
    events = [json.loads(line) for line in path.read_text().splitlines()]
    mapping = {}
    previous = ''
    target_old_hash = None
    for event in events:
        old_hash = event['hash']
        event['body'] = _replace_refs(event['body'], mapping)
        if event['kind'] == target_kind and (target_seq is None or event['seq'] == target_seq):
            if target_old_hash is None:
                target_old_hash = old_hash
                mutate(event['body'])
        event['prev'] = previous
        payload = {key: event[key] for key in ('seq', 'kind', 'body', 'prev')}
        event['hash'] = digest(payload)
        mapping[old_hash] = event['hash']
        previous = event['hash']
    if target_old_hash is None:
        raise AssertionError(f'missing target journal event: {target_kind}')
    path.write_text(''.join(json.dumps(event, sort_keys=True, separators=(',', ':')) + '\n'
                            for event in events))
    return mapping


class DiscoveryVerifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'recorded'
        shutil.copytree(RECORDED, self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def report(self):
        path = self.root / 'results.json'
        return json.loads(path.read_text()), path

    def test_recorded_run_is_positive_control(self):
        receipt = verify(self.root)
        self.assertEqual(receipt['candidate_failures_retained'], 2)
        self.assertEqual(receipt['goal_lineage_reconstructed'], 2)
        self.assertEqual(receipt['policy_executions'], 19)
        self.assertEqual(receipt['prediction_executions'], 12)
        self.assertEqual(receipt['checker_executions'], 3)
        self.assertEqual(receipt['model_calls'], 0)

    def test_report_only_count_tamper_is_rejected(self):
        report, path = self.report()
        report['prediction_executions'] += 1
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'prediction execution count differs'):
            verify(self.root)

    def test_rehashed_receipt_to_wrong_case_prediction_is_rejected(self):
        journal = self.root / 'events.jsonl'
        events = [json.loads(line) for line in journal.read_text().splitlines()]
        wrong_case_prediction = next(
            event['hash'] for event in events
            if event['kind'] == 'discovery_prediction'
            and event['body']['mode'] == 'prospective'
            and event['body']['case']['input']['x'] == 2)
        attempts = [event for event in events if event['kind'] == 'discovery_measurement_attempt']
        final_attempt = attempts[-1]
        def tamper_final(body):
            body['prediction_receipt'] = wrong_case_prediction
        _rewrite_journal(journal, 'discovery_measurement_attempt', tamper_final,
                         target_seq=final_attempt['seq'])
        with self.assertRaisesRegex(ValueError, 'prediction receipt belongs to another'):
            verify(self.root)

    def test_rehashed_goal_lineage_to_unrelated_request_is_rejected(self):
        journal = self.root / 'events.jsonl'
        events = [json.loads(line) for line in journal.read_text().splitlines()]
        request_signal = next(event['hash'] for event in events
                              if event['kind'] == 'goal_signal'
                              and isinstance(event['body']['content'], str))
        proposals = [event for event in events if event['kind'] == 'goal_interpretation_proposed']
        old_goal_id = proposals[1]['hash']
        mapping = _rewrite_journal(
            journal, 'goal_interpretation_proposed',
            lambda body: body.update(based_on=[request_signal]),
            target_seq=proposals[1]['seq'])
        new_goal_id = mapping[old_goal_id]

        report, path = self.report()
        ledger = report['goal_ledger']
        revised = ledger['interpretations'].pop(old_goal_id)
        revised['based_on'] = [request_signal]
        ledger['interpretations'][new_goal_id] = revised
        ledger['active'] = new_goal_id
        report['result']['answer']['goal'] = new_goal_id
        path.write_text(json.dumps(report))

        with self.assertRaisesRegex(ValueError, 'clarified goal does not preserve'):
            verify(self.root)

    def test_rehashed_contradiction_relabelled_as_match_is_rejected(self):
        journal = self.root / 'events.jsonl'
        events = [json.loads(line) for line in journal.read_text().splitlines()]
        contradicted = [event for event in events
                        if event['kind'] == 'discovery_measurement_result'
                        and event['body']['status'] == 'contradicted']
        self.assertEqual(len(contradicted), 2)
        target_seq = contradicted[1]['seq']

        events = [json.loads(line) for line in journal.read_text().splitlines()]
        mapping = {}
        previous = ''
        for event in events:
            old_hash = event['hash']
            event['body'] = _replace_refs(event['body'], mapping)
            if event['seq'] == target_seq:
                event['body']['status'] = 'prediction_matched'
            event['prev'] = previous
            event['hash'] = digest({key: event[key] for key in ('seq', 'kind', 'body', 'prev')})
            mapping[old_hash] = event['hash']
            previous = event['hash']
        journal.write_text(''.join(json.dumps(event, sort_keys=True, separators=(',', ':')) + '\n'
                                   for event in events))
        with self.assertRaisesRegex(ValueError, 'status disagrees with prediction and observation'):
            verify(self.root)

    def test_rehashed_failed_development_check_is_rejected(self):
        _rewrite_journal(self.root / 'events.jsonl', 'discovery_development_check_receipt',
                         lambda body: body['receipt'].update(output='{"result":{"y":99}}'))
        with self.assertRaisesRegex(ValueError, 'check output does not match'):
            verify(self.root)


if __name__ == '__main__':
    unittest.main()
