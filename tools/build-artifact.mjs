// Build a copy of the site for publishing as a Claude Artifact.
//   node tools/build-artifact.mjs   -> web/dist-artifact/
// The Artifact host wraps the main page in its own <!doctype><html><head><body> skeleton,
// so index.html is reduced to a fragment (title, styles, scripts and the body markup).
// The other pages are served as they are (each keeps its own doctype).
import { execSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const WEB = path.join(ROOT, 'web');
const OUT = path.join(WEB, 'dist-artifact');
fs.rmSync(OUT, { recursive: true, force: true });
execSync(`npx vite build --outDir dist-artifact --emptyOutDir`, { cwd: WEB, stdio: 'inherit', env: { ...process.env, BASE: './' } });

const f = path.join(OUT, 'index.html');
const html = fs.readFileSync(f, 'utf8');
const head = html.match(/<head>([\s\S]*?)<\/head>/)[1];
const body = html.match(/<body[^>]*>([\s\S]*?)<\/body>/)[1];
const keep = (head.match(/<title>[\s\S]*?<\/title>|<meta name="description"[^>]*>|<meta name="theme-color"[^>]*>|<link[^>]*>|<script[\s\S]*?<\/script>/g) || []).join('\n');
const fragment = `${keep}\n<script>document.body.classList.add('app');</script>\n${body.trim()}\n`;
fs.writeFileSync(f, fragment);

const files = [];
(function walk(d) {
  for (const e of fs.readdirSync(d, { withFileTypes: true })) {
    const p = path.join(d, e.name);
    if (e.isDirectory()) walk(p); else if (p !== f) files.push(path.relative(OUT, p).replace(/\\/g, '/'));
  }
})(OUT);
const total = files.reduce((a, r) => a + fs.statSync(path.join(OUT, r)).size, fs.statSync(f).size);
fs.writeFileSync(path.join(OUT, 'files.json'), JSON.stringify(files, null, 1));
console.log(`artifact build: ${files.length + 1} files, ${(total / 1048576).toFixed(2)} MB`);
