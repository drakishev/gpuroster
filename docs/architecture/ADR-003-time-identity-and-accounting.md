# ADR-003: Device identity, time windows, and history preservation

Status: accepted for Phase 2. Date: 2026-10-01.

## Context and alternatives

GPU indices can change between samples/restarts. Local-time strings alone cannot identify a point across midnight or timezone changes. Summing terminal durations double-counted overlap and excluded sessions that started before a reporting window. Old history contains no device UUID, so assigning it to today's index would invent identity.

Alternatives were continuing index-based history, rewriting old records to current UUIDs, or adding nullable UUID identity. Only the additive approach preserves known information without making assumptions about old hardware. For user duration, summed terminal-hours and connected wall time answer different questions; the dashboard will report connected wall time.

## Decision

New measurements carry GPU UUID and a separate display index. Process attribution reuses the sampled device map, and an unknown mapping is null. Rolling history is keyed by UUID with Unix-second timestamps. Numeric timestamps are ordered before chart formatting; UTC labels are explicit.

On the collector's first write, add `gpu_uuid TEXT` to the existing `gpu_util` table in a transaction. No historic UUID is fabricated. Old rows retain null and query as `legacy:index:N`, separately from new UUID series. Legacy databases can also be read without migration. Existing explicit-column writes remain valid. Thirty-one-day retention continues during ordinary writes, and failed transactions do not advance the monotonic write deadline.

Historical queries retain 5-minute/hour/6-hour aggregation for today/week/month, but fill missing time buckets with null. Rolling series also record gaps and reset smoothing after an unavailable sample. Changing a device's display index cannot combine it with another UUID's series. Display indices in aggregated history are presentation hints; `id` is authoritative.

Use UTC timestamps and an explicit `GPUROSTER_TIMEZONE` (default UTC) for the calendar-day boundary. Week/month windows mean 7/30 elapsed days, not calendar week/month. Read util-linux `last` with full names and ISO timestamps containing offsets, and preserve missing host fields. Unknown crash/end timestamps are excluded and flagged, rather than assumed active.

For each user and reporting window, clip intervals to the window and union overlaps before summing elapsed seconds. Include active non-TTY SSH evidence in the same union. This prevents double counting across sources and terminals, includes sessions crossing window boundaries, and handles DST day lengths. User-specific GPU-hours are not inferred from login time.

## Evidence and consequences

Tests cover additive migration, legacy reads, old-style writes, retry after failure, index swaps, gaps, retention, monotonic write timing, duplicate/overlapping intervals, cross-month sessions, 23/25-hour days, missing hosts, full usernames, truncated records, and unknown crash endpoints. [Storage benchmarks](../benchmarks.md) include 1/7/30/90 days of synthetic data; no benchmark databases are committed.

The response identifies schema version 2. External clients must handle UUID history keys, numeric live timestamps, ISO historical labels, and null process indices. Schema rollback cannot recover UUID meaning in an old application. Back up the database and stop the old collector before upgrading; run one writer instance.

The latest 2,000 login records and currently visible SSH processes are incomplete evidence. Source health reports truncation/unparsed records and the log's coverage start when available. Rotated logs and ended non-TTY SSH sessions are not reconstructed or persisted. Results remain connected-time estimates, not a billing ledger.

References: [NVIDIA identity guidance](https://docs.nvidia.com/deploy/nvidia-smi/index.html), [util-linux last timestamps and field widths](https://github.com/util-linux/util-linux/blob/master/login-utils/last.1.adoc).
