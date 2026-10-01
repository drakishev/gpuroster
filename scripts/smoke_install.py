"""Run outside the checkout using an installed console script; no GPU needed.

Usage: python scripts/smoke_install.py /path/to/clean/venv/bin/gpuroster [--demo]
Only Python's standard library is required by this smoke driver.
"""

import base64
import argparse
import json
import os
import secrets
import signal
import socket
import subprocess
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    executable = str(Path(args.executable).resolve())
    with tempfile.TemporaryDirectory(prefix="gpuroster-install-") as directory:
        root = Path(directory)
        password = secrets.token_urlsafe(24)
        password_file = root / "dashboard-password"
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
            CREDENTIALS_DIRECTORY=directory,
            XDG_STATE_HOME=str(root / "state"),
        )
        sentinel = root / "existing.db"
        if args.demo:
            sentinel.write_bytes(b"private database sentinel")
            env["GPUROSTER_DB_PATH"] = str(sentinel)
        assert (
            subprocess.check_output(
                [executable, "--version"], cwd=directory, env=env, text=True
            ).strip()
            == "0.6.0"
        )
        for signum in (signal.SIGTERM, signal.SIGINT):
            command = [executable]
            if args.demo:
                if signum == signal.SIGTERM:
                    command.append("--demo")
                else:
                    env["GPUROSTER_DEMO"] = "1"
            process = subprocess.Popen(
                command,
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
                assert snapshot["mode"] == ("demo" if args.demo else "live")
                if args.demo:
                    assert len(snapshot["gpus"]) == 4
                    assert snapshot["health"]["status"] == "ok"
                    assert b"Synthetic demo" in request(url + "/", password)[1]
                    for range_key in ("today", "week", "month"):
                        status, history = request(
                            url + "/api/gpu_history?range=" + range_key, password
                        )
                        assert (
                            status == 200 and len(json.loads(history)["datasets"]) == 4
                        )
                    assert sentinel.read_bytes() == b"private database sentinel"
                    assert not Path(str(sentinel) + ".lock").exists()
                    assert not (root / "state").exists()
                else:
                    assert snapshot["gpus"] == []
                    assert (
                        snapshot["health"]["sources"]["gpus"]["status"] == "unavailable"
                    )
                for route in (
                    "/",
                    "/static/dashboard.js",
                    "/static/dashboard.css",
                    "/static/vendor/chart.umd.js",
                    "/static/vendor/chartjs.LICENSE.txt",
                    "/api/gpu_history",
                ):
                    assert request(url + route)[0] == 401
                    assert request(url + route, password)[0] == 200
                assert request(url + "/api/sessions", password)[0] == 403
                assert request(url + "/", "incorrect")[0] == 401
                if not args.demo:
                    assert (root / "state/gpuroster/history.db").is_file()
                    with socket.socket() as other_socket:
                        other_socket.bind(("127.0.0.1", 0))
                        other_port = other_socket.getsockname()[1]
                    duplicate = subprocess.run(
                        [executable],
                        cwd=directory,
                        env={**env, "GPUROSTER_PORT": str(other_port)},
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
                    f"Installed {'demo isolation' if args.demo else 'live ownership'} smoke passed; {signum.name} shutdown {time.monotonic() - started:.3f}s"
                )
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()


if __name__ == "__main__":
    main()
