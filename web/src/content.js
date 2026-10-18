// Shared content helpers (3D app and the 2D pages).
const BASE = import.meta.env.BASE_URL;

export const CATEGORY_CLASS = { '史實': 'fact', '流傳說法': 'legend', '推測重建': 'recon' };

async function getJSON(path) {
  const r = await fetch(BASE + path, { cache: 'no-cache' });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

export async function loadContent() {
  const [h, s, t] = await Promise.all([
    getJSON('content/hotspots.json'),
    getJSON('content/sources.json'),
    getJSON('content/tour.json'),
  ]);
  const sources = Object.fromEntries(s.sources.map((x) => [x.id, x]));
  const hotspots = h.hotspots.map((x) => ({
    ...x,
    cls: CATEGORY_CLASS[x.category] || 'recon',
    sourceList: (x.sources || []).map((id) => sources[id]).filter(Boolean),
  }));
  // order hotspots by their first appearance in the tour
  const order = t.points.filter((p) => p.stop).map((p) => p.stop);
  hotspots.forEach((x) => { x.order = order.indexOf(x.id) + 1 || 99; });
  hotspots.sort((a, b) => a.order - b.order);
  return { hotspots, sources, sourceList: s.sources, checked: s.checked, tour: t };
}

/** plan coordinates [X, Y, Z] -> three.js world [x, y, z] */
export function planToWorld(p) {
  return [p[0], p[2] || 0, -p[1]];
}

export function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v;
    else if (k === 'text') e.textContent = v;
    else e.setAttribute(k, v);
  }
  for (const c of kids) if (c != null) e.append(c);
  return e;
}

export function paragraphs(text) {
  // split on 。 boundaries into readable paragraphs of ~2-3 sentences
  const sentences = text.split(/(?<=。)/).filter((s) => s.trim());
  const out = [];
  let cur = '';
  for (const s of sentences) {
    cur += s;
    if (cur.length > 70) { out.push(cur); cur = ''; }
  }
  if (cur) out.push(cur);
  return out;
}
