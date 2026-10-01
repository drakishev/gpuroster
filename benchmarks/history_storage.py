"""Synthetic 8-GPU history sizes, migration, write/retention, and API queries.

Run: python -m benchmarks.history_storage. Databases are temporary and removed.
"""

import json
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path

from gpuroster.monitoring.history import HistoryStore


def run_case(days, legacy=False):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "history.db"
        now = int(time.time())
        rows = days * 1440 * 8
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE gpu_util(ts INTEGER NOT NULL,gpu_idx INTEGER NOT NULL,util REAL NOT NULL"
                + (")" if legacy else ",gpu_uuid TEXT)")
            )
            connection.execute("CREATE INDEX ix_gpu_util_ts ON gpu_util(ts)")
            connection.executemany(
                "INSERT INTO gpu_util VALUES " + ("(?,?,?)" if legacy else "(?,?,?,?)"),
                (
                    (now - (days * 1440 - minute) * 60, gpu, (minute + gpu) % 101)
                    + (() if legacy else (f"GPU-00000000-0000-0000-0000-{gpu:012d}",))
                    for minute in range(days * 1440)
                    for gpu in range(8)
                ),
            )
        before_bytes = path.stat().st_size
        store = HistoryStore(path)
        start = time.perf_counter()
        store.initialize()
        migration_ms = (time.perf_counter() - start) * 1000
        queries = {}
        for period in ("today", "week", "month"):
            durations = []
            for _ in range(3):
                start = time.perf_counter()
                store.read(period, now)
                durations.append((time.perf_counter() - start) * 1000)
            queries[period + "_median_ms"] = round(statistics.median(durations), 3)
        start = time.perf_counter()
        store.write([(now, gpu, 40, f"GPU-example-{gpu}") for gpu in range(8)], now, 1)
        write_ms = (time.perf_counter() - start) * 1000
        with sqlite3.connect(path) as connection:
            remaining = connection.execute("SELECT COUNT(*) FROM gpu_util").fetchone()[
                0
            ]
        return {
            "schema": "legacy" if legacy else "uuid",
            "days": days,
            "seed_rows": rows,
            "db_mib_before": round(before_bytes / 1048576, 2),
            "migration_ms": round(migration_ms, 3),
            **queries,
            "write_and_retention_ms": round(write_ms, 3),
            "rows_after_retention": remaining,
            "db_mib_after": round(path.stat().st_size / 1048576, 2),
        }


if __name__ == "__main__":
    print(
        json.dumps(
            [
                run_case(days, legacy)
                for legacy in (True, False)
                for days in (1, 7, 30, 90)
            ],
            indent=2,
        )
    )
