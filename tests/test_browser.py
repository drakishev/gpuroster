"""Real Chromium DOM/network regressions, with synthetic API and CDN fixtures."""

import copy
import threading
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

import app
from settings import load_settings


def snapshot():
    process = {
        "gpu": 0,
        "pid": 123,
        "user": "example-user",
        "command": "python",
        "mem_mb": 2048,
    }
    return {
        "gpus": [
            {
                "index": 0,
                "name": "Example GPU",
                "utilization": 42,
                "memory_used": 2048,
                "memory_total": 8192,
                "temperature": 45,
                "power": 100,
                "processes": [process],
            }
        ],
        "processes": [process],
        "user_gpu": {
            "example-user": {"gpu_indices": [0], "mem_gb": 2, "proc_count": 1}
        },
        "system": {"cpu_percent": 12, "ram_used_gb": 4, "ram_total_gb": 32},
        "connections": [
            {
                "user": "example-user",
                "type": "SSH",
                "tty": "pts/0",
                "since": "Example time",
                "duration_min": 10,
            }
        ],
        "history": {
            "0": [{"ts": "23:59:59", "util": 10}, {"ts": "00:00:02", "util": 42}]
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "health": {
            "status": "ok",
            "sources": {
                name: {"status": "ok"}
                for name in ("gpus", "processes", "system", "connections", "history")
            },
        },
    }


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.configuration = patch.dict(
            app.app.config, {**load_settings({}), "SHOW_SESSIONS": True}
        )
        cls.configuration.start()
        cls.collectors = []
        for name in (
            "get_gpu_stats",
            "get_gpu_processes",
            "get_system_stats",
            "get_login_stats",
            "get_user_sessions",
            "get_active_connections",
        ):
            guard = patch.object(
                app,
                name,
                side_effect=AssertionError("Browser tests must use synthetic fixtures"),
            )
            guard.start()
            cls.collectors.append(guard)
        cls.server = make_server("127.0.0.1", 0, app.app, threaded=True)
        cls.server_thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True
        )
        cls.server_thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server_thread.join(timeout=5)
        cls.server.server_close()
        for guard in cls.collectors:
            guard.stop()
        cls.configuration.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.stats = snapshot()
        self.status = 200
        self.requests = []
        self.sessions = [
            {
                "user": "example-user",
                "login": "Example time",
                "logout": "active",
                "duration_min": 10,
                "terminal": "pts/0",
                "host": "example.invalid",
                "still_in": True,
                "source": "wtmp",
            }
        ]
        self.history_routes = []
        self.hold_history = False
        self.hold_stats = False
        self.stats_routes = []
        self.page.route(
            "https://cdn.tailwindcss.com/**",
            lambda route: route.fulfill(content_type="application/javascript", body=""),
        )
        self.page.route(
            "https://cdn.tailwindcss.com/",
            lambda route: route.fulfill(content_type="application/javascript", body=""),
        )
        self.page.route(
            "https://cdn.jsdelivr.net/**",
            lambda route: route.fulfill(
                content_type="application/javascript",
                body="window.testCharts=[]; window.Chart=class { static defaults={}; constructor(context,config){this.data=config.data;window.testCharts.push(this)} update(){} };",
            ),
        )
        self.page.route("**/api/**", self.api)

    def api(self, route):
        parsed = urlsplit(route.request.url)
        self.requests.append(parsed.path)
        if parsed.path == "/api/stats":
            if self.hold_stats:
                self.stats_routes.append(route)
                return
            return route.fulfill(status=self.status, json=copy.deepcopy(self.stats))
        if parsed.path == "/api/login_stats":
            return route.fulfill(
                json={"example-user": {"today_h": 1, "week_h": 4, "month_h": 12}}
            )
        if parsed.path == "/api/sessions":
            return route.fulfill(json=self.sessions)
        if parsed.path == "/api/gpu_history":
            if self.hold_history:
                self.history_routes.append(route)
                return
            return route.fulfill(
                json={
                    "labels": ["Example time"],
                    "datasets": [{"gpu": 3, "data": [80]}],
                    "points": 1,
                    "range": parse_qs(parsed.query)["range"][0],
                    "health": {"status": "ok"},
                }
            )
        route.fulfill(status=404, json={"error": "unexpected_test_endpoint"})

    def load(self):
        self.page.goto(self.url)
        self.page.wait_for_function(
            "document.getElementById('last-update').textContent !== '–'"
        )

    def test_host_strings_render_as_text_and_filters_remain_functional(self):
        payload = '"><img src=x onerror=window.injected=true><svg onload=window.injected=true>'
        self.stats["gpus"][0]["name"] = payload
        self.stats["processes"][0].update(user=payload, command=payload)
        self.stats["user_gpu"] = {
            payload: {"gpu_indices": [0], "mem_gb": 2, "proc_count": 1}
        }
        self.stats["connections"][0].update(user=payload, tty=payload, since=payload)
        self.sessions[0].update(
            user=payload, host=payload, terminal=payload, login=payload
        )
        self.load()
        self.page.wait_for_selector("#session-filter-pills button")
        self.assertEqual(
            self.page.locator("img, svg, [onerror], [onload], [onclick]").count(), 0
        )
        self.assertIsNone(self.page.evaluate("window.injected"))
        self.assertIn(payload, self.page.locator("#gpu-grid").inner_text())
        self.assertIn(payload, self.page.locator("#conn-tbody").inner_text())
        self.page.locator("#session-filter-pills button").nth(1).click()
        self.assertEqual(
            self.page.locator("#session-count").inner_text(), "Showing 1 sessions"
        )
        self.assertFalse(self.errors)

    def test_partial_failure_displays_unknown_instead_of_zero(self):
        self.stats["health"]["sources"]["gpus"] = {
            "status": "unavailable",
            "error": "command_timeout",
        }
        self.stats["health"]["sources"]["system"] = {"status": "unavailable"}
        self.stats["gpus"] = []
        self.stats["system"] = None
        self.load()
        self.assertEqual(
            self.page.locator("#connection-status").inner_text(), "PARTIAL"
        )
        self.assertEqual(self.page.locator("#sys-active-gpus").inner_text(), "—")
        self.assertEqual(self.page.locator("#sys-cpu").inner_text(), "—")
        self.assertIn(
            "GPU metrics unavailable", self.page.locator("#gpu-grid").inner_text()
        )
        self.assertFalse(self.errors)

    def test_nullable_gpu_metrics_render_without_nan_or_false_idle(self):
        self.stats["gpus"][0].update(
            utilization=None, temperature=None, power=None, memory_used=None
        )
        self.load()
        text = self.page.locator("#gpu-grid").inner_text()
        self.assertIn("UNKNOWN", text)
        self.assertIn("Unavailable", text)
        self.assertNotIn("NaN", text)
        self.assertFalse(self.errors)

    def test_request_failure_and_recovery_update_status(self):
        self.load()
        self.status = 503
        self.page.evaluate("fetchStats()")
        self.assertIn(
            "could not be refreshed", self.page.locator("#status-banner").inner_text()
        )

        self.assertNotEqual(
            self.page.locator("#connection-status").inner_text(), "LIVE"
        )
        self.status = 200
        self.page.evaluate("fetchStats()")
        self.assertEqual(self.page.locator("#connection-status").inner_text(), "LIVE")

    def test_stale_clock_marks_old_values(self):
        self.load()
        self.page.evaluate("lastSuccess = Date.now() - 16000; showStatus()")
        self.assertEqual(self.page.locator("#connection-status").inner_text(), "STALE")

    def test_missing_chart_dependency_does_not_stop_metric_tables(self):
        self.page.unroute("https://cdn.jsdelivr.net/**")
        self.page.route("https://cdn.jsdelivr.net/**", lambda route: route.abort())
        self.load()
        self.assertIn("example-user", self.page.locator("#user-gpu-tbody").inner_text())
        self.assertIn(
            "Charts could not load", self.page.locator("#status-banner").inner_text()
        )
        self.assertFalse(self.errors)

    def test_charts_keep_midnight_order_and_update_gpu_identity(self):
        self.load()
        self.assertEqual(
            self.page.evaluate("testCharts[0].data.labels"), ["23:59:59", "00:00:02"]
        )
        self.page.locator('[data-util-range="week"]').click()
        self.page.wait_for_function("testCharts[0].data.datasets[0].label === 'GPU 3'")
        self.assertFalse(self.errors)

    def test_late_history_response_cannot_replace_live_selection(self):
        self.load()
        self.hold_history = True
        self.page.locator('[data-util-range="month"]').click()
        self.page.wait_for_timeout(100)
        self.assertEqual(len(self.history_routes), 1)
        self.page.locator('[data-util-range="live"]').click()
        self.history_routes[0].fulfill(
            json={
                "labels": ["OLD"],
                "datasets": [{"gpu": 0, "data": [90]}],
                "points": 1,
            }
        )
        self.page.wait_for_timeout(100)
        self.assertIn(
            "Recent Samples", self.page.locator("#util-chart-title").text_content()
        )
        self.assertNotIn("OLD", self.page.evaluate("testCharts[0].data.labels"))

    def test_default_privacy_makes_no_session_requests(self):
        with patch.dict(app.app.config, {"SHOW_SESSIONS": False}):
            self.load()
        self.assertNotIn("/api/sessions", self.requests)
        self.assertNotIn("/api/login_stats", self.requests)
        self.assertEqual(self.page.locator("#session-tbody, #conn-tbody").count(), 0)
        self.assertFalse(self.errors)

    def test_slow_requests_do_not_overlap_and_timeout_is_visible(self):
        self.page.clock.install()
        self.load()
        self.hold_stats = True
        self.page.clock.fast_forward(5001)
        self.page.wait_for_timeout(100)
        self.assertEqual(len(self.stats_routes), 1)
        count = self.requests.count("/api/stats")
        self.page.clock.fast_forward(5000)
        self.page.wait_for_timeout(100)
        self.assertEqual(self.requests.count("/api/stats"), count)
        self.page.clock.fast_forward(10000)
        self.page.wait_for_function("panelErrors.has('statsRequest')")
        self.assertIn(
            "could not be refreshed", self.page.locator("#status-banner").inner_text()
        )
        for route in self.stats_routes:
            route.abort()


if __name__ == "__main__":
    unittest.main()
