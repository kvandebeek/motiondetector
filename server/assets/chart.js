// File commentary: server/assets/chart.js - This file contains browser-side behavior for the project.
// server/assets/chart.js
import { clamp01 } from './utils.js';

// drawChart keeps this part of the interface easy to understand and use.
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
  });
}

// drawAudioChart keeps this part of the interface easy to understand and use.
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
  });
}

// drawSeriesChart keeps this part of the interface easy to understand and use.
function drawSeriesChart({ canvas, historyPayloads, mapper, emptyText, meanColor, peakColor, yLabelTop, yLabelBottom }) {
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
      return {
        t: Number(p?.timestamp) || 0,
        mean: clamp01(mapped.mean),
        peak: clamp01(mapped.peak),
      };
    })
    .filter((p) => p.t > 0)
    .sort((a, b) => a.t - b.t);

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

    if (pts.length === 1) {
      const yMean = (h - 20) - pts[0].mean * (h - 40);
      const yPeak = (h - 20) - pts[0].peak * (h - 40);
      ctx.fillStyle = meanColor;
      ctx.beginPath();
      ctx.arc(40, yMean, 3, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = peakColor;
      ctx.beginPath();
      ctx.arc(44, yPeak, 3, 0, Math.PI * 2);
      ctx.fill();
    }

    ctx.fillStyle = 'rgba(255,255,255,0.55)';
    ctx.font = '12px system-ui';
    ctx.fillText(yLabelTop, 10, 20);
    ctx.fillText(yLabelBottom, 10, h - 20);
    return;
  }

  const tMin = pts[0].t;
  const tMax = pts[pts.length - 1].t;
  const tSpan = Math.max(1e-6, tMax - tMin);

// drawLine keeps this part of the interface easy to understand and use.
  const drawLine = (key, color) => {
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    for (let i = 0; i < pts.length; i++) {
      const x = 40 + ((pts[i].t - tMin) / tSpan) * (w - 50);
      const y = (h - 20) - pts[i][key] * (h - 40);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  };

  drawLine('mean', meanColor);
  drawLine('peak', peakColor);

  ctx.fillStyle = 'rgba(255,255,255,0.55)';
  ctx.font = '12px system-ui';
  ctx.fillText(yLabelTop, 10, 20);
  ctx.fillText(yLabelBottom, 10, h - 20);
}
