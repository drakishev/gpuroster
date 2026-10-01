# Snapshot API version 2

`GET /api/stats` reads the last published snapshot. Authentication/privacy checks precede cache access. `503 collector_not_ready` means no cycle has published yet; it does not trigger collection. After publication, 200 may contain degraded/stale source health. Consumers must inspect health, not HTTP status alone.

| Field | Meaning |
| --- | --- |
| `schema_version` | `2` |
| `mode` | `live` or `demo`; demo measurements are generated, never host data |
| `sequence` | Increases once per published collector cycle; resets after process restart |
| `timestamp` | UTC ISO timestamp at cycle start; never request time |
| `collector.backend` | Actual selected provider: `nvml` or `nvidia-smi` |
| `collector.interval_seconds` | Configured collection cadence; slow cycles can delay publication |
| `collector.timezone` | Calendar-day reporting timezone |
| `gpus[].uuid` | Stable device identity; `index` is a display/driver index |
| `gpus[].utilization` | Percent, nullable |
| `gpus[].memory_used`, `memory_total` | MiB, nullable |
| `gpus[].temperature`, `power` | Degrees Celsius and watts, nullable |
| `processes[].gpu_uuid` | Driver-reported identity |
| `processes[].gpu` | Sampled index or null if the mapping is unknown |
| `processes[].mem_mb` | MiB, nullable; old API field name retained |
| `user_gpu[].mem_gb` | GiB, nullable if any contributing allocation is unknown |
| `system.cpu_percent` | Percent over the collector's interval; null while warming up |
| `system.ram_used_bytes`, `ram_total_bytes` | Exact byte counts; existing `*_gb` fields are rounded decimal GB |
| `history` | UUID → list of `{ts: Unix seconds, util: percent or null}` |
| `history_devices` | UUID → display index and name |

`health.sources` includes `gpus`, `processes`, `system`, `history`, `connections`, and `sessions`. Each exposes `status`, `sampled_at` (last attempt), `last_success_at`, and `age_seconds` since success, plus a safe `error` code when appropriate. Statuses are `not_started`, `ok`, `warming_up`, `partial`, `unavailable`, `stale`, and `disabled`. No raw stderr, exception messages, or credentials are returned.

Staleness uses a monotonic clock, with a threshold of the greater of 15 seconds or three source intervals. It can change between requests without a new sequence. A failed source returns empty/unknown measurements and an unavailable status; clients must not interpret those as measured zero. Other sources can remain usable.

Session routes retain their object/list shapes and add `X-Snapshot-Sequence`. They read cached evidence, are disabled by default (403), and return 503 if required sources are unavailable or stale. Session `login`/`logout` timestamps are UTC ISO strings or `active`. Estimates use unioned connected intervals, not terminal-hours or GPU-hours.

`GET /api/gpu_history?range=today|week|month` reads SQLite, with UTC ISO labels and datasets containing `id`, `gpu`, `label`, and nullable `data`. `id` is a UUID or `legacy:index:N`; legacy and known identities never merge. A missing database produces 503 without creating a file. Historical reads do not poll hardware.

`X-GPU-Roster-Mode: live` or `demo` accompanies every response, including list-valued session endpoints. Historical JSON also includes `mode`. Demo history uses generated values and no SQLite file; its source health refers to the synthetic provider. Mode is selected at startup and cannot be changed through an API request.
