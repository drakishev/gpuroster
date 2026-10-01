"""Environment configuration; no credentials belong in repository files."""

import math
import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def load_settings(environ=None):
    env = os.environ if environ is None else environ

    def flag(name):
        value = env.get(name, "0").lower()
        if value not in {"0", "1", "false", "true"}:
            raise ValueError(f"{name} must be 0, 1, false, or true")
        return value in {"1", "true"}

    username = env.get("GPUROSTER_AUTH_USER", "")
    password = env.get("GPUROSTER_AUTH_PASSWORD", "")
    password_file = env.get("GPUROSTER_AUTH_PASSWORD_FILE", "")
    if not password_file and not password and env.get("CREDENTIALS_DIRECTORY"):
        password_file = str(Path(env["CREDENTIALS_DIRECTORY"]) / "dashboard-password")
    if password_file:
        if password:
            raise ValueError("Choose a password or a password file, not both")
        try:
            password = Path(password_file).read_text(encoding="utf-8").rstrip("\r\n")
        except (OSError, UnicodeError):
            raise ValueError("Cannot read GPUROSTER_AUTH_PASSWORD_FILE") from None
        if not password or "\n" in password or "\r" in password:
            raise ValueError("Password file must contain one nonempty line")
    if bool(username) != bool(password):
        raise ValueError("Set both GPUROSTER_AUTH_USER and GPUROSTER_AUTH_PASSWORD")
    timeout = float(env.get("GPUROSTER_COMMAND_TIMEOUT", "3"))
    if not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError(
            "GPUROSTER_COMMAND_TIMEOUT must be greater than 0 and at most 30"
        )
    port = int(env.get("GPUROSTER_PORT", "18081"))
    if not 1 <= port <= 65535:
        raise ValueError("GPUROSTER_PORT must be between 1 and 65535")
    interval = float(env.get("GPUROSTER_COLLECT_INTERVAL", "3"))
    session_interval = float(env.get("GPUROSTER_SESSION_INTERVAL", "60"))
    if not math.isfinite(interval) or not 0.1 <= interval <= 60:
        raise ValueError("GPUROSTER_COLLECT_INTERVAL must be between 0.1 and 60")
    if not math.isfinite(session_interval) or not 1 <= session_interval <= 3600:
        raise ValueError("GPUROSTER_SESSION_INTERVAL must be between 1 and 3600")
    backend = env.get("GPUROSTER_GPU_BACKEND", "auto")
    if backend not in {"auto", "smi", "nvml"}:
        raise ValueError("GPUROSTER_GPU_BACKEND must be auto, smi, or nvml")
    timezone_name = env.get("GPUROSTER_TIMEZONE", "UTC")
    try:
        ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValueError(
            "GPUROSTER_TIMEZONE must name an installed IANA timezone"
        ) from None
    return {
        "DEMO": flag("GPUROSTER_DEMO"),
        "BIND_HOST": env.get("GPUROSTER_HOST", "127.0.0.1"),
        "BIND_PORT": port,
        "AUTH_USER": username,
        "AUTH_PASSWORD": password,
        "SHOW_COMMANDS": flag("GPUROSTER_SHOW_COMMANDS"),
        "SHOW_SESSIONS": flag("GPUROSTER_SHOW_SESSIONS"),
        "COMMAND_TIMEOUT": timeout,
        "COLLECT_INTERVAL": interval,
        "SESSION_INTERVAL": session_interval,
        "GPU_BACKEND": backend,
        "TIMEZONE": timezone_name,
        "DB_PATH": env.get(
            "GPUROSTER_DB_PATH",
            str(
                Path(env.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
                / "gpuroster"
                / "history.db"
            ),
        ),
    }
