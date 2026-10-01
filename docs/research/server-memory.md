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
