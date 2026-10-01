"""Measure the real local demo and packaged assets; no host monitoring.

Run: python -m benchmarks.dashboard [--screenshot docs/images/demo-dashboard.png]
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


def measure(screenshot=None):
    application = create_app({**load_settings({}), "DEMO": True})
    collector = application.extensions["collector"]
    server = HTTPServer(application, "127.0.0.1", 0)
    stop = threading.Event()
    thread = threading.Thread(target=server.run, args=(stop,), daemon=True)
    collector.start()
    thread.start()
    url = f"http://127.0.0.1:{server.server.effective_port}"
    external, errors, loads = [], [], []

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
                    assets = page.evaluate(
                        "performance.getEntriesByType('resource').filter(r => r.name.includes('/static/')).map(r => ({path: new URL(r.name).pathname, bytes: r.encodedBodySize}))"
                    )
                context.close()
            browser.close()
        assert not external and not errors, (
            "Unexpected network requests or browser errors"
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
            "assets": assets,
        }
    finally:
        stop.set()
        thread.join(3)
        server.close()
        collector.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(measure(str(args.screenshot) if args.screenshot else None), indent=2)
    )
