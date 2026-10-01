# Phase 5 validation

Date: 2026-10-01. Scope: share historical queries and measure longer dashboard
loads. Implementation and research are preserved on `perf/shared-history-cache`
and [PR #5](https://github.com/drakishev/gpuroster/pull/5).

The backend suite now has **85 tests**, including 13 new cache/API regressions.
They cover 50 simultaneous cold readers, isolated response copies, monotonic
expiry, successful/skipped/failed writes, writes racing queries, wall-clock
steps, local midnight on DST days, independent range locks, safe shared failures
and recovery, authentication ordering, and current writer health on cached data.
No GPU or private host data is needed.

All eight CI jobs passed for implementation commit `8d02963`: backend Python
3.10/3.12/3.14, 16 Chromium regressions, quality/reproducible assets, dependency
audit, and clean installed live/demo smoke tests on Python 3.10/3.14. This is
**101 distinct regression tests** across the backend and browser suites. The PR
also requires successful CI for the final commit before merge.

The [mixed-load experiment](../research/history-query-sharing.md) compares the
unchanged production HTTP adapter against a temporary database with 30 days of
eight-GPU synthetic rows. At 50 accelerated clients, history SQL calls fell from
100 to one; live/history p95 fell from 2.76/4.98 seconds to 358/519 ms. The longer
185-second run completed 11,067 HTTP requests with zero request/write failures,
six SQL queries, and four successful writes. Collector counts did not multiply
with requests, though some deadlines were missed. Combined server/client RSS
grew, so this does not establish a memory plateau. Raw aggregates and limitations
are linked from the report; generated databases are deleted.

The [five-minute browser run](../benchmarks.md#five-minute-browser-run) used real
Chromium and the production Waitress adapter with synthetic providers. After
1,000 warm-up refreshes, 599 additional manual refreshes and chart-range switches
completed without HTTP/browser errors or external requests. Post-GC retained JS
heap samples stayed between 4,234,040 and 4,370,212 bytes. This measures one
workload's JavaScript heap, not total browser memory or long-term leak freedom.

Version is 0.5.0. There is no new schema/journal-mode change or deployment action.
The existing database checksum remains unchanged, the original virtual
environment is untouched, and no existing service was restarted. The baseline
commit remains in Git history. Source and result files contain synthetic device
and user data only.

Remaining work includes separate-process memory profiling over longer periods,
target-host driver/MIG validation, runtime dependency locking, and release
automation. Cold queries can still delay request workers; the cache does not
provide a SQL execution deadline or support multiple web processes.
