"""Existing authored discovery fixture continued across four real processes."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys

from experiments.astra_discovery.run import goal_matches_clarification
from kuttaka_runtime import ArtifactMemory
from kuttaka_runtime.execution import PythonExecutor, preflight
from kuttaka_runtime.investigators import run_policy, investigator_payload, InvestigatorBudget
from kuttaka_runtime.programs import ProgramTask
from kuttaka_runtime.session import InvestigationSession
from kuttaka_runtime.store import Journal


EXTENSION = '''
fixture_run = run
def run(context):
    state = context['state'] or {'step': 0}
    step = state['step']
    if step < 18:
        return fixture_run(context)
    if step == 18:
        tool, value = 'retain_representation', {'representation': state['revised'],
            'name': 'retained device predictor', 'description': 'Authored discovery fixture output'}
    elif step == 19:
        state['method'] = context['history'][-1]['observation']['artifact']
        tool, value = 'validate_method', {'artifact': state['method']}
    else:
        return {'state': state, 'action': {'kind': 'answer', 'answer': {'artifact': state['method']}}}
    state['step'] = step + 1
    return {'state': state, 'action': {'kind': 'experiment', 'tool': tool, 'input': value,
        'predictions': [{'hypothesis': 'authored integration fixture', 'outcome': 'checked by host'}]}}
'''


class NoModel:
    identity = {'model': 'none', 'attribution': 'authored policy fixture'}

    def propose(self, context):
        raise AssertionError('no model calls permitted')


def stage(output, number, pause):
    if not preflight()['ready']:
        raise RuntimeError('pinned sandbox unavailable; no downloads attempted')
    executor = PythonExecutor(timeout=30)
    task = ProgramTask('Help me understand this device well enough to use it.', [
        {'input': {'tool': 'reading', 'input': {'x': x}}, 'expected': {'y': min(2*x, 6)}}
        for x in (0, 6, 10)])
    seen = []

    def reading(value):
        """Input {x: integer in [0,10]}; return a deterministic device reading."""
        if not isinstance(value, dict) or set(value) != {'x'} or type(value['x']) is not int or not 0 <= value['x'] <= 10:
            raise ValueError('invalid reading input')
        seen.append(value['x'])
        return {'y': min(2*value['x'], 6)}

    if number == 0:
        output.mkdir(parents=True, exist_ok=False)
        journal = Journal(output/'events.jsonl')
    else:
        previous = json.loads((output/f'stage-{number-1}.json').read_text())
        journal = Journal.resume(output/'events.jsonl', expected_head=previous['journal_head'])
    with journal, ArtifactMemory(output/'memory') as memory:
        options = {'context': {'device': 'authored bounded deterministic fixture', 'version': 1},
            'provenance': {'producer': 'authored lifecycle fixture'},
            'max_observations': 8, 'max_predictions': 32, 'max_program_executions': 8}
        if number == 0:
            session = InvestigationSession(task, memory, journal, executor, {'reading': reading}, **options)
            source = (Path(__file__).parents[1]/'astra_discovery'/'policy.py').read_text()+EXTENSION
            policy = memory.put(investigator_payload(source, name='authored continuation fixture',
                description='Existing discovery lifecycle extended with retention and validation'))
            (output/'policy.json').write_text(json.dumps({'artifact': policy})+'\n')
        else:
            session = InvestigationSession.restore(task, memory, journal, executor, {'reading': reading}, **options)
            policy = json.loads((output/'policy.json').read_text())['artifact']

        def clarify_purpose(value):
            """Input {}; return one scripted clarification, without external messaging."""
            if value != {}:
                raise ValueError('empty input required')
            text = 'I need to predict readings on new inputs, not maximize the reading.'
            eid = session.goals.record_signal({'text': text, 'attribution': 'scripted user fixture'}, source='user')
            return {'text': text, 'evidence': eid}

        def checker(request, answer):
            # Keep the existing exact authored clarification check. Authority is
            # not inferred from a model-proposed goal or a passed predictor.
            snapshot = session.goals.status({})
            for gid, proposal in snapshot['interpretations'].items():
                for eid in proposal['based_on']:
                    if goal_matches_clarification(session.goals, {'goal': gid, 'user_evidence': eid}):
                        session.goals.authorize(gid, user_evidence=eid)
                        return session.checker(request, answer)
            return False

        result = run_policy(task.request, policy, NoModel(),
            {**session.tools(), 'clarify_purpose': clarify_purpose}, checker, journal, executor,
            InvestigatorBudget(model_calls=0, tool_calls=24, policy_executions=24, max_steps=24),
            memory=memory, pause_after_steps=pause, resume=number > 0, resume_context=session.binding)
        events = Journal.read(output/'events.jsonl')
        report = {'stage': number, 'process_id': os.getpid(), 'result': asdict(result),
            'usage': session.usage(), 'new_observation_inputs': seen,
            'active_representation': session.discovery.active, 'goals': session.goals.status({}),
            'journal_head': events[-1]['hash'], 'session_binding': session.binding,
            'attribution': 'authored fixture; no model discovery or autonomous goal inference'}
    (output/f'stage-{number}.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'directory': str(output), 'stage': number, 'status': result.status,
                      'usage': report['usage'], 'new_observations': seen}), flush=True)
    return report


def run(output):
    output.mkdir(parents=True, exist_ok=False)
    project = Path(__file__).resolve().parents[2]
    for name, pauses in [('uninterrupted', [None]), ('resumed', [11, 8, 1, None])]:
        for number, pause in enumerate(pauses):
            command = [sys.executable, '-B', '-m', 'experiments.astra_session.run',
                       '--output', str(output/name), '--stage', str(number)]
            if pause is not None:
                command.extend(['--pause', str(pause)])
            subprocess.run(command, cwd=project, check=True, timeout=300)
    uninterrupted = json.loads((output/'uninterrupted'/'stage-0.json').read_text())
    resumed = [json.loads((output/'resumed'/f'stage-{i}.json').read_text()) for i in range(4)]
    assert uninterrupted['result']['status'] == resumed[-1]['result']['status'] == 'checked'
    assert [r['result']['status'] for r in resumed] == ['paused', 'paused', 'paused', 'checked']
    assert len({r['process_id'] for r in resumed}) == 4
    assert uninterrupted['usage'] == resumed[-1]['usage'] == {
        'observations': 5, 'predictions': 12, 'program_executions': 3}
    assert [x for r in resumed for x in r['new_observation_inputs']] == [1, 2, 4, 5, 3]
    assert all(resumed[-1]['result'][k] == uninterrupted['result'][k]
               for k in ('model_calls', 'tool_calls', 'policy_executions', 'steps'))
    final_sources = []
    for name, report in [('uninterrupted', uninterrupted), ('resumed', resumed[-1])]:
        with ArtifactMemory(output/name/'memory') as memory:
            artifact = report['result']['answer']['artifact']
            method = memory.get(artifact)
            final_sources.append(method['body']['source'])
            assert method['provenance']['derivation']['representation'] == report['active_representation']
    assert final_sources[0] == final_sources[1]
    result = {'status': 'checked', 'fresh_resume_processes': 4,
        'equal_cumulative_counts': True, 'no_repeated_observations': True,
        'same_final_method_source': True, 'usage_per_condition': resumed[-1]['usage'],
        'policy_executions_per_condition': resumed[-1]['result']['policy_executions'],
        'model_calls': 0,
        'attribution': 'existing authored scenario; engineering integration, not acquired investigation skill'}
    (output/'results.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stage', type=int)
    parser.add_argument('--pause', type=int)
    args = parser.parse_args()
    if args.stage is None:
        run(args.output.resolve())
    else:
        stage(args.output.resolve(), args.stage, args.pause)
