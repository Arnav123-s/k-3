"""Unchanged recovered investigator, scripted replies, real sandbox continuation.

This is a compatibility/continuation fixture, not source qualification or a
model task win. The evidence-revision decisions come from the exact recovered
policy; the experiment choices and answer are authored here.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from kuttaka_runtime import ArtifactMemory
from kuttaka_runtime.execution import PythonExecutor, preflight
from kuttaka_runtime.investigators import InvestigatorBudget, investigator_payload, run_policy
from kuttaka_runtime.store import Journal, digest


PROJECT = Path(__file__).resolve().parents[2]
CANDIDATE = PROJECT / 'experiments/investigator_transfer/recovered/candidate-v10.json'
SOURCE_HASH = '9c35846efb7c8e4b95b66cd9bb358dc8affc57459f9e9884d195870b54c4eb87'
CANDIDATE_HASH = '9a81b283ef018d500bddc640163869ff39633e0335615e30e90aa053bed63854'
HYPOTHESIS = 'reading equals twice x'
GOAL = 'Observe the device at inputs 1, 2 and 4, then report those three readings.'
BINDING = {'fixture': 'scripted clipped device v1', 'candidate': CANDIDATE_HASH}
BUDGET = InvestigatorBudget(model_calls=4, tool_calls=4, policy_executions=12,
                            max_steps=12, max_response_tokens=128)


class ScriptedReplies:
    identity = {'model': 'authored continuation fixture; no endpoint', 'version': 1}
    max_tokens = 128
    last_usage = {'input_tokens': 0, 'output_tokens': 0}
    last_response = None

    def propose(self, context):
        observed = [h for h in context['history']
                    if h.get('action', {}).get('tool') == 'reading']
        if len(observed) < 3:
            x = (1, 2, 4)[len(observed)]
            return {'kind': 'experiment', 'tool': 'reading', 'input': {'x': x},
                    'predictions': [{'hypothesis': HYPOTHESIS, 'outcome': {'y': 2*x}}]}
        return {'kind': 'answer', 'answer': [h['observation']['y'] for h in observed]}


def stage(output, number, pause):
    if not preflight()['ready']:
        raise RuntimeError('pinned sandbox unavailable; no download attempted')
    candidate = json.loads(CANDIDATE.read_text())
    assert digest(candidate) == CANDIDATE_HASH
    assert hashlib.sha256(candidate['source'].encode()).hexdigest() == SOURCE_HASH
    if number == 0:
        output.mkdir(parents=True, exist_ok=False)
        journal = Journal(output/'events.jsonl')
    else:
        previous = json.loads((output/f'stage-{number-1}.json').read_text())
        journal = Journal.resume(output/'events.jsonl', expected_head=previous['journal_head'])
    seen = []

    def reading(value):
        """Input {x: integer in [0,10]}; return the deterministic reading {y}."""
        if (not isinstance(value, dict) or set(value) != {'x'}
                or type(value['x']) is not int or not 0 <= value['x'] <= 10):
            raise ValueError('invalid reading input')
        seen.append(value['x'])
        return {'y': min(2*value['x'], 6)}

    with journal, ArtifactMemory(output/'memory') as memory:
        payload = investigator_payload(candidate['source'], name=candidate['name'],
            description=candidate['description'], provenance={'candidate_sha256': CANDIDATE_HASH})
        if number == 0:
            artifact = memory.put(payload)
        else:
            artifact = previous['artifact']
            assert memory.get(artifact) == payload
        result = run_policy(GOAL, artifact, ScriptedReplies(), {'reading': reading},
            lambda goal, answer: goal == GOAL and answer == [2, 4, 6], journal,
            PythonExecutor(timeout=30), BUDGET, memory=memory, pause_after_steps=pause,
            resume=number > 0, resume_context=BINDING)
        events = Journal.read(output/'events.jsonl')
        revisions = [e['body'] for e in events if e['kind'] == 'representation_revision']
        report = {'stage': number, 'process_id': os.getpid(), 'artifact': artifact,
            'source_sha256': SOURCE_HASH, 'result': asdict(result),
            'new_observation_inputs': seen, 'representation_revisions': revisions,
            'journal_head': events[-1]['hash'], 'new_endpoint_calls': 0,
            'attribution': 'unchanged model-acquired policy; authored proposals and final-answer fixture'}
    (output/f'stage-{number}.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'condition': output.name, 'stage': number, 'status': result.status,
                      'new_observations': seen, 'revisions': len(revisions)}), flush=True)


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    # First pause retains an unconsumed model reply. The others retain the
    # policy's support and later retraction, respectively.
    for condition, pauses in [('uninterrupted', [None]), ('resumed', [1, 4, 3, None])]:
        for number, pause in enumerate(pauses):
            command = [sys.executable, '-B', '-m', 'experiments.astra_session.recovered_policy',
                       '--output', str(output/condition), '--stage', str(number)]
            if pause is not None:
                command += ['--pause', str(pause)]
            subprocess.run(command, cwd=PROJECT, check=True, timeout=240)
    result = verify(output)
    (output/'results.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2), flush=True)


def verify(output):
    """Check existing records offline, including their hash chains and counts."""
    reference = json.loads((output/'uninterrupted/stage-0.json').read_text())
    resumed = [json.loads((output/f'resumed/stage-{i}.json').read_text()) for i in range(4)]
    assert [r['result']['status'] for r in resumed] == ['paused', 'paused', 'paused', 'checked']
    assert reference['result'] == resumed[-1]['result']
    assert reference['artifact'] == resumed[-1]['artifact']
    assert len({r['process_id'] for r in resumed}) == 4
    assert [x for r in resumed for x in r['new_observation_inputs']] == [1, 2, 4]
    assert resumed[1]['representation_revisions'][-1]['after']['supported_hypotheses'] == [HYPOTHESIS]
    assert 'supported_hypotheses' not in resumed[2]['representation_revisions'][-1]['after']
    assert reference['representation_revisions'] == resumed[-1]['representation_revisions']
    assert resumed[-1]['representation_revisions'][-1]['after']['hypothesis_stats'][HYPOTHESIS] == {
        'hits': 2, 'misses': 1, 'total': 3}
    semantic = []
    for condition in ('uninterrupted', 'resumed'):
        events = Journal.read(output/condition/'events.jsonl')
        assert events[-1]['kind'] == 'investigator_finish'
        assert events[-1]['body'] == reference['result']
        starts = [e['body'] for e in events if e['kind'] == 'investigator_start']
        assert len(starts) == 1 and starts[0]['policy_sha256'] == SOURCE_HASH
        assert starts[0]['model'] == ScriptedReplies.identity
        for counter, kind in [('model_calls', 'model_attempt'),
                              ('tool_calls', 'investigator_tool_attempt'),
                              ('policy_executions', 'policy_attempt'), ('steps', 'policy_result')]:
            assert reference['result'][counter] == sum(e['kind'] == kind for e in events)
        receipts = [e['body']['receipt'] for e in events if e['kind'] == 'policy_execution_receipt']
        assert len(receipts) == reference['result']['policy_executions']
        assert all(r['status'] == 'completed' and r['exit_code'] == 0 for r in receipts)
        assert all(e['kind'] not in {'policy_execution_failure', 'policy_error', 'model_error'} for e in events)
        pauses = [e for e in events if e['kind'] == 'investigator_pause']
        assert len(pauses) == (3 if condition == 'resumed' else 0)
        assert sum(e['kind'] == 'investigator_resume' for e in events) == len(pauses)
        for pause in pauses:
            assert pause['body']['contract']['policy_artifact'] == reference['artifact']
        if pauses:
            pending = pauses[0]['body']['cursor']['previous_model_reply']
            assert pending['kind'] == 'experiment' and pending['input'] == {'x': 1}
        with ArtifactMemory(output/condition/'memory') as memory:
            artifact = memory.get(reference['artifact'])
            assert hashlib.sha256(artifact['body']['source'].encode()).hexdigest() == SOURCE_HASH
        semantic.append([(e['kind'], e['body']) for e in events if e['kind'] in {
            'model_reply', 'policy_result', 'investigator_observation', 'representation_revision'}])
    assert semantic[0] == semantic[1]
    return {'status': 'checked', 'source_sha256': SOURCE_HASH,
        'candidate_sha256': CANDIDATE_HASH, 'artifact': reference['artifact'],
        'same_actions_and_revisions': True, 'same_cumulative_counts': True,
        'pending_reply_preserved': True, 'support_then_retraction_preserved': True,
        'no_repeated_observations': True, 'fresh_resume_processes': 4,
        'per_condition': reference['result'], 'new_endpoint_calls': 0,
        'sandbox_executions_total': 2*reference['result']['policy_executions'],
        'attribution': 'scripted continuation compatibility; not live qualification or transfer'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stage', type=int)
    parser.add_argument('--pause', type=int)
    parser.add_argument('--verify-only', action='store_true')
    args = parser.parse_args()
    if args.verify_only:
        result = verify(args.output.resolve())
        assert result == json.loads((args.output/'results.json').read_text())
        print(json.dumps(result, indent=2))
    elif args.stage is None:
        run(args.output.resolve())
    else:
        stage(args.output.resolve(), args.stage, args.pause)
