"""Hardware-independent regressions. All persistence uses temporary databases."""

import base64
import importlib.util
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app
from settings import load_settings


class BackendTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="gpuroster-test-")
        self.addCleanup(directory.cleanup)
        self.db = str(Path(directory.name) / "history.db")
        for patcher in (
            patch.dict(app.app.config, {**load_settings({}), "TESTING": True}),
            patch.object(app, "DB_PATH", self.db),
            patch.object(app, "_last_db_write", 0),
            patch.dict(app._gpu_history, {}, clear=True),
            patch.dict(app._gpu_ema, {}, clear=True),
            patch.dict(app._failure_logs, {}, clear=True),
            patch.dict(
                app._sampler_health,
                {"status": "not_started", "last_sample": None, "last_write": None},
                clear=True,
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = app.app.test_client()

    def mock_stats(self):
        for name, value in (
            ("get_gpu_stats", []),
            ("get_gpu_processes", []),
            (
                "get_system_stats",
                {
                    "cpu_percent": 12,
                    "ram_used_gb": 1,
                    "ram_total_gb": 4,
                    "ram_percent": 25,
                },
            ),
            ("get_active_connections", []),
        ):
            patcher = patch.object(app, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def enable_auth(self):
        password = secrets.token_hex(24)
        app.app.config.update(AUTH_USER="viewer", AUTH_PASSWORD=password)
        encoded = base64.b64encode(f"viewer:{password}".encode()).decode()
        return {"Authorization": "Basic " + encoded}

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
        self.assertEqual(app.app.config["BIND_HOST"], "127.0.0.1")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'id="conn-tbody"', response.data)
        self.assertNotIn(b'id="session-tbody"', response.data)
        self.assertIn(b'data-sessions-enabled="false"', response.data)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_session_endpoints_are_disabled_before_collection(self):
        with (
            patch.object(app, "_run") as run,
            patch.object(app, "get_active_connections") as connections,
        ):
            for route in ("/api/login_stats", "/api/sessions", "/api/connections"):
                self.assertEqual(self.client.get(route).status_code, 403)
        run.assert_not_called()
        connections.assert_not_called()

    def test_stats_does_not_collect_disabled_session_details(self):
        self.mock_stats()
        response = self.client.get("/api/stats")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["connections"], [])
        app.get_active_connections.assert_not_called()

    def test_remote_requests_and_dns_rebinding_are_denied(self):
        with patch.object(app, "get_gpu_stats") as collector:
            response = self.client.get(
                "/api/stats",
                environ_overrides={"REMOTE_ADDR": "192.0.2.10"},
                headers={"X-Forwarded-For": "127.0.0.1"},
            )
            self.assertEqual(response.status_code, 403)
            response = self.client.get(
                "/api/stats", base_url="http://untrusted.example"
            )
            self.assertEqual(response.status_code, 403)
        collector.assert_not_called()

    def test_auth_covers_home_api_and_static_before_collection(self):
        headers = self.enable_auth()
        with patch.object(app, "get_gpu_stats") as collector:
            for route in ("/", "/api/stats", "/static/dashboard.js"):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 401)
                self.assertIn("Basic", response.headers["WWW-Authenticate"])
            collector.assert_not_called()
        response = self.client.get(
            "/", headers=headers, environ_overrides={"REMOTE_ADDR": "192.0.2.10"}
        )
        self.assertEqual(response.status_code, 200)

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

    def test_remote_launcher_refuses_missing_auth(self):
        app.app.config["BIND_HOST"] = "0.0.0.0"
        with (
            patch.object(app, "start_sampler") as sampler,
            self.assertRaises(SystemExit),
        ):
            app.main()
        sampler.assert_not_called()

    def test_process_arguments_are_not_read_by_default(self):
        process = Mock()
        process.username.return_value = "example-user"
        process.name.return_value = "python"
        process.cmdline.return_value = [
            "python",
            "--credential",
            "SENSITIVE_TEST_MARKER",
        ]
        with (
            patch.object(app, "_build_uuid_map", return_value={"GPU-example": 0}),
            patch.object(app, "_run", return_value="123, GPU-example, 1024"),
            patch.object(app.psutil, "Process", return_value=process),
        ):
            self.assertEqual(app.get_gpu_processes()[0]["command"], "python")
            process.cmdline.assert_not_called()
            app.app.config["SHOW_COMMANDS"] = True
            self.assertIn(
                "SENSITIVE_TEST_MARKER", app.get_gpu_processes()[0]["command"]
            )

    def test_command_failures_are_safe_and_distinguishable(self):
        for error, expected in (
            (FileNotFoundError("private path"), "command_missing"),
            (
                subprocess.TimeoutExpired(["tool"], 1, stderr="private output"),
                "command_timeout",
            ),
            (
                subprocess.CalledProcessError(1, ["tool"], stderr="private output"),
                "command_failed",
            ),
        ):
            with (
                self.subTest(expected=expected),
                patch.object(app.subprocess, "run", side_effect=error),
                self.assertRaises(app.CollectionError) as caught,
            ):
                app._run(["tool"])
            self.assertEqual(str(caught.exception), expected)

    def test_real_subprocess_timeout_is_bounded(self):
        app.app.config["COMMAND_TIMEOUT"] = 0.05
        started = time.monotonic()
        with self.assertRaisesRegex(app.CollectionError, "command_timeout"):
            app._run([sys.executable, "-c", "import time; time.sleep(5)"])
        self.assertLess(time.monotonic() - started, 2)

    def test_collector_children_do_not_inherit_dashboard_credentials(self):
        with (
            patch.dict(app.os.environ, {"GPUROSTER_AUTH_PASSWORD": "TEST_ONLY"}),
            patch.object(app.subprocess, "run", return_value=Mock(stdout="ok")) as run,
        ):
            self.assertEqual(app._run(["tool"]), "ok")
        self.assertNotIn("GPUROSTER_AUTH_PASSWORD", run.call_args.kwargs["env"])
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")

    def test_sampler_start_is_idempotent_within_one_process(self):
        thread = Mock()
        thread.is_alive.return_value = True
        with (
            patch.object(app, "_sampler_thread", None),
            patch.object(app, "_sampler_stop"),
            patch.object(app.threading, "Thread", return_value=thread) as constructor,
        ):
            app.start_sampler()
            app.start_sampler()
        constructor.assert_called_once()
        thread.start.assert_called_once()
        self.assertTrue(Path(self.db).exists())

    def test_unsupported_metrics_keep_the_gpu(self):
        with patch.object(
            app,
            "_run",
            return_value='0, "Example, GPU", N/A, 10, 100, [Not Supported], N/A',
        ):
            gpu = app.get_gpu_stats()[0]
        self.assertEqual(gpu["name"], "Example, GPU")
        self.assertIsNone(gpu["utilization"])
        self.assertIsNone(gpu["temperature"])
        self.assertIsNone(gpu["power"])

    def test_malformed_gpu_output_is_visible(self):
        for output in (
            "driver diagnostic",
            "0, Example, NaN, 10, 100, 20, 30",
            "0, Example, -1, 10, 100, 20, 30",
        ):
            with (
                patch.object(app, "_run", return_value=output),
                self.assertRaises(app.CollectionError),
            ):
                app.get_gpu_stats()

    def test_partial_failure_returns_health_without_private_error_text(self):
        self.mock_stats()
        app.get_gpu_stats.side_effect = app.CollectionError("command_timeout")
        app.get_gpu_processes.side_effect = RuntimeError("PRIVATE_TEST_MARKER")
        response = self.client.get("/api/stats")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["health"]["status"], "degraded")
        self.assertEqual(
            response.json["health"]["sources"]["gpus"]["error"], "command_timeout"
        )
        self.assertEqual(response.json["system"]["cpu_percent"], 12)
        self.assertNotIn(b"PRIVATE_TEST_MARKER", response.data)

    def test_unknown_process_memory_is_not_reported_as_zero(self):
        self.mock_stats()
        app.get_gpu_processes.return_value = [
            {
                "gpu": 0,
                "pid": 123,
                "user": "example-user",
                "command": "python",
                "mem_mb": None,
            }
        ]
        self.assertIsNone(
            self.client.get("/api/stats").json["user_gpu"]["example-user"]["mem_gb"]
        )

    def test_history_query_does_not_create_missing_database(self):
        self.assertEqual(self.client.get("/api/gpu_history").status_code, 503)
        self.assertFalse(Path(self.db).exists())

    def test_invalid_history_range_is_rejected(self):
        self.assertEqual(
            self.client.get("/api/gpu_history?range=invalid").status_code, 400
        )

    def test_failed_database_write_can_retry_immediately(self):
        app._init_db()
        with patch.object(
            app, "get_gpu_stats", return_value=[{"index": 0, "utilization": 20}]
        ):
            with (
                patch.object(
                    app.sqlite3,
                    "connect",
                    side_effect=sqlite3.OperationalError("test-only"),
                ),
                self.assertRaises(sqlite3.Error),
            ):
                app._sample_history()
            self.assertEqual(app._last_db_write, 0)
            app._sample_history()
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM gpu_util").fetchone()[0], 1
            )
        self.assertGreater(app._last_db_write, 0)
        self.assertEqual(app.history_health()["status"], "ok")

    def test_unavailable_utilization_is_a_gap_not_zero(self):
        app._init_db()
        with patch.object(
            app, "get_gpu_stats", return_value=[{"index": 0, "utilization": None}]
        ):
            app._sample_history()
        self.assertIsNone(app._gpu_history[0][0]["util"])
        with sqlite3.connect(self.db) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM gpu_util").fetchone()[0], 0
            )

    def test_sampler_error_is_observable(self):
        stop = Mock()
        stop.is_set.side_effect = [False, True]
        with (
            patch.object(app, "_sampler_stop", stop),
            patch.object(
                app,
                "_sample_history",
                side_effect=app.CollectionError("command_missing"),
            ),
        ):
            app._history_sampler()
        self.assertEqual(app.history_health()["error"], "command_missing")

    def test_history_health_detects_staleness(self):
        app._sampler_health.update(status="ok", last_sample=time.time() - 30)
        self.assertEqual(app.history_health()["status"], "stale")

    def test_repeated_failure_logging_is_rate_limited(self):
        with patch.object(app.app.logger, "warning") as warning:
            app._log_failure("history", "history_failed")
            app._log_failure("history", "history_failed")
            app._log_failure("history", "command_missing")
        self.assertEqual(warning.call_count, 2)

    def test_spoofed_ssh_processes_are_rejected(self):
        def process(title, owner="example-user", executable="/usr/sbin/sshd"):
            return SimpleNamespace(
                info={
                    "name": "sshd",
                    "username": owner,
                    "cmdline": [title],
                    "create_time": time.time() - 60,
                },
                exe=lambda: executable,
            )

        processes = [
            process("sshd: claimed-user@pts/0"),
            process("sshd: example-user@<img/src=x/onerror=alert(1)>"),
            process("sshd: example-user@pts/0", executable="/usr/bin/python3"),
            process("sshd: example-user@notty"),
        ]
        with (
            patch.object(app.psutil, "process_iter", return_value=processes),
            patch.object(
                app.os, "stat", return_value=SimpleNamespace(st_uid=0, st_mode=0o100755)
            ),
        ):
            connections = app.get_active_connections()
        self.assertEqual(len(connections), 1)
        self.assertEqual(connections[0]["user"], "example-user")
        self.assertEqual(connections[0]["type"], "SSH (no TTY)")

    def test_writable_or_user_owned_ssh_binary_is_rejected(self):
        process = SimpleNamespace(
            info={
                "name": "sshd",
                "username": "example-user",
                "cmdline": ["sshd: example-user@pts/0"],
                "create_time": time.time(),
            },
            exe=lambda: "/usr/sbin/sshd",
        )
        for uid, mode in ((1000, 0o100755), (0, 0o100777)):
            with (
                patch.object(app.psutil, "process_iter", return_value=[process]),
                patch.object(
                    app.os,
                    "stat",
                    return_value=SimpleNamespace(st_uid=uid, st_mode=mode),
                ),
            ):
                self.assertEqual(app.get_active_connections(), [])


class SettingsTests(unittest.TestCase):
    def test_invalid_config_fails_closed(self):
        for environment in (
            {"GPUROSTER_AUTH_USER": "viewer"},
            {"GPUROSTER_COMMAND_TIMEOUT": "nan"},
            {"GPUROSTER_COMMAND_TIMEOUT": "0"},
            {"GPUROSTER_COMMAND_TIMEOUT": "31"},
            {"GPUROSTER_SHOW_COMMANDS": "maybe"},
            {"GPUROSTER_PORT": "0"},
        ):
            with self.subTest(environment=environment), self.assertRaises(ValueError):
                load_settings(environment)


if __name__ == "__main__":
    unittest.main()
