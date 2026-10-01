"""Exercise disposable Compose projects; optionally validate real GPUs read-only.

Run from a checkout: python -m scripts.smoke_container --image gpuroster:local
Add --gpus for the Linux/NVIDIA override instead of the GPU-independent cases.
Only aggregate results are printed. Passwords/state are temporary and removed.
"""

import argparse
import base64
import json
import os
import pwd
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from scripts.container_password import create_password

ROOT = Path(__file__).resolve().parents[1]


def command(arguments, env, timeout=120, check=True):
    result = subprocess.run(
        arguments, env=env, capture_output=True, text=True, timeout=timeout
    )
    if check and result.returncode:
        # NVIDIA runtime and Docker errors can contain host paths/identities.
        raise RuntimeError("Container command failed; inspect the local Docker setup")
    return result


def request(url, password=None):
    headers = {}
    if password is not None:
        token = base64.b64encode(f"viewer:{password}".encode()).decode()
        headers["Authorization"] = "Basic " + token
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(
            urllib.request.Request(url, headers=headers), timeout=5
        ) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def run_case(image, mode):
    service = "demo" if mode == "demo" else "gpuroster"
    project = "gpuroster-check-" + secrets.token_hex(6)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GPUROSTER_", "COMPOSE_"))
    }
    with tempfile.TemporaryDirectory(prefix="gpuroster-container-") as directory:
        password = secrets.token_urlsafe(32)
        password_path = create_password(directory, password)
        env.update(
            GPUROSTER_IMAGE=image,
            GPUROSTER_PASSWORD_FILE=str(password_path),
            GPUROSTER_HTTP_PORT="0",
            GPUROSTER_AUTH_USER="viewer",
            GPUROSTER_COLLECT_INTERVAL="0.25",
        )
        override = Path(directory) / "test.json"
        override.write_text(
            json.dumps(
                {
                    "services": {
                        service: {
                            "restart": "no",
                            "healthcheck": {"interval": "1s", "start_period": "1s"},
                        }
                    }
                }
            )
        )
        compose = [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "--project-name",
            project,
            "-f",
            str(ROOT / "compose.yaml"),
        ]
        if mode == "gpu":
            compose.extend(["-f", str(ROOT / "compose.gpu.yaml")])
        compose.extend(["-f", str(override)])

        def docker(*arguments, **options):
            return command(["docker", *arguments], env, **options)

        def current():
            return command([*compose, "ps", "--quiet", service], env).stdout.strip()

        def address(container):
            port = docker("port", container, "18081/tcp").stdout.strip()
            assert port.startswith("127.0.0.1:"), "Unexpected published interface"
            return "http://" + port

        def stop(container, signal):
            before = time.monotonic()
            docker("kill", "--signal", signal, container)
            result = docker("wait", container, timeout=15)
            assert result.stdout.strip() == "0", "Container did not stop cleanly"
            return round(time.monotonic() - before, 3)

        try:
            started = time.monotonic()
            command(
                [
                    *compose,
                    "up",
                    "--detach",
                    "--no-build",
                    "--pull",
                    "never",
                    "--wait",
                    "--wait-timeout",
                    "90",
                    service,
                ],
                env,
            )
            ready_seconds = round(time.monotonic() - started, 3)
            container = current()
            details = json.loads(docker("inspect", container).stdout)[0]
            host = details["HostConfig"]
            assert details["Config"]["User"] == "10001:10001"
            assert host["ReadonlyRootfs"] and not host["Privileged"]
            assert host["CapDrop"] == ["ALL"]
            assert any(
                option.startswith("no-new-privileges") for option in host["SecurityOpt"]
            )
            assert host["Init"] and host["PidsLimit"] == 128
            assert details["State"]["Health"]["Status"] == "healthy"
            assert password not in json.dumps(details), (
                "Secret exposed in container metadata"
            )
            url = address(container)
            for route in (
                "/",
                "/static/dashboard.js",
                "/static/dashboard.css",
                "/static/vendor/chart.umd.js",
                "/api/stats",
            ):
                assert request(url + route)[0] == 401, "Unauthenticated access accepted"
                assert request(url + route, password)[0] == 200, "Packaged route failed"
            assert request(url + "/", "wrong")[0] == 401
            assert request(url + "/api/sessions", password)[0] == 403
            snapshot = json.loads(request(url + "/api/stats", password)[1])
            sequence = snapshot["sequence"]
            time.sleep(0.6)
            snapshot = json.loads(request(url + "/api/stats", password)[1])
            assert snapshot["sequence"] > sequence, "Collector stopped advancing"
            process_matches = vanished = 0
            if mode == "demo":
                assert snapshot["mode"] == "demo" and len(snapshot["gpus"]) == 4
                assert not host["PidMode"] and not host["DeviceRequests"]
                assert not any(
                    m["Destination"] == "/var/lib/gpuroster" for m in details["Mounts"]
                )
                docker(
                    "exec",
                    container,
                    "python",
                    "-c",
                    "from pathlib import Path; assert not Path('/var/lib/gpuroster/history.db').exists(); assert not Path('/var/lib/gpuroster/history.db.lock').exists()",
                )
            else:
                assert snapshot["mode"] == "live"
                if mode == "gpu":
                    assert host["PidMode"] == "host" and host["DeviceRequests"]
                    assert any(
                        m["Destination"] == "/etc/passwd" and not m["RW"]
                        for m in details["Mounts"]
                    )
                    assert (
                        snapshot["collector"]["backend"] == "nvml" and snapshot["gpus"]
                    )
                    for source in ("gpus", "processes", "history"):
                        assert snapshot["health"]["sources"][source]["status"] == "ok"
                    host_memory = next(
                        int(line.split()[1]) * 1024
                        for line in Path("/proc/meminfo").read_text().splitlines()
                        if line.startswith("MemTotal:")
                    )
                    assert snapshot["system"]["ram_total_bytes"] == host_memory
                    for process in snapshot["processes"]:
                        try:
                            uid = os.stat(f"/proc/{process['pid']}").st_uid
                        except FileNotFoundError:
                            vanished += 1
                            continue
                        try:
                            username = pwd.getpwuid(uid).pw_name
                        except KeyError:
                            username = str(uid)
                        assert process["user"] == username, (
                            "Container process owner differs from host"
                        )
                        process_matches += 1
                else:
                    assert snapshot["gpus"] == []
                    assert (
                        snapshot["health"]["sources"]["gpus"]["status"] == "unavailable"
                    )
                # Only this project's disposable volume is written. Seed a
                # synthetic row so restart persistence is testable without GPUs.
                docker(
                    "exec",
                    container,
                    "python",
                    "-c",
                    "import os, sqlite3, time; p='/var/lib/gpuroster/history.db'; assert os.stat(p).st_mode & 0o777 == 0o600; c=sqlite3.connect(p); c.execute('INSERT INTO gpu_util(ts,gpu_idx,util,gpu_uuid) VALUES (?,?,?,?)',(int(time.time()),99,42,'GPU-container-fixture')); c.commit(); c.close()",
                )
                duplicate = command(
                    [*compose, "run", "--no-deps", "--rm", service],
                    env,
                    check=False,
                    timeout=30,
                )
                assert duplicate.returncode == 1, "Duplicate database owner was allowed"
                assert password not in duplicate.stdout + duplicate.stderr
            for key in ("today", "week", "month"):
                assert (
                    request(url + "/api/gpu_history?range=" + key, password)[0] == 200
                )
            int_seconds = stop(container, "SIGINT")
            command(
                [
                    *compose,
                    "up",
                    "--detach",
                    "--no-build",
                    "--pull",
                    "never",
                    "--force-recreate",
                    "--wait",
                    "--wait-timeout",
                    "90",
                    service,
                ],
                env,
            )
            container = current()
            if mode != "demo":
                history = json.loads(
                    request(address(container) + "/api/gpu_history", password)[1]
                )
                assert any(
                    row["id"] == "GPU-container-fixture" for row in history["datasets"]
                ), "History lost after recreation"
            term_seconds = stop(container, "SIGTERM")
            logs = docker("logs", container)
            assert password not in logs.stdout + logs.stderr
            return {
                "mode": mode,
                "passed": True,
                "ready_seconds": ready_seconds,
                "sigint_seconds": int_seconds,
                "sigterm_seconds": term_seconds,
                "gpus": len(snapshot["gpus"]),
                "process_owners_compared": process_matches,
                "processes_vanished_before_comparison": vanished,
            }
        finally:
            # This unpredictable project name identifies only resources made here.
            command(
                [*compose, "--profile", "*", "down", "--volumes", "--remove-orphans"],
                env,
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="gpuroster:local")
    parser.add_argument("--gpus", action="store_true")
    args = parser.parse_args()
    env = dict(os.environ)
    refused = command(
        ["docker", "run", "--pull=never", "--rm", "--read-only", args.image],
        env,
        check=False,
        timeout=15,
    )
    assert refused.returncode == 1, "Unconfigured remote listener was allowed"
    results = [
        run_case(args.image, mode)
        for mode in (("gpu",) if args.gpus else ("demo", "live"))
    ]
    print(
        json.dumps({"unauthenticated_start_refused": True, "cases": results}, indent=2)
    )


if __name__ == "__main__":
    main()
