"""Generate or check pip-compatible hash locks with the pinned resolver."""

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESOLVER_VERSION = "0.11.28"
GROUPS = {
    "runtime": ("requirements.txt", ()),
    "build": ("requirements/build.in", ()),
    "dev": ("requirements-dev.txt", ("runtime", "build")),
    "audit": ("requirements/audit.in", ("runtime", "build")),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check", action="store_true", help="fail on drift without editing files"
    )
    mode.add_argument(
        "--upgrade",
        action="store_true",
        help="deliberately refresh all allowed versions",
    )
    args = parser.parse_args()
    resolver = shutil.which("uv")
    if (
        resolver is None
        or subprocess.check_output([resolver, "--version"], text=True).split()[1]
        != RESOLVER_VERSION
    ):
        parser.error(f"install the build lock to use uv {RESOLVER_VERSION}")
    # Public PyPI is the sole source for these public-package locks. Do not let
    # local indexes, credentials, constraints, or user config affect generation.
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("UV_", "PIP_"))
    }
    with tempfile.TemporaryDirectory() as directory:
        generated = Path(directory)
        for group, (source, constraints) in GROUPS.items():
            target = generated / f"{group}.lock"
            existing = ROOT / "requirements" / target.name
            if existing.exists():
                shutil.copyfile(existing, target)
            command = [
                resolver,
                "--no-config",
                "pip",
                "compile",
                source,
                "--universal",
                "--python-version",
                "3.10",
                "--generate-hashes",
                "--no-annotate",
                "--default-index",
                "https://pypi.org/simple",
                "--custom-compile-command",
                "python scripts/lock_dependencies.py",
                "--output-file",
                str(target),
                "--quiet",
            ]
            for constraint in constraints:
                command.extend(("--constraint", str(generated / f"{constraint}.lock")))
            if args.upgrade:
                command.append("--upgrade")
            subprocess.run(command, cwd=ROOT, env=environment, check=True)
        changed = [
            path.name
            for path in generated.iterdir()
            if not (ROOT / "requirements" / path.name).exists()
            or path.read_bytes() != (ROOT / "requirements" / path.name).read_bytes()
        ]
        if args.check and changed:
            parser.exit(
                1, "Dependency lock drift: " + ", ".join(sorted(changed)) + "\n"
            )
        if not args.check:
            for path in generated.iterdir():
                shutil.copyfile(path, ROOT / "requirements" / path.name)
        print(
            "Dependency locks are current"
            if args.check
            else "Dependency locks generated"
        )


if __name__ == "__main__":
    main()
