# Packaged frontend assets

The dashboard makes no third-party network requests. Python installs ship the
stylesheet, scripts, Chart.js UMD bundle, and license notices. Node is needed
only to refresh the vendored Chart.js bundle.

## Authored files

These files are edited directly. There is no CSS build step.

| File | Purpose |
| --- | --- |
| `gpuroster/templates/index.html` | Page structure |
| `gpuroster/static/dashboard.css` | Design tokens, reset, components, and layout |
| `gpuroster/static/dashboard.js` | Polling, rendering, charts, and theme switching |
| `gpuroster/static/theme.js` | Applies a saved theme before first paint |
| `gpuroster/static/favicon.svg` | Icon matching the CSS brand mark |

The stylesheet defines light and dark design tokens as CSS custom properties.
Use the tokens rather than fixed colors so both themes stay correct. The
categorical chart palette lives in `dashboard.js` because canvas drawing needs
literal colors; [ADR-009](../docs/architecture/ADR-009-dashboard-experience.md)
explains how it was validated. Status colors (good, warning, critical) are
reserved for state and always appear with a text label.

## Vendored Chart.js

Refresh with Node 24 and the committed dependency lock:

```bash
npm ci --ignore-scripts
npm run build:assets
npm audit
```

Commit the regenerated files under `gpuroster/static/vendor`. CI rebuilds them
and fails on any difference. The build copies Chart.js 4.4.3 from its npm
distribution without changing executable code; it only removes the optional
source-map URL. The package includes MIT notices for Chart.js and its bundled
color implementation. `package-lock.json` records registry integrity hashes.
Upstream projects: [Chart.js](https://github.com/chartjs/Chart.js/tree/v4.4.3)
and [color](https://github.com/kurkle/color).

## Content policy

Scripts, styles, and fetches may come only from this application, with no
inline script or `eval` exception. Widths and colors set through DOM style
properties still drive meters and Chart.js sizing. Host-provided strings are
always inserted as text, never HTML, and the page uses no `<img>` or `<svg>`
elements; icons are drawn in CSS. Browser tests load the real assets with
external requests blocked, check chart rendering and layout, and assert that
there are no policy violations. Other tests use a Chart stub to isolate
request ordering and rendering behavior.

The stylesheet is about 20 kB and the chart bundle about 205 kB before
compression. These sizes are not browser timing or capacity measurements.
Tailwind was removed in October 2026; see
[ADR-010](../docs/architecture/ADR-010-authored-css.md).
