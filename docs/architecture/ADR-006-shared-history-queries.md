# ADR-006: Share bounded historical query results

Status: accepted for Phase 5, 2026-10-01.

## Context and evidence

The collector already shares live snapshots, but each history request repeats a
SQLite aggregation. At 50 accelerated clients, 100 month requests made 100 SQL
queries; live p95 reached 2.76 seconds and history p95 4.98 seconds. The database
contained 30 days of synthetic eight-GPU measurements. See the
[experiment and baseline](../research/history-query-sharing.md).

Requirements: preserve UUID/legacy identity, null gaps, read-only access, local
calendar-day semantics, and independent hardware polling. Bound repeated SQL
work and moving-window age. Keep source failure and query freshness visible.
Do not add schema migrations or make hardware collection wait for query locks.

## Alternatives

- Continue querying per request: minimal state, but the baseline shows avoidable
  contention even with the current retention limit.
- Share results on demand: one query per range/version, up to three cached
  results, no new thread or lifecycle. Cold queries still occupy request threads.
- Refresh all ranges in a background worker: avoids some cold-request latency,
  but adds scheduling/shutdown and computes unused ranges. Keep as a future
  option if measured cold latency is unacceptable.
- Persist downsampled tables or switch databases: adds migration and maintenance
  complexity. Single-query month cost is about 200 ms on this host; first test
  eliminating duplicate work.
- Enable SQLite WAL: can improve reader/writer overlap, but does not eliminate
  repeated aggregation. It also changes backup and file-management requirements.
  SQLite documents [connection isolation and journal behavior](https://www.sqlite.org/isolation.html).
  Keep the existing journal mode for this experiment.

## Decision

Create one `HistoryQueryCache` per Flask application with the configured timezone
and exactly three range locks. Requests for the same range share a completed
query. Different ranges and the collector do not acquire each other's cache
locks. SQLite connections remain short-lived and confined to their calling
thread; cache entries contain plain response data, copied before delivery.
[Python lock semantics](https://docs.python.org/3/library/threading.html#lock-objects)
provide mutual exclusion during each range's refresh.

Successful results are eligible for reuse for less than 60 monotonic seconds
from query start. Refresh on a changed successful-write monotonic timestamp,
today's local midnight, or a wall/monotonic clock divergence over one second.
Capture the write timestamp before querying: a concurrent write cannot cause an
earlier query to be cached as if it incorporated that write. A request racing a
write can return the earlier result; the next request refreshes it.

Expose `query.as_of`, `query.age_seconds`, and `query.max_age_seconds` in history
responses. Query time is the upper bound used in the SQL window, not the last
persisted sample. Add current writer health after retrieving the result so a
cache hit cannot conceal a failed/stale collector. Week/month windows may lag
request time by the reported age. Today never reuses yesterday's entry.

On SQLite failure, discard the old entry and return the existing safe 503 error.
Share that failure for one second after query completion to prevent queued
clients from repeatedly executing failing SQL. A changed successful-write
timestamp permits immediate retry. Do not cache exception text or retain old
data as a successful refresh. Authentication and range validation precede query
access. HTTP responses retain `Cache-Control: no-store`.

## Consequences and limits

No schema or journal-mode change; legacy reads and retention remain unchanged.
The demo implements the same successful-write timestamp contract without disk
access. `HistoryStore.read` stays uncached for direct storage callers and raw
query benchmarks. The cache is process-local and assumes the supported single
writer deployment. Out-of-band file changes are discovered on the next refresh,
within the reuse lifetime under normal clocks; cached success is not a filesystem
availability probe. Entry count is fixed, but size depends on device identities.

Cold queries, simultaneous misses for different ranges, and unusually large or
slow databases can still delay HTTP workers. This cache does not impose a SQL
execution deadline. The 60-second setting limits reuse, not total query execution
time. Longer retention and multiple web processes need separate measurements.

The identical 50-client short workload reduced live/history p95 to 358/519 ms
and SQL queries from 100 to one. A 185-second mixed run completed 11,067 HTTP
requests with zero failures and four successful writes; six queries served
1,850 history requests. Some collection deadlines were missed, and combined
server/load-generator RSS grew during the run. These measurements support
removing duplicate SQL work, without establishing a capacity or memory plateau.
Tests cover concurrency, invalidation, clock/DST boundaries, failures, mutation
isolation, and current health on cached responses.
