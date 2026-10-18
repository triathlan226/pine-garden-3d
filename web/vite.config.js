import { defineConfig } from 'vite';
import { resolve } from 'node:path';
import fs from 'node:fs';

// content/*.json lives at the repo root so it can be edited without touching code.
// Dev: served from /content/*. Build: copied to dist/content/.
const CONTENT = resolve(__dirname, '..', 'content');

function contentPlugin() {
  return {
    name: 'pine-garden-content',
    configureServer(server) {
      server.middlewares.use('/content', (req, res, next) => {
        const f = resolve(CONTENT, decodeURIComponent(req.url.split('?')[0]).replace(/^\//, ''));
        if (f.startsWith(CONTENT) && fs.existsSync(f) && fs.statSync(f).isFile()) {
          res.setHeader('Content-Type', 'application/json; charset=utf-8');
          res.setHeader('Cache-Control', 'no-store');
          fs.createReadStream(f).pipe(res);
        } else next();
      });
    },
    writeBundle(opts) {
      const out = resolve(opts.dir, 'content');
      fs.mkdirSync(out, { recursive: true });
      for (const f of fs.readdirSync(CONTENT)) if (f.endsWith('.json')) fs.copyFileSync(resolve(CONTENT, f), resolve(out, f));
    },
  };
}

export default defineConfig({
  // BASE is set by CI for GitHub Pages (/repo-name/); relative paths work everywhere else.
  base: process.env.BASE || './',
  plugins: [contentPlugin()],
  // cache-busting token for the un-hashed assets in public/ (models, textures)
  define: { __ASSET_V__: JSON.stringify(Date.now().toString(36)) },
  build: {
    target: 'es2020',
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      input: {
        index: resolve(__dirname, 'index.html'),
        about: resolve(__dirname, 'about.html'),
        sources: resolve(__dirname, 'sources.html'),
        credits: resolve(__dirname, 'credits.html'),
        list: resolve(__dirname, 'list.html'),
      },
    },
  },
  server: { port: 5173, host: '127.0.0.1' },
  preview: { port: 4173, host: '127.0.0.1' },
});
