"""Mixed live/history HTTP traffic against disposable, synthetic 30-day SQLite.

Hypothesis: sharing history aggregations reduces SQL work and live API latency.
Success: zero errors, fixed hardware cadence, fewer queries than history requests,
and lower mixed-load latency. Cold history requests are included in every run.
"""

import argparse
import concurrent.futures
import json
import logging
import math
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

import psutil

from benchmarks.shared_collector import FixtureGPU, FixtureSystem, percentiles
from gpuroster.app import create_app
from gpuroster.monitoring.history import HistoryStore
from gpuroster.monitoring.service import CollectorService
from gpuroster.server import HTTPServer
from gpuroster.settings import load_settings


class MeasuredStore(HistoryStore):
    def __init__(self, path):
        super().__init__(path)
        self.reads = []
        self.writes = []
        self.write_errors = 0

    def read(self, *args, **kwargs):
        started = time.monotonic()
        try:
            return super().read(*args, **kwargs)
        finally:
            self.reads.append((time.monotonic() - started) * 1000)

    def write(self, *args, **kwargs):
        before = self.last_write_monotonic
        started = time.monotonic()
        try:
            super().write(*args, **kwargs)
        except sqlite3.Error:
            self.write_errors += 1
            raise
        if self.last_write_monotonic != before:
            self.writes.append((time.monotonic() - started) * 1000)


def summary(values):
    return {
        "count": len(values),
        **(percentiles(values) if values else {}),
        "max_ms": round(max(values), 3) if values else None,
    }


def run_case(clients, duration, stats_interval, history_interval):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "history.db"
        now = int(time.time())
        store = MeasuredStore(path)
        store.initialize()
        with sqlite3.connect(path) as connection:
            connection.executemany(
                "INSERT INTO gpu_util VALUES (?,?,?,?)",
                (
                    (
                        now - (30 * 1440 - minute) * 60,
                        gpu,
                        (minute + gpu) % 101,
                        f"GPU-00000000-0000-0000-0000-{gpu:012d}",
                    )
                    for minute in range(30 * 1440)
                    for gpu in range(8)
                ),
            )
        config = load_settings({"GPUROSTER_COLLECT_INTERVAL": "0.25"})
        config["DB_PATH"] = str(path)
        gpu = FixtureGPU()
        service = CollectorService(
            config, path, gpu=gpu, system=FixtureSystem(), store=store
        )
        # Match the stored identities, without real device information.
        original_gpus = gpu.gpus

        def devices():
            from dataclasses import replace

            return tuple(
                replace(device, uuid=f"GPU-00000000-0000-0000-0000-{device.index:012d}")
                for device in original_gpus()
            )

        gpu.gpus = devices
        for point in range(120):
            service.history.append(devices(), now - (120 - point) * 0.25)
        service.collect_once()
        server = HTTPServer(create_app(config, service), "127.0.0.1", 0)
        stop = threading.Event()
        thread = threading.Thread(target=server.run, args=(stop,), daemon=True)
        thread.start()
        service.start()
        url = f"http://127.0.0.1:{server.server.effective_port}"
        before_calls = gpu.calls
        started = time.monotonic()
        cpu_started = time.process_time()
        end = started + duration
        process = psutil.Process()
        rss = [{"seconds": 0, "rss_bytes": process.memory_info().rss}]
        measurements = {"stats": [], "history": []}
        failures = {"stats": 0, "history": 0}
        result_lock = threading.Lock()

        def client(kind):
            interval = stats_interval if kind == "stats" else history_interval
            route = "/api/stats" if kind == "stats" else "/api/gpu_history?range=month"
            while time.monotonic() < end:
                request_started = time.monotonic()
                failed = False
                try:
                    with urllib.request.urlopen(url + route, timeout=15) as response:
                        data = json.load(response)
                    if kind == "stats":
                        failed = len(data["gpus"]) != 8 or data["schema_version"] != 2
                    else:
                        failed = len(data["datasets"]) != 8 or not data["points"]
                except Exception:
                    failed = True
                with result_lock:
                    measurements[kind].append(
                        (time.monotonic() - request_started) * 1000
                    )
                    failures[kind] += int(failed)
                remaining = min(
                    interval - (time.monotonic() - request_started),
                    end - time.monotonic(),
                )
                if remaining > 0:
                    time.sleep(remaining)

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=clients * 2) as pool:
                futures = [
                    pool.submit(client, kind)
                    for _ in range(clients)
                    for kind in measurements
                ]
                while time.monotonic() < end:
                    time.sleep(min(5, max(0, end - time.monotonic())))
                    rss.append(
                        {
                            "seconds": round(time.monotonic() - started, 3),
                            "rss_bytes": process.memory_info().rss,
                        }
                    )
                for future in futures:
                    future.result()
            elapsed = time.monotonic() - started
            calls = gpu.calls - before_calls
            assert calls <= math.ceil(elapsed / 0.25) + 1, "Multiplied collection"
            return {
                "clients": clients,
                "duration_s": round(elapsed, 3),
                "stats_interval_s": stats_interval,
                "history_interval_s": history_interval,
                "seed_rows": 30 * 1440 * 8,
                "db_bytes": path.stat().st_size,
                "hardware_cycles": calls,
                "cycle_interval_s": 0.25,
                "cpu_seconds": round(time.process_time() - cpu_started, 3),
                "failures": failures,
                "http": {key: summary(values) for key, values in measurements.items()},
                "sql_reads": summary(store.reads),
                "sql_writes": summary(store.writes),
                "write_errors": store.write_errors,
                "rss_samples": rss,
            }
        finally:
            stop.set()
            thread.join(3)
            server.close()
            service.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clients", type=int)
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--stats-interval", type=float, default=0.2)
    parser.add_argument("--history-interval", type=float, default=5)
    args = parser.parse_args()
    logging.getLogger("waitress.queue").setLevel(logging.ERROR)
    if args.clients:
        result = run_case(
            args.clients, args.duration, args.stats_interval, args.history_interval
        )
    else:
        result = []
        for clients in (1, 5, 20, 50):
            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "benchmarks.history_load",
                    "--clients",
                    str(clients),
                    "--duration",
                    str(args.duration),
                    "--stats-interval",
                    str(args.stats_interval),
                    "--history-interval",
                    str(args.history_interval),
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=args.duration + 60,
            )
            result.append(json.loads(run.stdout))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
