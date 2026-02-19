// server/assets/chart.js
import { clamp01 } from './utils.js';

/**
 * Draw a simple time-series chart of video.motion_mean onto a canvas.
 *
 * Inputs:
 * - canvas: HTMLCanvasElement (expected to have width/height set in pixels)
 * - historyPayloads: array of status payloads (each should contain { timestamp, video: { motion_mean } })
 *
 * Rendering goals:
 * - Lightweight: no external chart libs, fast to redraw on a polling loop.
 * - Robust: tolerate missing/partial payloads and still draw a useful frame.
 * - Stable scale: y-axis is clamped to [0..1] to match normalized motion metrics.
 */
export function drawChart(canvas, historyPayloads) {
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const w = canvas.width;
  const h = canvas.height;

  // Clear the previous frame and paint a translucent background panel so the chart
  // is visible against the page without needing CSS tricks.
  ctx.clearRect(0, 0, w, h);

  ctx.fillStyle = 'rgba(0,0,0,0.15)';
  ctx.fillRect(0, 0, w, h);

  // Normalize and sanitize points:
  // - timestamp must be numeric and > 0
  // - y is video.motion_mean clamped into [0..1]
  // - sort by timestamp so out-of-order history still draws correctly
  const pts = (historyPayloads || [])
    .map(p => ({ t: Number(p.timestamp) || 0, y: clamp01(Number(p?.video?.motion_mean) || 0) }))
    .filter(p => p.t > 0)
    .sort((a, b) => a.t - b.t);

  // Draw light horizontal grid lines for visual scale reference.
  // Layout uses a left margin (x=40) for y-axis labels and a small right margin (x=w-10).
  ctx.strokeStyle = 'rgba(255,255,255,0.08)';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 5; i++) {
    // Chart area is (40..w-10) horizontally and (20..h-20) vertically.
    // We compute gridlines over the drawable height (h-40) and offset by 20 px margins.
    const yy = (h - 20) - i * ((h - 40) / 5);
    ctx.beginPath();
    ctx.moveTo(40, yy);
    ctx.lineTo(w - 10, yy);
    ctx.stroke();
  }

  // Not enough data for a polyline: show a friendly placeholder and optionally
  // render the single point if present.
  if (pts.length < 2) {
    ctx.fillStyle = 'rgba(255,255,255,0.55)';
    ctx.font = '16px system-ui';
    ctx.fillText('Collecting history…', 16, 28);

    if (pts.length === 1) {
      const y1 = (h - 20) - pts[0].y * (h - 40);
      ctx.fillStyle = 'rgba(120, 190, 255, 0.95)';
      ctx.beginPath();
      ctx.arc(40, y1, 3, 0, Math.PI * 2);
      ctx.fill();
    }
    return;
  }

  // X-axis normalization:
  // - Map timestamps into [0..1] across the observed time span
  // - Guard against zero span so division is safe
  const tMin = pts[0].t;
  const tMax = pts[pts.length - 1].t;
  const tSpan = Math.max(1e-6, tMax - tMin);

  // Draw the polyline.
  // The chart uses:
  // - left margin 40 (room for y labels)
  // - right margin 10
  // - top/bottom margins 20
  ctx.strokeStyle = 'rgba(120, 190, 255, 0.95)';
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (let i = 0; i < pts.length; i++) {
    const x = 40 + ((pts[i].t - tMin) / tSpan) * (w - 50);
    const y = (h - 20) - pts[i].y * (h - 40);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();

  // Minimal y-axis labels (fixed because the scale is fixed [0..1]).
  ctx.fillStyle = 'rgba(255,255,255,0.55)';
  ctx.font = '12px system-ui';
  ctx.fillText('1.0', 10, 20);
  ctx.fillText('0.0', 10, h - 20);
}
