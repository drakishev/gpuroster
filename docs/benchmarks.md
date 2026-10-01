# Phase 2 benchmarks

Recorded 2026-10-01 on one Linux host, using Python 3.12. Results are observations, not production capacity or cross-driver guarantees. Committed output contains aggregate measurements and synthetic data only. No benchmark databases or real process/device identities are included.

## GPU backends

Hypothesis: NVML reduces steady-state latency and CPU cost while preserving requested metrics. [Method, alternatives, and direct NVML/CLI results](research/gpu-backends.md) cover 30 samples per case at 1/4/8 GPUs. The CLI harness uses two subprocesses per sample; NVML initializes once and queries the corresponding device/process information.

```bash
python -m benchmarks.gpu_backends
python -m benchmarks.shared_collector --nvml
```

On eight GPUs, the two-query CLI median was **281.141 ms**, versus **0.583 ms** for direct NVML. The selected persistent worker then measured **0.662 ms median / 0.923 ms p95** over 100 warm samples, including IPC. Cold start was **475.298 ms**, worker CPU averaged about **0.7 ms/sample**, and worker RSS was approximately **55 MiB**. The worker benchmark reads raw GPU/process measurements; OS process-owner lookup is excluded. Rapid warm samples may benefit from driver caching.

Timeout, worker-exit, unsupported-field, initialization-fallback, and restart behavior are tested with synthetic workers, separate from hardware timing. A native-launcher smoke check also produced a healthy eight-GPU snapshot and persisted history to a temporary database. The existing application database was not opened for writing.

## Concurrent dashboard clients

Hypothesis: client count affects delivery work while source calls remain fixed by the collector schedule. Success means no request-triggered source reads, no invalid responses, and no multiplication of collection cycles.

```bash
python -m benchmarks.shared_collector
```

Each case uses a fresh process, threaded loopback HTTP server, eight synthetic GPUs/processes, 120 preloaded history points, a 250 ms collection interval, and a fixed 5 ms simulated GPU read. Clients poll every 200 ms for three seconds, much faster than the dashboard's five-second default. CPU includes server, collector, and load generator; RSS is peak process RSS. This is a short concurrency/stress check, not a soak test.

| Clients | API requests | Source cycles | Errors | HTTP median / p95 (ms) | CPU seconds | Peak RSS (MiB) |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 15 | 12 | 0 | 4.015 / 7.700 | 0.083 | 40.1 |
| 5 | 75 | 12 | 0 | 3.826 / 16.216 | 0.311 | 42.4 |
| 20 | 300 | 12 | 0 | 7.119 / 30.819 | 1.222 | 48.4 |
| 50 | 732 | 12 | 0 | 127.849 / 228.854 | 3.108 | 61.2 |

All cases recorded **12 source cycles**. HTTP CPU/latency grew at the highest accelerated load, as expected from JSON delivery and the test server. These results establish collection independence, not constant API cost. CI also checks 1/5/20/50 concurrent cache readers, mutation isolation, API reuse of a sequence, and readable snapshots while collection blocks.

## Historical storage

```bash
python -m benchmarks.history_storage
```

Synthetic rows represent eight GPUs sampled once per minute for 1/7/30/90 days. Each case creates a temporary database, measures schema initialization/migration, takes the median of three real API-query/aggregation calls per range, and times one eight-row write plus retention cleanup. UUID cases use full-length synthetic identifiers. The 90-day case deliberately exceeds normal retention to exercise catch-up cleanup. Seeding time is excluded.

| Schema | Days / rows | DB size (MiB) | Schema init/migration (ms) | Today / week / month query median (ms) | Write + retention (ms) |
| --- | --- | --- | --- | --- | --- |
| Legacy | 1 / 11,520 | 0.33 | 1.256 | 2.678 / 4.567 / 4.601 | 1.389 |
| Legacy | 7 / 80,640 | 2.32 | 0.863 | 2.487 / 33.453 / 33.510 | 1.872 |
| Legacy | 30 / 345,600 | 10.09 | 1.115 | 2.507 / 33.238 / 144.916 | 2.300 |
| Legacy | 90 / 1,036,800 | 30.36 | 2.387 | 2.520 / 35.186 / 145.244 | 211.144 |
| UUID | 1 / 11,520 | 0.78 | 0.186 | 2.713 / 4.305 / 4.357 | 2.068 |
| UUID | 7 / 80,640 | 5.50 | 0.157 | 2.532 / 35.324 / 35.892 | 2.071 |
| UUID | 30 / 345,600 | 23.68 | 0.181 | 2.549 / 37.839 / 197.381 | 1.595 |
| UUID | 90 / 1,036,800 | 71.10 | 0.265 | 2.584 / 38.476 / 203.738 | 265.354 |

The additive legacy migration preserved rows. Retention reduced the 90-day cases to 357,128 rows, including the newest eight-row write. SQLite kept the allocated file size after deletion; this implementation does not automatically vacuum it. UUID storage costs more space than the original index-only schema. A normalized device table/downsampling could reduce cost, but no storage-engine replacement is justified by these measurements alone. Month queries remain synchronous database work and warrant caching or preaggregation if history traffic grows.

Raw aggregate outputs are under [benchmarks/results](../benchmarks/results/). Future work includes longer soak tests, browser memory/load timing, multiple driver/GPU configurations, MIG behavior, and production-server benchmarks.
