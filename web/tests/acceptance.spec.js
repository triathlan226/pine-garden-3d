// Acceptance tests (see 任務 §5). Run: npm run build && npx playwright test
import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const OUT = path.resolve('..', 'renders', 'acceptance');
fs.mkdirSync(OUT, { recursive: true });
const IGNORE = [/X4122/, /GPU stall/, /Automatic fallback to software WebGL/, /WebGL.*performance/i];

function watchConsole(page) {
  const errors = [];
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    const t = m.text();
    if (!IGNORE.some((r) => r.test(t))) errors.push(t);
  });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  page.on('requestfailed', (r) => errors.push('requestfailed: ' + r.url()));
  return errors;
}

async function boot(page, query = 'autostart&nointro') {
  await page.goto('./?' + query);
  await page.waitForFunction(() => window.__pg && window.__pg.ready, null, { timeout: 120000 });
  await page.waitForTimeout(1200);
}

const shot = (page, name, info) => page.screenshot({ path: path.join(OUT, `${info.project.name}-${name}.png`) });

// screen position of a hotspot marker (for real click / tap picking)
async function markerScreen(page, id) {
  return page.evaluate((id) => {
    const it = __pg.hotspots.byId(id);
    const v = it.glow.getWorldPosition(new __pg.THREE.Vector3()).project(__pg.camera);
    return { x: (v.x + 1) / 2 * innerWidth, y: (1 - v.y) / 2 * innerHeight, z: v.z };
  }, id);
}

test('home, panels and minimap render without console errors', async ({ page }, info) => {
  const errors = watchConsole(page);
  await page.goto('./');
  await page.waitForSelector('#btn-start:visible', { timeout: 120000 });
  await shot(page, '01-home', info);
  await page.click('#btn-start');
  await page.waitForFunction(() => window.__pg && window.__pg.introActive, null, { timeout: 30000 });
  await page.waitForTimeout(4000);
  await shot(page, '02-intro', info);
  await page.click('#btn-skip');
  await page.waitForTimeout(1500);
  await shot(page, '03-free-roam', info);

  // three hotspot panels, opened the way a visitor would (click / tap on the marker)
  for (const [k, id] of [['04', 'facade'], ['05', 'seaview'], ['06', 'stairs']]) {
    await page.evaluate((id) => { const it = __pg.hotspots.byId(id); __pg.hotspots.hide();
      const w = it.world, lk = it.look; const d = new __pg.THREE.Vector3(w.x - lk.x, 0, w.z - lk.z).normalize();
      __pg.player.teleport(w.x + d.x * 3.2, w.y + 0.3, w.z + d.z * 3.2, Math.atan2(-(w.x - (w.x + d.x * 3.2)), -(w.z - (w.z + d.z * 3.2)))); __pg.player.pitch = -0.1; }, id);
    await page.waitForTimeout(700);
    const s = await markerScreen(page, id);
    if (info.project.name === 'mobile') await page.touchscreen.tap(s.x, s.y); else await page.mouse.click(s.x, s.y);
    await page.waitForTimeout(800);
    let open = await page.evaluate(() => __pg.hotspots.open);
    if (open !== id) { // marker hidden behind something: fall back to the proximity prompt / E key
      await page.evaluate((id) => __pg.teleportTo(id), id);
      await page.waitForTimeout(500);
      open = await page.evaluate(() => __pg.hotspots.open);
    }
    expect(open).toBe(id);
    const title = await page.textContent('#panel-title');
    const want = await page.evaluate((id) => __pg.hotspots.byId(id).h.title, id);
    expect(title).toBe(want);
    // text must not overflow the panel horizontally
    const overflow = await page.evaluate(() => { const p = document.querySelector('.panel-scroll'); return p.scrollWidth - p.clientWidth; });
    expect(overflow).toBeLessThanOrEqual(1);
    await shot(page, `${k}-panel-${id}`, info);
  }
  await page.click('#panel-close');
  await page.click('#minimap-toggle');
  await page.waitForTimeout(600);
  await shot(page, '07-minimap', info);
  // teleport through the minimap: click hotspot #1 on the enlarged map
  const hit = await page.evaluate(() => { const h = __pg.minimap._hits.find((q) => q.id === 'entrance'); const r = document.getElementById('minimap-canvas').getBoundingClientRect(); return { x: r.left + h.x / 360 * r.width, y: r.top + h.y / 360 * r.height }; });
  await page.mouse.click(hit.x, hit.y);
  await page.waitForTimeout(500);
  expect(await page.evaluate(() => __pg.hotspots.open)).toBe('entrance');
  expect(errors, errors.join('\n')).toEqual([]);
});

test('every hotspot opens with the content from hotspots.json', async ({ page }) => {
  const errors = watchConsole(page);
  await boot(page);
  const ids = await page.evaluate(() => __pg.content.hotspots.map((h) => h.id));
  expect(ids.length).toBeGreaterThanOrEqual(10);
  expect(ids.length).toBeLessThanOrEqual(14);
  for (const id of ids) {
    await page.evaluate((id) => __pg.teleportTo(id), id);
    await page.waitForTimeout(150);
    const r = await page.evaluate((id) => {
      const h = __pg.hotspots.byId(id).h;
      return {
        open: __pg.hotspots.open, title: document.getElementById('panel-title').textContent, want: h.title,
        badge: document.getElementById('panel-badge').textContent, cat: h.category,
        nsrc: document.querySelectorAll('#panel-sources a').length, wantSrc: h.sources.length,
        body: document.getElementById('panel-body').textContent.length, summary: h.summary.length,
      };
    }, id);
    expect(r.open).toBe(id);
    expect(r.title).toBe(r.want);
    expect(r.badge).toBe(r.cat);
    expect(r.nsrc).toBe(r.wantSrc);
    expect(r.body).toBeGreaterThanOrEqual(150);
    if (r.cat === '史實') expect(r.wantSrc).toBeGreaterThan(0);
  }
  expect(errors, errors.join('\n')).toEqual([]);
});

test('auto tour walks the whole route without sticking or falling', async ({ page }, info) => {
  test.skip(info.project.name === 'mobile', 'route physics is identical on mobile; run once');
  const errors = watchConsole(page);
  await boot(page, 'autostart&nointro&fixeddpr');
  await page.evaluate(() => {
    window.__track = { minY: Infinity, maxPush: 0, samples: [] };
    __pg.tour.on('stop-at', (id) => window.__track.samples.push(id));
    __pg.startTour({ timeScale: 4, dwell: 0.4 });
    const loop = () => {
      const p = __pg.player.pos;
      window.__track.minY = Math.min(window.__track.minY, p.y);
      if (__pg.tour.active) requestAnimationFrame(loop);
    };
    loop();
  });
  await page.waitForFunction(() => !__pg.tour.active, null, { timeout: 200000, polling: 500 });
  const r = await page.evaluate(() => ({ stats: __pg.tour.stats, falls: __pg.player.falls, track: window.__track, ending: !document.getElementById('ending').hidden }));
  fs.writeFileSync(path.join(OUT, 'tour-result.json'), JSON.stringify(r, null, 1));
  expect(r.stats.stuck, 'stuck events').toBe(0);
  expect(r.falls, 'falls').toBe(0);
  expect(r.track.minY).toBeGreaterThan(-1);
  expect(r.stats.stops.length).toBe(14);
  expect(r.stats.minClear, 'capsule clearance (m)').toBeGreaterThan(0.05);
  await page.waitForTimeout(1200);
  await shot(page, '08-tour-ending', info);
  expect(errors, errors.join('\n')).toEqual([]);
});

test('walls and the site boundary hold when walking into them', async ({ page }, info) => {
  test.skip(info.project.name === 'mobile', 'same physics');
  await boot(page, 'autostart&nointro&fixeddpr');
  const walk = async (from, yaw, ms, key = 'w') => {
    await page.evaluate(([f, yaw]) => { __pg.player.teleport(f[0], f[1], f[2], yaw); }, [from, yaw]);
    await page.keyboard.down(key);
    await page.keyboard.down('Shift');
    await page.waitForTimeout(ms);
    await page.keyboard.up('Shift');
    await page.keyboard.up(key);
    await page.waitForTimeout(300);
    return page.evaluate(() => __pg.player.pos.toArray());
  };
  // 1F arcade: walk north (into the rooms' back wall at plan y = -2.95, i.e. world z = 2.95)
  let p = await walk([6, 0.6, 4.4], 0, 2500);
  expect(p[2]).toBeGreaterThan(2.95 + 0.25);
  // 2F balcony: walk south into the balustrade (front face plan y = -5.75 -> world z = 5.75)
  p = await walk([6, 4.6, 4.2], Math.PI, 2500);
  expect(p[2]).toBeLessThan(5.75);
  expect(p[1]).toBeGreaterThan(4.0);
  // south cliff: walk south off the lawn edge for 6 s
  p = await walk([0, 0.3, 30], Math.PI, 6000);
  const inside = await page.evaluate((p) => {
    const poly = __pg.world.meta.walk_boundary; const x = p[0], y = -p[2];
    let c = false; for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) { const [xi, yi] = poly[i], [xj, yj] = poly[j]; if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) c = !c; } return c;
  }, p);
  expect(inside).toBe(true);
  expect(p[1]).toBeGreaterThan(-0.5);
  // tree trunk: walk into a signature pine
  const tree = await page.evaluate(() => __pg.world.trees.find((t) => t.z === 'garden' && t.t.startsWith('B')).p);
  p = await walk([tree[0], tree[1] + 0.3, tree[2] + 3], 0, 2500);
  expect(Math.hypot(p[0] - tree[0], p[2] - tree[2])).toBeGreaterThan(0.45);
});

test('performance sample', async ({ page }, info) => {
  await boot(page, 'autostart&nointro');
  // walk a little so LODs and grass update, then sample fps for 8 s at three views
  const views = [['facade', 0], ['pines', 0], ['balcony', 0]];
  const res = { project: info.project.name, views: [] };
  for (const [id] of views) {
    await page.evaluate((id) => { __pg.teleportTo(id); __pg.hotspots.hide(); }, id);
    await page.waitForTimeout(2500);
    const f = await page.evaluate(() => new Promise((ok) => {
      let n = 0; const t0 = performance.now();
      const tick = () => { n++; if (performance.now() - t0 < 6000) requestAnimationFrame(tick); else ok({ fps: n / ((performance.now() - t0) / 1000), calls: __pg.stats.calls, tris: __pg.stats.tris, dpr: __pg.dpr }); };
      requestAnimationFrame(tick);
    }));
    res.views.push({ id, ...f });
  }
  res.tier = await page.evaluate(() => ({ name: __pg.tier.name, gpu: __pg.tier.gpu, loadMs: __pg.loadMs }));
  res.bytes = await page.evaluate(() => performance.getEntriesByType('resource').reduce((a, r) => a + (r.transferSize || r.encodedBodySize || 0), 0));
  fs.writeFileSync(path.join(OUT, `perf-${info.project.name}.json`), JSON.stringify(res, null, 1));
  for (const v of res.views) expect(v.calls).toBeLessThanOrEqual(150);
});

test('content pages and 2D fallback load cleanly', async ({ page }, info) => {
  const errors = watchConsole(page);
  for (const p of ['about.html', 'sources.html', 'credits.html', 'list.html?reason=webgl']) {
    await page.goto('./' + p);
    await page.waitForTimeout(800);
    await shot(page, '09-page-' + p.split('.')[0], info);
  }
  expect(await page.locator('.card').count()).toBeGreaterThanOrEqual(10);
  const wide = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
  expect(wide).toBeLessThanOrEqual(1);
  expect(errors, errors.join('\n')).toEqual([]);
});

test('reduced motion skips the intro', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('./');
  await page.waitForSelector('#btn-start:visible', { timeout: 120000 });
  await page.click('#btn-start');
  await page.waitForTimeout(800);
  expect(await page.evaluate(() => __pg.introActive)).toBe(false);
  expect(await page.evaluate(() => __pg.reducedMotion)).toBe(true);
});
