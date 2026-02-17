// server/assets/utils.js

/**
 * Clamp a numeric value into the normalized [0..1] range.
 *
 * Behavior:
 * - Coerces input via Number(...); NaN/undefined/null become 0.
 * - Returns 0 for values < 0, 1 for values > 1, else the value itself.
 *
 * Used throughout the UI to keep motion metrics and color mapping stable even when
 * payloads are partial or malformed.
 */
 export function clamp01(x) {
  const v = Number(x) || 0;
  if (v < 0) return 0;
  if (v > 1) return 1;
  return v;
}

/**
 * Format an epoch-seconds timestamp into a locale string for display.
 *
 * Input:
 * - ts: seconds since epoch (float or int). Payloads use epoch seconds.
 *
 * Output:
 * - Localized date/time string or "—" on failure.
 *
 * Note:
 * - Uses the browser locale/timezone, which is generally what the user expects for a dashboard.
 */
export function fmtTime(ts) {
  try {
    const d = new Date(Number(ts) * 1000);
    return d.toLocaleString();
  } catch {
    return '—';
  }
}

/**
 * Compute partition edges for splitting a 1D span into `parts`.
 *
 * Returns an array of length parts+1 such that:
 * - out[0] = 0
 * - out[parts] = size
 * - intermediate values are rounded proportional positions
 *
 * Use case:
 * - Mapping a canvas width/height in CSS pixels into tile rectangles without accumulating
 *   rounding errors across many columns/rows.
 *
 * Note:
 * - This does not enforce monotonicity if rounding ever produces equal/decreasing edges.
 *   If you need strict monotonic guarantees, add a fix-up pass (as done in the Python version).
 */
export function edges(size, parts) {
  const out = new Array(parts + 1);
  for (let i = 0; i <= parts; i++) out[i] = Math.round((i * size) / parts);
  out[0] = 0;
  out[parts] = size;
  return out;
}

/**
 * Map a normalized value in [0..1] to an RGB heat color.
 *
 * Current palette:
 * - v=0 -> reddish (higher red, lower green)
 * - v=1 -> greenish (lower red, higher green)
 * - blue channel is kept relatively low/constant
 *
 * Notes:
 * - This is intentionally simple (no gradients/colormaps) and fast to compute per tile.
 * - Uses clamp01 so invalid inputs do not break the renderer.
 */
export function colorFor01(x) {
  const v = clamp01(x);
  const r = Math.round(220 * (1 - v) + 40 * v);
  const g = Math.round(60 * (1 - v) + 220 * v);
  const b = Math.round(60 * (1 - v) + 60 * v);
  return `rgb(${r}, ${g}, ${b})`;
}

/**
 * Infer a square grid from a tile count.
 *
 * Used as a fallback when payloads omit grid metadata but provide tiles.
 * Example:
 * - 9 tiles -> 3x3
 * - 16 tiles -> 4x4
 *
 * Returns:
 * - { rows, cols } if n is a perfect square, else { rows: 0, cols: 0 }.
 */
export function inferSquareGridFromTiles(tilesLen) {
  const n = Number(tilesLen) || 0;
  if (n <= 0) return { rows: 0, cols: 0 };
  const side = Math.round(Math.sqrt(n));
  return side > 0 && side * side === n ? { rows: side, cols: side } : { rows: 0, cols: 0 };
}
