"""Bounded NVIDIA CLI reads and system measurements owned by one collector."""

import csv
import math
import os
import subprocess
import time

import psutil

from monitoring.models import GPU, GPUProcess, System


class CollectionError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class CommandRunner:
    def __init__(self, timeout=3):
        self.timeout = timeout

    def __call__(self, command):
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"GPUROSTER_AUTH_USER", "GPUROSTER_AUTH_PASSWORD"}
        }
        environment.update(LC_ALL="C", TZ="UTC")
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True,
                timeout=self.timeout,
                env=environment,
            )
        except subprocess.TimeoutExpired:
            raise CollectionError("command_timeout") from None
        except FileNotFoundError:
            raise CollectionError("command_missing") from None
        except (subprocess.CalledProcessError, OSError, UnicodeError):
            raise CollectionError("command_failed") from None
        return result.stdout.strip()


def metric(value, maximum=None):
    if value.strip() in {"N/A", "[N/A]", "[Not Supported]", "Not Supported"}:
        return None
    try:
        number = float(value)
    except ValueError:
        raise CollectionError("invalid_output") from None
    if not math.isfinite(number) or number < 0:
        raise CollectionError("invalid_output")
    if maximum is not None and number > maximum:
        raise CollectionError("invalid_output")
    return number


class NvidiaSMI:
    name = "nvidia-smi"

    def __init__(self, runner, show_commands=False):
        self.run = runner
        self.show_commands = show_commands

    def gpus(self):
        output = self.run(
            [
                "nvidia-smi",
                "--query-gpu=index,uuid,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                "--format=csv,noheader,nounits",
            ]
        )
        devices = []
        identifiers = set()
        try:
            for row in csv.reader(output.splitlines(), skipinitialspace=True):
                if not row:
                    continue
                if len(row) != 8 or not row[1] or row[1] in identifiers:
                    raise CollectionError("invalid_output")
                index = int(row[0])
                if index < 0:
                    raise CollectionError("invalid_output")
                devices.append(
                    GPU(
                        index,
                        row[1],
                        row[2],
                        metric(row[3], 100),
                        metric(row[4]),
                        metric(row[5]),
                        metric(row[6]),
                        metric(row[7]),
                    )
                )
                identifiers.add(row[1])
        except (ValueError, csv.Error):
            raise CollectionError("invalid_output") from None
        return tuple(devices)

    def processes(self, devices):
        # Identity comes from the same GPU sample: no second device enumeration.
        indices = {device.uuid: device.index for device in devices}
        output = self.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,gpu_uuid,used_memory",
                "--format=csv,noheader,nounits",
            ]
        )
        processes = []
        try:
            for row in csv.reader(output.splitlines(), skipinitialspace=True):
                if not row:
                    continue
                if len(row) != 3 or not row[1]:
                    raise CollectionError("invalid_output")
                pid = int(row[0])
                if pid < 1:
                    raise CollectionError("invalid_output")
                memory = metric(row[2])
                username, command = process_description(pid, self.show_commands)
                processes.append(
                    GPUProcess(
                        row[1], indices.get(row[1]), pid, username, command, memory
                    )
                )
        except (ValueError, csv.Error):
            raise CollectionError("invalid_output") from None
        return tuple(processes)

    def close(self):
        pass


def process_description(pid, show_commands):
    try:
        process = psutil.Process(pid)
        username = process.username()
        command = " ".join(process.cmdline()[:6]) if show_commands else process.name()
        return username, command[:120] + ("…" if len(command) > 120 else "")
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return "unknown", "unknown"


class SystemCollector:
    """Sample CPU on one owning thread; never publish its initial zero baseline."""

    def __init__(self):
        self.last_sample = None

    def reset(self):
        self.last_sample = None

    def collect(self):
        now = time.monotonic()
        measured = psutil.cpu_percent(interval=None)
        ready = self.last_sample is not None and now - self.last_sample >= 0.1
        self.last_sample = now
        memory = psutil.virtual_memory()
        return System(
            measured if ready else None, memory.used, memory.total, memory.percent
        )
