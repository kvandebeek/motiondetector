// server/assets/chart.js
import { clamp01 } from './utils.js';

/**
 * Draw a simple time-series chart of video.motion_mean onto a canvas.
 */
export function drawChart(canvas, historyPayloads) {
  drawSeriesChart({
    canvas,
    historyPayloads,
    mapper: (p) => ({ y: Number(p?.video?.motion_mean) || 0 }),
    emptyText: 'Collecting history…',
    lineColorLeft: 'rgba(120, 190, 255, 0.95)',
    yLabelTop: '1.0',
    yLabelBottom: '0.0',
  });
}

/**
 * Draw audio history as two lines (left/right), where input values are expected
 * to be percentages in [0..100].
 */
export function drawAudioChart(canvas, historyPayloads) {
  drawSeriesChart({
    canvas,
    historyPayloads,
    mapper: (p) => ({
      left: (Number(p?.audio?.left) || 0) / 100,
      right: (Number(p?.audio?.right) || 0) / 100,
    }),
    emptyText: 'Collecting audio history…',
    lineColorLeft: 'rgba(120, 190, 255, 0.95)',
    lineColorRight: 'rgba(255, 170, 90, 0.95)',
    yLabelTop: '100',
    yLabelBottom: '0',
    stereo: true,
  });
}

function drawSeriesChart({
  canvas,
  historyPayloads,
  mapper,
  emptyText,
  lineColorLeft,
  lineColorRight,
  yLabelTop,
  yLabelBottom,
  stereo = false,
}) {
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
        l: clamp01(stereo ? mapped.left : mapped.y),
        r: clamp01(mapped.right),
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
      const y = (h - 20) - pts[0].l * (h - 40);
      ctx.fillStyle = lineColorLeft;
      ctx.beginPath();
      ctx.arc(40, y, 3, 0, Math.PI * 2);
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

  ctx.strokeStyle = lineColorLeft;
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (let i = 0; i < pts.length; i++) {
    const x = 40 + ((pts[i].t - tMin) / tSpan) * (w - 50);
    const y = (h - 20) - pts[i].l * (h - 40);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.stroke();

  if (stereo) {
    ctx.strokeStyle = lineColorRight;
    ctx.beginPath();
    for (let i = 0; i < pts.length; i++) {
      const x = 40 + ((pts[i].t - tMin) / tSpan) * (w - 50);
      const y = (h - 20) - pts[i].r * (h - 40);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }

  ctx.fillStyle = 'rgba(255,255,255,0.55)';
  ctx.font = '12px system-ui';
  ctx.fillText(yLabelTop, 10, 20);
  ctx.fillText(yLabelBottom, 10, h - 20);
}
