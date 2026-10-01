# Collection, storage, and serving benchmarks

Recorded 2026-10-01 on one Linux host, using Python 3.12. Results are observations, not production capacity or cross-driver guarantees. Committed output contains aggregate measurements and synthetic data only. No benchmark databases or real process/device identities are included.

## Rolling-history identity churn

The [accelerated churn experiment](research/identity-churn.md) replaces all eight
GPU identities every tick across ten retention windows. All 9,600 synthetic
identities obeyed the 968-identity retention bound; all state expired after the
final gap. Second-half traced Python allocations stayed in a 2,248-byte band.
This measures the history model with simulated time, not an end-to-end soak.

```bash
python -m benchmarks.identity_churn
```

## Native worker on real GPUs

The [15-minute target-host experiment](research/target-host.md) sampled eight
H200 GPUs 900 times with no invalid samples or missed deadlines. Worker USS was
flat, the parent second-half USS band was 0.023 MiB, and ten forced worker-exit
recoveries succeeded without accumulating descriptors. Driver 590.48.01 and
disabled MIG are the only real configuration covered. The report separates
native-worker/parent resources and explains the remaining permission/MIG gaps.

```bash
python -m benchmarks.hardware_validation --duration 900
```

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

## Production HTTP server

Phase 3 hypothesis: four Waitress request threads can serve the shared snapshot without errors or request-driven collection. The acceptance criteria are zero invalid/failed requests and collector cycles consistent with elapsed time and the 250 ms schedule. Compare against the previous threaded Werkzeug server; no claim about other production servers is inferred.

```bash
python -m benchmarks.shared_collector --server waitress --duration 5
python -m benchmarks.shared_collector --server werkzeug --duration 5
```

Recorded on the same Linux/Python 3.12 host on 2026-10-01, sequentially with a fresh process per case. Both use the same eight-GPU synthetic snapshot, 120 history points, and 200 ms client polling described above. Waitress is 3.0.2; Werkzeug is 3.1.9. Request completion can extend a nominal five-second run slightly. CPU and peak RSS include the load generator. No real devices are polled.

| Server | Clients | Requests | Cycles | Errors | Median / p95 (ms) | CPU seconds | Peak RSS (MiB) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Waitress | 1 | 25 | 20 | 0 | 4.107 / 7.168 | 0.149 | 37.1 |
| Waitress | 5 | 125 | 20 | 0 | 5.253 / 17.930 | 0.661 | 39.5 |
| Waitress | 20 | 500 | 20 | 0 | 6.713 / 32.906 | 2.133 | 46.8 |
| Waitress | 50 | 1,218 | 20 | 0 | 49.945 / 197.663 | 5.058 | 59.9 |
| Werkzeug | 1 | 25 | 20 | 0 | 5.327 / 10.185 | 0.184 | 37.4 |
| Werkzeug | 5 | 125 | 20 | 0 | 5.967 / 20.219 | 0.730 | 40.0 |
| Werkzeug | 20 | 500 | 20 | 0 | 8.284 / 37.664 | 2.232 | 46.9 |
| Werkzeug | 50 | 1,065 | 21 | 0 | 244.186 / 271.851 | 5.282 | 59.6 |

Waitress passes the criteria and preserves collector independence. Its 50-client p95 is lower in this run, but this short test excludes slow clients, TLS/proxy overhead, and concurrent history aggregation. It establishes suitability for the measured workload, not a production capacity limit. [ADR-004](architecture/ADR-004-production-deployment.md) records the lifecycle and deployment tradeoffs.

Raw aggregate outputs are under [benchmarks/results](../benchmarks/results/). Later sections cover mixed live/history traffic and browser timing. Multiple driver/GPU configurations, MIG behavior, and longer resource profiles remain future work.

## Offline demo browser

Phase 4 hypothesis: packaged assets render the real dashboard without third-party requests, while the demo exercises normal API delivery and chart updates. Success means populated charts, no external requests, and no browser errors. Run with development dependencies and Chromium installed:

```bash
python -m benchmarks.dashboard
python -m benchmarks.dashboard --screenshot docs/images/demo-dashboard.png
```

Measured on 2026-10-01 with Python 3.12, Chromium, the production HTTP adapter, four synthetic GPUs, and five fresh browser contexts. Each load waits for four rendered GPU cards. One context then runs 1,000 sequential `fetchStats()` refreshes, much faster than the default five-second interval. The wall-clock measurements include browser automation and local HTTP; they exclude dependency installation and remote network latency.

| Measurement | Result |
| --- | --- |
| Dashboard ready, median / maximum | 106.775 / 360.031 ms |
| Full refresh, median / p95 | 10.834 / 16.343 ms |
| External requests / browser errors | 0 / 0 |
| Compiled CSS / chart bundle | 10,943 / 205,325 bytes |
| Dashboard JavaScript | 17,704 bytes |
| Retained JS heap, before / after 100 / after 1,000 refreshes | 2,947,704 / 3,711,432 / 4,269,580 bytes |

Heap was measured through Chromium's performance API after explicit garbage collection. It grew during this short run; the figures do not establish a leak-free steady state or browser process RSS. A later timed run is described below. Assets are shipped in the Python package, and the recorded screenshot/API data use only generated identities. Raw output is [offline-demo-browser-2026-10-01.json](../benchmarks/results/offline-demo-browser-2026-10-01.json).

## Shared historical queries and mixed traffic

Phase 5 hypothesis: eliminating duplicate history aggregation reduces contention
on the four HTTP workers while preserving independent hardware polling. The
[research report](research/history-query-sharing.md) includes methodology,
1/5/20/50-client baseline/comparison tables, raw outputs, and limits. Each short
case runs in a fresh process with 345,600 synthetic rows and includes cold
queries. Reproduce with:

```bash
python -m benchmarks.history_load
python -m benchmarks.history_load --clients 50 --duration 185 --stats-interval 1 --history-interval 5
```

| 50-client, 10-second workload | Before | Shared queries |
| --- | --- | --- |
| History requests / SQL queries | 100 / 100 | 100 / 1 |
| Live responses delivered | 942 | 1,976 |
| Live HTTP p95 | 2,761.274 ms | 358.274 ms |
| History HTTP p95 | 4,978.465 ms | 519.367 ms |
| CPU, including clients | 26.844 s | 10.382 s |
| Source cycles / HTTP failures | 37 / 0 | 40 / 0 |

The 185-second mixed run completed 11,067 requests without HTTP or write failures.
Six SQL queries served 1,850 history requests across four writes. Live/history
p95 was 140.126/495.966 ms, with maximum latency reaching 1.68 seconds. The
collector ran 736 cycles against about 740 scheduled opportunities. Combined
server/client RSS grew from 37.0 to 86.4 MiB; no memory plateau or production
capacity claim is made. See the report for retained benchmark data and host-load
limitations. The original uncached storage benchmark remains valid:
`HistoryStore.read` still executes SQLite directly.

## Five-minute browser run

```bash
python -m benchmarks.dashboard --soak-seconds 300
```

Phase 5 extends the same real Chromium/demo benchmark: after five fresh page
loads and 1,000 warm-up updates, refresh every 500 ms for 300 seconds, leaving
normal browser polling enabled. Switch live/today/week/month views every 30
seconds and refresh the selected historical view along with live stats. Measure
retained JS heap after explicit garbage collection every 30 seconds. The browser
run follows the HTTP load run; they do not run simultaneously.

| Measurement | Result |
| --- | --- |
| Timed duration / additional manual refreshes | 300.024 s / 599 |
| HTTP errors / browser errors / external requests | 0 / 0 / 0 |
| Retained JS heap, start / finish | 4,266,392 / 4,317,480 bytes |
| Retained JS heap, sampled minimum / maximum | 4,234,040 / 4,370,212 bytes |
| Fresh dashboard ready, median | 109.437 ms |
| Warm-up refresh, median / p95 | 11.109 / 16.780 ms |

Sampled retained heap stayed within a narrow band after warm-up, including range
changes. This is encouraging for this generated workload but does not rule out
long-term leaks, measure total browser RSS, or test growing real identity counts.
The demo uses no SQLite or host monitoring. Raw results:
[browser-soak-2026-10-01.json](../benchmarks/results/browser-soak-2026-10-01.json).

## Server memory separated from clients

Phase 6 tests whether the earlier combined server/client RSS growth reflects
sustained growth in the server. [Method, acceptance criteria, and results](research/server-memory.md)
use a spawned production HTTP server, a separate load generator, and temporary
synthetic SQLite history. Run:

```bash
python -m benchmarks.server_memory --duration 600 --cooldown 60
```

Fifty clients completed 36,000 live/history requests over ten minutes with zero
HTTP/validation/write failures. During the final five loaded minutes, server USS
stayed within 39.64–40.46 MiB, a 0.82 MiB band with approximately zero endpoint
growth. Sampled threads stayed at seven and file descriptors returned to the
baseline of 14 after traffic. Server RSS ended at 51.69 MiB and remained there
after an idle minute and explicit GC. The criteria passed without a runtime
change. This is evidence for a bounded sampled range under this fixed synthetic
workload, not proof of long-term leak freedom or native-driver behavior.
