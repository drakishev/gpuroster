# GPU collection experiment

Date: 2026-10-01. Status: measured; worker isolation is being validated before integration.

Hypothesis: NVML can materially reduce collection latency and CPU overhead while preserving the requested GPU identity, utilization, memory, temperature, power, and compute-process information.

Success criteria: lower steady-state latency/CPU than two CLI queries, unavailable fields remain distinguishable from zero, and a stalled backend must not stall API delivery or escape the collection deadline.

Run `python benchmarks/gpu_backends.py` in an isolated environment containing `nvidia-ml-py==13.615.71`. The script uses read-only queries and emits aggregate results only. It runs 30 samples after one warm-up in a fresh process for each backend and device count. CLI cases issue two queries per sample; NVML initializes once and reads equivalent fields per device. Measurements here used Python 3.12 on one Linux host, selecting 1/4/8 devices from the same host. This does not represent three different machines or driver compatibility coverage.

| GPUs | CLI median / p95 (ms) | NVML median / p95 (ms) | CLI CPU ms/sample | NVML CPU ms/sample |
| --- | --- | --- | --- | --- |
| 1 | 281.860 / 456.509 | 0.086 / 0.097 | 147.324 | 0.089 |
| 4 | 284.603 / 336.512 | 0.200 / 0.216 | 152.144 | 0.205 |
| 8 | 281.141 / 342.680 | 0.583 / 0.599 | 160.786 | 0.587 |

NVML initialization took 92–119 ms. Peak parent RSS was about 37 MiB with NVML versus 15 MiB for the CLI harness, whose child peak RSS was about 22–23 MiB. Peak RSS includes imports/initialization and is not incremental application memory. CPU includes CLI child user/system time and excludes NVML initialization/shutdown. Fixed-order, rapidly repeated samples can favor warm driver caches; these numbers are observational rather than a production capacity promise. No queried fields were unsupported during these runs. Process identities and commands were never emitted.

The latency hypothesis is supported. An in-process NVML call cannot be interrupted with the existing subprocess deadline, so the next prototype puts it in a persistent worker process with bounded parent waits, termination, and restart. Keep the CLI collector as a fallback when NVML cannot initialize. A runtime driver failure should remain visible rather than silently changing backends.

References: [NVIDIA NVML API](https://docs.nvidia.com/deploy/nvml-api/nvml-api-reference.html), [NVIDIA CLI compatibility and identity guidance](https://docs.nvidia.com/deploy/nvidia-smi/index.html), [psutil CPU sampling semantics](https://psutil.io/api/), [util-linux ISO session timestamps](https://github.com/util-linux/util-linux/blob/master/login-utils/last.1.adoc).
