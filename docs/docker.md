# Docker deployment

The image contains the application, Python dependencies, NVML binding, and
compiled dashboard assets. Docker builds need no GPU or Node installation.
Runtime data lives outside the image. Linux AMD64 is the validated platform.
Use Docker Engine with its Compose plugin. Live monitoring also needs the host
NVIDIA driver and a registered NVIDIA Container Toolkit runtime; see
[NVIDIA's installation guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
The repository does not reconfigure Docker or drivers.

## Start the demo

From the checkout, create a password interactively, then start the demo:

```bash
python3 scripts/container_password.py
docker compose up --build -d demo
```

Open **http://127.0.0.1:18081**, with username **viewer** and your chosen password.
There is no built-in password. Demo uses synthetic data, no GPU or host process
access, and no persistent database. Stop it before using the same port for live
monitoring: `docker compose stop demo`.

The helper creates `private/container/dashboard-password`, excluded from Git and
the Docker context. Its parent directory has mode 0700; the file has mode 0444
so the container's non-root UID can read the mounted secret. The private parent
protects the file from other host users; preserve its permissions. Compose file
secrets retain host ownership/mode; YAML `uid` and `mode` do not remap them. See
[Docker's secret reference](https://docs.docker.com/reference/compose-file/services/#secrets).
The helper refuses to replace an existing file. A secret manager can supply
another readable file through `GPUROSTER_PASSWORD_FILE`.

## Monitor real GPUs

```bash
docker compose -f compose.yaml -f compose.gpu.yaml up --build -d gpuroster
docker compose -f compose.yaml -f compose.gpu.yaml ps
```

The override selects the `nvidia` runtime and all GPU devices, with the driver's
`utility` capability for NVML and `nvidia-smi`. On the validation host, a GPU
reservation alone produced an NVML initialization error; selecting the runtime
worked as an unprivileged user. See
[NVIDIA's runtime guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/docker-specialized.html).

Host PID sharing makes GPU process IDs refer to the correct `/proc` entries. A
read-only `/etc/passwd` mount resolves host account names. This grants host
process visibility and reduces PID isolation; use it on a trusted monitoring
host. The app runs as UID/GID 10001 with all capabilities dropped and privilege
escalation disabled. Choose unused service IDs via `GPUROSTER_UID` and
`GPUROSTER_GID` build arguments if those IDs belong to another host account.
Existing volume ownership must match the image user after an ID change.

Host policy can still restrict metrics and process owners. Command arguments
and sessions stay disabled; host login records are not mounted. Use the
[native deployment](deployment.md) for full host login/session collection. MIG
instances are not enumerated separately; enabled-MIG behavior remains unvalidated.

For no-GPU deployment testing, `docker compose up --build -d gpuroster` starts
the base live service with unavailable GPU metrics. Use the GPU override for
real monitoring. Adding GPU access without addressing PID/account mapping can
misattribute host process IDs to unrelated container processes.

## Configuration and access

Compose publishes port 18081 only on host loopback and requires authentication.
Use an SSH tunnel or TLS reverse proxy as described in the
[deployment guide](deployment.md), retaining the Authorization header.
Export non-secret settings or put them in an ignored `.env` file:

| Setting | Default | Purpose |
| --- | --- | --- |
| `GPUROSTER_HTTP_PORT` | `18081` | Published loopback port |
| `GPUROSTER_AUTH_USER` | `viewer` | Dashboard username |
| `GPUROSTER_PASSWORD_FILE` | `./private/container/dashboard-password` | Host secret file path, not its contents |
| `GPUROSTER_TIMEZONE` | `UTC` | IANA timezone for today's history |
| `GPUROSTER_COLLECT_INTERVAL` | `3` | Collection interval in seconds |
| `GPUROSTER_IMAGE` | `gpuroster:local` | Locally built image tag |
| `GPUROSTER_UID` / `GPUROSTER_GID` | `10001` | Non-root build-time IDs |

Defaults allow one collector, 128 processes and 512 MiB memory, with a read-only
root filesystem and a 16 MiB temporary filesystem. Adjust limits for larger
workloads. CPU/RAM use kernel system counters, not per-container accounting;
Docker Desktop exposes the counters of its Linux VM.

Container health requires an authenticated, fresh published snapshot. Missing
GPUs can coexist with a healthy HTTP/collector process; inspect `health.sources`
for data-source status. A stalled collector becomes unhealthy. Docker's restart
policy restarts an exited process, not one merely marked unhealthy.

## History, upgrades, and rollback

The live service stores its database and ownership lock in the project-scoped
`history` named volume at `/var/lib/gpuroster`. It survives container recreation.
The empty lock file intentionally remains after exit. Do not scale the service:
a second owner sharing that volume is rejected, while a different volume would
duplicate collection. Run one monitoring deployment per host.

Before upgrading, preserve the previous image tag, stop the service, and back up
the volume. Docker's [volume backup guide](https://docs.docker.com/engine/storage/volumes/#back-up-restore-or-migrate-data-volumes)
describes volume copying. Keep backups private. Check out a reviewed commit,
rebuild/recreate with the commands above, then check health and source status.
Rollback uses the saved image and, when needed, the backup; it cannot recover
rows deleted by normal retention. Version 0.6.0 adds no schema migration.

The existing checkout database is never imported automatically. To migrate,
stop the old collector, back up its database, and copy it into the named volume
with the image's UID/GID and mode 0600 before starting the container. Do not run
old and new collectors together. Stop sends SIGTERM with a 120-second grace
period; SIGINT also works. To remove containers while retaining history:

```bash
docker compose -f compose.yaml -f compose.gpu.yaml --profile '*' down
```

Adding `--volumes` deletes history. After updating the private password file,
recreate the service: an atomic file replacement can leave an existing bind
mount attached to the old inode.

## Validate

```bash
docker build -t gpuroster:local .
python3 -m scripts.check_container_context
python3 -m scripts.smoke_container --image gpuroster:local
# Optional, on a configured Linux/NVIDIA host:
python3 -m scripts.smoke_container --image gpuroster:local --gpus
```

Tests use random project names, temporary credentials/volumes, and random loopback
ports. They check authenticated assets/API access, advancing snapshots, non-root
settings, demo isolation, degraded no-GPU operation, persistence across recreation,
duplicate ownership, and both signals. The GPU case compares process owners with
the host and emits counts only; synthetic history goes only into its test volume.

The context check copies tracked files into a temporary directory and verifies
Docker excludes 11 fake credential/database/environment fixtures. It never
copies the original ignored database or credentials. Ordinary CI builds and
tests the image without a GPU. CI does not publish to Docker Hub or GHCR. Review
and refresh the pinned base digest and dependency locks during maintenance.
