/* Host-provided values are always text nodes or DOM properties, never HTML. */
const colors = ['#6366f1', '#8b5cf6', '#ec4899', '#f43f5e', '#f97316', '#eab308', '#10b981', '#06b6d4'];
const sessionsEnabled = document.body.dataset.sessionsEnabled === 'true';
const demoMode = document.body.dataset.demo === 'true';
const requestTimeout = Number(document.body.dataset.requestTimeoutMs) || 14000;
const panelErrors = new Map();
let lastSuccess = 0;
let collectionStale = false;
let utilRange = 'live';
let historyRequest = 0;
let lastHistory = {};
let historyDevices = {};
let loginPeriod = 'week';
let loginData = {};
let sessionUserFilter = null;
let hideShortSessions = false;
let sessionData = [];

const byId = id => document.getElementById(id);
const finite = value => typeof value === 'number' && Number.isFinite(value);

function element(tag, className = '', text) {
  const result = document.createElement(tag);
  result.className = className;
  if (text !== undefined) result.textContent = String(text);
  return result;
}

function pill(text) { return element('span', 'pill pill-user', text); }
function cell(text, className = '') {
  const result = element('td', className);
  result.append(text instanceof Node ? text : document.createTextNode(String(text)));
  return result;
}
function tableMessage(id, columns, message) {
  const row = element('tr');
  const td = cell(message, 'text-center text-slate-500 py-6');
  td.colSpan = columns;
  row.append(td);
  byId(id).replaceChildren(row);
}
function fmtMemory(mib) {
  if (!finite(mib)) return 'Unavailable';
  return mib >= 1024 ? (mib / 1024).toFixed(1) + ' GiB' : mib + ' MiB';
}
function fmtDuration(minutes) {
  if (!finite(minutes)) return 'Unavailable';
  if (minutes < 1) return '<1 min';
  const rounded = Math.round(minutes);
  return rounded < 60 ? rounded + ' min' : Math.floor(rounded / 60) + 'h ' + rounded % 60 + 'm';
}
function userColor(user) {
  let hash = 0;
  for (const char of String(user)) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return colors[hash % colors.length];
}

function showStatus() {
  const stale = collectionStale || (lastSuccess && Date.now() - lastSuccess > 15000);
  const errors = [...panelErrors.values()];
  byId('connection-status').textContent = stale ? 'STALE' : errors.length ? 'PARTIAL' : lastSuccess ? (demoMode ? 'DEMO' : 'LIVE') : 'CONNECTING';
  const message = stale ? 'Updates are delayed. Displayed values may be out of date. ' : '';
  byId('status-banner').textContent = message + (errors.join(' ') || (lastSuccess ? 'Metrics are updating.' : 'Waiting for the first update…'));
  byId('status-banner').classList.toggle('text-amber-300', Boolean(stale || errors.length));
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

function createChart(id, type) {
  if (!byId(id)) return null;
  if (typeof Chart === 'undefined') {
    setError('charts', 'Charts could not load. Metric tables remain available.');
    return null;
  }
  Chart.defaults.color = '#94a3b8';
  Chart.defaults.borderColor = '#1e2138';
  return new Chart(byId(id).getContext('2d'), {
    type,
    data: {labels: [], datasets: []},
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: {mode: 'index', intersect: false},
      plugins: {legend: {position: 'top', labels: {boxWidth: 12}}},
      scales: {y: {min: 0, ...(type === 'line' ? {max: 100} : {})}, x: {ticks: {maxTicksLimit: 12}}}
    }
  });
}
const utilChart = createChart('util-chart', 'line');
const loginChart = createChart('login-chart', 'bar');

function progressBar(label, description, percentage, color) {
  const wrapper = element('div', 'mb-3');
  const heading = element('div', 'flex justify-between text-xs text-slate-400 mb-1');
  heading.append(element('span', '', label), element('span', 'font-bold text-white', description));
  const background = element('div', 'bar-bg');
  const fill = element('div', 'bar-fill ' + color);
  fill.style.width = (finite(percentage) ? Math.max(0, Math.min(100, percentage)) : 0) + '%';
  background.append(fill);
  wrapper.append(heading, background);
  return wrapper;
}

function renderGPUGrid(gpus) {
  const cards = gpus.map(gpu => {
    const card = element('div', 'card gpu-card p-4');
    const header = element('div', 'flex items-center justify-between mb-3');
    const name = element('div');
    name.append(element('span', 'text-xs font-bold text-slate-400', 'GPU ' + gpu.index), element('div', 'text-sm font-semibold text-white', gpu.name));
    const active = finite(gpu.utilization) && gpu.utilization > 5;
    header.append(name, element('span', 'badge ' + (active ? 'badge-active' : 'badge-idle'), !finite(gpu.utilization) ? 'UNKNOWN' : active ? 'ACTIVE' : 'IDLE'));
    const memoryPercentage = finite(gpu.memory_used) && gpu.memory_total > 0 ? gpu.memory_used / gpu.memory_total * 100 : null;
    const utilizationColor = gpu.utilization >= 80 ? 'util-high' : gpu.utilization >= 30 ? 'util-mid' : 'util-low';
    card.append(header,
      progressBar('Utilization', finite(gpu.utilization) ? gpu.utilization + '%' : 'Unavailable', gpu.utilization, utilizationColor),
      progressBar('VRAM', fmtMemory(gpu.memory_used) + ' / ' + fmtMemory(gpu.memory_total), memoryPercentage, 'mem-fill'));
    const sensors = element('div', 'flex items-center justify-between text-xs');
    sensors.append(element('span', gpu.temperature >= 70 ? 'badge badge-hot' : 'text-slate-400', finite(gpu.temperature) ? gpu.temperature + '°C' : 'Temperature unavailable'), element('span', 'text-slate-500', finite(gpu.power) ? gpu.power.toFixed(0) + ' W' : 'Power unavailable'));
    const users = element('div', 'mt-2 flex flex-wrap gap-1');
    for (const user of new Set(gpu.processes.map(process => process.user))) users.append(pill(user));
    card.append(sensors, users);
    return card;
  });
  byId('gpu-grid').replaceChildren(...(cards.length ? cards : [element('p', 'text-slate-500', 'No GPUs detected.')]));
}

function renderProcTable(processes) {
  if (!processes.length) return tableMessage('proc-tbody', 4, 'No GPU processes');
  byId('proc-tbody').replaceChildren(...processes.map(process => {
    const row = element('tr');
    row.append(cell(pill(process.user)), cell(process.gpu === null ? 'Unmapped GPU' : 'GPU ' + process.gpu, 'font-mono text-indigo-300'), cell(fmtMemory(process.mem_mb), 'text-right text-purple-300'), cell(process.command, 'font-mono text-xs text-slate-400 max-w-xs truncate'));
    return row;
  }));
}

function renderUserGPUTable(users) {
  const names = Object.keys(users).sort();
  if (!names.length) return tableMessage('user-gpu-tbody', 5, 'No active GPU users');
  byId('user-gpu-tbody').replaceChildren(...names.map(name => {
    const info = users[name];
    const row = element('tr');
    row.append(cell(pill(name)), cell(info.gpu_indices.join(', ') || 'Unmapped'), cell(finite(info.mem_gb) ? info.mem_gb + ' GiB' : 'Unavailable', 'text-right text-purple-300'), cell(info.proc_count, 'text-right'), cell('Active', 'text-center text-emerald-400'));
    return row;
  }));
}

function renderConnections(connections) {
  if (!connections.length) return tableMessage('conn-tbody', 5, 'No visible SSH connections');
  byId('conn-tbody').replaceChildren(...connections.map(connection => {
    const row = element('tr');
    row.append(cell(pill(connection.user)), cell(connection.type), cell(connection.since), cell(fmtDuration(connection.duration_min), 'text-right'), cell(connection.tty));
    return row;
  }));
}

function renderLoginStats() {
  const users = Object.keys(loginData).sort();
  const key = loginPeriod + '_h';
  const maximum = Math.max(1, ...users.map(user => loginData[user][key] || 0));
  byId('login-bars').replaceChildren(...users.map(user => {
    const hours = loginData[user][key] || 0;
    const row = element('div', 'flex items-center gap-3');
    const bar = element('div', 'flex-1 bar-bg');
    const fill = element('div', 'bar-fill');
    fill.style.width = Math.max(0, Math.min(100, hours / maximum * 100)) + '%';
    fill.style.backgroundColor = userColor(user);
    bar.append(fill);
    row.append(pill(user), bar, element('span', 'w-20 text-right text-xs', hours.toFixed(1) + ' h'));
    return row;
  }));
  if (!users.length) byId('login-bars').append(element('p', 'text-slate-500', 'No login records available.'));
  if (loginChart) {
    loginChart.data.labels = users;
    loginChart.data.datasets = ['today', 'week', 'month'].map((period, index) => ({label: ['Today', 'Last 7 days', 'Last 30 days'][index], data: users.map(user => loginData[user][period + '_h']), backgroundColor: colors[index], borderRadius: 4}));
    loginChart.update('none');
  }
}

function renderSessionRows() {
  const visible = sessionData.filter(session => (sessionUserFilter === null || session.user === sessionUserFilter) && (!hideShortSessions || session.duration_min >= 1));
  byId('session-count').textContent = 'Showing ' + visible.length + ' sessions';
  if (!visible.length) return tableMessage('session-tbody', 6, 'No matching sessions');
  byId('session-tbody').replaceChildren(...visible.map(session => {
    const row = element('tr', session.still_in ? 'session-row-live' : '');
    row.append(cell(pill(session.user)), cell(session.login), cell(session.still_in ? 'active' : session.logout), cell(fmtDuration(session.duration_min), 'text-right'), cell(session.terminal), cell(session.host));
    return row;
  }));
}
function renderSessionTable(sessions) {
  sessionData = sessions;
  const users = [...new Set(sessions.map(session => session.user))].sort();
  if (sessionUserFilter !== null && !users.includes(sessionUserFilter)) sessionUserFilter = null;
  const buttons = [null, ...users].map(user => {
    const button = element('button', 'pill pill-user' + (user === sessionUserFilter ? ' active-filter' : ''), user === null ? 'All' : user);
    button.type = 'button';
    button.addEventListener('click', () => { sessionUserFilter = user; renderSessionTable(sessionData); });
    return button;
  });
  byId('session-filter-pills').replaceChildren(...buttons);
  renderSessionRows();
}

function applyChartData(labels, datasets) {
  if (!utilChart) return;
  utilChart.data.labels = labels;
  utilChart.data.datasets = datasets.map(dataset => ({label: dataset.label || 'GPU ' + dataset.gpu, data: dataset.data, borderColor: dataset.id ? userColor(dataset.id) : colors[dataset.gpu % colors.length], backgroundColor: 'transparent', borderWidth: 1.5, pointRadius: 0, tension: 0.2, spanGaps: false}));
  utilChart.update('none');
}
function renderLiveChart(history) {
  // Epoch timestamps preserve chronology across midnight and clock/timezone changes.
  const arrays = Object.values(history);
  const longest = arrays.reduce((current, values) => values.length > current.length ? values : current, []);
  const timestamps = [...new Set(arrays.flat().map(point => point.ts))];
  const ordered = timestamps.every(finite) ? timestamps.sort((a, b) => a - b) : longest.map(point => point.ts);
  const labels = ordered.map(timestamp => finite(timestamp) ? new Date(timestamp * 1000).toISOString().slice(11, 19) : timestamp);
  const datasets = Object.keys(history).sort().map(id => {
    const samples = new Map(history[id].map(point => [point.ts, point.util]));
    const device = historyDevices[id];
    return {id, gpu: device ? device.index : id, data: ordered.map(timestamp => samples.has(timestamp) ? samples.get(timestamp) : null)};
  });
  applyChartData(labels, datasets);
  byId('util-chart-title').textContent = 'GPU Utilization — Recent Samples (UTC, EMA smoothed)';
  byId('util-chart-note').textContent = '';
}
async function fetchHistoryChart(range) {
  const request = ++historyRequest;
  try {
    const data = await fetchJSON('/api/gpu_history?range=' + encodeURIComponent(range));
    if (request !== historyRequest || range !== utilRange) return;
    const labels = data.labels.map(label => /^\d{4}-\d{2}-\d{2}T/.test(label) ? label.slice(0, 16).replace('T', ' ') + ' UTC' : label);
    applyChartData(labels, data.datasets);
    const titles = {today: 'Today (5-min avg)', week: 'Last 7 days (hourly avg)', month: 'Last 30 days (6-hour avg)'};
    byId('util-chart-title').textContent = 'GPU Utilization — ' + titles[range];
    byId('util-chart-note').textContent = data.points ? data.points + ' data points' : 'No data yet — history builds up as the server runs.';
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
  document.querySelectorAll('[data-util-range]').forEach(button => {
    button.classList.toggle('bg-indigo-900', button.dataset.utilRange === range);
    button.classList.toggle('text-indigo-200', button.dataset.utilRange === range);
  });
  setError('historyRequest', null);
  if (range === 'live') renderLiveChart(lastHistory);
  else fetchHistoryChart(range);
}

async function fetchStats() {
  try {
    const data = await fetchJSON('/api/stats');
    const sources = data.health.sources;
    collectionStale = ['gpus', 'processes', 'system'].some(source => sources[source].status === 'stale');
    const labels = {gpus: 'GPU metrics', processes: 'Process metrics', system: 'System metrics', connections: 'SSH connections', sessions: 'Session records', history: 'History collection'};
    for (const [source, label] of Object.entries(labels)) {
      if (!sources[source]) continue;
      const state = sources[source].status;
      setError(source, ['ok', 'disabled'].includes(state) ? null : label + ' unavailable or delayed.');
    }
    if (sources.gpus.status === 'ok') renderGPUGrid(data.gpus);
    else byId('gpu-grid').replaceChildren(element('p', 'text-amber-300', 'GPU metrics unavailable.'));
    if (sources.processes.status === 'ok') {
      renderProcTable(data.processes);
      renderUserGPUTable(data.user_gpu);
    } else {
      tableMessage('proc-tbody', 4, 'Process metrics unavailable.');
      tableMessage('user-gpu-tbody', 5, 'User GPU metrics unavailable.');
    }
    byId('sys-cpu').textContent = data.system && finite(data.system.cpu_percent) ? data.system.cpu_percent + '%' : '—';
    byId('sys-ram').textContent = data.system ? data.system.ram_used_gb + ' / ' + data.system.ram_total_gb + ' GB' : '—';
    const utilizationComplete = data.gpus.every(gpu => finite(gpu.utilization));
    const activeCount = utilizationComplete ? data.gpus.filter(gpu => gpu.utilization > 5).length : '—';
    byId('sys-active-gpus').textContent = sources.gpus.status === 'ok' ? activeCount + ' / ' + data.gpus.length : '—';
    byId('sys-procs').textContent = sources.processes.status === 'ok' ? data.processes.length : '—';
    if (sessionsEnabled) {
      if (sources.connections.status === 'ok') renderConnections(data.connections);
      else tableMessage('conn-tbody', 5, 'Connection details unavailable.');
    }
    lastHistory = data.history;
    historyDevices = data.history_devices || {};
    if (utilRange === 'live') renderLiveChart(lastHistory);
    lastSuccess = Date.now();
    byId('last-update').textContent = new Date(data.timestamp).toLocaleTimeString();
    setError('statsRequest', null);
  } catch (error) {
    setError('statsRequest', 'Metrics could not be refreshed. ' + (error.message === 'Sign-in is required.' ? error.message : 'Previously displayed values may be out of date.'));
  }
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

document.querySelectorAll('[data-util-range]').forEach(button => button.addEventListener('click', () => setUtilRange(button.dataset.utilRange)));
document.querySelectorAll('[data-login-period]').forEach(button => button.addEventListener('click', () => {
  loginPeriod = button.dataset.loginPeriod;
  document.querySelectorAll('[data-login-period]').forEach(item => item.classList.toggle('bg-indigo-900', item === button));
  renderLoginStats();
}));
if (sessionsEnabled) {
  byId('btn-short').addEventListener('click', () => {
    hideShortSessions = !hideShortSessions;
    byId('btn-short').classList.toggle('bg-slate-700', hideShortSessions);
    renderSessionRows();
  });
  poll(fetchLoginStats, 60000);
  poll(fetchSessions, 60000);
}
poll(fetchStats, 5000);
poll(async () => { if (utilRange !== 'live') await fetchHistoryChart(utilRange); }, 60000);
setInterval(showStatus, 1000);
