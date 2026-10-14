# ADR-010: Replace compiled Tailwind with authored CSS

Status: accepted, 2026-10-14. Supersedes the Tailwind part of
[ADR-005](ADR-005-offline-assets-and-demo.md); the offline-asset and demo
decisions are unchanged.

## Context

ADR-005 compiled Tailwind 3.4.17 so the dashboard no longer needed a CDN. At
the time, most styling lived in utility classes, and rewriting it would have
widened that change. The redesign in
[ADR-009](ADR-009-dashboard-experience.md) moved almost all styling into
token-based component CSS. Only about 25 layout utilities (grid columns, gaps,
responsive visibility) and Tailwind's reset were left.

In October 2026, `npm audit`, which CI runs on every push, began reporting a
high-severity advisory for `braces` (all versions) and a moderate one for
`postcss-selector-parser` below 7.1.6. Both come in through Tailwind 3's own
dependency tree (`chokidar`, `micromatch`, `fast-glob`, `postcss-nested`). No
Tailwind 3 release avoids them. The only upstream fix is Tailwind 4. These
packages run only at build time, on files from the repository, and nothing
from them ships at runtime, so the practical risk was low. Still, the audit
gate would fail on `main` the next time CI ran.

## Alternatives

- **Migrate to Tailwind 4.** Fixes the audit, but adds platform-specific native
  binaries (`@tailwindcss/oxide`, `lightningcss`), changes reset defaults,
  and requires Safari 16.4 / Chrome 111 / Firefox 128 or newer. It is a large
  migration for about 25 classes.
- **Allow-list the advisories.** npm has no built-in ignore list. Adding a tool
  such as `audit-ci` adds dependencies to silence a warning about dependencies.
- **Audit only runtime dependencies (`--omit=dev`).** Every npm package here
  is a development dependency, so this would disable the check.
- **Author the CSS directly.** Replace the utilities with semantic layout
  classes and a minimal reset. Remove Tailwind, its configuration, and its
  license notice.

## Decision

Author `gpuroster/static/dashboard.css` directly. It contains the design tokens,
a short reset, the component styles, and semantic layout classes
(`kpi-grid`, `gpu-grid`, `split-grid`). `npm run build:assets` now only
refreshes the vendored Chart.js bundle and its notices. CI keeps rebuilding
vendored assets, failing on drift, and running `npm audit`.

## Consequences

- The npm lock drops from 76 packages to 2 (`chart.js`,
  `@kurkle/color`), and `npm audit` reports no vulnerabilities.
- The stylesheet is about 20 kB unminified, versus 22 kB compiled and minified
  before. CSS changes no longer need Node or a rebuild.
- Contributors write plain CSS. New layout needs a named class instead of a
  utility. Use the existing tokens and breakpoints (640, 768, and 1280 px).
- The rendered dashboard is unchanged. Full-page screenshots of the demo with
  live values masked are pixel-identical before and after, in both themes at
  1440 px and 390 px. Element geometry inside the masked regions (cards,
  sparklines, swatches, meters, share bars) also matches. The browser suite
  passes, including the 390 px no-horizontal-scroll check.
