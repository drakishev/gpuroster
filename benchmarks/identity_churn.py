"""Accelerated, synthetic rolling-history retention experiment (no hardware/DB).

Replace every GPU identity every tick for ten history windows, then expire all
devices. Report counts and traced Python bytes; simulated time is not wall time.
"""

import gc
import json
import time
import tracemalloc

from gpuroster.monitoring.history import RollingHistory
from gpuroster.monitoring.models import GPU


def run(windows=10, length=120, count=8):
    history = RollingHistory(interval=3, length=length)
    identity_bound = count * (length + 1)  # expiry is strictly beyond the horizon
    samples = []
    checks_passed = True
    started = time.monotonic()
    tracemalloc.start(1)
    try:
        for cycle in range(windows * length):
            devices = tuple(
                GPU(
                    index,
                    f"GPU-synthetic-{cycle}-{index}",
                    "Synthetic",
                    50,
                    0,
                    100,
                    30,
                    20,
                )
                for index in range(count)
            )
            history.append(devices, cycle * 3)
            keys = set(history.series)
            checks_passed &= (
                len(keys) <= identity_bound
                and keys == set(history.devices) == set(history.last_seen)
                and set(history.ema) == {device.uuid for device in devices}
            )
            if (cycle + 1) % length == 0:
                gc.collect()
                samples.append(
                    {
                        "cycles": cycle + 1,
                        "identities": len(keys),
                        "points": sum(
                            len(points) for points in history.series.values()
                        ),
                        "traced_bytes": tracemalloc.get_traced_memory()[0],
                    }
                )
        history.append((), (windows * length + length + 1) * 3)
        gc.collect()
        after = {
            "series": len(history.series),
            "devices": len(history.devices),
            "last_seen": len(history.last_seen),
            "ema": len(history.ema),
            "traced_bytes": tracemalloc.get_traced_memory()[0],
        }
        checks_passed &= all(
            after[key] == 0 for key in ("series", "devices", "last_seen", "ema")
        )
        steady = [row["traced_bytes"] for row in samples[windows // 2 :]]
        return {
            "schema_version": 1,
            "synthetic": True,
            "clock": "simulated, increasing three-second ticks",
            "history_length": length,
            "gpus_per_tick": count,
            "windows": windows,
            "distinct_identities": count * windows * length,
            "identity_bound": identity_bound,
            "samples": samples,
            "second_half_traced_band_bytes": max(steady) - min(steady),
            "after_expiry": after,
            "peak_traced_bytes": tracemalloc.get_traced_memory()[1],
            "wall_seconds": round(time.monotonic() - started, 3),
            "checks_passed": bool(checks_passed),
        }
    finally:
        tracemalloc.stop()


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["checks_passed"] else 1)
