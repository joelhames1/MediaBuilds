// Hand-written stand-in for a Claude-written kit: paper + watercolor washes + ink line.
const PAPER = '#f3ead8';
function paperLayer(S) {
  const c = new OffscreenCanvas(S.W, S.H), g = c.getContext('2d');
  g.fillStyle = PAPER; g.fillRect(0, 0, S.W, S.H);
  const r = U.mulberry32(7);
  for (let i = 0; i < 2500; i++) { g.fillStyle = `rgba(120,100,70,${0.02 + r() * 0.04})`; g.fillRect(r() * S.W, r() * S.H, 1 + r() * 2, 1 + r() * 2); }
  const v = g.createRadialGradient(S.W / 2, S.H / 2, S.H * 0.3, S.W / 2, S.H / 2, S.H * 0.9);
  v.addColorStop(0, 'rgba(0,0,0,0)'); v.addColorStop(1, 'rgba(80,60,30,0.18)'); g.fillStyle = v; g.fillRect(0, 0, S.W, S.H);
  return c;
}
function blob(cx, cy, r, seed, wobble = 0.35, n = 48) {
  const pts = [];
  for (let i = 0; i < n; i++) { const a = i / n * U.TAU; const k = 1 + wobble * U.noise3(Math.cos(a) * 1.3 + seed, Math.sin(a) * 1.3, seed); pts.push([cx + Math.cos(a) * r * k, cy + Math.sin(a) * r * k]); }
  return pts;
}
function wash(ctx, cx, cy, r, color, alpha, seed, grow = 1) {
  for (let layer = 0; layer < 6; layer++) {
    const pts = blob(cx, cy, r * grow * (0.75 + layer * 0.06), seed + layer * 3.1, 0.3 + layer * 0.03);
    ctx.beginPath(); pts.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); ctx.closePath();
    ctx.fillStyle = U.rgba(color, alpha * 0.22); ctx.fill();
    ctx.strokeStyle = U.rgba(color, alpha * 0.18); ctx.lineWidth = 2.5; ctx.stroke();  // pigment pooling at the edge
  }
}
function inkPath(ctx, pts, upTo, color, width) {
  const n = Math.max(2, Math.floor(pts.length * U.clamp(upTo)));
  ctx.strokeStyle = color; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  for (let i = 1; i < n; i++) { ctx.lineWidth = width * (0.7 + 0.3 * U.noise2(i * 0.05, 1)); ctx.beginPath(); ctx.moveTo(...pts[i - 1]); ctx.lineTo(...pts[i]); ctx.stroke(); }
}
