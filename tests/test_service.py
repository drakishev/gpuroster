import concurrent.futures
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from monitoring.models import GPU, System
from monitoring.service import CollectorService
from monitoring.sessions import SessionRecords
from settings import load_settings


class ServiceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = load_settings({})
        self.gpu = Mock()
        self.gpu.name = "fixture"
        self.gpu.gpus.return_value = (
            GPU(0, "GPU-example", "Example", 20, 10, 100, 30, 20),
        )
        self.gpu.processes.return_value = ()
        self.system = Mock()
        self.system.collect.return_value = System(25, 10, 100, 10)
        self.sessions = Mock()
        self.sessions.records.return_value = SessionRecords(())
        self.sessions.connections.return_value = ()
        self.service = CollectorService(
            self.config,
            Path(directory.name) / "test.db",
            self.gpu,
            self.system,
            self.sessions,
        )
        self.addCleanup(self.service.stop)

    def test_concurrent_readers_never_collect_and_cannot_mutate_cache(self):
        self.service.collect_once()

        def read(_):
            value = self.service.snapshot()
            value["gpus"][0]["name"] = "mutated"
            value["history"].clear()
            return value["sequence"]

        for count in (1, 5, 20, 50):
            with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
                self.assertEqual(set(pool.map(read, range(count * 5))), {1})
        self.gpu.gpus.assert_called_once()
        self.gpu.processes.assert_called_once()
        self.system.collect.assert_called_once()
        self.assertEqual(self.service.snapshot()["gpus"][0]["name"], "Example")
        self.assertTrue(self.service.snapshot()["history"])

    def test_atomic_snapshot_reads_continue_while_backend_is_blocked(self):
        self.service.collect_once()
        entered, release = threading.Event(), threading.Event()
        original = self.gpu.gpus.return_value

        def blocked():
            entered.set()
            release.wait(3)
            return original

        self.gpu.gpus.side_effect = blocked
        thread = threading.Thread(target=self.service.collect_once)
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.service.snapshot()["sequence"], 1)
            self.assertEqual(self.service.snapshot()["gpus"][0]["utilization"], 20)
        finally:
            release.set()
            thread.join(3)
        self.assertEqual(self.service.snapshot()["sequence"], 2)

    def test_stopped_collector_ages_to_stale_without_refreshing_timestamp(self):
        self.service.collect_once()
        timestamp = self.service.snapshot()["timestamp"]
        with patch(
            "monitoring.service.time.monotonic", return_value=time.monotonic() + 20
        ):
            data = self.service.snapshot()
        self.assertEqual(data["health"]["sources"]["gpus"]["status"], "stale")
        self.assertEqual(data["timestamp"], timestamp)

    def test_failure_is_partial_and_recovers_next_cycle(self):
        self.gpu.gpus.side_effect = RuntimeError("PRIVATE_TEST_MARKER")
        self.service.collect_once()
        failed = self.service.snapshot()
        self.assertEqual(failed["health"]["sources"]["gpus"]["status"], "unavailable")
        self.assertNotIn("PRIVATE_TEST_MARKER", str(failed))
        self.assertEqual(failed["system"]["cpu_percent"], 25)
        self.gpu.gpus.side_effect = None
        self.service.collect_once()
        self.assertEqual(self.service.snapshot()["health"]["status"], "ok")

    def test_optional_sessions_have_independent_cadence_and_visible_truncation(self):
        self.service.config["SHOW_SESSIONS"] = True
        self.sessions.records.return_value = SessionRecords((), truncated=True)
        with patch("monitoring.service.time.monotonic", return_value=100):
            self.service.collect_once()
        with patch("monitoring.service.time.monotonic", return_value=103):
            self.service.collect_once()
        self.sessions.records.assert_called_once()
        with patch("monitoring.service.time.monotonic", return_value=161):
            self.service.collect_once()
            data = self.service.snapshot()
        self.assertEqual(self.sessions.records.call_count, 2)
        self.assertEqual(data["health"]["sources"]["sessions"]["status"], "partial")
        self.assertTrue(data["health"]["sources"]["sessions"]["truncated"])

    def test_repeated_source_failures_are_rate_limited(self):
        self.gpu.gpus.side_effect = RuntimeError("PRIVATE_TEST_MARKER")
        with patch("monitoring.service.LOG.warning") as warning:
            self.service.collect_once()
            self.service.collect_once()
        warning.assert_called_once_with(
            "Collection unavailable: source=%s code=%s", "gpus", "collection_failed"
        )

    def test_storage_failure_does_not_discard_hardware_snapshot(self):
        self.service.store.write = Mock(side_effect=RuntimeError("PRIVATE_TEST_MARKER"))
        self.service.collect_once()
        data = self.service.snapshot()
        self.assertEqual(data["gpus"][0]["utilization"], 20)
        self.assertEqual(data["health"]["sources"]["history"]["status"], "unavailable")

    def test_start_is_idempotent_and_stop_closes_provider(self):
        self.service.start()
        first = self.service._thread
        self.service.start()
        self.assertIs(first, self.service._thread)
        self.service.stop()
        self.assertFalse(first.is_alive())
        self.gpu.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
