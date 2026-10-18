import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { MeshBVH, acceleratedRaycast } from 'three-mesh-bvh';

THREE.Mesh.prototype.raycast = acceleratedRaycast;
const BASE = import.meta.env.BASE_URL;
const V = typeof __ASSET_V__ !== 'undefined' ? __ASSET_V__ : 'dev';
const A = (p) => (V ? `${BASE}${p}?v=${V}` : `${BASE}${p}`);
// Claude Artifact hosting does not serve .glb, so that build ships embedded .gltf.json instead
const MODEL_EXT = typeof __MODEL_EXT__ !== 'undefined' ? __MODEL_EXT__ : '.glb';

const SKY_TOP = new THREE.Color('#3f74c4');
const SKY_HORIZON = new THREE.Color('#d6e2ea');
export const HAZE = new THREE.Color('#c9d7de');

/** Shared uniforms for wind animation. */
export const windUniforms = { uTime: { value: 0 } };

/** Clone geometry with float attributes (meshopt-quantized int attributes can't take applyMatrix4). */
export function floatGeometry(src) {
  const g = new THREE.BufferGeometry();
  for (const [k, a] of Object.entries(src.attributes)) {
    const n = a.count, s = a.itemSize, out = new Float32Array(n * s);
    for (let i = 0; i < n; i++) for (let j = 0; j < s; j++) out[i * s + j] = a.getComponent(i, j);
    g.setAttribute(k, new THREE.BufferAttribute(out, s));
  }
  if (src.index) g.setIndex(src.index.clone());
  return g;
}

function pointInPoly(x, y, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i]; const [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi + 1e-12) + xi) inside = !inside;
  }
  return inside;
}

function bakedPatch(mat) {
  // Static, light-mapped surfaces: ignore the realtime sun (its light and shadows are in the lightmap).
  mat.onBeforeCompile = (sh) => {
    sh.fragmentShader = sh.fragmentShader.replace(
      '#include <lights_fragment_begin>',
      THREE.ShaderChunk.lights_fragment_begin.replace(/NUM_DIR_LIGHTS > 0/g, 'NUM_DIR_LIGHTS < 0').replace(/NUM_HEMI_LIGHTS > 0/g, 'NUM_HEMI_LIGHTS < 0'));
    if (mat.userData.lawn) {
      sh.uniforms.map2 = { value: mat.userData.map2 };
      sh.vertexShader = sh.vertexShader
        .replace('#include <common>', '#include <common>\nattribute vec4 color;\nvarying float vDirt;\nvarying vec2 vWorldXZ;')
        .replace('#include <begin_vertex>', '#include <begin_vertex>\nvDirt = color.r;\nvWorldXZ = (modelMatrix * vec4(position, 1.0)).xz;');
      sh.fragmentShader = sh.fragmentShader
        .replace('#include <common>', '#include <common>\nuniform sampler2D map2;\nvarying float vDirt;\nvarying vec2 vWorldXZ;')
        .replace('#include <map_fragment>', `#include <map_fragment>
          vec3 soil = texture2D(map2, vMapUv * 0.8).rgb * vec3(0.85, 0.78, 0.68);
          float macro = texture2D(map, vWorldXZ * 0.013).g;
          diffuseColor.rgb *= 0.78 + 0.45 * macro;
          diffuseColor.rgb = mix(diffuseColor.rgb, soil, smoothstep(0.15, 0.75, vDirt));`);
    }
  };
  mat.customProgramCacheKey = () => (mat.userData.lawn ? 'baked-lawn' : 'baked');
}

function windPatch(mat, { instanced, strength = 1 }) {
  mat.onBeforeCompile = (sh) => {
    sh.uniforms.uTime = windUniforms.uTime;
    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', '#include <common>\nuniform float uTime;')
      .replace('#include <begin_vertex>', `#include <begin_vertex>
        {
          vec3 wp0 = ${instanced ? 'instanceMatrix[3].xyz' : 'position'};
          float ph = wp0.x * 0.37 + wp0.z * 0.23;
          float h = max(0.0, position.y - ${instanced ? '2.5' : '0.0'});
          float k = ${instanced ? `h * h * 0.0009 * ${strength.toFixed(2)}` : `0.012 * ${strength.toFixed(2)}`};
          float gust = 0.6 + 0.4 * sin(uTime * 0.21 + ph * 0.1);
          transformed.x += sin(uTime * 1.1 + ph) * k * gust;
          transformed.z += cos(uTime * 0.9 + ph * 1.3) * k * 0.7 * gust;
        }`);
  };
  mat.customProgramCacheKey = () => `wind-${instanced}-${strength}`;
}

const step = (t) => window.__loadStep && window.__loadStep(t);

export async function loadWorld({ renderer, scene, manager, tier }) {
  step('讀取場景資料');
  const meta = await (await fetch(A('models/scene_meta.json'))).json();
  const treesJson = await (await fetch(A('models/trees.json'))).json();
  const gltfLoader = new GLTFLoader(manager);
  if (MODEL_EXT === '.glb') {
    // only the .glb build is meshopt-compressed; the decoder instantiates WebAssembly on import
    const { MeshoptDecoder } = await import('three/addons/libs/meshopt_decoder.module.js');
    gltfLoader.setMeshoptDecoder(MeshoptDecoder);
  }
  const texLoader = new THREE.TextureLoader(manager);
  const maxAniso = Math.min(renderer.capabilities.getMaxAnisotropy(), tier.aniso);

  const texCache = new Map();
  function tex(file, srgb = true) {
    if (!texCache.has(file)) {
      const clones = [];
      const t = texLoader.load(A(file), () => { for (const c of clones) c.needsUpdate = true; });
      t.userData.clones = clones;
      t.wrapS = t.wrapT = THREE.RepeatWrapping;
      t.colorSpace = srgb ? THREE.SRGBColorSpace : THREE.NoColorSpace;
      t.anisotropy = maxAniso;
      texCache.set(file, t);
    }
    return texCache.get(file);
  }
  function tiled(file, tu, tv, srgb = true) {
    const src = tex(file, srgb);
    const t = src.clone();
    t.repeat.set(1 / tu, 1 / tv);
    if (src.image && src.image.complete !== false && src.image.width) t.needsUpdate = true;
    else src.userData.clones.push(t);
    return t;
  }
  function lightmap(name) {
    if (!name) return null;
    const t = tex(`models/${name}.webp`, true);
    t.wrapS = t.wrapT = THREE.ClampToEdgeWrapping;
    t.flipY = false;
    t.channel = 1;
    return t;
  }

  step('下載模型');
  const loadModel = async (name) => {
    if (MODEL_EXT === '.glb') return gltfLoader.loadAsync(A(`models/${name}.glb`));
    // .gltf.json with a base64 buffer: decode it here and parse as an in-memory GLB, so no
    // data: URL ever goes through fetch (blocked by some sandboxed hosts' CSP).
    const json = await (await fetch(A(`models/${name}${MODEL_EXT}`))).json();
    const b64 = json.buffers[0].uri.split(',')[1];
    delete json.buffers[0].uri;
    const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    let jsonBytes = new TextEncoder().encode(JSON.stringify(json));
    const jpad = (4 - (jsonBytes.length % 4)) % 4, bpad = (4 - (bin.length % 4)) % 4;
    const total = 12 + 8 + jsonBytes.length + jpad + 8 + bin.length + bpad;
    const glb = new Uint8Array(total);
    const dv = new DataView(glb.buffer);
    dv.setUint32(0, 0x46546c67, true); dv.setUint32(4, 2, true); dv.setUint32(8, total, true);
    dv.setUint32(12, jsonBytes.length + jpad, true); dv.setUint32(16, 0x4e4f534a, true);
    glb.set(jsonBytes, 20); glb.fill(0x20, 20 + jsonBytes.length, 20 + jsonBytes.length + jpad);
    const o = 20 + jsonBytes.length + jpad;
    dv.setUint32(o, bin.length + bpad, true); dv.setUint32(o + 4, 0x004e4942, true);
    glb.set(bin, o + 8);
    return gltfLoader.parseAsync(glb.buffer, '');
  };
  const [sceneGltf, treesGltf, colGltf] = await Promise.all(['scene', 'trees', 'collision'].map(loadModel));

  // ---------- environment ----------
  const sunDir = new THREE.Vector3(...meta.sun_dir).normalize();
  const sky = makeSky(sunDir);
  scene.add(sky);
  const pmrem = new THREE.PMREMGenerator(renderer);
  const envScene = new THREE.Scene();
  envScene.add(makeSky(sunDir).clone());
  const envRT = pmrem.fromScene(envScene, 0.02);
  scene.environment = envRT.texture;
  scene.environmentIntensity = 0.22;
  scene.fog = new THREE.Fog(HAZE, 140, 9000);

  const hemi = new THREE.HemisphereLight('#cfe0ff', '#5d6b45', 0.16);
  scene.add(hemi);
  const sun = new THREE.DirectionalLight('#fff1dc', 2.6);
  sun.position.copy(sunDir).multiplyScalar(80);
  scene.add(sun, sun.target);

  // ---------- materials ----------
  const md = meta.materials;
  const mats = {};
  const LM_INT = Math.PI * meta.lm_scale * 0.8;
  function staticMaterial(name, objName) {
    const key = name + '|' + objName;
    if (mats[key]) return mats[key];
    const d = md[name] || { tint: [0.6, 0.6, 0.6], rough: 0.9, tile: [1, 1] };
    const [lmName, aoName] = meta.lightmaps[objName] || [null, null];
    let m;
    if (name === 'lamp') {
      m = new THREE.MeshBasicMaterial({ color: new THREE.Color(1.0, 0.86, 0.62).multiplyScalar(2.2) });
    } else if (name === 'pergola_roof') {
      m = new THREE.MeshStandardMaterial({ color: '#5f9b6c', transparent: true, opacity: 0.45, roughness: 0.3, side: THREE.DoubleSide, depthWrite: false });
    } else if (name === 'far') {
      m = new THREE.MeshLambertMaterial({ vertexColors: true, side: THREE.DoubleSide });
    } else if (name === 'sea') {
      m = makeSeaMaterial(sunDir);
    } else if (name === 'ivy') {
      m = new THREE.MeshStandardMaterial({ map: tex('textures/ivy_atlas.webp'), alphaTest: 0.45, side: THREE.DoubleSide, vertexColors: true, roughness: 0.75 });
      m.map.wrapS = m.map.wrapT = THREE.ClampToEdgeWrapping;
      windPatch(m, { instanced: false, strength: 1 });
    } else {
      const p = { color: new THREE.Color(...d.tint), roughness: d.rough, metalness: d.metal || 0 };
      if (d.tex && !d.tex.startsWith('@')) {
        p.map = tiled(`textures/${d.tex}_color.webp`, d.tile[0], d.tile[1]);
        if (tier.normalMaps) {
          p.normalMap = tiled(`textures/${d.tex}_normal.webp`, d.tile[0], d.tile[1], false);
          p.normalScale = new THREE.Vector2(0.8, 0.8);
        }
      }
      if (name === 'glass') { p.roughness = 0.06; p.envMapIntensity = 2.2; }
      m = new THREE.MeshStandardMaterial(p);
      if (lmName) {
        m.lightMap = lightmap(lmName);
        m.lightMapIntensity = LM_INT;
        if (aoName && tier.ao) { m.aoMap = lightmap(aoName); m.aoMapIntensity = 0.45; }
        if (name === 'lawn' || name === 'lawn_far') {
          m.userData.lawn = true;
          m.userData.map2 = tiled('textures/Ground037_color.webp', d.tile[0], d.tile[1]);
        }
        m.envMapIntensity = name === 'glass' ? 1.6 : 0.12;
        bakedPatch(m);
      }
    }
    m.name = name;
    mats[key] = m;
    return m;
  }

  const root = sceneGltf.scene;
  const groundMeshes = [];
  let terrainCore = null;
  root.traverse((o) => {
    if (!o.isMesh) return;
    const objName = (o.parent && o.parent !== root && !o.parent.isScene ? o.parent.name : o.name).replace(/_\d+$/, '');
    const matName = (o.material && o.material.name || '').replace(/\.\d+$/, '');
    o.userData.obj = objName;
    o.material = staticMaterial(matName, objName);
    o.castShadow = false;
    o.receiveShadow = matName === 'ivy';
    o.matrixAutoUpdate = false;
    o.updateMatrix();
    if (objName === 'Ground') groundMeshes.push(o);
    if (objName === 'TerrainCore') terrainCore = o;
    if (objName === 'Sea') { o.renderOrder = -1; o.frustumCulled = false; }
  });
  scene.add(root);
  root.updateMatrixWorld(true);

  step('建立碰撞網格');
  // ---------- collision ----------
  const colGeoms = [];
  colGltf.scene.updateMatrixWorld(true);
  colGltf.scene.traverse((o) => {
    if (!o.isMesh) return;
    const g = floatGeometry(o.geometry);
    g.applyMatrix4(o.matrixWorld);
    for (const k of Object.keys(g.attributes)) if (k !== 'position') g.deleteAttribute(k);
    colGeoms.push(g.index ? g.toNonIndexed() : g);
  });
  const colGeom = mergeGeometries(colGeoms, false);
  colGeom.boundsTree = new MeshBVH(colGeom);
  const collider = new THREE.Mesh(colGeom, new THREE.MeshBasicMaterial({ wireframe: true, color: 0xff00ff }));
  collider.visible = false;
  collider.name = 'collider';
  scene.add(collider);

  step('配置松樹');
  // ---------- trees (instanced, two LODs) ----------
  const types = {};
  treesGltf.scene.updateMatrixWorld(true);
  treesGltf.scene.traverse((o) => {
    if (!o.isMesh) return;
    const nm = (o.parent && !o.parent.isScene && o.parent !== treesGltf.scene ? o.parent.name : o.name);
    const m = nm.match(/Tree_(\w\d)_LOD(\d)/);
    if (!m) return;
    const [, t, lod] = m;
    types[t] = types[t] || { 0: [], 1: [] };
    const g = floatGeometry(o.geometry);
    g.applyMatrix4(o.matrixWorld);
    types[t][lod].push({ geometry: g, matName: (o.material.name || '').replace(/\.\d+$/, '') });
  });
  const barkMat = new THREE.MeshStandardMaterial({ map: tiled('textures/Bark012_color.webp', 1.2, 2.4), color: new THREE.Color(...md.bark.tint).multiplyScalar(1.15), roughness: 0.95, vertexColors: true });
  const needleMat = new THREE.MeshStandardMaterial({ map: tex('textures/pine_atlas.webp'), alphaTest: 0.42, side: THREE.DoubleSide, vertexColors: true, roughness: 0.85, color: '#e6f0d8' });
  const shrubMat = new THREE.MeshStandardMaterial({ map: tex('textures/shrub_atlas.webp'), alphaTest: 0.45, side: THREE.DoubleSide, vertexColors: true, roughness: 0.85 });
  for (const t of [needleMat.map, shrubMat.map]) t.wrapS = t.wrapT = THREE.ClampToEdgeWrapping;
  windPatch(needleMat, { instanced: true, strength: 1 });
  windPatch(shrubMat, { instanced: true, strength: 0.5 });
  const treeMat = (n) => (n === 'needles' ? needleMat : n === 'shrub' ? shrubMat : barkMat);

  const inst = treesJson.instances.map((q) => {
    const m = new THREE.Matrix4().compose(
      new THREE.Vector3(...q.p), new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), q.r), new THREE.Vector3(q.s, q.s, q.s));
    return { ...q, matrix: m, pos: new THREE.Vector3(...q.p) };
  });
  const lodGroups = [];
  const treesRoot = new THREE.Group();
  treesRoot.name = 'trees';
  for (const [t, lods] of Object.entries(types)) {
    const list = inst.filter((q) => q.t === t);
    if (!list.length) continue;
    const grp = { type: t, list, meshes: { 0: [], 1: [] } };
    for (const lod of [0, 1]) {
      for (const part of lods[lod]) {
        const im = new THREE.InstancedMesh(part.geometry, treeMat(part.matName), list.length);
        im.castShadow = part.matName !== 'bark' || lod === 0;
        im.receiveShadow = false;
        im.count = 0;
        im.frustumCulled = false;
        treesRoot.add(im);
        grp.meshes[lod].push(im);
      }
    }
    lodGroups.push(grp);
  }
  scene.add(treesRoot);
  const LOD_DIST2 = tier.lodDist * tier.lodDist;
  const lastLod = new THREE.Vector3(1e9, 0, 0);
  let lodKey = '';
  function updateTreeLOD(camPos) {
    // Re-upload instance matrices only when the near/far split actually changes:
    // frequent instanceMatrix uploads stall the GPU on some drivers.
    if (camPos.distanceToSquared(lastLod) < 4) return;
    lastLod.copy(camPos);
    let key = '';
    for (const g of lodGroups) for (const q of g.list) key += q.z === 'garden' && q.pos.distanceToSquared(camPos) < LOD_DIST2 ? '1' : '0';
    if (key === lodKey) return;
    lodKey = key;
    for (const g of lodGroups) {
      let n0 = 0, n1 = 0;
      for (const q of g.list) {
        const near = q.z === 'garden' && q.pos.distanceToSquared(camPos) < LOD_DIST2;
        if (near) { for (const im of g.meshes[0]) im.setMatrixAt(n0, q.matrix); n0++; }
        else { for (const im of g.meshes[1]) im.setMatrixAt(n1, q.matrix); n1++; }
      }
      for (const im of g.meshes[0]) { im.count = n0; im.instanceMatrix.needsUpdate = true; }
      for (const im of g.meshes[1]) { im.count = n1; im.instanceMatrix.needsUpdate = true; }
    }
  }

  step('鋪設草地');
  // ---------- grass tufts (instanced, distance-faded) ----------
  const grass = makeGrass({ terrainCore, groundMeshes, walk: meta.walk_boundary, count: tier.grass, tex: tex('textures/grass_atlas.webp'),
    lm: lightmap('lm_core'), lmInt: LM_INT, rect: meta.lm_core_rect || [-108, 72, -72, 54] });
  if (grass) scene.add(grass);

  return {
    meta, sun, hemi, sky, collider, updateTreeLOD, grass, treesRoot, sunDir,
    trees: inst, materials: mats,
    setShadows(mapSize) {
      renderer.shadowMap.enabled = mapSize > 0;
      sun.castShadow = mapSize > 0;
      if (mapSize > 0) {
        sun.shadow.mapSize.set(mapSize, mapSize);
        const c = sun.shadow.camera;
        c.left = c.bottom = -32; c.right = c.top = 32; c.near = 1; c.far = 220;
        c.updateProjectionMatrix();
        sun.shadow.bias = -0.0006;
        sun.shadow.normalBias = 0.04;
        if (sun.shadow.map) { sun.shadow.map.dispose(); sun.shadow.map = null; }
      }
      scene.traverse((o) => { if (o.material) o.material.needsUpdate = true; });
    },
    followSun(camPos) {
      if (!sun.castShadow) return;
      sun.target.position.set(Math.round(camPos.x / 4) * 4, 0, Math.round(camPos.z / 4) * 4);
      sun.position.copy(sun.target.position).addScaledVector(sunDir, 100);
      sun.target.updateMatrixWorld();
    },
  };
}

function makeSky(sunDir) {
  const g = new THREE.SphereGeometry(15000, 32, 16);
  const m = new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, fog: false,
    uniforms: { top: { value: SKY_TOP }, horizon: { value: SKY_HORIZON }, sunDir: { value: sunDir } },
    vertexShader: `varying vec3 vDir; void main(){ vDir = normalize(position); vec4 p = projectionMatrix * modelViewMatrix * vec4(position,1.0); gl_Position = p.xyww; }`,
    fragmentShader: `uniform vec3 top; uniform vec3 horizon; uniform vec3 sunDir; varying vec3 vDir;
      void main(){
        float h = clamp(vDir.y, -1.0, 1.0);
        vec3 c = mix(horizon, top, pow(smoothstep(-0.02, 0.6, h), 0.65));
        c = mix(c, vec3(0.62, 0.66, 0.62), smoothstep(0.0, -0.08, h));
        float s = max(dot(normalize(vDir), normalize(sunDir)), 0.0);
        c += vec3(1.0, 0.86, 0.62) * (pow(s, 900.0) * 6.0 + pow(s, 12.0) * 0.18);
        gl_FragColor = vec4(c, 1.0);
        #include <colorspace_fragment>
      }`,
  });
  const mesh = new THREE.Mesh(g, m);
  mesh.frustumCulled = false;
  mesh.renderOrder = -10;
  mesh.name = 'sky';
  mesh.onBeforeRender = (r, s, cam) => mesh.position.copy(cam.position);
  return mesh;
}

function makeSeaMaterial(sunDir) {
  return new THREE.ShaderMaterial({
    fog: true,
    uniforms: THREE.UniformsUtils.merge([THREE.UniformsLib.fog, {
      uTime: windUniforms.uTime, sunDir: { value: sunDir },
      deep: { value: new THREE.Color('#0d3a52') }, skyc: { value: new THREE.Color('#9fbfd6') },
    }]),
    vertexShader: `varying vec3 vW; #include <fog_pars_vertex>
      void main(){ vec4 w = modelMatrix * vec4(position,1.0); vW = w.xyz; vec4 mvPosition = viewMatrix * w; gl_Position = projectionMatrix * mvPosition; #include <fog_vertex> }`.replace(/#include <(\w+)>/g, '\n#include <$1>\n'),
    fragmentShader: `uniform float uTime; uniform vec3 sunDir; uniform vec3 deep; uniform vec3 skyc; varying vec3 vW;
      #include <common>
      #include <fog_pars_fragment>
      void main(){
        vec2 p = vW.xz * 0.02;
        vec3 n = normalize(vec3(
          sin(p.x * 3.1 + uTime * 0.6) * 0.06 + sin(p.y * 5.3 - uTime * 0.8 + p.x) * 0.04 + sin((p.x + p.y) * 11.0 + uTime * 1.7) * 0.02,
          1.0,
          cos(p.y * 2.7 + uTime * 0.5) * 0.06 + cos(p.x * 6.1 + uTime * 0.9) * 0.03));
        vec3 v = normalize(cameraPosition - vW);
        float fres = pow(1.0 - max(dot(v, n), 0.0), 4.0);
        vec3 c = mix(deep, skyc, 0.15 + 0.7 * fres);
        vec3 r = reflect(-v, n);
        c += vec3(1.0, 0.9, 0.7) * pow(max(dot(r, normalize(sunDir)), 0.0), 220.0) * 2.5;
        gl_FragColor = vec4(c, 1.0);
        #include <colorspace_fragment>
        #include <fog_fragment>
      }`,
  });
}

function makeGrass({ terrainCore, groundMeshes, walk, count, tex, lm, lmInt, rect }) {
  if (!terrainCore || !count) return null;
  tex.wrapS = tex.wrapT = THREE.ClampToEdgeWrapping;
  const geo = terrainCore.geometry;
  const pos = geo.attributes.position;
  const col = geo.attributes.color;
  const mw = terrainCore.matrixWorld;
  // candidate vertices on the lawn (plan polygon is (x, -z))
  const cand = [];
  const v = new THREE.Vector3();
  for (let i = 0; i < pos.count; i++) {
    v.fromBufferAttribute(pos, i).applyMatrix4(mw);
    const dirt = col ? col.getX(i) : 0;
    if (dirt > 0.42) continue;
    if (!pointInPoly(v.x, -v.z, walk)) continue;
    cand.push([v.x, v.y, v.z]);
  }
  if (!cand.length) return null;
  // decks/paths: raycast down against the Ground meshes and drop tufts underneath
  const ray = new THREE.Raycaster();
  ray.firstHitOnly = true;
  for (const g of groundMeshes) if (!g.geometry.boundsTree) g.geometry.boundsTree = new MeshBVH(g.geometry);
  const tuft = new THREE.BufferGeometry();
  const W = 0.32, H = 0.34;
  const P = [], UV = [], IDX = [];
  for (let k = 0; k < 3; k++) {
    const a = (k * Math.PI) / 3;
    const cx = Math.cos(a) * W, cz = Math.sin(a) * W;
    const b = P.length / 3;
    P.push(-cx, 0, -cz, cx, 0, cz, cx, H, cz, -cx, H, -cz);
    const cu = (k % 2) * 0.5, cv = 0.5;
    UV.push(cu, cv, cu + 0.5, cv, cu + 0.5, 1, cu, 1);
    IDX.push(b, b + 1, b + 2, b, b + 2, b + 3);
  }
  tuft.setAttribute('position', new THREE.Float32BufferAttribute(P, 3));
  tuft.setAttribute('normal', new THREE.Float32BufferAttribute(new Array(P.length).fill(0).map((_, i) => (i % 3 === 1 ? 1 : 0)), 3));
  tuft.setAttribute('uv', new THREE.Float32BufferAttribute(UV, 2));
  tuft.setIndex(IDX);
  // Unlit, modulated by the terrain lightmap sampled at the tuft's world position, so the
  // grass carries the same baked pine shadows as the ground underneath.
  const mat = new THREE.MeshBasicMaterial({ map: tex, alphaTest: 0.4, side: THREE.DoubleSide, color: '#c8d6a8' });
  const fade = { value: new THREE.Vector2(22, 34) };
  mat.onBeforeCompile = (sh) => {
    sh.uniforms.uTime = windUniforms.uTime;
    sh.uniforms.uFade = fade;
    sh.uniforms.uLm = { value: lm };
    sh.uniforms.uLmInt = { value: lmInt / Math.PI };
    sh.uniforms.uRect = { value: new THREE.Vector4(...rect) };
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', '#include <common>\nuniform sampler2D uLm;\nuniform float uLmInt;\nvarying vec2 vLmUv;\nvarying float vH;')
      .replace('#include <map_fragment>', '#include <map_fragment>\ndiffuseColor.rgb *= texture2D(uLm, vLmUv).rgb * uLmInt * (0.7 + 0.4 * vH);');
    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', '#include <common>\nuniform float uTime;\nuniform vec2 uFade;\nuniform vec4 uRect;\nvarying vec2 vLmUv;\nvarying float vH;')
      .replace('#include <begin_vertex>', `#include <begin_vertex>
        vec3 ip = (modelMatrix * instanceMatrix * vec4(0.0, 0.0, 0.0, 1.0)).xyz;
        float fd = 1.0 - smoothstep(uFade.x, uFade.y, distance(cameraPosition, ip));
        transformed *= fd;
        vLmUv = vec2((ip.x - uRect.x) / (uRect.y - uRect.x), 1.0 - ((-ip.z) - uRect.z) / (uRect.w - uRect.z));
        vH = position.y / 0.34;
        transformed.x += sin(uTime * 2.0 + ip.x * 0.7 + ip.z * 0.4) * 0.04 * position.y / 0.34;`);
  };
  mat.customProgramCacheKey = () => 'grass';
  const rnd = mulberry32(7);
  const n = count;
  const im = new THREE.InstancedMesh(tuft, mat, n);
  const m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3();
  const c = new THREE.Color();
  const down = new THREE.Vector3(0, -1, 0);
  let k = 0;
  for (let tries = 0; k < n && tries < n * 3; tries++) {
    const b = cand[Math.floor(rnd() * cand.length)];
    p.set(b[0] + (rnd() - 0.5) * 3.0, b[1] - 0.02, b[2] + (rnd() - 0.5) * 3.0);
    ray.set(new THREE.Vector3(p.x, p.y + 2, p.z), down);
    if (groundMeshes.length && ray.intersectObjects(groundMeshes, false).length) continue;
    q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), rnd() * Math.PI);
    const sc = 0.7 + rnd() * 0.7;
    s.set(sc, sc * (0.8 + rnd() * 0.5), sc);
    m4.compose(p, q, s);
    im.setMatrixAt(k, m4);
    c.setHSL(0.22 + rnd() * 0.05, 0.35, 0.5 + rnd() * 0.15);
    im.setColorAt(k, c);
    k++;
  }
  im.count = k;
  im.receiveShadow = false;
  im.frustumCulled = false;
  im.name = 'grass';
  im.userData.fade = fade;
  return im;
}

export function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
