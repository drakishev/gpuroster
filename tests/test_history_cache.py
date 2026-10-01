import concurrent.futures
import sqlite3
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from gpuroster.monitoring.history import HistoryStore
from gpuroster.monitoring.history_cache import HistoryQueryCache


class HistoryCacheTests(unittest.TestCase):
    def setUp(self):
        self.wall = 1790856000.0
        self.monotonic = 100.0
        self.store = Mock(last_write_monotonic=None)
        self.store.read.return_value = {"datasets": [{"data": [42]}]}
        self.cache = self.make_cache(self.store)

    def make_cache(self, store, timezone="UTC"):
        return HistoryQueryCache(
            store,
            timezone,
            wall_clock=lambda: self.wall,
            monotonic=lambda: self.monotonic,
        )

    def advance(self, seconds):
        self.wall += seconds
        self.monotonic += seconds

    def concurrent_reads(self, count=50):
        barrier = threading.Barrier(count)

        def request():
            barrier.wait(timeout=5)
            try:
                return self.cache.read("month")
            except sqlite3.Error as error:
                return str(error)

        with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
            return list(pool.map(lambda _: request(), range(count)))

    def test_concurrent_cold_requests_share_query_and_return_isolated_copies(self):
        results = self.concurrent_reads()
        self.store.read.assert_called_once_with("month", self.wall, "UTC")
        results[0]["datasets"][0]["data"][0] = 0
        results[0]["health"] = "changed"
        self.assertEqual(results[1]["datasets"][0]["data"], [42])
        again = self.cache.read("month")
        self.assertEqual(again["datasets"][0]["data"], [42])
        self.assertNotIn("health", again)

    def test_ttl_and_query_timestamp_do_not_refresh_on_cache_hits(self):
        first = self.cache.read("month")
        self.advance(59)
        second = self.cache.read("month")
        self.assertEqual(second["query"]["as_of"], first["query"]["as_of"])
        self.assertEqual(second["query"]["age_seconds"], 59)
        self.assertEqual(second["query"]["max_age_seconds"], 60)
        self.store.read.assert_called_once()
        self.advance(1)
        third = self.cache.read("month")
        self.assertNotEqual(third["query"]["as_of"], first["query"]["as_of"])
        self.assertEqual(self.store.read.call_count, 2)

    def test_successful_write_invalidates_but_skipped_or_failed_write_does_not(self):
        with tempfile.TemporaryDirectory() as directory:
            store = HistoryStore(Path(directory) / "test.db")
            row = (int(self.wall), 0, 10, "GPU-example")
            store.write([row], self.wall, 0)
            cache = self.make_cache(store)
            with patch.object(store, "read", wraps=store.read) as read:
                cache.read("month")
                store.write([row], self.wall, 1)  # not due
                cache.read("month")
                read.assert_called_once()
                with (
                    patch(
                        "gpuroster.monitoring.history.sqlite3.connect",
                        side_effect=sqlite3.OperationalError("test failure"),
                    ),
                    self.assertRaises(sqlite3.Error),
                ):
                    store.write([row], self.wall, 61)
                cache.read("month")
                read.assert_called_once()
                # Same wall time: monotonic write identity still distinguishes it.
                store.write([row], self.wall, 62)
                cache.read("month")
                self.assertEqual(read.call_count, 2)

    def test_write_during_query_cannot_mark_old_result_current(self):
        def racing_read(*args):
            self.store.last_write_monotonic = 200
            return {"datasets": []}

        self.store.read.side_effect = racing_read
        self.cache.read("month")
        self.cache.read("month")
        self.assertEqual(self.store.read.call_count, 2)
        self.cache.read("month")
        self.assertEqual(self.store.read.call_count, 2)

    def test_clock_steps_in_both_directions_expire_results(self):
        for step in (3600, -7200):
            with self.subTest(step=step):
                self.cache.read("month")
                before = self.store.read.call_count
                self.wall += step
                self.cache.read("month")
                self.assertEqual(self.store.read.call_count, before + 1)

    def test_calendar_day_changes_expire_today_including_dst_days(self):
        for timezone, date in (
            ("UTC", "2026-10-01"),
            ("Asia/Almaty", "2026-10-01"),
            ("America/New_York", "2026-03-08"),
            ("America/New_York", "2026-11-01"),
        ):
            with self.subTest(timezone=timezone, date=date):
                self.wall = (
                    datetime.fromisoformat(date + "T23:59:59")
                    .replace(tzinfo=ZoneInfo(timezone))
                    .timestamp()
                )
                cache = self.make_cache(self.store, timezone)
                cache.read("today")
                before = self.store.read.call_count
                self.advance(2)
                cache.read("today")
                self.assertEqual(self.store.read.call_count, before + 1)

    def test_failure_is_shared_sanitized_and_retried_without_old_success(self):
        self.cache.read("month")
        self.advance(60)
        self.store.read.side_effect = sqlite3.OperationalError("PRIVATE_TEST_MARKER")
        failures = self.concurrent_reads()
        self.assertEqual(set(failures), {"history_unavailable"})
        self.assertEqual(self.store.read.call_count, 2)
        self.advance(1)
        self.store.read.side_effect = None
        self.assertIsInstance(self.cache.read("month"), dict)
        self.assertEqual(self.store.read.call_count, 3)

    def test_successful_write_allows_immediate_retry_after_query_failure(self):
        self.store.read.side_effect = sqlite3.OperationalError("test failure")
        with self.assertRaises(sqlite3.Error):
            self.cache.read("month")
        self.store.last_write_monotonic = 200
        self.store.read.side_effect = None
        self.cache.read("month")
        self.assertEqual(self.store.read.call_count, 2)

    def test_age_includes_query_duration_and_failure_backoff_starts_after_query(self):
        def slow_read(*args):
            self.advance(2)
            return {"datasets": []}

        self.store.read.side_effect = slow_read
        self.assertEqual(self.cache.read("month")["query"]["age_seconds"], 2)

        def slow_failure(*args):
            self.advance(2)
            raise sqlite3.OperationalError("test failure")

        self.advance(60)
        self.store.read.side_effect = slow_failure
        for _ in range(2):
            with self.assertRaises(sqlite3.Error):
                self.cache.read("month")
        self.assertEqual(self.store.read.call_count, 2)

    def test_blocked_month_query_does_not_block_another_range(self):
        entered, release = threading.Event(), threading.Event()

        def read(range_key, *args):
            if range_key == "month":
                entered.set()
                if not release.wait(timeout=5):
                    raise RuntimeError("test query was not released")
            return {"datasets": []}

        self.store.read.side_effect = read
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            month = pool.submit(self.cache.read, "month")
            try:
                self.assertTrue(entered.wait(timeout=2))
                week = pool.submit(self.cache.read, "week")
                self.assertEqual(week.result(timeout=2)["datasets"], [])
            finally:
                release.set()
            month.result(timeout=2)

    def test_range_validation_precedes_provider_access(self):
        with self.assertRaises(ValueError):
            self.cache.read("invalid")
        self.store.read.assert_not_called()
        for range_key in ("today", "week", "month") * 3:
            self.cache.read(range_key)
        self.assertEqual(self.store.read.call_count, 3)


if __name__ == "__main__":
    unittest.main()
