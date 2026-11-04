"""Goal-scoped persistent views over caller-supplied complete Boolean models.

This wrapper adds source retention and goal-relative recompilation to the
existing engineered planning compilers. It does not infer models or goals.
"""

from __future__ import annotations

import hashlib
import json

from .artifacts import ArtifactMemory, canonical
from .factored_memory import compile_factored
from .planning_memory import plan as plan_compiled
from .store import digest


FORMAT = 'adaptive-planning-view-v1'
SOURCE_FORMAT = 'complete-boolean-transition-system-v1'
COMPILER_VERSION = 'factored-boolean-planner-v1'
SOURCE_CHECKER = 'adaptive-planning-memory.source.v1'
VIEW_CHECKER = 'adaptive-planning-memory.compile-factored.v1'


def _copy(value):
    return json.loads(canonical(value))


def _hash_json(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _scope(source_id, context):
    return digest({'format': FORMAT, 'source': source_id, 'context': context})


class AdaptivePlanningMemory:
    """Persist a complete source model and explicitly scoped goal-cost views.

    ``compile`` compiles a caller-provided finite Boolean model. ``plan`` reuses
    a view only for its exact binding. A different goal is compiled from the
    retained source under the same context; a different context is refused.
    Source/view artifacts are ordinary immutable ArtifactMemory data.

    Qualification receipts are host attestations containing hashes and checker
    identities, not authenticated proofs. This wrapper trusts the host that
    writes and qualifies ArtifactMemory; attacker-controlled stores with
    fabricated qualifications are outside this trust boundary.
    """

    def __init__(self, memory: ArtifactMemory, *, provenance=None):
        if not isinstance(memory, ArtifactMemory):
            raise ValueError('memory must be an ArtifactMemory')
        self.memory = memory
        self.provenance = _copy({'attribution': 'engineered compiler wrapper; caller-supplied model'}
                                if provenance is None else provenance)

    def compile(self, spec, *, context, complete, preserve='goal_cost', max_relevant=12):
        """Store source and a compiled view; return dropped distinctions and bytes.

        ``spec`` is copied before compilation/storage. The model must satisfy the
        existing compiler's declared-complete finite Boolean contract. The
        projection preserves the selected goal obligation only.
        """
        if complete is not True:
            raise ValueError('requires caller-declared complete Boolean model')
        source_spec = _copy(spec)
        context_value = _copy(context)
        if preserve not in ('goal_cost', 'behavior'):
            raise ValueError('unknown preservation obligation')
        # Compile before writing artifacts, so invalid/too-large inputs leave no
        # half-registered source or view in persistent memory.
        record, start, audit = compile_factored(source_spec, context=context_value,
            complete=complete, preserve=preserve, max_relevant=max_relevant)
        source_digest = _hash_json(source_spec)
        source_payload = {
            'kind': 'representation',
            'body': {'format': SOURCE_FORMAT, 'name': 'complete Boolean source model',
                     'description': 'Caller-supplied finite transition system retained for recompilation.',
                     'spec': source_spec, 'source_digest': source_digest},
            'dependencies': [], 'provenance': self.provenance,
            'applicability': {'contract': SOURCE_FORMAT},
        }
        source_id = _hash_json(source_payload)
        scope_id = _scope(source_id, context_value)
        if self._status_if_present(source_id, scope_id) == 'invalidated':
            raise ValueError('retained source is invalidated for this context; host revalidation is required')
        self.memory.put(source_payload)
        self._qualify_source(source_id, source_digest, scope_id)
        return self._store_view(source_id, source_spec, context_value, record, start,
                                audit, preserve, max_relevant, reused=False)

    def plan(self, view_id, *, goal, context, preserve=None, max_relevant=None):
        """Plan from a view, recompiling only for a changed goal under same context.

        Returns explicit refusal statuses for missing/invalidated source or a
        changed context. A changed preservation mode/compiler ceiling is a new
        binding and therefore recompiles from retained source.
        """
        try:
            view_payload = self.memory.get(view_id)
        except (KeyError, ValueError, OSError):
            return {'status': 'view_unavailable', 'view': view_id}
        if (view_payload.get('kind') != 'representation'
                or not isinstance(view_payload.get('body'), dict)
                or view_payload['body'].get('format') != FORMAT):
            raise ValueError('not an adaptive planning view')
        body = view_payload['body']
        context_value = _copy(context)
        if canonical(context_value) != canonical(body.get('context')):
            return {'status': 'context_mismatch', 'view': view_id,
                    'expected_context': _copy(body.get('context'))}

        scope_id = body.get('scope')
        source_id = body.get('source_artifact')
        envelope = body.get('preservation_envelope')
        compiler = body.get('compiler')
        if (not isinstance(source_id, str) or view_payload.get('dependencies') != [source_id]
                or not isinstance(envelope, dict) or set(envelope) != {'preserve', 'goal', 'guarantee'}
                or not isinstance(compiler, dict) or set(compiler) != {'name', 'version', 'max_relevant'}
                or compiler.get('name') != 'compile_factored'
                or compiler.get('version') != COMPILER_VERSION
                or body.get('complete_model_assumed') is not True
                or body.get('goal') != envelope.get('goal')
                or body.get('context') != view_payload.get('applicability', {}).get('context')
                or scope_id != _scope(source_id, context_value)
                or body.get('binding') != self._binding(source_id, body.get('source_digest'),
                    body.get('goal'), context_value, envelope.get('preserve'), compiler.get('max_relevant'))):
            raise ValueError('malformed adaptive planning view binding')
        try:
            if self.memory.status(view_id, scope_id) == 'invalidated':
                return {'status': 'invalidated_view', 'view': view_id}
            if self.memory.status(source_id, scope_id) == 'invalidated':
                return {'status': 'invalidated_source', 'source': source_id, 'view': view_id}
            source_payload = self.memory.get(source_id)
        except (KeyError, ValueError, OSError):
            return {'status': 'source_unavailable', 'source': source_id, 'view': view_id}
        if (source_payload.get('kind') != 'representation'
                or source_payload.get('body', {}).get('format') != SOURCE_FORMAT
                or source_id not in view_payload.get('dependencies', [])):
            return {'status': 'source_unavailable', 'source': source_id, 'view': view_id}
        source_spec = _copy(source_payload['body'].get('spec'))
        if (_hash_json(source_spec) != source_payload['body'].get('source_digest')
                or body.get('source_digest') != source_payload['body'].get('source_digest')):
            return {'status': 'source_unavailable', 'source': source_id, 'view': view_id}

        # Artifact applicability and a `qualified` status are not proof of a
        # compiler run. Require this wrapper's exact checker identity and a
        # receipt bound to the actual source, scope, binding, and cached output.
        if not self._has_source_receipt(source_id, source_payload['body']['source_digest'], scope_id):
            return {'status': 'source_unqualified', 'source': source_id, 'view': view_id}
        if not self._has_view_receipt(view_id, body, scope_id):
            return {'status': 'view_unqualified', 'view': view_id}

        desired_goal = _copy(goal)
        desired_preserve = body['preservation_envelope']['preserve'] if preserve is None else preserve
        desired_max_relevant = body['compiler']['max_relevant'] if max_relevant is None else max_relevant
        if desired_preserve not in ('goal_cost', 'behavior'):
            raise ValueError('unknown preservation obligation')
        if type(desired_max_relevant) is not int or not 0 <= desired_max_relevant <= 16:
            raise ValueError('invalid relevant-variable ceiling')
        requested_binding = self._binding(source_id, source_payload['body']['source_digest'],
            desired_goal, context_value, desired_preserve, desired_max_relevant)
        if requested_binding == body.get('binding'):
            result = plan_compiled(body['record'], body['start'], context=context_value)
            return {'status': 'planned', 'view': view_id, 'source': source_id,
                    'reused': True, 'plan': result,
                    'audit': _copy(body['audit']), 'bytes': _copy(body['bytes'])}

        # New goal/objective, same declared world context: rebuild from the
        # exact retained source rather than trying to expand a lossy quotient.
        revised_spec = _copy(source_spec)
        revised_spec['goal'] = desired_goal
        record, start, audit = compile_factored(revised_spec, context=context_value,
            complete=True, preserve=desired_preserve, max_relevant=desired_max_relevant)
        stored = self._store_view(source_id, source_spec, context_value, record, start,
                                  audit, desired_preserve, desired_max_relevant, reused=False,
                                  goal=desired_goal)
        result = plan_compiled(record, start, context=context_value)
        return {'status': 'planned', 'view': stored['view'], 'source': source_id,
                'reused': False, 'recompiled_for_goal': True, 'plan': result,
                'audit': stored['audit'], 'bytes': stored['bytes']}

    @staticmethod
    def _binding(source_id, source_digest, goal, context, preserve, max_relevant):
        return {'format': FORMAT, 'source_artifact': source_id,
                'source_digest': source_digest, 'compiler': COMPILER_VERSION,
                'context': _copy(context), 'goal': _copy(goal),
                'preserve': preserve, 'max_relevant': max_relevant,
                'complete_model': True}

    def _store_view(self, source_id, source_spec, context, record, start, audit,
                    preserve, max_relevant, *, reused, goal=None):
        goal_value = _copy(source_spec['goal'] if goal is None else goal)
        source_payload = self.memory.get(source_id)
        source_digest = source_payload['body']['source_digest']
        binding = self._binding(source_id, source_digest, goal_value, context,
                                preserve, max_relevant)
        scope_id = _scope(source_id, context)
        source_spec_bytes = len(canonical(source_spec))
        view_record_bytes = len(canonical(record))
        body = {'format': FORMAT, 'name': 'goal-scoped planning view',
            'description': f'{preserve} view for caller-declared goal under exact context.',
            'source_artifact': source_id, 'source_digest': source_digest,
            'compiler': {'name': 'compile_factored', 'version': COMPILER_VERSION,
                         'max_relevant': max_relevant},
            'context': _copy(context), 'goal': goal_value,
            'preservation_envelope': {'preserve': preserve, 'goal': goal_value,
                'guarantee': ('minimum_goal_cost' if preserve == 'goal_cost'
                              else 'behavior_of_declared_boolean_model')},
            'complete_model_assumed': True, 'binding': binding, 'scope': scope_id,
            'record': _copy(record), 'start': start, 'audit': _copy(audit),
            'bytes': {'source_spec_bytes': source_spec_bytes,
                      'compiled_source_spec_bytes': audit.get('source_spec_bytes', source_spec_bytes),
                      'source_artifact_payload_bytes': len(canonical(source_payload)),
                      'view_record_bytes': view_record_bytes},
        }
        payload = {'kind': 'representation', 'body': body,
            'dependencies': [source_id], 'provenance': self.provenance,
            'applicability': {'format': FORMAT, 'context': _copy(context),
                              'goal': goal_value, 'preserve': preserve}}
        view_id = _hash_json(payload)
        source_status = self._status_if_present(source_id, scope_id)
        if source_status == 'invalidated':
            raise ValueError('retained source is invalidated for this context; host revalidation is required')
        if source_status == 'qualified':
            if not self._has_source_receipt(source_id, source_digest, scope_id):
                raise ValueError('retained source has a different host qualification')
        else:
            self._qualify_source(source_id, source_digest, scope_id)
        view_status = self._status_if_present(view_id, scope_id)
        if view_status == 'invalidated':
            raise ValueError('cached view is invalidated for this context; host revalidation is required')
        if view_status == 'qualified':
            if not self._has_view_receipt(view_id, body, scope_id):
                raise ValueError('cached view has a different host qualification')
        else:
            self.memory.put(payload)
            self.memory.qualify(view_id, scope_id, VIEW_CHECKER,
                                self._view_evidence(view_id, body, scope_id))
        return {'source': source_id, 'view': view_id, 'scope': scope_id,
                'reused': reused, 'audit': _copy(audit), 'bytes': body['bytes']}

    @staticmethod
    def _source_evidence(source_id, source_digest, scope_id):
        return [f'{SOURCE_FORMAT}:{source_id}:{source_digest}:{scope_id}']

    def _qualify_source(self, source_id, source_digest, scope_id):
        status = self._status_if_present(source_id, scope_id)
        if status == 'invalidated':
            raise ValueError('retained source is invalidated for this context; host revalidation is required')
        if status == 'qualified':
            if self._has_source_receipt(source_id, source_digest, scope_id):
                return
            raise ValueError('retained source has a different host qualification')
        self.memory.qualify(source_id, scope_id, SOURCE_CHECKER,
                            self._source_evidence(source_id, source_digest, scope_id))

    def _status_if_present(self, artifact_id, scope_id):
        try:
            return self.memory.status(artifact_id, scope_id)
        except KeyError:
            return 'absent'

    @staticmethod
    def _view_evidence(view_id, body, scope_id):
        result_digest = _hash_json({'binding': body.get('binding'), 'record': body.get('record'),
                                    'start': body.get('start'), 'audit': body.get('audit')})
        return [f'{FORMAT}:{view_id}:{body.get("source_artifact")}:'
                f'{body.get("source_digest")}:{scope_id}:{result_digest}']

    def _has_source_receipt(self, source_id, source_digest, scope_id):
        try:
            return (self.memory.status(source_id, scope_id) == 'qualified'
                    and any(q.get('scope') == scope_id and q.get('checker') == SOURCE_CHECKER
                            and q.get('active') is True
                            and q.get('evidence') == self._source_evidence(source_id, source_digest, scope_id)
                            for q in self.memory.qualifications(source_id)))
        except (KeyError, ValueError):
            return False

    def _has_view_receipt(self, view_id, body, scope_id):
        try:
            return (self.memory.status(view_id, scope_id) == 'qualified'
                    and any(q.get('scope') == scope_id and q.get('checker') == VIEW_CHECKER
                            and q.get('active') is True
                            and q.get('evidence') == self._view_evidence(view_id, body, scope_id)
                            for q in self.memory.qualifications(view_id)))
        except (KeyError, ValueError):
            return False
