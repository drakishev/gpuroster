# Upgrading from earlier versions

These notes moved here from the README. They describe behavior changes since
the original baseline and the one-time Phase 2 database migration. For
current installation and configuration, see the [README](../README.md).

## Changes from the baseline

- The default bind changed from all interfaces to loopback. Remote binding without configured authentication is rejected.
- Session endpoints return `403` unless explicitly enabled. The default process `command` contains an executable name instead of arguments.
- Unsupported GPU metrics are `null`, with collection failures described in `health.sources`. The dashboard distinguishes partial, stale, and unavailable values.
- Non-interactive SSH is labeled as such; it no longer implies VS Code. API fields changed from `has_vscode` to `has_noninteractive_ssh`, and the synthetic session source is now `noninteractive_ssh`.
- Imports no longer initialize SQLite or start threads. Use `gpuroster`, `python -m gpuroster`, or the compatibility checkout launcher `bash start.sh`. A bare WSGI factory (`gpuroster.app:create_app`) does not start collection or acquire database ownership.
- Phase 1 preserved the database schema. Phase 2 adds a nullable `gpu_uuid` column on the collector's first write. Existing records remain labeled as legacy index history; they are not assigned to a current GPU. The application database has not been migrated merely by checking out or importing this code.

## Phase 2 API and database migration

Install the updated requirements before restarting. Stop the old instance and back up its SQLite file before launching the new collector. Use one web process; do not run multiple instances against the same database. The migration adds a column and preserves rows within the existing 31-day retention policy. Ordinary writes still remove expired rows. Old explicit-column inserts remain compatible, but rolling back cannot restore UUID-based history semantics.

`/api/stats` now returns `schema_version: 2`, `sequence`, and the actual collection timestamp. GPU records include `uuid`; processes include `gpu_uuid`, and an unmapped index is `null`. Live `history` is keyed by UUID with numeric Unix-second timestamps; `history_devices` supplies display indices. Historical datasets have stable `id` values, UTC ISO labels, and explicit gaps. GPU/process memory fields retain their old API names but mean MiB/GiB; system RAM also exposes exact byte counts. See the [snapshot contract](architecture/snapshot-v2.md).

Before the first snapshot, `/api/stats` returns `503 collector_not_ready`. Starting the collector is explicit: importing a WSGI app alone will not start it. Session endpoints use cached evidence and return 503 when required sources are unavailable or stale. Truncated or malformed records are marked partial in source health.

Connected time is the union of a user's session intervals, clipped to each window. Simultaneous terminals count once. “Today” uses the configured timezone (including DST); week/month mean the preceding 7/30 elapsed days. Non-interactive SSH contributes only currently visible evidence and is not persisted as an authoritative session ledger.

## Dashboard redesign (October 2026)

The API, database, and configuration are unchanged. Visible dashboard text
changed ("Recent samples", "7 days", "Showing 1 session"), and times now use
`GPUROSTER_TIMEZONE` with the zone named next to them. The API remains UTC. Use
the JSON API rather than scraping page text. See
[ADR-009](architecture/ADR-009-dashboard-experience.md).
