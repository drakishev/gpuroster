/* Host-provided values are always text nodes or DOM properties, never HTML. */
const sessionsEnabled = document.body.dataset.sessionsEnabled === 'true';
const demoMode = document.body.dataset.demo === 'true';
const requestTimeout = Number(document.body.dataset.requestTimeoutMs) || 14000;
const SESSION_PAGE = 25;
const SPARKLINE = {width: 112, height: 40}; // Matches .sparkline in dashboard.css; avoids layout reads.
// Validated categorical palette: adjacent slots stay distinct for common color-vision deficiencies.
const palettes = {
  dark: ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'],
  light: ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'],
};
const backendNames = {nvml: 'NVML', 'nvidia-smi': 'nvidia-smi', demo: 'Synthetic'};
const panelErrors = new Map();
const seriesSlots = new Map();
const hiddenSeries = new Set();
const gpuCards = new Map();
let lastSuccess = 0;
let collectionStale = false;
let utilRange = 'live';
let historyRequest = 0;
let lastHistory = {};
let historyDevices = {};
let lastStats = null;
let lastChart = null;
let collectorInterval = null;
let loginPeriod = 'week';
let loginData = null;
let sessionUserFilter = null;
let hideShortSessions = false;
let sessionData = null;
let sessionLimit = SESSION_PAGE;
let sessionChipsKey = '';

const byId = id => document.getElementById(id);
const finite = value => typeof value === 'number' && Number.isFinite(value);
const clamp = value => Math.max(0, Math.min(100, value));

function element(tag, className = '', text) {
  const result = document.createElement(tag);
  result.className = className;
  if (text !== undefined) result.textContent = String(text);
  return result;
}
function cell(content, className = '') {
  const result = element('td', className);
  result.append(content instanceof Node ? content : document.createTextNode(String(content)));
  return result;
}
function tableMessage(id, columns, message) {
  const row = element('tr');
  const td = cell(message, 'empty-cell');
  td.colSpan = columns;
  row.append(td);
  byId(id).replaceChildren(row);
}
function setValue(id, main, unit = '') {
  const target = byId(id);
  target.replaceChildren(document.createTextNode(main));
  if (!unit) return;
  // A leading space stays outside the no-wrap unit so a narrow tile can wrap the total onto its own line.
  if (unit.startsWith(' ')) target.append(document.createTextNode(' '));
  target.append(element('span', 'kpi-unit', unit.trimStart()));
}
function setBar(fill, percentage, warnAt = Infinity, critAt = Infinity) {
  const known = finite(percentage);
  fill.style.width = (known ? clamp(percentage) : 0) + '%';
  fill.classList.toggle('is-warn', known && percentage >= warnAt && percentage < critAt);
  fill.classList.toggle('is-crit', known && percentage >= critAt);
}
function fmtNumber(value) { return Number.isInteger(value) ? String(value) : value.toFixed(1); }
function fmtMemory(mib) {
  if (!finite(mib)) return 'Unavailable';
  return mib >= 1024 ? (mib / 1024).toFixed(1) + ' GiB' : Math.round(mib) + ' MiB';
}
function fmtDuration(minutes) {
  if (!finite(minutes)) return 'Unavailable';
  if (minutes < 1) return '<1 min';
  const rounded = Math.round(minutes);
  if (rounded < 60) return rounded + ' min';
  return Math.floor(rounded / 60) + 'h' + (rounded % 60 ? ' ' + rounded % 60 + 'm' : '');
}
function plural(count, noun) { return count + ' ' + noun + (count === 1 ? '' : 's'); }

/* Times display in the configured reporting timezone, which also defines "today". */
const configuredZone = document.body.dataset.timezone || 'UTC';
const displayZone = (() => {
  try {
    new Intl.DateTimeFormat(undefined, {timeZone: configuredZone});
    return configuredZone;
  } catch (error) {
    return 'UTC';
  }
})();
// Some names the server accepts (e.g. "localtime") are unknown to browsers; say what is actually shown.
if (displayZone !== configuredZone) {
  byId('footer-timezone').textContent = 'Times in UTC (this browser does not recognize “' + configuredZone + '”)';
}
function timeFormat(options) { return new Intl.DateTimeFormat(undefined, {timeZone: displayZone, hourCycle: 'h23', ...options}); }
const formats = {
  clock: timeFormat({hour: '2-digit', minute: '2-digit', second: '2-digit'}),
  minute: timeFormat({hour: '2-digit', minute: '2-digit'}),
  weekdayHour: timeFormat({weekday: 'short', hour: '2-digit', minute: '2-digit'}),
  dayHour: timeFormat({month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'}),
  full: timeFormat({weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'}),
  // Tooltips name the zone so the repeated hour at a DST change stays distinguishable.
  tooltip: timeFormat({weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short'}),
  tooltipClock: timeFormat({weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', timeZoneName: 'short'}),
};
function parseTime(value) {
  if (finite(value)) return new Date(value * 1000);
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T/.test(value)) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}
function fmtTime(value, format) {
  const date = parseTime(value);
  return date ? format.format(date) : String(value);
}

/* Theme: CSS owns the tokens; scripts read them for canvas drawing. */
function readInk() {
  const style = getComputedStyle(document.documentElement);
  const token = name => style.getPropertyValue(name).trim();
  return {
    scheme: token('--scheme') === 'light' ? 'light' : 'dark',
    text: token('--text'), text2: token('--text-2'), muted: token('--text-3'),
    grid: token('--grid'), surface: token('--surface'), border: token('--border-strong'),
  };
}
let ink = readInk();

// Color follows identity (GPU UUID): each new series takes the lowest slot no displayed series uses.
function seriesColor(key) {
  if (!seriesSlots.has(key)) {
    const used = new Set(seriesSlots.values());
    let slot = 0;
    while (used.has(slot)) slot++;
    seriesSlots.set(key, slot);
  }
  const palette = palettes[ink.scheme];
  return palette[seriesSlots.get(key) % palette.length];
}
// Release slots of series no longer shown, then assign new ones in priority order.
function syncSeriesSlots(keys) {
  const shown = new Set(keys);
  for (const key of [...seriesSlots.keys()]) if (!shown.has(key)) seriesSlots.delete(key);
  keys.forEach(seriesColor);
}
function datasetKey(dataset) { return dataset.id !== undefined ? dataset.id : 'index:' + dataset.gpu; }
function userColor(user) {
  let hash = 0;
  for (const char of String(user)) hash = (hash * 31 + char.codePointAt(0)) >>> 0;
  const palette = palettes[ink.scheme];
  return palette[hash % palette.length];
}
function withAlpha(hex, alpha) {
  const value = parseInt(hex.slice(1), 16);
  return 'rgba(' + (value >> 16 & 255) + ',' + (value >> 8 & 255) + ',' + (value & 255) + ',' + alpha + ')';
}
function readableInk(hex) {
  const value = parseInt(hex.slice(1), 16);
  const [r, g, b] = [value >> 16 & 255, value >> 8 & 255, value & 255].map(channel => {
    const c = channel / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  const luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return 1.05 / (luminance + 0.05) >= (luminance + 0.05) / 0.054 ? '#ffffff' : '#0b0c10';
}
function initials(user) {
  const parts = String(user).split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  if (!parts.length) return '?';
  const first = Array.from(parts[0])[0];
  return parts.length > 1 ? first + Array.from(parts[parts.length - 1])[0] : first;
}
function userChip(user) {
  const chip = element('span', 'user-chip');
  const avatar = element('span', 'avatar', initials(user));
  const color = userColor(user);
  avatar.style.backgroundColor = color;
  avatar.style.color = readableInk(color);
  avatar.setAttribute('aria-hidden', 'true');
  chip.append(avatar, element('span', 'user-chip-name', user));
  chip.title = String(user);
  return chip;
}
function gpuKey(gpu) { return gpu.uuid || 'index:' + gpu.index; }
function gpuKeys(gpus) {
  const seen = new Set();
  return gpus.map((gpu, position) => {
    let key = gpuKey(gpu);
    if (seen.has(key)) key += '#' + position;
    seen.add(key);
    return key;
  });
}

/* Status */
function showStatus() {
  const stale = collectionStale || (lastSuccess && Date.now() - lastSuccess > 15000);
  const errors = [...panelErrors.values()];
  const state = stale ? 'STALE' : errors.length ? 'PARTIAL' : lastSuccess ? (demoMode ? 'DEMO' : 'LIVE') : 'CONNECTING';
  const pill = byId('connection-status');
  if (pill.textContent !== state) {
    pill.textContent = state;
    pill.dataset.state = state.toLowerCase();
  }
  const problem = (stale ? 'Updates are delayed. Displayed values may be out of date. ' : '') + errors.join(' ');
  // The live region stays in the accessibility tree (empty and collapsed) so new problems are announced.
  const banner = byId('status-banner');
  if (banner.textContent !== problem) banner.textContent = problem;
  banner.classList.toggle('is-quiet', !problem);
}
function setError(key, message) {
  if (message) panelErrors.set(key, message);
  else panelErrors.delete(key);
  showStatus();
}

async function fetchJSON(url) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), requestTimeout);
  try {
    const response = await fetch(url, {signal: controller.signal, cache: 'no-store'});
    if (!response.ok) {
      throw new Error(response.status === 401 ? 'Sign-in is required.' : 'Request failed (HTTP ' + response.status + ').');
    }
    return await response.json();
  } finally {
    clearTimeout(timeout);
  }
}

/* Utilization chart */
function createChart(id) {
  if (!byId(id)) return null;
  if (typeof Chart === 'undefined') {
    setError('charts', 'Charts could not load. Metric tables remain available.');
    return null;
  }
  if (Chart.defaults.font) Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.color = ink.muted;
  return new Chart(byId(id).getContext('2d'), {
    type: 'line',
    data: {labels: [], datasets: []},
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: {mode: 'index', intersect: false},
      plugins: {
        legend: {position: 'top', align: 'end', labels: {usePointStyle: true, pointStyle: 'circle', boxWidth: 8, boxHeight: 8, padding: 14, color: ink.text2}},
        tooltip: {
          backgroundColor: ink.surface, borderColor: ink.border, borderWidth: 1, titleColor: ink.text, bodyColor: ink.text2,
          padding: 10, cornerRadius: 10, boxPadding: 4, usePointStyle: true,
          itemSort: (a, b) => (finite(b.parsed.y) ? b.parsed.y : -1) - (finite(a.parsed.y) ? a.parsed.y : -1),
          callbacks: {
            title: items => items.length ? (lastChart && lastChart.titles[items[0].dataIndex]) || items[0].label : '',
            label: item => ' ' + item.dataset.label + ': ' + (finite(item.parsed.y) ? item.parsed.y.toFixed(1) + '%' : 'no data'),
          },
        },
      },
      scales: {
        y: {min: 0, max: 100, border: {display: false}, grid: {color: ink.grid}, ticks: {stepSize: 25, color: ink.muted, callback: value => value + '%'}},
        x: {border: {display: false}, grid: {display: false}, ticks: {color: ink.muted, maxTicksLimit: 8, maxRotation: 0, autoSkipPadding: 16}},
      },
    },
  });
}
const utilChart = createChart('util-chart');

function themeChart() {
  if (!utilChart || !utilChart.options || !utilChart.options.scales) return;
  const {plugins, scales} = utilChart.options;
  plugins.legend.labels.color = ink.text2;
  Object.assign(plugins.tooltip, {backgroundColor: ink.surface, borderColor: ink.border, titleColor: ink.text, bodyColor: ink.text2});
  scales.y.grid.color = ink.grid;
  scales.y.ticks.color = ink.muted;
  scales.x.ticks.color = ink.muted;
}
function applyChartData(labels, titles, datasets) {
  lastChart = {labels, titles, datasets};
  if (!utilChart) return;
  // Series hidden through the legend stay hidden when refreshed data replaces the datasets.
  if (typeof utilChart.isDatasetVisible === 'function') {
    utilChart.data.datasets.forEach((dataset, index) => {
      if (utilChart.isDatasetVisible(index)) hiddenSeries.delete(dataset.seriesKey);
      else hiddenSeries.add(dataset.seriesKey);
    });
  }
  utilChart.data.labels = labels;
  // Order series by display index so the legend reads GPU 0, 1, 2… regardless of UUIDs.
  const order = dataset => finite(dataset.gpu) ? dataset.gpu : Infinity;
  const sorted = [...datasets].sort((a, b) => order(a) - order(b) || String(a.id).localeCompare(String(b.id)));
  utilChart.data.datasets = sorted.map(dataset => {
    const seriesKey = datasetKey(dataset);
    const color = seriesColor(seriesKey);
    return {
      seriesKey, hidden: hiddenSeries.has(seriesKey),
      label: dataset.label || 'GPU ' + dataset.gpu, data: dataset.data, borderColor: color, backgroundColor: color,
      borderWidth: 2, borderCapStyle: 'round', borderJoinStyle: 'round', pointRadius: 0, pointHoverRadius: 4,
      pointHoverBorderWidth: 2, pointHoverBorderColor: ink.surface, tension: 0.3, spanGaps: false,
    };
  });
  if (utilChart.options && utilChart.options.plugins) utilChart.options.plugins.legend.display = datasets.length > 1;
  utilChart.update('none');
}
function renderLiveChart(history) {
  // Epoch timestamps preserve chronology across midnight and clock/timezone changes.
  const arrays = Object.values(history);
  const longest = arrays.reduce((current, values) => values.length > current.length ? values : current, []);
  const timestamps = [...new Set(arrays.flat().map(point => point.ts))];
  const ordered = timestamps.every(finite) ? timestamps.sort((a, b) => a - b) : longest.map(point => point.ts);
  const datasets = Object.keys(history).sort().map(id => {
    const samples = new Map(history[id].map(point => [point.ts, point.util]));
    const device = historyDevices[id];
    return {id, gpu: device ? device.index : id, data: ordered.map(timestamp => samples.has(timestamp) ? samples.get(timestamp) : null)};
  });
  applyChartData(ordered.map(ts => fmtTime(ts, formats.clock)), ordered.map(ts => fmtTime(ts, formats.tooltipClock)), datasets);
  byId('util-chart-title').textContent = 'Recent samples · smoothed · ' + displayZone;
  const span = ordered.length > 1 && finite(ordered[0]) ? (ordered[ordered.length - 1] - ordered[0]) / 60 : null;
  byId('util-chart-note').textContent = span !== null && collectorInterval
    ? 'Last ' + Math.max(1, Math.round(span)) + ' min · one sample every ' + fmtNumber(collectorInterval) + ' s'
    : '';
}
const rangeInfo = {
  today: {title: 'Today · 5-minute averages', format: formats.minute},
  week: {title: 'Last 7 days · hourly averages', format: formats.weekdayHour},
  month: {title: 'Last 30 days · 6-hour averages', format: formats.dayHour},
};
async function fetchHistoryChart(range) {
  const request = ++historyRequest;
  try {
    const data = await fetchJSON('/api/gpu_history?range=' + encodeURIComponent(range));
    if (request !== historyRequest || range !== utilRange) return;
    const info = rangeInfo[range];
    applyChartData(data.labels.map(label => fmtTime(label, info.format)), data.labels.map(label => fmtTime(label, formats.tooltip)), data.datasets);
    byId('util-chart-title').textContent = info.title + ' · ' + displayZone;
    byId('util-chart-note').textContent = data.points ? plural(data.points, 'data point') : 'No data yet — history builds up while the server runs.';
    setError('historyRequest', null);
  } catch (error) {
    if (request !== historyRequest || range !== utilRange) return;
    setError('historyRequest', 'Historical data could not be refreshed.');
    byId('util-chart-note').textContent = 'History unavailable. Previously displayed data may be out of date.';
  }
}
function setUtilRange(range) {
  utilRange = range;
  historyRequest++;
  document.querySelectorAll('[data-util-range]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.utilRange === range)));
  setError('historyRequest', null);
  if (range === 'live') renderLiveChart(lastHistory);
  else fetchHistoryChart(range);
}

/* GPU cards update in place so meters animate and hover/focus survive refreshes. */
function gpuState(gpu, processesKnown) {
  if (!finite(gpu.utilization)) return 'unknown';
  if (gpu.utilization > 5) return 'active';
  const memoryFree = finite(gpu.memory_used) && gpu.memory_total > 0 && gpu.memory_used / gpu.memory_total < 0.05;
  return processesKnown && !(gpu.processes || []).length && memoryFree ? 'free' : 'idle';
}
function createGPUCard() {
  const card = {root: element('article', 'gpu-card'), usersKey: null};
  const head = element('div', 'gpu-head');
  card.swatch = element('span', 'series-swatch');
  card.swatch.setAttribute('aria-hidden', 'true');
  const titles = element('div', 'min-w-0');
  card.index = element('div', 'gpu-index');
  card.name = element('div', 'gpu-name');
  titles.append(card.index, card.name);
  card.badge = element('span', 'state-badge');
  head.append(card.swatch, titles, card.badge);

  const hero = element('div', 'gpu-hero');
  const reading = element('div');
  card.util = element('div', 'gpu-util');
  reading.append(card.util, element('div', 'gpu-caption', 'utilization'));
  card.spark = element('canvas', 'sparkline');
  card.spark.setAttribute('aria-hidden', 'true');
  hero.append(reading, card.spark);

  const memory = element('div');
  const memoryHead = element('div', 'meter-head');
  card.memory = element('span', 'meter-value');
  memoryHead.append(element('span', '', 'Memory'), card.memory);
  const track = element('div', 'bar-bg');
  card.memoryFill = element('div', 'bar-fill');
  track.append(card.memoryFill);
  memory.append(memoryHead, track);

  const foot = element('div', 'gpu-foot');
  const sensors = element('div', 'sensor-list');
  card.temperature = element('span', 'sensor');
  card.power = element('span', 'sensor');
  sensors.append(card.temperature, card.power);
  card.users = element('div', 'chip-list');
  foot.append(sensors, card.users);
  card.root.append(head, hero, memory, foot);
  return card;
}
function updateGPUCard(card, gpu, key, processesKnown) {
  const state = gpuState(gpu, processesKnown);
  card.root.dataset.state = state;
  card.root.setAttribute('aria-label', 'GPU ' + gpu.index + ', ' + state);
  card.swatch.style.backgroundColor = seriesColor(key);
  card.index.textContent = 'GPU ' + gpu.index;
  card.name.textContent = gpu.name;
  card.name.title = String(gpu.name);
  card.badge.textContent = state.toUpperCase();
  card.badge.dataset.state = state;
  if (finite(gpu.utilization)) {
    card.util.replaceChildren(document.createTextNode(fmtNumber(gpu.utilization)), element('span', 'kpi-unit', '%'));
    card.util.classList.remove('is-unknown');
  } else {
    card.util.textContent = 'Unavailable';
    card.util.classList.add('is-unknown');
  }
  card.memory.textContent = fmtMemory(gpu.memory_used) + ' / ' + fmtMemory(gpu.memory_total);
  setBar(card.memoryFill, finite(gpu.memory_used) && gpu.memory_total > 0 ? gpu.memory_used / gpu.memory_total * 100 : null);
  const temperature = gpu.temperature;
  const level = !finite(temperature) ? '' : temperature >= 90 ? 'crit' : temperature >= 80 ? 'warn' : '';
  card.temperature.dataset.level = level;
  card.temperature.textContent = !finite(temperature) ? 'Temperature unavailable'
    : fmtNumber(temperature) + ' °C' + (level === 'crit' ? ' · critical' : level === 'warn' ? ' · hot' : '');
  card.power.textContent = finite(gpu.power) ? gpu.power.toFixed(0) + ' W' : 'Power unavailable';
  const users = [...new Set((gpu.processes || []).map(process => process.user))].sort();
  const usersKey = [ink.scheme, processesKnown, ...users].join('\u0000');
  if (card.usersKey !== usersKey) {
    if (users.length) card.users.replaceChildren(...users.map(userChip));
    else card.users.replaceChildren(element('span', '', processesKnown ? 'No visible processes' : 'Processes unavailable'));
    card.usersKey = usersKey;
  }
}
function drawSparkline(canvas, points, color) {
  const {width, height} = SPARKLINE;
  const ratio = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
  }
  const context = canvas.getContext('2d');
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  const values = points.map(point => point.util);
  if (values.filter(finite).length < 2) return;
  const x = index => index / (values.length - 1) * (width - 2) + 1;
  const y = value => height - 2 - clamp(value) / 100 * (height - 4);
  // Gaps stay gaps: draw each run of consecutive samples separately.
  const runs = [];
  values.forEach((value, index) => {
    if (!finite(value)) return;
    if (index && finite(values[index - 1])) runs[runs.length - 1].push(index);
    else runs.push([index]);
  });
  context.lineWidth = 1.5;
  context.lineJoin = 'round';
  context.lineCap = 'round';
  for (const run of runs) {
    context.beginPath();
    run.forEach((index, position) => position ? context.lineTo(x(index), y(values[index])) : context.moveTo(x(index), y(values[index])));
    context.strokeStyle = color;
    context.stroke();
    if (run.length < 2) continue;
    context.lineTo(x(run[run.length - 1]), height);
    context.lineTo(x(run[0]), height);
    context.closePath();
    context.fillStyle = withAlpha(color, 0.12);
    context.fill();
  }
}
function renderGPUGrid(gpus, processesKnown) {
  const grid = byId('gpu-grid');
  grid.removeAttribute('aria-busy');
  if (!gpus.length) {
    gpuCards.clear();
    grid.replaceChildren(element('p', 'empty-state', 'No GPUs detected.'));
    return;
  }
  const keys = gpuKeys(gpus);
  const seen = new Set(keys);
  const cards = gpus.map((gpu, position) => {
    const key = keys[position];
    if (!gpuCards.has(key)) gpuCards.set(key, createGPUCard());
    const card = gpuCards.get(key);
    updateGPUCard(card, gpu, key, processesKnown);
    return {card, key};
  });
  for (const key of [...gpuCards.keys()]) if (!seen.has(key)) gpuCards.delete(key);
  const current = [...grid.children];
  if (current.length !== cards.length || cards.some(({card}, index) => current[index] !== card.root)) {
    grid.replaceChildren(...cards.map(({card}) => card.root));
  }
  for (const {card, key} of cards) drawSparkline(card.spark, lastHistory[key] || [], seriesColor(key));
}
function gpuUnavailable(message) {
  gpuCards.clear();
  const grid = byId('gpu-grid');
  grid.removeAttribute('aria-busy');
  grid.replaceChildren(element('p', 'empty-state', message));
}

/* Tables */
function renderProcTable(processes) {
  if (!processes.length) return tableMessage('proc-tbody', 5, 'No GPU processes right now.');
  const order = process => process.gpu === null ? Infinity : process.gpu;
  const sorted = [...processes].sort((a, b) => order(a) - order(b) || (b.mem_mb || 0) - (a.mem_mb || 0));
  byId('proc-tbody').replaceChildren(...sorted.map(process => {
    const row = element('tr');
    const gpu = element('span', 'gpu-ref');
    if (process.gpu !== null) {
      const swatch = element('span', 'series-swatch');
      swatch.style.backgroundColor = seriesColor(process.gpu_uuid || 'index:' + process.gpu);
      gpu.append(swatch);
    }
    gpu.append(document.createTextNode(process.gpu === null ? 'Unmapped' : 'GPU ' + process.gpu));
    const command = cell(process.command, 'mono truncate-cell');
    command.title = String(process.command);
    row.append(cell(userChip(process.user)), cell(gpu), cell(finite(process.pid) ? process.pid : '—', 'num mono'), cell(fmtMemory(process.mem_mb), 'num'), command);
    return row;
  }));
}
function renderUserGPUTable(users, gpus) {
  const names = Object.keys(users).sort((a, b) => (users[b].mem_gb || 0) - (users[a].mem_gb || 0) || a.localeCompare(b));
  if (!names.length) return tableMessage('user-gpu-tbody', 4, 'Nobody is using a GPU right now.');
  const totalGiB = gpus.reduce((sum, gpu) => sum + (finite(gpu.memory_total) ? gpu.memory_total / 1024 : 0), 0);
  byId('user-gpu-tbody').replaceChildren(...names.map(name => {
    const info = users[name];
    const row = element('tr');
    const memory = element('span', 'share');
    if (finite(info.mem_gb) && totalGiB > 0) {
      const track = element('span', 'bar-bg');
      const fill = element('span', 'bar-fill');
      fill.style.backgroundColor = userColor(name);
      setBar(fill, info.mem_gb / totalGiB * 100);
      track.append(fill);
      track.title = (info.mem_gb / totalGiB * 100).toFixed(1) + '% of GPU memory';
      memory.append(track);
    }
    memory.append(element('span', 'share-value', finite(info.mem_gb) ? info.mem_gb.toFixed(1) + ' GiB' : 'Unavailable'));
    row.append(cell(userChip(name)), cell(info.gpu_indices.join(', ') || 'Unmapped'), cell(info.proc_count, 'num'), cell(memory, 'num'));
    return row;
  }));
}
function renderConnections(connections) {
  if (!connections.length) return tableMessage('conn-tbody', 5, 'No visible SSH connections.');
  byId('conn-tbody').replaceChildren(...connections.map(connection => {
    const row = element('tr');
    row.append(cell(userChip(connection.user)), cell(connection.type), cell(fmtTime(connection.since, formats.full)), cell(fmtDuration(connection.duration_min), 'num'), cell(connection.tty, 'mono'));
    return row;
  }));
}

/* Host summary */
function renderSummary(data, gpusOk, processesOk) {
  // Host-wide figures are shown only when every GPU reports the input; a partial sum would read as a total.
  const gpus = gpusOk ? data.gpus : [];
  const complete = gpus.every(gpu => finite(gpu.utilization));
  const average = gpus.length && complete ? gpus.reduce((sum, gpu) => sum + gpu.utilization, 0) / gpus.length : null;
  if (finite(average)) setValue('sys-gpu-util', fmtNumber(Math.round(average * 10) / 10), '%');
  else setValue('sys-gpu-util', '—');
  setBar(byId('sys-gpu-util-bar'), average);

  const memoryComplete = gpus.length > 0 && gpus.every(gpu => finite(gpu.memory_used) && gpu.memory_total > 0);
  const used = memoryComplete ? gpus.reduce((sum, gpu) => sum + gpu.memory_used, 0) / 1024 : null;
  const total = memoryComplete ? gpus.reduce((sum, gpu) => sum + gpu.memory_total, 0) / 1024 : null;
  if (memoryComplete) setValue('sys-vram', used.toFixed(1), ' / ' + fmtNumber(Math.round(total * 10) / 10) + ' GiB');
  else setValue('sys-vram', '—');
  setBar(byId('sys-vram-bar'), memoryComplete ? used / total * 100 : null);

  const states = gpus.map(gpu => gpuState(gpu, processesOk));
  const active = states.filter(state => state === 'active').length;
  const free = states.filter(state => state === 'free').length;
  // FREE needs a known utilization and process list for every GPU; otherwise the count is unknown.
  const freeKnown = gpus.length > 0 && complete && processesOk;
  if (gpusOk) setValue('sys-active-gpus', complete ? String(active) : '—', ' / ' + data.gpus.length);
  else setValue('sys-active-gpus', '—');
  byId('sys-free-gpus').textContent = !gpus.length ? ' ' : !freeKnown ? 'Free count unknown' : free ? free + ' free' : 'None free';
  byId('gpu-summary').textContent = gpus.length
    ? [plural(gpus.length, 'GPU'), complete ? active + ' active' : 'activity unknown', freeKnown ? free + ' free' : 'free count unknown'].join(' · ')
    : '';
  document.title = (freeKnown ? free + '/' + gpus.length + ' free · ' : '') + 'GPU Roster' + (demoMode ? ' · Demo' : '');

  setValue('sys-procs', processesOk ? String(data.processes.length) : '—');
  byId('sys-users').textContent = processesOk ? plural(Object.keys(data.user_gpu).length, 'user') : ' ';

  const system = data.system;
  if (system && finite(system.cpu_percent)) setValue('sys-cpu', fmtNumber(system.cpu_percent), '%');
  else setValue('sys-cpu', '—');
  setBar(byId('sys-cpu-bar'), system ? system.cpu_percent : null, 90, 98);
  if (system) setValue('sys-ram', String(system.ram_used_gb), ' / ' + system.ram_total_gb + ' GB');
  else setValue('sys-ram', '—');
  const ramPercent = !system ? null : finite(system.ram_percent) ? system.ram_percent : system.ram_used_gb / system.ram_total_gb * 100;
  setBar(byId('sys-ram-bar'), ramPercent, 90, 97);
}

function renderStats(data) {
  const sources = data.health.sources;
  collectionStale = ['gpus', 'processes', 'system'].some(source => sources[source].status === 'stale');
  const labels = {gpus: 'GPU metrics', processes: 'Process metrics', system: 'System metrics', connections: 'SSH connections', sessions: 'Session records', history: 'History collection'};
  for (const [source, label] of Object.entries(labels)) {
    if (!sources[source]) continue;
    const state = sources[source].status;
    // A first CPU sample needs a baseline; that is expected, not a failure.
    setError(source, ['ok', 'disabled', 'warming_up'].includes(state) ? null : state === 'partial' ? label + ' are incomplete.' : label + ' unavailable or delayed.');
  }
  const gpusOk = sources.gpus.status === 'ok';
  const processesOk = sources.processes.status === 'ok';
  lastHistory = data.history || {};
  historyDevices = data.history_devices || {};
  if (data.collector && finite(data.collector.interval_seconds)) collectorInterval = data.collector.interval_seconds;
  // Keep series colors for what is displayed: current GPUs first (by index), then other charted series.
  if (gpusOk) {
    const keys = gpuKeys(data.gpus);
    const byIndex = data.gpus.map((gpu, position) => [gpu.index, keys[position]]).sort((a, b) => a[0] - b[0]).map(([, key]) => key);
    syncSeriesSlots([...byIndex, ...Object.keys(lastHistory).sort(), ...(lastChart ? lastChart.datasets.map(datasetKey) : [])]);
  }
  if (gpusOk) renderGPUGrid(data.gpus, processesOk);
  else gpuUnavailable('GPU metrics unavailable.');
  if (processesOk) {
    renderProcTable(data.processes);
    renderUserGPUTable(data.user_gpu, data.gpus);
  } else {
    tableMessage('proc-tbody', 5, 'Process metrics unavailable.');
    tableMessage('user-gpu-tbody', 4, 'User GPU metrics unavailable.');
  }
  renderSummary(data, gpusOk, processesOk);
  if (sessionsEnabled) {
    if (sources.connections.status === 'ok') renderConnections(data.connections);
    else tableMessage('conn-tbody', 5, 'Connection details unavailable.');
  }
  if (utilRange === 'live') renderLiveChart(lastHistory);
  if (data.collector) {
    const backend = backendNames[data.collector.backend] || data.collector.backend;
    byId('footer-collector').textContent = backend + ' collector · every ' + fmtNumber(data.collector.interval_seconds) + ' s';
  }
}

async function fetchStats() {
  try {
    const data = await fetchJSON('/api/stats');
    renderStats(data);
    lastStats = data;
    lastSuccess = Date.now();
    byId('last-update').textContent = fmtTime(data.timestamp, formats.clock);
    setError('statsRequest', null);
  } catch (error) {
    setError('statsRequest', 'Metrics could not be refreshed. ' + (error.message === 'Sign-in is required.' ? error.message : 'Previously displayed values may be out of date.'));
  }
}

/* Optional session panels */
function renderLoginStats() {
  const container = byId('login-bars');
  if (!container || !loginData) return;
  const key = loginPeriod + '_h';
  const hours = user => loginData[user][key] || 0;
  const users = Object.keys(loginData).sort((a, b) => hours(b) - hours(a) || a.localeCompare(b));
  if (!users.length) return container.replaceChildren(element('p', 'empty-state', 'No login records available.'));
  const maximum = Math.max(1, ...users.map(hours));
  container.replaceChildren(...users.map(user => {
    const row = element('div', 'usage-row');
    const track = element('div', 'bar-bg');
    const fill = element('div', 'bar-fill');
    fill.style.backgroundColor = userColor(user);
    setBar(fill, hours(user) / maximum * 100);
    track.append(fill);
    const totals = loginData[user];
    row.title = ['Today ' + (totals.today_h || 0).toFixed(1) + ' h', '7 days ' + (totals.week_h || 0).toFixed(1) + ' h', '30 days ' + (totals.month_h || 0).toFixed(1) + ' h'].join(' · ');
    row.append(userChip(user), track, element('span', 'usage-value', hours(user).toFixed(1) + ' h'));
    return row;
  }));
}
function renderSessionRows() {
  if (!sessionData) return;
  const visible = sessionData.filter(session => (sessionUserFilter === null || session.user === sessionUserFilter) && (!hideShortSessions || session.duration_min >= 1));
  const shown = visible.slice(0, sessionLimit);
  byId('session-count').textContent = shown.length === visible.length
    ? 'Showing ' + plural(visible.length, 'session')
    : 'Showing ' + shown.length + ' of ' + visible.length + ' sessions';
  const more = byId('session-more');
  more.hidden = shown.length === visible.length;
  more.textContent = 'Show ' + Math.min(SESSION_PAGE, visible.length - shown.length) + ' more';
  if (!visible.length) return tableMessage('session-tbody', 6, 'No matching sessions.');
  byId('session-tbody').replaceChildren(...shown.map(session => {
    const row = element('tr', session.still_in ? 'row-live' : '');
    const logout = session.still_in ? element('span', 'live-tag', 'Active') : fmtTime(session.logout, formats.full);
    row.append(cell(userChip(session.user)), cell(fmtTime(session.login, formats.full)), cell(logout), cell(fmtDuration(session.duration_min), 'num'), cell(session.terminal, 'mono'), cell(session.host, 'muted'));
    return row;
  }));
}
function renderSessionTable(sessions) {
  sessionData = sessions;
  const users = [...new Set(sessions.map(session => session.user))].sort();
  if (sessionUserFilter !== null && !users.includes(sessionUserFilter)) sessionUserFilter = null;
  // Rebuild filters only when they change, so keyboard focus survives refreshes.
  const chipsKey = [ink.scheme, ...users].join('\u0000');
  if (chipsKey !== sessionChipsKey) {
    sessionChipsKey = chipsKey;
    byId('session-filter-pills').replaceChildren(...[null, ...users].map(user => {
      const button = element('button', 'filter-chip');
      button.type = 'button';
      button.setAttribute('aria-pressed', String(user === sessionUserFilter));
      if (user === null) button.textContent = 'All users';
      else button.append(...userChip(user).childNodes);
      button.addEventListener('click', () => {
        sessionUserFilter = user;
        sessionLimit = SESSION_PAGE;
        byId('session-filter-pills').querySelectorAll('.filter-chip').forEach(chip => chip.setAttribute('aria-pressed', String(chip === button)));
        renderSessionRows();
      });
      return button;
    }));
  }
  renderSessionRows();
}
async function fetchLoginStats() {
  try {
    loginData = await fetchJSON('/api/login_stats');
    renderLoginStats();
    setError('loginRequest', null);
  } catch (error) { setError('loginRequest', 'Login estimates could not be refreshed.'); }
}
async function fetchSessions() {
  try {
    renderSessionTable(await fetchJSON('/api/sessions'));
    setError('sessionsRequest', null);
  } catch (error) { setError('sessionsRequest', 'Session details could not be refreshed.'); }
}
async function poll(action, interval) {
  await action();
  setTimeout(() => poll(action, interval), interval);
}

/* Theme switching */
function applyTheme() {
  ink = readInk();
  const next = ink.scheme === 'dark' ? 'light' : 'dark';
  byId('theme-toggle').setAttribute('aria-label', 'Switch to ' + next + ' theme');
  byId('theme-toggle').title = 'Switch to ' + next + ' theme';
  if (typeof Chart !== 'undefined') Chart.defaults.color = ink.muted;
  themeChart();
  if (lastChart) applyChartData(lastChart.labels, lastChart.titles, lastChart.datasets);
  if (lastStats) renderStats(lastStats);
  if (sessionsEnabled) {
    renderLoginStats();
    if (sessionData) renderSessionTable(sessionData);
  }
}
byId('theme-toggle').addEventListener('click', () => {
  const next = ink.scheme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem('gpuroster-theme', next); } catch (error) { /* The choice lasts for this page only. */ }
  applyTheme();
});
window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
  if (!document.documentElement.dataset.theme) applyTheme();
});

document.querySelectorAll('[data-util-range]').forEach(button => button.addEventListener('click', () => setUtilRange(button.dataset.utilRange)));
document.querySelectorAll('[data-login-period]').forEach(button => button.addEventListener('click', () => {
  loginPeriod = button.dataset.loginPeriod;
  document.querySelectorAll('[data-login-period]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
  renderLoginStats();
}));
if (sessionsEnabled) {
  byId('btn-short').addEventListener('click', () => {
    hideShortSessions = !hideShortSessions;
    sessionLimit = SESSION_PAGE;
    byId('btn-short').setAttribute('aria-pressed', String(hideShortSessions));
    renderSessionRows();
  });
  byId('session-more').addEventListener('click', () => {
    const more = byId('session-more');
    const focused = document.activeElement === more;
    sessionLimit += SESSION_PAGE;
    renderSessionRows();
    // The button hides after the last page; keep keyboard focus nearby instead of dropping it to <body>.
    if (focused && more.hidden) byId('session-count').focus();
  });
  poll(fetchLoginStats, 60000);
  poll(fetchSessions, 60000);
}
applyTheme();
poll(fetchStats, 5000);
poll(async () => { if (utilRange !== 'live') await fetchHistoryChart(utilRange); }, 60000);
setInterval(showStatus, 1000);
