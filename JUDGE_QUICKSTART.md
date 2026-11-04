# Judge quickstart

Everything below starts from the public **k-3** repository. Use Linux or WSL with Python 3.11+, Bash, and a running Docker daemon.

## 1. Check all three engines

```bash
git clone https://github.com/Arnav123-s/k-3.git
cd k-3
docker pull python@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
bash smoke-engines.sh
```

Expect three pass lines, one each for the final tested, speed-focused, and best-scoring editions. The smoke check opens the solver and server help and executes a small Python task in each edition's sandbox. It needs no model key. If Docker preflight fails, run `docker info` from the same shell and confirm the pinned image pull succeeded.

## 2. Run the full tests on the default edition

From the repository root:

```bash
cd engine
KUTTAKA_DOCKER_TESTS=1 python3 -m unittest discover -s tests -t .
```

The packaged suite previously passed 1,248 tests with Docker enabled. It uses scripted model fixtures and needs no API key. See the [engine guide](engine/README.md) for its entry points and implementation.

## 3. Give an engine a live task

From the repository root, set `edition` to `engine` for the default, `engine-speed` for W4-r, or `engine-best` for W4-o. Set `NVIDIA_API_KEY` privately in your shell before running this example. It requires access to the named model. Model availability and charges depend on your account.

```bash
edition=engine
(
  cd "$edition"
  python3 -m kuttaka_runtime.solve 'Write run(n) to return the sum of the integers from 1 through n.' \
    --example '{"input":4,"expected":10}' \
    --protocol openai --base-url https://integrate.api.nvidia.com/v1 \
    --key-env NVIDIA_API_KEY --model meta/muse-glimmer-30b --rounds 1 \
    --workspace "$PWD/.judge-workspace"
  python3 -m kuttaka_runtime library --workspace "$PWD/.judge-workspace"
)
```

The solver can ask you to review proposed checks. A checked method comes with its support. An unresolved result explains what prevented verification. The library command shows methods retained in that edition's workspace.

For a local Ollama model, use `--protocol ollama --base-url http://localhost:11434 --model YOUR_LOCAL_MODEL` in place of the four endpoint and credential options above. Install and start that model in Ollama first. For other options, run `python3 -m kuttaka_runtime.solve --help` inside the chosen edition.

For the benchmark comparison, see the [recorded evidence and limits](EVIDENCE.md).
