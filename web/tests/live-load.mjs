// Cold-load timing of a deployed URL: node tests/live-load.mjs <url> [runs]
import { chromium } from '@playwright/test';
const [url, runs = 3] = process.argv.slice(2);
const b = await chromium.launch({ headless: true, args: ['--use-angle=d3d11', '--enable-gpu', '--ignore-gpu-blocklist'] });
const res = [];
for (let i = 0; i < +runs; i++) {
  const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errs = []; page.on('pageerror', (e) => errs.push(e.message));
  const t0 = Date.now();
  await page.goto(url + (url.includes('?') ? '&' : '?') + 'autostart&nointro');
  await page.waitForFunction(() => window.__pg && window.__pg.ready, null, { timeout: 120000 });
  const ready = Date.now() - t0;
  const r = await page.evaluate(() => ({ bytes: performance.getEntriesByType('resource').reduce((a, x) => a + (x.transferSize || 0), 0), n: performance.getEntriesByType('resource').length, ttfb: performance.getEntriesByType('navigation')[0].responseStart }));
  await page.waitForTimeout(3000);
  const fps = await page.evaluate(() => __pg.stats.fps);
  res.push({ run: i + 1, readyMs: ready, ttfbMs: Math.round(r.ttfb), MB: +(r.bytes / 1048576).toFixed(2), requests: r.n, fps: +fps.toFixed(1), errors: errs.length });
  await ctx.close();
}
console.log(JSON.stringify(res));
await b.close();
