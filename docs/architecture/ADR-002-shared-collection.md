# ADR-002: One collector, shared snapshots, and isolated NVML

Status: accepted for Phase 2. Date: 2026-10-01.

## Context and requirements

Phase 1 bounded commands and protected access, but every stats request still ran three NVIDIA commands and system/process inspection. A separate history thread also read GPU metrics. Client count therefore multiplied collection, and CPU measurements had request-thread baselines.

The collector must run once per schedule, independent of clients; snapshots must publish atomically; readers must continue during source stalls; unavailable and stale measurements must remain visible. Ordinary tests must work without hardware.

## Evidence and alternatives

[Backend experiments](../research/gpu-backends.md) measured two CLI queries at approximately 282 ms per eight-GPU sample and direct NVML at 0.583 ms. The isolated NVML prototype measured a 0.662 ms median and 0.923 ms p95 over 100 warm eight-GPU samples, with a 475 ms cold start. Measurements are from one host and do not establish cross-driver compatibility.

| Option | Evaluation |
| --- | --- |
| Per-request collection | Rejected: client count changes hardware work and CPU baselines |
| One collector per web worker | Rejected for this phase: duplicates collection and history writes across workers |
| Separate collector service plus shared transport/cache | Useful production direction; adds deployment and ownership decisions beyond this phase |
| One thread and process-local snapshot cache | Chosen for the existing single-web-process launcher; simple ownership and bounded atomic publication |
| NVML in the web process | Fast, but native calls cannot use the CLI subprocess timeout mechanism |
| Persistent NVML child with deadline/restart | Chosen: keeps measured latency benefit and permits timeout termination without blocking API reads |

## Decision

`CollectorService` owns GPU, process, CPU, optional session, rolling-history, and SQLite-write work. The web layer only reads snapshots or historical SQL results. Frozen measurement records normalize identity/units; the cache publishes and copies complete payloads under a short lock. The cache lock is never held during hardware or SQL work.

Use `time.monotonic()` for schedule, source ages, session refresh, and write cadence. Skip missed ticks instead of issuing catch-up reads. A cycle supplies both API metrics and utilization history. Session evidence defaults to a separate 60-second cadence. CPU baselines are established on the collector thread and reset on restart.

Default `auto` GPU selection initializes NVML in a persistent spawned child process. The parent enforces `GPUROSTER_COMMAND_TIMEOUT`, terminates a stalled child, and creates a new one on the next attempt. Failed initialization can select the CLI provider. Once NVML is running, errors remain visible rather than silently changing backends. Explicit `nvml` and `smi` settings support diagnostics and compatibility needs. Collection uses only read APIs.

Every API snapshot has a sequence, source timestamps/ages, and health. A request never advances the collection timestamp. Before the first sample, stats returns 503. A blocked/failed collector leaves a readable snapshot which ages to stale. Session endpoints reject unavailable/stale required sources. Source errors use safe codes and rate-limited logs.

## Validation and consequences

The [HTTP benchmark](../benchmarks.md) used 1/5/20/50 clients; each case recorded 12 scheduled source reads while API traffic grew. Regression tests verify concurrent readers, mutation isolation, publication while a source blocks, failure recovery, worker termination/restart, and lifecycle ownership.

The cache is deliberately process-local. This does not authorize a multi-worker deployment. NVML isolation adds a child process and about 55 MiB worker RSS in the observed run. OS process inspection and SQLite writes still share the collector thread; slow work can delay publication, but requests do not wait for it. Per-request history queries and frontend polling remain unchanged transport choices; SSE and external cache/storage selection require separate evidence.

References: [NVIDIA NVML](https://docs.nvidia.com/deploy/nvml-api/nvml-api-reference.html), [NVIDIA CLI compatibility](https://docs.nvidia.com/deploy/nvidia-smi/index.html), [psutil sampling semantics](https://psutil.io/api/).
