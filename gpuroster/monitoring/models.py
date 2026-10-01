"""Normalized measurements: UTC epoch seconds, MiB, degrees C, W, and percent."""

from dataclasses import asdict, dataclass
from collections import defaultdict
from datetime import datetime, timezone


def utc_iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


@dataclass(frozen=True)
class GPU:
    index: int
    uuid: str
    name: str
    utilization: float | None
    memory_used: float | None
    memory_total: float | None
    temperature: float | None
    power: float | None

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class GPUProcess:
    gpu_uuid: str
    gpu: int | None
    pid: int
    user: str
    command: str
    mem_mb: float | None  # MiB; retained API name for compatibility

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class System:
    cpu_percent: float | None
    ram_used_bytes: int
    ram_total_bytes: int
    ram_percent: float

    def to_dict(self):
        return {
            **asdict(self),
            "ram_used_gb": round(self.ram_used_bytes / 1e9, 1),
            "ram_total_gb": round(self.ram_total_bytes / 1e9, 1),
        }


@dataclass(frozen=True)
class MetricSnapshot:
    sequence: int
    collected_at: float
    gpus: tuple[GPU, ...]
    processes: tuple[GPUProcess, ...]
    system: System | None

    def to_dict(self):
        users = defaultdict(list)
        processes = [process.to_dict() for process in self.processes]
        for process in processes:
            users[process["user"]].append(process)
        return {
            "schema_version": 2,
            "sequence": self.sequence,
            "timestamp": utc_iso(self.collected_at),
            "gpus": [
                {
                    **gpu.to_dict(),
                    "processes": [
                        process
                        for process in processes
                        if process["gpu_uuid"] == gpu.uuid
                    ],
                }
                for gpu in self.gpus
            ],
            "processes": processes,
            "system": self.system.to_dict() if self.system else None,
            "user_gpu": {
                user: {
                    "gpu_indices": sorted(
                        {p["gpu"] for p in rows if p["gpu"] is not None}
                    ),
                    "gpu_uuids": sorted({p["gpu_uuid"] for p in rows}),
                    "mem_gb": round(sum(p["mem_mb"] for p in rows) / 1024, 1)
                    if all(p["mem_mb"] is not None for p in rows)
                    else None,
                    "proc_count": len(rows),
                }
                for user, rows in users.items()
            },
        }


@dataclass(frozen=True)
class Session:
    user: str
    terminal: str
    host: str
    started_at: float
    ended_at: float | None
    source: str = "wtmp"

    def to_dict(self, now):
        end = min(now, self.ended_at) if self.ended_at is not None else now
        return {
            "user": self.user,
            "terminal": self.terminal,
            "host": self.host,
            "login": utc_iso(self.started_at),
            "logout": utc_iso(end) if self.ended_at is not None else "active",
            "duration_min": round(max(0, end - self.started_at) / 60, 1),
            "still_in": self.ended_at is None,
            "source": self.source,
        }
