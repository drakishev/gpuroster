# ADR-004: Installable single-process production serving

Status: accepted, 2026-10-01.

Follow-up: [ADR-008](ADR-008-container-deployment.md) adds Docker deployment after
validating host process attribution. The original native deployment decision
and its context below remain applicable.

## Context and requirements

The shared cache and collector belong to one process. Flask's development server is unsuitable for production, and a generic multi-worker launcher would duplicate hardware polling and history writes. Deployment needs an installable artifact with assets, explicit startup/shutdown, protected credentials, persistent writable state, and GPU-independent installation tests.

## Alternatives and evidence

- Keep Werkzeug serving: useful as a test baseline, but [Flask explicitly excludes it from production](https://flask.palletsprojects.com/en/stable/deploying/).
- Gunicorn with one threaded worker and lifecycle hooks: viable on Linux, but requires a master/worker lifecycle and careful enforcement of worker count. More workers would require an independent collector/IPC design. No claim is made about comparative Gunicorn performance; it was not benchmarked here.
- Waitress: [one process with threaded request workers](https://flask.palletsprojects.com/en/stable/deploying/waitress/) matches the existing ownership model. [Documented controls](https://docs.pylonsproject.org/projects/waitress/en/stable/arguments.html) bound channels, header/body sizes, idle connections, and worker count.
- Split collector and web processes: supports multiple HTTP workers but adds IPC, supervision, permissions, and snapshot transport. The current measurements do not justify that complexity yet.

Hypothesis: four Waitress workers can serve the existing snapshot workload without request errors or multiplying collection. The [same-host benchmark](../benchmarks.md#production-http-server) passes at 1/5/20/50 synthetic clients. At 50 clients, Waitress recorded 1,218 requests, zero errors, 20 collector cycles, and 197.663 ms p95 in roughly five seconds; the development server recorded 1,065 requests, zero errors, 21 cycles over a slightly longer run, and 271.851 ms p95. These are short acceptance measurements, not a capacity guarantee or a reason to rank all WSGI servers.

## Decision

Package code and assets under `gpuroster` with setuptools and a `gpuroster` console entry point. Build wheels from an sdist in CI, then install and exercise them outside the checkout on Python 3.10 and 3.14. Runtime requirements are shared by source installs and package metadata.

Use Waitress 3.0.2 with four HTTP threads and one collector. Own its dispatcher and socket map explicitly so failed binds also release resources. The adapter uses version-specific lifecycle interfaces (`_dispatcher`, `wasyncore`); pinning and socket/thread cleanup tests are required when upgrading. Signal handlers only set an event. Startup takes an advisory local-filesystem lock beside the database before starting collection. Imports perform no database, hardware, or thread initialization.

Installed commands default to user state storage; compatibility source launchers preserve the checkout database path. A native systemd example uses an unprivileged service account, `StateDirectory`, `LoadCredential`, loopback binding, and an external SSH tunnel or TLS proxy. [systemd's execution configuration](https://github.com/systemd/systemd/blob/main/man/systemd.exec.xml) documents these facilities. Host-native deployment preserves the process/device view; a container deployment is deferred until host attribution semantics are explicitly designed and tested.

## Tradeoffs and consequences

Four HTTP workers bound concurrency; history queries still occupy workers and can delay other requests. Long-duration mixed history/live workloads need future benchmarks. There is no multi-worker mode, live reload, or zero-downtime drain. In-flight responses may end during shutdown. A service-manager deadline covers OS-level collector stalls that Python cannot cancel safely.

The database lock prevents accidental competing supported launchers, not every possible writer or hard-link alias. Paths and state must be on a local filesystem. Source module imports move under `gpuroster`; integrations must use the factory and supported lifecycle. No API/schema redesign or automatic live-server migration is included.

Dependency versions are not fully locked. Offline third-party frontend assets, demo data, longer soak testing, and optional real-GPU CI remain subsequent work. A syntax-checked unit is not proof that every host's device permissions or sandbox policy works unchanged.
