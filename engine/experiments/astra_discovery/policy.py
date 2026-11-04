"""Authored protocol fixture, deliberately not a learned investigator."""


def run(context):
    state = context['state'] or {'step': 0}
    i = state['step']
    previous = context['history'][-1]['observation'] if context['history'] else None
    if i == 1:
        state['request_evidence'] = next(iter(previous['signals']))
    if i == 2:
        state['old_goal'] = previous['goal']
    if i == 3:
        state['clarification'] = previous['evidence']
    if i == 4:
        state['goal'] = previous['goal']
    if i == 5:
        state['evidence'] = [previous['evidence']]
    if i == 6:
        state['linear'] = previous['representation']
    if i == 11:
        state['evidence'].append(previous['evidence'])
    if i == 12:
        state['patch'] = previous['representation']
    if i == 14:
        state['evidence'].append(previous['evidence'])
    if i == 15:
        state['revised'] = previous['representation']
    linear = "def run(case):\n    return {'y': 2 * case['input']['x']}\n"
    patch = "def run(case):\n    x = case['input']['x']\n    return {'y': {1: 2, 2: 4, 4: 6}.get(x, 2*x)}\n"
    revised = "def run(case):\n    return {'y': min(2 * case['input']['x'], 6)}\n"
    def proposal(name, source, parent):
        return {'name': name, 'source': source, 'parent': parent, 'based_on': state['evidence']}
    def measurement(rid, x):
        return {'representation': rid, 'tool': 'reading', 'input': {'x': x}}
    if i == 0:
        tool, value = 'goal_status', {}
    elif i == 1:
        tool, value = 'goal_propose', {'description': 'Maximize the device reading',
            'success_criteria': ['largest attainable reading'], 'unknowns': ['intended use'],
            'tradeoffs': [], 'parent': None, 'based_on': [state['request_evidence']]}
    elif i == 2:
        tool, value = 'clarify_purpose', {}
    elif i == 3:
        tool, value = 'goal_propose', {'description': 'Predict device readings',
            'success_criteria': ['checked predictions on new inputs'], 'unknowns': ['device response law'],
            'tradeoffs': ['measurement cost versus confidence'], 'parent': state['old_goal'],
            'based_on': [state['clarification']]}
    elif i == 4:
        tool, value = 'discover_measure', measurement(None, 1)
    elif i == 5:
        tool, value = 'discover_represent', proposal('linear', linear, None)
    elif i == 6:
        tool, value = 'discover_assess', {'representation': state['linear']}
    elif i == 7:
        tool, value = 'discover_adopt', {'representation': state['linear']}
    elif i == 8:
        tool, value = 'discover_measure', measurement(state['linear'], 2)
    elif i == 9:
        tool, value = 'discover_adopt', {'representation': state['linear']}
        # adopt has no new evidence; save the measurement from the previous turn.
        state['evidence'].append(context['history'][-1]['observation']['evidence'])
    elif i == 10:
        tool, value = 'discover_measure', measurement(state['linear'], 4)
    elif i == 11:
        tool, value = 'discover_represent', proposal('overfit patch', patch, state['linear'])
    elif i == 12:
        tool, value = 'discover_assess', {'representation': state['patch']}
    elif i == 13:
        tool, value = 'discover_measure', measurement(state['patch'], 5)
    elif i == 14:
        tool, value = 'discover_represent', proposal('revised response', revised, state['patch'])
    elif i == 15:
        tool, value = 'discover_assess', {'representation': state['revised']}
    elif i == 16:
        tool, value = 'discover_measure', measurement(state['revised'], 3)
    elif i == 17:
        tool, value = 'discover_adopt', {'representation': state['revised']}
    else:
        return {'state': state, 'action': {'kind': 'answer', 'answer': {
            'representation': state['revised'], 'goal': state['goal'], 'user_evidence': state['clarification']}}}
    state['step'] = i + 1
    return {'state': state, 'action': {'kind': 'experiment', 'tool': tool, 'input': value,
        'predictions': [{'hypothesis': 'authored lifecycle fixture', 'outcome': {'status': 'fixture'}}]}}
