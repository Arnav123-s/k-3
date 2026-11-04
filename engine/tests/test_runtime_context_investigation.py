"""Opt-in bounded context, charged exact recall and clean-boundary continuation."""
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest

from kuttaka_runtime.context_memory import ContextMemory
from kuttaka_runtime.investigators import InvestigatorBudget, run_investigation
from kuttaka_runtime.store import Journal, canonical


def observation(index):
    return {'index': index, 'reading': index * 7,
            'note': 'earlier counterexample' if index == 3 else 'ordinary observation',
            'payload': 'x' * 240}


class StreamReplies:
    """Author-scripted workload, not model capability evidence."""
    identity = {'model': 'scripted-long-investigation', 'version': 1}
    max_tokens = 64
    last_usage = {'input_tokens': 0, 'output_tokens': 0}
    last_response = None

    def __init__(self, length):
        self.length = length
        self.context_sizes = []

    def propose(self, context):
        self.context_sizes.append(len(canonical(context).encode()))
        step = self.length + 2 - context['remaining']['steps'] - 1
        if step < self.length:
            return {'kind': 'experiment', 'tool': 'observe', 'input': step,
                    'predictions': [{'hypothesis': 'authored stream fixture', 'outcome': None}]}
        if step == self.length:
            if 'memory' not in context:
                return {'kind': 'answer', 'answer': context['history'][3]['observation']}
            return {'kind': 'experiment', 'tool': 'history_read',
                    'input': {'index': 3, 'path': '/observation'},
                    'predictions': [{'hypothesis': 'retained earlier evidence', 'outcome': None}]}
        entry = context['history'][-1]
        record = entry['record'] if 'memory' in context else entry
        return {'kind': 'answer', 'answer': record['observation']['value']}


class ContextInvestigationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def run_case(self, path, *, length=40, memory=None, pause=None, resume=False):
        observed = []
        def observe(value):
            """Input stream index (integer); return an authored observation."""
            observed.append(value)
            return observation(value)
        model = StreamReplies(length)
        budget = InvestigatorBudget(model_calls=length+2, tool_calls=length+2,
            policy_executions=0, max_steps=length+2, input_bytes_per_call=8000,
            max_response_tokens=64)
        head = Journal.read(path)[-1]['hash'] if resume else None
        journal = Journal.resume(path, expected_head=head) if resume else Journal(path)
        with journal:
            result = run_investigation('Read the stream, then recover observation three exactly.',
                model, {'observe': observe}, lambda g, a: a == observation(3), journal,
                budget, context_memory=memory, pause_after_steps=pause,
                resume=resume, resume_context={'stream_length': length})
        return result, model, observed

    def memory(self, max_bytes=1800):
        return ContextMemory(max_bytes=max_bytes, recent_records=2, read_bytes=1200)

    def test_same_context_limit_stops_full_history_but_paged_mode_recovers_old_evidence(self):
        baseline, _, _ = self.run_case(self.root/'full.jsonl')
        self.assertEqual(baseline.status, 'incomplete')
        self.assertEqual(baseline.reason, 'model context exceeds input byte budget')
        result, model, seen = self.run_case(self.root/'paged.jsonl', memory=self.memory())
        self.assertEqual(result.status, 'checked')
        self.assertEqual(result.answer, observation(3))
        self.assertEqual(result.tool_calls, 42)  # 40 observations, recall, checker.
        self.assertEqual(seen, list(range(40)))
        self.assertLessEqual(max(model.context_sizes), 8000)
        events = Journal.read(self.root/'paged.jsonl')
        recall = [e['body'] for e in events if e['kind'] == 'investigator_proposal'
                  and e['body'].get('tool') == 'history_read']
        self.assertEqual(len(recall), 1)
        contexts = [e['body']['context'] for e in events if e['kind'] == 'model_attempt']
        self.assertTrue(contexts[-2]['memory']['partial'])
        self.assertNotIn(3, [r['index'] for r in contexts[-2]['history']])

    def test_resume_keeps_omitted_evidence_and_original_call_counts(self):
        path = self.root/'resumed.jsonl'
        first, _, seen1 = self.run_case(path, memory=self.memory(), pause=30)
        self.assertEqual(first.status, 'paused')
        resumed, _, seen2 = self.run_case(path, memory=self.memory(), resume=True)
        reference, _, _ = self.run_case(self.root/'reference.jsonl', memory=self.memory())
        self.assertEqual(asdict(resumed), asdict(reference))
        self.assertEqual(seen1 + seen2, list(range(40)))
        self.assertEqual(resumed.answer, observation(3))

    def test_full_history_succeeds_when_all_evidence_fits(self):
        result, _, seen = self.run_case(self.root/'short-full.jsonl', length=8)
        self.assertEqual(result.status, 'checked')
        self.assertEqual(result.answer, observation(3))
        self.assertEqual(seen, list(range(8)))
        self.assertEqual(result.tool_calls, 9)  # No recall tool is needed.

    def test_resume_rejects_changed_view_contract_and_disabling_memory(self):
        path = self.root/'changed.jsonl'
        self.run_case(path, memory=self.memory(), pause=30)
        for changed in (self.memory(2000), None):
            with self.subTest(memory=changed), self.assertRaisesRegex(ValueError, 'contract changed'):
                self.run_case(path, memory=changed, resume=True)

    def test_history_tool_collision_rejects_without_running_model(self):
        with Journal(self.root/'collision.jsonl') as journal:
            with self.assertRaisesRegex(ValueError, 'collision'):
                run_investigation('fixture', StreamReplies(1), {'history_read': lambda v: v},
                    lambda g, a: True, journal, context_memory=self.memory())

    def test_search_refuses_a_page_that_cannot_advance_its_cursor(self):
        memory = ContextMemory(read_bytes=512)
        memory.bind([{'text': 'x'*262}])
        result = memory.search({'query': 'x'*262})
        self.assertEqual(result['status'], 'invalid_request')
        self.assertIn('shorten', result['error'])
        self.assertLessEqual(len(canonical(result).encode()), 512)


if __name__ == '__main__':
    unittest.main()
