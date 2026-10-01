# Linux deployment

Use the installed `gpuroster` command under a service manager. For actual monitoring, leave `GPUROSTER_DEMO` unset or `0` and omit `--demo`; demo mode deliberately does not monitor the host or persist history. It runs Waitress with four request threads, one collector thread, and at most one NVML worker. A bare Flask/WSGI import does not start collection. Multiple web workers and network-filesystem history are not supported. See [ADR-004](architecture/ADR-004-production-deployment.md).

## Install the package

Use a reviewed [CI build bundle](releases.md), or build one from a clean Git checkout with Python 3.10 or newer. The wheel includes Python code, templates, and dashboard JavaScript; it does not contain a database or credentials.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements/build.lock
.venv/bin/python scripts/build_release.py --output dist/release
(cd dist/release && sha256sum -c SHA256SUMS)
sudo python3 -m venv /opt/gpuroster/venv
sudo /opt/gpuroster/venv/bin/python -m pip install --require-hashes --only-binary=:all: -r dist/release/runtime.lock
sudo /opt/gpuroster/venv/bin/python -m pip install --no-deps dist/release/gpuroster-0.5.1-py3-none-any.whl
sudo /opt/gpuroster/venv/bin/python -m pip check
/opt/gpuroster/venv/bin/gpuroster --version
```

Keep the installed environment separate from the source checkout. The bundled lock selects exact runtime versions and verifies dependency hashes. Python, operating-system, and NVIDIA driver versions remain outside this lock. Save the entire reviewed bundle and the previous environment's dependency inventory for rollback; CI artifacts expire after 30 days.

The host needs its NVIDIA driver and accessible NVML library. The CLI fallback needs `nvidia-smi` in the service's PATH. Run as an unprivileged account with the device/process permissions required by your host. Missing access appears as partial or unavailable data. Avoid granting root just to improve process attribution.

## Configure systemd

The example requires systemd 247 or newer for `LoadCredential`. Review paths and host-specific GPU permissions before installation. These are installation instructions, not actions performed by development tests.

```bash
sudo useradd --system --user-group --no-create-home --shell /usr/sbin/nologin gpuroster
sudo install -d -m 0700 /etc/gpuroster
sudo install -m 0600 deploy/gpuroster.conf.example /etc/gpuroster/gpuroster.conf
sudo install -m 0600 /dev/null /etc/gpuroster/password
sudo systemd-ask-password 'GPU Roster dashboard password:' | sudo tee /etc/gpuroster/password > /dev/null
sudo install -m 0644 deploy/gpuroster.service /etc/systemd/system/gpuroster.service
sudo systemctl daemon-reload
sudo systemctl enable --now gpuroster
```

Skip account creation if the service account already exists. Choose a nonempty password; startup rejects an empty or unreadable credential. With neither explicit password option set, GPU Roster reads `dashboard-password` from systemd's `CREDENTIALS_DIRECTORY`. This works without newer unit-file path specifiers. The secret is never placed in a service argument or repository file. Change non-secret options with `sudoedit /etc/gpuroster/gpuroster.conf`; restart after configuration or password changes. Keep password options out of this example's environment file so `LoadCredential` supplies the value.

systemd creates `/var/lib/gpuroster` for history and its ownership lock, with mode 0700. The unit keeps the filesystem read-only except for managed state and temporary directories. It deliberately retains access to NVIDIA devices and host process information; `PrivateDevices=yes` or a hidden `/proc` would interfere with collection. OS policy may still restrict owners, command names, or login records. Sessions and command arguments remain disabled by default.

## Connect and verify

The example listens on loopback. For remote use, connect through an SSH tunnel as described in the [README](../README.md), or terminate TLS at a reverse proxy. Configure a real hostname and certificate on the proxy, forward the original Host and Authorization headers, and proxy to `http://127.0.0.1:18081`. Keep application authentication enabled even on loopback. Never expose Basic credentials over unencrypted remote HTTP. [Flask's nginx guide](https://flask.palletsprojects.com/en/stable/deploying/nginx/) describes the proxy topology; GPU Roster does not use forwarded headers to grant access.

Check service status and request a snapshot with an interactive password prompt:

```bash
sudo systemctl status gpuroster
curl --fail --user viewer http://127.0.0.1:18081/api/stats
```

Snapshot output contains host metrics and process owners: inspect it locally and do not paste it into public issues. HTTP 503 `collector_not_ready` is expected before the first publication. Afterwards, HTTP 200 means a snapshot is available, not that all sources are healthy. Check `health.sources`, ages, and sequence advancement. A host without NVIDIA support should serve the page with visibly unavailable GPU metrics.

`journalctl -u gpuroster` contains startup/shutdown and safe collection error codes. The launcher does not log credentials, raw command errors, or an access log. Third-party server errors may include request-related details; keep service logs private. Styles and Chart.js are packaged and served by the application; browsers need no CDN access.

## Ownership and shutdown

The launcher takes a nonblocking advisory lock beside the configured database. A second launcher pointing at the same resolved path exits before starting hardware collection. The empty `.lock` file stays after exit to preserve inode ownership; do not delete it while a process is running. The lock is not a defense against manual database writers, alternate hard links, or launchers that bypass it. Run one instance per monitored host.

SIGINT and SIGTERM request shutdown without interrupting locks. The HTTP event loop stops accepting work, request workers receive up to five seconds to finish application work, and the collector stops and closes its GPU worker. Buffered responses can be interrupted; this is not zero-downtime draining. Ordinary collection shutdown waits at most `3 * GPUROSTER_COMMAND_TIMEOUT + 3` seconds. OS process inspection can still hang beyond application control. systemd's 120-second stop deadline and `KillMode=mixed` provide a final process-group cleanup. No automatic reload or forked web workers are configured.

## Existing database and rollback

The checkout launcher (`bash start.sh` or `python app.py`) retains the original `gpu_stats.db` location. The installed command uses the user's state directory unless `GPUROSTER_DB_PATH` is set; the example unit explicitly uses `/var/lib/gpuroster/history.db`.

Before changing deployments, stop the old service and make a private backup of its SQLite database. Copy it into the new state directory with service-account ownership and mode 0600, or deliberately configure its existing path and suitable write permissions. Do not run old and new collectors together. No deployment command in this repository copies or migrates an existing database automatically.

The first collector write retains the Phase 2 additive `gpu_uuid` migration and 31-day retention policy. Phases 3–6 make no new schema change. To roll back, stop the new process, reinstall the saved previous environment, and use the appropriate database backup if necessary. A software rollback cannot restore rows removed by normal retention. Keep backups, credential files, and real snapshots outside the repository.

## Validate without installing a service

```bash
python3 -m venv /tmp/gpuroster-install
/tmp/gpuroster-install/bin/python -m pip install --require-hashes --only-binary=:all: -r dist/release/runtime.lock
/tmp/gpuroster-install/bin/python -m pip install --no-deps dist/release/gpuroster-0.5.1-py3-none-any.whl
python3 scripts/smoke_install.py /tmp/gpuroster-install/bin/gpuroster
```

The smoke test starts real HTTP processes in temporary directories with GPU tools unavailable. It validates page/assets/API/history access, authentication, exclusive database ownership, signals, and restart. Ordinary CI does not install systemd units or require an NVIDIA device. The example unit is syntax-checked separately; its sandbox and device access still need validation on the target host.
