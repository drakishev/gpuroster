# Phase 3 validation

Recorded 2026-10-01. Phase 1 and Phase 2 were merged in dependency order after their CI passed. Production serving and packaging were implemented on `feat/production-deployment`.

## Local checks

- 65 backend/collector/storage/server tests pass on Python 3.12.
- 12 Chromium regressions pass after the package move; visual behavior is unchanged.
- Ruff lint/format, JavaScript syntax, shell syntax, and Git whitespace checks pass.
- Runtime dependency audit reports no known vulnerabilities at validation time.
- An isolated build produces an sdist, then builds the wheel from that sdist. The wheel contains only package code/assets and distribution metadata; it contains no database, key, or credential file. Deployment examples and the smoke driver are included in the source archive.
- A fresh environment installs the wheel and runs the real console script from temporary working directories. The smoke checks Basic auth on pages/assets/history, disabled session access, schema v2 snapshots, unavailable GPU status without tools, state creation, duplicate-instance rejection, SIGTERM/SIGINT, and restart. It uses `CREDENTIALS_DIRECTORY`; explicit password-file behavior also has unit coverage. Signal exits took approximately 0.08 seconds locally.
- systemd 249 parses the service with no warnings for this unit. Only `ExecStart` was replaced with `/usr/bin/true` in a temporary copy for syntax checking; no service was installed or started. Validation caught and removed use of a newer credential path specifier before the deployment example was committed.

## Production lifecycle evidence

Ownership tests reject a second database lock, preserve its inode after release, reject unauthenticated remote binding before collection, and release locks, sockets, and worker threads after bind/server failure. The HTTP server uses an explicit Waitress lifecycle adapter tested against its pinned version.

An optional installed-launcher smoke used native NVML on eight GPUs with a separate temporary database and runtime-generated authentication. It observed healthy GPU snapshots, then terminated via SIGTERM. Both child processes (NVML worker and multiprocessing resource tracker) stopped; shutdown measured about 0.22 seconds. Only counts/status/timing were retained, not device or process identities. This does not validate every NVIDIA driver, service-account policy, or systemd sandbox combination.

The original checkout database checksum remained unchanged. Its virtual environment was not updated, and the live host deployment was not replaced.

## Concurrency and CI

[Production HTTP benchmarks](../benchmarks.md#production-http-server) cover 1/5/20/50 clients with synthetic snapshots. All Waitress cases produced zero request errors and 20 source cycles. The 50-client p95 was 197.663 ms. The same run against Werkzeug produced 271.851 ms p95; see methodology and limitations before interpreting this comparison.

CI retains Python 3.10/3.12/3.14 backend coverage, Chromium, quality checks, and dependency auditing. It adds wheel-from-sdist builds and clean installed-process smoke tests on Python 3.10 and 3.14, plus unit-file syntax validation. No ordinary job needs NVIDIA hardware, real credentials, login records, or a production database.

## Remaining limits

The systemd example needs validation under the target account and host permissions. Four HTTP workers can still queue during expensive history reads. Shutdown is bounded best-effort cleanup, not zero-downtime draining. The database lock assumes a local filesystem and supported launcher. CDN assets, a real demo provider, dependency locking, longer mixed-load benchmarks, and optional GPU CI remain next work.
