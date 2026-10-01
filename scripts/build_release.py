"""Build and compare release artifacts from a clean Git commit; no publishing."""

import argparse
import ast
import gzip
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zlib
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 build environment
    import tomli as tomllib


def git(source, *arguments):
    return subprocess.check_output(["git", *arguments], cwd=source, text=True).strip()


def source_identity(source):
    if git(source, "status", "--porcelain"):
        raise ValueError("Release builds require a clean working tree")
    return git(source, "rev-parse", "HEAD"), int(
        git(source, "show", "-s", "--format=%ct", "HEAD")
    )


def normalize_sdist(source, destination, epoch):
    """Preserve archive payloads/modes, replacing volatile tar/gzip metadata."""
    with tarfile.open(source, "r:gz") as original, destination.open("wb") as output:
        with gzip.GzipFile(
            filename="", fileobj=output, mode="wb", mtime=epoch
        ) as compressed:
            with tarfile.open(
                fileobj=compressed, mode="w|", format=tarfile.PAX_FORMAT
            ) as normalized:
                for member in sorted(original.getmembers(), key=lambda item: item.name):
                    member.uid = member.gid = 0
                    member.uname = member.gname = ""
                    member.mtime = epoch
                    member.pax_headers = {}
                    content = original.extractfile(member) if member.isfile() else None
                    try:
                        normalized.addfile(member, content)
                    finally:
                        if content is not None:
                            content.close()


def package_version(source):
    version = tomllib.loads((source / "pyproject.toml").read_text())["project"][
        "version"
    ]
    tree = ast.parse((source / "gpuroster/__init__.py").read_text())
    values = [
        ast.literal_eval(statement.value)
        for statement in tree.body
        if isinstance(statement, ast.Assign)
        for target in statement.targets
        if isinstance(target, ast.Name) and target.id == "__version__"
    ]
    if values != [version]:
        raise ValueError("Project and launcher versions disagree")
    return version


def build_once(archive, directory, epoch):
    checkout = directory / "checkout"
    checkout.mkdir(parents=True)
    with tarfile.open(archive) as source:
        source.extractall(checkout, filter="data")
    version = package_version(checkout)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTHON", "PIP_", "UV_"))
    }
    environment.update(SOURCE_DATE_EPOCH=str(epoch), PYTHONHASHSEED="0")
    raw, final = directory / "raw", directory / "final"
    final.mkdir()
    command = [sys.executable, "-m", "build", "--no-isolation"]
    subprocess.run(
        command + ["--sdist", "--outdir", str(raw), str(checkout)],
        env=environment,
        check=True,
    )
    sdist = final / f"gpuroster-{version}.tar.gz"
    normalize_sdist(raw / sdist.name, sdist, epoch)
    unpacked = directory / "unpacked"
    with tarfile.open(sdist) as source:
        source.extractall(unpacked, filter="data")
    # Build from the normalized sdist to verify it is a complete build input.
    subprocess.run(
        command
        + ["--wheel", "--outdir", str(final), str(unpacked / f"gpuroster-{version}")],
        env=environment,
        check=True,
    )
    for name in ("runtime.lock", "build.lock"):
        shutil.copyfile(checkout / "requirements" / name, final / name)
    return version, final


def checksums(directory):
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.iterdir())
    }


def build_release(source, destination, expected_version=None):
    source, destination = source.resolve(), destination.resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Release output directory must be empty")
    commit, epoch = source_identity(source)
    with tempfile.TemporaryDirectory(prefix="gpuroster-build-") as directory:
        temporary = Path(directory)
        archive = temporary / "source.tar"
        subprocess.run(
            ["git", "archive", "--format=tar", "--output", str(archive), commit],
            cwd=source,
            check=True,
        )
        version, first = build_once(archive, temporary / "first", epoch)
        if expected_version is not None and version != expected_version:
            raise ValueError("Requested release version does not match the commit")
        second_version, second = build_once(archive, temporary / "second", epoch)
        if version != second_version or checksums(first) != checksums(second):
            raise ValueError("Independent builds produced different artifacts")
        provenance = {
            "version": version,
            "commit": commit,
            "source_date_epoch": epoch,
            "python": platform.python_version(),
            "zlib": zlib.ZLIB_RUNTIME_VERSION,
            "build_tools": {
                name: importlib.metadata.version(name)
                for name in ("build", "setuptools", "packaging", "pyproject-hooks")
            },
            "independent_builds_matched": True,
        }
        (first / "build-info.json").write_text(
            json.dumps(provenance, indent=2, sort_keys=True) + "\n"
        )
        hashes = checksums(first)
        (first / "SHA256SUMS").write_text(
            "".join(f"{digest}  {name}\n" for name, digest in hashes.items())
        )
        destination.mkdir(parents=True, exist_ok=True)
        for path in first.iterdir():
            shutil.copyfile(path, destination / path.name)
        return provenance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--output", type=Path, default=Path("dist/release"))
    parser.add_argument("--expected-version")
    args = parser.parse_args()
    try:
        result = build_release(args.source, args.output, args.expected_version)
    except ValueError as error:
        parser.exit(1, str(error) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
