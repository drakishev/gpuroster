/* Build checked-in runtime assets. Python installs do not require Node. */
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');

process.chdir(path.resolve(__dirname, '..'));
const target = 'gpuroster/static/vendor';
fs.mkdirSync(target, {recursive: true});
execFileSync(process.execPath, [
  'node_modules/tailwindcss/lib/cli.js',
  '--config', 'frontend/tailwind.config.cjs',
  '--input', 'frontend/dashboard.css',
  '--output', 'gpuroster/static/dashboard.css',
  '--minify',
], {stdio: 'inherit'});
const chart = fs.readFileSync('node_modules/chart.js/dist/chart.umd.js', 'utf8');
// Omit the optional source-map pointer: no map is shipped or needed at runtime.
fs.writeFileSync(`${target}/chart.umd.js`, chart.replace(/^\/\/# sourceMappingURL=.*\n?/gm, ''));
for (const [source, filename] of [
  ['chart.js/LICENSE.md', 'chartjs'],
  ['@kurkle/color/LICENSE.md', 'color'],
  ['tailwindcss/LICENSE', 'tailwind'],
]) {
  fs.copyFileSync(`node_modules/${source}`, `${target}/${filename}.LICENSE.txt`);
}
