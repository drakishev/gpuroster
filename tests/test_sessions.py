import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from gpuroster.monitoring.models import Session
from gpuroster.monitoring.sessions import (
    SessionCollector,
    parse_records,
    summarize,
    union_seconds,
)


def epoch(value):
    return datetime.fromisoformat(value).timestamp()


class SessionTests(unittest.TestCase):
    def test_interval_union_clips_both_ends_and_does_not_double_count(self):
        self.assertEqual(
            union_seconds([(-10, 20), (10, 30), (30, 35), (40, 80)], 0, 50), 45
        )
        self.assertEqual(union_seconds([(1, 1), (20, 10), (60, 70)], 0, 50), 0)

    def test_crossing_month_boundary_and_overlapping_sources(self):
        now = epoch("2026-10-01T12:00:00+00:00")
        rows = [
            Session("example-user", "pts/0", "example.invalid", now - 40 * 86400, None)
        ]
        connections = [Session("example-user", "notty", "SSH", now - 3600, None)]
        data = summarize(rows, connections, now)["example-user"]
        self.assertEqual(data["today_h"], 12)
        self.assertEqual(data["week_h"], 168)
        self.assertEqual(data["month_h"], 720)
        self.assertTrue(data["has_noninteractive_ssh"])

    def test_disjoint_and_duplicate_sessions_within_one_day(self):
        now = epoch("2026-10-01T12:00:00+00:00")
        row = Session("example-user", "pts/0", "", now - 7200, now - 3600)
        data = summarize(
            [row, row, Session("example-user", "pts/1", "", now - 1800, None)], [], now
        )
        self.assertEqual(data["example-user"]["today_h"], 1.5)
        self.assertEqual(data["example-user"]["week_h"], 1.5)

    def test_today_uses_configured_timezone_and_dst_elapsed_time(self):
        for now, hours in [
            ("2026-03-08T12:00:00-04:00", 11),
            ("2026-11-01T12:00:00-05:00", 13),
        ]:
            timestamp = epoch(now)
            rows = [Session("example-user", "pts/0", "", timestamp - 2 * 86400, None)]
            result = summarize(rows, [], timestamp, "America/New_York")
            self.assertEqual(result["example-user"]["today_h"], hours)

    def test_iso_parser_preserves_full_usernames_and_optional_host(self):
        result = parse_records(
            "example-long-user pts/0 example.invalid 2026-10-01T10:00:00+05:00 - 2026-10-01T11:00:00+05:00 (01:00)\n"
            "example-user tty1 2026-10-01T06:00:00+00:00 still logged in\n"
            "wtmp begins 2026-09-01T00:00:00+00:00\n"
        )
        self.assertEqual(result.unparsed, 0)
        self.assertEqual(result.sessions[0].user, "example-long-user")
        self.assertEqual(
            result.sessions[0].started_at, epoch("2026-10-01T05:00:00+00:00")
        )
        self.assertEqual(result.sessions[1].host, "")
        self.assertIsNone(result.sessions[1].ended_at)
        self.assertIsNotNone(result.coverage_start)

    def test_invalid_and_crashed_sessions_are_not_invented_as_active(self):
        result = parse_records(
            "example-user pts/0 host 2026-10-01T10:00:00+00:00 - crash (00:00)\n"
            "example-user pts/0 host 2026-10-01T10:00:00+00:00 - 2026-10-01T09:00:00+00:00\n"
            "malformed output\n"
        )
        self.assertEqual(result.unparsed, 3)
        self.assertFalse(result.sessions)

    def test_truncated_records_are_flagged(self):
        line = "example-user pts/0 host 2026-10-01T10:00:00+00:00 still logged in\n"
        result = parse_records(line * 2001)
        self.assertTrue(result.truncated)
        self.assertEqual(len(result.sessions), 2000)

    def test_future_sessions_do_not_add_duration(self):
        result = summarize([Session("example-user", "pts/0", "", 200, None)], [], 100)
        self.assertEqual(result, {})

    def test_connection_verification_rejects_title_and_owner_spoofing(self):
        processes = []
        for title, owner, path in [
            ("sshd: claimed-user@pts/0", "example-user", "/usr/sbin/sshd"),
            ("sshd: example-user@notty", "example-user", "/usr/bin/python3"),
            ("sshd: example-user@notty", "example-user", "/usr/sbin/sshd"),
        ]:
            process = Mock(info={"name": "sshd", "username": owner, "create_time": 100})
            process.cmdline.return_value = [title]
            process.exe.return_value = path
            processes.append(process)
        with (
            patch(
                "gpuroster.monitoring.sessions.psutil.process_iter",
                return_value=processes,
            ),
            patch(
                "gpuroster.monitoring.sessions.os.stat",
                return_value=Mock(st_uid=0, st_mode=0o100755),
            ),
        ):
            records = SessionCollector(Mock()).connections()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].terminal, "notty")

    def test_writable_or_user_owned_binary_is_rejected(self):
        process = Mock(
            info={"name": "sshd", "username": "example-user", "create_time": 100}
        )
        process.cmdline.return_value = ["sshd: example-user@pts/0"]
        process.exe.return_value = "/usr/sbin/sshd"
        for uid, mode in [(1000, 0o100755), (0, 0o100777)]:
            with (
                patch(
                    "gpuroster.monitoring.sessions.psutil.process_iter",
                    return_value=[process],
                ),
                patch(
                    "gpuroster.monitoring.sessions.os.stat",
                    return_value=Mock(st_uid=uid, st_mode=mode),
                ),
            ):
                self.assertEqual(SessionCollector(Mock()).connections(), ())


if __name__ == "__main__":
    unittest.main()
