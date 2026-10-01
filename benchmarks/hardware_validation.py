"""Opt-in, read-only GPU checks; stdout contains aggregates, never identities.

Run from a checkout: python -m benchmarks.hardware_validation --duration 900
No HTTP listener, database, session inspection, GPU workload, or device mutation.
"""

import argparse
import csv
import json
import math
import platform
import re
import sys
import time
from collections import Counter
from importlib.metadata import version

import psutil

from gpuroster.monitoring.collectors import CollectionError, CommandRunner, NvidiaSMI
from gpuroster.monitoring.nvml import NVMLWorker

METRICS = ("utilization", "memory_used", "memory_total", "temperature", "power")
LATENCY_BOUNDS_MS = (1, 2, 5, 10, 25, 50, 100, 250, 500, 1000, 3000, 10000)
ERROR_CODES = {
    "command_timeout",
    "command_missing",
    "command_failed",
    "invalid_output",
    "nvml_unavailable",
    "gpu_collection_failed",
    "gpu_worker_failed",
    "process_metrics_unavailable",
    "gpu_sample_unavailable",
    "worker_cleanup_failed",
    "worker_exit_not_detected",
    "invalid_sample",
}


def inventory(runner):
    output = runner(
        [
            "nvidia-smi",
            "--query-gpu=driver_version,mig.mode.current,mig.mode.pending",
            "--format=csv,noheader,nounits",
        ]
    )
    versions, current, pending = set(), Counter(), Counter()
    rows = list(csv.reader(output.splitlines(), skipinitialspace=True))
    for row in rows:
        if len(row) != 3 or not re.fullmatch(r"\d+(?:\.\d+){1,3}", row[0]):
            raise CollectionError("invalid_output")
        versions.add(row[0])
        for counts, value in ((current, row[1]), (pending, row[2])):
            counts[
                value.lower() if value in {"Enabled", "Disabled"} else "unavailable"
            ] += 1
    return {
        "device_count": len(rows),
        "driver_versions": sorted(versions),
        "mig_current": dict(current),
        "mig_pending": dict(pending),
    }


def summarize_sample(devices, processes):
    """Validate in memory; retain no UUIDs, names, PIDs, commands, or usernames."""
    identities = {device.uuid: device.index for device in devices}
    valid = bool(devices) and len(identities) == len(devices)
    valid &= len({device.index for device in devices}) == len(devices)
    missing = Counter({field: 0 for field in METRICS})
    for device in devices:
        valid &= bool(device.uuid) and device.index >= 0
        for field in METRICS:
            value = getattr(device, field)
            if value is None:
                missing[field] += 1
            else:
                valid &= math.isfinite(value) and value >= 0
        if device.utilization is not None:
            valid &= device.utilization <= 100
        if device.memory_used is not None and device.memory_total is not None:
            valid &= device.memory_used <= device.memory_total
    for process in processes:
        valid &= process.pid > 0 and process.gpu_uuid in identities
        valid &= process.gpu == identities.get(process.gpu_uuid)
        if process.mem_mb is not None:
            valid &= math.isfinite(process.mem_mb) and process.mem_mb >= 0
    return {
        "valid": bool(valid),
        "gpus": len(devices),
        "processes": len(processes),
        "unknown_process_users": sum(p.user == "unknown" for p in processes),
        "null_metrics": dict(missing),
    }


def compare_static(left, right):
    """Dynamic readings are sequential and must not be treated as simultaneous."""
    left = {device.uuid: device for device in left}
    right = {device.uuid: device for device in right}
    identities_match = bool(left) and left.keys() == right.keys()
    comparable = [
        uuid
        for uuid in left.keys() & right.keys()
        if left[uuid].memory_total is not None and right[uuid].memory_total is not None
    ]
    return {
        "identities_match": identities_match,
        "memory_totals_compared": len(comparable),
        # CLI rounds to whole MiB; allow that rounding, not arbitrary drift.
        "memory_totals_match": all(
            abs(left[uuid].memory_total - right[uuid].memory_total) <= 1
            for uuid in comparable
        ),
    }


def resources(process):
    memory, cpu = process.memory_full_info(), process.cpu_times()
    return {
        "rss_bytes": memory.rss,
        "uss_bytes": memory.uss,
        "threads": process.num_threads(),
        "file_descriptors": process.num_fds(),
        "cpu_seconds": round(cpu.user + cpu.system, 3),
    }


def memory_window(samples):
    # The second half excludes initial driver/allocator warm-up.
    tail = [row for row in samples if row["seconds"] >= samples[-1]["seconds"] / 2]
    result = {}
    elapsed = tail[-1]["seconds"] - tail[0]["seconds"]
    for name in ("parent", "worker"):
        values = [row[name]["uss_bytes"] for row in tail]
        band = (max(values) - min(values)) / 1048576
        slope = (values[-1] - values[0]) / 1048576 / (elapsed / 60) if elapsed else None
        result[name] = {
            "uss_band_mib": round(band, 3),
            "uss_endpoint_growth_mib_per_minute": round(slope, 3)
            if slope is not None
            else None,
            "within_memory_threshold": slope is not None and band < 10 and slope < 1,
        }
    return result


def recovery_check(provider, cycles):
    """Terminate only children created by this benchmark, never GPU workloads."""
    recovered = 0
    for _ in range(cycles):
        provider.gpus()
        child = provider.worker
        child.terminate()
        child.join(timeout=2)
        if child.is_alive():
            raise CollectionError("worker_cleanup_failed")
        try:
            provider.gpus()
        except CollectionError as error:
            if error.code != "gpu_worker_failed":
                raise
        else:
            raise CollectionError("worker_exit_not_detected")
        devices = provider.gpus()
        if not summarize_sample(devices, provider.processes(devices))["valid"]:
            raise CollectionError("invalid_sample")
        provider.close()
        recovered += 1
    return recovered


def run(duration, interval, sample_interval, recovery_cycles):
    runner = CommandRunner(timeout=3)
    report = {
        "schema_version": 1,
        "python": platform.python_version(),
        "nvml_binding": version("nvidia-ml-py"),
        "duration_seconds": duration,
        "interval_seconds": interval,
        "resource_interval_seconds": sample_interval,
        "inventory": inventory(runner),
    }
    provider, parent = NVMLWorker(timeout=3), psutil.Process()
    try:
        started = time.monotonic()
        devices = provider.gpus()
        report["cold_start_ms"] = round((time.monotonic() - started) * 1000, 3)
        initial_ids = {device.uuid for device in devices}
        report["static_comparison"] = compare_static(devices, NvidiaSMI(runner).gpus())
        report["initial_sample"] = summarize_sample(
            devices, provider.processes(devices)
        )
        worker = psutil.Process(provider.worker.pid)
        started = time.monotonic()
        samples = [
            {"seconds": 0, "parent": resources(parent), "worker": resources(worker)}
        ]
        histogram = [0] * (len(LATENCY_BOUNDS_MS) + 1)
        cycles = invalid = changed = missed = 0
        maximum = 0
        process_maximum = unknown_maximum = 0
        missing = Counter()
        deadline, next_resource = started, started + sample_interval
        while time.monotonic() < started + duration:
            before = time.monotonic()
            devices = provider.gpus()
            summary = summarize_sample(devices, provider.processes(devices))
            latency = (time.monotonic() - before) * 1000
            bucket = next(
                (i for i, bound in enumerate(LATENCY_BOUNDS_MS) if latency <= bound),
                len(LATENCY_BOUNDS_MS),
            )
            histogram[bucket] += 1
            maximum = max(maximum, latency)
            cycles += 1
            invalid += not summary["valid"]
            changed += {device.uuid for device in devices} != initial_ids
            process_maximum = max(process_maximum, summary["processes"])
            unknown_maximum = max(unknown_maximum, summary["unknown_process_users"])
            missing.update(summary["null_metrics"])
            now = time.monotonic()
            if now >= next_resource:
                row = {
                    "seconds": round(now - started, 3),
                    "parent": resources(parent),
                    "worker": resources(worker),
                }
                samples.append(row)
                print(json.dumps(row), file=sys.stderr, flush=True)
                next_resource += sample_interval
            deadline += interval
            if deadline <= now:
                missed += 1
                deadline = now + interval
            time.sleep(max(0, min(deadline, started + duration) - time.monotonic()))
        samples.append(
            {
                "seconds": round(time.monotonic() - started, 3),
                "parent": resources(parent),
                "worker": resources(worker),
            }
        )
        report.update(
            cycles=cycles,
            invalid_samples=invalid,
            identity_changes=changed,
            missed_deadlines=missed,
            max_processes=process_maximum,
            max_unknown_process_users=unknown_maximum,
            null_metric_observations=dict(missing),
            latency={
                "bounds_ms": list(LATENCY_BOUNDS_MS),
                "counts": histogram,
                "max_ms": round(maximum, 3),
            },
            resources=samples,
            memory_window=memory_window(samples),
        )
        child = provider.worker
        provider.close()
        report["worker_stopped"] = not child.is_alive()
        child.close()
        report["parent_before_recovery"] = resources(parent)
        report["recovery_cycles_passed"] = recovery_check(provider, recovery_cycles)
        report["parent_after_recovery"] = resources(parent)
        comparison = report["static_comparison"]
        report["checks_passed"] = bool(
            report["initial_sample"]["valid"]
            and report["inventory"]["device_count"] == len(initial_ids)
            and not invalid
            and not changed
            and comparison["identities_match"]
            and comparison["memory_totals_match"]
            and report["worker_stopped"]
        )
        return report
    finally:
        provider.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=900)
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument("--sample-interval", type=float, default=30)
    parser.add_argument("--recovery-cycles", type=int, default=10)
    args = parser.parse_args()
    if not (
        1 <= args.duration <= 86400
        and 0.1 <= args.interval <= args.duration
        and 0.1 <= args.sample_interval <= args.duration
        and 0 <= args.recovery_cycles <= 100
    ):
        parser.error("invalid duration, interval, sample interval, or recovery count")
    try:
        report = run(
            args.duration, args.interval, args.sample_interval, args.recovery_cycles
        )
    except Exception as error:
        # Driver and OS exceptions can contain real host/process identifiers.
        code = (
            error.code
            if isinstance(error, CollectionError) and error.code in ERROR_CODES
            else "hardware_validation_failed"
        )
        print(json.dumps({"checks_passed": False, "error": code}))
        raise SystemExit(1) from None
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["checks_passed"] else 1)


if __name__ == "__main__":
    main()
