# Dependency and artifact reproducibility experiment

Date: 2026-10-01. Baseline: 0.5.0, commit `ddd6050`.

Hypothesis: pip-compatible hash locks and a fixed build environment can reproduce
the installed dependency set and release artifacts without introducing a runtime
package-manager dependency. Acceptance: clean locked installation and tests on
Python 3.10/3.12/3.14; CI detects lock drift; two clean builds from the same commit
produce matching wheel and source-archive hashes; installed live/demo smoke
checks pass using the built wheel.

## Alternatives

- Keep minimum requirements alone: useful for compatibility testing, but each
  installation can resolve different transitive versions and artifacts.
- Pin direct dependencies only: transitive versions remain unconstrained.
- Adopt a uv project lock: supports platform/version resolution and environment
  synchronization, but changes the project's dependency-management interface.
- Compile existing requirements into hashed pip files: retains the established
  pip installation and package metadata, while adding explicit deployment and
  CI inputs. Use one universal lock per runtime/build/dev/audit group, with dev
  and audit constrained to the runtime/build versions.

The selected prototype uses uv 0.11.28 only to resolve locks. Pip verifies hashes
and installs them. Public PyPI is the explicit source; local package indexes or
credentials cannot enter generated files. Runtime package metadata still states
compatible requirements; the separate lock selects tested deployment versions.
[uv compilation](https://docs.astral.sh/uv/pip/compile/) preserves existing pins
unless upgrades are requested. [pip hash checking](https://pip.pypa.io/en/stable/topics/secure-installs/)
requires a complete, pinned dependency closure. CI will retain a separate
unlocked compatibility check for the declared requirement ranges.

## Initial measurements

A clean Python 3.12 pip environment installed the runtime prototype using
`--require-hashes --only-binary=:all:`. `pip check` and all 85 backend tests passed.
The runtime lock has ten packages, including transitive dependencies.

The generated runtime/build/dev/audit locks also installed together in a clean
Python 3.12 environment without conflicts. All backend tests and the runtime
vulnerability audit passed. Adding a dependency temporarily made `--check` fail
with drift, while leaving the committed lock contents unchanged. A deliberately
incorrect wheel hash was rejected by pip. The input was restored after the
negative check; no test-only dependency enters the project.

Two ordinary `python -m build --no-isolation` runs with the same
`SOURCE_DATE_EPOCH` produced identical wheel hashes, but different source-archive
hashes. Inspection of setuptools 84.0.0 shows tar/gzip timestamps and ownership
are not normalized by that environment variable. Test normalization of archive
metadata, preserve file bytes and modes, and compare both artifacts after two
independent builds. Pin build dependencies and build from an exported commit so
ignored databases, credentials, and local files cannot enter the source tree.
