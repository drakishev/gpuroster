"""Run outside the checkout using an installed console script; no GPU needed.

Usage: python scripts/smoke_install.py /path/to/clean/venv/bin/gpuroster
Only Python's standard library is required by this smoke driver.
"""

import base64
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def request(url, password=None):
    headers = {}
    if password is not None:
        value = base64.b64encode(f"viewer:{password}".encode()).decode()
        headers["Authorization"] = "Basic " + value
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers=headers), timeout=2
        ) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def main():
    executable = str(Path(sys.argv[1]).resolve())
    with tempfile.TemporaryDirectory(prefix="gpuroster-install-") as directory:
        root = Path(directory)
        password = secrets.token_urlsafe(24)
        password_file = root / "password"
        password_file.write_text(password + "\n")
        password_file.chmod(0o600)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("GPUROSTER_", "PYTHON"))
        }
        env.update(
            PATH=directory,  # nvidia-smi is deliberately unavailable
            GPUROSTER_HOST="127.0.0.1",
            GPUROSTER_PORT=str(port),
            GPUROSTER_GPU_BACKEND="smi",
            GPUROSTER_COLLECT_INTERVAL="0.1",
            GPUROSTER_AUTH_USER="viewer",
            GPUROSTER_AUTH_PASSWORD_FILE=str(password_file),
            XDG_STATE_HOME=str(root / "state"),
        )
        assert (
            subprocess.check_output(
                [executable, "--version"], cwd=directory, env=env, text=True
            ).strip()
            == "0.3.0"
        )
        for signum in (signal.SIGTERM, signal.SIGINT):
            process = subprocess.Popen(
                [executable],
                cwd=directory,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            try:
                url = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    assert process.poll() is None, "Installed launcher exited early"
                    try:
                        status, body = request(url + "/api/stats", password)
                        if status == 200:
                            break
                    except (OSError, urllib.error.URLError):
                        pass
                    time.sleep(0.1)
                else:
                    raise AssertionError(
                        "Installed launcher did not publish a snapshot"
                    )
                snapshot = json.loads(body)
                assert snapshot["schema_version"] == 2 and snapshot["sequence"] > 0
                assert snapshot["gpus"] == []
                assert snapshot["health"]["sources"]["gpus"]["status"] == "unavailable"
                for route in ("/", "/static/dashboard.js", "/api/gpu_history"):
                    assert request(url + route)[0] == 401
                    assert request(url + route, password)[0] == 200
                assert request(url + "/api/sessions", password)[0] == 403
                assert request(url + "/", "incorrect")[0] == 401
                assert (root / "state/gpuroster/history.db").is_file()
                duplicate = subprocess.run(
                    [executable],
                    cwd=directory,
                    env=env,
                    capture_output=True,
                    timeout=10,
                )
                assert duplicate.returncode == 1, "Duplicate collector was allowed"
                assert password.encode() not in duplicate.stderr
                started = time.monotonic()
                process.send_signal(signum)
                _, logs = process.communicate(timeout=10)
                assert process.returncode == 0, "Signal shutdown failed"
                assert password.encode() not in logs
                print(
                    f"Installed HTTP/auth/assets/history/lock smoke passed; {signum.name} shutdown {time.monotonic() - started:.3f}s"
                )
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()


if __name__ == "__main__":
    main()
