// File commentary: server/assets/chart.js - This file contains browser-side behavior for the project.
// server/assets/chart.js
import { clamp01 } from './utils.js';

export function drawChart(canvas, historyPayloads) {
  drawSeriesChart({
    canvas,
    historyPayloads,
    mapper: (p) => ({
      mean: Number(p?.video?.motion_mean) || 0,
      peak: Number(p?.video?.motion_instant_top1) || 0,
    }),
    emptyText: 'Collecting history…',
    meanColor: 'rgba(90, 220, 120, 0.95)',
    peakColor: 'rgba(255, 80, 80, 0.95)',
    yLabelTop: 'MAX',
    yLabelBottom: 'MIN',
    clampValues: true,
  });
}

export function drawBlockinessChart(canvas, historyPayloads) {
  drawSeriesChart({
    canvas,
    historyPayloads,
    mapper: (p) => ({
      mean: Number(p?.video?.blockiness?.score_ema),
      peak: Number(p?.video?.blockiness?.score),
    }),
    emptyText: 'Collecting blockiness history…',
    meanColor: 'rgba(80, 180, 255, 0.95)',
    peakColor: 'rgba(255, 170, 60, 0.95)',
    yLabelTop: 'HIGH',
    yLabelBottom: 'LOW',
    clampValues: false,
  });
}

export function drawAudioChart(canvas, historyPayloads) {
  drawSeriesChart({
    canvas,
    historyPayloads,
    mapper: (p) => ({
      mean: (Number(p?.audio?.left) || 0) / 100,
      peak: (Number(p?.audio?.right) || 0) / 100,
    }),
    emptyText: 'Collecting audio history…',
    meanColor: 'rgba(90, 220, 120, 0.95)',
    peakColor: 'rgba(255, 80, 80, 0.95)',
    yLabelTop: 'MAX',
    yLabelBottom: 'MIN',
    clampValues: true,
  });
}

function drawSeriesChart({ canvas, historyPayloads, mapper, emptyText, meanColor, peakColor, yLabelTop, yLabelBottom, clampValues }) {
  const ctx = canvas?.getContext?.('2d');
  if (!ctx) return;

  const w = canvas.width;
  const h = canvas.height;

  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = 'rgba(0,0,0,0.15)';
  ctx.fillRect(0, 0, w, h);

  const pts = (historyPayloads || [])
    .map((p) => {
      const mapped = mapper(p) || {};
      const meanRaw = Number(mapped.mean);
      const peakRaw = Number(mapped.peak);
      const mean = Number.isFinite(meanRaw) ? meanRaw : 0;
      const peak = Number.isFinite(peakRaw) ? peakRaw : 0;
      return {
        t: Number(p?.timestamp) || 0,
        mean: clampValues ? clamp01(mean) : Math.max(0, mean),
        peak: clampValues ? clamp01(peak) : Math.max(0, peak),
      };
    })
    .filter((p) => p.t > 0)
    .sort((a, b) => a.t - b.t);

  const yMax = clampValues
    ? 1
    : Math.max(1, ...pts.map((p) => Math.max(p.mean, p.peak)));

  ctx.strokeStyle = 'rgba(255,255,255,0.08)';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 5; i++) {
    const yy = (h - 20) - i * ((h - 40) / 5);
    ctx.beginPath();
    ctx.moveTo(40, yy);
    ctx.lineTo(w - 10, yy);
    ctx.stroke();
  }

  if (pts.length < 2) {
    ctx.fillStyle = 'rgba(255,255,255,0.55)';
    ctx.font = '16px system-ui';
    ctx.fillText(emptyText, 16, 28);
  } else {
    const tMin = pts[0].t;
    const tMax = pts[pts.length - 1].t;
    const tSpan = Math.max(1e-6, tMax - tMin);

    const drawLine = (key, color) => {
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      ctx.beginPath();
      for (let i = 0; i < pts.length; i++) {
        const x = 40 + ((pts[i].t - tMin) / tSpan) * (w - 50);
        const y = (h - 20) - (Math.min(yMax, pts[i][key]) / yMax) * (h - 40);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      }
      ctx.stroke();
    };

    drawLine('mean', meanColor);
    drawLine('peak', peakColor);
  }

  ctx.fillStyle = 'rgba(255,255,255,0.55)';
  ctx.font = '12px system-ui';
  ctx.fillText(yLabelTop, 10, 20);
  ctx.fillText(yLabelBottom, 10, h - 20);
}
