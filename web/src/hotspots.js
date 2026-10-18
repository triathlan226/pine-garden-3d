import * as THREE from 'three';
import { planToWorld, paragraphs, el } from './content.js';

const NEAR = 4.2;

function glowTexture() {
  const c = document.createElement('canvas');
  c.width = c.height = 128;
  const g = c.getContext('2d');
  const grd = g.createRadialGradient(64, 64, 4, 64, 64, 62);
  grd.addColorStop(0, 'rgba(255,240,200,1)');
  grd.addColorStop(0.25, 'rgba(255,200,110,0.85)');
  grd.addColorStop(0.6, 'rgba(240,160,60,0.25)');
  grd.addColorStop(1, 'rgba(240,160,60,0)');
  g.fillStyle = grd;
  g.fillRect(0, 0, 128, 128);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

function numberTexture(n, cls) {
  const c = document.createElement('canvas');
  c.width = c.height = 96;
  const g = c.getContext('2d');
  const col = { fact: '#2f7a5f', legend: '#b5701d', recon: '#6a5aa8' }[cls];
  g.fillStyle = '#fff';
  g.beginPath(); g.arc(48, 48, 40, 0, Math.PI * 2); g.fill();
  g.fillStyle = col;
  g.beginPath(); g.arc(48, 48, 33, 0, Math.PI * 2); g.fill();
  g.fillStyle = '#fff';
  g.font = 'bold 40px system-ui, sans-serif';
  g.textAlign = 'center'; g.textBaseline = 'middle';
  g.fillText(String(n), 48, 51);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

export class Hotspots {
  constructor({ scene, camera, content, ui, reducedMotion }) {
    this.scene = scene;
    this.camera = camera;
    this.content = content;
    this.ui = ui;
    this.reduced = reducedMotion;
    this.items = [];
    this.near = null;
    this.open = null;
    this.visited = new Set();
    this.listeners = {};
    const glow = glowTexture();
    const group = new THREE.Group();
    group.name = 'hotspots';
    for (const h of content.hotspots) {
      const [x, y, z] = planToWorld(h.plan);
      const g = new THREE.Group();
      g.position.set(x, y + 1.25, z);
      const s1 = new THREE.Sprite(new THREE.SpriteMaterial({ map: glow, blending: THREE.AdditiveBlending, depthWrite: false, transparent: true, fog: false }));
      s1.scale.set(1.5, 1.5, 1);
      const s2 = new THREE.Sprite(new THREE.SpriteMaterial({ map: numberTexture(h.order, h.cls), depthWrite: false, transparent: true, fog: false }));
      s2.scale.set(0.42, 0.42, 1);
      s2.renderOrder = 2;
      g.add(s1, s2);
      g.userData.id = h.id;
      group.add(g);
      const look = new THREE.Vector3(...planToWorld(h.look));
      this.items.push({ h, group: g, glow: s1, label: s2, world: new THREE.Vector3(x, y, z), look });
    }
    scene.add(group);
    this.group = group;
    this._ray = new THREE.Raycaster();
    this._bindPanel();
  }

  on(ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); }
  emit(ev, ...a) { (this.listeners[ev] || []).forEach((f) => f(...a)); }
  byId(id) { return this.items.find((i) => i.h.id === id); }

  update(t, feet) {
    let best = null, bd = NEAR;
    for (const it of this.items) {
      const d = Math.hypot(it.world.x - feet.x, it.world.z - feet.z) + Math.abs(it.world.y - feet.y) * 1.5;
      if (d < bd) { bd = d; best = it; }
      const pulse = this.reduced ? 1 : 1 + 0.12 * Math.sin(t * 2.4 + it.world.x);
      const nearK = d < NEAR ? 1.35 : 1;
      it.glow.scale.setScalar(1.5 * pulse * nearK);
      const close = Math.min(1, Math.max(0, (d - 1.2) / 1.8));   // fade out when standing on it
      it.glow.material.opacity = (this.visited.has(it.h.id) ? 0.55 : 1) * close;
      it.label.material.opacity = close;
    }
    if (best !== this.near) {
      this.near = best;
      this.emit('near', best);
    }
  }

  pick(x, y) {
    const v = new THREE.Vector2((x / innerWidth) * 2 - 1, -(y / innerHeight) * 2 + 1);
    this._ray.setFromCamera(v, this.camera);
    const hits = this._ray.intersectObjects(this.items.map((i) => i.glow), false);
    if (hits.length) {
      const it = this.items.find((i) => i.glow === hits[0].object);
      if (it && hits[0].distance < 60) { this.show(it.h.id); return true; }
    }
    return false;
  }

  _bindPanel() {
    const u = this.ui;
    u.panelClose.addEventListener('click', () => this.hide());
    // swipe-down to close the bottom sheet
    let sy = null;
    u.panel.addEventListener('touchstart', (e) => { if (u.panelScroll.scrollTop <= 0) sy = e.touches[0].clientY; }, { passive: true });
    u.panel.addEventListener('touchmove', (e) => { if (sy !== null && e.touches[0].clientY - sy > 90) { sy = null; this.hide(); } }, { passive: true });
    u.panel.addEventListener('touchend', () => { sy = null; });
  }

  show(id) {
    const it = this.byId(id);
    if (!it) return;
    const h = it.h;
    const u = this.ui;
    u.badge.className = 'badge ' + h.cls;
    u.badge.textContent = h.category;
    u.index.textContent = `第 ${h.order} 站／共 ${this.items.length} 站`;
    u.title.textContent = h.title;
    u.summary.textContent = h.summary;
    u.body.replaceChildren(...paragraphs(h.body).map((p) => el('p', { text: p })));
    u.sources.replaceChildren(...(h.sourceList.length ? h.sourceList.map((s) => el('li', {}, el('a', { href: s.url, target: '_blank', rel: 'noopener', text: s.title }), el('span', { class: 'small', text: `（${s.level}）` })))
      : [el('li', { text: h.category === '史實' ? '（缺來源，請補充）' : '本條為推測或流傳說法，無一手來源。' })]));
    u.panel.hidden = false;
    u.panel.dataset.id = id;
    u.panelScroll.scrollTop = 0;
    this.open = id;
    this.visited.add(id);
    this.emit('open', it);
  }

  hide() {
    if (!this.open) return;
    this.ui.panel.hidden = true;
    const id = this.open;
    this.open = null;
    this.emit('close', id);
  }
}
