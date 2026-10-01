"""Measure server resources separately from synthetic HTTP load generation.

Run: python -m benchmarks.server_memory --duration 600 --cooldown 60
Only a temporary, synthetic database is used; no hardware provider is started.
"""

import argparse
import concurrent.futures
import gc
import json
import logging
import math
import multiprocessing
import sqlite3
import sys
import tempfile
import threading
import time
import tracemalloc
import urllib.request
from dataclasses import replace
from pathlib import Path

import psutil

from benchmarks.shared_collector import FixtureGPU, FixtureSystem
from gpuroster.app import create_app
from gpuroster.monitoring.history import HistoryStore
from gpuroster.monitoring.service import CollectorService
from gpuroster.server import HTTPServer
from gpuroster.settings import load_settings


class CounterStore(HistoryStore):
    def __init__(self, path):
        super().__init__(path)
        self.reads = self.writes = self.write_errors = 0

    def read(self, *args, **kwargs):
        self.reads += 1
        return super().read(*args, **kwargs)

    def write(self, *args, **kwargs):
        previous = self.last_write_monotonic
        try:
            super().write(*args, **kwargs)
        except sqlite3.Error:
            self.write_errors += 1
            raise
        self.writes += self.last_write_monotonic != previous


class SyntheticGPU(FixtureGPU):
    def gpus(self):
        return tuple(
            replace(device, uuid=f"GPU-00000000-0000-0000-0000-{device.index:012d}")
            for device in super().gpus()
        )


def server_process(pipe, path, trace_python):
    logging.getLogger("waitress.queue").setLevel(logging.ERROR)
    if trace_python:
        tracemalloc.start(1)
    config = {**load_settings({"GPUROSTER_COLLECT_INTERVAL": "0.25"}), "DB_PATH": path}
    store, gpu = CounterStore(path), SyntheticGPU()
    service = CollectorService(config, path, gpu, FixtureSystem(), store=store)
    devices, now = gpu.gpus(), time.time()
    for point in range(120):
        service.history.append(devices, now - (120 - point) * 0.25)
    service.collect_once()
    application = create_app(config, service)
    server = HTTPServer(application, "127.0.0.1", 0)
    stop = threading.Event()
    thread = threading.Thread(target=server.run, args=(stop,), daemon=True)
    thread.start()
    service.start()
    process = psutil.Process()
    started, first_calls = time.monotonic(), gpu.calls
    cpu = process.cpu_times()
    cpu_start = cpu.user + cpu.system
    try:
        pipe.send({"port": server.server.effective_port})
        while True:
            command = pipe.recv()
            if command == "stop":
                break
            reclaimed = gc.collect() if command == "collect" else None
            memory = process.memory_full_info()
            cpu = process.cpu_times()
            pipe.send(
                {
                    "seconds": round(time.monotonic() - started, 3),
                    "rss_bytes": memory.rss,
                    "uss_bytes": memory.uss,
                    "threads": process.num_threads(),
                    "file_descriptors": process.num_fds(),
                    "cpu_seconds": round(cpu.user + cpu.system - cpu_start, 3),
                    "hardware_cycles": gpu.calls - first_calls,
                    "sql_reads": store.reads,
                    "sql_writes": store.writes,
                    "write_errors": store.write_errors,
                    "health": service.snapshot()["health"]["status"],
                    "collected_objects": reclaimed,
                    **(
                        {"traced_python_bytes": tracemalloc.get_traced_memory()[0]}
                        if trace_python
                        else {}
                    ),
                }
            )
    finally:
        stop.set()
        thread.join(3)
        server.close()
        service.stop()
        pipe.close()


class Latencies:
    """Fixed buckets avoid retaining one measurement per client request."""

    bounds = (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 15000, math.inf)

    def __init__(self):
        self.lock = threading.Lock()
        self.buckets = [0] * len(self.bounds)
        self.requests = self.failures = 0
        self.maximum = 0

    def add(self, milliseconds, failed):
        with self.lock:
            self.requests += 1
            self.failures += int(failed)
            self.maximum = max(self.maximum, milliseconds)
            for index, bound in enumerate(self.bounds):
                if milliseconds <= bound:
                    self.buckets[index] += 1
                    break

    def summary(self):
        cumulative = 0
        p95 = None
        for bound, count in zip(self.bounds, self.buckets):
            cumulative += count
            if cumulative >= math.ceil(self.requests * 0.95):
                p95 = bound if math.isfinite(bound) else None
                break
        return {
            "requests": self.requests,
            "failures": self.failures,
            "p95_upper_bound_ms": p95,
            "max_ms": round(self.maximum, 3),
        }


def run_case(clients, duration, cooldown, sample_interval, trace_python=False):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "history.db"
        HistoryStore(path).initialize()
        now = int(time.time())
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
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(
            target=server_process, args=(child, str(path), trace_python)
        )
        process.start()
        child.close()

        def receive():
            if not parent.poll(15):
                raise RuntimeError("Benchmark server did not respond")
            return parent.recv()

        def sample(phase, command="sample"):
            parent.send(command)
            return {"phase": phase, **receive()}

        try:
            url = f"http://127.0.0.1:{receive()['port']}"
            samples = [sample("before_load")]
            measurements = {"stats": Latencies(), "history": Latencies()}
            end = time.monotonic() + duration

            def client(kind):
                interval = 1 if kind == "stats" else 5
                iteration = 0
                while time.monotonic() < end:
                    started, failed = time.monotonic(), False
                    period = ("today", "week", "month")[iteration % 3]
                    route = (
                        "/api/stats"
                        if kind == "stats"
                        else "/api/gpu_history?range=" + period
                    )
                    try:
                        with urllib.request.urlopen(
                            url + route, timeout=15
                        ) as response:
                            data = json.load(response)
                        if kind == "stats":
                            failed = (
                                len(data["gpus"]) != 8 or data["schema_version"] != 2
                            )
                        else:
                            failed = len(data["datasets"]) != 8 or not data["points"]
                    except Exception:
                        failed = True
                    measurements[kind].add((time.monotonic() - started) * 1000, failed)
                    iteration += 1
                    remaining = min(
                        interval - (time.monotonic() - started), end - time.monotonic()
                    )
                    if remaining > 0:
                        time.sleep(remaining)

            with concurrent.futures.ThreadPoolExecutor(max_workers=clients * 2) as pool:
                futures = [
                    pool.submit(client, kind)
                    for _ in range(clients)
                    for kind in measurements
                ]
                while time.monotonic() < end:
                    time.sleep(min(sample_interval, max(0, end - time.monotonic())))
                    samples.append(sample("load"))
                    print(json.dumps(samples[-1]), file=sys.stderr, flush=True)
                for future in futures:
                    future.result()
            samples.append(sample("after_load"))
            idle_end = time.monotonic() + cooldown
            while time.monotonic() < idle_end:
                time.sleep(min(sample_interval, max(0, idle_end - time.monotonic())))
                samples.append(sample("idle"))
            samples.append(sample("after_gc", "collect"))
            result = {
                "python": sys.version.split()[0],
                "server": "waitress",
                "trace_python": trace_python,
                "clients": clients,
                "requested_load_seconds": duration,
                "requested_idle_seconds": cooldown,
                "stats_interval_seconds": 1,
                "history_interval_seconds": 5,
                "history_ranges": ["today", "week", "month"],
                "cycle_interval_seconds": 0.25,
                "seed_rows": 345600,
                "http": {key: value.summary() for key, value in measurements.items()},
                "server_samples": samples,
            }
            assert all(value.failures == 0 for value in measurements.values())
            assert all(row["write_errors"] == 0 for row in samples)
            parent.send("stop")
            process.join(15)
            assert process.exitcode == 0, "Benchmark server did not stop cleanly"
            return result
        finally:
            if process.is_alive():
                process.terminate()
                process.join(5)
                if process.is_alive():
                    process.kill()
                    process.join(5)
            parent.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clients", type=int, default=50)
    parser.add_argument("--duration", type=float, default=600)
    parser.add_argument("--cooldown", type=float, default=60)
    parser.add_argument("--sample-interval", type=float, default=30)
    parser.add_argument("--trace-python", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.clients <= 50 or any(
        not math.isfinite(value) or value <= 0
        for value in (args.duration, args.cooldown, args.sample_interval)
    ):
        parser.error("clients must be 1–50; times must be positive and finite")
    print(
        json.dumps(
            run_case(
                args.clients,
                args.duration,
                args.cooldown,
                args.sample_interval,
                args.trace_python,
            ),
            indent=2,
        )
    )
