"""One persistent host toolbox for goals, discovery and checked method reuse.

Continuation is at an explicit completed action boundary, not an automatic retry
after an uncertain external effect. The host owns the task/checker and must keep
observation semantics stable under its declared context identifier.
"""
from .discovery import DiscoveryWorkspace
from .goals import GoalLedger
from .programs import ProgramTools, QUALIFIER_ID, _json_copy
from .store import Journal, digest


class InvestigationSession:
    def __init__(self, task, memory, journal, executor, observation_tools, *, context,
                 provenance, max_observations=16, max_predictions=64, max_program_executions=64,
                 _events=None):
        self.task, self.memory, self.journal = task, memory, journal
        manifest = _json_copy({'version': 'investigation-session-v1', 'request': task.request,
            'task_scope': task.scope_id, 'context': context, 'provenance': provenance,
            'tools': {name: tool.__doc__ or '' for name, tool in observation_tools.items()},
            'limits': {'observations': max_observations, 'predictions': max_predictions,
                       'program_executions': max_program_executions}})
        self.binding = digest(manifest)
        self.programs = ProgramTools(task, memory, journal, executor,
            max_executions=max_program_executions, provenance=provenance)
        if _events is None:
            journal.append('investigation_session', {'binding': self.binding, 'manifest': manifest})
            self.goals = GoalLedger(task.request, journal)
            self.discovery = DiscoveryWorkspace(memory, journal, executor, observation_tools,
                context=context, max_observations=max_observations, max_executions=max_predictions)
        else:
            starts = [e['body'] for e in _events if e['kind'] == 'investigation_session']
            if starts != [{'binding': self.binding, 'manifest': manifest}]:
                raise ValueError('session binding changed or multiple sessions share this journal')
            self.goals = GoalLedger.restore(task.request, journal, _events)
            self.discovery = DiscoveryWorkspace.restore(memory, journal, executor, observation_tools,
                context=context, events=_events)
            self._restore_programs(_events)

    @classmethod
    def restore(cls, task, memory, journal, executor, observation_tools, **kwargs):
        """Reconstruct controllers under the caller's exclusively resumed journal."""
        events = Journal.read(journal.path)
        if not events or events[-1]['kind'] != 'investigator_pause':
            raise ValueError('session restore requires a clean investigator pause')
        return cls(task, memory, journal, executor, observation_tools, _events=events, **kwargs)

    def _restore_programs(self, events):
        attempts, completed = {}, set()
        for event in events:
            body = event['body']
            if event['kind'] == 'program_execution_precommit':
                if body['scope'] != self.task.scope_id or body['attempt'] != len(attempts)+1:
                    raise ValueError('program execution scope or count changed')
                attempts[event['hash']] = body
            elif event['kind'] == 'program_execution_result':
                attempt = attempts.get(body.get('proposal'))
                if (attempt is None or body['proposal'] in completed
                        or body['attempt'] != attempt['attempt'] or body['artifact'] != attempt['artifact']):
                    raise ValueError('unmatched program execution result')
                completed.add(body['proposal'])
                self.programs._receipts.append(event['hash'])
            elif event['kind'] == 'method_check_receipt':
                self.programs._receipts.append(event['hash'])
            elif event['kind'] == 'method_validation' and body['status'] == 'qualified':
                artifact = body['artifact']
                if body['scope'] != self.task.scope_id or body['checker'] != QUALIFIER_ID:
                    raise ValueError('method qualification scope changed')
                if self.memory.status(artifact, self.task.scope_id) == 'qualified':
                    self.memory.get_qualified(artifact, self.task.scope_id)
                    self.programs._locally_qualified.add(artifact)
        if set(attempts) != completed or len(attempts) > self.programs.max_executions:
            raise ValueError('unfinished or over-budget program execution')
        self.programs._execution_attempts = len(attempts)

    def retain_representation(self, request):
        """Copy an active executable conjecture into method memory, unqualified.

        Input {representation, name, description}. Source input/output semantics
        remain exactly run({tool,input}) -> predicted JSON observation. No task
        adaptation or correctness is inferred. validate_method must still check
        the new method against the host task before a final answer is accepted.
        """
        if not isinstance(request, dict) or set(request) != {'representation', 'name', 'description'}:
            raise ValueError('representation, name and description required')
        rid = request['representation']
        if rid != self.discovery.active:
            raise ValueError('only the active supported representation can be retained')
        support = self.discovery.assess({'representation': rid})
        if support['status'] != 'supported':
            raise ValueError('representation no longer supported')
        payload = self.memory.get(rid)
        result = self.programs.remember_method({'name': request['name'],
            'description': request['description'], 'source': payload['body']['source']},
            derivation={'representation': rid, 'support': support, 'session': self.binding})
        self.journal.append('representation_retained', {'representation': rid,
            'method': result['artifact'], 'session': self.binding, 'task_qualified': False})
        return result

    def tools(self):
        return {**self.goals.tools(), **self.discovery.tools(), **self.programs.tools(),
                'retain_representation': self.retain_representation}

    def checker(self, original_request, answer):
        return self.programs.checker(original_request, answer)

    def usage(self):
        return {'observations': self.discovery.observations, 'predictions': self.discovery.executions,
                'program_executions': self.programs.execution_attempts}
