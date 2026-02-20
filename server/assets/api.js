// File commentary: server/assets/api.js - This file contains browser-side behavior for the project.
// server/assets/api.js

// Centralized API route constants used by the browser UI.
// Keeping these in one place avoids scattered "magic strings" and makes refactors safer.
export const STATUS_URL = '/status';
export const HISTORY_URL = '/history';

// UI endpoints (HTML / UI helper pages).
export const UI_URL = '/ui';
export const TILE_NUMBERS_URL = '/ui/tile-numbers';
export const GRID_URL = '/ui/grid';
export const REGION_URL = '/ui/region';
export const STATE_OVERLAY_URL = '/ui/state-overlay';

// Tile mask endpoints (get current disabled tiles / update them).
// Intentionally separate constants even if the paths match, so call sites remain explicit.
export const TILES_GET_URL = '/tiles';
export const TILES_PUT_URL = '/tiles';
export const QUALITY_EVENTS_URL = '/quality/events';
export const QUALITY_CLIPS_URL = '/quality/clips';

/**
 * Fetch JSON with caching disabled.
 *
 * Why `cache: 'no-store'`:
 * - This UI is effectively a live dashboard; stale responses are more harmful than beneficial.
 * - Some browsers/proxies can cache GETs aggressively; `no-store` prevents that.
 *
 * Error handling:
 * - Throws on non-2xx so callers can handle errors consistently (toast/banner/etc).
 */
export async function fetchJson(url) {
  const res = await fetch(url, { cache: 'no-store' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

/**
 * POST JSON helper.
 *
 * Consistency:
 * - Forces application/json content type.
 * - Disables caching (useful if any intermediaries are involved).
 *
 * Note:
 * - This does not add retries or timeouts; keep callers deterministic and let the UI decide.
 */
export async function postJson(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    cache: 'no-store',
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

/**
 * PUT JSON helper.
 *
 * Intended for idempotent updates (e.g., updating the disabled tile set).
 * Mirrors `postJson` to keep call sites uniform.
 */
export async function putJson(url, body) {
  const res = await fetch(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    cache: 'no-store',
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}
