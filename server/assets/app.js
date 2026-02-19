// server/assets/app.js
import {
  STATUS_URL,
  HISTORY_URL,
  UI_URL,
  TILE_NUMBERS_URL,
  TILES_GET_URL,
  TILES_PUT_URL,
  fetchJson,
  postJson,
  putJson,
} from './api.js';
import { fmtTime } from './utils.js';
import { drawMotionChart, drawAudioChart } from './chart.js';
import { drawTilesHeatmap, getGridAndTiles, tileIndexFromCanvasClick } from './heatmap.js';

// --- DOM wiring --------------------------------------------------------------
// These elements are treated as required by the UI. If any are missing, the page will
// likely fail early, which is fine for a tightly-coupled single-page dashboard.
const jsonBox = document.getElementById('jsonBox');
const tsLabel = document.getElementById('tsLabel');
const pillVideo = document.getElementById('pillVideo');
const pillMean = document.getElementById('pillMean');
const pillOverall = document.getElementById('pillOverall');
const gridLabel = document.getElementById('gridLabel');

const copyBtn = document.getElementById('copyJson');
const quitBtn = document.getElementById('quitBtn');
const toggleTileNumbers = document.getElementById('toggleTileNumbers');

const chartCanvas = document.getElementById('chart');
const audioCanvas = document.getElementById('audioChart');
const heatCanvas = document.getElementById('tilesHeatmap');

// --- Client-side state -------------------------------------------------------
// Disabled tile mask comes from the server (/tiles) and is also embedded in /status.
let disabledTiles = new Set();

// UI toggle state mirroring: keep a local memory of the last server-known state so
// we can revert on failures.
let lastShowTileNumbers = true;

// Guard to prevent programmatic checkbox changes from re-triggering the change handler.
let suppressToggleHandler = false;

// Last raw /status payload (used for interactions that must avoid display-only JSON transforms).
let lastStatusPayload = null;

function setToggleCheckedFromServer(value) {
  // Normalize any “truthy”/“falsy” value into a strict boolean.
  const v = Boolean(value);
  lastShowTileNumbers = v;

  // Defensive: if the toggle isn't present, nothing to sync.
  if (!toggleTileNumbers) return;

  // Avoid touching DOM if already in the desired state.
  if (toggleTileNumbers.checked === v) return;

  // When we update the checkbox programmatically, suppress the 'change' listener.
  suppressToggleHandler = true;
  toggleTileNumbers.checked = v;
  suppressToggleHandler = false;
}

/**
 * Display-only transformation:
 * - Keep the backend payload intact for logic/heatmap calculations.
 * - Make the JSON viewer more readable by rendering disabled tiles as "disabled"
 *   instead of `null`.
 *
 * Note:
 * - The heatmap/logic uses the raw `payload` (not this transformed view) because
 *   it expects tiles to be numbers or nulls, not strings.
 */
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

function renderStatus(payload) {
  // Timestamp label is a convenience; the canonical timestamp remains in the JSON.
  tsLabel.textContent = fmtTime(payload.timestamp);

  // Resilient reads: payloads can be ERROR/warmup and omit parts of the structure.
  const vState = payload?.video?.state ?? '—';
  const mean = payload?.video?.motion_mean ?? 0;
  const oState = payload?.overall?.state ?? '—';

  // “Pills” are quick-glance indicators.
  pillVideo.textContent = `video: ${vState}`;
  pillMean.textContent = `motion_mean: ${Number(mean).toFixed(4)}`;
  pillOverall.textContent = `overall: ${oState}`;

  // JSON viewer: keep it human-readable (pretty printed).
  jsonBox.textContent = JSON.stringify(statusForDisplay(payload), null, 2);
  lastStatusPayload = payload;

  // Disabled tiles are duplicated in /status for convenience; keep our Set in sync.
  const d = payload?.video?.disabled_tiles;
  if (Array.isArray(d)) disabledTiles = new Set(d);

  // Single source of truth for the toggle is server-side UI state, embedded in /status.
  setToggleCheckedFromServer(payload?.ui?.show_tile_numbers);
}

async function loadTileMask() {
  // Best-effort: UI can still function with an empty mask if this fails.
  try {
    const d = await fetchJson(TILES_GET_URL);
    const raw = d?.disabled_tiles;
    if (Array.isArray(raw)) disabledTiles = new Set(raw);
  } catch {
    // ignore
  }
}

async function saveTileMask() {
  // Persist as a sorted list to keep payloads deterministic and diff-friendly.
  const list = Array.from(disabledTiles)
    .filter((n) => Number.isInteger(n))
    .sort((a, b) => a - b);

  const res = await putJson(TILES_PUT_URL, { disabled_tiles: list });

  // Server may validate/normalize; use its response as the new truth.
  const raw = res?.disabled_tiles;
  if (Array.isArray(raw)) disabledTiles = new Set(raw);
}

async function tick() {
  // Polling loop:
  // - /status drives the live values and the heatmap
  // - /history drives the chart
  // - /ui is a fallback source for UI-only state if /status fails
  let status = null;

  try {
    status = await fetchJson(STATUS_URL);
    renderStatus(status);

    // Heatmap draws the current per-tile values and overlay state (disabled tiles).
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
    const history = hist.history || [];
    drawMotionChart(chartCanvas, history);
    drawAudioChart(audioCanvas, history);
  } catch {
    // If history fails, show an empty chart rather than stale or broken visuals.
    drawMotionChart(chartCanvas, []);
    drawAudioChart(audioCanvas, []);
  }

  // If status fetch failed, keep the tile-number toggle reasonably in sync via /ui.
  if (!status) {
    try {
      const ui = await fetchJson(UI_URL);
      setToggleCheckedFromServer(ui?.show_tile_numbers);
    } catch {
      // ignore
    }
  }
}

// --- UI events --------------------------------------------------------------
copyBtn.addEventListener('click', async () => {
  // Copy what the user currently sees. This includes the display transformation
  // (disabled tiles as strings), which is intentional for readability.
  try {
    await navigator.clipboard.writeText(jsonBox.textContent || '{}');
  } catch {}
});

quitBtn.addEventListener('click', async () => {
  // Best-effort quit request; UI doesn't need to block on this.
  // (Uses raw fetch to keep the dependency surface small.)
  try {
    await fetch('/quit', { method: 'POST' });
  } catch {}
});

toggleTileNumbers.addEventListener('change', async () => {
  // If we just updated the checkbox programmatically, do not send a server request.
  if (suppressToggleHandler) return;

  const desired = Boolean(toggleTileNumbers.checked);

  // Optimistic UI:
  // - Keep the checkbox state immediately responsive.
  // - If the server rejects, revert to last server-known state.
  try {
    const res = await postJson(TILE_NUMBERS_URL, { enabled: desired });
    setToggleCheckedFromServer(res?.show_tile_numbers ?? res?.enabled);
  } catch {
    // Revert to last known state, then attempt to refresh from /ui as the ultimate truth.
    setToggleCheckedFromServer(lastShowTileNumbers);
    try {
      const ui = await fetchJson(UI_URL);
      setToggleCheckedFromServer(ui?.show_tile_numbers);
    } catch {}
  }
});

heatCanvas.addEventListener('click', async (ev) => {
  // Determine which tile was clicked, then toggle it in the disabled set.
  // Use the latest raw /status payload for hit-testing and grid dimensions.
  const payload = lastStatusPayload;

  // Extract grid and tiles in a normalized way (helper handles missing structures).
  const { tiles, rows, cols } = getGridAndTiles(payload);
  if (!rows || !cols || !Array.isArray(tiles) || tiles.length !== rows * cols) return;

  const idx = tileIndexFromCanvasClick(heatCanvas, rows, cols, ev);

  // Toggle membership.
  if (disabledTiles.has(idx)) disabledTiles.delete(idx);
  else disabledTiles.add(idx);

  // Persist and reconcile with server response. If save fails, reload from server.
  try {
    await saveTileMask();
  } catch {
    await loadTileMask();
  }
});

async function initUi() {
  // Initial UI state:
  // - Prefer /ui so the checkbox is correct even before the first /status response arrives.
  // - Default to true if /ui is unavailable (sensible, user-visible behavior).
  try {
    const ui = await fetchJson(UI_URL);
    setToggleCheckedFromServer(ui?.show_tile_numbers);
  } catch {
    setToggleCheckedFromServer(true);
  }
}

// Startup sequence:
// - init UI (toggle state)
// - load tile mask (server state)
// - run the first tick immediately (fast first paint)
// - then poll at a fixed interval
initUi();
loadTileMask();
tick();
setInterval(tick, 200);
