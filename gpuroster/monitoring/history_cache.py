"""Bounded, per-range query sharing for one application's history provider."""

import copy
import sqlite3
import threading
import time
from dataclasses import dataclass

from gpuroster.monitoring.history import history_window
from gpuroster.monitoring.models import utc_iso


@dataclass(frozen=True)
class _Entry:
    data: dict | None
    as_of: float
    monotonic: float
    write_version: float | None


class HistoryQueryCache:
    """Coalesce misses without holding a collector or SQLite connection lock.

    Providers publish last_write_monotonic only after a successful write. Capture
    it before querying, so a write racing the query invalidates the next read.
    The three locks/entries are fixed; HTTP input cannot grow the cache.
    """

    max_age = 60
    retry_delay = 1

    def __init__(self, store, timezone_name, *, wall_clock=None, monotonic=None):
        self.store = store
        self.timezone = timezone_name
        self._wall_clock = wall_clock or time.time
        self._monotonic = monotonic or time.monotonic
        self._locks = {key: threading.Lock() for key in ("today", "week", "month")}
        self._entries = {}

    def _valid(self, entry, range_key, now, monotonic, version):
        age = monotonic - entry.monotonic
        lifetime = self.retry_delay if entry.data is None else self.max_age
        return (
            entry.write_version == version
            and 0 <= age < lifetime
            # Detect a clock step in either direction; TTL itself is monotonic.
            and abs((now - entry.as_of) - age) <= 1
            and (
                range_key != "today"
                or history_window("today", now, self.timezone)[0]
                == history_window("today", entry.as_of, self.timezone)[0]
            )
        )

    def read(self, range_key):
        if range_key not in self._locks:
            raise ValueError("invalid_range")
        with self._locks[range_key]:
            # Take clocks/version after waiting for any concurrent miss.
            now, monotonic = self._wall_clock(), self._monotonic()
            version = self.store.last_write_monotonic
            entry = self._entries.get(range_key)
            if entry is None or not self._valid(
                entry, range_key, now, monotonic, version
            ):
                self._entries.pop(range_key, None)
                try:
                    data = self.store.read(range_key, now, self.timezone)
                except sqlite3.Error:
                    # Share a safe failure briefly instead of serializing a
                    # queue of repeated failing SQL calls. Never serve old data
                    # as a successful refresh or retain raw exception details.
                    entry = _Entry(None, self._wall_clock(), self._monotonic(), version)
                else:
                    entry = _Entry(data, now, monotonic, version)
                self._entries[range_key] = entry
            if entry.data is None:
                raise sqlite3.OperationalError("history_unavailable")
            result = copy.deepcopy(entry.data)
            result["query"] = {
                "as_of": utc_iso(entry.as_of),
                "age_seconds": round(max(0, self._monotonic() - entry.monotonic), 3),
                "max_age_seconds": self.max_age,
            }
            return result
