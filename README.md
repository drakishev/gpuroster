# GPU Roster

A lightweight dashboard for GPU utilization, memory, and process ownership on a shared NVIDIA host.

The current application combines Flask, `nvidia-smi`, psutil, and SQLite. It shows live metrics, a rolling utilization chart, and up to 31 days of utilization history. Session estimates are optional. This is an early development version; production deployment and collection architecture are still being improved.

![Dashboard showing four synthetic GPUs and example users](docs/images/phase-one-dashboard.png)

The screenshot uses synthetic data. It does not depict a real server or an available demo mode.

## Run locally

Use Python 3.10 or newer on Linux, an NVIDIA driver providing `nvidia-smi`, and permission to inspect the relevant processes. Optional session history needs the `last` utility and readable login records.

```bash
git clone https://github.com/drakishev/gpuroster.git
cd gpuroster
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
bash start.sh
```

Open **http://127.0.0.1:18081**. The launcher binds to loopback by default. Missing GPU tools produce visible partial-data status; they do not prevent the web interface from opening.

For a remote machine, keep the application on loopback and use an SSH tunnel:

```bash
ssh -N -L 18081:127.0.0.1:18081 user@example.invalid
```

Replace the example SSH destination with your own. Open the same local URL after the tunnel connects. Other users on the server can reach its loopback interface; configure authentication on a shared host.

## Access and privacy

Optional HTTP Basic authentication protects the page, static assets, and every API. Set credentials through your service's secret configuration or read them interactively without placing values in shell history:

```bash
read -r -p 'Dashboard username: ' GPUROSTER_AUTH_USER
read -r -s -p 'Dashboard password: ' GPUROSTER_AUTH_PASSWORD
printf '\n'
export GPUROSTER_AUTH_USER GPUROSTER_AUTH_PASSWORD
bash start.sh
```

Basic authentication requires an encrypted transport: use an SSH tunnel or a TLS-terminating reverse proxy. The application does not provide TLS or user account management. Non-loopback binding requires both credentials, and forwarded identity headers are not trusted. See the [access decision](docs/architecture/ADR-001-access-and-collection-safety.md).

Process owners, PIDs, and executable names remain available to authorized viewers. Command arguments and login/session details are disabled by default. Enabling them may disclose sensitive command arguments or client addresses to every viewer.

| Environment variable | Default | Purpose |
| --- | --- | --- |
| `GPUROSTER_HOST` | `127.0.0.1` | Listening address |
| `GPUROSTER_PORT` | `18081` | Listening port |
| `GPUROSTER_AUTH_USER` | unset | Basic-auth username; requires password |
| `GPUROSTER_AUTH_PASSWORD` | unset | Basic-auth password; requires username |
| `GPUROSTER_SHOW_COMMANDS` | `0` | Include truncated command arguments |
| `GPUROSTER_SHOW_SESSIONS` | `0` | Enable SSH connections and login/session APIs and panels |
| `GPUROSTER_COMMAND_TIMEOUT` | `3` | Timeout in seconds for each external command; greater than 0, at most 30 |
| `GPUROSTER_DB_PATH` | `gpu_stats.db` beside `app.py` | SQLite history file; parent directory must exist |

Boolean options accept `0`, `1`, `false`, or `true`. Environment variables are read at startup; `.env` files are not loaded automatically. Keep databases and credentials outside Git.

## Development and checks

Ordinary tests need no GPU, login records, credentials, or production database. Browser tests intercept metrics and CDN requests with synthetic fixtures.

```bash
venv/bin/python -m pip install -r requirements-dev.txt
venv/bin/python -m playwright install --with-deps chromium
venv/bin/python -m unittest discover -s tests -v
venv/bin/ruff check .
venv/bin/ruff format --check .
node --check static/dashboard.js
bash -n start.sh
```

Backend tests alone need only the runtime dependencies:

```bash
venv/bin/python -m unittest discover -s tests -p 'test_backend.py' -v
```

[CI](.github/workflows/ci.yml) installs dependencies in clean environments, tests Python 3.10/3.12/3.14, runs Chromium regressions, checks Python formatting/lint and JavaScript syntax, and audits runtime dependencies. See [validation evidence](docs/validation/phase-one.md) and [contribution guidelines](CONTRIBUTING.md).

## Changes from the baseline

- The default bind changed from all interfaces to loopback. Remote binding without configured authentication is rejected.
- Session endpoints return `403` unless explicitly enabled. The default process `command` contains an executable name instead of arguments.
- Unsupported GPU metrics are `null`, with collection failures described in `health.sources`. The dashboard distinguishes partial, stale, and unavailable values.
- Non-interactive SSH is labeled as such; it no longer implies VS Code. API fields changed from `has_vscode` to `has_noninteractive_ssh`, and the synthetic session source is now `noninteractive_ssh`.
- Importing `app` no longer initializes SQLite or starts a thread. Use `bash start.sh` or `venv/bin/python app.py`; `flask run` and a bare WSGI import do not start history collection.
- The database schema is unchanged. Existing history files remain readable.

## Current limits and next work

The launcher uses Flask's development server in a single process. [Flask's deployment guidance](https://flask.palletsprojects.com/en/stable/deploying/) explains why it is unsuitable for production serving. There is no supported multi-worker collector deployment yet.

Live API requests still poll hardware per client. A shared collector/cache, measured NVML comparison, and multi-client benchmarks are next architecture work. The first nonblocking CPU sample can be inaccurate. History still uses GPU indices and local-time labels; stable device identity, UTC time handling, and retention/query benchmarks remain planned work.

Login durations are estimates with known overlap and time-window accounting issues; do not use them for billing or usage quotas. SSH process visibility also depends on OS permissions.

Tailwind and Chart.js still load from external CDNs. Charts report load failures, but offline asset delivery and stronger script CSP remain future work. Runtime dependencies are not fully locked, and installable packaging and demo mode are not implemented yet.
