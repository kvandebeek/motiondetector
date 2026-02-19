// server/assets/chart.js
import { clamp01 } from './utils.js';

function drawSeriesChart({ canvas, points, lineColor, emptyLabel }) {
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const w = canvas.width;
  const h = canvas.height;

  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = 'rgba(0,0,0,0.15)';
  ctx.fillRect(0, 0, w, h);

  ctx.strokeStyle = 'rgba(255,255,255,0.08)';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 5; i++) {
    const yy = (h - 20) - i * ((h - 40) / 5);
    ctx.beginPath();
    ctx.moveTo(40, yy);
    ctx.lineTo(w - 10, yy);
    ctx.stroke();
  }

  if (points.length < 2) {
    ctx.fillStyle = 'rgba(255,255,255,0.55)';
    ctx.font = '16px system-ui';
    ctx.fillText(emptyLabel, 16, 28);

    if (points.length === 1) {
      const y1 = (h - 20) - points[0].y * (h - 40);
      ctx.fillStyle = lineColor;
      ctx.beginPath();
      ctx.arc(40, y1, 3, 0, Math.PI * 2);
      ctx.fill();
    }

    ctx.fillStyle = 'rgba(255,255,255,0.55)';
    ctx.font = '12px system-ui';
    ctx.fillText('1.0', 10, 20);
    ctx.fillText('0.0', 10, h - 20);
    return;
  }

  const tMin = points[0].t;
  const tMax = points[points.length - 1].t;
  const tSpan = Math.max(1e-6, tMax - tMin);

  ctx.strokeStyle = lineColor;
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (let i = 0; i < points.length; i++) {
    const x = 40 + ((points[i].t - tMin) / tSpan) * (w - 50);
    const y = (h - 20) - points[i].y * (h - 40);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();

  ctx.fillStyle = 'rgba(255,255,255,0.55)';
  ctx.font = '12px system-ui';
  ctx.fillText('1.0', 10, 20);
  ctx.fillText('0.0', 10, h - 20);
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
  });
}

export function drawAudioChart(canvas, historyPayloads) {
  const pts = (historyPayloads || [])
    .map((p) => {
      const hasAudio = typeof p?.audio?.level === 'number' && Number.isFinite(p.audio.level);
      return { t: Number(p.timestamp) || 0, y: hasAudio ? clamp01(Number(p.audio.level)) : 0, hasAudio };
    })
    .filter((p) => p.t > 0)
    .sort((a, b) => a.t - b.t);

  const hasAnyAudio = pts.some((p) => p.hasAudio);
  drawSeriesChart({
    canvas,
    points: pts,
    lineColor: 'rgba(255, 176, 89, 0.95)',
    emptyLabel: hasAnyAudio ? 'Collecting history…' : 'No audio',
  });
}
