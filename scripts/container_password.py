"""Create a local Compose password with a private parent directory; never echo it."""

import argparse
import getpass
import os
from pathlib import Path


def create_password(directory, password):
    if not password or "\n" in password or "\r" in password:
        raise ValueError("Choose a nonempty, single-line password")
    directory = Path(directory)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = directory.stat()
    if directory.is_symlink() or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("Password directory must be owned by you with mode 0700")
    path = directory / "dashboard-password"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(password + "\n")
        # Compose file secrets preserve host ownership. Only the mounted file
        # is readable by the container UID; the 0700 host parent stays private.
        os.fchmod(stream.fileno(), 0o444)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default="private/container")
    args = parser.parse_args()
    password = getpass.getpass("Dashboard password: ")
    if password != getpass.getpass("Confirm password: "):
        parser.error("Passwords do not match")
    try:
        create_password(args.directory, password)
    except (OSError, ValueError):
        parser.error(
            "Cannot create password: check the directory permissions and existing file"
        )
    print("Password file created. Keep its parent directory private (0700).")


if __name__ == "__main__":
    main()
