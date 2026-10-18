// Quick visual check: node tests/shot.mjs [url] [out.png] [w] [h] [js-to-run]
import { chromium } from '@playwright/test';

const [url = 'http://127.0.0.1:5173/?autostart&nointro', out = '../renders/_web.png', w = '1440', h = '900', js = ''] = process.argv.slice(2);
const browser = await chromium.launch({
  headless: true,
  args: ['--use-angle=d3d11', '--enable-gpu', '--ignore-gpu-blocklist', '--enable-webgl'],
});
const page = await browser.newPage({ viewport: { width: +w, height: +h } });
const errors = [];
page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') errors.push(`[${m.type()}] ${m.text().slice(0, 300)}`); });
page.on('pageerror', (e) => errors.push('[pageerror] ' + e.message));
await page.goto(url);
await page.waitForFunction(() => window.__pg && window.__pg.ready, null, { timeout: 120000 });
if (js) await page.evaluate(js);
await page.waitForTimeout(3500);
await page.screenshot({ path: out });
const info = await page.evaluate(() => ({ fps: __pg.stats.fps.toFixed(1), calls: __pg.stats.calls, tris: __pg.stats.tris, tier: __pg.tier.name, gpu: __pg.tier.gpu, dpr: __pg.dpr, load: Math.round(__pg.loadMs), pos: __pg.player.pos.toArray().map((v) => v.toFixed(2)) }));
console.log(JSON.stringify(info));
console.log(errors.slice(0, 15).join('\n'));
await browser.close();
