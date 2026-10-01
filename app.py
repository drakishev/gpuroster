"""Compatibility launcher for existing source checkouts."""

import os
from pathlib import Path


def main():
    # Preserve the original checkout database; installed CLI uses user state.
    os.environ.setdefault(
        "GPUROSTER_DB_PATH", str(Path(__file__).resolve().with_name("gpu_stats.db"))
    )
    from gpuroster.server import main as serve

    serve()


if __name__ == "__main__":
    main()
