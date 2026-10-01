"""Authenticated cached-snapshot liveness probe; never print credentials/data."""

import base64
import json
import time
import urllib.request
from datetime import datetime

from gpuroster.settings import load_settings


def fresh_snapshot(snapshot, interval, now):
    stamp = datetime.fromisoformat(snapshot["timestamp"])
    if stamp.tzinfo is None:
        return False
    age = now - stamp.timestamp()
    return (
        snapshot["schema_version"] == 2
        and snapshot["sequence"] > 0
        and -5 <= age <= max(15, interval * 3)
    )


def check():
    try:
        config = load_settings()
        request = urllib.request.Request(
            f"http://127.0.0.1:{config['BIND_PORT']}/api/stats"
        )
        if config["AUTH_USER"]:
            encoded = base64.b64encode(
                f"{config['AUTH_USER']}:{config['AUTH_PASSWORD']}".encode()
            ).decode()
            request.add_header("Authorization", "Basic " + encoded)
        # A health probe must not route local credentials through an HTTP proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=3) as response:
            body = response.read(4 * 1024 * 1024 + 1)
            if response.status != 200 or len(body) > 4 * 1024 * 1024:
                return False
            snapshot = json.loads(body)
        return fresh_snapshot(snapshot, config["COLLECT_INTERVAL"], time.time())
    except Exception:
        return False


if __name__ == "__main__":
    raise SystemExit(0 if check() else 1)
