import * as THREE from 'three';
import { loadContent, planToWorld } from './content.js';
import { loadWorld, windUniforms } from './world.js';
import { Player, EYE } from './player.js';
import { Controls } from './controls.js';
import { Hotspots } from './hotspots.js';
import { Tour } from './tour.js';
import { Minimap } from './minimap.js';
import { Ambience } from './audio.js';

document.body.classList.add('app');
const $ = (id) => document.getElementById(id);
const params = new URLSearchParams(location.search);
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches || params.has('reduced');

// ---------- capability check & quality tier ----------
function detectTier() {
  const probe = document.createElement('canvas');
  const gl = probe.getContext('webgl2');
  if (!gl) return null;
  const dbg = gl.getExtension('WEBGL_debug_renderer_info');
  const gpu = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : '';
  const mobile = /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent) || (navigator.maxTouchPoints > 1 && matchMedia('(pointer: coarse)').matches);
  const software = /SwiftShader|llvmpipe|softpipe|Software/i.test(gpu);
  const weakMobile = /Mali-[GT]?[4-7]\d|Adreno \(TM\) [3-5]\d\d|PowerVR/i.test(gpu);
  const integrated = /Intel|UHD|Iris|Apple M|AMD Radeon\(TM\) Graphics/i.test(gpu);
  let name = mobile ? (weakMobile ? 'low' : 'medium') : integrated ? 'medium' : 'high';
  if (software) name = 'low';
  if (['low', 'medium', 'high'].includes(params.get('q'))) name = params.get('q');
  const dpr = window.devicePixelRatio || 1;
  const T = {
    high: { maxDpr: Math.min(dpr, 2), shadows: 2048, grass: 26000, lodDist: 75, aniso: 8, normalMaps: true, ao: true, aa: true },
    medium: { maxDpr: Math.min(dpr, mobile ? 1.5 : 1.25), shadows: 0, grass: mobile ? 9000 : 12000, lodDist: 55, aniso: 4, normalMaps: true, ao: true, aa: !mobile },
    low: { maxDpr: Math.min(dpr, 1), shadows: 0, grass: 4000, lodDist: 38, aniso: 1, normalMaps: false, ao: false, aa: false },
  }[name];
  return { name, gpu, mobile, software, ...T };
}

const tier = detectTier();
if (!tier) {
  location.replace('./list.html?reason=webgl');
  throw new Error('WebGL2 not available');
}

// ---------- renderer / scene ----------
const canvas = $('view');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: tier.aa, powerPreference: 'high-performance' });
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.AgXToneMapping;
renderer.toneMappingExposure = 1.05;
renderer.shadowMap.type = THREE.PCFShadowMap;
let dpr = tier.maxDpr;
renderer.setPixelRatio(dpr);
renderer.setSize(innerWidth, innerHeight, false);
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(70, innerWidth / innerHeight, 0.08, 16000);
function resize() {
  const a = innerWidth / innerHeight;
  camera.aspect = a;
  camera.fov = a < 0.8 ? 82 : a < 1.2 ? 74 : 68;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight, false);
}
addEventListener('resize', resize);
resize();

// ---------- loading ----------
const manager = new THREE.LoadingManager();
const bar = $('bar-fill');
manager.onProgress = (url, done, total) => {
  const p = Math.round((done / Math.max(total, 1)) * 100);
  bar.style.width = p + '%';
  bar.parentElement.setAttribute('aria-valuenow', p);
  if (!$('loader-status').dataset.err) $('loader-status').textContent = `載入場景中… ${p}%（${loadStep}）`;
};
manager.onError = (url) => console.warn('load error', url);

const toast = (html, ms = 4500) => {
  const t = $('toast');
  t.innerHTML = html;
  t.hidden = false;
  clearTimeout(toast._t);
  if (ms) toast._t = setTimeout(() => { t.hidden = true; }, ms);
  return t;
};

const ui = {
  panel: $('panel'), panelClose: $('panel-close'), panelScroll: document.querySelector('.panel-scroll'),
  badge: $('panel-badge'), index: $('panel-index'), title: $('panel-title'), summary: $('panel-summary'),
  body: $('panel-body'), sources: $('panel-sources'),
};

function showLoadError(e) {
  console.error(e);
  $('loader-status').dataset.err = '1';
  $('loader-status').innerHTML = `載入失敗：${String(e && e.message || e).slice(0, 160)}<br>請重新整理，或<a href="./list.html" style="color:#e2a65f">改看 2D 熱點清單</a>。`;
}
addEventListener('unhandledrejection', (ev) => { if (!window.__pg) showLoadError(ev.reason); });
addEventListener('error', (ev) => { if (!window.__pg) showLoadError(ev.error || ev.message); });
let loadStep = '開始';
window.__loadStep = (t) => { loadStep = t; const el = $('loader-status'); if (el && !el.dataset.err) el.textContent = `載入場景中…（${t}）`; };
setTimeout(() => { if (!window.__pg) showLoadError(new Error(`超過 40 秒仍停在「${loadStep}」`)); }, 40000);
const t0 = performance.now();
let content, world;
try {
  [content, world] = await Promise.all([
    loadContent(),
    loadWorld({ renderer, scene, manager, tier }),
  ]);
} catch (e) {
  showLoadError(e);
  throw e;
}
world.setShadows(tier.shadows);
const loadMs = performance.now() - t0;

window.__loadStep('啟動互動');
const player = new Player(world.collider);
const controls = new Controls(canvas, $('joystick'));
const hotspots = new Hotspots({ scene, camera, content, ui, reducedMotion });
const tour = new Tour({ player, hotspots, content, reducedMotion });
const audio = new Ambience();
const minimap = new Minimap({
  root: $('minimap'), canvas: $('minimap-canvas'), toggle: $('minimap-toggle'), bounds: world.meta.minimap,
  hotspots, onTeleport: (id) => teleportTo(id),
});

// start position: outside the east gate, facing the garden
{
  const s = new THREE.Vector3(...planToWorld(content.tour.start));
  const f = new THREE.Vector3(...planToWorld(content.tour.points[0].p));
  player.teleport(s.x, s.y + 0.1, s.z, Math.atan2(-(f.x - s.x), -(f.z - s.z)));
}

function teleportTo(id) {
  const it = hotspots.byId(id);
  if (!it) return;
  if (tour.active) tour.stop('teleport');
  const w = it.world, lk = it.look;
  const back = new THREE.Vector3(w.x - lk.x, 0, w.z - lk.z).normalize().multiplyScalar(0.6);
  player.teleport(w.x + back.x, w.y + 0.3, w.z + back.z, Math.atan2(-(lk.x - w.x), -(lk.z - w.z)));
  player.pitch = 0;
  hotspots.show(id);
}

// ---------- UI wiring ----------
$('loader-status').textContent = '載入完成';
bar.style.width = '100%';
$('start-wrap').hidden = false;
$('btn-start').focus();

let intro = null;
function enter() {
  $('loader').classList.add('done');
  setTimeout(() => { $('loader').hidden = true; }, 700);
  for (const id of ['hud-top', 'hud-actions', 'minimap']) $(id).hidden = false;
  if (controls.touch) $('joystick').hidden = false;
  if (!reducedMotion && !params.has('nointro')) startIntro();
  else afterIntro();
}
$('btn-start').addEventListener('click', enter);
if (params.has('autostart')) enter();

function startIntro() {
  const pts = [[260, -700, 150], [140, -360, 80], [70, -130, 32], [44, -50, 9], [39.5, -28, 1.6]]
    .map((p) => new THREE.Vector3(...planToWorld(p)));
  const looks = [[0, 0, 0], [0, -5, 2], [0, -8, 4], [5, -12, 4], [0, -10, 4]].map((p) => new THREE.Vector3(...planToWorld(p)));
  intro = { curve: new THREE.CatmullRomCurve3(pts), looks, t: 0, dur: 13 };
  $('btn-skip').hidden = false;
}
function afterIntro() {
  intro = null;
  $('btn-skip').hidden = true;
  toast(`${controls.touch ? '左下搖桿移動、右側滑動轉視角' : 'WASD 移動、滑鼠拖曳轉視角、E 查看解說'}。<button class="btn primary" id="t-audio">開啟環境音</button>`, 9000);
  const b = document.getElementById('t-audio');
  if (b) b.onclick = () => { setAudio(true); $('toast').hidden = true; };
}
$('btn-skip').addEventListener('click', afterIntro);

async function setAudio(v) {
  const on = await audio.toggle(v);
  const b = $('btn-audio');
  b.textContent = on ? '🔊' : '🔇';
  b.setAttribute('aria-pressed', String(on));
  b.setAttribute('aria-label', on ? '環境音（開啟中）' : '環境音（目前靜音）');
}
$('btn-audio').addEventListener('click', () => setAudio());

function setTourUI(on) {
  const b = $('btn-tour');
  b.setAttribute('aria-pressed', String(on));
  b.textContent = on ? '■ 停止導覽' : '▶ 自動導覽';
  $('btn-next').hidden = !on;
  $('tour-status').hidden = !on;
}
function startTour(opts) {
  $('ending').hidden = true;
  hotspots.hide();
  tour.begin(opts);
}
$('btn-tour').addEventListener('click', () => (tour.active ? tour.stop('button') : startTour()));
$('btn-next').addEventListener('click', () => tour.skipWait());
controls.on('toggle-tour', () => (tour.active ? tour.stop('button') : startTour()));
controls.on('toggle-map', () => minimap.setBig(!minimap.big));
tour.on('start', () => { setTourUI(true); $('tour-status').textContent = '自動導覽中・隨時移動即可改回自由漫遊'; });
tour.on('stop', (why) => { setTourUI(false); if (why === 'user') toast('已切換為自由漫遊'); });
tour.on('stop-at', (id) => {
  const it = hotspots.byId(id);
  $('tour-status').textContent = `自動導覽：第 ${it.h.order}／${hotspots.items.length} 站　${it.h.title}`;
});
tour.on('finish', () => {
  setTourUI(false);
  setTimeout(() => { $('ending').hidden = false; $('btn-again').focus(); }, 900);
});
const interrupt = () => { if (tour.active && !intro) tour.stop('user'); };
controls.on('user-move', interrupt);
controls.on('user-look', interrupt);
controls.on('interact', () => { if (hotspots.near && !intro) hotspots.show(hotspots.near.h.id); });
controls.on('escape', () => { hotspots.hide(); $('help').hidden = true; });
controls.on('pick', (x, y) => { if (!intro) hotspots.pick(x, y); });
$('prompt').addEventListener('click', () => { if (hotspots.near) hotspots.show(hotspots.near.h.id); });
$('btn-again').addEventListener('click', () => startTour());
$('btn-free').addEventListener('click', () => { $('ending').hidden = true; hotspots.hide(); });
$('btn-help').addEventListener('click', () => { $('help').hidden = false; $('help-close').focus(); });
$('help-close').addEventListener('click', () => { $('help').hidden = true; });
hotspots.on('near', (it) => {
  const p = $('prompt');
  if (!it || tour.active) { p.hidden = true; return; }
  p.innerHTML = `${controls.touch ? '點這裡' : '按 <b>E</b>'} 查看：<b>${it.h.title}</b>`;
  p.hidden = false;
});
hotspots.on('open', () => { $('prompt').hidden = true; $('toast').hidden = true; });

// ---------- main loop ----------
const clock = new THREE.Timer();
const eye = new THREE.Vector3();
const fwd = new THREE.Vector3();
const stats = { fps: 0, frames: 0, acc: 0, history: [], calls: 0, tris: 0, low: 0 };
let lodT = 0, dynT = 0, elapsed = 0;

function wrapAngle(a) { return Math.atan2(Math.sin(a), Math.cos(a)); }

function frame() {
  clock.update();
  const dt = Math.min(clock.getDelta(), 0.1);
  elapsed += dt;
  windUniforms.uTime.value = elapsed;
  const inp = controls.sample();

  if (intro) {
    intro.t += dt / intro.dur;
    const t = Math.min(1, intro.t);
    const e = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
    camera.position.copy(intro.curve.getPointAt(e));
    const seg = Math.min(intro.looks.length - 2, Math.floor(e * (intro.looks.length - 1)));
    const lt = e * (intro.looks.length - 1) - seg;
    const look = intro.looks[seg].clone().lerp(intro.looks[seg + 1], lt);
    camera.lookAt(look);
    if (t >= 1) {
      const d = new THREE.Vector3(); camera.getWorldDirection(d);
      player.yaw = Math.atan2(-d.x, -d.z); player.pitch = 0;
      afterIntro();
    }
  } else {
    let wish = new THREE.Vector3();
    if (tour.active) {
      const o = tour.update(dt);
      wish = o.wish;
      if (o.yawTarget !== null) player.yaw += wrapAngle(o.yawTarget - player.yaw) * Math.min(1, dt * 3.2 * (tour.timeScale || 1));
      if (o.pitchTarget !== undefined) player.pitch += (o.pitchTarget - player.pitch) * Math.min(1, dt * 2.5 * (tour.timeScale || 1));
    } else {
      player.yaw -= inp.look.dx;
      player.pitch = THREE.MathUtils.clamp(player.pitch - inp.look.dy, -1.2, 1.2);
      const sp = inp.fast ? 3.2 : 1.6;
      const sy = Math.sin(player.yaw), cy = Math.cos(player.yaw);
      // forward = (-sin, 0, -cos); right = (cos, 0, -sin)
      wish.set((-sy * inp.y + cy * inp.x) * sp, 0, (-cy * inp.y - sy * inp.x) * sp);
    }
    player.update(tour.active ? dt * (tour.timeScale || 1) / (tour.timeScale || 1) : dt, wish);
    player.eye(eye);
    camera.position.copy(eye);
    camera.rotation.set(player.pitch, player.yaw, 0, 'YXZ');
  }
  camera.updateMatrixWorld();
  hotspots.update(elapsed, player.pos);

  lodT -= dt;
  if (lodT <= 0) { world.updateTreeLOD(camera.position); world.followSun(camera.position); lodT = 0.25; }
  minimap.setPlayer(player.pos.x, -player.pos.z, player.yaw);
  minimap.draw();
  // sea sound on the 2F balcony or at the sea-view deck
  const pp = player.pos;
  const balcony = pp.y > 3.5 && pp.z > 2.9 ? 1 : 0;
  const deck = Math.max(0, 1 - Math.hypot(pp.x - 26, pp.z - 40) / 9);
  audio.update(Math.max(balcony, deck), 1 - balcony * 0.3);

  renderer.render(scene, camera);
  stats.calls = renderer.info.render.calls;
  stats.tris = renderer.info.render.triangles;

  // fps + dynamic resolution
  stats.frames++; stats.acc += dt;
  if (stats.acc >= 1) {
    stats.fps = stats.frames / stats.acc;
    stats.history.push(+stats.fps.toFixed(1));
    if (stats.history.length > 120) stats.history.shift();
    stats.frames = 0; stats.acc = 0;
    dynT += 1;
    if (!params.has('fixeddpr') && dynT >= 2 && !intro) {
      const f = stats.fps;
      const target = tier.mobile ? 30 : 55;
      if (f < target - 2 && dpr > 0.7) { dpr = Math.max(0.7, dpr * 0.88); renderer.setPixelRatio(dpr); resize(); dynT = 0; }
      else if (f < 24 && renderer.shadowMap.enabled) { world.setShadows(0); dynT = 0; }
      else if (f > 57 && dpr < tier.maxDpr && dynT > 6) { dpr = Math.min(tier.maxDpr, dpr * 1.1); renderer.setPixelRatio(dpr); resize(); dynT = 0; }
      if (f < 15 && dpr <= 0.71) {
        stats.low++;
        if (stats.low === 8) toast('這台裝置跑 3D 有點吃力，<a href="./list.html">改看 2D 熱點清單</a>？', 12000);
      } else stats.low = 0;
    }
  }
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

// ---------- test / debug hooks ----------
window.__pg = {
  THREE, scene, camera, renderer, player, tour, hotspots, minimap, world, content, tier, stats,
  loadMs, reducedMotion,
  get dpr() { return dpr; },
  startTour, teleportTo, enter, skipIntro: () => afterIntro(),
  get introActive() { return !!intro; },
  ready: true,
};
document.body.dataset.ready = '1';
