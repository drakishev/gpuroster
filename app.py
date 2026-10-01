import csv
import hmac
import ipaddress
import math
import os
import re
import sqlite3
import stat
import subprocess
import threading
import time
from collections import defaultdict, deque
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import psutil
from flask import Flask, jsonify, render_template, request

from settings import load_settings

app = Flask(__name__)
app.config.update(load_settings())


def _is_loopback(address):
    if address == "localhost":
        return True
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


@app.before_request
def protect_access():
    """Authenticate before collection; never trust forwarded identity headers."""
    if app.config["AUTH_USER"]:
        auth = request.authorization
        username = auth.username if auth and auth.type == "basic" else ""
        password = auth.password if auth and auth.type == "basic" else ""
        valid_user = hmac.compare_digest(
            (username or "").encode(), app.config["AUTH_USER"].encode()
        )
        valid_password = hmac.compare_digest(
            (password or "").encode(), app.config["AUTH_PASSWORD"].encode()
        )
        if not (valid_user and valid_password):
            return (
                jsonify(error="authentication_required"),
                401,
                {"WWW-Authenticate": 'Basic realm="GPU Roster", charset="UTF-8"'},
            )
    else:
        try:
            hostname = urlsplit("http://" + request.host).hostname or ""
        except ValueError:
            hostname = ""
        if not _is_loopback(request.remote_addr or "") or not _is_loopback(hostname):
            return jsonify(error="local_access_only"), 403
    if request.path in {"/api/login_stats", "/api/sessions", "/api/connections"}:
        if not app.config["SHOW_SESSIONS"]:
            return jsonify(error="session_visibility_disabled"), 403


@app.after_request
def protect_responses(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
    )
    return response


# Rolling 120-point history per GPU (sampled every 3s = last 6 minutes)
_gpu_history = defaultdict(lambda: deque(maxlen=120))
_gpu_ema = {}  # EMA smoothed value per GPU index
_history_lock = threading.Lock()
EMA_ALPHA = 0.2  # lower = smoother; 0.2 means 80% weight on old value

# ---------------------------------------------------------------------------
# SQLite — persistent GPU utilization history
# ---------------------------------------------------------------------------
DB_PATH = os.environ.get(
    "GPUROSTER_DB_PATH", os.path.join(os.path.dirname(__file__), "gpu_stats.db")
)


def _init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS gpu_util (
                ts      INTEGER NOT NULL,
                gpu_idx INTEGER NOT NULL,
                util    REAL    NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS ix_gpu_util_ts ON gpu_util(ts)")


# ---------------------------------------------------------------------------
# Data collection helpers
# ---------------------------------------------------------------------------


class CollectionError(RuntimeError):
    """A safe error code, without subprocess output or host information."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


_failure_logs = {}
_failure_lock = threading.Lock()


def _log_failure(source, code):
    now = time.monotonic()
    with _failure_lock:
        previous = _failure_logs.get(source)
        if previous and previous[0] == code and now - previous[1] < 60:
            return
        _failure_logs[source] = (code, now)
    app.logger.warning("Collection unavailable: source=%s code=%s", source, code)


def _run(cmd):
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"GPUROSTER_AUTH_USER", "GPUROSTER_AUTH_PASSWORD"}
    }
    environment["LC_ALL"] = "C"
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=app.config["COMMAND_TIMEOUT"],
            env=environment,
        )
    except subprocess.TimeoutExpired:
        raise CollectionError("command_timeout") from None
    except FileNotFoundError:
        raise CollectionError("command_missing") from None
    except (subprocess.CalledProcessError, OSError, UnicodeError):
        raise CollectionError("command_failed") from None
    return result.stdout.strip()


def _metric(value):
    if value.strip() in {"N/A", "[N/A]", "[Not Supported]", "Not Supported"}:
        return None
    try:
        number = float(value)
    except ValueError:
        raise CollectionError("invalid_output") from None
    if not math.isfinite(number) or number < 0:
        raise CollectionError("invalid_output")
    return number


def _collect(source, collector, fallback, health):
    try:
        result = collector()
    except Exception as exc:
        code = exc.code if isinstance(exc, CollectionError) else "collection_failed"
        _log_failure(source, code)
        health[source] = {"status": "unavailable", "error": code}
        return fallback
    health[source] = {"status": "ok"}
    return result


def get_gpu_stats():
    out = _run(
        [
            "nvidia-smi",
            "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
            "--format=csv,noheader,nounits",
        ]
    )
    gpus = []
    for parts in csv.reader(out.splitlines(), skipinitialspace=True):
        if not parts:
            continue
        try:
            gpus.append(
                {
                    "index": int(parts[0]),
                    "name": parts[1],
                    "utilization": _metric(parts[2]),
                    "memory_used": _metric(parts[3]),
                    "memory_total": _metric(parts[4]),
                    "temperature": _metric(parts[5]),
                    "power": _metric(parts[6]) if len(parts) > 6 else None,
                }
            )
        except (ValueError, IndexError):
            raise CollectionError("invalid_output") from None
    return gpus


def _build_uuid_map():
    """Return {uuid: gpu_index} mapping."""
    out = _run(["nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader"])
    m = {}
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2:
            try:
                m[parts[1]] = int(parts[0])
            except ValueError:
                raise CollectionError("invalid_output") from None
        elif line.strip():
            raise CollectionError("invalid_output")
    return m


def get_gpu_processes():
    """
    Use --query-compute-apps for accurate per-process VRAM.
    pmon reports 0/1 MB for many processes; compute-apps reports actual allocation.
    """
    uuid_map = _build_uuid_map()
    out = _run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,gpu_uuid,used_memory",
            "--format=csv,noheader,nounits",
        ]
    )
    processes = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            raise CollectionError("invalid_output")
        try:
            pid = int(parts[0])
            gpu_uuid = parts[1]
            mem_mb = _metric(parts[2])
            gpu_idx = uuid_map.get(gpu_uuid, -1)
        except ValueError:
            raise CollectionError("invalid_output") from None

        try:
            proc = psutil.Process(pid)
            username = proc.username()
            cmd_short = proc.name()
            if app.config["SHOW_COMMANDS"]:
                cmd_short = " ".join(proc.cmdline()[:6])
            if len(cmd_short) > 120:
                cmd_short = cmd_short[:120] + "…"
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            username = "unknown"
            cmd_short = "unknown"

        processes.append(
            {
                "gpu": gpu_idx,
                "pid": pid,
                "user": username,
                "mem_mb": mem_mb,
                "command": cmd_short,
            }
        )
    return processes


DATE_FMT = "%a %b %d %H:%M:%S %Y"


def _parse_login_line(line):
    """
    Parse one line from `last -F`.
    Returns (username, terminal, host, login_dt, logout_dt, still_in) or None.
    """
    parts = line.split()
    if len(parts) < 8:
        return None
    username = parts[0]
    if username in ("reboot", "shutdown", "wtmp", ""):
        return None
    terminal = parts[1]
    host = parts[2]
    try:
        login_str = " ".join(parts[3:8])
        login_dt = datetime.strptime(login_str, DATE_FMT)
    except ValueError:
        return None

    still_in = "still logged in" in line or "still running" in line
    if still_in:
        logout_dt = datetime.now()
    else:
        if " - " not in line:
            return None
        try:
            after_dash = line.split(" - ", 1)[1].strip()
            logout_str = " ".join(after_dash.split()[:5])
            logout_dt = datetime.strptime(logout_str, DATE_FMT)
        except ValueError:
            return None

    return username, terminal, host, login_dt, logout_dt, still_in


def get_login_stats():
    """Aggregate login duration per user: today / week / month."""
    out = _run(["last", "-F", "-n", "2000"])
    now = datetime.now()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    user_stats = defaultdict(
        lambda: {"today_s": 0.0, "week_s": 0.0, "month_s": 0.0, "last_seen": None}
    )

    for line in out.splitlines():
        parsed = _parse_login_line(line)
        if not parsed:
            continue
        username, _, _, login_dt, logout_dt, _ = parsed
        duration_s = max(0.0, (logout_dt - login_dt).total_seconds())

        ls = user_stats[username]["last_seen"]
        if ls is None or logout_dt > ls:
            user_stats[username]["last_seen"] = logout_dt

        if login_dt >= month_ago:
            user_stats[username]["month_s"] += duration_s
        if login_dt >= week_ago:
            user_stats[username]["week_s"] += duration_s
        if login_dt >= today_start:
            user_stats[username]["today_s"] += duration_s

    # Non-interactive SSH estimates only cover currently visible connections.
    # Historical completeness and overlap accounting remain separate work.
    now = datetime.now()
    noninteractive_durations = get_noninteractive_session_durations()
    for user, dur_s in noninteractive_durations.items():
        user_stats[user]["today_s"] += dur_s
        ls = user_stats[user]["last_seen"]
        if ls is None or now > ls:
            user_stats[user]["last_seen"] = now

    return {
        user: {
            "today_h": round(s["today_s"] / 3600, 2),
            "week_h": round(s["week_s"] / 3600, 2),
            "month_h": round(s["month_s"] / 3600, 2),
            "last_seen": s["last_seen"].isoformat() if s["last_seen"] else None,
            "has_noninteractive_ssh": user in noninteractive_durations,
        }
        for user, s in user_stats.items()
    }


def get_active_connections():
    """Return verified SSH processes; a non-TTY connection is not proof of VSCode."""
    now = datetime.now()
    connections = []

    for proc in psutil.process_iter(
        ["pid", "name", "username", "cmdline", "create_time"]
    ):
        try:
            name = proc.info["name"] or ""
            cmdline = proc.info["cmdline"] or []
            cmd = " ".join(cmdline)

            if name in {"sshd", "sshd-session"} and "@" in cmd:
                m = re.fullmatch(
                    r"sshd(?:-session)?:\s+([^@\s]+)@(notty|pts/\d+|tty\d+)", cmd
                )
                if not m:
                    continue
                user, tty = m.group(1), m.group(2)
                if user != proc.info["username"]:
                    continue
                executable = proc.exe()
                if Path(executable).name not in {"sshd", "sshd-session"}:
                    continue
                executable_stat = os.stat(executable)
                if (
                    executable_stat.st_uid != 0
                    or executable_stat.st_mode & 0o022
                    or not stat.S_ISREG(executable_stat.st_mode)
                ):
                    continue
                if proc.info["create_time"] is None:
                    continue
                conn_type = "SSH (no TTY)" if tty == "notty" else "SSH"
                since_dt = datetime.fromtimestamp(proc.info["create_time"])
                duration_min = round(
                    (now.timestamp() - proc.info["create_time"]) / 60, 1
                )
                connections.append(
                    {
                        "user": user,
                        "type": conn_type,
                        "tty": tty,
                        "since": since_dt.strftime("%Y-%m-%d %H:%M:%S"),
                        "duration_min": duration_min,
                    }
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            pass

    connections.sort(key=lambda c: (c["tty"] != "notty", c["user"], c["since"]))
    return connections


def get_noninteractive_session_durations():
    """Estimate today's duration from currently verified non-TTY connections."""
    now_ts = datetime.now().timestamp()
    today_start = (
        datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    )

    durations = {}
    for connection in get_active_connections():
        if connection["tty"] == "notty":
            duration = min(connection["duration_min"] * 60, now_ts - today_start)
            user = connection["user"]
            durations[user] = max(durations.get(user, 0), duration)
    return durations


def get_user_sessions(limit=300):
    """
    Return individual login/logout session rows (from last), most recent first.
    Also prepends active non-TTY SSH connections as synthetic session rows.
    """
    sessions = []

    # Non-TTY connections may not appear in wtmp.
    for conn in get_active_connections():
        if conn["tty"] == "notty":
            sessions.append(
                {
                    "user": conn["user"],
                    "terminal": conn["tty"],
                    "host": "Non-interactive SSH",
                    "login": conn["since"],
                    "logout": "active",
                    "duration_min": conn["duration_min"],
                    "still_in": True,
                    "source": "noninteractive_ssh",
                }
            )

    out = _run(["last", "-F", "-n", str(limit)])
    for line in out.splitlines():
        parsed = _parse_login_line(line)
        if not parsed:
            continue
        username, terminal, host, login_dt, logout_dt, still_in = parsed
        duration_s = max(0.0, (logout_dt - login_dt).total_seconds())
        sessions.append(
            {
                "user": username,
                "terminal": terminal,
                "host": host,
                "login": login_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "logout": "active"
                if still_in
                else logout_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "duration_min": round(duration_s / 60, 1),
                "still_in": still_in,
                "source": "wtmp",
            }
        )
    return sessions


def get_system_stats():
    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory()
    return {
        "cpu_percent": cpu,
        "ram_used_gb": round(ram.used / 1e9, 1),
        "ram_total_gb": round(ram.total / 1e9, 1),
        "ram_percent": ram.percent,
    }


# ---------------------------------------------------------------------------
# Background history sampler — EMA smoothed, 3-second interval
# ---------------------------------------------------------------------------

_last_db_write = 0
_sampler_thread = None
_sampler_stop = threading.Event()
_sampler_start_lock = threading.Lock()
_sampler_health = {"status": "not_started", "last_sample": None, "last_write": None}


def _sample_history():
    """Perform one sample; advance write bookkeeping only after commit succeeds."""
    global _last_db_write
    gpus = get_gpu_stats()
    now_ts = int(time.time())
    ts_str = datetime.now().strftime("%H:%M:%S")
    rows = []
    with _history_lock:
        for gpu in gpus:
            idx, raw = gpu["index"], gpu["utilization"]
            if raw is None:
                _gpu_history[idx].append({"ts": ts_str, "util": None})
                continue
            previous = _gpu_ema.get(idx, float(raw))
            _gpu_ema[idx] = EMA_ALPHA * raw + (1 - EMA_ALPHA) * previous
            value = round(_gpu_ema[idx], 1)
            _gpu_history[idx].append({"ts": ts_str, "util": value})
            rows.append((now_ts, idx, value))
        _sampler_health["last_sample"] = now_ts

    if now_ts - _last_db_write >= 60:
        with closing(sqlite3.connect(DB_PATH, timeout=1)) as conn, conn:
            conn.executemany(
                "INSERT INTO gpu_util (ts, gpu_idx, util) VALUES (?,?,?)", rows
            )
            conn.execute("DELETE FROM gpu_util WHERE ts < ?", (now_ts - 31 * 86400,))
        _last_db_write = now_ts
        with _history_lock:
            _sampler_health["last_write"] = now_ts
    with _history_lock:
        _sampler_health["status"] = "ok"
        _sampler_health.pop("error", None)


def _history_sampler():
    while not _sampler_stop.is_set():
        try:
            _sample_history()
        except Exception as exc:
            code = exc.code if isinstance(exc, CollectionError) else "history_failed"
            _log_failure("history", code)
            with _history_lock:
                _sampler_health.update(status="unavailable", error=code)
        _sampler_stop.wait(3)


def start_sampler():
    """Explicit, idempotent startup for the single-process development launcher."""
    global _sampler_thread
    with _sampler_start_lock:
        if _sampler_thread is not None and _sampler_thread.is_alive():
            return
        _init_db()
        _sampler_stop.clear()
        with _history_lock:
            _sampler_health["status"] = "starting"
        _sampler_thread = threading.Thread(target=_history_sampler, daemon=True)
        _sampler_thread.start()


def history_health():
    with _history_lock:
        health = dict(_sampler_health)
    if health["status"] == "ok" and time.time() - (health["last_sample"] or 0) > 15:
        health["status"] = "stale"
    return health


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/stats")
def api_stats():
    health = {}
    gpus = _collect("gpus", get_gpu_stats, [], health)
    processes = _collect("processes", get_gpu_processes, [], health)
    system = _collect("system", get_system_stats, None, health)
    connections = []
    if app.config["SHOW_SESSIONS"]:
        connections = _collect("connections", get_active_connections, [], health)
    else:
        health["connections"] = {"status": "disabled"}
    health["history"] = history_health()

    gpu_procs = defaultdict(list)
    for p in processes:
        gpu_procs[p["gpu"]].append(p)
    for g in gpus:
        g["processes"] = gpu_procs[g["index"]]

    user_gpu = defaultdict(
        lambda: {
            "gpu_indices": set(),
            "mem_mb": 0,
            "proc_count": 0,
            "memory_complete": True,
        }
    )
    for p in processes:
        user_gpu[p["user"]]["gpu_indices"].add(p["gpu"])
        if p["mem_mb"] is None:
            user_gpu[p["user"]]["memory_complete"] = False
        else:
            user_gpu[p["user"]]["mem_mb"] += p["mem_mb"]
        user_gpu[p["user"]]["proc_count"] += 1
    user_gpu_out = {
        u: {
            "gpu_indices": sorted(v["gpu_indices"]),
            "mem_gb": round(v["mem_mb"] / 1024, 1) if v["memory_complete"] else None,
            "proc_count": v["proc_count"],
        }
        for u, v in user_gpu.items()
    }

    with _history_lock:
        history = {str(idx): list(data) for idx, data in _gpu_history.items()}

    return jsonify(
        {
            "gpus": gpus,
            "processes": processes,
            "user_gpu": user_gpu_out,
            "history": history,
            "system": system,
            "connections": connections,
            "health": {
                "status": "ok"
                if all(s["status"] in {"ok", "disabled"} for s in health.values())
                else "degraded",
                "sources": health,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )


@app.route("/api/login_stats")
def api_login_stats():
    return jsonify(get_login_stats())


@app.route("/api/sessions")
def api_sessions():
    return jsonify(get_user_sessions())


@app.route("/api/connections")
def api_connections():
    return jsonify(get_active_connections())


@app.route("/api/gpu_history")
def api_gpu_history():
    """
    Return aggregated GPU utilization for a time range.
    ?range=today  → per-5-min buckets since midnight
    ?range=week   → per-hour buckets, last 7 days
    ?range=month  → per-6h buckets, last 30 days
    """
    range_key = request.args.get("range", "today")
    if range_key not in {"today", "week", "month"}:
        return jsonify(error="invalid_range"), 400
    now_ts = int(time.time())
    now_dt = datetime.now()

    if range_key == "today":
        midnight = int(datetime(now_dt.year, now_dt.month, now_dt.day).timestamp())
        since = midnight
        bucket = 5 * 60  # 5-minute buckets
        date_format = "%H:%M"
    elif range_key == "week":
        since = now_ts - 7 * 86400
        bucket = 3600  # 1-hour buckets
        date_format = "%a %d %H:%M"
    else:  # month
        since = now_ts - 30 * 86400
        bucket = 6 * 3600  # 6-hour buckets
        date_format = "%b %d %H:%M"

    uri = Path(DB_PATH).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=1)) as conn:
        rows = conn.execute(
            """
            SELECT
                (ts / :b) * :b  AS bucket_ts,
                gpu_idx,
                ROUND(AVG(util), 1) AS avg_util
            FROM gpu_util
            WHERE ts >= :since
            GROUP BY bucket_ts, gpu_idx
            ORDER BY bucket_ts, gpu_idx
        """,
            {"b": bucket, "since": since},
        ).fetchall()

    # Organise into {gpu_idx: {bucket_ts: avg_util}}
    gpu_map = defaultdict(dict)
    all_buckets = set()
    for bucket_ts, gpu_idx, avg_util in rows:
        gpu_map[gpu_idx][bucket_ts] = avg_util
        all_buckets.add(bucket_ts)

    sorted_buckets = sorted(all_buckets)
    labels = [datetime.fromtimestamp(b).strftime(date_format) for b in sorted_buckets]

    datasets = []
    for gpu_idx in sorted(gpu_map.keys()):
        datasets.append(
            {
                "gpu": gpu_idx,
                "data": [gpu_map[gpu_idx].get(b) for b in sorted_buckets],
            }
        )

    return jsonify(
        {
            "labels": labels,
            "datasets": datasets,
            "range": range_key,
            "points": len(sorted_buckets),
            "health": history_health(),
        }
    )


@app.errorhandler(CollectionError)
def collection_error(error):
    _log_failure("request", error.code)
    return jsonify(error=error.code), 503


@app.errorhandler(sqlite3.Error)
def storage_error(error):
    _log_failure("history", "history_failed")
    return jsonify(error="history_unavailable"), 503


def main():
    host = app.config["BIND_HOST"]
    if not _is_loopback(host) and not app.config["AUTH_USER"]:
        raise SystemExit(
            "Remote binding requires GPUROSTER_AUTH_USER and GPUROSTER_AUTH_PASSWORD"
        )
    start_sampler()
    try:
        app.run(
            host=host, port=app.config["BIND_PORT"], debug=False, use_reloader=False
        )
    finally:
        _sampler_stop.set()
        _sampler_thread.join(timeout=5)


if __name__ == "__main__":
    main()
