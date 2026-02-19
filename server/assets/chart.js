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
  drawSeriesChart(canvas, historyPayloads, p => Number(p?.video?.motion_mean) || 0, 'Collecting history…');
}

export function drawAudioChart(canvas, historyPayloads) {
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  drawSeriesChart(
    canvas,
    historyPayloads,
    p => ({ left: (Number(p?.audio?.left) || 0) / 100, right: (Number(p?.audio?.right) || 0) / 100 }),
    'Collecting audio history…',
    true,
  );
}

function drawSeriesChart(canvas, historyPayloads, mapper, emptyText, stereo = false) {
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const w = canvas.width;
  const h = canvas.height;

  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = 'rgba(0,0,0,0.15)';
  ctx.fillRect(0, 0, w, h);

  // Normalize and sanitize points:
  // - timestamp must be numeric and > 0
  // - y is video.motion_mean clamped into [0..1]
  // - sort by timestamp so out-of-order history still draws correctly
  const pts = (historyPayloads || [])
    .map(p => {
      const mapped = mapper(p);
      if (stereo) {
        return { t: Number(p.timestamp) || 0, l: clamp01(mapped.left), r: clamp01(mapped.right) };
      }
      return { t: Number(p.timestamp) || 0, y: clamp01(mapped) };
    })
    .filter(p => p.t > 0)
    .sort((a, b) => a.t - b.t);

  // Draw light horizontal grid lines for visual scale reference.
  // Layout uses a left margin (x=40) for y-axis labels and a small right margin (x=w-10).
  ctx.strokeStyle = 'rgba(255,255,255,0.08)';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 5; i++) {
    const yy = (h - 20) - i * ((h - 40) / 5);
    ctx.beginPath();
    ctx.moveTo(40, yy);
    ctx.lineTo(w - 10, yy);
    ctx.stroke();
  }

  const safeYMax = Math.max(1e-9, Number(yMax) || 1);

  if (points.length < 2) {
    ctx.fillStyle = 'rgba(255,255,255,0.55)';
    ctx.font = '16px system-ui';
    ctx.fillText(emptyText, 16, 28);

    if (pts.length === 1) {
      const y1 = (h - 20) - (stereo ? pts[0].l : pts[0].y) * (h - 40);
      ctx.fillStyle = 'rgba(120, 190, 255, 0.95)';
      ctx.beginPath();
      ctx.arc(40, y1, 3, 0, Math.PI * 2);
      ctx.fill();
    }

    ctx.fillStyle = 'rgba(255,255,255,0.55)';
    ctx.font = '12px system-ui';
    ctx.fillText(yLabelTop, 10, 20);
    ctx.fillText(yLabelBottom, 10, h - 20);
    return;
  }

  const tMin = points[0].t;
  const tMax = points[points.length - 1].t;
  const tSpan = Math.max(1e-6, tMax - tMin);

  ctx.strokeStyle = lineColor;
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (let i = 0; i < pts.length; i++) {
    const x = 40 + ((pts[i].t - tMin) / tSpan) * (w - 50);
    const y = (h - 20) - (stereo ? pts[i].l : pts[i].y) * (h - 40);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();

  if (stereo) {
    ctx.strokeStyle = 'rgba(255, 170, 90, 0.95)';
    ctx.beginPath();
    for (let i = 0; i < pts.length; i++) {
      const x = 40 + ((pts[i].t - tMin) / tSpan) * (w - 50);
      const y = (h - 20) - pts[i].r * (h - 40);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }

  // Minimal y-axis labels (fixed because the scale is fixed [0..1]).
  ctx.fillStyle = 'rgba(255,255,255,0.55)';
  ctx.font = '12px system-ui';
  ctx.fillText(yLabelTop, 10, 20);
  ctx.fillText(yLabelBottom, 10, h - 20);
}

export function drawMotionChart(canvas, historyPayloads) {
  const pts = (historyPayloads || [])
    .map((p) => ({ t: Number(p.timestamp) || 0, y: clamp01(Number(p?.video?.motion_mean) || 0) }))
    .filter((p) => p.t > 0)
    .sort((a, b) => a.t - b.t);

  drawSeriesChart({
    canvas,
    points: pts,
    lineColor: 'rgba(120, 190, 255, 0.95)',
    emptyLabel: 'Collecting history…',
    yMax: 1,
    yLabelTop: '1.0',
    yLabelBottom: '0.0',
  });
}

export function drawAudioChart(canvas, historyPayloads) {
  const pts = (historyPayloads || [])
    .map((p) => {
      const rawPeak = Number(p?.audio?.peak);
      const hasAudio = Number.isFinite(rawPeak);
      // peak is expected in [0..1] for float32 PCM; normalize to percentage for readability.
      const peakPct = hasAudio ? clamp01(rawPeak) * 100.0 : 0.0;
      return { t: Number(p.timestamp) || 0, y: peakPct, hasAudio };
    })
    .filter((p) => p.t > 0)
    .sort((a, b) => a.t - b.t);

  const hasAnyAudio = pts.some((p) => p.hasAudio);
  drawSeriesChart({
    canvas,
    points: pts,
    lineColor: 'rgba(255, 176, 89, 0.95)',
    emptyLabel: hasAnyAudio ? 'Collecting history…' : 'No audio',
    yMax: 100,
    yLabelTop: '100',
    yLabelBottom: '0',
  });
}
