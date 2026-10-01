# ADR-008: A single collector in Docker with explicit host attribution

Status: accepted, 2026-10-01. Extends the native deployment in [ADR-004](ADR-004-production-deployment.md).

## Context and requirements

Deployment should include Python, application dependencies, and compiled assets,
while keeping credentials and history outside the image. The existing single
collector, ownership lock, and HTTP worker model must survive containerization.
GPU process IDs originate on the host; interpreting them in a private container
PID namespace risks missing or misattributing processes. Ordinary CI must still
work without NVIDIA hardware.

## Alternatives and evidence

- Native systemd remains supported and provides the full host login/session
  view. Docker offers a separate installation path, with explicit device and
  process access rather than an automatic migration.
- A CUDA development image provides tools the application does not need. NVML
  and `nvidia-smi` are supplied by the host driver through the NVIDIA runtime;
  the application only needs Python and the existing NVML binding. A pinned
  Python slim base produced a 142,615,897-byte local image. No comparative CUDA
  image size or performance benchmark was performed.
- Private PID namespaces isolate processes but cannot reliably resolve host
  GPU PIDs through container `/proc`. The GPU override shares host PIDs and
  mounts `/etc/passwd` read-only. The final real-host smoke run matched all 19
  observed process owners across eight GPUs, with none disappearing before
  comparison. It also matched host RAM totals. This is observed attribution,
  not a controlled workload or proof for every host policy.
- Requesting GPUs without selecting the NVIDIA runtime failed NVML initialization
  with `NVMLError_Unknown` on the validation host. Root and non-root variants
  failed. Explicit `runtime: nvidia` passed as non-root with all capabilities
  dropped. The experiment supports explicit runtime selection here; it does not
  establish the internal driver/runtime cause or imply elevated privileges are
  required. NVIDIA documents the runtime and `utility` capability in its
  [Docker guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/docker-specialized.html).
- Environment-based passwords can appear in container metadata. Compose mounts
  a password file; the interactive helper protects it with a private host parent
  directory and a container-readable file. File-backed secrets preserve host
  permissions, as described in [Docker's reference](https://docs.docker.com/reference/compose-file/services/#secrets).

Hypothesis: the existing single-process application can run unprivileged in a
read-only container without losing authentication, ownership, persistent history,
or host process attribution. Acceptance requires passing demo and no-GPU tests,
real-GPU owner comparison, recreation with retained history, rejection of a
second owner, and clean shutdown on both supported signals. All passed; see
[Phase 8 validation](../validation/phase-eight.md) for measurements and limits.

## Decision

Build the wheel in a separate stage using hash-locked build tools. Install it
and hash-locked runtime dependencies into a venv, then copy only that environment
into a digest-pinned Python 3.12 slim runtime. Include packaged frontend assets;
neither Node nor application build tools are required at runtime. Refresh base
digests and locks deliberately. [Docker's build guidance](https://docs.docker.com/build/building/best-practices/)
describes digest pinning and non-root execution.

Use one Compose live service with a persistent history volume and one isolated
demo service with no state volume. Run as UID/GID 10001 by default, with a
read-only root filesystem, dropped capabilities, no privilege escalation, bounded
memory/process counts, a small temporary filesystem, and loopback publishing.
Keep commands and sessions disabled. The optional GPU override selects the
NVIDIA runtime and devices, shares host PIDs, and mounts only account names.

Probe the authenticated cached API for a fresh snapshot; do not poll hardware
from health checks. An unavailable GPU source does not make a publishing API
unhealthy. Continue using the existing ownership lock and signal-driven shutdown.
Allow 120 seconds for shutdown at the Compose level.

Allowlist Docker build inputs. A real Docker context test seeds 11 synthetic
private files and verifies their exclusion; it caught a directory-negation rule
that initially admitted nested fixtures. CI now builds the image and exercises
demo and no-GPU live deployment before emitting the existing release bundle.

## Tradeoffs and consequences

Host PID sharing reduces isolation. Local account names are visible inside the
GPU container, while host session records are not mounted. Native deployment
remains the option for full session collection. Administrators should choose
unused service IDs and preserve volume ownership if changing them. Host policy
can still deny process or device access.

Linux AMD64 with rootful Docker is validated. Other architectures, rootless
Docker, enabled MIG, and different NVIDIA configurations require validation.
System CPU/RAM counters describe the host kernel rather than cgroup allocation.
The context test guards known fixtures, not every possible sensitive filename.
Image byte reproducibility and an OS-layer vulnerability audit are not claimed.

No registry publication, daemon/driver reconfiguration, schema migration, or
existing service restart is introduced. Users build from the reviewed source
and explicitly manage migration, backups, and upgrades. Docker restarts exited
processes under the chosen policy; an unhealthy status alone does not restart
the container. One monitoring deployment per host remains the supported model.
