import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from gpuroster.monitoring.history import HistoryStore, RollingHistory
from gpuroster.monitoring.models import GPU


class HistoryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "history # test.db"
        self.store = HistoryStore(self.path)
        self.now = 1790856000

    def test_additive_migration_preserves_legacy_rows_and_old_writes(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "CREATE TABLE gpu_util(ts INTEGER NOT NULL,gpu_idx INTEGER NOT NULL,util REAL NOT NULL)"
            )
            connection.execute(
                "INSERT INTO gpu_util VALUES (?,?,?)", (self.now - 60, 0, 10)
            )
        self.store.initialize()
        self.store.initialize()
        self.store.write([(self.now, 0, 20, "GPU-example")], self.now, 0)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT INTO gpu_util(ts,gpu_idx,util) VALUES (?,?,?)",
                (self.now - 30, 0, 15),
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM gpu_util").fetchone()[0], 3
            )
        data = self.store.read("today", self.now)
        self.assertEqual(
            {row["id"] for row in data["datasets"]}, {"legacy:index:0", "GPU-example"}
        )

    def test_legacy_read_does_not_migrate(self):
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "CREATE TABLE gpu_util(ts INTEGER,gpu_idx INTEGER,util REAL)"
            )
            connection.execute("INSERT INTO gpu_util VALUES (?,?,?)", (self.now, 0, 20))
        self.assertEqual(
            self.store.read("today", self.now)["datasets"][0]["id"], "legacy:index:0"
        )
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(
                len(connection.execute("PRAGMA table_info(gpu_util)").fetchall()), 3
            )

    def test_failed_write_retries_without_advancing_clock(self):
        self.store.initialize()
        with (
            patch(
                "gpuroster.monitoring.history.sqlite3.connect",
                side_effect=sqlite3.OperationalError("test-only"),
            ),
            self.assertRaises(sqlite3.Error),
        ):
            self.store.write([(self.now, 0, 10, "GPU-example")], self.now, 1)
        self.assertIsNone(self.store.last_write_monotonic)
        self.store.write([(self.now, 0, 10, "GPU-example")], self.now, 2)
        self.assertEqual(self.store.last_write_monotonic, 2)

    def test_write_cadence_uses_monotonic_time_after_wall_clock_change(self):
        self.store.write([(self.now, 0, 10, "GPU-example")], self.now, 0)
        self.store.write([(self.now - 3600, 0, 20, "GPU-example")], self.now - 3600, 61)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM gpu_util").fetchone()[0], 2
            )

    def test_history_keeps_uuid_identity_when_indices_swap(self):
        self.store.write(
            [
                (self.now - 3600, 0, 10, "GPU-alpha"),
                (self.now - 3600, 1, 80, "GPU-beta"),
            ],
            self.now - 3600,
            0,
        )
        self.store.write(
            [(self.now, 1, 20, "GPU-alpha"), (self.now, 0, 90, "GPU-beta")],
            self.now,
            61,
        )
        data = self.store.read("today", self.now)
        series = {
            row["id"]: [value for value in row["data"] if value is not None]
            for row in data["datasets"]
        }
        self.assertEqual(series, {"GPU-alpha": [10, 20], "GPU-beta": [80, 90]})
        self.assertIn(None, data["datasets"][0]["data"])
        self.assertTrue(all(label.endswith("+00:00") for label in data["labels"]))

    def test_missing_database_is_not_created_by_read(self):
        with self.assertRaises(sqlite3.Error):
            self.store.read("today", self.now)
        self.assertFalse(self.path.exists())

    def test_retention_removes_expired_rows_only_during_write(self):
        self.store.write(
            [
                (self.now - 32 * 86400, 0, 10, "GPU-example"),
                (self.now, 0, 20, "GPU-example"),
            ],
            self.now,
            1,
        )
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM gpu_util").fetchone()[0], 1
            )

    def test_rolling_gaps_reset_smoothing_and_identity_is_stable(self):
        history = RollingHistory()
        gpu = GPU(0, "GPU-example", "GPU", 20, 0, 100, 30, 20)
        history.append([gpu], self.now)
        history.append([], self.now + 3)
        rows = history.append([replace(gpu, index=3, utilization=80)], self.now + 6)
        self.assertEqual(rows[0][2], 80)
        self.assertEqual(
            [point["util"] for point in history.to_dict()[gpu.uuid]], [20, None, 80]
        )
        self.assertEqual(history.devices[gpu.uuid]["index"], 3)
        history.append([], self.now + 400)
        self.assertEqual(history.to_dict(), {})


if __name__ == "__main__":
    unittest.main()
