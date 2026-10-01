# Contributing

Use [drakishev/gpuroster](https://github.com/drakishev/gpuroster) as the canonical repository. Keep `main` stable and create a feature, fix, research, or experiment branch for meaningful work.

Before every commit, inspect `git status`, `git diff`, and `git diff --staged`. Run the relevant checks from the [README](README.md). Review additions for credentials, `.env` files, private addresses, usernames, real process arguments, databases, and generated artifacts. Tests and screenshots must use synthetic data.

Use descriptive commits such as `fix: handle unavailable GPU metrics` and push each meaningful tested unit. Open a pull request for significant changes, explaining behavior, tests, migration implications, and limitations. Include synthetic screenshots for UI changes. Wait for relevant CI checks before merging. Never force-push `main` or remove the preserved baseline.

For uncertain architectural choices, write a hypothesis and success criteria, build the smallest useful experiment, and record measurements and rejected alternatives. Put decisions in `docs/architecture/` and benchmark methods/results in `benchmarks/` or `docs/benchmarks.md`. Do not commit benchmark databases or real server output.

Ordinary pull requests must pass without NVIDIA hardware. Keep real-hardware checks optional and separate. Session accounting, collector performance, and history storage changes need tests or measurements that verify the claimed behavior.
