# History query sharing experiment

Date: 2026-10-01. Baseline application: `a5079a9`.

Hypothesis: sharing repeated history aggregations will reduce database work and
live API latency when many dashboards view the same range. History is written
once per minute, but every HTTP request currently scans and aggregates SQLite.

Success criteria: fewer SQL queries than history requests; reduced live/history
p95 latency against the same workload; no HTTP or write errors; collection counts
remain governed by the sampler interval. Check refresh after successful writes,
expiration, clock changes, calendar-day changes, concurrent requests, and errors.
Run a longer workload across multiple real 60-second write intervals before
accepting the implementation.

## Baseline

`python -m benchmarks.history_load` uses the production four-thread Waitress
adapter, a fresh process per 1/5/20/50-client case, and a temporary database with
345,600 synthetic rows (eight GPUs, 30 days, one sample/minute). Each client has
independent live/history request loops: live every 200 ms, month history every
five seconds, for ten seconds. Cold requests are included. Source collection is
synthetic, every 250 ms. CPU and RSS include the load generator. No real database
or device is accessed. This is accelerated, synchronized traffic, not ordinary
dashboard polling or a capacity guarantee.

| Clients | History requests / SQL queries | Live p95 (ms) | History p95 (ms) | CPU seconds | Source cycles |
| --- | --- | --- | --- | --- | --- |
| 1 | 2 / 2 | 5.598 | 205.295 | 0.722 | 40 |
| 5 | 10 / 10 | 27.772 | 471.182 | 3.429 | 40 |
| 20 | 40 / 40 | 526.343 | 1,339.570 | 12.322 | 40 |
| 50 | 100 / 100 | 2,761.274 | 4,978.465 | 26.844 | 37 |

All responses succeeded. At 50 clients, the service delivered only 942 live
responses, and scheduling skipped some collection deadlines under load. The
baseline supports testing query sharing before changing storage engines or
adding persistent aggregation tables. Raw results:
[history-load-baseline-2026-10-01.json](../../benchmarks/results/history-load-baseline-2026-10-01.json).

## Proposed experiment

Use a bounded process-local cache for the three supported ranges and the
configured timezone. A per-range lock coalesces concurrent misses. A successful
write invalidates earlier results; a maximum 60-second age bounds moving-window
staleness even without writes. Include query time and age in responses. Keep
query work outside the hardware collector, preserve read-only legacy access,
and report refresh failures explicitly. Measure the same workload before
deciding whether more complex preaggregation or background refresh is needed.

## Cache comparison

The same ten-second workload with the shared query cache:

| Clients | History requests / SQL queries | Live p95 (ms) | History p95 (ms) | CPU seconds | Source cycles |
| --- | --- | --- | --- | --- | --- |
| 1 | 2 / 1 | 7.356 | 230.652 | 0.528 | 40 |
| 5 | 10 / 1 | 24.949 | 275.552 | 1.495 | 40 |
| 20 | 40 / 1 | 19.103 | 334.232 | 4.679 | 40 |
| 50 | 100 / 1 | 358.274 | 519.367 | 10.382 | 40 |

All responses succeeded. The 50-client case delivered 1,976 live responses
instead of 942. Its live median was 244.094 ms versus 219.136 ms before: sharing
reduced the long tail and increased delivered work, but this accelerated load
still queues requests. The one-client history p95 represents the single cold
query and did not improve; caching does not speed up an individual SQL scan.
Results are one run per case, not confidence intervals or capacity guarantees.
Raw output: [history-load-cached-2026-10-01.json](../../benchmarks/results/history-load-cached-2026-10-01.json).

The improvement justifies the cache implementation. Acceptance still requires
the longer mixed workload across successful writes; [ADR-006](../architecture/ADR-006-shared-history-queries.md)
records its consistency and failure semantics.
