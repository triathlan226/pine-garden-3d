// Compress Blender exports for the web.
//   node tools/optimize.mjs
// - models/*_raw.glb  -> models/*.glb   (dedup + weld + EXT_meshopt_compression; collision kept uncompressed-float)
// - blender/bake/*.png -> models/*.webp (lightmaps / AO)
// - CC0 textures + generated atlases -> textures/*.webp
// - renders/minimap.jpg -> textures/minimap.webp
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(path.join(ROOT, 'web', 'package.json'));
const imp = (m) => import(pathToFileURL(require.resolve(m)).href);
const { NodeIO } = await imp('@gltf-transform/core');
const { ALL_EXTENSIONS } = await imp('@gltf-transform/extensions');
const { dedup, prune, weld, meshopt, quantize } = await imp('@gltf-transform/functions');
const { MeshoptEncoder, MeshoptDecoder } = await imp('meshoptimizer');
const sharp = (await imp('sharp')).default;

const MODELS = path.join(ROOT, 'web', 'public', 'models');
const TEX = path.join(ROOT, 'web', 'public', 'textures');
fs.mkdirSync(TEX, { recursive: true });

await MeshoptEncoder.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({
  'meshopt.encoder': MeshoptEncoder, 'meshopt.decoder': MeshoptDecoder,
});

const kb = (f) => (fs.statSync(f).size / 1024).toFixed(0) + ' KB';

for (const name of ['scene', 'trees', 'collision']) {
  const src = path.join(ROOT, 'blender', 'export', `${name}_raw.glb`);
  if (!fs.existsSync(src)) { console.warn('missing', src); continue; }
  const doc = await io.read(src);
  const steps = [dedup(), weld(), prune({ keepAttributes: true, keepLeaves: true })];
  if (name !== 'collision') {
    steps.push(quantize({ quantizePosition: 16, quantizeTexcoord: 14, quantizeNormal: 10, quantizeColor: 8 }));
    steps.push(meshopt({ encoder: MeshoptEncoder, level: 'medium' }));
  }
  await doc.transform(...steps);
  const out = path.join(MODELS, `${name}.glb`);
  await io.write(out, doc);
  console.log(`${name}.glb`, kb(src), '->', kb(out));
}

// lightmaps (sRGB-encoded irradiance) + AO
const BAKE = path.join(ROOT, 'blender', 'bake');
if (fs.existsSync(BAKE)) {
  for (const f of fs.readdirSync(BAKE).filter((f) => f.endsWith('.png'))) {
    const out = path.join(MODELS, f.replace('.png', '.webp'));
    await sharp(path.join(BAKE, f)).webp({ quality: f.startsWith('ao') ? 80 : 90, effort: 5 }).toFile(out);
    console.log(f, '->', kb(out));
  }
}

// CC0 material textures (ambientCG, 1K): colour + normal
const SRC = path.join(ROOT, 'blender', 'textures_src');
const meta = JSON.parse(fs.readFileSync(path.join(MODELS, 'scene_meta.json'), 'utf8'));
const used = new Set(Object.values(meta.materials).flatMap((m) => [m.tex, m.blend]).filter((t) => t && !t.startsWith('@')));
for (const f of fs.readdirSync(TEX)) if (/_(color|normal)\.webp$/.test(f) && !used.has(f.replace(/_(color|normal)\.webp$/, ''))) fs.rmSync(path.join(TEX, f));
for (const folder of fs.readdirSync(SRC).filter((f) => used.has(f))) {
  const base = path.join(SRC, folder, `${folder}_1K-JPG`);
  const jobs = [['Color', 'color', 82], ['NormalGL', 'normal', 88]];
  for (const [kind, suffix, q] of jobs) {
    const f = `${base}_${kind}.jpg`;
    if (!fs.existsSync(f)) continue;
    const out = path.join(TEX, `${folder}_${suffix}.webp`);
    await sharp(f).resize(1024, 1024, { fit: 'fill' }).webp({ quality: q, effort: 5 }).toFile(out);
  }
}
// generated alpha atlases
const GEN = path.join(ROOT, 'blender', 'textures_gen');
for (const f of fs.readdirSync(GEN).filter((f) => f.endsWith('.png'))) {
  const out = path.join(TEX, f.replace('.png', '.webp'));
  await sharp(path.join(GEN, f)).webp({ quality: 85, alphaQuality: 90, effort: 5 }).toFile(out);
}
const mm = path.join(ROOT, 'renders', 'minimap.jpg');
if (fs.existsSync(mm)) await sharp(mm).resize(720, 720).webp({ quality: 80 }).toFile(path.join(TEX, 'minimap.webp'));

let total = 0;
for (const dir of [MODELS, TEX]) for (const f of fs.readdirSync(dir)) {
  if (f.includes('_raw')) continue;
  total += fs.statSync(path.join(dir, f)).size;
}
console.log('public/models + public/textures total:', (total / 1048576).toFixed(2), 'MB');
