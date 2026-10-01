import base64
import json
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from gpuroster.healthcheck import check, fresh_snapshot


class HealthcheckTests(unittest.TestCase):
    def setUp(self):
        self.now = 1790860000
        self.snapshot = {
            "schema_version": 2,
            "sequence": 1,
            "timestamp": datetime.fromtimestamp(self.now, timezone.utc).isoformat(),
            "health": {
                "status": "degraded",
                "sources": {"gpus": {"status": "unavailable"}},
            },
        }

    def test_fresh_delivery_is_healthy_even_when_gpu_is_unavailable(self):
        self.assertTrue(fresh_snapshot(self.snapshot, 3, self.now + 1))

    def test_stalled_initial_future_or_naive_snapshots_are_unhealthy(self):
        self.assertFalse(fresh_snapshot(self.snapshot, 3, self.now + 16))
        self.assertTrue(fresh_snapshot(self.snapshot, 60, self.now + 100))
        self.assertFalse(fresh_snapshot(self.snapshot, 3, self.now - 6))
        self.assertFalse(fresh_snapshot({**self.snapshot, "sequence": 0}, 3, self.now))
        self.assertFalse(
            fresh_snapshot(
                {**self.snapshot, "timestamp": "2026-10-01T00:00:00"}, 3, self.now
            )
        )

    def test_probe_authenticates_locally_without_using_environment_proxy(self):
        response = Mock(status=200)
        response.read.return_value = json.dumps(self.snapshot).encode()
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        settings = {
            "BIND_PORT": 12345,
            "AUTH_USER": "viewer",
            "AUTH_PASSWORD": "synthetic",
            "COLLECT_INTERVAL": 3,
        }
        with (
            patch("gpuroster.healthcheck.load_settings", return_value=settings),
            patch(
                "gpuroster.healthcheck.urllib.request.build_opener", return_value=opener
            ),
            patch("gpuroster.healthcheck.urllib.request.ProxyHandler") as proxy,
            patch("gpuroster.healthcheck.time.time", return_value=self.now),
        ):
            self.assertTrue(check())
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:12345/api/stats")
        self.assertEqual(
            request.get_header("Authorization"),
            "Basic " + base64.b64encode(b"viewer:synthetic").decode(),
        )
        proxy.assert_called_once_with({})
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 3)

    def test_probe_returns_false_without_printing_configuration_errors(self):
        with (
            patch(
                "gpuroster.healthcheck.load_settings",
                side_effect=ValueError("PRIVATE_CREDENTIAL"),
            ),
            patch("builtins.print") as output,
        ):
            self.assertFalse(check())
        output.assert_not_called()

    def test_probe_rejects_malformed_response_without_printing_data(self):
        response = Mock(status=200)
        response.read.return_value = b"PRIVATE_NOT_JSON"
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with (
            patch(
                "gpuroster.healthcheck.load_settings",
                return_value={
                    "BIND_PORT": 12345,
                    "AUTH_USER": "",
                    "COLLECT_INTERVAL": 3,
                },
            ),
            patch(
                "gpuroster.healthcheck.urllib.request.build_opener", return_value=opener
            ),
            patch("builtins.print") as output,
        ):
            self.assertFalse(check())
        output.assert_not_called()


if __name__ == "__main__":
    unittest.main()
