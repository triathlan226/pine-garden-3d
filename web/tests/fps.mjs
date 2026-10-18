// node tests/fps.mjs "<query>" "<js tweak>"  -> fps at the facade viewpoint
import { chromium } from '@playwright/test';
const [q = '', tweak = '', w = 1440, h = 900] = process.argv.slice(2);
const b = await chromium.launch({ headless: true, args: ['--use-angle=d3d11', '--enable-gpu', '--ignore-gpu-blocklist'] });
const page = await b.newPage({ viewport: { width: +w, height: +h } });
await page.goto('http://127.0.0.1:5173/?autostart&nointro&fixeddpr&' + q);
await page.waitForFunction(() => window.__pg && window.__pg.ready, null, { timeout: 120000 });
await page.evaluate("__pg.teleportTo('facade'); __pg.hotspots.hide();" + tweak);
await page.waitForTimeout(2500);
const r = await page.evaluate(() => new Promise((ok) => { let n = 0; const t0 = performance.now(); const f = () => { n++; if (performance.now() - t0 < 5000) requestAnimationFrame(f); else ok((n / 5).toFixed(1) + ' fps, calls ' + __pg.stats.calls + ', tris ' + __pg.stats.tris); }; requestAnimationFrame(f); }));
console.log(q, '|', tweak.slice(0, 60), '=>', r);
await b.close();
