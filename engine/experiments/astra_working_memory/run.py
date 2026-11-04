"""Authored workload checks for bounded context and changing planning goals."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from kuttaka_runtime import ArtifactMemory, AdaptivePlanningMemory, ContextMemory
from kuttaka_runtime.factored_memory import evaluate
from kuttaka_runtime.investigators import InvestigatorBudget, run_investigation
from kuttaka_runtime.store import Journal, canonical


LENGTH = 128
CONTEXT = {'world': 'authored complete Boolean fixture', 'revision': 1}


def reading(index):
    return {'index': index, 'reading': index*7,
            'note': 'earlier counterexample' if index == 3 else 'ordinary observation',
            'payload': 'x'*240}


class ScriptedStream:
    identity = {'model': 'authored memory workload; no endpoint', 'version': 1}
    max_tokens = 64
    last_usage = {'input_tokens': 0, 'output_tokens': 0}
    last_response = None

    def propose(self, context):
        step = LENGTH + 2 - context['remaining']['steps'] - 1
        if step < LENGTH:
            tool, value = 'observe', step
        elif step == LENGTH:
            if 'memory' not in context:
                return {'kind': 'answer', 'answer': context['history'][3]['observation']}
            tool, value = 'history_read', {'index': 3, 'path': '/observation'}
        else:
            entry = context['history'][-1]
            record = entry['record'] if 'memory' in context else entry
            return {'kind': 'answer', 'answer': record['observation']['value']}
        return {'kind': 'experiment', 'tool': tool, 'input': value,
                'predictions': [{'hypothesis': 'authored integration fixture', 'outcome': None}]}


def stream_stage(output, condition, stage):
    folder = output/condition
    if stage == 0:
        folder.mkdir(parents=True, exist_ok=False)
        journal = Journal(folder/'events.jsonl')
    else:
        previous = json.loads((folder/'stage-0.json').read_text())
        journal = Journal.resume(folder/'events.jsonl', expected_head=previous['journal_head'])
    seen = []
    def observe(index):
        """Input an integer stream index; return an authored observation."""
        if type(index) is not int or not 0 <= index < LENGTH:
            raise ValueError('invalid stream index')
        seen.append(index)
        return reading(index)
    with journal:
        result = run_investigation('Read all 128 observations; then recover observation three exactly.',
            ScriptedStream(), {'observe': observe}, lambda g, a: a == reading(3), journal,
            InvestigatorBudget(model_calls=LENGTH+2, tool_calls=LENGTH+2, policy_executions=0,
                max_steps=LENGTH+2, input_bytes_per_call=8000, max_response_tokens=64),
            context_memory=None if condition == 'full' else ContextMemory(
                max_bytes=1800, recent_records=2, read_bytes=1200),
            pause_after_steps=100 if condition == 'resumed' and stage == 0 else None,
            resume=stage > 0, resume_context={'stream_length': LENGTH})
        events = Journal.read(folder/'events.jsonl')
        contexts = [e['body']['context'] for e in events if e['kind'] == 'model_attempt']
        report = {'result': asdict(result), 'new_observations': seen,
            'process_id': os.getpid(), 'journal_head': events[-1]['hash'],
            'maximum_context_bytes': max(len(canonical(c).encode()) for c in contexts),
            'endpoint_calls': 0, 'attribution': 'scripted model replies; no task-performance claim'}
    (folder/f'stage-{stage}.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'condition': condition, 'stage': stage,
        'status': result.status, 'observations': len(seen)}), flush=True)


def validate_plan(spec, goal, actions):
    state = dict(spec['initial'])
    cost = 0
    for name in actions:
        row = spec['actions'][name]
        assert evaluate(row['guard'], state)
        state = {v: evaluate(row['assign'].get(v, v), state) for v in spec['variables']}
        cost += row['cost']
    assert evaluate(goal, state)
    return {'final_done': state['done'], 'final_red': state['red'], 'cost': cost}


def changing_goals(output):
    names = ['done', 'red'] + [f'irrelevant_{i}' for i in range(998)]
    spec = {'variables': names, 'initial': dict.fromkeys(names, False), 'goal': 'done',
        'actions': {'finish': {'guard': True, 'cost': 1, 'assign': {'done': True}},
                    'toggle_red': {'guard': True, 'cost': 1, 'assign': {'red': ['not', 'red']}}}}
    with ArtifactMemory(output/'planning-memory') as memory:
        wrapper = AdaptivePlanningMemory(memory)
        first = wrapper.compile(spec, context=CONTEXT, complete=True, max_relevant=4)
        assert first['audit']['retained_variables'] == ['done']
        assert first['audit']['removed_actions'] == ['toggle_red']
    # Reopen disk memory before the goal changes.
    with ArtifactMemory(output/'planning-memory') as memory:
        wrapper = AdaptivePlanningMemory(memory)
        old = wrapper.plan(first['view'], goal='done', context=CONTEXT)
        changed = wrapper.plan(first['view'], goal='red', context=CONTEXT)
        combined = wrapper.plan(changed['view'], goal=['and', 'done', 'red'], context=CONTEXT)
        assert old['reused'] and old['plan']['actions'] == ['finish']
        assert changed['recompiled_for_goal'] and changed['plan']['actions'] == ['toggle_red']
        assert combined['audit']['retained_variables'] == ['done', 'red']
        checks = [validate_plan(spec, goal, row['plan']['actions']) for goal, row in (
            ('done', old), ('red', changed), (['and', 'done', 'red'], combined))]
        assert [c['cost'] for c in checks] == [1, 1, 2]
        refused = wrapper.plan(first['view'], goal='red', context={'world': 'changed'})
        assert refused['status'] == 'context_mismatch'
        report = {'initial': first, 'old_goal': old, 'changed_goal': changed,
            'combined_goal': combined, 'executed_in_source_model': checks,
            'changed_context': refused, 'source_variables': 1000,
            'attribution': 'supplied complete deterministic Boolean model and explicit goals; engineered compiler'}
    (output/'goal-results.json').write_text(json.dumps(report, indent=2)+'\n')
    return report


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    project = Path(__file__).resolve().parents[2]
    for condition, stages in [('full', [0]), ('paged', [0]), ('resumed', [0, 1])]:
        for stage in stages:
            subprocess.run([sys.executable, '-B', '-m', 'experiments.astra_working_memory.run',
                '--output', str(output), '--condition', condition, '--stage', str(stage)],
                cwd=project, check=True, timeout=120)
    full = json.loads((output/'full/stage-0.json').read_text())
    paged = json.loads((output/'paged/stage-0.json').read_text())
    resumed = [json.loads((output/f'resumed/stage-{i}.json').read_text()) for i in (0, 1)]
    assert full['result']['status'] == 'incomplete'
    assert full['result']['reason'] == 'model context exceeds input byte budget'
    assert paged['result']['status'] == resumed[-1]['result']['status'] == 'checked'
    assert paged['result'] == resumed[-1]['result']
    assert resumed[0]['result']['status'] == 'paused'
    assert resumed[0]['process_id'] != resumed[1]['process_id']
    assert [x for r in resumed for x in r['new_observations']] == list(range(LENGTH))
    semantic = []
    for condition in ('paged', 'resumed'):
        events = Journal.read(output/condition/'events.jsonl')
        semantic.append([(e['kind'], e['body']) for e in events if e['kind'] in {
            'model_reply', 'investigator_observation', 'representation_revision'}])
    assert semantic[0] == semantic[1]
    goals = changing_goals(output)
    result = {'endpoint_calls': 0, 'scripted_calls_per_completed_condition': LENGTH+2,
        'observations_per_completed_condition': LENGTH, 'recall_calls_per_completed_condition': 1,
        'full_history_result': full['result'], 'paged_result': paged['result'],
        'same_semantic_events_after_resume': True,
        'maximum_paged_context_bytes': paged['maximum_context_bytes'], 'context_ceiling': 8000,
        'goal_view_projected_states': [goals[k]['audit']['enumerated_projected_states']
                                      for k in ('old_goal', 'changed_goal', 'combined_goal')],
        'storage_limit': 'full evidence/source retained; host RAM, disk and search time are not bounded',
        'attribution': 'engineered mechanism demonstration with scripted replies and supplied models'}
    (output/'results.json').write_text(json.dumps(result, indent=2)+'\n')
    files = ['kuttaka_runtime/'+name for name in (
        'context_memory.py', 'adaptive_planning_memory.py', 'investigators.py',
        'factored_memory.py', 'planning_memory.py', 'store.py', 'artifacts.py')]
    files.append('experiments/astra_working_memory/run.py')
    (output/'implementation.json').write_text(json.dumps({'capture': 'after verified development run',
        'files': {p: hashlib.sha256((project/p).read_bytes()).hexdigest() for p in files}}, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--condition', choices=['full', 'paged', 'resumed'])
    parser.add_argument('--stage', type=int, default=0)
    args = parser.parse_args()
    if args.condition:
        stream_stage(args.output.resolve(), args.condition, args.stage)
    else:
        run(args.output.resolve())
