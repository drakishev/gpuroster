# Isolated server memory experiment

Date: 2026-10-01. Application baseline: `ddd6050` (0.5.0).

The Phase 5 mixed-load process grew from 37.0 to 86.4 MiB RSS, but that process
also contained the HTTP load generator, its threads, and latency arrays. It
cannot identify server growth. Hypothesis: after warm-up, a fixed set of devices
and clients does not cause sustained growth in server memory, threads, or file
descriptors.

Use a spawned server process and a separate client process. Seed 30 days of
synthetic eight-GPU data in a temporary database before spawning the server.
Exercise the real four-thread Waitress adapter, collector, rolling history,
SQLite persistence, and query cache. Fifty clients poll live stats every second
and rotate today/week/month history every five seconds. Hardware sources are
synthetic and sampled every 250 ms. No NVML worker, host processes, sessions, or
original database is used.

Run ten minutes of traffic, then one minute without HTTP requests. Sample server
RSS, USS, CPU, thread/file-descriptor counts, source health, collection cycles,
and SQL counters every 30 seconds. Client latency uses fixed histogram buckets;
p95 is an upper bound, not an exact percentile. Server instrumentation stores
counts only. Perform one final explicit garbage collection after the idle
period. An optional `--trace-python` diagnostic reports live traced Python bytes
but adds overhead and must be labeled separately.

Acceptance for this workload: zero request/write failures, healthy collection,
no accumulating threads/descriptors, and the final five loaded minutes staying
within a 10 MiB server USS band without growth exceeding 1 MiB/minute between its
endpoints. This pragmatic threshold detects sustained growth here; it cannot
prove leak freedom. If the threshold fails, inspect traced Python allocations
before choosing a fix. Native driver-worker memory and changing identity counts
require separate experiments.

```bash
python -m benchmarks.server_memory --duration 600 --cooldown 60
```

## Results and decision

The run completed 30,000 live and 6,000 historical requests with zero validation,
HTTP, or write failures. All sampled source-health states were healthy. There
were 2,399 collection cycles over 600.027 loaded seconds on the 250 ms schedule,
31 history queries, and ten writes including startup. The idle minute brought
the totals to 2,639 cycles and eleven writes. No HTTP request triggered hardware
collection. Live/history p95 upper bounds were 100/500 ms, with observed maxima
of 609.508/593.466 ms; histogram bounds are not exact percentiles.

| Server measurement | Before load | End of load | After idle + GC |
| --- | --- | --- | --- |
| RSS (bytes) | 36,589,568 | 54,198,272 | 54,198,272 |
| USS (bytes) | 24,657,920 | 41,570,304 | 41,594,880 |
| Threads | 7 | 7 | 7 |
| Open file descriptors | 14 | 14 | 14 |
| CPU seconds since startup | 0 | 112.10 | 112.66 |

Over the final five loaded minutes, sampled USS ranged from 41,566,208 to
42,426,368 bytes, a **0.82 MiB band**. Endpoint growth was approximately
**−0.001 MiB/minute**. Sampled thread count remained seven; descriptor counts
ranged from 14 to 23 and returned to 14. Synchronized client bursts occasionally
reached Waitress's existing 100-channel cap, without failed requests. Thirty-
second sampling does not capture every short-lived peak. Final explicit GC
collected zero objects and did not lower RSS.

The workload passes the stated criteria. It shows warm-up followed by a bounded
sampled range on this host, rather than evidence requiring an application memory
fix. Preserve the existing runtime implementation. The earlier combined-process
measurement cannot be attributed to a server leak; it included clients and
latency storage, and used only month queries. This experiment rotates all three
ranges, so the two RSS numbers are not a controlled before/after comparison.

Python 3.12.13, Linux, synthetic identities, and no Python allocation tracing.
Some build/dependency checks ran separately on the same host; CPU/latency values
are contextual observations, not isolated capacity measurements. Ten minutes
does not establish long-term leak freedom. Changing identities, sessions, native
driver/worker memory, and real host permissions remain outside this experiment.
Raw results: [server-memory-2026-10-01.json](../../benchmarks/results/server-memory-2026-10-01.json).
