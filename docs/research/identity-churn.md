# Rolling-history identity churn

Date: 2026-10-01. Runtime baseline: `9810654` (0.5.1). Synthetic data only.

Hypothesis: rolling-history storage depends on identities observed within its
retention horizon, rather than all identities ever observed. Test this before
changing the runtime: prior memory experiments used a fixed device set.

```bash
python -m benchmarks.identity_churn
```

Replace all eight GPU UUIDs on every simulated three-second tick, keeping the
eight display indices. Run 1,200 ticks: ten default 120-point history windows,
9,600 distinct identities, and one hour of simulated collection. Advance time
past the expiry horizon with no devices. This is accelerated model time, not a
one-hour soak. No hardware, database, HTTP server, or actual users are involved.

Before measuring, require at most `8 * (120 + 1) = 968` retained identities,
matching series/device/last-seen key sets, smoothing state only for current
devices, and zero retained state after expiry. The extra tick reflects the
existing strictly-greater-than expiry boundary. Each series also has a fixed
120-point limit. A smaller 300-tick regression checks those invariants in CI.

Trace Python allocations and collect garbage at each window boundary. Samples
include benchmark bookkeeping and cannot be interpreted as total process RSS.
The second half should stay within a 1 MiB traced-allocation band; inspect actual
counts as well, since allocators can retain freed memory.

The run passed. From window two onward, it retained exactly **968 identities and
59,040 points**, independent of later identities. The second-half traced range
was **2,248 bytes**, from 12,535,432 to 12,537,680 bytes. Peak traced allocation
was 12,705,064 bytes. After expiry all four state maps were empty; the tracing
counter fell to 252,472 bytes, which includes empty-map capacity and benchmark
references/bookkeeping. Wall time was 2.894 seconds with Python 3.12.13 and
allocation tracing enabled; it is not a collector latency benchmark.

Keep the current expiry implementation. This supports bounded retention for a
fixed arrival rate and increasing clock in the tested workload, not a universal
bound on arbitrary device arrival rates. Clock steps, process/session identity
churn, database history, snapshot copies, and frontend chart retention are
outside this experiment. The native-worker observation ran separately at the
same time; no driver calls occur in this synthetic benchmark.

Raw results: [identity-churn-2026-10-01.json](../../benchmarks/results/identity-churn-2026-10-01.json).
