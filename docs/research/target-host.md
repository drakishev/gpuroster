# Target-host NVML and worker validation

Date: 2026-10-01. Runtime baseline: `9810654` (0.5.1), measurement harness
`a46f92c`. Read-only observation on one Linux host with eight NVIDIA H200 GPUs,
driver 590.48.01, Python 3.12.13, and `nvidia-ml-py==13.615.71`. All eight devices
had MIG disabled, both current and pending. No GPU workloads or configuration
changes were made by this experiment.

The [validation guide](../validation/hardware.md) defines the hypothesis,
acceptance criteria, privacy policy, and limitations. The command uses the
production `NVMLWorker`, including process attribution, and compares static
properties with the production CLI provider. It runs independently of the web
server and SQLite. No original environment, database, or service is modified.

```bash
python -m benchmarks.hardware_validation --duration 900
```

## Observations

All **900 one-second cycles** completed with zero invalid samples, identity
changes, missed deadlines, or unavailable metrics. Both backends agreed on all
eight UUIDs and memory totals within one MiB of CLI rounding. Up to 13 process
records were observed; none had an unknown user. This checks structure and OS
visibility, not attribution accuracy against a controlled GPU workload.

Cold startup took 165.373 ms. The histogram's p95 upper bound was 10 ms; the
observed maximum was 6.612 ms. Timing includes IPC and OS process attribution,
so it is not directly comparable with the earlier raw NVML/IPC microbenchmark.
Other host workloads and a short synthetic retention experiment ran during
observation; these timings are not isolated capacity measurements.

| Resource | Parent start → end | Native worker start → end |
| --- | --- | --- |
| RSS (bytes) | 21,966,848 → 22,986,752 | 43,966,464 → 44,257,280 |
| USS (bytes) | 13,185,024 → 13,250,560 | 30,597,120 → 30,597,120 |
| Threads | 1 → 1 | 2 → 2 |
| File descriptors | 9 → 9 | 42 → 42 |
| CPU seconds during observation | 1.52 | 2.60 |

There were 31 resource samples, including endpoints, over 900 seconds. Threads
and descriptors remained at those counts at every sample. During the second
half, the parent USS band was **0.023 MiB**, with endpoint growth of
**0.003 MiB/minute**. Worker USS was flat for the entire run. Small RSS changes
are recorded separately; the experiment does not equate RSS with retained
private allocations. Sampling can miss short-lived resource peaks.

The original benchmark worker stopped cleanly. All **ten forced child-exit
recovery cycles** succeeded. Parent descriptors were six both before and after
recovery, with one thread throughout. Parent USS changed from 14,217,216 to
14,299,136 bytes during recovery. This tests termination and restart of the
benchmark's own child, not recovery from an actual GPU reset or driver failure.

## Decision and coverage

The functional and resource criteria passed. Keep the current isolated NVML
implementation; this workload provides no evidence requiring a memory fix.
The [separate identity-churn experiment](identity-churn.md) also found bounded
rolling-history state and complete expiry under synthetic device replacement.

| Configuration | Evidence |
| --- | --- |
| H200 × 8, driver 590.48.01, MIG disabled, current account | Real 15-minute observation and ten worker recoveries passed |
| Unsupported GPU metrics / denied process access | Synthetic regressions only |
| MIG enabled or per-instance monitoring | Not tested; per-instance monitoring is not implemented |
| Other drivers/devices, containers, service-account sandbox | Not validated by this run |

Fifteen minutes on one host does not establish long-term native-driver leak
freedom or broad NVIDIA compatibility. The command does not validate HTTP,
sessions, database retention, or production deployment permissions. Reports
contain aggregates only, excluding device/process identities and host details.

Raw results: [target-host-2026-10-01.json](../../benchmarks/results/target-host-2026-10-01.json).
