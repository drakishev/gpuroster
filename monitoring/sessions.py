"""Session evidence and union-of-intervals accounting; no request-time reads."""

import os
import re
import stat
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psutil

from monitoring.models import Session, utc_iso

ISO_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:?\d{2}|Z)")
RECORD_LIMIT = 2000


@dataclass(frozen=True)
class SessionRecords:
    sessions: tuple[Session, ...]
    truncated: bool = False
    unparsed: int = 0
    coverage_start: float | None = None


def parse_records(output):
    records = []
    unparsed = 0
    seen = 0
    coverage_start = None
    for line in output.splitlines():
        if not line.strip():
            continue
        stamps = list(ISO_TIME.finditer(line))
        if line.startswith("wtmp begins"):
            if stamps:
                coverage_start = datetime.fromisoformat(
                    stamps[0].group().replace("Z", "+00:00")
                ).timestamp()
            continue
        seen += 1
        if seen > RECORD_LIMIT:
            continue
        if line.split()[0] in {"reboot", "shutdown", "runlevel"}:
            continue
        try:
            if not stamps:
                raise ValueError
            prefix = line[: stamps[0].start()].split()
            if len(prefix) < 2 or len(prefix) > 3:
                raise ValueError
            start = datetime.fromisoformat(
                stamps[0].group().replace("Z", "+00:00")
            ).timestamp()
            if "still logged in" in line or "still running" in line:
                end = None
            elif len(stamps) == 2:
                end = datetime.fromisoformat(
                    stamps[1].group().replace("Z", "+00:00")
                ).timestamp()
                if end < start:
                    raise ValueError
            else:
                # An unknown crash/down timestamp is not an active session.
                raise ValueError
            records.append(
                Session(
                    prefix[0],
                    prefix[1],
                    prefix[2] if len(prefix) == 3 else "",
                    start,
                    end,
                )
            )
        except ValueError:
            unparsed += 1
    return SessionRecords(tuple(records), seen > RECORD_LIMIT, unparsed, coverage_start)


class SessionCollector:
    def __init__(self, runner):
        self.run = runner

    def records(self):
        return parse_records(
            self.run(
                [
                    "last",
                    "--time-format",
                    "iso",
                    "--fullnames",
                    "-n",
                    str(RECORD_LIMIT + 1),
                ]
            )
        )

    def connections(self):
        records = []
        for process in psutil.process_iter(["name", "username", "create_time"]):
            try:
                if process.info["name"] not in {"sshd", "sshd-session"}:
                    continue
                title = " ".join(process.cmdline())
                match = re.fullmatch(
                    r"sshd(?:-session)?:\s+([^@\s]+)@(notty|pts/\d+|tty\d+)", title
                )
                if not match or match[1] != process.info["username"]:
                    continue
                executable = process.exe()
                if Path(executable).name not in {"sshd", "sshd-session"}:
                    continue
                metadata = os.stat(executable)
                if (
                    metadata.st_uid != 0
                    or metadata.st_mode & 0o022
                    or not stat.S_ISREG(metadata.st_mode)
                ):
                    continue
                started_at = process.info["create_time"]
                if started_at is None:
                    continue
                records.append(
                    Session(
                        match[1],
                        match[2],
                        "Non-interactive SSH" if match[2] == "notty" else "SSH",
                        started_at,
                        None,
                        "ssh_process",
                    )
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        return tuple(
            sorted(
                records, key=lambda item: (item.user, item.started_at, item.terminal)
            )
        )


def union_seconds(intervals, start, end):
    clipped = sorted(
        (max(a, start), min(b, end))
        for a, b in intervals
        if min(b, end) > max(a, start)
    )
    total = 0
    cursor = start
    for begin, finish in clipped:
        total += max(0, finish - max(begin, cursor))
        cursor = max(cursor, finish)
    return total


def summarize(records, connections, now, timezone_name="UTC"):
    """Union connected wall time, including visible active non-TTY SSH evidence."""
    today = (
        datetime.fromtimestamp(now, ZoneInfo(timezone_name))
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .timestamp()
    )
    windows = {"today": today, "week": now - 7 * 86400, "month": now - 30 * 86400}
    by_user = defaultdict(list)
    noninteractive = [item for item in connections if item.terminal == "notty"]
    for session in (*records, *noninteractive):
        end = min(session.ended_at, now) if session.ended_at is not None else now
        if session.started_at < end:
            by_user[session.user].append((session.started_at, end))
    noninteractive_users = {item.user for item in noninteractive}
    return {
        user: {
            **{
                key + "_h": round(union_seconds(intervals, start, now) / 3600, 2)
                for key, start in windows.items()
            },
            "last_seen": utc_iso(max(end for _, end in intervals)),
            "has_noninteractive_ssh": user in noninteractive_users,
        }
        for user, intervals in by_user.items()
    }


def connection_rows(records, now):
    return [
        {
            "user": item.user,
            "type": "SSH (no TTY)" if item.terminal == "notty" else "SSH",
            "tty": item.terminal,
            "since": utc_iso(item.started_at),
            "duration_min": round(max(0, now - item.started_at) / 60, 1),
        }
        for item in records
    ]


def session_rows(records, connections, now):
    combined = [
        *records,
        *(
            Session(
                item.user,
                item.terminal,
                item.host,
                item.started_at,
                None,
                "noninteractive_ssh",
            )
            for item in connections
            if item.terminal == "notty"
        ),
    ]
    return [
        item.to_dict(now)
        for item in sorted(combined, key=lambda item: item.started_at, reverse=True)[
            :300
        ]
    ]
