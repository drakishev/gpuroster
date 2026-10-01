"""Verify Docker's real ignore behavior using only tracked files and fake secrets."""

import os
import secrets
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    image = "gpuroster-context-check:" + secrets.token_hex(6)
    container = None
    marker = b"GPUROSTER_SYNTHETIC_CONTEXT_CANARY"
    forbidden = (
        ".env",
        ".env.local",
        "credentials.json",
        "private/dashboard-password",
        "gpu_stats.db",
        "sample.key",
        "venv/private.txt",
        ".git/private.txt",
        "gpuroster/private.db",
        "gpuroster/.env",
        "gpuroster/__pycache__/private.pyc",
    )
    with tempfile.TemporaryDirectory(prefix="gpuroster-context-") as directory:
        context = Path(directory) / "context"
        context.mkdir()
        files = (
            subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
            .decode()
            .split("\0")
        )
        for name in filter(None, files):
            source, destination = root / name, context / name
            if source.is_file() and not source.is_symlink():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
        for name in forbidden:
            path = context / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(marker)
        try:
            subprocess.run(
                ["docker", "build", "--file", "-", "--tag", image, str(context)],
                input=b"FROM scratch\nCOPY . /context\n",
                capture_output=True,
                check=True,
                timeout=120,
            )
            container = subprocess.check_output(
                ["docker", "create", image, "/unused"], text=True
            ).strip()
            archive = Path(directory) / "context.tar"
            with archive.open("wb") as output:
                subprocess.run(
                    ["docker", "export", container],
                    stdout=output,
                    stderr=subprocess.PIPE,
                    check=True,
                    timeout=60,
                )
            with tarfile.open(archive) as contents:
                names = set(contents.getnames())
                assert "context/gpuroster/healthcheck.py" in names
                assert "context/gpuroster/static/vendor/chart.umd.js" in names
                assert "context/requirements/runtime.lock" in names
                leaked = [name for name in forbidden if "context/" + name in names]
                assert not leaked, f"Synthetic files reached context: {leaked}"
                for member in contents:
                    if member.isfile():
                        with contents.extractfile(member) as stream:
                            assert marker not in stream.read(), (
                                "Fake secret reached the build context"
                            )
            print(
                f"Docker context excludes all {len(forbidden)} synthetic private-file fixtures."
            )
        finally:
            if container:
                subprocess.run(
                    ["docker", "rm", "--force", container],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            subprocess.run(
                ["docker", "image", "rm", image],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )


if __name__ == "__main__":
    # Prevent the context check from inheriting an accidental deployment secret.
    for key in list(os.environ):
        if key.startswith("GPUROSTER_"):
            os.environ.pop(key)
    main()
