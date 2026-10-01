"""Demo isolation is enforced at the host I/O boundaries, including sessions."""

import concurrent.futures
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from gpuroster.app import create_app
from gpuroster.monitoring.demo import DemoHistory, devices_at
from gpuroster.server import HTTPServer, serve
from gpuroster.settings import load_settings


class DemoTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.database = self.directory / "do-not-touch.db"
        self.database.write_bytes(b"existing private history sentinel")
        self.config = {
            **load_settings({}),
            "DEMO": True,
            "DB_PATH": str(self.database),
            "BIND_PORT": 0,
        }

    def test_full_demo_reads_no_host_sources_and_never_opens_sqlite(self):
        boundaries = (
            "subprocess.run",
            "sqlite3.connect",
            "psutil.cpu_percent",
            "psutil.virtual_memory",
            "psutil.process_iter",
            "psutil.Process",
            "gpuroster.monitoring.nvml.NVMLWorker.gpus",
        )
        with ExitStack() as stack:
            for target in boundaries:
                stack.enter_context(
                    patch(target, side_effect=AssertionError("Host I/O in demo"))
                )
            application = create_app({**self.config, "SHOW_SESSIONS": True})
            service = application.extensions["collector"]
            service.collect_once()
            client = application.test_client()
            stats = client.get("/api/stats").json
            self.assertEqual(stats["mode"], "demo")
            self.assertEqual(stats["health"]["status"], "ok")
            self.assertEqual(len(stats["gpus"]), 4)
            self.assertTrue(all(user.startswith("demo-") for user in stats["user_gpu"]))
            self.assertTrue(
                all(len(points) == 120 for points in stats["history"].values())
            )
            for route in ("/api/sessions", "/api/login_stats", "/api/connections"):
                response = client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json)
                self.assertEqual(response.headers["X-GPU-Roster-Mode"], "demo")
            for range_key in ("today", "week", "month"):
                response = client.get("/api/gpu_history?range=" + range_key)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json["mode"], "demo")
                self.assertEqual(len(response.json["datasets"]), 4)
            self.assertEqual(client.get("/api/gpu_history?range=bad").status_code, 400)
        self.assertEqual(
            self.database.read_bytes(), b"existing private history sentinel"
        )
        self.assertEqual(list(self.directory.iterdir()), [self.database])

    def test_serving_demo_does_not_acquire_a_database_lock(self):
        application = create_app(self.config)
        service = application.extensions["collector"]
        stop = threading.Event()

        def read_until_ready(event):
            deadline = time.monotonic() + 2
            while service.snapshot()["sequence"] == 0 and time.monotonic() < deadline:
                event.wait(0.01)
            event.set()

        with (
            patch(
                "gpuroster.server.InstanceLock",
                side_effect=AssertionError("Demo locked a database"),
            ),
            patch.object(HTTPServer, "run", side_effect=read_until_ready),
        ):
            serve(application, stop)
        self.assertGreater(service.snapshot()["sequence"], 0)
        self.assertFalse(service._thread.is_alive())
        self.assertEqual(list(self.directory.iterdir()), [self.database])

    def test_demo_keeps_authentication_and_session_privacy(self):
        application = create_app(
            {**self.config, "AUTH_USER": "viewer", "AUTH_PASSWORD": "test-only"}
        )
        client = application.test_client()
        for route in ("/", "/api/stats", "/static/dashboard.css"):
            self.assertEqual(client.get(route).status_code, 401)
        self.assertEqual(
            client.get("/api/sessions", auth=("viewer", "test-only")).status_code, 403
        )
        self.assertIn(
            b"Synthetic demo", client.get("/", auth=("viewer", "test-only")).data
        )

    def test_shared_demo_cache_is_isolated_from_mutating_clients(self):
        service = create_app(self.config).extensions["collector"]
        service.collect_once()

        def read(_):
            snapshot = service.snapshot()
            snapshot["gpus"].clear()
            return snapshot["sequence"]

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            self.assertEqual(set(pool.map(read, range(100))), {1})
        self.assertEqual(len(service.snapshot()["gpus"]), 4)

    def test_generated_samples_are_stable_for_a_time_and_change_over_time(self):
        self.assertEqual(devices_at(1000), devices_at(1000))
        self.assertNotEqual(
            devices_at(1000)[0].utilization, devices_at(1010)[0].utilization
        )
        for timestamp in (0, 1000, 1000000000):
            for device in devices_at(timestamp):
                self.assertGreaterEqual(device.utilization, 0)
                self.assertLessEqual(device.utilization, 100)
                self.assertLessEqual(device.memory_used, device.memory_total)
        self.assertIsNone(devices_at(1000)[2].power)

    def test_history_uses_bounded_utc_buckets_and_configured_calendar_day(self):
        now = datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc).timestamp()
        store = DemoHistory()
        local = store.read("today", now, "Asia/Almaty")
        self.assertEqual(local["labels"][0], "2026-01-01T19:00:00+00:00")
        for range_key in ("today", "week", "month"):
            result = store.read(range_key, now)
            self.assertLessEqual(result["points"], 301)
            self.assertEqual(result["labels"], sorted(result["labels"]))
            for dataset in result["datasets"]:
                self.assertEqual(len(dataset["data"]), result["points"])
        with self.assertRaises(ValueError):
            store.read("invalid", now)

    def test_demo_is_explicit_and_invalid_flag_is_rejected(self):
        self.assertFalse(load_settings({})["DEMO"])
        self.assertTrue(load_settings({"GPUROSTER_DEMO": "1"})["DEMO"])
        with self.assertRaises(ValueError):
            load_settings({"GPUROSTER_DEMO": "maybe"})


if __name__ == "__main__":
    unittest.main()
