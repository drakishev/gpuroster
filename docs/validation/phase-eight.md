# Phase 8 validation

Date: 2026-10-01. Scope: Docker packaging and deployment, including read-only
real-GPU validation. Package version: **0.6.0**. Implementation commit:
`8cac56d84890d187b7f9a5af015571b5562503ff`.

The [Docker guide](../docker.md) gives setup, credential, persistence, upgrade,
and rollback procedures. [ADR-008](../architecture/ADR-008-container-deployment.md)
records the deployment decision and experiments. The application keeps its
existing collector, normalized snapshot, shared history queries, and SQLite
schema. Only packaging and an authenticated health probe are added to runtime.

## Local validation

- 106 backend tests and five packaging tests pass; Ruff lint/format checks and
  both Compose configurations pass.
- BuildKit builds the two-stage image successfully. The final image is
  142,615,897 bytes, using the digest-pinned Python 3.12.14 slim-bookworm base
  recorded in the Dockerfile and hash-locked Python dependencies.
- The context test excludes all 11 fake environment, credential, database, and
  generated-file fixtures. It uses a temporary copy of tracked files rather
  than the host's ignored database or credentials.
- Demo, no-GPU live, and real-GPU Compose smoke tests pass. They check protected
  assets/API routes, invalid authentication, advancing snapshots, non-root and
  read-only settings, no privilege escalation, and clean shutdown on both signals.
- Demo has four synthetic GPUs, no host PID/device access, and no database or
  state volume. No-GPU live mode stays healthy while reporting unavailable GPU
  metrics. Both live tests preserve a synthetic history row across recreation
  and reject a second collector using the same volume.
- The real-GPU test observes eight H200 GPUs through NVML, healthy GPU/process/
  history sources, host RAM totals, and **19 process owners matching the host**.
  No observed process disappeared before comparison. All GPUs had MIG disabled;
  no GPU workloads were launched and no GPU configuration changed.
- Passwords are absent from inspected container metadata and logs. Test output
  contains only aggregate counts/timings; real UUIDs, PIDs, account names,
  commands, and host addresses are not published.

Reproduce using the commands in the Docker guide. GPU tests require the optional
host toolkit setup. Every test uses temporary credentials, random loopback ports,
a random Compose project, and disposable state. It removes only its own test
resources. The original database checksum is unchanged, the existing environment
and host service were not modified, and the preserved Git baseline remains an
ancestor. The missing Compose/Buildx user CLI plugins were installed from official
release artifacts after checksum verification; the Docker daemon was not changed
or restarted.

## Measurements and environment

| Mode | Ready (s) | SIGINT exit (s) | SIGTERM exit (s) | GPUs |
| --- | --- | --- | --- | --- |
| Synthetic demo | 6.246 | 0.505 | 0.418 | 4 synthetic |
| Live without GPU access | 6.047 | 0.586 | 0.560 | 0 |
| Live with NVIDIA override | 6.826 | 0.578 | 0.640 | 8 real |

These are single final smoke runs, not latency percentiles or capacity tests.
Readiness includes Compose orchestration and health polling, excludes image
build/pull, and uses a test-only one-second health interval with 0.25-second
collection. Shutdown measurements include Docker CLI calls. Production defaults
are three-second collection and a 30-second health interval. Aggregate output:
[container-deployment-2026-10-01.json](../../benchmarks/results/container-deployment-2026-10-01.json).

Validated platform: Linux AMD64, Docker 27.5.1, Compose 5.5.1, Buildx 0.37.2,
NVIDIA Container Toolkit 1.18.2, NVIDIA driver 590.48.01. Explicitly selecting the
NVIDIA runtime resolved initialization failure under GPU reservation alone;
root execution did not. The final successful run retains all Compose restrictions.

## CI and remaining limits

The new GPU-independent container job builds and tests the image in ordinary CI.
Together with 16 existing browser tests, there are **127 distinct tests** plus
container integration checks. The backend matrix covers Python 3.10/3.12/3.14;
the image uses Python 3.12. CI requires all eleven jobs, including dependency
auditing and the reproducible release bundle, before merge. All eleven passed
at the implementation commit in [run 36891108520](https://github.com/drakishev/gpuroster/actions/runs/36891108520).
The [PR #9](https://github.com/drakishev/gpuroster/pull/9) final revision remains
gated on its own checks.

No UI behavior changes require new screenshots. The Docker build includes the
existing compiled assets and serves them under authentication. There is no
database migration or automatic import of the original database.

Enabled MIG, controlled multi-user GPU workloads, rootless Docker, other CPU
architectures, OS-image vulnerability scanning, registry publication, and long
container-specific soak tests remain outside this validation. Host account/PID
visibility is an explicit live deployment tradeoff. Short tests do not establish
production capacity, long-term memory stability, or every host policy's behavior.
