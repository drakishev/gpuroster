"""Synthetic HTTP clients and optional real NVML-worker timing; aggregates only.

Run: python -m benchmarks.shared_collector
Optional hardware run: python -m benchmarks.shared_collector --nvml
"""

import argparse
import concurrent.futures
import json
import math
import resource
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from functools import partial

import psutil
from werkzeug.serving import WSGIRequestHandler, make_server

from gpuroster.app import create_app
from gpuroster.monitoring.models import GPU, GPUProcess, System
from gpuroster.monitoring.nvml import NVMLWorker
from gpuroster.monitoring.service import CollectorService
from gpuroster.settings import load_settings
from gpuroster.server import HTTPServer


class FixtureGPU:
    name = "synthetic"

    def __init__(self):
        self.calls = 0

    def gpus(self):
        self.calls += 1
        time.sleep(0.005)  # fixed source cost, independent of API clients
        return tuple(
            GPU(index, f"GPU-example-{index}", "Example GPU", 40, 1024, 8192, 40, 100)
            for index in range(8)
        )

    def processes(self, devices):
        return tuple(
            GPUProcess(
                device.uuid,
                device.index,
                100 + device.index,
                "example-user",
                "python",
                1024,
            )
            for device in devices
        )

    def close(self):
        pass


class FixtureSystem:
    def collect(self):
        return System(20, 1000000000, 8000000000, 12.5)


class QuietHandler(WSGIRequestHandler):
    def log_request(self, *args, **kwargs):
        pass


def percentiles(values):
    return {
        "median_ms": round(statistics.median(values), 3),
        "p95_ms": round(sorted(values)[math.ceil(len(values) * 0.95) - 1], 3),
    }


def http_case(clients, duration, server_kind="werkzeug"):
    with tempfile.TemporaryDirectory() as directory:
        config = load_settings({"GPUROSTER_COLLECT_INTERVAL": "0.25"})
        config["DB_PATH"] = str(Path(directory) / "history.db")
        gpu = FixtureGPU()
        service = CollectorService(
            config, config["DB_PATH"], gpu=gpu, system=FixtureSystem()
        )
        now = time.time()
        devices = gpu.gpus()
        for point in range(120):
            service.history.append(devices, now - (120 - point) * 0.25)
        service.collect_once()
        application = create_app(config, service)
        stop = threading.Event()
        if server_kind == "waitress":
            server = HTTPServer(application, "127.0.0.1", 0)
            target = partial(server.run, stop)
            port = server.server.effective_port
        else:
            server = make_server(
                "127.0.0.1", 0, application, threaded=True, request_handler=QuietHandler
            )
            target = server.serve_forever
            port = server.server_port
        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{port}/api/stats"
        service.start()
        before_calls = gpu.calls
        latencies = []
        failures = []
        started = time.monotonic()
        cpu_started = time.process_time()
        end = started + duration

        def client():
            while time.monotonic() < end:
                request_started = time.monotonic()
                try:
                    with urllib.request.urlopen(url, timeout=3) as response:
                        data = json.load(response)
                    if len(data["gpus"]) != 8 or data["schema_version"] != 2:
                        failures.append("invalid_snapshot")
                except Exception:
                    failures.append("request_failed")
                latencies.append((time.monotonic() - request_started) * 1000)
                remaining = min(
                    0.2 - (time.monotonic() - request_started), end - time.monotonic()
                )
                if remaining > 0:
                    time.sleep(remaining)

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=clients) as pool:
                list(pool.map(lambda _: client(), range(clients)))
            elapsed = time.monotonic() - started
            cpu = time.process_time() - cpu_started
            calls = gpu.calls - before_calls
            assert calls <= math.ceil(elapsed / 0.25) + 1, (
                "Collection multiplied with clients"
            )
            return {
                "server": server_kind,
                "clients": clients,
                "duration_s": round(elapsed, 3),
                "api_requests": len(latencies),
                "failures": len(failures),
                "hardware_cycles": calls,
                "cycle_interval_s": 0.25,
                "cpu_seconds": round(cpu, 3),
                "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                **percentiles(latencies),
            }
        finally:
            service.stop()
            if server_kind == "waitress":
                stop.set()
                thread.join(3)
                server.close()
            else:
                server.shutdown()
                thread.join(3)
                server.server_close()


def native_case():
    provider = NVMLWorker()
    try:
        start = time.perf_counter()
        devices = provider.gpus()
        cold_ms = (time.perf_counter() - start) * 1000
        worker = psutil.Process(provider.worker.pid)
        before = worker.cpu_times()
        samples = []
        for _ in range(100):
            start = time.perf_counter()
            provider.gpus()
            samples.append((time.perf_counter() - start) * 1000)
        after = worker.cpu_times()
        return {
            "gpus": len(devices),
            "samples": len(samples),
            "cold_start_ms": round(cold_ms, 3),
            "worker_cpu_ms_per_sample": round(
                (after.user + after.system - before.user - before.system)
                * 1000
                / len(samples),
                3,
            ),
            "worker_rss_kib": worker.memory_info().rss // 1024,
            **percentiles(samples),
        }
    finally:
        provider.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clients", type=int)
    parser.add_argument("--duration", type=float, default=3)
    parser.add_argument("--nvml", action="store_true")
    parser.add_argument(
        "--server", choices=("werkzeug", "waitress"), default="werkzeug"
    )
    args = parser.parse_args()
    if args.nvml:
        print(json.dumps(native_case(), indent=2))
    elif args.clients:
        print(json.dumps(http_case(args.clients, args.duration, args.server)))
    else:
        result = []
        for clients in (1, 5, 20, 50):
            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "benchmarks.shared_collector",
                    "--clients",
                    str(clients),
                    "--duration",
                    str(args.duration),
                    "--server",
                    args.server,
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=60,
            )
            result.append(json.loads(run.stdout))
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
