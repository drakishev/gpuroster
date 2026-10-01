"""One scheduled collector publishes snapshots; clients never invoke sources."""

import copy
import logging
import threading
import time

from monitoring.collectors import (
    CollectionError,
    CommandRunner,
    NvidiaSMI,
    SystemCollector,
)
from monitoring.history import HistoryStore, RollingHistory
from monitoring.models import MetricSnapshot, utc_iso
from monitoring.nvml import AutoGPU, NVMLWorker
from monitoring.sessions import (
    SessionCollector,
    SessionRecords,
    connection_rows,
    session_rows,
    summarize,
)

LOG = logging.getLogger(__name__)


class CollectorService:
    def __init__(
        self, config, db_path, gpu=None, system=None, sessions=None, store=None
    ):
        self.config = dict(config)
        runner = CommandRunner(config["COMMAND_TIMEOUT"])
        providers = {
            "auto": lambda: AutoGPU(runner, config["SHOW_COMMANDS"]),
            "nvml": lambda: NVMLWorker(runner.timeout, config["SHOW_COMMANDS"]),
            "smi": lambda: NvidiaSMI(runner, config["SHOW_COMMANDS"]),
        }
        self.gpu = gpu if gpu is not None else providers[config["GPU_BACKEND"]]()
        self.system = system if system is not None else SystemCollector()
        self.sessions = sessions if sessions is not None else SessionCollector(runner)
        self.store = store if store is not None else HistoryStore(db_path)
        self.history = RollingHistory(config["COLLECT_INTERVAL"])
        self.interval = config["COLLECT_INTERVAL"]
        self.session_interval = config["SESSION_INTERVAL"]
        self._cache_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._cycle_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._sequence = 0
        self._next_sessions = 0
        self._records = SessionRecords(())
        self._connections = ()
        self._health = {
            name: {
                "status": "not_started",
                "sampled_at": None,
                "last_success_at": None,
                "_monotonic": None,
            }
            for name in (
                "gpus",
                "processes",
                "system",
                "connections",
                "sessions",
                "history",
            )
        }
        if not config["SHOW_SESSIONS"]:
            for name in ("connections", "sessions"):
                self._health[name]["status"] = "disabled"
        self._logs = {}
        self._published = copy.deepcopy(self._payload((), (), None, time.time()))

    def _collect(self, name, action, fallback, now, monotonic):
        health = self._health[name]
        health["sampled_at"] = utc_iso(now)
        try:
            result = action()
        except Exception as error:
            code = (
                error.code
                if isinstance(error, CollectionError)
                else "collection_failed"
            )
            health.update(status="unavailable", error=code)
            previous = self._logs.get(name)
            if previous is None or previous[0] != code or monotonic - previous[1] >= 60:
                LOG.warning("Collection unavailable: source=%s code=%s", name, code)
                self._logs[name] = (code, monotonic)
            return fallback
        health.update(status="ok", last_success_at=utc_iso(now), _monotonic=monotonic)
        health.pop("error", None)
        return result

    def _payload(self, devices, processes, system, now):
        return {
            **MetricSnapshot(
                self._sequence, now, tuple(devices), tuple(processes), system
            ).to_dict(),
            "history": self.history.to_dict(),
            "history_devices": self.history.devices,
            "connections": connection_rows(self._connections, now),
            "login_stats": summarize(
                self._records.sessions, self._connections, now, self.config["TIMEZONE"]
            ),
            "sessions": session_rows(self._records.sessions, self._connections, now),
            "health": {"sources": self._health},
            "collector": {
                "backend": self.gpu.name,
                "interval_seconds": self.interval,
                "timezone": self.config["TIMEZONE"],
            },
        }

    def collect_once(self):
        # Also prevents overlapping cycles in embedding/test integrations.
        with self._cycle_lock:
            now, monotonic = time.time(), time.monotonic()
            devices = self._collect("gpus", self.gpu.gpus, (), now, monotonic)
            processes = self._collect(
                "processes", lambda: self.gpu.processes(devices), (), now, monotonic
            )
            system = self._collect("system", self.system.collect, None, now, monotonic)
            if system is not None and system.cpu_percent is None:
                self._health["system"]["status"] = "warming_up"
            if self.config["SHOW_SESSIONS"] and monotonic >= self._next_sessions:
                self._connections = self._collect(
                    "connections", self.sessions.connections, (), now, monotonic
                )
                self._records = self._collect(
                    "sessions",
                    self.sessions.records,
                    SessionRecords(()),
                    now,
                    monotonic,
                )
                if self._health["sessions"]["status"] == "ok":
                    if self._records.truncated or self._records.unparsed:
                        self._health["sessions"].update(
                            status="partial", error="incomplete_records"
                        )
                    self._health["sessions"].update(
                        truncated=self._records.truncated,
                        unparsed_records=self._records.unparsed,
                        coverage_start=utc_iso(self._records.coverage_start)
                        if self._records.coverage_start is not None
                        else None,
                    )
                self._next_sessions = monotonic + self.session_interval
            rows = self.history.append(devices, now)
            self._collect(
                "history",
                lambda: self.store.write(rows, now, monotonic),
                None,
                now,
                monotonic,
            )
            self._health["history"]["last_write"] = (
                utc_iso(self.store.last_write)
                if self.store.last_write is not None
                else None
            )
            self._sequence += 1
            payload = self._payload(devices, processes, system, now)
            with self._cache_lock:
                self._published = copy.deepcopy(payload)

    def snapshot(self):
        with self._cache_lock:
            data = copy.deepcopy(self._published)
        now = time.monotonic()
        for name, health in data["health"]["sources"].items():
            sampled = health.pop("_monotonic")
            health["age_seconds"] = (
                round(max(0, now - sampled), 3) if sampled is not None else None
            )
            interval = (
                self.session_interval
                if name in {"sessions", "connections"}
                else self.interval
            )
            if (
                health["status"] in {"ok", "partial", "warming_up"}
                and sampled is not None
                and now - sampled > max(15, interval * 3)
            ):
                health["status"] = "stale"
        data["health"]["status"] = (
            "ok"
            if all(
                item["status"] in {"ok", "disabled"}
                for item in data["health"]["sources"].values()
            )
            else "degraded"
        )
        return data

    def _run(self):
        if isinstance(self.system, SystemCollector):
            self.system.reset()
        deadline = time.monotonic()
        try:
            while not self._stop.is_set():
                try:
                    self.collect_once()
                except Exception:
                    # An unexpected orchestration error leaves the old snapshot
                    # to age visibly; never leak exception text containing host data.
                    LOG.error("Collector cycle failed")
                deadline += self.interval
                now = time.monotonic()
                if deadline <= now:
                    deadline = now + self.interval
                self._stop.wait(max(0, deadline - now))
        finally:
            self.gpu.close()

    def start(self):
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="metrics-collector", daemon=True
            )
            self._thread.start()

    def stop(self):
        with self._lifecycle_lock:
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=self.config["COMMAND_TIMEOUT"] * 3 + 3)
