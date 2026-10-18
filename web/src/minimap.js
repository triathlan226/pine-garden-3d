// Plan-view minimap: pre-rendered top orthographic image from Blender + live player/hotspot overlay.
// The image is plan-up (+Y of the plan frame = image up). A north arrow shows true north.
const BASE = import.meta.env.BASE_URL;
const COLORS = { fact: '#2f7a5f', legend: '#b5701d', recon: '#6a5aa8' };

export class Minimap {
  constructor({ root, canvas, toggle, bounds, hotspots, onTeleport, northBearingOfX = 61.5 }) {
    this.root = root;
    this.c = canvas;
    this.g = canvas.getContext('2d');
    this.b = bounds;              // plan bounds x0,x1,y0,y1
    this.hotspots = hotspots;
    this.onTeleport = onTeleport;
    this.img = new Image();
    this.img.src = BASE + 'textures/minimap.webp?v=' + (typeof __ASSET_V__ !== 'undefined' ? __ASSET_V__ : 'dev');
    this.zoom = 1.7;             // small view zooms on the garden
    // true north in plan coords: bearing 0 -> angle (0 - northBearingOfX) clockwise from +X
    const a = (-northBearingOfX * Math.PI) / 180;
    this.north = [Math.cos(a), -Math.sin(a)];
    this.player = { x: 0, y: 0, yaw: 0 };
    toggle.addEventListener('click', () => this.setBig(!root.classList.contains('big')));
    canvas.addEventListener('click', (e) => this._click(e));
    this.big = false;
  }

  setBig(v) {
    this.big = v;
    this.root.classList.toggle('big', v);
    this.zoom = v ? 1.15 : 1.7;
  }

  // plan -> canvas pixel
  _xf() {
    const W = this.c.width;
    const span = (this.b.x1 - this.b.x0) / this.zoom;
    const cx = this.big ? -12 : this.player.x, cy = this.big ? -2 : this.player.y;
    const k = W / span;
    return { k, map: (x, y) => [(x - cx) * k + W / 2, (cy - y) * k + W / 2], cx, cy, span };
  }

  draw() {
    const g = this.g, W = this.c.width;
    const { k, map, cx, cy, span } = this._xf();
    g.fillStyle = '#4d6440';
    g.fillRect(0, 0, W, W);
    if (this.img.complete && this.img.naturalWidth) {
      const iw = this.img.naturalWidth;
      const s = iw / (this.b.x1 - this.b.x0);   // image px per metre
      const sx = (cx - span / 2 - this.b.x0) * s;
      const sy = (this.b.y1 - (cy + span / 2)) * s;
      g.drawImage(this.img, sx, sy, span * s, span * s, 0, 0, W, W);
    }
    // hotspots
    this._hits = [];
    for (const it of this.hotspots.items) {
      const [px, py] = map(it.h.plan[0], it.h.plan[1]);
      if (px < -10 || py < -10 || px > W + 10 || py > W + 10) continue;
      const r = this.big ? 15 : 12;
      g.beginPath(); g.arc(px, py, r, 0, Math.PI * 2);
      g.fillStyle = '#fff'; g.fill();
      g.beginPath(); g.arc(px, py, r - 3, 0, Math.PI * 2);
      g.fillStyle = COLORS[it.h.cls]; g.fill();
      g.fillStyle = '#fff'; g.font = `bold ${this.big ? 15 : 13}px system-ui, sans-serif`;
      g.textAlign = 'center'; g.textBaseline = 'middle';
      g.fillText(String(it.h.order), px, py + 1);
      this._hits.push({ id: it.h.id, x: px, y: py, r: r + 6 });
    }
    // player
    const [ppx, ppy] = map(this.player.x, this.player.y);
    const yaw = this.player.yaw;   // camera yaw (three): forward = (-sin, -cos) in (x, z) => plan (−sin, +cos)
    const fx = -Math.sin(yaw), fy = Math.cos(yaw);
    g.save();
    g.translate(ppx, ppy);
    g.rotate(Math.atan2(fx, fy));
    g.beginPath();
    g.moveTo(0, 0); g.arc(0, 0, 46, -Math.PI / 2 - 0.5, -Math.PI / 2 + 0.5); g.closePath();
    g.fillStyle = 'rgba(255,240,180,0.35)'; g.fill();
    g.beginPath(); g.moveTo(0, -13); g.lineTo(8, 9); g.lineTo(0, 4); g.lineTo(-8, 9); g.closePath();
    g.fillStyle = '#ffcc4d'; g.strokeStyle = '#3a2a00'; g.lineWidth = 2; g.fill(); g.stroke();
    g.restore();
    // north arrow
    g.save();
    g.translate(30, 30);
    g.rotate(Math.atan2(this.north[0], this.north[1]));
    g.fillStyle = 'rgba(0,0,0,0.45)'; g.beginPath(); g.arc(0, 0, 20, 0, Math.PI * 2); g.fill();
    g.fillStyle = '#fff'; g.beginPath(); g.moveTo(0, -15); g.lineTo(6, 5); g.lineTo(-6, 5); g.closePath(); g.fill();
    g.font = 'bold 12px system-ui'; g.textAlign = 'center'; g.fillText('N', 0, 14);
    g.restore();
    // scale bar
    const m10 = 10 * k;
    g.fillStyle = 'rgba(0,0,0,0.5)'; g.fillRect(W - m10 - 22, W - 26, m10 + 14, 18);
    g.fillStyle = '#fff'; g.fillRect(W - m10 - 15, W - 14, m10, 3);
    g.font = '11px system-ui'; g.textAlign = 'center'; g.fillText('10 m', W - m10 / 2 - 15, W - 19);
  }

  _click(e) {
    const r = this.c.getBoundingClientRect();
    const x = ((e.clientX - r.left) / r.width) * this.c.width;
    const y = ((e.clientY - r.top) / r.height) * this.c.height;
    const hit = (this._hits || []).find((h) => Math.hypot(h.x - x, h.y - y) < h.r);
    if (hit) { this.onTeleport(hit.id); if (this.big) this.setBig(false); }
    else this.setBig(!this.big);
  }

  setPlayer(x, y, yaw) { this.player.x = x; this.player.y = y; this.player.yaw = yaw; }
}
