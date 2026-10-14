/* Build checked-in vendor assets. Python installs do not require Node. */
const fs = require('node:fs');
const path = require('node:path');

process.chdir(path.resolve(__dirname, '..'));
const target = 'gpuroster/static/vendor';
fs.mkdirSync(target, {recursive: true});
const chart = fs.readFileSync('node_modules/chart.js/dist/chart.umd.js', 'utf8');
// Omit the optional source-map pointer: no map is shipped or needed at runtime.
fs.writeFileSync(`${target}/chart.umd.js`, chart.replace(/^\/\/# sourceMappingURL=.*\n?/gm, ''));
for (const [source, filename] of [
  ['chart.js/LICENSE.md', 'chartjs'],
  ['@kurkle/color/LICENSE.md', 'color'],
]) {
  fs.copyFileSync(`node_modules/${source}`, `${target}/${filename}.LICENSE.txt`);
}
