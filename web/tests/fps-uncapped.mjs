// Uncapped frame rate (no vsync) + optional CPU throttling, mobile or desktop viewport.
// node tests/fps-uncapped.mjs <desktop|mobile> <cpuRate> [base url]
import { chromium } from '@playwright/test';
const [mode = 'desktop', rate = '1', base = 'http://127.0.0.1:5173/'] = process.argv.slice(2);
const b = await chromium.launch({ headless: true, args: ['--use-angle=d3d11', '--enable-gpu', '--ignore-gpu-blocklist', '--disable-gpu-vsync', '--disable-frame-rate-limit'] });
const ctx = mode === 'mobile'
  ? await b.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true, userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1' })
  : await b.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const cdp = await ctx.newCDPSession(page);
await page.goto(base + '?autostart&nointro&fixeddpr');
await page.waitForFunction(() => window.__pg && window.__pg.ready, null, { timeout: 120000 });
await cdp.send('Emulation.setCPUThrottlingRate', { rate: +rate });
const out = [];
for (const id of ['facade', 'pines', 'balcony', 'today']) {
  await page.evaluate((id) => { __pg.teleportTo(id); __pg.hotspots.hide(); }, id);
  await page.waitForTimeout(2000);
  const r = await page.evaluate(() => new Promise((ok) => { let n = 0; const t0 = performance.now(); const f = () => { n++; if (performance.now() - t0 < 5000) requestAnimationFrame(f); else ok({ fps: +(n / ((performance.now() - t0) / 1000)).toFixed(1), calls: __pg.stats.calls, tris: __pg.stats.tris, dpr: +__pg.dpr.toFixed(2), tier: __pg.tier.name }); }; requestAnimationFrame(f); }));
  out.push({ id, ...r });
}
console.log(JSON.stringify({ mode, cpuThrottle: +rate, views: out }));
await b.close();
