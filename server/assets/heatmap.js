// server/assets/heatmap.js
import { clamp01, colorFor01, edges, inferSquareGridFromTiles } from './utils.js';

/**
 * Size the heatmap canvas to match the monitored region aspect ratio while fitting the UI.
 *
 * Strategy:
 * - Use the parent container width as the desired CSS width.
 * - Derive height from the region aspect ratio, capped by a max height to avoid huge canvases.
 * - Set both CSS sizing (via style.aspectRatio) and backing store sizing (canvas.width/height)
 *   at device pixel ratio for crisp rendering.
 *
 * Inputs:
 * - region is expected to contain { width, height } in pixels (capture space).
 */
export function resizeHeatmapCanvas(canvas, region) {
  const wrap = canvas.parentElement;
  if (!wrap) return;

  const rw = Number(region?.width) || 0;
  const rh = Number(region?.height) || 0;
  if (!rw || !rh) return;

  // Keep the heatmap from taking over the page.
  const maxH = 540;

  // Fit to container width; fall back to a reasonable default.
  const wrapW = wrap.clientWidth || 540;

  const aspect = rw / rh;
  const desiredW = wrapW;
  const desiredH = Math.min(maxH, Math.round(desiredW / aspect));

  // Keep CSS size explicit so backing-store updates do not influence layout width.
  canvas.style.width = '100%';
  canvas.style.maxWidth = '100%';
  canvas.style.height = `${desiredH}px`;

  // Helps layout engines reserve the correct space even before the canvas is painted.
  canvas.style.aspectRatio = `${rw} / ${rh}`;

  // Backing store scaling for HiDPI displays.
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(desiredW * dpr));
  canvas.height = Math.max(1, Math.round(desiredH * dpr));
}

/**
 * Draw a “disabled” overlay on top of a tile.
 *
 * Visual language:
 * - Light wash to de-emphasize tile
 * - Dark X to clearly communicate disabled/ignored region
 */
export function drawDisabledOverlay(ctx, x0, y0, w, h) {
  ctx.fillStyle = 'rgba(255,255,255,0.65)';
  ctx.fillRect(x0, y0, w, h);

  ctx.strokeStyle = 'rgba(10,10,10,0.65)';
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(x0 + 8, y0 + 8);
  ctx.lineTo(x0 + w - 8, y0 + h - 8);
  ctx.moveTo(x0 + w - 8, y0 + 8);
  ctx.lineTo(x0 + 8, y0 + h - 8);
  ctx.stroke();
}

/**
 * Draw a centered tile number inside a tile rectangle.
 *
 * Notes:
 * - Labels are 1-based (tile 1..N) because that matches typical user expectation.
 * - Font size adapts to tile dimensions so dense grids remain readable.
 * - Uses a stroke + fill to stay visible on both dark and bright heatmap colors.
 */
function drawTileNumber(ctx, x0, y0, ww, hh, idx) {
  const label = String(idx + 1); // 1-based

  ctx.save();

  // Smaller + less aggressive for dense grids (e.g. 10x10).
  const fontPx = Math.max(11, Math.min(20, Math.floor(Math.min(ww, hh) * 0.34)));
  ctx.font = `700 ${fontPx}px system-ui`; // less bold than 900
  ctx.textBaseline = 'middle';
  ctx.textAlign = 'center';

  const cx = x0 + ww / 2;
  const cy = y0 + hh / 2;

  // Thinner/softer stroke + slightly transparent fill.
  ctx.lineWidth = Math.max(1.5, fontPx * 0.10);
  ctx.strokeStyle = 'rgba(0,0,0,0.70)';
  ctx.fillStyle = 'rgba(255,255,255,0.92)';

  ctx.strokeText(label, cx, cy);
  ctx.fillText(label, cx, cy);

  ctx.restore();
}

/**
 * Determine whether a tile is disabled.
 *
 * Sources of truth:
 * - Backend tile value can be null/undefined for disabled tiles.
 * - Some UI views render "disabled" as a string (display transformation).
 * - disabledTilesSet is the local/server mask for interactive toggling.
 */
export function isTileDisabled(raw, idx, disabledTilesSet) {
  return raw === null || raw === undefined || raw === 'disabled' || disabledTilesSet.has(idx);
}

/**
 * Extract { tiles, rows, cols } from a payload, with fallbacks and inference.
 *
 * Supports multiple payload shapes:
 * - Preferred: payload.video.tiles + payload.video.grid
 * - Fallback:  payload.tiles + payload.grid (useful for older payloads or tests)
 *
 * If grid is missing or inconsistent with tile count, attempts to infer a square-ish grid
 * from the number of tiles (e.g., 9 => 3x3, 16 => 4x4).
 */
export function getGridAndTiles(payload) {
  const tiles = payload?.video?.tiles ?? payload?.tiles ?? [];
  const grid = payload?.video?.grid ?? payload?.grid ?? { rows: 0, cols: 0 };

  let rows = Number(grid.rows) || 0;
  let cols = Number(grid.cols) || 0;

  const n = Array.isArray(tiles) ? tiles.length : 0;

  // If grid metadata is missing/invalid but tile count is known, infer a grid.
  if ((!rows || !cols || rows * cols !== n) && n > 0) {
    const inferred = inferSquareGridFromTiles(n);
    if (inferred.rows && inferred.cols) {
      rows = inferred.rows;
      cols = inferred.cols;
    }
  }

  return { tiles, rows, cols };
}

/**
 * Render a tiles heatmap:
 * - Color per tile based on normalized motion value [0..1]
 * - Draw disabled overlay for masked tiles
 * - Optionally draw tile numbers (driven by server UI state)
 * - Draw grid lines
 *
 * Parameters:
 * - canvas: heatmap canvas element
 * - gridLabelEl: element where the "grid: C×R (N tiles)" label is displayed
 * - payload: latest status payload from /status (preferred) or compatible shape
 * - disabledTilesSet: current set of disabled tile indices (0-based)
 */
export function drawTilesHeatmap({ canvas, gridLabelEl, payload, disabledTilesSet }) {
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  // Adjust canvas size to match monitored region aspect ratio (best-effort).
  resizeHeatmapCanvas(canvas, payload?.region);

  const { tiles, rows, cols } = getGridAndTiles(payload);

  // Label is purely informational; rendering still validates data below.
  gridLabelEl.textContent = rows && cols ? `grid: ${cols}×${rows} (${rows * cols} tiles)` : 'grid: —';

  // Draw in CSS pixels, but keep backing store scaled by DPR for crispness.
  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  // We render sharp-edged rects (no interpolation needed).
  ctx.imageSmoothingEnabled = false;

  const cssW = canvas.width / dpr;
  const cssH = canvas.height / dpr;

  // Clear and paint background.
  ctx.clearRect(0, 0, cssW, cssH);
  ctx.fillStyle = 'rgba(0,0,0,0.15)';
  ctx.fillRect(0, 0, cssW, cssH);

  // Validate tile array length matches grid dimensions.
  const n = Array.isArray(tiles) ? tiles.length : 0;
  if (!rows || !cols || n !== rows * cols) {
    ctx.fillStyle = 'rgba(255,255,255,0.55)';
    ctx.font = '14px system-ui';
    ctx.fillText('No tile data', 12, 22);
    return;
  }

  // Tile-number visibility is driven by server UI state (so all clients stay consistent).
  const showTileNumbers = Boolean(payload?.ui?.show_tile_numbers);

  // Use shared edges() helper (rounding-based, monotonic) to prevent off-by-one gaps.
  const xEdges = edges(cssW, cols);
  const yEdges = edges(cssH, rows);

  // Paint each tile.
  for (let r = 0; r < rows; r++) {
    const y0 = yEdges[r];
    const y1 = yEdges[r + 1];
    const hh = y1 - y0;

    for (let c = 0; c < cols; c++) {
      const x0 = xEdges[c];
      const x1 = xEdges[c + 1];
      const ww = x1 - x0;

      const idx = r * cols + c;
      const raw = tiles[idx];
      const disabled = isTileDisabled(raw, idx, disabledTilesSet);

      // Disabled tiles render as 0 intensity but with an overlay; enabled tiles are clamped.
      const v = disabled ? 0 : clamp01(raw);

      ctx.fillStyle = colorFor01(v);
      ctx.fillRect(x0, y0, ww, hh);

      if (disabled) drawDisabledOverlay(ctx, x0, y0, ww, hh);

      if (showTileNumbers) drawTileNumber(ctx, x0, y0, ww, hh, idx);
    }
  }

  // Grid lines (drawn after fills/overlays so they remain visible).
  ctx.strokeStyle = 'rgba(0,0,0,0.25)';
  ctx.lineWidth = 1;

  for (let c = 1; c < cols; c++) {
    const x = xEdges[c];
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, cssH);
    ctx.stroke();
  }
  for (let r = 1; r < rows; r++) {
    const y = yEdges[r];
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(cssW, y);
    ctx.stroke();
  }
}

/**
 * Convert a canvas click event into a 0-based tile index (row-major).
 *
 * Implementation:
 * - Uses the element's bounding box (CSS pixels) rather than canvas backing store size.
 * - Divides rect evenly by rows/cols and clamps indices to bounds.
 *
 * Note:
 * - This assumes the visual grid is evenly spaced in CSS pixels. The drawing uses edges()
 *   which distributes rounding error; this click mapping is close enough for user interaction.
 *   If you ever need pixel-perfect mapping, reuse the same edges() logic with rect width/height.
 */
export function tileIndexFromCanvasClick(canvas, rows, cols, ev) {
  const rect = canvas.getBoundingClientRect();
  const x = ev.clientX - rect.left;
  const y = ev.clientY - rect.top;
  const w = rect.width / cols;
  const h = rect.height / rows;

  const c = Math.max(0, Math.min(cols - 1, Math.floor(x / w)));
  const r = Math.max(0, Math.min(rows - 1, Math.floor(y / h)));
  return r * cols + c;
}
