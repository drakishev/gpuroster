# ADR-001: Access boundaries and observable collection failures

Status: accepted for Phase 1

Date: 2026-10-01

## Context and requirements

The baseline served host metrics, process arguments, and session details without authentication on every interface. Host-controlled strings entered HTML and inline event handlers. External commands had no deadlines, and failures could disappear behind apparently healthy dashboard values.

Phase 1 must protect access before collection, reduce sensitive data exposure, render untrusted values safely, and make failures visible. It must preserve the existing database schema and remain testable without a GPU or real login records.

## Alternatives considered

| Choice | Benefit | Tradeoff |
| --- | --- | --- |
| Require a correctly configured authentication proxy | Supports existing organizational identity | An accidental direct connection can bypass a proxy-only boundary |
| Add application accounts, sessions, and OIDC now | Per-user identity and richer authorization | Requires account/session lifecycle and deployment decisions beyond this phase |
| Loopback by default, optional application Basic auth | Small, testable boundary; works through SSH or TLS proxy | Shared credentials, no roles or logout flow; transport encryption remains external |
| Escape HTML strings at each interpolation | Smaller frontend diff | Every text, attribute, selector, and handler context needs correct escaping |
| Construct DOM nodes with text content | Untrusted values remain text; event handlers use closures | Rewrites dynamic rendering and needs browser regressions |

## Decision

Default to loopback. Without credentials, accept only loopback clients and loopback/localhost Host values. This prevents a forged forwarded header or a DNS-rebinding hostname from granting local access. If credentials are configured, require HTTP Basic authentication on all routes before collectors run. Require credentials for non-loopback launcher binding. Do not interpret proxy identity headers.

Keep process arguments and all session collection behind separate opt-in flags. Preserve process names, owners, and allocation metrics for the roster's core purpose. Verified SSH executable metadata and ownership must support a connection estimate; a non-TTY process is not proof of an editor session. OS visibility restrictions may omit connections.

Use DOM text nodes and property setters for host strings, and registered event listeners for actions. Add no-store, framing, MIME-sniffing, referrer, and limited CSP response protections. CDN scripts remain allowed by the current policy; this is not a comprehensive script CSP.

Bound every external command with a configurable timeout and safe error codes. Do not return raw subprocess diagnostics or inherit dashboard credentials into collector children. Preserve unsupported measurements as null. Expose source and sampler health, rate-limit repeated safe log messages, and display partial/stale status. Browser polling waits for completion and has an abort deadline.

Start the history sampler explicitly and at most once per application process, rather than at import. Close SQLite connections, make history reads read-only, and advance write timing only after a successful commit so failed writes can retry.

## Evidence

The [regression suite](../validation/phase-one.md) checks authentication before collection, privacy defaults, malformed GPU output, actual subprocess deadlines, database retry behavior, and spoofed SSH metadata. Chromium tests exercise hostile strings, missing chart scripts, failed requests, staleness, recovery, and delayed responses using synthetic data.

These decisions address security and correctness requirements; no hardware-performance improvement is claimed. NVML, shared-cache concurrency, and production serving decisions require separate experiments.

Primary guidance: [Flask web security](https://flask.palletsprojects.com/en/stable/web-security/), [HTTP authentication and transport](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Authentication), and [Flask deployment](https://flask.palletsprojects.com/en/stable/deploying/).

## Consequences and limits

Existing remote deployments must configure authentication and encrypted transport or switch to SSH tunneling. Basic auth is a single-viewer-role mechanism and does not provide account management or brute-force rate limiting. Other local users can reach loopback, so shared hosts should configure credentials too.

API consumers must handle null measurements, health metadata, disabled session routes, and renamed non-interactive SSH fields. The SQLite schema does not change. Importing the module has no collection or persistence side effects; production worker lifecycle is deferred.

Command deadlines bound subprocess waits, not every possible psutil/kernel operation or total HTTP request duration. Multiple clients still trigger independent hardware collection. Session overlap/time-window accounting, first CPU sample correctness, UTC history, GPU UUID identity, offline assets, and production deployment remain separate work.
