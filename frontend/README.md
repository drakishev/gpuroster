# Packaged frontend assets

The dashboard makes no third-party network requests. Python installs ship the compiled CSS, Chart.js UMD bundle, and their license notices. Node is needed only when maintaining these assets.

Rebuild using Node 24 and the committed dependency lock:

```bash
npm ci --ignore-scripts
npm run build:assets
npm audit
```

Commit the regenerated files under `gpuroster/static` with the source changes. CI rebuilds and checks for differences. Tailwind 3.4.17 scans the template and dashboard JavaScript, including complete class names used by DOM rendering. Keep dynamic classes as complete strings, as described in [Tailwind's content configuration](https://v3.tailwindcss.com/docs/content-configuration).

`frontend/dashboard.css` contains Tailwind directives and the existing custom styles. The build copies Chart.js 4.4.3 from its npm distribution without changing executable code; only the optional source-map URL is removed. Runtime files include MIT notices for Chart.js, its bundled color implementation, and Tailwind. `package-lock.json` records registry integrity hashes; upstream projects are [Chart.js](https://github.com/chartjs/Chart.js/tree/v4.4.3), [color](https://github.com/kurkle/color), and [Tailwind](https://github.com/tailwindlabs/tailwindcss/tree/v3.4.17).

The response policy allows scripts, styles, and fetches only from this application, with no inline script or eval exception. Widths/colors set through DOM style properties still support progress bars and Chart.js sizing. Inline HTML style attributes were moved into the stylesheet. Browser tests load the real assets with external requests blocked, check chart rendering and layout, and assert no policy violations; other tests use a Chart stub to isolate request ordering and rendering behavior.

The compiled stylesheet is approximately 11 kB and the chart bundle approximately 205 kB before compression. This removes the Tailwind runtime compiler and CDN availability from page startup. These file sizes are not browser timing or capacity measurements.
