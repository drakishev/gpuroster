# Read-only target-host validation

This optional check needs Linux, readable NVIDIA devices, `nvidia-smi`, and the
locked runtime dependencies. Ordinary CI uses fixtures and does not require a
GPU. Run from a reviewed checkout in a separate environment, under the intended
service account where practical. It does not start the dashboard, open a port,
touch SQLite or login logs, launch GPU workloads, or change driver/MIG settings.

```bash
python -m benchmarks.hardware_validation --duration 900
```

Use `--duration 5 --sample-interval 1 --recovery-cycles 2` for an initial smoke
check. Default steady-state sampling is once per second, with resource samples
every 30 seconds. The normal collector default is less frequent. Each native
worker sample and CLI command retains the application's three-second deadline;
process attribution also reads OS process metadata and has no separate deadline.

The command checks GPU metric ranges, process-to-GPU mapping, identity stability,
and UUID/memory-total agreement between NVML and the CLI. Only static properties
are compared across backends: utilization, power, and process state can change
between sequential reads. Total-memory comparisons allow one MiB of CLI
rounding; `memory_totals_compared` identifies coverage when values are unavailable.
Unsupported metrics remain null and are counted, rather than replaced with zero.

Driver version, device/MIG counts, null/unknown counts, timing histograms, and
parent/worker resources are reported. Device UUIDs/names, process IDs, usernames,
commands, addresses, and hostnames are never emitted. Failures use a fixed safe
error instead of driver exception text. A failure exits nonzero. This report is
aggregate validation evidence, not a dump of the dashboard API.

After measurement, the command closes its worker, then tests ten forced worker
exits and subsequent recovery. It terminates only its own NVML child processes.
It does not terminate GPU application processes, reset a GPU, or simulate an
actual device/driver failure. Parent descriptors before and after recovery help
detect resource accumulation. SIGINT also closes the benchmark's worker.

## Acceptance and interpretation

Hypothesis: the persistent native worker preserves valid metrics over a longer
run, keeps a bounded memory/resource footprint, and recovers from child exits.
For a 15-minute run, require valid samples, stable device identities, static
backend agreement, all requested recovery cycles, and a stopped final worker.
Require no accumulating threads/descriptors and, over the second half, a USS
band below 10 MiB and endpoint growth below 1 MiB/minute for parent and worker.
These practical thresholds are workload criteria, not proof of leak freedom.

`checks_passed` covers functional validation. Review `memory_window`, the raw
resource samples, recovery counts, unavailable metrics, and unknown process
users separately. Short smoke runs do not establish a memory plateau. Histogram
percentiles are upper bounds. The final histogram bucket is overflow. CPU times
are cumulative per process; subtract first from last. The parent includes the
benchmark's counters/resource samples; the child is the actual NVML worker.

The command tests the current user's visibility, not a systemd sandbox or every
user's permissions. It checks structural process attribution, not its accuracy
against a controlled GPU workload. Other applications can change GPU state
during observation. Session, HTTP, storage, container, hot-plug, and long-term
driver failure coverage require separate validation.

## MIG coverage

The current collector enumerates physical GPUs. It does not enumerate MIG
instances or promise per-instance utilization/accounting. NVIDIA documents that
device utilization can be unsupported with MIG, and physical-device process
queries return aggregate data only with appropriate permissions. See the
[NVML device queries](https://docs.nvidia.com/deploy/nvml-api/latest/api/group__nvmlDeviceQueries.html)
and [MIG mode queries](https://docs.nvidia.com/deploy/nvml-api/latest/api/group__nvmlMultiInstanceGPU.html).

Check current and pending MIG counts in the report. Disabled MIG provides no
evidence about enabled-MIG behavior. Synthetic regressions cover null metrics
and denied process access, but cannot establish hardware compatibility. Do not
enable MIG as part of this validation; use a separately provisioned host for
that test. No broad driver or MIG support claim follows from one passing host.
