"""UUID-based rolling history and additive, backward-compatible SQLite storage."""

import sqlite3
from collections import defaultdict, deque
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from gpuroster.monitoring.models import utc_iso


class RollingHistory:
    def __init__(self, interval=3, length=120):
        self.interval = interval
        self.length = length
        self.series = defaultdict(lambda: deque(maxlen=length))
        self.ema = {}
        self.devices = {}
        self.last_seen = {}

    def append(self, devices, now):
        current = {device.uuid: device for device in devices}
        values = []
        for uuid in set(self.series) | set(current):
            device = current.get(uuid)
            if device is not None:
                self.devices[uuid] = {"index": device.index, "name": device.name}
                self.last_seen[uuid] = now
            if now - self.last_seen[uuid] > self.interval * self.length:
                self.series.pop(uuid, None)
                self.ema.pop(uuid, None)
                self.devices.pop(uuid, None)
                self.last_seen.pop(uuid, None)
                continue
            raw = device.utilization if device else None
            value = None
            if raw is not None:
                value = round(0.2 * raw + 0.8 * self.ema.get(uuid, raw), 1)
                self.ema[uuid] = value
                values.append((int(now), device.index, value, uuid))
            else:
                # A returning device starts a new smoothing interval after a gap.
                self.ema.pop(uuid, None)
            self.series[uuid].append({"ts": now, "util": value})
        return values

    def to_dict(self):
        return {uuid: list(points) for uuid, points in self.series.items()}


class HistoryStore:
    def __init__(self, path):
        self.path = str(path)
        self.initialized = False
        self.last_write = None
        self.last_write_monotonic = None

    def initialize(self):
        with closing(sqlite3.connect(self.path, timeout=1)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS gpu_util (ts INTEGER NOT NULL, gpu_idx INTEGER NOT NULL, util REAL NOT NULL, gpu_uuid TEXT)"
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(gpu_util)")
            }
            if "gpu_uuid" not in columns:
                connection.execute("ALTER TABLE gpu_util ADD COLUMN gpu_uuid TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS ix_gpu_util_ts ON gpu_util(ts)"
            )
        self.initialized = True

    def write(self, rows, now, monotonic):
        if not self.initialized:
            self.initialize()
        if (
            self.last_write_monotonic is not None
            and monotonic - self.last_write_monotonic < 60
        ):
            return
        with closing(sqlite3.connect(self.path, timeout=1)) as connection, connection:
            connection.executemany(
                "INSERT INTO gpu_util (ts,gpu_idx,util,gpu_uuid) VALUES (?,?,?,?)", rows
            )
            connection.execute(
                "DELETE FROM gpu_util WHERE ts < ?", (int(now) - 31 * 86400,)
            )
        self.last_write = now
        self.last_write_monotonic = monotonic

    def read(self, range_key, now, timezone_name="UTC"):
        if range_key not in {"today", "week", "month"}:
            raise ValueError("invalid_range")
        if range_key == "today":
            since = (
                datetime.fromtimestamp(now, ZoneInfo(timezone_name))
                .replace(hour=0, minute=0, second=0, microsecond=0)
                .timestamp()
            )
            bucket = 300
        else:
            since = now - (7 if range_key == "week" else 30) * 86400
            bucket = 3600 if range_key == "week" else 21600
        uri = Path(self.path).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(gpu_util)")
            }
            # Read legacy databases without migrating them as a side effect.
            identity = (
                "COALESCE(gpu_uuid, 'legacy:index:' || gpu_idx)"
                if "gpu_uuid" in columns
                else "'legacy:index:' || gpu_idx"
            )
            rows = connection.execute(
                f"SELECT (ts / :bucket) * :bucket, {identity} AS identity, MAX(gpu_idx), ROUND(AVG(util),1) FROM gpu_util WHERE ts >= :since AND ts <= :now GROUP BY 1, 2 ORDER BY 1, 2",
                {"bucket": bucket, "since": int(since), "now": int(now)},
            ).fetchall()
        labels = (
            list(
                range(
                    int(since) // bucket * bucket,
                    int(now) // bucket * bucket + 1,
                    bucket,
                )
            )
            if rows
            else []
        )
        datasets = {}
        for timestamp, identity, index, value in rows:
            dataset = datasets.setdefault(identity, {"gpu": index, "values": {}})
            dataset["gpu"] = index
            dataset["values"][timestamp] = value
        return {
            "labels": [utc_iso(timestamp) for timestamp in labels],
            "datasets": [
                {
                    "id": identity,
                    "gpu": dataset["gpu"],
                    "label": f"GPU {dataset['gpu']} · "
                    + (
                        "legacy index"
                        if identity.startswith("legacy:index:")
                        else identity[-8:]
                    ),
                    "data": [dataset["values"].get(timestamp) for timestamp in labels],
                }
                for identity, dataset in sorted(datasets.items())
            ],
            "points": len(labels),
            "range": range_key,
            "timezone": timezone_name,
        }
