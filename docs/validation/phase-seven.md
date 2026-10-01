# Phase 7 validation

Date: 2026-10-01. Scope: target GPU collection, native-worker resource use, and
changing GPU identity retention. Runtime/package version remains 0.5.1; no
application, schema, dependency, deployment, or GPU configuration changes.

[PR #8](https://github.com/drakishev/gpuroster/pull/8) adds an optional read-only
[hardware validation command](hardware.md). It reports aggregate measurements
without UUIDs, PIDs, usernames, commands, or host addresses. Unsupported values
stay distinguishable from zero; allowlisted failure codes support diagnosis
without exposing driver exception text. Hiding `nvidia-smi` from PATH produced
the expected nonzero `command_missing` result.

The [real-host experiment](../research/target-host.md) observed eight H200 GPUs
with driver 590.48.01 and MIG disabled. All 900 samples and ten worker recoveries
passed. NVML/CLI static properties agreed for all devices. Worker USS remained
flat; the second-half parent USS band was 0.023 MiB. Threads and descriptors did
not accumulate. The source report records timings, RSS, method, and limits.

The [synthetic identity experiment](../research/identity-churn.md) replaced all
eight devices every tick across ten history windows: 9,600 distinct identities.
Retained state stayed within the expected bound, and all four maps emptied after
expiry. The second-half traced allocation band was 2,248 bytes. This is an
accelerated model test, not an hour-long end-to-end run.

There are **13 new backend regressions**, for **98 backend tests**. They cover
multi-GPU identity mapping, unsupported/denied metrics, process-source failure,
safe driver errors, validation-report privacy, static comparison and numeric
failures, memory-window interpretation, safe CLI failure codes, and repeated
history identity churn. With 16 browser and five packaging tests, CI exercises
**119 distinct tests** without a GPU. All ten CI jobs passed at implementation
commit `3883301`; the PR remains gated on its final commit's checks. Source
archives now include benchmark modules needed by the validation tests.

The original database checksum is unchanged, the original environment and host
service are untouched, and the preserved baseline remains in Git history. The
experiments support keeping the existing runtime implementation. Enabled-MIG,
container/service-account permissions, controlled GPU-process attribution,
longer driver failure/soak coverage, and release publication remain future work.
