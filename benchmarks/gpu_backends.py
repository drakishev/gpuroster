"""Read-only comparison. Output contains aggregates, never device/process IDs.

Install nvidia-ml-py in a separate benchmark environment, then run this module.
Each case runs in a fresh process so peak RSS and child CPU are attributable.
"""

import argparse
import csv
import json
import math
import resource
import statistics
import subprocess
import sys
import time


def worker(backend, count, samples):
    handles = []
    if backend == "nvml":
        import pynvml as nvml

        started = time.perf_counter()
        nvml.nvmlInit()
        if nvml.nvmlDeviceGetCount() < count:
            raise RuntimeError("insufficient_devices")
        handles = [nvml.nvmlDeviceGetHandleByIndex(index) for index in range(count)]
        initialization_ms = (time.perf_counter() - started) * 1000
    else:
        initialization_ms = 0
    unavailable = 0

    def collect():
        nonlocal unavailable
        if backend == "smi":
            commands = [
                [
                    "nvidia-smi",
                    "--query-gpu=index,uuid,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                    "--format=csv,noheader,nounits",
                    "--id=" + ",".join(map(str, range(count))),
                ],
                [
                    "nvidia-smi",
                    "--query-compute-apps=pid,gpu_uuid,used_memory",
                    "--format=csv,noheader,nounits",
                    "--id=" + ",".join(map(str, range(count))),
                ],
            ]
            for command in commands:
                result = subprocess.run(
                    command, capture_output=True, text=True, check=True, timeout=3
                )
                rows = list(csv.reader(result.stdout.splitlines()))
                unavailable += sum(
                    "N/A" in item or "Not Supported" in item
                    for row in rows
                    for item in row
                )
            return
        for handle in handles:
            calls = [
                lambda: nvml.nvmlDeviceGetUUID(handle),
                lambda: nvml.nvmlDeviceGetName(handle),
                lambda: nvml.nvmlDeviceGetUtilizationRates(handle),
                lambda: nvml.nvmlDeviceGetMemoryInfo(handle),
                lambda: nvml.nvmlDeviceGetTemperature(
                    handle, nvml.NVML_TEMPERATURE_GPU
                ),
                lambda: nvml.nvmlDeviceGetPowerUsage(handle),
                lambda: nvml.nvmlDeviceGetComputeRunningProcesses(handle),
            ]
            for call in calls:
                try:
                    call()
                except nvml.NVMLError_NotSupported:
                    unavailable += 1

    collect()  # warm-up; initialization and import excluded from steady-state timing
    unavailable = 0
    parent_before = time.process_time()
    child_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    latency = []
    try:
        for _ in range(samples):
            started = time.perf_counter()
            collect()
            latency.append((time.perf_counter() - started) * 1000)
        parent_cpu = time.process_time() - parent_before
    finally:
        if handles:
            nvml.nvmlShutdown()
    child_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    child_cpu = (
        child_after.ru_utime
        + child_after.ru_stime
        - child_before.ru_utime
        - child_before.ru_stime
    )
    return {
        "backend": backend,
        "gpus": count,
        "samples": samples,
        "initialization_ms": round(initialization_ms, 3),
        "median_ms": round(statistics.median(latency), 3),
        "p95_ms": round(sorted(latency)[math.ceil(samples * 0.95) - 1], 3),
        "cpu_ms_per_sample_including_children": round(
            (parent_cpu + child_cpu) * 1000 / samples, 3
        ),
        "parent_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "child_peak_rss_kib": child_after.ru_maxrss,
        "subprocesses_per_sample": 2 if backend == "smi" else 0,
        "unsupported_metric_observations": unavailable,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=["smi", "nvml"])
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--samples", type=int, default=30)
    args = parser.parse_args()
    if args.samples < 2 or args.count < 1:
        parser.error("positive device count and at least two samples required")
    if args.worker:
        try:
            print(json.dumps(worker(args.worker, args.count, args.samples)))
        except Exception:
            # Drivers can put identifiers into exception text; never publish it.
            print(
                json.dumps(
                    {
                        "backend": args.worker,
                        "gpus": args.count,
                        "error": "benchmark_unavailable",
                    }
                )
            )
            raise SystemExit(1) from None
        return
    results = []
    for count in (1, 4, 8):
        for backend in ("smi", "nvml"):
            result = subprocess.run(
                [
                    sys.executable,
                    __file__,
                    "--worker",
                    backend,
                    "--count",
                    str(count),
                    "--samples",
                    str(args.samples),
                ],
                capture_output=True,
                text=True,
                timeout=240,
            )
            results.append(json.loads(result.stdout))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
