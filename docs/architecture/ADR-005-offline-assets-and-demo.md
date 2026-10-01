# ADR-005: Local frontend assets and an isolated synthetic demo

Status: accepted, 2026-10-01.

## Context and requirements

The installed dashboard still required external CDNs, including Tailwind's browser compiler. That added a network dependency and prevented a restrictive script policy. New users also needed a monitored host to see populated charts. A demo must show the real UI/API without exposing host metrics, mixing generated history with real history, or requiring Node at runtime.

## Alternatives and evidence

CDN fallbacks retain external requests and failure modes. Hand-maintained utility CSS duplicates Tailwind behavior; replacing the styling system broadens the change unnecessarily. Compile the existing Tailwind 3.4.17 classes and package the existing Chart.js 4.4.3 UMD distribution, with pinned npm inputs and license notices including bundled color code. See the [rebuild workflow and upstream references](../../frontend/README.md).

Browser-only demo mocks bypass the API/collector lifecycle. A generated SQLite database exercises persistence but adds disk management and risks selecting a real database path. Instead, use synthetic providers inside the same scheduled collector and an in-memory history provider generating bounded chart buckets. Share range/timezone calculation with real history storage. Hardware compatibility and SQLite migration retain separate tests.

Success criteria: real charts/styles work with external requests blocked and no normal-update CSP violations; the demo works without hardware and cannot read/write the configured database. Browser tests satisfy the frontend checks. Isolation tests replace GPU/system/process/subprocess/SQLite boundaries with failures and verify healthy synthetic results, including optional sessions. Installed smoke tests preserve a database sentinel and create no ownership lock or state directory.

## Decision

Ship CSS, Chart.js, and notices in wheels and source distributions. `npm ci --ignore-scripts` plus the asset build reproduce committed runtime files; CI rejects drift and audits dependencies. Node is a maintenance dependency. Scripts, styles, and fetches use the application origin only, without inline-script or eval exceptions. DOM style properties support chart sizing/progress bars; inline template styles move into CSS. Chart load failure still leaves tables usable.

Expose `gpuroster --demo` and `GPUROSTER_DEMO=1`, default off. Supply four generated GPUs, example identities, synthetic CPU/RAM and optional sessions, a prefilled rolling chart, and generated historical buckets. Skip persistent storage and database locking. Live mode retains its collectors, SQLite, and ownership lock.

Keep authentication, bind restrictions, and session defaults common to both modes. Show a persistent synthetic-data banner and DEMO status. Every response carries `X-GPU-Roster-Mode`; stats/history JSON includes `mode: demo` or `mode: live`. These additive fields retain schema version 2. Mode is selected at startup, not through a request parameter or UI toggle.

## Tradeoffs and consequences

Packaged CSS/Chart.js add approximately 216 kB before compression, plus notices. Maintainers must rebuild after changing class names/dependencies and audit pinned-code upgrades. Demo history is illustrative and regenerated, not a usage record or exact replay. It cannot certify driver support or target-host permissions.

Dependency installation still requires internet or a prepared wheelhouse; offline runtime does not imply an offline installer. [Browser measurements](../benchmarks.md#offline-demo-browser) cover one machine and a short run, not a memory-leak or production-capacity guarantee.
