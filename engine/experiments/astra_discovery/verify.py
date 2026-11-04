"""Validate recorded authored lifecycle receipts without rerunning models or sandboxes."""
import argparse
import json
from pathlib import Path

from kuttaka_runtime import ArtifactMemory
from kuttaka_runtime.programs import _strict_json_output
from kuttaka_runtime.store import Journal, canonical


CLARIFICATION = {
    'text': 'I need to predict readings on new inputs, not maximize the reading.',
    'attribution': 'scripted user fixture',
}


def _require(condition, message):
    if not condition:
        raise ValueError('recorded discovery verification failed: ' + message)


def _events_of(events, kind):
    return [event for event in events if event['kind'] == kind]


def _verify_measurements(events):
    by_hash = {event['hash']: (index, event) for index, event in enumerate(events)}
    attempts = _events_of(events, 'discovery_measurement_attempt')
    observations = _events_of(events, 'discovery_observation')
    results = _events_of(events, 'discovery_measurement_result')
    _require(len(attempts) == len(observations) == len(results),
             'measurement attempts, observations, and results do not pair one-to-one')

    mismatches = []
    statuses = []
    prediction_count = len(_events_of(events, 'discovery_prediction_attempt'))
    for attempt, observation, result in zip(attempts, observations, results):
        attempt_body, observation_body, result_body = attempt['body'], observation['body'], result['body']
        _require(attempt['seq'] < observation['seq'] < result['seq'],
                 'measurement evidence is out of order')
        _require(attempt_body['case'] == observation_body['case'],
                 'measurement observation does not match its attempted case')
        _require(result_body['evidence'] == observation['hash'],
                 'measurement result does not reference its observation event')
        _require(result_body['representation'] == attempt_body['representation'],
                 'measurement result representation differs from its attempt')

        receipt_id = attempt_body['prediction_receipt']
        representation = attempt_body['representation']
        if receipt_id is None:
            _require(representation is None,
                     'candidate measurement lacks its precommitted prediction')
            expected_status = 'observed'
        else:
            _require(receipt_id in by_hash, 'prediction receipt does not exist')
            prediction_index, prediction_event = by_hash[receipt_id]
            prediction = prediction_event['body']
            _require(prediction_index < attempt['seq'],
                     'prediction was not committed before its measurement')
            _require(prediction_event['kind'] == 'discovery_prediction',
                     'prediction receipt points to the wrong event type')
            _require(prediction.get('mode') == 'prospective',
                     'measurement used a non-prospective prediction receipt')
            _require(prediction.get('representation') == representation,
                     'prediction receipt belongs to another candidate')
            _require(prediction.get('case') == attempt_body['case'],
                     'prediction receipt belongs to another measurement case')
            matches = canonical(prediction['prediction']) == canonical(observation_body['observation'])
            expected_status = 'prediction_matched' if matches else 'contradicted'
            if not matches:
                mismatches.append({'representation': representation,
                                   'case': attempt_body['case'],
                                   'prediction': prediction['prediction'],
                                   'observation': observation_body['observation'],
                                   'prediction_receipt': receipt_id,
                                   'evidence': observation['hash']})
        _require(result_body['status'] == expected_status,
                 'measurement result status disagrees with prediction and observation')
        statuses.append(expected_status)

    _require(statuses == ['observed', 'prediction_matched', 'contradicted',
                           'contradicted', 'prediction_matched'],
             'unexpected authored fixture measurement sequence')
    _require(len(mismatches) == 2, 'expected exactly two retained candidate failures')
    return {'statuses': statuses, 'mismatches': mismatches,
            'prediction_executions': prediction_count,
            'observation_inputs': [event['body']['case']['input']['x'] for event in observations]}


def _verify_goal_ledger(events, report):
    signals = {}
    interpretations = {}
    authorizations = []
    for event in events:
        if event['kind'] == 'goal_signal':
            signals[event['hash']] = event['body']
        elif event['kind'] == 'goal_interpretation_proposed':
            proposal = event['body']
            _require(all(evidence in signals for evidence in proposal['based_on']),
                     'goal proposal cites unknown or later signal evidence')
            _require(proposal['parent'] is None or proposal['parent'] in interpretations,
                     'goal proposal parent is unknown or later')
            interpretations[event['hash']] = proposal
        elif event['kind'] == 'goal_interpretation_authorized':
            authorizations.append(event['body'])

    _require(len(signals) == 2 and len(interpretations) == 2 and len(authorizations) == 1,
             'unexpected authored goal-ledger event counts')
    signal_items = list(signals.items())
    original_id, original_signal = signal_items[0]
    clarification_id, clarification_signal = signal_items[1]
    _require(original_signal == {'source': 'user', 'content':
             'Help me understand this device well enough to use it.'},
             'original request signal changed')
    _require(clarification_signal == {'source': 'user', 'content': CLARIFICATION},
             'scripted clarification signal changed')

    goal_items = list(interpretations.items())
    first_id, first = goal_items[0]
    active_id, active = goal_items[1]
    _require(first['description'] == 'Maximize the device reading'
             and first['parent'] is None and first['based_on'] == [original_id],
             'initial provisional interpretation was not preserved')
    _require(active['description'] == 'Predict device readings'
             and active['success_criteria'] == ['checked predictions on new inputs']
             and active['parent'] == first_id
             and active['based_on'] == [clarification_id],
             'clarified goal does not preserve its parent and cited clarification')

    answer = report['result']['answer']
    authorization = authorizations[0]
    _require(answer.get('goal') == active_id and answer.get('user_evidence') == clarification_id,
             'final answer does not cite the clarified interpretation and signal')
    _require(authorization['after'] == active_id
             and authorization['user_evidence'] == clarification_id,
             'host authorization does not match the final clarified goal')
    reconstructed = {'original_request': original_signal['content'], 'active': active_id,
                     'interpretations': interpretations, 'signals': signals}
    _require(canonical(report['goal_ledger']) == canonical(reconstructed),
             'reported goal ledger differs from its journal reconstruction')
    return reconstructed


def verify(root):
    root = Path(root)
    report = json.loads((root/'results.json').read_text())
    events = Journal.read(root/'events.jsonl')
    ledger = _verify_goal_ledger(events, report)

    measurements = _verify_measurements(events)
    proposals = _events_of(events, 'discovery_representation')
    _require(len(proposals) == 3, 'expected three immutable representation proposals')
    proposal_bodies = [event['body'] for event in proposals]
    _require(proposal_bodies[1]['payload']['body']['parent'] == proposal_bodies[0]['representation']
             and proposal_bodies[2]['payload']['body']['parent'] == proposal_bodies[1]['representation'],
             'representation revision lineage is broken')
    proposal_ids = {body['representation'] for body in proposal_bodies}
    _require(all(mismatch['representation'] in proposal_ids for mismatch in measurements['mismatches']),
             'failed prediction is not linked to a retained candidate')
    _require([mismatch['case']['input']['x'] for mismatch in measurements['mismatches']] == [4, 5],
             'unexpected failed candidate inputs')

    assessments = [event['body'] for event in _events_of(events, 'discovery_assessment')]
    _require(len(assessments) == 6
             and assessments[0]['status'] == assessments[1]['status'] == 'retrospective_fit'
             and assessments[2]['status'] == 'supported'
             and assessments[3]['status'] == 'retrospective_fit'
             and assessments[4]['status'] == 'retrospective_fit'
             and assessments[5]['status'] == 'supported',
             'assessment sequence does not show retrospective fit then prospective support')
    adoptions = [event['body'] for event in _events_of(events, 'discovery_adoption')]
    _require(len(adoptions) == 2 and adoptions[0]['after'] == proposal_bodies[0]['representation']
             and adoptions[1]['after'] == proposal_bodies[2]['representation'],
             'only the supported original and revised candidates may be adopted')

    with ArtifactMemory(root/'memory') as memory:
        memory.get(report['policy_artifact'])
        for body in proposal_bodies:
            _require(memory.get(body['representation']) == body['payload'],
                     'stored representation differs from its journal proposal')

    finish_events = _events_of(events, 'investigator_finish')
    _require(len(finish_events) == 1, 'expected exactly one investigator finish')
    finish = finish_events[0]['body']
    _require(canonical(report['result']) == canonical(finish),
             'reported result differs from the journaled finish')
    policy_attempts = len(_events_of(events, 'policy_attempt'))
    policy_receipts = len(_events_of(events, 'policy_execution_receipt'))
    model_calls = len(_events_of(events, 'model_attempt'))
    tool_calls = len(_events_of(events, 'investigator_tool_attempt'))
    check_attempts = _events_of(events, 'discovery_development_check_attempt')
    check_receipts = _events_of(events, 'discovery_development_check_receipt')
    _require(policy_attempts == policy_receipts == finish['policy_executions'] == 19,
             'policy execution count disagrees with receipts or finish')
    _require(model_calls == finish['model_calls'] == 0,
             'unexpected model calls in authored fixture')
    _require(tool_calls == finish['tool_calls'] == 19,
             'tool call count disagrees with finish')
    _require(len(check_attempts) == len(check_receipts) == report['checker_extra_executions'] == 3,
             'final development-check count disagrees with receipts')
    checker_inputs = [event['body']['input']['x'] for event in check_attempts]
    _require(checker_inputs == report['checker_inputs'] == [0, 6, 10],
             'final development-check inputs differ from report')
    for attempt, receipt_event in zip(check_attempts, check_receipts):
        receipt = receipt_event['body']['receipt']
        _require(attempt['seq'] < receipt_event['seq']
                 and attempt['body']['input'] == receipt_event['body']['input']
                 and receipt['status'] == 'completed' and receipt['exit_code'] == 0,
                 'final development-check receipt does not match its attempt')
        x = attempt['body']['input']['x']
        _require(_strict_json_output(receipt['output']) == {'y': min(2*x, 6)},
                 'final development-check output does not match the authored device')
    _require(measurements['prediction_executions'] == report['prediction_executions'] == 12,
             'prediction execution count differs from journal')
    _require(measurements['observation_inputs'] == report['observation_inputs'] == [1, 2, 4, 5, 3],
             'observation inputs differ from journal')
    _require(finish['status'] == 'checked', 'authored fixture did not pass its supplied checker')

    _require(report['active'] == proposal_bodies[2]['representation'],
             'reported active representation differs from final supported candidate')
    _require(report['attribution'].startswith('all candidate/policy/device/clarification code authored'),
             'authored fixture attribution changed')

    return {'journal_integrity': True, 'artifact_integrity': True,
            'prediction_receipts_matched': len(measurements['statuses']) - 1,
            'candidate_failures_retained': len(measurements['mismatches']),
            'goal_lineage_reconstructed': len(ledger['interpretations']),
            'policy_executions': policy_attempts,
            'prediction_executions': measurements['prediction_executions'],
            'checker_executions': len(check_receipts), 'model_calls': model_calls}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('recorded', type=Path)
    print(json.dumps(verify(parser.parse_args().recorded), indent=2))
