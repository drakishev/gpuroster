"""Environment configuration; no credentials belong in repository files."""

import math
import os


def load_settings(environ=None):
    env = os.environ if environ is None else environ

    def flag(name):
        value = env.get(name, "0").lower()
        if value not in {"0", "1", "false", "true"}:
            raise ValueError(f"{name} must be 0, 1, false, or true")
        return value in {"1", "true"}

    username = env.get("GPUROSTER_AUTH_USER", "")
    password = env.get("GPUROSTER_AUTH_PASSWORD", "")
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
    return {
        "BIND_HOST": env.get("GPUROSTER_HOST", "127.0.0.1"),
        "BIND_PORT": port,
        "AUTH_USER": username,
        "AUTH_PASSWORD": password,
        "SHOW_COMMANDS": flag("GPUROSTER_SHOW_COMMANDS"),
        "SHOW_SESSIONS": flag("GPUROSTER_SHOW_SESSIONS"),
        "COMMAND_TIMEOUT": timeout,
    }
