import os
import re
import sqlite3
import subprocess
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta

import psutil
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# Rolling 120-point history per GPU (sampled every 3s = last 6 minutes)
_gpu_history = defaultdict(lambda: deque(maxlen=120))
_gpu_ema = {}          # EMA smoothed value per GPU index
_history_lock = threading.Lock()
EMA_ALPHA = 0.2        # lower = smoother; 0.2 means 80% weight on old value

# ---------------------------------------------------------------------------
# SQLite — persistent GPU utilization history
# ---------------------------------------------------------------------------
DB_PATH = os.path.join(os.path.dirname(__file__), 'gpu_stats.db')


def _init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS gpu_util (
                ts      INTEGER NOT NULL,
                gpu_idx INTEGER NOT NULL,
                util    REAL    NOT NULL
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS ix_gpu_util_ts ON gpu_util(ts)')


_init_db()


# ---------------------------------------------------------------------------
# Data collection helpers
# ---------------------------------------------------------------------------

def _run(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout.strip()


def get_gpu_stats():
    out = _run([
        "nvidia-smi",
        "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
        "--format=csv,noheader,nounits",
    ])
    gpus = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        try:
            gpus.append({
                "index": int(parts[0]),
                "name": parts[1],
                "utilization": int(parts[2]),
                "memory_used": int(parts[3]),
                "memory_total": int(parts[4]),
                "temperature": int(parts[5]),
                "power": parts[6] if len(parts) > 6 else "N/A",
            })
        except (ValueError, IndexError):
            pass
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
                pass
    return m


def get_gpu_processes():
    """
    Use --query-compute-apps for accurate per-process VRAM.
    pmon reports 0/1 MB for many processes; compute-apps reports actual allocation.
    """
    uuid_map = _build_uuid_map()
    out = _run([
        "nvidia-smi",
        "--query-compute-apps=pid,gpu_uuid,used_memory",
        "--format=csv,noheader,nounits",
    ])
    processes = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            gpu_uuid = parts[1]
            mem_mb = int(parts[2])
            gpu_idx = uuid_map.get(gpu_uuid, -1)
        except ValueError:
            continue

        try:
            proc = psutil.Process(pid)
            username = proc.username()
            cmd_parts = proc.cmdline()
            cmd_short = " ".join(cmd_parts[:6])
            if len(cmd_short) > 120:
                cmd_short = cmd_short[:120] + "…"
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            username = "unknown"
            cmd_short = "unknown"

        processes.append({
            "gpu": gpu_idx,
            "pid": pid,
            "user": username,
            "mem_mb": mem_mb,
            "command": cmd_short,
        })
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

    user_stats = defaultdict(lambda: {"today_s": 0.0, "week_s": 0.0, "month_s": 0.0, "last_seen": None})

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

    # Add VSCode session time (not in wtmp) — only today is reliable,
    # since closed VSCode connections leave no trace in wtmp.
    now = datetime.now()
    vscode_dur = get_vscode_session_durations()
    for user, dur_s in vscode_dur.items():
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
            "has_vscode": user in vscode_dur,
        }
        for user, s in user_stats.items()
    }


def get_active_connections():
    """
    Detect active SSH and VSCode connections by scanning sshd processes.
    VSCode SSH uses @notty (no TTY) so it never appears in last/who.
    Also finds .vscode-server processes to confirm VSCode sessions.
    Returns list of {user, type, tty, host, since, duration_min}.
    """
    now = datetime.now()
    connections = []
    seen_users_notty = set()

    for proc in psutil.process_iter(['pid', 'name', 'username', 'cmdline', 'create_time']):
        try:
            name = proc.info['name'] or ''
            cmdline = proc.info['cmdline'] or []
            cmd = ' '.join(cmdline)

            # sshd: user@pts/X  →  regular SSH terminal
            # sshd: user@notty  →  VSCode / non-interactive SSH
            if name == 'sshd' and '@' in cmd:
                m = re.search(r'sshd:\s+(\w+)@(\S+)', cmd)
                if not m:
                    continue
                user, tty = m.group(1), m.group(2)
                conn_type = 'VSCode' if tty == 'notty' else 'SSH'
                since_dt = datetime.fromtimestamp(proc.info['create_time'])
                duration_min = round((now.timestamp() - proc.info['create_time']) / 60, 1)
                connections.append({
                    'user': user,
                    'type': conn_type,
                    'tty': tty,
                    'since': since_dt.strftime('%Y-%m-%d %H:%M:%S'),
                    'duration_min': duration_min,
                })
                if tty == 'notty':
                    seen_users_notty.add(user)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # Sort: VSCode first, then by user, then by since
    connections.sort(key=lambda c: (c['type'] != 'VSCode', c['user'], c['since']))
    return connections


def get_vscode_session_durations():
    """
    Return {user: duration_seconds_today} for users with active VSCode sessions.
    Uses sshd: user@notty processes (one per connection, not multiplied by children).
    Takes the earliest notty connection started today as the session start.
    """
    now_ts = datetime.now().timestamp()
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

    # earliest sshd@notty start today, per user
    earliest: dict = {}

    for proc in psutil.process_iter(['name', 'cmdline', 'create_time']):
        try:
            if (proc.info['name'] or '') != 'sshd':
                continue
            cmd = ' '.join(proc.info['cmdline'] or [])
            if '@notty' not in cmd:
                continue
            m = re.search(r'sshd:\s+(\w+)@notty', cmd)
            if not m:
                continue
            user = m.group(1)
            ct = proc.info['create_time']
            # Only sessions that started today (or earlier — clamp to today_start)
            effective_start = max(ct, today_start)
            if user not in earliest or effective_start < earliest[user]:
                earliest[user] = effective_start
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    return {user: now_ts - start for user, start in earliest.items()}


def get_user_sessions(limit=300):
    """
    Return individual login/logout session rows (from last), most recent first.
    Also prepends active VSCode connections as synthetic session rows.
    """
    now = datetime.now()
    sessions = []

    # Prepend live VSCode connections (not in last at all)
    for conn in get_active_connections():
        if conn['type'] == 'VSCode':
            sessions.append({
                'user': conn['user'],
                'terminal': conn['tty'],
                'host': 'VSCode SSH',
                'login': conn['since'],
                'logout': 'active',
                'duration_min': conn['duration_min'],
                'still_in': True,
                'source': 'vscode',
            })

    out = _run(["last", "-F", "-n", str(limit)])
    for line in out.splitlines():
        parsed = _parse_login_line(line)
        if not parsed:
            continue
        username, terminal, host, login_dt, logout_dt, still_in = parsed
        duration_s = max(0.0, (logout_dt - login_dt).total_seconds())
        sessions.append({
            'user': username,
            'terminal': terminal,
            'host': host,
            'login': login_dt.strftime('%Y-%m-%d %H:%M:%S'),
            'logout': 'active' if still_in else logout_dt.strftime('%Y-%m-%d %H:%M:%S'),
            'duration_min': round(duration_s / 60, 1),
            'still_in': still_in,
            'source': 'wtmp',
        })
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

_last_db_write = 0   # unix timestamp of last SQLite write


def _history_sampler():
    global _last_db_write
    while True:
        try:
            gpus = get_gpu_stats()
            ts_str = datetime.now().strftime("%H:%M:%S")
            now_ts = int(time.time())
            with _history_lock:
                for g in gpus:
                    idx = g["index"]
                    raw = g["utilization"]
                    if idx not in _gpu_ema:
                        _gpu_ema[idx] = float(raw)
                    else:
                        _gpu_ema[idx] = EMA_ALPHA * raw + (1 - EMA_ALPHA) * _gpu_ema[idx]
                    _gpu_history[idx].append({"ts": ts_str, "util": round(_gpu_ema[idx], 1)})

            # Write to SQLite once per minute
            if now_ts - _last_db_write >= 60:
                _last_db_write = now_ts
                rows = [(now_ts, g["index"], round(_gpu_ema.get(g["index"], g["utilization"]), 1))
                        for g in gpus]
                with sqlite3.connect(DB_PATH) as conn:
                    conn.executemany(
                        'INSERT INTO gpu_util (ts, gpu_idx, util) VALUES (?,?,?)', rows
                    )
                    # Keep only last 31 days
                    conn.execute('DELETE FROM gpu_util WHERE ts < ?', (now_ts - 31 * 86400,))
        except Exception:
            pass
        time.sleep(3)


threading.Thread(target=_history_sampler, daemon=True).start()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/stats")
def api_stats():
    gpus = get_gpu_stats()
    processes = get_gpu_processes()

    gpu_procs = defaultdict(list)
    for p in processes:
        gpu_procs[p["gpu"]].append(p)
    for g in gpus:
        g["processes"] = gpu_procs[g["index"]]

    user_gpu = defaultdict(lambda: {"gpu_indices": set(), "mem_mb": 0, "proc_count": 0})
    for p in processes:
        user_gpu[p["user"]]["gpu_indices"].add(p["gpu"])
        user_gpu[p["user"]]["mem_mb"] += p["mem_mb"]
        user_gpu[p["user"]]["proc_count"] += 1
    user_gpu_out = {
        u: {
            "gpu_indices": sorted(v["gpu_indices"]),
            "mem_gb": round(v["mem_mb"] / 1024, 1),
            "proc_count": v["proc_count"],
        }
        for u, v in user_gpu.items()
    }

    with _history_lock:
        history = {str(idx): list(data) for idx, data in _gpu_history.items()}

    return jsonify({
        "gpus": gpus,
        "processes": processes,
        "user_gpu": user_gpu_out,
        "history": history,
        "system": get_system_stats(),
        "connections": get_active_connections(),
        "timestamp": datetime.now().isoformat(),
    })


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
    now_ts = int(time.time())
    now_dt = datetime.now()

    if range_key == "today":
        midnight = int(datetime(now_dt.year, now_dt.month, now_dt.day).timestamp())
        since = midnight
        bucket = 5 * 60          # 5-minute buckets
        fmt = lambda ts: datetime.fromtimestamp(ts).strftime("%H:%M")
    elif range_key == "week":
        since = now_ts - 7 * 86400
        bucket = 3600            # 1-hour buckets
        fmt = lambda ts: datetime.fromtimestamp(ts).strftime("%a %d %H:%M")
    else:  # month
        since = now_ts - 30 * 86400
        bucket = 6 * 3600        # 6-hour buckets
        fmt = lambda ts: datetime.fromtimestamp(ts).strftime("%b %d %H:%M")

    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute('''
            SELECT
                (ts / :b) * :b  AS bucket_ts,
                gpu_idx,
                ROUND(AVG(util), 1) AS avg_util
            FROM gpu_util
            WHERE ts >= :since
            GROUP BY bucket_ts, gpu_idx
            ORDER BY bucket_ts, gpu_idx
        ''', {"b": bucket, "since": since}).fetchall()

    # Organise into {gpu_idx: {bucket_ts: avg_util}}
    gpu_map = defaultdict(dict)
    all_buckets = set()
    for bucket_ts, gpu_idx, avg_util in rows:
        gpu_map[gpu_idx][bucket_ts] = avg_util
        all_buckets.add(bucket_ts)

    sorted_buckets = sorted(all_buckets)
    labels = [fmt(b) for b in sorted_buckets]

    datasets = []
    for gpu_idx in sorted(gpu_map.keys()):
        datasets.append({
            "gpu": gpu_idx,
            "data": [gpu_map[gpu_idx].get(b) for b in sorted_buckets],
        })

    return jsonify({
        "labels": labels,
        "datasets": datasets,
        "range": range_key,
        "points": len(sorted_buckets),
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=18081, debug=False)
