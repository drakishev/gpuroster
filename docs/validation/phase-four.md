# Phase 4 validation

Recorded 2026-10-01 on `feat/offline-demo`; baseline main was the merged production-serving work.

- 72 backend/collector/history/demo/server tests and 16 Chromium tests pass locally.
- Demo isolation tests prohibit subprocess, psutil, NVML-worker, and SQLite calls, including with sessions enabled. The configured database sentinel remains unchanged. Demo startup/shutdown creates no database lock; live ownership tests remain intact.
- Browser tests load real Chart.js/CSS from the production server with external requests blocked. They cover generated metrics, historical charts, optional sessions, persistent demo labeling after failure, CSP enforcement, and narrow-screen layout. The screenshot contains generated identities only.
- A clean wheel install outside the checkout passes live and demo smoke checks without GPU tools. Both demo entry points, authentication, packaged assets/licenses, state isolation, restart, SIGINT, and SIGTERM are covered. Demo exits measured approximately 0.23 seconds locally.
- Asset builds use the npm lock and retain upstream licenses. Python/frontend audits report no known vulnerabilities at validation time. Ruff, JS/shell syntax, packaging, and whitespace checks pass.
- [Browser measurements](../benchmarks.md#offline-demo-browser) use five fresh contexts and 1,000 accelerated refreshes, recording load/update latency, retained JavaScript heap, and zero external requests/errors. This is not a long-duration soak or a claim of constant memory.

The original database checksum is unchanged, its virtual environment was not modified, and no existing service was restarted. CI needs no GPU or private host data. It rebuilds assets to detect drift and tests installed live/demo launchers on Python 3.10 and 3.14 alongside existing checks.

Remaining work includes longer mixed history/live loads, runtime dependency locking, optional hardware CI, release automation, and contribution/security guidance. Demo success does not establish real driver or host-permission compatibility.
