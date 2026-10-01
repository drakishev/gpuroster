# Phase 6 validation

Date: 2026-10-01. Scope: separate server memory measurements, hash-locked
dependencies, and reproducible build bundles. Package version: 0.5.1.

The [isolated memory experiment](../research/server-memory.md), preserved in
[PR #6](https://github.com/drakishev/gpuroster/pull/6), completed 36,000 requests
over ten minutes and then observed one idle minute. No HTTP/validation/write
failures occurred. The final five loaded minutes stayed in a 0.82 MiB server USS
band, with seven threads and descriptor counts returning to baseline. The
acceptance criteria passed; no runtime memory change was justified. The report
records workload limits and does not claim long-term or native-driver leak freedom.

Clean Python 3.12 environments installed the runtime and combined
runtime/build/dev/audit sets using required hashes and wheels only. `pip check`,
85 backend tests, and the runtime vulnerability audit passed. A changed
requirement caused the lock check to fail without modifying generated locks; a
deliberately invalid dependency hash was rejected by pip. The original inputs
were restored after these negative checks.

Five packaging tests cover archive metadata normalization with preserved file
contents/modes, mismatched versions, dirty source trees, ignored-file exclusion,
and refusal to overwrite existing output. Clean fixture builds of 0.5.1 produced
matching source/wheel hashes across two independent builds. The exact wheel
installed with the runtime lock and passed live ownership and isolated demo
smoke tests with SIGINT/SIGTERM. The normal 16-test Chromium suite is also part
of validation, for 106 distinct tests across all three suites.

CI uses the locks on Python 3.10/3.12/3.14, retains a separate latest-compatible
runtime job, checks lock and asset drift, audits dependencies, and tests clean
package installation. The final artifact job depends on every validation job,
compares two builds, verifies checksums, and smoke-tests the exact wheel before
upload. Repository permissions remain read-only. Final CI must pass before
merge; no tag, GitHub Release, or PyPI publication is performed by this workflow.

The original virtual environment, database, and host service remain untouched.
Only temporary environments/databases and synthetic data are used. The preserved
baseline remains in Git history. Build archives come from exported commits and
exclude ignored databases, local credentials, and environments.

Remaining work includes target-host/driver/MIG validation, longer changing-
identity and native-worker memory profiles, and a release publication/signing
policy. Hash locks do not pin OS, interpreter, or driver dependencies. Artifact
repeatability is checked within one recorded toolchain; manifests are unsigned,
and GitHub Actions artifacts expire after 30 days.
