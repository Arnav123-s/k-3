"""Evidence-linked executable conjectures for an investigator's host toolbox.

No hypothesis family or solution is supplied here. Callers propose code; the
injected isolated executor predicts tool observations. Support means only that
retained deterministic observations fit, including an uncached prospective test.
It is neither a proof of generality nor authority to redefine the user's goal.
"""
from .programs import _json_copy, _strict_json_output, _nonempty_string
from .store import canonical, digest


DISCOVERY_VERSION = 'discovery-workspace-v1'


class DiscoveryWorkspace:
    def __init__(self, memory, journal, executor, observation_tools, *, context,
                 max_observations=16, max_executions=64):
        if any(type(x) is not int or x < 0 for x in (max_observations, max_executions)):
            raise ValueError('invalid discovery budget')
        if (not isinstance(observation_tools, dict) or
                any(not isinstance(k, str) or not k or not callable(v) for k, v in observation_tools.items())):
            raise ValueError('invalid observation tools')
        self.memory, self.journal, self.executor = memory, journal, executor
        self.observation_tools = dict(observation_tools)
        self.context = _json_copy(context)
        self.max_observations, self.max_executions = max_observations, max_executions
        self.observations = self.executions = 0
        self._candidates, self._cache, self._evidence = {}, {}, {}
        self.active = None
        journal.append('discovery_start', {'version': DISCOVERY_VERSION, 'context': self.context,
            'tools': {name: tool.__doc__ or '' for name, tool in sorted(observation_tools.items())},
            'max_observations': max_observations,
            'max_executions': max_executions,
            'assumption': 'bounded trusted deterministic observation callbacks; exact JSON comparison'})

    @classmethod
    def restore(cls, memory, journal, executor, observation_tools, *, context, events):
        """Rebuild state from a full, validated journal prefix without effects.

        The caller must exclusively resume the same journal at this prefix's
        head. Pending prediction or measurement effects fail closed; a caller
        must retain the old prefix together with the resumed segment.
        """
        if (not isinstance(events, list) or not isinstance(observation_tools, dict)
                or not callable(executor)
                or any(not isinstance(k, str) or not k or not callable(v)
                       for k, v in observation_tools.items())):
            raise ValueError('invalid restore inputs')
        supplied_context = _json_copy(context)
        previous = ''
        starts = []
        for seq, event in enumerate(events):
            if (not isinstance(event, dict) or set(event) != {'seq', 'kind', 'body', 'prev', 'hash'}
                    or type(event['seq']) is not int or event['seq'] != seq
                    or event['prev'] != previous
                    or event['hash'] != digest({k: v for k, v in event.items() if k != 'hash'})):
                raise ValueError('invalid journal prefix')
            previous = event['hash']
            if event['kind'] == 'discovery_start':
                starts.append(event['body'])
        if len(starts) != 1:
            raise ValueError('journal must contain exactly one discovery start')
        start = starts[0]
        expected = {'version', 'context', 'tools', 'max_observations', 'max_executions', 'assumption'}
        if set(start) != expected or start['version'] != DISCOVERY_VERSION:
            raise ValueError('unsupported discovery journal version')
        tool_contracts = {name: tool.__doc__ or '' for name, tool in sorted(observation_tools.items())}
        if canonical(start['context']) != canonical(supplied_context) or start['tools'] != tool_contracts:
            raise ValueError('discovery context or tool contract mismatch')
        for key in ('max_observations', 'max_executions'):
            if type(start[key]) is not int or start[key] < 0:
                raise ValueError('invalid discovery budget in journal')

        workspace = cls.__new__(cls)
        workspace.memory, workspace.journal, workspace.executor = memory, journal, executor
        workspace.observation_tools = dict(observation_tools)
        workspace.context = supplied_context
        workspace.max_observations = start['max_observations']
        workspace.max_executions = start['max_executions']
        workspace.observations = workspace.executions = 0
        workspace._candidates, workspace._cache, workspace._evidence = {}, {}, {}
        workspace.active = None

        # Check proposal-time lineage against evidence and candidates that
        # existed then, rather than trusting descriptive artifact fields.
        known_evidence, known_representations = [], set()
        for event in events:
            if event['kind'] == 'discovery_representation':
                body = event['body']
                if not isinstance(body, dict) or set(body) != {'representation', 'payload'}:
                    raise ValueError('invalid representation event')
                payload = body['payload']
                metadata = payload.get('body') if isinstance(payload, dict) else None
                if (not isinstance(metadata, dict) or
                        (metadata.get('parent') is not None and metadata['parent'] not in known_representations) or
                        metadata.get('observed_before_proposal') != known_evidence or
                        not isinstance(metadata.get('based_on'), list) or
                        any(not isinstance(evidence, str) or evidence not in known_evidence
                            for evidence in metadata['based_on'])):
                    raise ValueError('invalid representation evidence lineage')
                known_representations.add(body['representation'])
            elif event['kind'] == 'discovery_measurement_result':
                evidence = event['body'].get('evidence')
                if evidence not in known_evidence:
                    known_evidence.append(evidence)

        # Validate each artifact identity and its immutable source before using it.
        for event in events:
            if event['kind'] != 'discovery_representation':
                continue
            body = event['body']
            if not isinstance(body, dict) or set(body) != {'representation', 'payload'}:
                raise ValueError('invalid representation event')
            rid, payload = body['representation'], _json_copy(body['payload'])
            if not isinstance(rid, str):
                raise ValueError('invalid representation ID')
            try:
                stored = memory.get(rid)
            except Exception as error:
                raise ValueError('representation artifact is unavailable') from error
            if stored != payload:
                raise ValueError('representation artifact identity mismatch')
            if (payload.get('kind') != 'representation'
                    or payload.get('applicability') != {'contract': 'deterministic-tool-predictor-v1', 'context': supplied_context}
                    or payload.get('body', {}).get('context') != supplied_context
                    or not isinstance(payload.get('body', {}).get('source'), str)
                    or not payload['body']['source'].strip()
                    or not isinstance(payload.get('body', {}).get('name'), str)
                    or not payload['body']['name'].strip()
                    or payload.get('dependencies') != []):
                raise ValueError('invalid stored representation')
            source = payload['body']['source']
            if rid in workspace._candidates and workspace._candidates[rid]['source'] != source:
                raise ValueError('representation source changed')
            workspace._candidates.setdefault(rid, {'source': source, 'checks': {},
                                                    'fresh': set(), 'failed_cases': {}})

        # Pair each effect attempt with one terminal event. Replaying a dangling
        # attempt could duplicate an outside effect, so reject rather than retry.
        prediction_attempts = {}
        predictions = {}
        measurement_attempts = {}
        observations = {}
        completed_results = set()
        for event in events:
            kind, body, eid = event['kind'], event['body'], event['hash']
            if kind == 'discovery_prediction_attempt':
                rid = body.get('representation')
                if rid not in workspace._candidates or body.get('mode') not in ('prospective', 'retrospective'):
                    raise ValueError('invalid prediction attempt')
                if (workspace.executions >= workspace.max_executions
                        or body.get('number') != workspace.executions + 1):
                    raise ValueError('prediction attempt exceeds or resets its committed budget')
                key = (rid, canonical(body.get('case')), body['mode'])
                prediction_attempts[eid] = key
                workspace.executions += 1
            elif kind == 'discovery_prediction':
                rid = body.get('representation')
                key = (rid, canonical(body.get('case')), body.get('mode'))
                matching = [aid for aid, value in prediction_attempts.items()
                            if value == key and aid not in predictions]
                if not matching or not isinstance(body.get('prediction'), (dict, list, str, int, bool, type(None), float)):
                    raise ValueError('prediction lacks matching precommit')
                predictions[eid] = (key, _json_copy(body['prediction']))
                aid = matching[0]
                predictions[aid] = predictions[eid]
                workspace._candidates[rid]['failed_cases'].pop(key[1], None)
                historical = workspace._cache.get(key[1])
                if historical is not None:
                    workspace._candidates[rid]['checks'][historical['evidence']] = {
                        'matched': canonical(body['prediction']) == canonical(historical['observation']),
                        'receipt': eid}
            elif kind == 'discovery_prediction_failure':
                rid = body.get('representation')
                # Failure has no case in its payload; pair with earliest unresolved attempt.
                pending = [aid for aid in prediction_attempts if aid not in predictions]
                if not pending or rid != prediction_attempts[pending[0]][0]:
                    raise ValueError('prediction failure lacks matching attempt')
                aid = pending[0]
                predictions[aid] = None
                workspace._candidates[rid]['failed_cases'][prediction_attempts[aid][1]] = 'prediction execution failed'
            elif kind == 'discovery_prediction_blocked':
                rid, case = body.get('representation'), body.get('case')
                if (rid not in workspace._candidates or body.get('mode') not in ('prospective', 'retrospective')
                        or workspace.executions < workspace.max_executions
                        or body.get('reason') != 'execution budget exhausted'):
                    raise ValueError('blocked prediction has unknown representation')
                workspace._candidates[rid]['failed_cases'][canonical(case)] = 'execution budget exhausted'
            elif kind == 'discovery_measurement_attempt':
                if (workspace.observations >= workspace.max_observations
                        or body.get('number') != workspace.observations + 1):
                    raise ValueError('measurement attempt exceeds or resets its committed budget')
                rid = body.get('representation')
                prediction_ref = body.get('prediction_receipt')
                if rid is None:
                    if prediction_ref is not None:
                        raise ValueError('observation-only attempt has a prediction receipt')
                elif (rid not in workspace._candidates or prediction_ref not in predictions
                      or predictions[prediction_ref] is None
                      or predictions[prediction_ref][0] != (rid, canonical(body.get('case')), 'prospective')):
                    raise ValueError('measurement attempt lacks its prospective precommit')
                workspace.observations += 1
                measurement_attempts[eid] = body
            elif kind == 'discovery_measurement_failure':
                pending = [aid for aid in measurement_attempts if aid not in observations]
                if not pending or measurement_attempts[pending[0]].get('case') != body.get('case'):
                    raise ValueError('measurement failure lacks matching attempt')
                observations[pending[0]] = None
            elif kind == 'discovery_observation':
                case = body.get('case')
                pending = [aid for aid in measurement_attempts if aid not in observations]
                if not pending or measurement_attempts[pending[0]].get('case') != case:
                    raise ValueError('observation lacks matching measurement attempt')
                observations[pending[0]] = (eid, _json_copy(body['observation']))
            elif kind == 'discovery_measurement_result':
                aid = body.get('evidence')
                if aid not in {value[0] for value in observations.values() if value is not None}:
                    raise ValueError('measurement result lacks observation')
                completed_results.add(aid)
                # Locate the observation event and its case, then rebuild cache.
                obs_event = next(e for e in events if e['hash'] == aid)
                case = obs_event['body']['case']
                observation = obs_event['body']['observation']
                entry = {'case': case, 'observation': observation, 'evidence': aid}
                workspace._cache[canonical(case)] = entry
                workspace._evidence[aid] = entry
                pred_ref = measurement_attempts[next(k for k, v in observations.items()
                                                       if v is not None and v[0] == aid)].get('prediction_receipt')
                rid = body.get('representation')
                if rid is not None:
                    attempt_body = measurement_attempts[next(k for k, v in observations.items()
                                                               if v is not None and v[0] == aid)]
                    if attempt_body.get('representation') != rid:
                        raise ValueError('measurement representation differs from precommit')
                    pair = predictions.get(pred_ref)
                    if pair is None or pair[0][:2] != (rid, canonical(case)):
                        raise ValueError('measurement does not reference its exact prediction')
                    matched = canonical(pair[1]) == canonical(observation)
                    if body.get('status') != ('prediction_matched' if matched else 'contradicted'):
                        raise ValueError('measurement result disagrees with prediction receipt')
                    workspace._candidates[rid]['checks'][aid] = {'matched': matched, 'receipt': pred_ref}
                    if (matched and pair[0][2] == 'prospective'
                            and attempt_body.get('prediction_receipt')):
                        workspace._candidates[rid]['fresh'].add(aid)
                else:
                    attempt_body = measurement_attempts[next(k for k, v in observations.items()
                                                               if v is not None and v[0] == aid)]
                    if attempt_body.get('representation') is not None or pred_ref is not None or body.get('status') != 'observed':
                        raise ValueError('unrepresented measurement has prediction metadata')
                if body.get('status') not in ('observed', 'prediction_matched', 'contradicted'):
                    raise ValueError('invalid measurement status')
            elif kind == 'discovery_support_suspended':
                body = event['body']; rid, evidence = body.get('representation'), body.get('evidence')
                pending = [(attempt_id, value) for attempt_id, value in observations.items()
                           if value is not None and value[0] == evidence]
                if (rid != workspace.active or rid not in workspace._candidates or len(pending) != 1):
                    raise ValueError('invalid support suspension')
                attempt_id, (evidence, observed) = pending[0]
                attempt = measurement_attempts[attempt_id]
                current_prediction = predictions.get(attempt.get('prediction_receipt'))
                active_was_checked = (attempt.get('representation') == rid
                    and current_prediction is not None
                    and current_prediction[0] == (rid, canonical(attempt['case']), 'prospective'))
                active_matches = (active_was_checked and
                    canonical(current_prediction[1]) == canonical(observed))
                if active_matches:
                    raise ValueError('support suspended despite matching evidence')
                workspace.active = None
            elif kind == 'discovery_adoption':
                body = event['body']; rid = body.get('after')
                if body.get('before') != workspace.active or rid not in workspace._candidates:
                    raise ValueError('invalid adoption lineage')
                candidate = workspace._candidates[rid]
                assessment = body.get('assessment')
                supported = (bool(candidate['fresh']) and not candidate['failed_cases']
                             and all(check['matched'] for check in candidate['checks'].values())
                             and len(candidate['checks']) == len(workspace._evidence))
                expected_assessment = {
                    'representation': rid, 'status': 'supported',
                    'observations_checked': len(workspace._evidence),
                    'fresh_matches': len(candidate['fresh']), 'mismatches': [],
                    'unresolved_execution_cases': 0,
                    'scope': 'retained deterministic observations in this workspace context only'}
                if not supported or assessment != expected_assessment:
                    raise ValueError('adoption lacks derivable supported evidence')
                workspace.active = rid
        if any(aid not in predictions for aid in prediction_attempts):
            raise ValueError('pending prediction attempt; restoration refused')
        if any(value is not None and value[0] not in completed_results for value in observations.values()):
            raise ValueError('pending measurement finalization; restoration refused')
        if any(aid not in observations for aid in measurement_attempts):
            raise ValueError('pending measurement attempt; restoration refused')
        if workspace.observations > workspace.max_observations or workspace.executions > workspace.max_executions:
            raise ValueError('journal exceeds committed discovery budget')

        return workspace

    def tools(self):
        return {'discover_represent': self.represent, 'discover_measure': self.measure,
                'discover_assess': self.assess, 'discover_adopt': self.adopt,
                'discover_catalog': self.catalog}

    def catalog(self, request):
        """Input {}. List permitted observation tools and their public contracts."""
        if request != {}:
            raise ValueError('empty input required')
        return {'tools': {name: tool.__doc__ or '' for name, tool in self.observation_tools.items()},
                'context': _json_copy(self.context), 'active': self.active,
                'remaining': {'observations': self.max_observations-self.observations,
                              'executions': self.max_executions-self.executions}}

    def _candidate(self, request):
        if not isinstance(request, dict) or set(request) != {'representation'}:
            raise ValueError('representation ID required')
        rid = request['representation']
        if not isinstance(rid, str) or rid not in self._candidates:
            raise ValueError('unknown representation in current workspace')
        return rid, self._candidates[rid]

    def represent(self, request):
        """Propose a new executable conjecture, not a verified method.

        Input {name,source,parent,based_on}. parent is null or an existing ID;
        based_on lists observation evidence IDs returned here. Python source
        defines run(case), where case={tool,input}, returning its predicted JSON
        observation. It runs only in the sandbox. Source may change representation
        freely; ALL observed evidence is replayed regardless of based_on selection.
        """
        if not isinstance(request, dict) or set(request) != {'name', 'source', 'parent', 'based_on'}:
            raise ValueError('invalid representation proposal')
        name = _nonempty_string(request['name'], 'name')
        source = _nonempty_string(request['source'], 'source')
        if len(source.encode()) > 8192:
            raise ValueError('source exceeds 8 KiB')
        parent, basis = request['parent'], request['based_on']
        if parent is not None and (not isinstance(parent, str) or parent not in self._candidates):
            raise ValueError('unknown parent')
        if not isinstance(basis, list) or any(not isinstance(e, str) or e not in self._evidence for e in basis):
            raise ValueError('unknown observation evidence')
        payload = {'kind': 'representation', 'body': {'name': name, 'source': source,
            'parent': parent, 'based_on': list(basis), 'context': self.context,
            'observed_before_proposal': list(self._evidence)}, 'dependencies': [],
            'provenance': {'attribution': 'caller-proposed source; no discovery claim inferred'},
            'applicability': {'contract': 'deterministic-tool-predictor-v1', 'context': self.context}}
        rid = self.memory.put(payload)
        if rid not in self._candidates:
            self._candidates[rid] = {'source': source, 'checks': {}, 'fresh': set(), 'failed_cases': {}}
        self.journal.append('discovery_representation', {'representation': rid, 'payload': payload})
        return {'representation': rid, 'status': 'proposed', 'active': self.active}

    def _predict(self, rid, case, mode):
        if self.executions >= self.max_executions:
            self._candidates[rid]['failed_cases'][canonical(case)] = 'execution budget exhausted'
            self.journal.append('discovery_prediction_blocked', {'representation': rid,
                'case': _json_copy(case), 'mode': mode, 'reason': 'execution budget exhausted'})
            return {'status': 'incomplete', 'reason': 'execution budget exhausted'}
        self.executions += 1
        self.journal.append('discovery_prediction_attempt', {'representation': rid,
            'case': case, 'mode': mode, 'number': self.executions})
        try:
            receipt = self.executor({'source': self._candidates[rid]['source'], 'input': _json_copy(case)})
            # Executor owns isolation and bounded diagnostics. Avoid copying arbitrary
            # exception text, which may contain private host data.
            if not isinstance(receipt, dict) or receipt.get('status') != 'completed' or receipt.get('exit_code', 0) != 0:
                raise ValueError('candidate execution failed')
            prediction = _strict_json_output(receipt['output'])
            if len(canonical(prediction).encode()) > 8192:
                raise ValueError('prediction exceeds limit')
        except Exception as error:
            self._candidates[rid]['failed_cases'][canonical(case)] = 'prediction execution failed'
            self.journal.append('discovery_prediction_failure', {'representation': rid, 'type': type(error).__name__})
            return {'status': 'incomplete', 'reason': 'prediction execution failed'}
        self._candidates[rid]['failed_cases'].pop(canonical(case), None)
        receipt_id = self.journal.append('discovery_prediction', {'representation': rid,
            'case': case, 'mode': mode, 'prediction': prediction})
        return {'status': 'predicted', 'prediction': prediction, 'receipt': receipt_id}

    def measure(self, request):
        """Measure a permitted tool, optionally testing an executable conjecture.

        Input {representation,tool,input}; representation may be null to explore
        before proposing an explanation. Identical tool/input requests are cached
        within this deterministic context and never earn new prospective credit.
        A predictor executes and commits its forecast BEFORE a fresh measurement.
        """
        if not isinstance(request, dict) or set(request) != {'representation', 'tool', 'input'}:
            raise ValueError('invalid measurement request')
        tool, rid = request['tool'], request['representation']
        if not isinstance(tool, str) or tool not in self.observation_tools:
            raise ValueError('unknown observation tool')
        if rid is not None:
            self._candidate({'representation': rid})
        case = _json_copy({'tool': tool, 'input': request['input']})
        if len(canonical(case).encode()) > 8192:
            raise ValueError('measurement input exceeds limit')
        key = canonical(case)
        if key in self._cache:
            old = self._cache[key]
            return {'status': 'cached', 'cached': True, 'observation': _json_copy(old['observation']),
                    'evidence': old['evidence']}
        if self.observations >= self.max_observations:
            return {'status': 'incomplete', 'reason': 'observation budget exhausted'}
        forecast = None
        if rid is not None:
            forecast = self._predict(rid, case, 'prospective')
            if forecast['status'] != 'predicted':
                return forecast
        self.observations += 1
        self.journal.append('discovery_measurement_attempt', {'case': case, 'number': self.observations,
            'representation': rid, 'prediction_receipt': None if forecast is None else forecast['receipt']})
        try:
            observed = _json_copy(self.observation_tools[tool](_json_copy(case['input'])))
            if len(canonical(observed).encode()) > 8192:
                raise ValueError('observation exceeds limit')
        except Exception as error:
            self.journal.append('discovery_measurement_failure', {'case': case, 'type': type(error).__name__})
            return {'status': 'incomplete', 'reason': 'measurement failed'}
        eid = self.journal.append('discovery_observation', {'case': case, 'observation': observed})
        entry = {'case': case, 'observation': observed, 'evidence': eid}
        self._cache[key] = entry
        self._evidence[eid] = entry
        status = 'observed'
        if rid is not None:
            matched = canonical(forecast['prediction']) == canonical(observed)
            self._candidates[rid]['checks'][eid] = {'matched': matched, 'receipt': forecast['receipt']}
            if matched:
                self._candidates[rid]['fresh'].add(eid)
                status = 'prediction_matched'
            else:
                status = 'contradicted'
        # Any new evidence makes prior support incomplete until checked against it.
        # Do not leave an active candidate silently valid after an untested probe.
        if self.active is not None:
            check = self._candidates[self.active]['checks'].get(eid)
            if check is None or not check['matched']:
                self.journal.append('discovery_support_suspended', {'representation': self.active, 'evidence': eid})
                self.active = None
        self.journal.append('discovery_measurement_result', {'representation': rid, 'evidence': eid, 'status': status})
        return {'status': status, 'cached': False, 'observation': _json_copy(observed), 'evidence': eid}

    def assess(self, request):
        """Replay ALL retained observations; distinguish old fit from fresh support.

        Input {representation}. supported means exact fit to the finite evidence
        plus at least one successful post-proposal uncached prediction. It is not
        a universal guarantee or independent final benchmark qualification.
        """
        rid, candidate = self._candidate(request)
        mismatches = []
        for eid, entry in self._evidence.items():
            if eid not in candidate['checks']:
                prediction = self._predict(rid, entry['case'], 'retrospective')
                if prediction['status'] != 'predicted':
                    return {**prediction, 'representation': rid}
                candidate['checks'][eid] = {'matched': canonical(prediction['prediction']) == canonical(entry['observation']),
                                           'receipt': prediction['receipt']}
            if not candidate['checks'][eid]['matched']:
                mismatches.append(eid)
        status = ('contradicted' if mismatches else 'incomplete' if candidate['failed_cases']
                  else 'supported' if candidate['fresh']
                  else 'retrospective_fit' if self._evidence else 'untested')
        result = {'representation': rid, 'status': status, 'observations_checked': len(self._evidence),
                  'fresh_matches': len(candidate['fresh']), 'mismatches': mismatches,
                  'unresolved_execution_cases': len(candidate['failed_cases']),
                  'scope': 'retained deterministic observations in this workspace context only'}
        self.journal.append('discovery_assessment', result)
        return result

    def adopt(self, request):
        """Use a conjecture only after finite replay and a fresh matched prediction.

        Input {representation}. Adoption supplies a working representation; the
        independent goal checker still owns acceptance of any final answer.
        """
        result = self.assess(request)
        adopted = result['status'] == 'supported'
        if adopted:
            before = self.active
            self.active = result['representation']
            self.journal.append('discovery_adoption', {'before': before, 'after': self.active,
                                                     'assessment': result})
        return {**result, 'adopted': adopted, 'active': self.active}
