"""Synthetic providers: no device, process, login-record, or database access."""

import math
import time

from gpuroster.monitoring.history import history_window
from gpuroster.monitoring.models import GPU, GPUProcess, Session, System, utc_iso
from gpuroster.monitoring.sessions import SessionRecords


def utilization(index, timestamp):
    if index == 3:
        return 0.0
    return round(48 + 32 * math.sin(timestamp / 45 + index * 1.7), 1)


def devices_at(timestamp):
    devices = []
    for index in range(4):
        util = utilization(index, timestamp)
        total = 24576 if index < 2 else 49152
        used = round(total * (0.2 + util / 160)) if index != 3 else 0
        devices.append(
            GPU(
                index,
                f"GPU-DEMO-{index}",
                f"Demo GPU {total // 1024} GiB",
                util,
                used,
                total,
                round(32 + util * 0.4, 1),
                None if index == 2 else round(25 + util * 2.4, 1),
            )
        )
    return tuple(devices)


class DemoGPU:
    name = "demo"

    def gpus(self):
        return devices_at(time.time())

    def processes(self, devices):
        return tuple(
            GPUProcess(
                device.uuid,
                device.index,
                1000 + device.index,
                ("demo-alex", "demo-sam", "demo-alex")[device.index],
                "demo-trainer",
                device.memory_used,
            )
            for device in devices
            if device.index < 3
        )

    def close(self):
        pass


class DemoSystem:
    def collect(self):
        phase = math.sin(time.time() / 70)
        total = 64_000_000_000
        used = int(total * (0.48 + phase * 0.08))
        return System(
            round(35 + 15 * phase, 1), used, total, round(used / total * 100, 1)
        )


class DemoSessions:
    def __init__(self):
        self.started = time.time()

    def records(self):
        rows = [
            Session(
                "demo-alex", "pts/0", "demo.invalid", self.started - 5400, None, "demo"
            )
        ]
        for day in range(30):
            start = self.started - (day + 1) * 86400
            for index, user in enumerate(("demo-alex", "demo-sam", "demo-morgan")):
                rows.append(
                    Session(
                        user,
                        f"pts/{index}",
                        "demo.invalid",
                        start,
                        start + (index + 1) * 3600,
                        "demo",
                    )
                )
        return SessionRecords(tuple(rows), coverage_start=self.started - 30 * 86400)

    def connections(self):
        return (
            Session(
                "demo-alex", "pts/0", "demo.invalid", self.started - 5400, None, "demo"
            ),
            Session(
                "demo-sam", "notty", "demo.invalid", self.started - 2700, None, "demo"
            ),
        )


class DemoHistory:
    """Generate bounded, illustrative bucket values; never open a SQLite file."""

    last_write = None

    def write(self, rows, now, monotonic):
        self.last_write = now

    def read(self, range_key, now, timezone_name="UTC"):
        since, bucket = history_window(range_key, now, timezone_name)
        timestamps = list(
            range(
                int(since) // bucket * bucket, int(now) // bucket * bucket + 1, bucket
            )
        )
        return {
            "labels": [utc_iso(timestamp) for timestamp in timestamps],
            "datasets": [
                {
                    "id": f"GPU-DEMO-{index}",
                    "gpu": index,
                    "label": f"GPU {index} · demo",
                    "data": [utilization(index, timestamp) for timestamp in timestamps],
                }
                for index in range(4)
            ],
            "points": len(timestamps),
            "range": range_key,
            "timezone": timezone_name,
        }


def create_demo_collector(config):
    from gpuroster.monitoring.service import CollectorService

    service = CollectorService(
        config,
        None,
        gpu=DemoGPU(),
        system=DemoSystem(),
        sessions=DemoSessions(),
        store=DemoHistory(),
    )
    now = time.time()
    for point in range(service.history.length - 1, 0, -1):
        timestamp = now - point * service.interval
        service.history.append(devices_at(timestamp), timestamp)
    return service
