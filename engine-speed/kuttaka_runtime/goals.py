"""Provisional goal interpretations, separate from facts and user authority.

The ledger does not infer intent. A model can propose alternative goals and
unknowns. Only a trusted host can record external signals or authorize an
interpretation; neither operation is exposed in the model tool dictionary.
"""
from .store import digest
from .programs import _json_copy, _nonempty_string


class GoalLedger:
    def __init__(self, original_request, journal):
        self._original = _nonempty_string(original_request, 'original request')
        self.journal = journal
        self._signals, self._goals = {}, {}
        self._active = None
        self.request_evidence = self.record_signal(original_request, source='user')

    @classmethod
    def restore(cls, original_request, journal, events):
        """Restore controller state from a validated, complete journal prefix.

        Replay performs no writes. The caller must pass the full hash-chained
        prefix and an exclusively resumed Journal whose head is that prefix.
        """
        original = _nonempty_string(original_request, 'original request')
        if not isinstance(events, list):
            raise ValueError('events must be a journal prefix')
        previous = ''
        signals, goals, active, request_evidence = {}, {}, None, None
        for seq, event in enumerate(events):
            if (not isinstance(event, dict) or set(event) != {'seq', 'kind', 'body', 'prev', 'hash'}
                    or type(event['seq']) is not int or event['seq'] != seq
                    or event['prev'] != previous
                    or event['hash'] != digest({k: v for k, v in event.items() if k != 'hash'})):
                raise ValueError('invalid journal prefix')
            previous = event['hash']
            kind, body, eid = event['kind'], event['body'], event['hash']
            if kind == 'goal_signal':
                if not isinstance(body, dict) or set(body) != {'source', 'content'}:
                    raise ValueError('invalid goal signal')
                if body['source'] not in ('user', 'observation'):
                    raise ValueError('invalid goal signal source')
                content = _json_copy(body['content'])
                if not signals and (body['source'] != 'user' or content != original):
                    raise ValueError('original request evidence mismatch')
                signals[eid] = {'source': body['source'], 'content': content}
                if request_evidence is None:
                    request_evidence = eid
            elif kind == 'goal_interpretation_proposed':
                expected = {'description', 'success_criteria', 'unknowns', 'tradeoffs', 'parent', 'based_on'}
                if not isinstance(body, dict) or set(body) != expected:
                    raise ValueError('invalid goal interpretation')
                value = _json_copy(body)
                _nonempty_string(value['description'], 'description')
                for key in ('success_criteria', 'unknowns', 'tradeoffs'):
                    if not isinstance(value[key], list) or any(not isinstance(x, str) or not x.strip() for x in value[key]):
                        raise ValueError('invalid goal interpretation')
                if value['parent'] is not None and value['parent'] not in goals:
                    raise ValueError('unknown goal parent')
                if not isinstance(value['based_on'], list) or any(x not in signals for x in value['based_on']):
                    raise ValueError('unknown goal evidence')
                goals[eid] = value
            elif kind == 'goal_interpretation_authorized':
                if not isinstance(body, dict) or set(body) != {'before', 'after', 'user_evidence'}:
                    raise ValueError('invalid goal authorization')
                if (body['before'] != active or body['after'] not in goals
                        or body['user_evidence'] not in signals
                        or signals[body['user_evidence']]['source'] != 'user'):
                    raise ValueError('invalid goal authorization lineage')
                active = body['after']
        if request_evidence is None:
            raise ValueError('journal has no original request signal')
        restored = cls.__new__(cls)
        restored._original, restored.journal = original, journal
        restored._signals, restored._goals = signals, goals
        restored._active, restored.request_evidence = active, request_evidence
        return restored

    @property
    def original_request(self):
        return self._original

    def record_signal(self, content, *, source):
        """Trusted host only: register a user clarification or a world observation."""
        if source not in ('user', 'observation'):
            raise ValueError('signal source must be user or observation')
        value = _json_copy(content)
        eid = self.journal.append('goal_signal', {'source': source, 'content': value})
        self._signals[eid] = {'source': source, 'content': value}
        return eid

    def propose(self, request):
        """Propose a goal interpretation without redefining the user's request.

        Input {description,success_criteria,unknowns,tradeoffs,parent,based_on}.
        Criteria, unknowns and tradeoffs are string lists; parent is null or a
        prior goal ID. based_on contains known signal IDs. Unsupported omissions
        remain possible; this ledger does not certify completeness or intent.
        """
        if not isinstance(request, dict) or set(request) != {
                'description', 'success_criteria', 'unknowns', 'tradeoffs', 'parent', 'based_on'}:
            raise ValueError('invalid goal interpretation')
        value = _json_copy(request)
        _nonempty_string(value['description'], 'description')
        for key in ('success_criteria', 'unknowns', 'tradeoffs'):
            if not isinstance(value[key], list) or any(not isinstance(x, str) or not x.strip() for x in value[key]):
                raise ValueError('criteria, unknowns and tradeoffs must be string lists')
        parent, basis = value['parent'], value['based_on']
        if parent is not None and (not isinstance(parent, str) or parent not in self._goals):
            raise ValueError('unknown parent interpretation')
        if not isinstance(basis, list) or any(not isinstance(e, str) or e not in self._signals for e in basis):
            raise ValueError('unknown goal evidence')
        gid = self.journal.append('goal_interpretation_proposed', value)
        self._goals[gid] = value
        return {'goal': gid, 'status': 'provisional', 'active': self._active}

    def authorize(self, goal, *, user_evidence):
        """Trusted host only: attest that a user signal authorizes this interpretation.

        The host must actually establish semantic agreement. Merely citing a user
        utterance is not an algorithmic proof of agreement. The initial request
        can suffice when it is clear; this does not require routine reconfirmation.
        """
        if goal not in self._goals:
            raise ValueError('unknown goal')
        if user_evidence not in self._signals or self._signals[user_evidence]['source'] != 'user':
            raise ValueError('user evidence required; observations do not authorize objectives')
        before = self._active
        self.journal.append('goal_interpretation_authorized', {'before': before, 'after': goal,
                                                              'user_evidence': user_evidence})
        self._active = goal
        return {'goal': goal, 'status': 'host_authorized'}

    def status(self, request):
        """Input {}. Return original request, provisional interpretations and signals."""
        if request != {}:
            raise ValueError('empty input required')
        return _json_copy({'original_request': self._original, 'active': self._active,
                          'interpretations': self._goals, 'signals': self._signals})

    def tools(self):
        return {'goal_propose': self.propose, 'goal_status': self.status}
