# Building and validating a release bundle

CI produces a downloadable artifact only after quality, locked backend/browser
tests, dependency audit, clean packaging, and compatibility checks pass. The
artifact name includes the tested Git commit. Open the successful
[CI run](https://github.com/drakishev/gpuroster/actions/workflows/ci.yml) and
download its `gpuroster-<commit>` artifact. Artifacts expire after 30 days; save
reviewed bundles before that deadline. A successful run is a build candidate,
not a published package release.

Each bundle contains a wheel, normalized source distribution, runtime/build
hash locks, `build-info.json`, and `SHA256SUMS`. CI installs and smoke-tests the
exact bundled wheel in live and synthetic demo modes before uploading it.
The workflow has read-only repository permissions and requires no publishing
credentials. It does not create tags or publish to GitHub Releases or PyPI.

## Reproduce locally

Check out the desired commit in a clean worktree. Install the build lock into a
fresh environment, then build into an empty destination:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes --only-binary=:all: -r requirements/build.lock
.venv/bin/python scripts/build_release.py --output dist/release
cd dist/release
sha256sum -c SHA256SUMS
```

`--expected-version 0.5.1` can enforce a planned version. The script rejects dirty
working trees, mismatched package/launcher versions, and nonempty output
directories. It exports the committed Git tree; ignored local configuration,
databases, caches, and environments are excluded. It never builds from the live
working directory or starts host monitoring.

Two independent exports are built with the same commit timestamp as
`SOURCE_DATE_EPOCH`, pinned build tools, and build isolation disabled. Source
archives have sorted members, fixed gzip/tar timestamps, and neutral ownership;
file contents and permission modes are preserved. Wheels are built from these
normalized source archives. The builder refuses to output a bundle unless both
source and wheel hashes match between builds.

The manifest checks download integrity. `build-info.json` records commit,
version, timestamp, Python/zlib versions, and build-tool versions; it is not a
cryptographic provenance attestation. Repeatability is tested within the same
toolchain. Different Python/zlib/OS versions can still change bytes; reproduce
with the recorded environment when comparing an older bundle. CI selects the
Python 3.12 line and records the actual patch version.

## Install a reviewed bundle

Verify checksums before installing. Use a separate environment and the included
runtime lock, then install the wheel without resolving dependencies again:

```bash
sha256sum -c SHA256SUMS
python3 -m venv /tmp/gpuroster-release-check
/tmp/gpuroster-release-check/bin/python -m pip install --require-hashes --only-binary=:all: -r runtime.lock
/tmp/gpuroster-release-check/bin/python -m pip install --no-deps gpuroster-0.5.1-py3-none-any.whl
/tmp/gpuroster-release-check/bin/python -m pip check
/tmp/gpuroster-release-check/bin/gpuroster --demo
```

Use the [deployment guide](deployment.md) for an actual host upgrade, including
database backup and stopping the previous owner. Dependency locks do not replace
target-host permissions/driver validation. This workflow does not install or
restart an existing service.
