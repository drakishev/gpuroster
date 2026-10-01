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

## Longer mixed workload

`python -m benchmarks.history_load --clients 50 --duration 185 --stats-interval 1 --history-interval 5`
ran against the same synthetic database and production HTTP adapter. Live
polling was five times more frequent and history polling twelve times more
frequent than the dashboard defaults. The result included 9,217 live and 1,850 historical
responses, zero HTTP/validation failures, four successful writes (including
startup), and zero write failures. Only six SQL queries served the historical
requests. TTL expiry and write invalidation can occur separately, so the design
does not guarantee exactly one query per write.

Live median/p95 was 7.339/140.126 ms; history median/p95 was 52.851/495.966 ms.
Maximum responses reached 1.68/1.63 seconds respectively. The collector completed
736 cycles against roughly 740 scheduled opportunities: no multiplication with
client count, but some deadlines were missed. Write median/max was
6.404/152.134 ms. CPU was 56.879 seconds, including load generation.

Combined server/load-generator RSS increased from 38,760,448 to 90,640,384 bytes;
after 60 seconds it ranged from 79,196,160 to 90,640,384 bytes. The harness also
retains latency measurements. This run does not demonstrate a memory plateau
or isolate server memory from clients/allocators. A separate-process, longer
resource profile is future work. Small development checks also ran on this host
during this soak; it is a functional endurance check, not an isolated latency
comparison.

Raw output: [history-load-soak-2026-10-01.json](../../benchmarks/results/history-load-soak-2026-10-01.json).
The comparison and successful writes under sustained traffic justify accepting
the cache; [ADR-006](../architecture/ADR-006-shared-history-queries.md) records its
consistency and failure semantics. Cold-query latency remains visible, and no
production capacity or leak-free guarantee is inferred.
