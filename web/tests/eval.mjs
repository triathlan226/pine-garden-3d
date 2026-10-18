// node tests/eval.mjs "<js expression returning JSON-able>"  -> prints result
import { chromium } from '@playwright/test';
const [expr, url = 'http://127.0.0.1:5173/?autostart&nointro'] = process.argv.slice(2);
const browser = await chromium.launch({ headless: true, args: ['--use-angle=d3d11', '--enable-gpu', '--ignore-gpu-blocklist'] });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
const errs = [];
page.on('pageerror', (e) => errs.push(e.message));
await page.goto(url);
await page.waitForFunction(() => window.__pg && window.__pg.ready, null, { timeout: 120000 });
await page.waitForTimeout(2500);
console.log(JSON.stringify(await page.evaluate(expr), null, 1));
if (errs.length) console.log('ERR', errs);
await browser.close();
