"""Goal-bound use of learned, provisional maps through exact artifact IDs.

The trusted host binds an authorized GoalLedger interpretation to an observable
output. No parser infers user intent here. Callbacks must be bounded and suitable
for reset/replay; these interfaces are not a sandbox for physical actions.
"""
from copy import deepcopy

from .active_navigation import plan, predict_trace, validate_model
from .distribution_probes import ProbeStream
from .store import digest


def _goal(goals, output):
    state = goals.status({})
    if (state['active'] is None or not isinstance(output, str)
            or not output.strip() or len(output) > 1024):
        return None
    return {'id': state['active'], 'output': output,
            'request': digest(state['original_request'])}


def _bound_model(memory, model_id, scope, context):
    payload = memory.get_qualified(model_id, scope)
    if (payload['kind'] != 'representation'
            or payload['applicability'].get('context') != context
            or payload['applicability'].get('scope') != scope):
        raise ValueError('learned map binding mismatch')
    if not validate_model(payload['body']):
        raise ValueError('invalid learned map')
    return payload['body']


def retain_route(memory, journal, goals, model_id, *, scope, context, goal_output):
    """Trusted host supplies the observable meaning of an authorized objective.

    A route is a proposal until executed and checked. No path means only that
    the current hypothesis has no path, never that the real goal is impossible.
    """
    goal = _goal(goals, goal_output)
    if goal is None:
        journal.append('navigation_goal_unresolved', {'model': model_id})
        return {'status': 'goal_unresolved'}
    model = _bound_model(memory, model_id, scope, context)
    binding = journal.append('navigation_goal_bound', {
        'goal': goal, 'authority': 'trusted host maps authorized intent to output'})
    proposed = plan(model, goal_output)
    if proposed['status'] == 'no_path':
        journal.append('navigation_no_route', {'model': model_id, 'goal': goal,
            'meaning': 'no route in this hypothesis; world reachability unresolved'})
        return {'status': 'no_route_in_hypothesis', 'model': model_id}
    actions = proposed['actions']
    body = {'format': 'astra-navigation-route-v1', 'model': model_id,
            'goal': goal, 'actions': actions, 'expected': predict_trace(model, actions)}
    route_id = memory.put({'kind': 'method', 'body': body,
        'dependencies': [model_id], 'provenance': {'binding': binding},
        'applicability': {'context': context, 'scope': scope,
                          'contract': 'reset, then checked deterministic route'}})
    journal.append('navigation_route_retained', {'artifact': route_id, 'model': model_id,
        'actions': actions, 'goal': goal})
    return {'status': 'route_proposed', 'artifact': route_id,
            'model': model_id, 'actions': actions}


def execute_route(memory, journal, goals, route_id, *, scope, context,
                  goal_output, reset, step, max_actions=128):
    """Reload and execute exact route/model IDs; stop at the first discrepancy.

    Each attempt, including reset, is committed before invoking the host. A
    mismatch invalidates the map and dependent routes in this scope. An observer
    exception is uncertainty, not a proof that the model is false. No automatic
    retry or interrupted-episode resume is implemented.
    """
    if type(max_actions) is not int or not 0 <= max_actions <= 128:
        raise ValueError('max_actions must be an integer in 0..128')
    payload = memory.get(route_id)
    if (payload['kind'] != 'method' or
            payload['applicability'].get('context') != context or
            payload['applicability'].get('scope') != scope):
        raise ValueError('route binding mismatch')
    body = payload['body']
    if (not isinstance(body, dict) or set(body) !=
            {'format', 'model', 'goal', 'actions', 'expected'} or
            body['format'] != 'astra-navigation-route-v1' or
            not isinstance(body['goal'], dict) or set(body['goal']) != {'id', 'output', 'request'} or
            payload['dependencies'] != [body['model']]):
        raise ValueError('invalid route artifact')
    # The artifact cannot choose what counts as success. The trusted host must
    # independently supply the same observable binding used for this objective.
    if _goal(goals, goal_output) != body['goal']:
        return {'status': 'goal_binding_changed', 'artifact': route_id}
    if memory.status(route_id, scope) == 'invalidated':
        return {'status': 'invalidated', 'artifact': route_id}
    model = _bound_model(memory, body['model'], scope, context)
    expected = predict_trace(model, body['actions'])
    if (body['expected'] != expected or expected[-1] != body['goal']['output']):
        raise ValueError('route does not reach its declared observable goal')
    if len(body['actions']) > max_actions:
        return {'status': 'execution_budget', 'artifact': route_id, 'action_attempts': 0}
    if reset is None:
        return {'status': 'reset_unavailable', 'artifact': route_id, 'action_attempts': 0}
    if not callable(reset) or not callable(step):
        raise ValueError('trusted reset/step callbacks required')
    use = journal.append('navigation_route_use', {'artifact': route_id,
        'model': body['model'], 'scope': scope, 'expected': expected,
        'actions': body['actions'], 'goal': body['goal'],
        'previous_status': memory.status(route_id, scope)})
    actual = []
    for index in range(len(expected)):
        action = None if index == 0 else body['actions'][index-1]
        attempt = journal.append('navigation_execution_attempt', {
            'use': use, 'index': index, 'action': action, 'expected': expected[index]})
        try:
            observation = reset() if index == 0 else step(action)
            if not isinstance(observation, str) or not observation.strip() or len(observation) > 1024:
                raise ValueError('observation must be a bounded nonempty string')
        except Exception as error:
            journal.append('navigation_execution_error', {'attempt': attempt,
                'type': type(error).__name__})
            return {'status': 'observer_error', 'artifact': route_id,
                    'reset_attempts': 1, 'action_attempts': index, 'observed': actual}
        actual.append(observation)
        evidence = journal.append('navigation_execution_observation', {
            'attempt': attempt, 'observation': observation, 'matches': observation == expected[index]})
        if observation != expected[index]:
            memory.invalidate(body['model'], 'fresh route counterexample', [evidence], scope=scope)
            question = memory.put({'kind': 'question', 'body': {
                'question': 'Which hidden state distinction or changed context explains this counterexample?',
                'model': body['model'], 'word': body['actions'][:index],
                'expected': expected[:index+1], 'observed': actual},
                'dependencies': [], 'provenance': {'evidence': evidence},
                'applicability': {'context': context, 'scope': scope}})
            return {'status': 'counterexample', 'artifact': route_id, 'question': question,
                    'reset_attempts': 1, 'action_attempts': index, 'observed': actual}
    receipt = journal.append('navigation_goal_observed', {'use': use,
        'artifact': route_id, 'model': body['model'], 'goal': body['goal'],
        'observed': actual, 'claim': 'this reset episode reached the host-bound observable goal'})
    memory.qualify(route_id, scope, 'fresh-route-execution', [receipt])
    return {'status': 'goal_observed', 'artifact': route_id, 'model': body['model'],
            'reset_attempts': 1, 'action_attempts': len(body['actions']),
            'observed': actual, 'evidence': receipt}


class NavigationTools:
    """JSON tool surface usable by any host agent; no model weights or API dependency.

    Host, not the model, supplies the goal binding, action interface and ceilings.
    The learner's total budgets persist across calls on this object. Goal/tool
    selection and domain instrumentation remain the caller's responsibility.
    """

    def __init__(self, learner, goals, *, goal_output, challenge_depth=5, execution_ceiling=128,
                 challenge_strategy='shortlex', extra_depth=1, sampling=None,
                 counterexample_strategy='prefixes', hybrid_compact=False):
        if type(challenge_depth) is not int or not 0 <= challenge_depth <= 12:
            raise ValueError('invalid challenge depth')
        if type(execution_ceiling) is not int or not 0 <= execution_ceiling <= 128:
            raise ValueError('invalid execution ceiling')
        if challenge_strategy not in ('shortlex', 'model_derived', 'adaptive',
                                      'distribution', 'hybrid'):
            raise ValueError('invalid challenge strategy')
        if type(extra_depth) is not int or not 0 <= extra_depth <= 3:
            raise ValueError('invalid extra depth')
        if counterexample_strategy not in ('prefixes', 'suffix'):
            raise ValueError('invalid counterexample strategy')
        if type(hybrid_compact) is not bool:
            raise ValueError('hybrid_compact must be a literal bool')
        if hybrid_compact and challenge_strategy != 'hybrid':
            raise ValueError('hybrid_compact is supported only by hybrid strategy')
        if challenge_strategy in ('distribution', 'hybrid'):
            if sampling is not None and (not isinstance(sampling, dict) or set(sampling) - {
                    'seed', 'max_length', 'persistence', 'epsilon', 'delta'}):
                raise ValueError('invalid host sampling configuration')
            actions = getattr(learner, 'actions', None)
            max_word_length = getattr(learner, 'max_word_length', None)
            if actions is None or type(max_word_length) is not int:
                raise ValueError('distribution strategies require a bounded ActiveNavigation learner')
            config = {'seed': 0, 'max_length': min(16, max_word_length), **deepcopy(sampling or {})}
            stream = ProbeStream(actions, **config)
            if stream.contract['max_length'] > max_word_length:
                raise ValueError('sampling max_length exceeds learner word ceiling')
            contract = stream.contract
            normalized_sampling = {key: contract[key] for key in
                ('seed', 'max_length', 'persistence', 'epsilon', 'delta')}
        else:
            if sampling is not None:
                raise ValueError('sampling is supported only for distribution or hybrid strategy')
            normalized_sampling = None
        self.learner, self.goals, self.goal_output = learner, goals, goal_output
        self.challenge_depth, self.execution_ceiling = challenge_depth, execution_ceiling
        self.remaining_execution = execution_ceiling
        self.challenge_strategy, self.extra_depth = challenge_strategy, extra_depth
        self._sampling = deepcopy(normalized_sampling)
        self.counterexample_strategy = counterexample_strategy
        self.hybrid_compact = hybrid_compact

    def learn(self, request):
        if request != {}:
            raise ValueError('empty input required')
        options = {'challenge_depth': self.challenge_depth,
            'strategy': self.challenge_strategy, 'extra_depth': self.extra_depth}
        # Preserve the legacy call shape for existing hosts and test doubles.
        if self.challenge_strategy in ('distribution', 'hybrid'):
            options['sampling'] = deepcopy(self._sampling)
        if self.counterexample_strategy != 'prefixes':
            options['counterexample_strategy'] = self.counterexample_strategy
        if self.hybrid_compact:
            options['hybrid_compact'] = True
        return self.learner.learn(**options)

    def route(self, request):
        if not isinstance(request, dict) or set(request) != {'model'}:
            raise ValueError('model ID required')
        return retain_route(self.learner.memory, self.learner.journal, self.goals,
            request['model'], scope=self.learner.scope, context=self.learner.context,
            goal_output=self.goal_output)

    def execute(self, request):
        if not isinstance(request, dict) or set(request) != {'route'}:
            raise ValueError('route ID required')
        # Each reset consumes one slot too, so repeated zero-action routes cannot
        # bypass the host's cumulative physical interaction ceiling.
        if self.remaining_execution <= 0:
            return {'status': 'execution_budget', 'action_attempts': 0}
        result = execute_route(self.learner.memory, self.learner.journal, self.goals,
            request['route'], scope=self.learner.scope, context=self.learner.context,
            goal_output=self.goal_output,
            reset=self.learner.reset_callback, step=self.learner.step_callback,
            max_actions=self.remaining_execution-1)
        self.remaining_execution -= result.get('action_attempts', 0) + result.get('reset_attempts', 0)
        return result

    def tools(self):
        return {'navigation_learn': self.learn, 'navigation_route': self.route,
                'navigation_execute': self.execute}
