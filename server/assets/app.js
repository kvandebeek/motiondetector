// server/assets/app.js
import {
  STATUS_URL,
  HISTORY_URL,
  UI_URL,
  TILE_NUMBERS_URL,
  GRID_URL,
  REGION_URL,
  STATE_OVERLAY_URL,
  TILES_GET_URL,
  TILES_PUT_URL,
  fetchJson,
  postJson,
  putJson,
} from './api.js';
import { fmtTime } from './utils.js';
import { drawAudioChart, drawChart } from './chart.js';
import { drawTilesHeatmap, getGridAndTiles, tileIndexFromCanvasClick } from './heatmap.js';

const jsonBox = document.getElementById('jsonBox');
const tsLabel = document.getElementById('tsLabel');
const pillVideo = document.getElementById('pillVideo');
const pillMean = document.getElementById('pillMean');
const pillAudioPeak = document.getElementById('pillAudioPeak');
const pillOverall = document.getElementById('pillOverall');
const gridLabel = document.getElementById('gridLabel');
const monitorInfo = document.getElementById('monitorInfo');

const copyBtn = document.getElementById('copyJson');
const quitBtn = document.getElementById('quitBtn');
const toggleTileNumbers = document.getElementById('toggleTileNumbers');
const toggleOverlayState = document.getElementById('toggleOverlayState');

const gridRowsInput = document.getElementById('gridRows');
const gridColsInput = document.getElementById('gridCols');
const gridRowsMinusBtn = document.getElementById('gridRowsMinus');
const gridRowsPlusBtn = document.getElementById('gridRowsPlus');
const gridColsMinusBtn = document.getElementById('gridColsMinus');
const gridColsPlusBtn = document.getElementById('gridColsPlus');

const regionXInput = document.getElementById('regionX');
const regionYInput = document.getElementById('regionY');
const regionWInput = document.getElementById('regionW');
const regionHInput = document.getElementById('regionH');
const regionXMinusBtn = document.getElementById('regionXMinus');
const regionXPlusBtn = document.getElementById('regionXPlus');
const regionYMinusBtn = document.getElementById('regionYMinus');
const regionYPlusBtn = document.getElementById('regionYPlus');
const regionWMinusBtn = document.getElementById('regionWMinus');
const regionWPlusBtn = document.getElementById('regionWPlus');
const regionHMinusBtn = document.getElementById('regionHMinus');
const regionHPlusBtn = document.getElementById('regionHPlus');

const chartCanvas = document.getElementById('chart');
const audioChartCanvas = document.getElementById('audioChart');
const heatCanvas = document.getElementById('tilesHeatmap');

let disabledTiles = new Set();
let suppressToggleHandler = false;
let suppressOverlayToggleHandler = false;
let lastShowTileNumbers = true;
let lastShowOverlayState = false;
let lastStatusPayload = null;
let numericInitialized = false;
let updatingGrid = false;
let updatingRegion = false;

function n(v, fallback = 0) {
  const x = Number(v);
  return Number.isFinite(x) ? x : fallback;
}

function toPosInt(v, fallback = 1) {
  const x = Math.round(n(v, fallback));
  return x > 0 ? x : fallback;
}

function setToggleCheckedFromServer(value) {
  const v = Boolean(value);
  lastShowTileNumbers = v;
  if (!toggleTileNumbers || toggleTileNumbers.checked === v) return;
  suppressToggleHandler = true;
  toggleTileNumbers.checked = v;
  suppressToggleHandler = false;
}

function setOverlayToggleCheckedFromServer(value) {
  const v = Boolean(value);
  lastShowOverlayState = v;
  if (!toggleOverlayState || toggleOverlayState.checked === v) return;
  suppressOverlayToggleHandler = true;
  toggleOverlayState.checked = v;
  suppressOverlayToggleHandler = false;
}

function statusForDisplay(payload) {
  const tiles = payload?.video?.tiles;
  if (!Array.isArray(tiles)) return payload;
  return {
    ...payload,
    video: {
      ...payload.video,
      tiles: tiles.map((v) => (v === null ? 'disabled' : v)),
    },
  };
}

function renderMonitorInfo(ui) {
  if (!monitorInfo) return;
  const monitors = Array.isArray(ui?.monitors) ? ui.monitors : [];
  const currentId = Number(ui?.current_monitor_id ?? 0);
  if (!monitors.length) {
    monitorInfo.textContent = 'monitor: unavailable';
    return;
  }

  const current = monitors.find((m) => Number(m?.id) === currentId) || monitors[0];
  const id = Number(current?.id ?? 0);
  const left = Number(current?.left ?? 0);
  const top = Number(current?.top ?? 0);
  const width = Number(current?.width ?? 0);
  const height = Number(current?.height ?? 0);
  monitorInfo.textContent = `monitor: ${id} (${left},${top}) ${width}x${height}`;
}

function applyUiValues(ui, { initOnly = false } = {}) {
  if (!ui || typeof ui !== 'object') return;

  if (!numericInitialized || !initOnly) {
    // Only initialize numeric controls once. Keep user-entered values afterward.
    if (!numericInitialized) {
      if (gridRowsInput) gridRowsInput.value = String(toPosInt(ui.grid_rows, 1));
      if (gridColsInput) gridColsInput.value = String(toPosInt(ui.grid_cols, 1));
      if (regionXInput) regionXInput.value = String(Math.round(n(ui.region_x, 0)));
      if (regionYInput) regionYInput.value = String(Math.round(n(ui.region_y, 0)));
      if (regionWInput) regionWInput.value = String(toPosInt(ui.region_width, 1));
      if (regionHInput) regionHInput.value = String(toPosInt(ui.region_height, 1));
      numericInitialized = true;
    }
  }

  setToggleCheckedFromServer(ui.show_tile_numbers);
  setOverlayToggleCheckedFromServer(ui.show_overlay_state);
  renderMonitorInfo(ui);
}



function setVideoStateTone(videoState, overallState) {
  if (!pillVideo) return;
  pillVideo.classList.remove('pill-state-ok', 'pill-state-warn', 'pill-state-alert');

  const v = String(videoState || '').toUpperCase();
  const o = String(overallState || '').toUpperCase();

  if (v.includes('NO_MOTION') || v.includes('NO_AUDIO')) {
    pillVideo.classList.add('pill-state-alert');
    return;
  }
  if (v.includes('LOW_ACTIVITY')) {
    pillVideo.classList.add('pill-state-warn');
    return;
  }
  if (o === 'OK' || v.includes('MOTION_WITH_AUDIO') || v.includes('MOTION')) {
    pillVideo.classList.add('pill-state-ok');
  }
}

function renderStatus(payload) {
  tsLabel.textContent = fmtTime(payload.timestamp);

  const vState = payload?.video?.state ?? '—';
  const mean = n(payload?.video?.motion_mean, 0);
  const oState = payload?.overall?.state ?? '—';
  const audioPeak = Math.max(n(payload?.audio?.left, 0), n(payload?.audio?.right, 0));

  pillVideo.textContent = `video: ${vState}`;
  setVideoStateTone(vState, oState);
  pillMean.textContent = `motion_mean: ${mean.toFixed(4)}`;
  pillAudioPeak.textContent = `audio peak: ${audioPeak.toFixed(1)}`;
  pillOverall.textContent = `overall: ${oState}`;

  jsonBox.textContent = JSON.stringify(statusForDisplay(payload), null, 2);
  lastStatusPayload = payload;

  const d = payload?.video?.disabled_tiles;
  if (Array.isArray(d)) disabledTiles = new Set(d);

  applyUiValues(payload?.ui, { initOnly: true });
}

async function loadTileMask() {
  try {
    const d = await fetchJson(TILES_GET_URL);
    const raw = d?.disabled_tiles;
    if (Array.isArray(raw)) disabledTiles = new Set(raw);
  } catch {}
}

async function saveTileMask() {
  const list = Array.from(disabledTiles)
    .filter(Number.isInteger)
    .sort((a, b) => a - b);

  const res = await putJson(TILES_PUT_URL, { disabled_tiles: list });
  const raw = res?.disabled_tiles;
  if (Array.isArray(raw)) disabledTiles = new Set(raw);
}

async function submitGrid() {
  if (updatingGrid) return;
  const rows = toPosInt(gridRowsInput?.value, 1);
  const cols = toPosInt(gridColsInput?.value, 1);
  if (!gridRowsInput || !gridColsInput) return;
  gridRowsInput.value = String(rows);
  gridColsInput.value = String(cols);

  updatingGrid = true;
  try {
    const res = await postJson(GRID_URL, { rows, cols });
    if (res?.grid_rows) gridRowsInput.value = String(toPosInt(res.grid_rows, rows));
    if (res?.grid_cols) gridColsInput.value = String(toPosInt(res.grid_cols, cols));
    await loadTileMask();
  } catch {
    // Keep user values visible; no forced rollback.
  } finally {
    updatingGrid = false;
  }
}

async function submitRegion() {
  if (updatingRegion) return;
  const x = Math.round(n(regionXInput?.value, 0));
  const y = Math.round(n(regionYInput?.value, 0));
  const width = toPosInt(regionWInput?.value, 1);
  const height = toPosInt(regionHInput?.value, 1);
  if (!regionXInput || !regionYInput || !regionWInput || !regionHInput) return;

  regionXInput.value = String(x);
  regionYInput.value = String(y);
  regionWInput.value = String(width);
  regionHInput.value = String(height);

  updatingRegion = true;
  try {
    const ui = await postJson(REGION_URL, { x, y, width, height });
    if (Number.isFinite(ui?.region_x)) regionXInput.value = String(Math.round(Number(ui.region_x)));
    if (Number.isFinite(ui?.region_y)) regionYInput.value = String(Math.round(Number(ui.region_y)));
    if (Number.isFinite(ui?.region_width)) regionWInput.value = String(toPosInt(ui.region_width, width));
    if (Number.isFinite(ui?.region_height)) regionHInput.value = String(toPosInt(ui.region_height, height));
    renderMonitorInfo(ui);
  } catch {
    // Keep user values visible; no forced rollback.
  } finally {
    updatingRegion = false;
  }
}

function adjustNumberInput(input, delta, min = null) {
  if (!input) return;
  const next = Math.round(n(input.value, 0) + delta);
  input.value = String(min === null ? next : Math.max(min, next));
}

async function tick() {
  let status = null;

  try {
    status = await fetchJson(STATUS_URL);
    renderStatus(status);

    drawTilesHeatmap({
      canvas: heatCanvas,
      gridLabelEl: gridLabel,
      payload: status,
      disabledTilesSet: disabledTiles,
    });
  } catch {
    // keep last render
  }

  try {
    const hist = await fetchJson(HISTORY_URL);
    drawChart(chartCanvas, hist.history || []);
    drawAudioChart(audioChartCanvas, hist.history || []);
  } catch {
    drawChart(chartCanvas, []);
    drawAudioChart(audioChartCanvas, []);
  }

  if (!status) {
    try {
      const ui = await fetchJson(UI_URL);
      applyUiValues(ui, { initOnly: true });
    } catch {}
  }
}

copyBtn.addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText(jsonBox.textContent || '{}');
  } catch {}
});

quitBtn.addEventListener('click', async () => {
  try {
    await fetch('/quit', { method: 'POST' });
  } catch {}
});

toggleTileNumbers.addEventListener('change', async () => {
  if (suppressToggleHandler) return;
  const desired = Boolean(toggleTileNumbers.checked);
  try {
    const res = await postJson(TILE_NUMBERS_URL, { enabled: desired });
    setToggleCheckedFromServer(res?.show_tile_numbers ?? res?.enabled);
  } catch {
    setToggleCheckedFromServer(lastShowTileNumbers);
  }
});

toggleOverlayState.addEventListener('change', async () => {
  if (suppressOverlayToggleHandler) return;
  const desired = Boolean(toggleOverlayState.checked);
  try {
    const res = await postJson(STATE_OVERLAY_URL, { enabled: desired });
    setOverlayToggleCheckedFromServer(res?.show_overlay_state);
  } catch {
    setOverlayToggleCheckedFromServer(lastShowOverlayState);
  }
});

heatCanvas.addEventListener('click', async (ev) => {
  const payload = lastStatusPayload;
  const { tiles, rows, cols } = getGridAndTiles(payload);
  if (!rows || !cols || !Array.isArray(tiles) || tiles.length !== rows * cols) return;

  const idx = tileIndexFromCanvasClick(heatCanvas, rows, cols, ev);
  if (disabledTiles.has(idx)) disabledTiles.delete(idx);
  else disabledTiles.add(idx);

  try {
    await saveTileMask();
  } catch {
    await loadTileMask();
  }
});

// Immediate grid updates.
gridRowsInput?.addEventListener('change', () => { void submitGrid(); });
gridColsInput?.addEventListener('change', () => { void submitGrid(); });
gridRowsMinusBtn?.addEventListener('click', () => { adjustNumberInput(gridRowsInput, -1, 1); void submitGrid(); });
gridRowsPlusBtn?.addEventListener('click', () => { adjustNumberInput(gridRowsInput, +1, 1); void submitGrid(); });
gridColsMinusBtn?.addEventListener('click', () => { adjustNumberInput(gridColsInput, -1, 1); void submitGrid(); });
gridColsPlusBtn?.addEventListener('click', () => { adjustNumberInput(gridColsInput, +1, 1); void submitGrid(); });

// Immediate region updates.
regionXInput?.addEventListener('change', () => { void submitRegion(); });
regionYInput?.addEventListener('change', () => { void submitRegion(); });
regionWInput?.addEventListener('change', () => { void submitRegion(); });
regionHInput?.addEventListener('change', () => { void submitRegion(); });
regionXMinusBtn?.addEventListener('click', () => { adjustNumberInput(regionXInput, -2, null); void submitRegion(); });
regionXPlusBtn?.addEventListener('click', () => { adjustNumberInput(regionXInput, +2, null); void submitRegion(); });
regionYMinusBtn?.addEventListener('click', () => { adjustNumberInput(regionYInput, -2, null); void submitRegion(); });
regionYPlusBtn?.addEventListener('click', () => { adjustNumberInput(regionYInput, +2, null); void submitRegion(); });
regionWMinusBtn?.addEventListener('click', () => { adjustNumberInput(regionWInput, -2, 1); void submitRegion(); });
regionWPlusBtn?.addEventListener('click', () => { adjustNumberInput(regionWInput, +2, 1); void submitRegion(); });
regionHMinusBtn?.addEventListener('click', () => { adjustNumberInput(regionHInput, -2, 1); void submitRegion(); });
regionHPlusBtn?.addEventListener('click', () => { adjustNumberInput(regionHInput, +2, 1); void submitRegion(); });

async function initUi() {
  try {
    const ui = await fetchJson(UI_URL);
    applyUiValues(ui, { initOnly: true });
  } catch {
    setToggleCheckedFromServer(true);
    setOverlayToggleCheckedFromServer(false);
  }
}

initUi();
loadTileMask();
tick();
setInterval(tick, 200);
