"""Bounded isolated Python execution through the managed local Docker daemon.

Candidate output is untrusted and is never a qualification certificate. No host
workspace or application credentials are mounted or forwarded into the container.
"""

import json
import math
import os
import selectors
import shutil
import subprocess
import time
import uuid

IMAGE = 'python@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159'
INPUT_LIMIT = 262144
# Until 2026-11-01 a job without libraries was refused above this size, before the method ran (a
# compatibility leftover from the pre-library runner, which reads stdin with no limit of its own). A task
# whose input is large (a 100000-letter string) then failed for every method. Kept for reference only.
LEGACY_INPUT_LIMIT = 32768
MAX_LIBRARIES = 16
RUNNER = '''import contextlib,json,sys
job=json.load(sys.stdin)
libraries=job.get("libraries",{})
loaded={}
dependency_calls=[]
def use(artifact):
    if artifact not in libraries:
        raise KeyError("undeclared method dependency "+str(artifact))
    if artifact not in loaded:
        space={"__name__":"__method__","use":use}
        exec(compile(libraries[artifact],"<"+artifact[:12]+">","exec"),space)
        loaded[artifact]=space["run"]
    method=loaded[artifact]
    def invoke(value):
        # Cooperative trace: records calls through this wrapper, not tamper-proof evidence.
        dependency_calls.append(artifact)
        return method(value)
    return invoke
namespace={"__name__":"__candidate__"}
if "libraries" in job:
    namespace["use"]=use
with contextlib.redirect_stdout(sys.stderr):
    exec(compile(job["source"],"<candidate>","exec"),namespace)
    answer=namespace["run"](job["input"])
sys.stdout.write(json.dumps({"result":answer,"dependency_calls":dependency_calls},allow_nan=False,separators=(",",":")))
sys.stdout.flush()
'''
# Opt-in (KUTTAKA-C3): the same runner, but the trace lists each called artifact once, in first-call
# order. A composite that calls a stored method thousands of times would otherwise overflow the
# 64 KiB receipt limit and turn a correct method into an infrastructure error. C1/C2 keep RUNNER.
RUNNER_DISTINCT = RUNNER.replace(
    '        dependency_calls.append(artifact)\n',
    '        if artifact not in dependency_calls:\n            dependency_calls.append(artifact)\n')
assert RUNNER_DISTINCT != RUNNER
TRACES = {'all': RUNNER, 'distinct': RUNNER_DISTINCT}


def _docker_base():
    return ['docker', '--host=unix:///var/run/docker.sock']


def _docker_env():
    env = dict(os.environ)
    for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
        env.pop(key, None)
    return env


def _bounded_docker_command(command, env, *, timeout=2, output_limit=8192):
    """Run one local Docker diagnostic/cleanup command with bounded I/O and time."""
    started = time.monotonic()
    streams = {'stdout': bytearray(), 'stderr': bytearray()}
    proc = None
    status = 'completed'
    selector = selectors.DefaultSelector()
    try:
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env, bufsize=0)
        for stream, label in ((proc.stdout, 'stdout'), (proc.stderr, 'stderr')):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        while selector.get_map():
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                status = 'timeout'
                break
            for key, _ in selector.select(min(remaining, 0.1)):
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                total = len(streams['stdout']) + len(streams['stderr'])
                room = output_limit - total
                if len(chunk) > room:
                    streams[key.data].extend(chunk[:max(0, room)])
                    status = 'output_limit'
                    break
                streams[key.data].extend(chunk)
            if status != 'completed':
                break
        if status == 'completed':
            try:
                proc.wait(timeout=max(0.001, timeout - (time.monotonic() - started)))
            except subprocess.TimeoutExpired:
                status = 'timeout'
    except OSError as error:
        status = 'error'
        error_type = type(error).__name__
    finally:
        selector.close()
        if proc is not None:
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
            try:
                remaining = max(0, started + timeout - time.monotonic())
                proc.wait(timeout=remaining)
            except (OSError, subprocess.TimeoutExpired):
                if status == 'completed':
                    status = 'timeout'
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream and not stream.closed:
                    try:
                        stream.close()
                    except OSError:
                        pass
    result = {'status': status, 'return_code': proc.returncode if proc is not None else None,
              'timed_out': status == 'timeout',
              'elapsed_seconds': round(time.monotonic() - started, 6),
              'stdout': streams['stdout'].decode('utf-8', errors='replace'),
              'stderr': streams['stderr'].decode('utf-8', errors='replace')}
    if status == 'error':
        result['error_type'] = error_type
    return result


def _safe_docker_command(command, env, *, timeout=2, output_limit=8192):
    """Keep diagnostic/cleanup failures from replacing the candidate result."""
    started = time.monotonic()
    try:
        return _bounded_docker_command(command, env, timeout=timeout, output_limit=output_limit)
    except Exception as error:
        return {'status': 'error', 'return_code': None, 'timed_out': False,
                'elapsed_seconds': round(time.monotonic() - started, 6),
                'stdout': '', 'stderr': '', 'error_type': type(error).__name__}


def preflight():
    """Check the local daemon and pinned image without pulling or exposing output."""
    if shutil.which('docker') is None:
        return {'ready': False, 'reason': 'docker_client_missing', 'image': IMAGE}
    env = _docker_env()
    try:
        daemon = subprocess.run(_docker_base() + ['info', '--format', '{{.ServerVersion}}'],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, env=env, timeout=5, check=False)
    except subprocess.TimeoutExpired:
        return {'ready': False, 'reason': 'docker_daemon_timeout', 'image': IMAGE}
    except OSError:
        return {'ready': False, 'reason': 'docker_daemon_unavailable', 'image': IMAGE}
    if daemon.returncode != 0:
        return {'ready': False, 'reason': 'docker_daemon_unavailable', 'image': IMAGE}
    try:
        image = subprocess.run(_docker_base() + ['image', 'inspect', '--format', '{{.Id}}', IMAGE],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, env=env, timeout=5, check=False)
    except subprocess.TimeoutExpired:
        return {'ready': False, 'reason': 'image_inspect_timeout', 'image': IMAGE}
    except OSError:
        return {'ready': False, 'reason': 'image_unavailable', 'image': IMAGE}
    if image.returncode != 0:
        return {'ready': False, 'reason': 'pinned_image_unavailable', 'image': IMAGE}
    return {'ready': True, 'reason': 'ready', 'image': IMAGE}


class PythonExecutor:
    """Run ``source`` defining ``run(input)`` in a fresh constrained container.

    The job is sent on Docker stdin, not the process command line. ``output`` is
    a compact JSON object with exactly the ``result`` key on successful runs;
    ordinary candidate prints are returned separately in bounded diagnostics.
    ``dependency_calls`` is a cooperative trace emitted by the runner: it records
    calls through ``use(id)`` wrappers, but candidate code shares the process and
    can tamper with that trace. It is not tamper-proof or causal evidence. A
    completed result still requires independent host validation.
    """

    LONG_RUN_LIMIT = 86400  # seconds; only for an explicit long run (deploying a checked method)
    MEMORY_LIMITS = ('256m', '512m', '1g', '2g', '4g')

    def __init__(self, *, timeout=20, output_limit=262144, image=IMAGE, trace='all', long_run=False,
                 memory='256m'):
        if trace not in TRACES:
            raise ValueError('trace must be all or distinct')
        self.trace = trace
        try:
            finite_timeout = type(timeout) in (int, float) and math.isfinite(timeout)
        except OverflowError:
            finite_timeout = False
        ceiling = self.LONG_RUN_LIMIT if long_run is True else 300
        if memory not in self.MEMORY_LIMITS:
            raise ValueError('memory must be one of ' + ', '.join(self.MEMORY_LIMITS))
        self.memory = memory
        if (not finite_timeout or not 0 < timeout <= ceiling or type(output_limit) is not int
                or not 0 < output_limit <= 1048576):
            raise ValueError('execution limits out of range')
        if not isinstance(image, str) or not image:
            raise ValueError('image reference required')
        self.timeout, self.output_limit, self.image = timeout, output_limit, image
        self.env = _docker_env()
        self.docker = _docker_base()

    def _command(self, name):
        return self.docker + ['run', '--rm', '--interactive', '--pull=never', '--name', name,
            '--network=none', '--read-only', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '--pids-limit=32',
            f'--memory={getattr(self, "memory", "256m")}', f'--memory-swap={getattr(self, "memory", "256m")}', '--cpus=1',
            '--user=65534:65534', '--tmpfs=/tmp:rw,noexec,nosuid,size=16m',
            '--env=HTTP_PROXY=', '--env=HTTPS_PROXY=', '--env=ALL_PROXY=', '--env=NO_PROXY=',
            '--env=http_proxy=', '--env=https_proxy=', '--env=all_proxy=', '--env=no_proxy=',
            self.image, 'python', '-I', '-c', TRACES[getattr(self, 'trace', 'all')]]

    @staticmethod
    def _validate_job(job):
        if (not isinstance(job, dict) or not {'source', 'input'} <= set(job)
                or set(job) - {'source', 'input', 'libraries'}
                or not isinstance(job['source'], str)):
            raise ValueError('source and input required; libraries are optional')
        libraries = job.get('libraries', {})
        if not isinstance(libraries, dict) or len(libraries) > MAX_LIBRARIES:
            raise ValueError('libraries must be a map of at most 16 artifact IDs to source')
        for artifact, source in libraries.items():
            if (not isinstance(artifact, str) or len(artifact) != 64
                    or any(ch not in '0123456789abcdef' for ch in artifact)):
                raise ValueError('library keys must be lowercase SHA-256 artifact IDs')
            if not isinstance(source, str) or not source.strip():
                raise ValueError('library sources must be nonempty Python source strings')
        return libraries

    @staticmethod
    def _parse_runner_output(output, *, libraries_present):
        def unique_keys(pairs):
            decoded = {}
            for key, value in pairs:
                if key in decoded:
                    raise ValueError('duplicate result key')
                decoded[key] = value
            return decoded

        report = json.loads(output, object_pairs_hook=unique_keys)
        if not isinstance(report, dict) or 'result' not in report:
            raise ValueError('runner output must contain a result')
        if set(report) == {'result'} and not libraries_present:
            calls = []  # Compatibility with the pre-library runner.
        elif set(report) == {'result', 'dependency_calls'}:
            calls = report['dependency_calls']
            if (not isinstance(calls, list)
                    or any(not isinstance(item, str) for item in calls)):
                raise ValueError('runner dependency_calls must be a string list')
        else:
            raise ValueError('runner output must contain only result and dependency_calls')
        clean_output = json.dumps({'result': report['result']}, ensure_ascii=False,
                                  allow_nan=False, separators=(',', ':'))
        return clean_output, calls

    def __call__(self, job):
        libraries = self._validate_job(job)
        try:
            payload_job = {'source': job['source'], 'input': job['input']}
            if 'libraries' in job:
                payload_job['libraries'] = libraries
            payload = json.dumps(payload_job, allow_nan=False, separators=(',', ':')).encode('utf-8')
        except (TypeError, ValueError, UnicodeEncodeError) as error:
            raise ValueError('source and input must be JSON-compatible') from error
        size_limit = INPUT_LIMIT   # one limit for every job, with or without libraries
        if len(payload) > size_limit:
            raise ValueError(f'program input and libraries exceed {size_limit // 1024} KiB')

        name = 'kuttaka-exec-' + uuid.uuid4().hex
        proc = subprocess.Popen(self._command(name), stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=self.env, bufsize=0)
        streams = {'stdout': bytearray(), 'stderr': bytearray()}
        status, sent = 'completed', 0
        started = time.monotonic()
        stdin_complete_seconds = None
        first_output_seconds = None
        process_exit_seconds = None
        process_exit_forced = False
        inspect_result = None
        cleanup_result = None
        selector = selectors.DefaultSelector()
        try:
            os.set_blocking(proc.stdin.fileno(), False)
            os.set_blocking(proc.stdout.fileno(), False)
            os.set_blocking(proc.stderr.fileno(), False)
            selector.register(proc.stdin, selectors.EVENT_WRITE, 'stdin')
            selector.register(proc.stdout, selectors.EVENT_READ, 'stdout')
            selector.register(proc.stderr, selectors.EVENT_READ, 'stderr')
            while selector.get_map():
                remaining = self.timeout - (time.monotonic() - started)
                if remaining <= 0:
                    status = 'timeout'
                    break
                events = selector.select(min(remaining, 0.1))
                for key, mask in events:
                    if key.data == 'stdin':
                        try:
                            count = os.write(proc.stdin.fileno(), payload[sent:sent + 8192])
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            selector.unregister(proc.stdin)
                            proc.stdin.close()
                            continue
                        sent += count
                        if sent == len(payload):
                            selector.unregister(proc.stdin)
                            proc.stdin.close()
                            stdin_complete_seconds = time.monotonic() - started
                        continue
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    if first_output_seconds is None:
                        first_output_seconds = time.monotonic() - started
                    total = len(streams['stdout']) + len(streams['stderr'])
                    room = self.output_limit - total
                    if len(chunk) > room:
                        streams[key.data].extend(chunk[:max(0, room)])
                        status = 'output_limit'
                        break
                    streams[key.data].extend(chunk)
                if status != 'completed':
                    break
            if status == 'completed':
                try:
                    proc.wait(timeout=max(0.01, self.timeout - (time.monotonic() - started)))
                    process_exit_seconds = time.monotonic() - started
                except subprocess.TimeoutExpired:
                    status = 'timeout'
        finally:
            selector.close()
            # Stop a timed-out Docker client before cleanup. Removing the exact
            # generated name while `docker run` is still creating it can race:
            # rm may observe no container, then the create finishes afterward.
            if proc.poll() is None and status in ('timeout', 'output_limit'):
                process_exit_forced = True
                try:
                    proc.kill()
                except OSError:
                    pass
            try:
                proc.wait(timeout=2)
                if process_exit_seconds is None:
                    process_exit_seconds = time.monotonic() - started
            except subprocess.TimeoutExpired:
                process_exit_forced = True
                try:
                    proc.kill()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=2)
                    if process_exit_seconds is None:
                        process_exit_seconds = time.monotonic() - started
                except (OSError, subprocess.TimeoutExpired):
                    pass
            if status == 'timeout':
                # Only ask Docker for the container's .State. Never inspect its
                # config, environment, mounts, command, or source payload.
                inspect_result = _safe_docker_command(
                    self.docker + ['inspect', '--format', '{{json .State}}', name],
                    self.env, timeout=2, output_limit=8192)
            cleanup_result = _safe_docker_command(
                self.docker + ['rm', '-f', name], self.env, timeout=10, output_limit=8192)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream and not stream.closed:
                    try:
                        stream.close()
                    except OSError:
                        pass

        output = streams['stdout'].decode('utf-8', errors='replace')
        diagnostics = streams['stderr'].decode('utf-8', errors='replace')
        dependency_calls = []
        if status == 'completed' and proc.returncode == 0:
            try:
                output, dependency_calls = self._parse_runner_output(
                    output, libraries_present=bool(libraries))
            except (json.JSONDecodeError, TypeError, ValueError):
                status = 'invalid_output'
        timings = {'stdin_complete_seconds': _round_time(stdin_complete_seconds),
                   'first_output_seconds': _round_time(first_output_seconds),
                   'process_exit_seconds': _round_time(process_exit_seconds),
                   'process_exit_forced': process_exit_forced,
                   'total_wall_seconds': round(time.monotonic() - started, 6)}
        return {'status': status, 'exit_code': proc.returncode,
                'output': output, 'diagnostics': diagnostics,
                'dependency_calls': dependency_calls,
                'container_name': name, 'timings': timings,
                'inspect_state': inspect_result, 'cleanup': cleanup_result}


def _round_time(value):
    return None if value is None else round(value, 6)
