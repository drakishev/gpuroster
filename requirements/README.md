# Tested dependency sets

The root `requirements.txt` defines package compatibility. These generated locks
select exact versions and hashes for reproducible installation with pip:

- `runtime.lock`: application and transitive runtime dependencies.
- `dev.lock`: runtime dependencies, Ruff, and Playwright.
- `build.lock`: pinned build, resolver, and installer tools.
- `audit.lock`: pinned dependency-audit tooling.

Locks use environment markers for Python/platform differences. CI validates
Linux Python 3.10, 3.12, and 3.14; other platforms are not certified. Wheel-only
installation intentionally fails where a supported dependency wheel is absent.
Use a new virtual environment so unrelated installed packages are excluded.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements/runtime.lock
```

For development, install `build.lock` and `dev.lock` together, then install the
project with `pip install --no-deps --no-build-isolation -e .`. For a built wheel,
install `runtime.lock` first, install the wheel with `--no-deps`, and run
`pip check`. Application package metadata remains compatible with normal pip
resolution; CI keeps a separate check using the unlocked requirement ranges.

## Maintenance

Edit the root requirements files or `build.in` / `audit.in`, then regenerate:

```bash
python scripts/lock_dependencies.py
python scripts/lock_dependencies.py --check
```

The script requires uv 0.11.28 from `build.lock`. Existing pins are retained when
compatible. `--upgrade` deliberately refreshes all allowed versions; exact
input pins must be edited explicitly. The resolver uses public PyPI and ignores
local uv/pip settings. Review lock diffs, audit the runtime lock, run tests and
installed smoke checks, then commit inputs and generated locks together. CI
regenerates in a temporary directory and rejects drift without modifying files.

Hashes protect artifact integrity; they do not establish that a package is free
of vulnerabilities. Versions, hashes, and audit tooling still require maintenance.
The lock does not pin Python, the operating system, NVIDIA drivers, or browser
system dependencies.
