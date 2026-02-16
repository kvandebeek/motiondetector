# server/server_html_contents.py
from __future__ import annotations


def get_index_html(*, history_seconds: int) -> str:
    hs = int(history_seconds)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>motiondetector</title>
  <style>
    :root {{
      --bg: #0b0e14;
      --panel: #0f1420;
      --panel2: #0c111b;
      --text: #e8eefc;
      --muted: #9aa7c6;
      --border: rgba(255,255,255,0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial;
      background: var(--bg);
      color: var(--text);
    }}
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px 18px;
      border-bottom: 1px solid var(--border);
    }}
    header h1 {{
      font-size: 20px;
      margin: 0;
      font-weight: 700;
      letter-spacing: 0.2px;
    }}
    .actions {{ display: flex; gap: 10px; }}
    button {{
      background: #121a2b;
      border: 1px solid var(--border);
      color: var(--text);
      padding: 8px 12px;
      border-radius: 10px;
      cursor: pointer;
      font-weight: 600;
    }}
    button:hover {{ border-color: rgba(255,255,255,0.18); }}

    .grid {{
      padding: 16px;
      display: grid;
      grid-template-columns: 1.25fr 1fr 0.75fr;
      gap: 14px;
    }}

    .panel {{
      background: linear-gradient(180deg, var(--panel), var(--panel2));
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 12px 12px 10px;
      overflow: hidden;
      min-height: 200px;
    }}
    .panel h2 {{
      margin: 0 0 10px 0;
      font-size: 14px;
      color: var(--muted);
      font-weight: 700;
      letter-spacing: 0.2px;
      display:flex;
      align-items:center;
      justify-content: space-between;
    }}
    .pillrow {{
      display:flex;
      flex-wrap:wrap;
      gap: 8px;
      margin-bottom: 10px;
    }}
    .pill {{
      display:inline-flex;
      align-items:center;
      gap: 8px;
      padding: 6px 10px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: rgba(255,255,255,0.03);
      font-weight: 700;
      font-size: 13px;
    }}
    pre {{
      margin: 0;
      padding: 10px;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: rgba(0,0,0,0.25);
      color: #e9f2ff;
      font-size: 12px;
      line-height: 1.35;
      overflow: auto;
      height: 520px;
    }}

    .chartwrap {{
      border: 1px solid var(--border);
      border-radius: 12px;
      background: rgba(0,0,0,0.18);
      padding: 10px;
    }}
    canvas {{ width: 100%; height: 540px; display:block; }}

    .heatwrap {{
      border: 1px solid var(--border);
      border-radius: 12px;
      background: rgba(0,0,0,0.18);
      padding: 10px;
    }}
    #tilesHeatmap {{
      width: 100%;
      height: auto;
      display: block;
      aspect-ratio: 16 / 9; /* fallback; JS will override per region */
      border-radius: 10px;
    }}

    .subtle {{ font-size: 12px; color: var(--muted); }}
  </style>
</head>
<body>
  <header>
    <h1>motiondetector</h1>
    <div class="actions">
      <button id="copyJson">Copy JSON</button>
      <button id="quitBtn">Quit</button>
    </div>
  </header>

  <div class="grid">
    <section class="panel">
      <h2><span>Status JSON</span><span class="subtle" id="tsLabel">—</span></h2>

      <div class="pillrow">
        <span class="pill" id="pillVideo">video: —</span>
        <span class="pill" id="pillMean">motion_mean: —</span>
        <span class="pill" id="pillOverall">overall: —</span>
      </div>

      <pre id="jsonBox">{{}}</pre>
    </section>

    <section class="panel">
      <h2><span>Motion mean (last {hs}s)</span></h2>
      <div class="chartwrap">
        <canvas id="chart" width="900" height="540"></canvas>
      </div>
    </section>

    <section class="panel">
      <h2><span>Tiles heatmap (0 → 1)</span></h2>
      <div class="heatwrap">
        <canvas id="tilesHeatmap" width="540" height="540"></canvas>
      </div>
      <div class="subtle" id="gridLabel" style="margin-top:10px;">grid: —</div>
    </section>
  </div>

<script>
const STATUS_URL = '/status';
const HISTORY_URL = '/history';

const jsonBox = document.getElementById('jsonBox');
const tsLabel = document.getElementById('tsLabel');
const pillVideo = document.getElementById('pillVideo');
const pillMean = document.getElementById('pillMean');
const pillOverall = document.getElementById('pillOverall');
const gridLabel = document.getElementById('gridLabel');

const copyBtn = document.getElementById('copyJson');
const quitBtn = document.getElementById('quitBtn');

function clamp01(x) {{
  if (x < 0) return 0;
  if (x > 1) return 1;
  return x;
}}

function resizeHeatmapCanvas(canvas, region) {{
  const wrap = canvas.parentElement;
  if (!wrap) return;

  const rw = Number(region?.width) || 0;
  const rh = Number(region?.height) || 0;
  if (!rw || !rh) return;

  const maxH = 540;
  const wrapW = wrap.clientWidth || 540;

  const aspect = rw / rh;
  const desiredW = wrapW;
  const desiredH = Math.min(maxH, Math.round(desiredW / aspect));

  canvas.style.aspectRatio = `${{rw}} / ${{rh}}`;

  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, Math.round(desiredW * dpr));
  canvas.height = Math.max(1, Math.round(desiredH * dpr));
}}

function colorFor01(x) {{
  const v = clamp01(Number(x) || 0);
  const r = Math.round(220 * (1 - v) + 40 * v);
  const g = Math.round(60 * (1 - v) + 220 * v);
  const b = Math.round(60 * (1 - v) + 60 * v);
  return `rgb(${{r}}, ${{g}}, ${{b}})`;
}}

function fmtTime(ts) {{
  try {{
    const d = new Date(ts * 1000);
    return d.toLocaleString();
  }} catch {{
    return '—';
  }}
}}

async function fetchJson(url) {{
  const res = await fetch(url, {{ cache: 'no-store' }});
  if (!res.ok) throw new Error(`HTTP ${{res.status}}`);
  return await res.json();
}}

function renderStatus(payload) {{
  tsLabel.textContent = fmtTime(payload.timestamp);

  const vState = payload?.video?.state ?? '—';
  const mean = payload?.video?.motion_mean ?? 0;
  const oState = payload?.overall?.state ?? '—';

  pillVideo.textContent = `video: ${{vState}}`;
  pillMean.textContent = `motion_mean: ${{Number(mean).toFixed(4)}}`;
  pillOverall.textContent = `overall: ${{oState}}`;

  jsonBox.textContent = JSON.stringify(payload, null, 2);
}}

function drawChart(historyPayloads) {{
  const canvas = document.getElementById('chart');
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  ctx.fillStyle = 'rgba(0,0,0,0.15)';
  ctx.fillRect(0, 0, w, h);

  const pts = (historyPayloads || [])
    .map(p => ({{ t: Number(p.timestamp) || 0, y: clamp01(Number(p?.video?.motion_mean) || 0) }}))
    .filter(p => p.t > 0)
    .sort((a,b) => a.t - b.t);

  ctx.strokeStyle = 'rgba(255,255,255,0.08)';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 5; i++) {{
    const yy = (h - 20) - i * ((h - 40) / 5);
    ctx.beginPath();
    ctx.moveTo(40, yy);
    ctx.lineTo(w - 10, yy);
    ctx.stroke();
  }}

  if (pts.length < 2) {{
    ctx.fillStyle = 'rgba(255,255,255,0.55)';
    ctx.font = '16px system-ui';
    ctx.fillText('Collecting history…', 16, 28);
    if (pts.length === 1) {{
      const y1 = (h - 20) - pts[0].y * (h - 40);
      ctx.fillStyle = 'rgba(120, 190, 255, 0.95)';
      ctx.beginPath();
      ctx.arc(40, y1, 3, 0, Math.PI * 2);
      ctx.fill();
    }}
    return;
  }}

  const tMin = pts[0].t;
  const tMax = pts[pts.length - 1].t;
  const tSpan = Math.max(1e-6, tMax - tMin);

  ctx.strokeStyle = 'rgba(120, 190, 255, 0.95)';
  ctx.lineWidth = 2;
  ctx.beginPath();
  for (let i = 0; i < pts.length; i++) {{
    const x = 40 + ((pts[i].t - tMin) / tSpan) * (w - 50);
    const y = (h - 20) - pts[i].y * (h - 40);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }}
  ctx.stroke();

  ctx.fillStyle = 'rgba(255,255,255,0.55)';
  ctx.font = '12px system-ui';
  ctx.fillText('1.0', 10, 20);
  ctx.fillText('0.0', 10, h - 20);
}}

function edges(size, parts) {{
  // Integer-stable boundaries to avoid drift/clipping with many tiles.
  const out = new Array(parts + 1);
  for (let i = 0; i <= parts; i++) out[i] = Math.round((i * size) / parts);
  out[0] = 0;
  out[parts] = size;
  return out;
}}

function drawTilesHeatmap(payload) {{
  const canvas = document.getElementById('tilesHeatmap');
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  resizeHeatmapCanvas(canvas, payload?.region);

  const tiles = payload?.video?.tiles ?? [];
  const grid = payload?.video?.grid ?? {{ rows: 0, cols: 0 }};
  const rows = Number(grid.rows) || 0;
  const cols = Number(grid.cols) || 0;

  gridLabel.textContent = rows && cols ? `grid: ${{cols}}×${{rows}} (${{rows*cols}} tiles)` : 'grid: —';

  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.imageSmoothingEnabled = false;

  const cssW = canvas.width / dpr;
  const cssH = canvas.height / dpr;

  ctx.clearRect(0, 0, cssW, cssH);
  ctx.fillStyle = 'rgba(0,0,0,0.15)';
  ctx.fillRect(0, 0, cssW, cssH);

  if (!rows || !cols || tiles.length !== rows * cols) {{
    ctx.fillStyle = 'rgba(255,255,255,0.55)';
    ctx.font = '14px system-ui';
    ctx.fillText('No tile data', 12, 22);
    return;
  }}

  const xEdges = edges(cssW, cols);
  const yEdges = edges(cssH, rows);

  for (let r = 0; r < rows; r++) {{
    const y0 = yEdges[r];
    const y1 = yEdges[r + 1];
    const hh = y1 - y0;
    for (let c = 0; c < cols; c++) {{
      const x0 = xEdges[c];
      const x1 = xEdges[c + 1];
      const ww = x1 - x0;

      const idx = r * cols + c;
      const v = clamp01(Number(tiles[idx]) || 0);

      ctx.fillStyle = colorFor01(v);
      ctx.fillRect(x0, y0, ww, hh);
    }}
  }}

  // grid lines on exact edges (no drift)
  ctx.strokeStyle = 'rgba(0,0,0,0.25)';
  ctx.lineWidth = 1;

  for (let c = 1; c < cols; c++) {{
    const x = xEdges[c];
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, cssH);
    ctx.stroke();
  }}
  for (let r = 1; r < rows; r++) {{
    const y = yEdges[r];
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(cssW, y);
    ctx.stroke();
  }}
}}

async function tick() {{
  try {{
    const status = await fetchJson(STATUS_URL);
    renderStatus(status);
    drawTilesHeatmap(status);
  }} catch {{
    // keep last render
  }}

  try {{
    const hist = await fetchJson(HISTORY_URL);
    drawChart(hist.history || []);
  }} catch {{
    drawChart([]);
  }}
}}

copyBtn.addEventListener('click', async () => {{
  try {{
    await navigator.clipboard.writeText(jsonBox.textContent || '{{}}');
  }} catch {{}}
}});

quitBtn.addEventListener('click', async () => {{
  try {{
    await fetch('/quit', {{ method: 'POST' }});
  }} catch {{}}
}});

tick();
setInterval(tick, 200);
</script>
</body>
</html>
"""
