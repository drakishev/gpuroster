"""Measure the real local demo and packaged assets; no host monitoring.

Run: python -m benchmarks.dashboard [--soak-seconds 300]
Requires the development dependencies and Playwright Chromium.
"""

import argparse
import json
import math
import statistics
import threading
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from gpuroster.app import create_app
from gpuroster.server import HTTPServer
from gpuroster.settings import load_settings


def measure(screenshot=None, soak_seconds=0, refresh_interval=0.5):
    application = create_app({**load_settings({}), "DEMO": True})
    collector = application.extensions["collector"]
    server = HTTPServer(application, "127.0.0.1", 0)
    stop = threading.Event()
    thread = threading.Thread(target=server.run, args=(stop,), daemon=True)
    collector.start()
    thread.start()
    url = f"http://127.0.0.1:{server.server.effective_port}"
    external, errors, http_errors, loads = [], [], [], []
    soak = None

    def local_only(route):
        if route.request.url.startswith(url + "/"):
            route.continue_()
        else:
            external.append(route.request.url)
            route.abort()

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for index in range(5):
                context = browser.new_context(viewport={"width": 1440, "height": 1050})
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on(
                    "response",
                    lambda response: (
                        http_errors.append(response.status)
                        if response.status >= 400
                        else None
                    ),
                )
                page.route("**/*", local_only)
                started = time.perf_counter()
                page.goto(url)
                page.locator(".gpu-card").nth(3).wait_for()
                loads.append((time.perf_counter() - started) * 1000)
                if index == 4:
                    page.locator("#connection-status").get_by_text(
                        "DEMO", exact=True
                    ).wait_for()
                    if screenshot:
                        page.screenshot(path=screenshot, full_page=True)
                    session = context.new_cdp_session(page)
                    session.send("Performance.enable")

                    def retained_heap():
                        session.send("HeapProfiler.collectGarbage")
                        return next(
                            item["value"]
                            for item in session.send("Performance.getMetrics")[
                                "metrics"
                            ]
                            if item["name"] == "JSHeapUsedSize"
                        )

                    before = retained_heap()
                    updates = []
                    for update in range(1000):
                        started = time.perf_counter()
                        page.evaluate("fetchStats()")
                        updates.append((time.perf_counter() - started) * 1000)
                        if update == 99:
                            after_100 = retained_heap()
                    after = retained_heap()
                    if soak_seconds:
                        soak_started = time.monotonic()
                        next_sample = 30
                        selected = "live"
                        refreshes = 0
                        samples = [{"seconds": 0, "retained_js_heap_bytes": int(after)}]
                        while time.monotonic() - soak_started < soak_seconds:
                            tick = time.monotonic()
                            elapsed = tick - soak_started
                            # Exercise chart reuse across live and historical views.
                            period = ("live", "today", "week", "month")[
                                int(elapsed // 30) % 4
                            ]
                            if period != selected:
                                page.evaluate("setUtilRange", period)
                                selected = period
                            page.evaluate(
                                "Promise.all([fetchStats(), utilRange === 'live' ? Promise.resolve() : fetchHistoryChart(utilRange)])"
                            )
                            refreshes += 1
                            if elapsed >= next_sample:
                                samples.append(
                                    {
                                        "seconds": round(elapsed, 3),
                                        "retained_js_heap_bytes": int(retained_heap()),
                                    }
                                )
                                next_sample += 30
                            remaining = min(
                                refresh_interval - (time.monotonic() - tick),
                                soak_seconds - (time.monotonic() - soak_started),
                            )
                            if remaining > 0:
                                page.wait_for_timeout(remaining * 1000)
                        samples.append(
                            {
                                "seconds": round(time.monotonic() - soak_started, 3),
                                "retained_js_heap_bytes": int(retained_heap()),
                            }
                        )
                        soak = {
                            "duration_s": round(time.monotonic() - soak_started, 3),
                            "manual_refresh_interval_s": refresh_interval,
                            "manual_refreshes": refreshes,
                            "range_switch_interval_s": 30,
                            "heap_samples": samples,
                        }
                    assets = page.evaluate(
                        "performance.getEntriesByType('resource').filter(r => r.name.includes('/static/')).map(r => ({path: new URL(r.name).pathname, bytes: r.encodedBodySize}))"
                    )
                context.close()
            browser.close()
        assert not external and not errors and not http_errors, (
            "Unexpected network requests, HTTP errors, or browser errors"
        )
        return {
            "mode": "synthetic_demo",
            "loads_with_fresh_context": 5,
            "ready_median_ms": round(statistics.median(loads), 3),
            "ready_max_ms": round(max(loads), 3),
            "updates": 1000,
            "update_median_ms": round(statistics.median(updates), 3),
            "update_p95_ms": round(
                sorted(updates)[math.ceil(len(updates) * 0.95) - 1], 3
            ),
            "retained_js_heap_before_bytes": int(before),
            "retained_js_heap_after_100_bytes": int(after_100),
            "retained_js_heap_after_bytes": int(after),
            "external_requests": len(external),
            "browser_errors": len(errors),
            "http_errors": len(http_errors),
            "assets": assets,
            **({"soak": soak} if soak else {}),
        }
    finally:
        stop.set()
        thread.join(3)
        server.close()
        collector.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--soak-seconds", type=float, default=0)
    parser.add_argument("--refresh-interval", type=float, default=0.5)
    args = parser.parse_args()
    if args.soak_seconds < 0 or args.refresh_interval <= 0:
        parser.error("soak-seconds must be nonnegative and refresh-interval positive")
    print(
        json.dumps(
            measure(
                str(args.screenshot) if args.screenshot else None,
                args.soak_seconds,
                args.refresh_interval,
            ),
            indent=2,
        )
    )
