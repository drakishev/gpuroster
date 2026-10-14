# ADR-009: Redesign the dashboard around "which GPU can I use?"

Status: accepted, 2026-10-14.

## Context

The dashboard answered the monitoring questions but made people work for the
roster question: which device is free right now, and who holds the busy ones?
GPU cards were rebuilt from scratch on every five-second refresh, so meters
could not animate and hover or focus reset. Session panels printed raw
microsecond ISO strings, listed every session in the 30-day window (about 90
rows in the demo), and repeated the per-user totals in a second chart. A
permanent banner said "Metrics are updating." even when nothing was wrong.
There was one dark theme, and chart colors were hashed from UUIDs into eight
hues, so two GPUs often shared a line color (59% chance with four devices).

Constraints from earlier decisions still apply: no third-party requests, no
inline scripts or styles under the content policy
([ADR-005](ADR-005-offline-assets-and-demo.md)), host strings only as text, UTC
API timestamps with explicit timezone labels
([ADR-003](ADR-003-time-identity-and-accounting.md)), and an unchanged schema 2
API. The browser tests also forbid `<img>` and `<svg>` elements in the page, so
icons must come from CSS.

## Decision

- **Roster state per GPU.** Cards show ACTIVE (utilization above 5%), IDLE, FREE
  or UNKNOWN. FREE needs low utilization, a known empty process list, and less
  than 5% memory in use, so a failed process probe or memory held by an
  invisible process never shows as free. The header, summary tiles, and browser
  tab title show the free count. When any GPU's utilization or the process list
  is unknown, they say "unknown" instead of a count. Host-wide tiles (average
  utilization, total GPU memory) show "—" unless every GPU reports the value.
- **In-place updates.** GPU cards are keyed by UUID and updated in place. Each
  card has a sparkline of the rolling live history and a memory meter. Process
  rows add the PID and sort by GPU, then memory. The user table shows each
  person's share of total GPU memory.
- **Stable, validated series colors.** GPUs get slots from a fixed
  eight-color categorical palette, keyed by UUID and assigned in display
  order. A new series takes the lowest slot no displayed series uses, and
  slots of GPUs or history series that are no longer shown are released.
  Colors therefore follow a device when its index changes and stay distinct for
  up to eight GPUs, even after a GPU is replaced while the page is open.
  Series hidden through the chart legend stay hidden across refreshes. Adjacent slots pass color-vision-deficiency separation in
  both themes. Status colors (good, warning, critical) are kept for state and
  always appear with a text label.
- **Light and dark themes.** CSS custom properties define both themes. The page
  follows the operating system until the viewer uses the toggle, and the choice
  is saved in `localStorage`. A 323-byte same-origin `theme.js` applies the
  saved theme before first paint. The content policy forbids an inline script,
  and loading the theme later would flash the wrong one. Charts read the
  tokens and re-theme in place.
- **Times in the reporting timezone.** Chart labels, session times, and the
  last-update clock use `GPUROSTER_TIMEZONE`, the zone that already defines
  "today". Each view names the zone, and chart tooltips include the zone
  abbreviation so the repeated hour at a DST change stays distinguishable. If
  the browser does not recognize the configured name (for example
  `localtime`), the page shows UTC and says so in the footer. The API stays
  UTC.
- **Quieter status.** The status banner appears only for problems. When
  everything is healthy it stays in the accessibility tree as an empty,
  collapsed live region, so a new problem is announced. A `warming_up` CPU
  baseline no longer marks the page PARTIAL.
- **Fewer, smaller session panels.** Connected time is one ranked bar list with
  a period switch; the duplicate grouped chart is gone. Session history pages
  25 rows at a time. Choosing a filter updates the buttons in place, so
  keyboard focus stays on the chosen one. After the last page is revealed,
  focus moves to the row count.
- **Accessibility.** The page adds a skip link, `aria-pressed` segmented
  controls, visible focus rings, a reduced-motion mode, and a status live
  region. Body text meets WCAG AA contrast (at least 4.5:1) in both themes.
  On phones the sticky header stays on one line by dropping the subtitle and
  then the clock, and summary totals wrap instead of being cut off.

## Alternatives

- Keep rebuilding cards each refresh: simplest, but transitions never run and
  focus is lost. Keyed updates cost about 100 lines of script.
- Inline SVG icon sprites: rejected because the XSS regression test forbids
  `<svg>` elements, and weakening that test is worse than drawing the few
  glyphs in CSS.
- A bundled web font: gives the same text on every platform, but adds about
  100 kB, a license notice, and asset-build work. The system font stack (Inter
  where installed) is enough.
- Charts in UTC with local-time tables: inconsistent, and "today" already
  starts at the configured midnight.
- Pause polling while the tab is hidden: saves a few requests, but the dynamic
  title exists so people can watch for a free GPU from a background tab.
  Browser timer throttling already limits background cost.

## Measurements

[Results](../../benchmarks/results/dashboard-redesign-2026-10-14.json) come from
`python -m benchmarks.dashboard`, with `main` and the redesign run back to back
on one machine. Do not compare them with runs from other machines.

| Measurement | Before (main) | After, light / dark |
| --- | --- | --- |
| Ready, median | 154 ms | 214 / 181 ms |
| Full refresh, median / p95 | 17.3 / 23.8 ms | 19.5 / 28.4 ms · 19.0 / 26.3 ms |
| Retained heap after 100 / 1,000 refreshes | 3.69 / 4.15 MB | 3.91 / 4.46 MB · 3.85 / 4.39 MB |
| CSS + dashboard script | 10.9 + 17.7 kB | 22.0 + 34.7 kB (+ 0.3 kB theme) |
| External requests / browser errors | 0 / 0 | 0 / 0 |

A refresh costs about 2 ms more, mostly for the larger DOM; Chart.js updates
dominate both versions. At the five-second interval that is under 0.05% of one
core. A first profile found a forced synchronous layout from sparkline sizing.
Sizing the canvases from constants cut card rendering from 1.25 ms to 0.12 ms.
Heap growth from 100 to 1,000 refreshes matches the baseline curve, so the
redesign adds no new leak pattern.

## Consequences

The API, schema, and configuration are unchanged. The package version moves
to 0.7.0 because the shipped files change. Integrations that scrape the
page should use the JSON API. Some visible strings changed: "Recent samples",
"7 days", and "Showing 1 session". The packaged assets now include
`static/theme.js` and `static/favicon.svg`, and the wheel, container, and smoke
checks cover both. The FREE heuristic does not reserve a device; MIG instances
and devices shared through other schedulers can still be in use.
