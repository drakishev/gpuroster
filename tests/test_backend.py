"""Flask access/API regressions: all collection and persistence are synthetic."""

import base64
import importlib.util
import secrets
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import gpuroster.app as app
from gpuroster.monitoring.models import GPU, GPUProcess, System
from gpuroster.monitoring.service import CollectorService
from gpuroster.monitoring.sessions import SessionRecords
from gpuroster.settings import load_settings


class BackendTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="gpuroster-test-")
        self.addCleanup(directory.cleanup)
        self.db = str(Path(directory.name) / "history.db")
        self.config = {**load_settings({}), "DB_PATH": self.db, "TESTING": True}
        self.gpu = Mock()
        self.gpu.name = "fixture"
        self.gpu.gpus.return_value = (
            GPU(0, "GPU-example", "Example GPU", 42, 10, 100, 40, 50),
        )
        self.gpu.processes.return_value = ()
        self.system = Mock()
        self.system.collect.return_value = System(12, 100, 1000, 10)
        self.sessions = Mock()
        self.sessions.records.return_value = SessionRecords(())
        self.sessions.connections.return_value = ()
        self.service = CollectorService(
            self.config, self.db, self.gpu, self.system, self.sessions
        )
        self.application = app.create_app(self.config, self.service)
        self.client = self.application.test_client()

    def ready(self):
        self.service.collect_once()

    def enable_auth(self):
        password = secrets.token_hex(24)
        self.application.config.update(AUTH_USER="viewer", AUTH_PASSWORD=password)
        value = base64.b64encode(f"viewer:{password}".encode()).decode()
        return {"Authorization": "Basic " + value}

    def test_import_has_no_database_or_sampler_side_effects(self):
        spec = importlib.util.spec_from_file_location("import_audit", app.__file__)
        module = importlib.util.module_from_spec(spec)
        with (
            patch.object(sqlite3, "connect") as connect,
            patch.object(threading.Thread, "start") as start,
        ):
            spec.loader.exec_module(module)
        connect.assert_not_called()
        start.assert_not_called()

    def test_safe_defaults_and_hidden_session_panels(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'id="conn-tbody"', response.data)
        self.assertNotIn(b'id="session-tbody"', response.data)
        self.assertIn(b'data-sessions-enabled="false"', response.data)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.application.config["BIND_HOST"], "127.0.0.1")

    def test_session_endpoints_are_disabled_before_cache_access(self):
        with patch.object(self.service, "snapshot") as snapshot:
            for route in ("/api/login_stats", "/api/sessions", "/api/connections"):
                self.assertEqual(self.client.get(route).status_code, 403)
        snapshot.assert_not_called()
        self.sessions.records.assert_not_called()

    def test_remote_requests_and_dns_rebinding_are_denied(self):
        for options in (
            {
                "environ_overrides": {"REMOTE_ADDR": "192.0.2.10"},
                "headers": {"X-Forwarded-For": "127.0.0.1"},
            },
            {"base_url": "http://untrusted.example"},
        ):
            self.assertEqual(self.client.get("/api/stats", **options).status_code, 403)
        self.gpu.gpus.assert_not_called()

    def test_auth_covers_home_api_and_static_before_cache_access(self):
        headers = self.enable_auth()
        with patch.object(self.service, "snapshot") as snapshot:
            for route in (
                "/",
                "/api/stats",
                "/static/dashboard.js",
                "/api/gpu_history",
            ):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 401)
                self.assertIn("Basic", response.headers["WWW-Authenticate"])
            snapshot.assert_not_called()
        self.assertEqual(
            self.client.get(
                "/", headers=headers, environ_overrides={"REMOTE_ADDR": "192.0.2.10"}
            ).status_code,
            200,
        )

    def test_invalid_and_malformed_auth_are_rejected(self):
        self.enable_auth()
        for value in (
            "Basic invalid",
            "Bearer example-only",
            "Basic " + base64.b64encode(b"viewer:wrong").decode(),
        ):
            self.assertEqual(
                self.client.get("/", headers={"Authorization": value}).status_code, 401
            )

    def test_unstarted_collector_returns_visible_unavailability_without_polling(self):
        response = self.client.get("/api/stats")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json["error"], "collector_not_ready")
        self.gpu.gpus.assert_not_called()
        self.assertFalse(Path(self.db).exists())

    def test_many_api_reads_share_the_same_sample(self):
        self.ready()
        for _ in range(20):
            response = self.client.get("/api/stats")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["sequence"], 1)
            self.assertEqual(response.json["gpus"][0]["uuid"], "GPU-example")
            self.assertNotIn("sessions", response.json)
        self.gpu.gpus.assert_called_once()
        self.gpu.processes.assert_called_once()
        self.system.collect.assert_called_once()
        self.sessions.records.assert_not_called()

    def test_partial_failure_exposes_safe_health_without_private_error_text(self):
        self.gpu.gpus.side_effect = RuntimeError("PRIVATE_TEST_MARKER")
        self.ready()
        response = self.client.get("/api/stats")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["health"]["status"], "degraded")
        self.assertEqual(response.json["system"]["cpu_percent"], 12)
        self.assertNotIn(b"PRIVATE_TEST_MARKER", response.data)

    def test_unknown_memory_and_unmapped_gpu_identity_remain_unknown(self):
        self.gpu.processes.return_value = (
            GPUProcess("GPU-unmapped", None, 123, "example-user", "python", None),
        )
        self.ready()
        data = self.client.get("/api/stats").json
        self.assertIsNone(data["user_gpu"]["example-user"]["mem_gb"])
        self.assertEqual(data["user_gpu"]["example-user"]["gpu_indices"], [])
        self.assertEqual(
            data["user_gpu"]["example-user"]["gpu_uuids"], ["GPU-unmapped"]
        )

    def test_history_read_does_not_create_database(self):
        self.assertEqual(self.client.get("/api/gpu_history").status_code, 503)
        self.assertFalse(Path(self.db).exists())

    def test_invalid_history_range_is_rejected(self):
        self.assertEqual(
            self.client.get("/api/gpu_history?range=invalid").status_code, 400
        )

    def test_history_reads_persisted_sample_and_health(self):
        self.ready()
        response = self.client.get("/api/gpu_history?range=today")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["datasets"][0]["id"], "GPU-example")
        self.assertEqual(response.json["health"]["status"], "ok")
        self.gpu.gpus.assert_called_once()

    def test_enabled_session_endpoints_use_one_cached_collection(self):
        self.service.config["SHOW_SESSIONS"] = self.application.config[
            "SHOW_SESSIONS"
        ] = True
        self.ready()
        for _ in range(5):
            for route in ("/api/login_stats", "/api/sessions", "/api/connections"):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["X-Snapshot-Sequence"], "1")
        self.sessions.connections.assert_called_once()
        self.sessions.records.assert_called_once()

    def test_failed_session_source_returns_503(self):
        self.service.config["SHOW_SESSIONS"] = self.application.config[
            "SHOW_SESSIONS"
        ] = True
        self.sessions.records.side_effect = RuntimeError("PRIVATE_TEST_MARKER")
        self.ready()
        response = self.client.get("/api/login_stats")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b"PRIVATE_TEST_MARKER", response.data)


class SettingsTests(unittest.TestCase):
    def test_invalid_config_fails_closed(self):
        for environment in (
            {"GPUROSTER_AUTH_USER": "viewer"},
            {"GPUROSTER_COMMAND_TIMEOUT": "nan"},
            {"GPUROSTER_COMMAND_TIMEOUT": "0"},
            {"GPUROSTER_COMMAND_TIMEOUT": "31"},
            {"GPUROSTER_SHOW_COMMANDS": "maybe"},
            {"GPUROSTER_PORT": "0"},
            {"GPUROSTER_COLLECT_INTERVAL": "nan"},
            {"GPUROSTER_COLLECT_INTERVAL": "0"},
            {"GPUROSTER_SESSION_INTERVAL": "0"},
            {"GPUROSTER_GPU_BACKEND": "invalid"},
            {"GPUROSTER_TIMEZONE": "invalid"},
        ):
            with self.subTest(environment=environment), self.assertRaises(ValueError):
                load_settings(environment)


if __name__ == "__main__":
    unittest.main()
