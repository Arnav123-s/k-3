# Judge quickstart

K^3 is a model wrapper that proposes checks, runs generated Python inside a pinned Docker sandbox, and repairs candidates when checks fail. The model weights stay the same.

## Verify locally

Use Python 3.11 or later and a running Docker daemon. From the checkout:

```bash
cd engine
docker pull python@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
python -c 'from kuttaka_runtime.execution import preflight; print(preflight())'
python -m unittest tests.test_krama
ASTRA_DOCKER_TESTS=1 python -m unittest discover -s tests -t .
```

The full suite previously passed 1,454 tests with Docker on at engine pin 89deb236a4e0b7e199dd4750523e83d9ed739e3c. The test suite uses scripted model fixtures and does not require an API key.

## Run a live task

Set NVIDIA_API_KEY in your environment before invoking this command. Obtain your own endpoint credentials. Model access and charges depend on your account.

```bash
python -m kuttaka_runtime.solve 'Define run(input) to return the sum of integers from 1 through input.' --protocol openai --base-url https://integrate.api.nvidia.com/v1 --key-env NVIDIA_API_KEY --model meta/muse-glimmer-30b --rounds 1
```

Inspect the result and the checks that support it. See engine/README.md for local and alternative model options. The film's W4-o result used the earlier pin 8ce0951. The packaged runtime uses the later, validated pin above.
