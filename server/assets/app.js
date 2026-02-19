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
const pillOverall = document.getElementById('pillOverall');
const gridLabel = document.getElementById('gridLabel');

const copyBtn = document.getElementById('copyJson');
const quitBtn = document.getElementById('quitBtn');
const toggleTileNumbers = document.getElementById('toggleTileNumbers');
const toggleOverlayState = document.getElementById('toggleOverlayState');
const gridRowsInput = document.getElementById('gridRows');
const gridColsInput = document.getElementById('gridCols');
const applyGridBtn = document.getElementById('applyGrid');
const regionXInput = document.getElementById('regionX');
const regionYInput = document.getElementById('regionY');
const regionWInput = document.getElementById('regionW');
const regionHInput = document.getElementById('regionH');
const applyRegionBtn = document.getElementById('applyRegion');
const nudgeUpBtn = document.getElementById('nudgeUp');
const nudgeDownBtn = document.getElementById('nudgeDown');
const nudgeLeftBtn = document.getElementById('nudgeLeft');
const nudgeRightBtn = document.getElementById('nudgeRight');

const chartCanvas = document.getElementById('chart');
const audioChartCanvas = document.getElementById('audioChart');
const heatCanvas = document.getElementById('tilesHeatmap');

let disabledTiles = new Set();
let suppressToggleHandler = false;
let suppressOverlayToggleHandler = false;
let lastShowTileNumbers = true;
let lastShowOverlayState = false;
let lastStatusPayload = null;

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
  return { ...payload, video: { ...payload.video, tiles: tiles.map((v) => (v === null ? 'disabled' : v)) } };
}

function applyUiValues(ui) {
  if (!ui || typeof ui !== 'object') return;
  if (gridRowsInput && ui.grid_rows) gridRowsInput.value = String(ui.grid_rows);
  if (gridColsInput && ui.grid_cols) gridColsInput.value = String(ui.grid_cols);
  if (regionXInput && Number.isFinite(ui.region_x)) regionXInput.value = String(ui.region_x);
  if (regionYInput && Number.isFinite(ui.region_y)) regionYInput.value = String(ui.region_y);
  if (regionWInput && Number.isFinite(ui.region_width)) regionWInput.value = String(ui.region_width);
  if (regionHInput && Number.isFinite(ui.region_height)) regionHInput.value = String(ui.region_height);
  setToggleCheckedFromServer(ui.show_tile_numbers);
  setOverlayToggleCheckedFromServer(ui.show_overlay_state);
}

function renderStatus(payload) {
  tsLabel.textContent = fmtTime(payload.timestamp);
  const vState = payload?.video?.state ?? '—';
  const mean = payload?.video?.motion_mean ?? 0;
  const oState = payload?.overall?.state ?? '—';

  pillVideo.textContent = `video: ${vState}`;
  pillMean.textContent = `motion_mean: ${Number(mean).toFixed(4)}`;
  pillOverall.textContent = `overall: ${oState}`;

  jsonBox.textContent = JSON.stringify(statusForDisplay(payload), null, 2);
  lastStatusPayload = payload;

  const d = payload?.video?.disabled_tiles;
  if (Array.isArray(d)) disabledTiles = new Set(d);
  applyUiValues(payload?.ui);
}

async function loadTileMask() {
  try {
    const d = await fetchJson(TILES_GET_URL);
    const raw = d?.disabled_tiles;
    if (Array.isArray(raw)) disabledTiles = new Set(raw);
  } catch {}
}

async function saveTileMask() {
  const list = Array.from(disabledTiles).filter(Number.isInteger).sort((a, b) => a - b);
  const res = await putJson(TILES_PUT_URL, { disabled_tiles: list });
  const raw = res?.disabled_tiles;
  if (Array.isArray(raw)) disabledTiles = new Set(raw);
}

async function pushRegionUpdate(x, y, widthOverride = null, heightOverride = null) {
  const width = widthOverride ?? Number(regionWInput?.value || lastStatusPayload?.ui?.region_width || 0);
  const height = heightOverride ?? Number(regionHInput?.value || lastStatusPayload?.ui?.region_height || 0);
  if (!Number.isInteger(x) || !Number.isInteger(y) || !Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0) return;
  const ui = await postJson(REGION_URL, { x, y, width, height });
  applyUiValues(ui);
}

async function tick() {
  let status = null;
  try {
    status = await fetchJson(STATUS_URL);
    renderStatus(status);
    drawTilesHeatmap({ canvas: heatCanvas, gridLabelEl: gridLabel, payload: status, disabledTilesSet: disabledTiles });
  } catch {}

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
      applyUiValues(ui);
    } catch {}
  }
}

copyBtn.addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(jsonBox.textContent || '{}'); } catch {}
});

quitBtn.addEventListener('click', async () => {
  try { await fetch('/quit', { method: 'POST' }); } catch {}
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
  try { await saveTileMask(); } catch { await loadTileMask(); }
});

applyGridBtn.addEventListener('click', async () => {
  const rows = Number(gridRowsInput?.value || 0);
  const cols = Number(gridColsInput?.value || 0);
  if (!Number.isInteger(rows) || !Number.isInteger(cols) || rows <= 0 || cols <= 0) return;
  try {
    const res = await postJson(GRID_URL, { rows, cols });
    applyUiValues(res);
    await loadTileMask();
  } catch {}
});

applyRegionBtn.addEventListener('click', async () => {
  const x = Number(regionXInput?.value || 0);
  const y = Number(regionYInput?.value || 0);
  const w = Number(regionWInput?.value || 0);
  const h = Number(regionHInput?.value || 0);
  try { await pushRegionUpdate(x, y, w, h); } catch {}
});

async function nudge(dx, dy) {
  const x = Number(regionXInput?.value || 0) + dx;
  const y = Number(regionYInput?.value || 0) + dy;
  await pushRegionUpdate(x, y);
}

nudgeUpBtn.addEventListener('click', async () => { try { await nudge(0, -2); } catch {} });
nudgeDownBtn.addEventListener('click', async () => { try { await nudge(0, 2); } catch {} });
nudgeLeftBtn.addEventListener('click', async () => { try { await nudge(-2, 0); } catch {} });
nudgeRightBtn.addEventListener('click', async () => { try { await nudge(2, 0); } catch {} });

async function initUi() {
  try {
    const ui = await fetchJson(UI_URL);
    applyUiValues(ui);
  } catch {
    setToggleCheckedFromServer(true);
    setOverlayToggleCheckedFromServer(false);
  }
}

initUi();
loadTileMask();
tick();
setInterval(tick, 200);
