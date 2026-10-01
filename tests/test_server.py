"""Production ownership, bind failure, shutdown, and credential regressions."""

import os
import secrets
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import psutil

from gpuroster.app import create_app
from gpuroster.server import HTTPServer, InstanceLock, serve
from gpuroster.settings import load_settings


class ServerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.database = self.directory / "history.db"
        self.config = {
            **load_settings({}),
            "DB_PATH": str(self.database),
            "BIND_PORT": 0,
        }
        self.service = Mock()
        self.application = create_app(self.config, self.service)

    def test_remote_bind_refused_before_collection_or_state_creation(self):
        self.application.config["BIND_HOST"] = "0.0.0.0"
        with self.assertRaises(ValueError):
            serve(self.application, threading.Event())
        self.service.start.assert_not_called()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_database_lock_rejects_second_owner_and_survives_release(self):
        with InstanceLock(self.database):
            with self.assertRaises(BlockingIOError):
                with InstanceLock(self.directory / "." / "history.db"):
                    self.fail("Second collector acquired the same history database")
        lock = Path(str(self.database) + ".lock")
        inode = lock.stat().st_ino
        with InstanceLock(self.database):
            self.assertEqual(lock.stat().st_ino, inode)
        self.assertFalse(self.database.exists())

    def test_bind_failure_cleans_up_workers_sockets_and_ownership(self):
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            self.application.config["BIND_PORT"] = occupied.getsockname()[1]
            before = psutil.Process().num_fds()
            with self.assertRaises(OSError):
                serve(self.application, threading.Event())
            self.assertEqual(psutil.Process().num_fds(), before)
        self.service.start.assert_not_called()
        self.assertFalse(
            any(t.name.startswith("waitress-") for t in threading.enumerate())
        )
        with InstanceLock(self.database):
            pass

    def test_server_failure_stops_collector_and_releases_database(self):
        with (
            patch.object(HTTPServer, "run", side_effect=RuntimeError("fixture")),
            self.assertRaises(RuntimeError),
        ):
            serve(self.application, threading.Event())
        self.service.start.assert_called_once()
        self.service.stop.assert_called_once()
        with InstanceLock(self.database):
            pass

    def test_clean_stop_releases_workers_and_collector(self):
        stop = threading.Event()
        stop.set()
        serve(self.application, stop)
        self.service.start.assert_called_once()
        self.service.stop.assert_called_once()
        self.assertFalse(
            any(t.name.startswith("waitress-") for t in threading.enumerate())
        )

    def test_password_file_and_conflicting_configuration(self):
        path = self.directory / "password"
        password = secrets.token_urlsafe(24)
        path.write_text(password + "\n")
        environment = {
            "GPUROSTER_AUTH_USER": "viewer",
            "GPUROSTER_AUTH_PASSWORD_FILE": str(path),
        }
        self.assertEqual(load_settings(environment)["AUTH_PASSWORD"], password)
        with self.assertRaises(ValueError):
            load_settings({**environment, "GPUROSTER_AUTH_PASSWORD": "ambiguous"})
        for content in ("", "\n", "two\nlines"):
            path.write_text(content)
            with self.assertRaises(ValueError):
                load_settings(environment)
        path.unlink()
        with self.assertRaisesRegex(
            ValueError, "Cannot read GPUROSTER_AUTH_PASSWORD_FILE"
        ):
            load_settings(environment)

    def test_installed_default_state_path_has_no_import_side_effect(self):
        state = self.directory / "state"
        config = load_settings({"XDG_STATE_HOME": str(state)})
        self.assertEqual(config["DB_PATH"], str(state / "gpuroster" / "history.db"))
        self.assertFalse(state.exists())
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(state)}):
            create_app()
        self.assertFalse(state.exists())

    def test_systemd_credential_directory_is_supported_without_specifier_expansion(
        self,
    ):
        password = secrets.token_urlsafe(24)
        (self.directory / "dashboard-password").write_text(password)
        config = load_settings(
            {
                "GPUROSTER_AUTH_USER": "viewer",
                "CREDENTIALS_DIRECTORY": str(self.directory),
            }
        )
        self.assertEqual(config["AUTH_PASSWORD"], password)


if __name__ == "__main__":
    unittest.main()
