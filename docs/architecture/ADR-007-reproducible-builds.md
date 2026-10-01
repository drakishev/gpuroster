# ADR-007: Hash-locked dependencies and verified build bundles

Status: accepted for Phase 6, 2026-10-01.

## Context and requirements

Minimum Python requirements made clean installs depend on current transitive
versions. Build isolation independently resolved build tools. CI tested
packages but did not retain a verified bundle. The project needs repeatable
deployment inputs, compatible package metadata, explicit updates, clean builds,
and artifacts tied to tested commits, without adding a runtime package manager.

## Alternatives and evidence

Direct pins alone leave transitive versions and artifacts unconstrained. A uv
project lock offers a broader environment-management workflow; hashed pip files
fit the existing packaging interface with fewer changes. Generate runtime,
build, dev, and audit locks from the existing requirement inputs with uv 0.11.28.
Constrain dev/audit to the runtime/build versions so combined environments agree.
Ordinary pip handles installation and hash checking. See the
[prototype and negative checks](../research/reproducible-builds.md) and
[lock maintenance procedure](../../requirements/README.md).

Initial clean installation passed 85 backend tests and the runtime audit.
Changing a requirement triggered drift detection without editing locks; an
incorrect artifact hash was rejected. Two ordinary builds with a fixed epoch
matched wheels but differed in source archives. Normalizing tar/gzip metadata
then produced matching wheel and source archives in two independent builds from
a clean fixture commit. Tests verify payload/mode preservation, metadata
normalization, version mismatches, dirty-tree rejection, ignored-file exclusion,
and preservation of existing output artifacts when a destination is nonempty.

## Decision

Keep package-compatible dependency ranges in `requirements.txt`; maintain
separate exact, hashed deployment/CI locks. Hash-verified wheel-only installation
fails when a dependency wheel is unavailable. CI uses the locks for supported
Python versions and regenerates them to detect drift. Keep one separate latest-
compatible runtime job so locks do not hide broken declared compatibility.
Build dependencies are installed from their lock and builds disable isolation.

Build artifacts from a clean exported Git commit. Compare two independent
source/wheel builds before emitting a bundle. Fix archive timestamps/ownership
and preserve source payloads/modes. Build the wheel from the normalized source
archive. Record the commit, package version, build tools, Python, and zlib; ship
SHA-256 checksums and runtime/build locks alongside the package files.

After all other CI jobs pass, smoke-test the exact wheel in a clean environment,
then upload the bundle as a GitHub Actions artifact for 30 days. No tags or
package publishing are triggered. Repository permissions remain read-only.
The pinned [upload-artifact action](https://github.com/actions/upload-artifact)
provides artifact storage; [workflow job dependencies](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idneeds)
enforce the validation gate.

## Tradeoffs and consequences

Generated hashes add maintenance text to the repository. Updates are deliberate
and must regenerate locks; a dependency audit does not detect all malicious code
or eliminate vulnerabilities. Universal resolution does not certify every OS;
CI covers Linux Python 3.10/3.12/3.14. Runtime locks omit interpreter, OS, browser,
and driver dependencies. A pinned resolver also needs deliberate upgrades.

Artifacts expire unless saved or later published through a separately defined
release process. The build manifest records provenance but is not signed.
Repeated byte equality is asserted for the same toolchain, not arbitrary Python,
compression-library, or OS versions. No new application schema, hardware
collection, or deployment behavior is introduced.
