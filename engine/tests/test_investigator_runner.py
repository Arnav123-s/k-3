"""Controls for the isolated compute tool in the investigator runner."""

import json
import hashlib
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from experiments.investigator_transfer import run as runner
from kuttaka_runtime.investigators import investigator_payload
from kuttaka_runtime.store import Journal, digest


COMPUTE_SOURCE = "def run(input):\n    return input\n"
COMPUTE_INPUT = {"x": 2}
OBSERVE_INPUT = {"x": 3}


class FakeModel:
    identity = "test-model"
    last_usage = {"input_tokens": 1, "output_tokens": 1}
    last_response = {"status": 200, "body": "{}"}

    def __init__(self, actions):
        self.actions = list(actions)
        self.contexts = []

    def propose(self, context):
        self.contexts.append(context)
        return self.actions.pop(0)


class RecordingExecutor:
    def __init__(self, jobs, *, status="completed", diagnostics=""):
        self.jobs = jobs
        self.status = status
        self.diagnostics = diagnostics

    def __call__(self, job):
        self.jobs.append(json.loads(json.dumps(job)))
        if self.status != "completed":
            return {"status": self.status, "exit_code": 17, "diagnostics": self.diagnostics,
                    "output": ""}
        return {"status": "completed", "exit_code": 0,
                "output": json.dumps({"result": job["input"]})}


def _actions():
    return [
        {"kind": "experiment", "tool": "compute",
         "input": {"source": COMPUTE_SOURCE, "input": COMPUTE_INPUT},
         "predictions": [{"hypothesis": "local check", "outcome": COMPUTE_INPUT}]},
        {"kind": "experiment", "tool": "observe", "input": OBSERVE_INPUT,
         "predictions": [{"hypothesis": "device reading", "outcome": {"y": 0}}]},
        {"kind": "unresolved", "reason": "tool contract exercised"},
    ]


class InvestigatorComputeToolTests(unittest.TestCase):
    def test_compute_and_observe_are_available_to_direct_and_policy_arms(self):
        for arm in ("ordinary", "policy"):
            with self.subTest(arm=arm), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "run"
                jobs = []
                fake_executor = RecordingExecutor(jobs)
                model = FakeModel(_actions())
                patches = [
                    mock.patch.object(runner, "_model", return_value=model),
                    mock.patch.object(runner, "PythonExecutor", side_effect=lambda **_: fake_executor),
                ]
                policy_contexts = []
                if arm == "policy":
                    bundle_path = Path(directory) / "bundle.json"
                    payload = investigator_payload(
                        "def run(input):\n    return {'state': None, 'action': {'kind': 'unresolved', 'reason': 'unused'}}\n",
                        name="test policy", description="test artifact",
                    )
                    bundle_path.write_text(json.dumps(payload))
                    actions = _actions()

                    def run_policy_step(source, context, execute_policy):
                        policy_contexts.append(context)
                        return {"state": None, "action": actions.pop(0)}, {
                            "status": "completed", "exit_code": 0, "output": "",
                        }

                    patches.append(mock.patch(
                        "kuttaka_runtime.investigators._run_policy", side_effect=run_policy_step
                    ))
                else:
                    bundle_path = None

                with patches[0], patches[1]:
                    if arm == "policy":
                        with patches[2]:
                            runner.run_one(output, "source_affine", 903, arm, bundle_path)
                    else:
                        runner.run_one(output, "source_affine", 903, arm)

                report = json.loads((output / "result.json").read_text())
                self.assertEqual(report["result"]["status"], "unresolved")
                self.assertEqual(report["costs"], {"observations": 1, "executions": 1})
                # The executor receives only the requested program/input. It never
                # receives private check inputs or expected outputs through compute.
                self.assertEqual(jobs, [{"source": COMPUTE_SOURCE, "input": COMPUTE_INPUT}])
                recorded_contexts = policy_contexts if arm == "policy" else model.contexts
                self.assertIn("observe", recorded_contexts[0]["tools"])
                self.assertIn("compute", recorded_contexts[0]["tools"])
                self.assertNotIn("expected", json.dumps(recorded_contexts))
                self.assertEqual(recorded_contexts[1]["history"][0]["observation"],
                                 {"status": "completed", "result": COMPUTE_INPUT})
                self.assertEqual(recorded_contexts[2]["history"][1]["observation"],
                                 runner.make_task("source_affine", 903).observe(OBSERVE_INPUT))

    def test_invalid_compute_request_is_rejected_without_spending_execution(self):
        actions = _actions()
        actions[0]["input"]["extra"] = "not allowed"
        model = FakeModel([actions[0], {"kind": "unresolved", "reason": "invalid request checked"}])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            jobs = []
            fake_executor = RecordingExecutor(jobs)
            with mock.patch.object(runner, "_model", return_value=model), \
                    mock.patch.object(runner, "PythonExecutor", side_effect=lambda **_: fake_executor):
                runner.run_one(output, "source_affine", 903, "ordinary")

            report = json.loads((output / "result.json").read_text())
            self.assertEqual(jobs, [])
            self.assertEqual(report["costs"], {"observations": 0, "executions": 0})
            observation = model.contexts[1]["history"][0]["observation"]
            self.assertEqual(observation, {"status": "invalid_compute_request"})

    def test_executor_failure_diagnostic_is_bounded_and_execution_is_charged(self):
        model = FakeModel([
            {"kind": "experiment", "tool": "compute",
             "input": {"source": COMPUTE_SOURCE, "input": COMPUTE_INPUT},
             "predictions": [{"hypothesis": "local check", "outcome": COMPUTE_INPUT}]},
            {"kind": "unresolved", "reason": "failure path checked"},
        ])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            jobs = []
            fake_executor = RecordingExecutor(jobs, status="timeout", diagnostics="x" * 12000)
            with mock.patch.object(runner, "_model", return_value=model), \
                    mock.patch.object(runner, "PythonExecutor", side_effect=lambda **_: fake_executor):
                runner.run_one(output, "source_affine", 903, "ordinary")

            report = json.loads((output / "result.json").read_text())
            events = Journal.read(output / "events.jsonl")
            receipt = next(e for e in events if e["kind"] == "investigator_observation")
            observation = receipt["body"]["observation"]
            self.assertEqual(report["costs"], {"observations": 0, "executions": 1})
            self.assertEqual(observation["status"], "timeout")
            self.assertLessEqual(len(observation["diagnostics"].encode()), 5000)
            self.assertEqual(len(jobs), 1)


class CampaignGateTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {'name': 'fixture', 'description': 'test only',
                          'source': COMPUTE_SOURCE, 'dependencies': []}
        self.review = {'source_sha256': hashlib.sha256(COMPUTE_SOURCE.encode()).hexdigest(),
                       'candidate_sha256': digest(self.candidate),
                       'substantive_review_passed': True, 'target_task_exposure': False,
                       'dependencies': [], 'review': 'Test gate fixture; no capability evidence.'}

    def test_targets_require_explicit_review_bound_to_complete_candidate(self):
        with self.assertRaises(ValueError):
            runner._review_candidate(self.candidate, None)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'review.json'
            path.write_text(json.dumps(self.review))
            self.assertEqual(runner._review_candidate(self.candidate, path), self.review)
            for change in ({'source': COMPUTE_SOURCE + '\n'},
                           {'description': 'changed after review'}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    runner._review_candidate(dict(self.candidate, **change), path)

    def test_failed_or_exposed_review_cannot_authorize_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'review.json'
            for change in ({'substantive_review_passed': False},
                           {'substantive_review_passed': 1},
                           {'target_task_exposure': True}, {'dependencies': ['solver']},
                           {'review': ''}):
                path.write_text(json.dumps(dict(self.review, **change)))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    runner._review_candidate(self.candidate, path)

    def test_transfer_rejects_qualification_from_other_profile_or_bundle(self):
        bundle = investigator_payload(COMPUTE_SOURCE, name='fixture', description='test only')
        manifest = {'policy_source_sha256': self.review['source_sha256'],
                    'policy_sha256': digest(bundle), 'model': runner._model().identity,
                    'budget': asdict(runner.BUDGET), 'limits': runner.LIMITS}
        runs = [{'report': {'family': family, 'seed': seed, 'arm': 'qualification',
                             'result': {'status': 'checked'}}}
                for family, seed in runner.TASK_PLAN if family.startswith('source')]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate = root / 'candidate.json'
            candidate.write_text(json.dumps(self.candidate))
            review = root / 'review.json'
            review.write_text(json.dumps(self.review))
            qualification = root / 'summary.json'
            changes = [
                {'model': dict(manifest['model'], model='different-model')},
                {'budget': dict(manifest['budget'], model_calls=11)},
                {'limits': dict(manifest['limits'], observations=9)},
                {'policy_sha256': '0' * 64},
            ]
            for index, change in enumerate(changes):
                qualification.write_text(json.dumps({'campaign': dict(manifest, **change), 'runs': runs}))
                with self.subTest(change=change), mock.patch.object(runner, 'invoke') as invoke:
                    with self.assertRaises(ValueError):
                        runner.campaign(root / str(index), candidate, 1, 'transfer', qualification, review)
                    invoke.assert_not_called()

    def test_review_does_not_bypass_source_qualification_and_success_runs_full_plan(self):
        for status, expected_calls in (('failed', 2), ('checked', 22)):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                candidate = root / 'candidate.json'
                candidate.write_text(json.dumps(self.candidate))
                review = root / 'review.json'
                review.write_text(json.dumps(self.review))

                def fake_invoke(_root, family, seed, arm):
                    return {'report': {'family': family, 'seed': seed, 'arm': arm,
                                       'result': {'status': status}}}

                with mock.patch.object(runner, 'invoke', side_effect=fake_invoke) as invoke:
                    runner.campaign(root / 'campaign', candidate, 1, 'all', review=review)
                self.assertEqual(invoke.call_count, expected_calls)
                summary = json.loads((root / 'campaign/summary.json').read_text())
                self.assertEqual(len(summary['runs']), expected_calls)
                self.assertEqual('skipped_transfer' in summary, status == 'failed')
                self.assertEqual(summary['campaign']['candidate_review_sha256'], digest(self.review))


if __name__ == "__main__":
    unittest.main()
