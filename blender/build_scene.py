"""松園別館 procedural scene builder.

Usage (from the project root):
    blender -b -P blender/build_scene.py -- [--bake] [--render] [--export] [--samples 64] [--quick]

Stages
    (always)  build geometry, materials, lighting, trees, collision; save blender/pine_garden.blend
    --bake    Cycles-bake lightmaps (DIFFUSE direct+indirect, no colour) + AO into the second UV set
    --render  preview renders into renders/ and the minimap image
    --export  GLB files + JSON into web/public/models (raw; tools/optimize.mjs compresses them)

Everything is in metres, in the plan frame documented in SPACE_PLAN.md
(+X along the building's long axis, +Y toward the rear, Z up).
"""
import bpy
import bmesh
import json
import math
import os
import random
import sys
import time

from mathutils import Vector, Matrix, noise

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_MODELS = os.path.join(ROOT, "web", "public", "models")
OUT_RENDERS = os.path.join(ROOT, "renders")
BAKE_DIR = os.path.join(HERE, "bake")
RAW_DIR = os.path.join(HERE, "export")
TEX_SRC = os.path.join(HERE, "textures_src")
TEX_GEN = os.path.join(HERE, "textures_gen")

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
ARGS = {
    "bake": "--bake" in argv,
    "render": "--render" in argv,
    "export": "--export" in argv,
    "quick": "--quick" in argv,
    "samples": int(argv[argv.index("--samples") + 1]) if "--samples" in argv else 64,
}

L = json.load(open(os.path.join(HERE, "layout.json"), encoding="utf-8"))
TOUR = json.load(open(os.path.join(ROOT, "content", "tour.json"), encoding="utf-8"))
M = L["main"]

# Afternoon sun (bearing ~205 deg, elevation 40 deg) expressed in the plan frame.
SUN_ELEV = math.radians(40)
_az = math.radians(205 - L["north_bearing_of_plus_x"])          # clockwise from +X
SUN_DIR = Vector((math.cos(_az) * math.cos(SUN_ELEV), -math.sin(_az) * math.cos(SUN_ELEV), math.sin(SUN_ELEV)))  # toward sun

T0 = time.time()


def log(*a):
    print("[%6.1fs]" % (time.time() - T0), *a, flush=True)


# --------------------------------------------------------------------------------------------
# small math helpers
# --------------------------------------------------------------------------------------------
def smoothstep(a, b, x):
    t = max(0.0, min(1.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


def point_in_poly(x, y, poly):
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy + 1e-12)))
    qx, qy = ax + t * dx, ay + t * dy
    return math.hypot(px - qx, py - qy), qx, qy


def poly_dist(px, py, pts, closed=True):
    best = (1e9, 0, 0)
    n = len(pts)
    rng = range(n) if closed else range(n - 1)
    for i in rng:
        a, b = pts[i], pts[(i + 1) % n]
        d = seg_dist(px, py, a[0], a[1], b[0], b[1])
        if d[0] < best[0]:
            best = d
    return best


BOUNDARY = L["boundary"][:-1]
RIVER_DIR = (-0.48, -0.88)
SEA_DIR = tuple(L["sea_dir"])
PATHS = L["paths"]
FOOTPRINTS = [(M["x0"] - 0.2, M["x1"] + 0.2, M["y0"] - 1.2, M["y1"] + 0.2)] + \
             [(b["x0"] - 0.2, b["x1"] + 0.2, b["y0"] - 0.2, b["y1"] + 0.2) for b in L["buildings"]]


def in_footprint(x, y, pad=0.0):
    for x0, x1, y0, y1 in FOOTPRINTS:
        if x0 - pad <= x <= x1 + pad and y0 - pad <= y <= y1 + pad:
            return True
    return False


def path_dist(x, y):
    best = 1e9
    for p in PATHS:
        d = poly_dist(x, y, p["pts"], closed=p.get("closed", False))[0] - p["w"] / 2
        best = min(best, d)
    return best


def terrain_h(x, y):
    """Ground height (m) relative to the lawn plateau."""
    inside = point_in_poly(x, y, BOUNDARY)
    d, qx, qy = poly_dist(x, y, BOUNDARY)
    # gentle undulation on the plateau
    h = 0.10 * math.sin(x * 0.13 + 1.0) * math.cos(y * 0.11) + 0.06 * math.sin(x * 0.31 + y * 0.17)
    if not inside:
        ux, uy = (x - qx) / (d + 1e-9), (y - qy) / (d + 1e-9)
        w = max(0.0, min(1.0, (ux * RIVER_DIR[0] + uy * RIVER_DIR[1] + 0.25) / 0.75))
        h -= 17.0 * smoothstep(1.0, 36.0, d) * w
        h -= 0.25 * smoothstep(0.0, 4.0, d) * (1 - w)          # streets sit a little lower
        h += 0.02 * max(0.0, d - 12) * (1 - w) * max(0.0, x * 0.6 + y * 0.8) / (abs(x) + abs(y) + 1)
        # river channel
        s = x * RIVER_DIR[0] + y * RIVER_DIR[1]
        h -= 3.5 * smoothstep(105, 125, s) * (1 - smoothstep(150, 170, s))
    else:
        # flatten under buildings and paths
        if in_footprint(x, y, 1.5):
            h *= 0.0
            h -= 0.03
        pd = path_dist(x, y)
        if pd < 1.2:
            h *= smoothstep(-0.2, 1.2, pd)
            h -= 0.02 * (1 - smoothstep(-0.2, 1.2, pd))
        h *= smoothstep(0.0, 2.0, d)                             # boundary itself sits at 0
    # Meilun hill to the NE (relative to the site, so the plateau itself stays at z = 0)
    h += hill(x, y)
    return h


def hill(x, y):
    hx, hy = 783.0, 441.0
    g = lambda px, py: 72.0 * math.exp(-((px - hx) ** 2 + (py - hy) ** 2) / (2 * 260.0 ** 2))
    return g(x, y) - g(0.0, 0.0)


def far_h(x, y):
    """Coarse height for the far ring (city plain, coast, sea floor), blended into terrain_h at the grid edge."""
    r = math.hypot(x, y)
    m = max(abs(x), abs(y))
    if m <= OUTER + 0.01:
        return terrain_h(x, y)
    s = x * SEA_DIR[0] + y * SEA_DIR[1]
    coast = 950 + 60 * math.sin(x * 0.004 + 1.3) + 40 * math.sin(y * 0.007)
    h = -20.0 - 8.5 * smoothstep(coast - 300, coast, s)
    if s > coast:
        h = -28.5 - 9.0 * smoothstep(coast, coast + 60, s)
    h += hill(x, y)
    t = smoothstep(OUTER, 700, m)
    edge = terrain_h(x * OUTER / m, y * OUTER / m)
    return edge * (1 - t) + h * t


# --------------------------------------------------------------------------------------------
# materials
# --------------------------------------------------------------------------------------------
MAT_DEFS = {
    # name: (texture folder or None, tile_u m, tile_v m, tint rgb, roughness, extra)
    "concrete":     ("Concrete034", 3.0, 1.5, (0.50, 0.48, 0.45), 0.9, {}),
    "plaster":      ("PaintedPlaster017", 3.0, 1.5, (0.74, 0.73, 0.69), 0.85, {}),
    "stone":        ("Concrete034", 2.0, 1.0, (0.42, 0.41, 0.39), 0.9, {}),
    "floor":        ("Concrete034", 2.0, 1.0, (0.55, 0.54, 0.52), 0.8, {}),
    "wood_white":   (None, 1, 1, (0.80, 0.80, 0.77), 0.55, {}),
    "wood_dark":    ("WoodFloor041", 1.6, 1.6, (0.55, 0.42, 0.32), 0.7, {}),
    "deck":         ("WoodFloor041", 2.0, 2.0, (0.80, 0.70, 0.60), 0.75, {}),
    "roof":         ("RoofingTiles013A", 2.0, 2.0, (0.55, 0.57, 0.60), 0.65, {}),
    "glass":        (None, 1, 1, (0.08, 0.10, 0.11), 0.08, {}),
    "metal_green":  (None, 1, 1, (0.16, 0.26, 0.20), 0.5, {"metal": 0.6}),
    "metal_dark":   (None, 1, 1, (0.05, 0.05, 0.05), 0.45, {"metal": 0.5}),
    "bronze":       (None, 1, 1, (0.30, 0.21, 0.10), 0.4, {"metal": 0.7}),
    "pergola_roof": (None, 1, 1, (0.30, 0.55, 0.38), 0.3, {"transmission": 0.55}),
    "brick":        ("Ground037", 1.5, 1.5, (0.45, 0.30, 0.24), 0.9, {}),
    "white":        (None, 1, 1, (0.85, 0.85, 0.83), 0.5, {}),
    "dark":         (None, 1, 1, (0.02, 0.02, 0.02), 0.95, {}),
    "gravel":       ("Gravel040", 1.5, 1.5, (0.75, 0.72, 0.68), 0.95, {}),
    "lawn":         ("Grass004", 2.5, 2.5, (1.0, 1.0, 1.0), 0.95, {"blend": "Ground037"}),
    "lawn_far":     ("Grass004", 8.0, 8.0, (0.9, 0.95, 0.85), 0.95, {"blend": "Ground037"}),
    "bark":         ("Bark012", 1.2, 2.4, (0.40, 0.36, 0.35), 0.95, {}),
    "needles":      ("@pine_atlas", 1, 1, (1, 1, 1), 0.8, {"alpha": True}),
    "ivy":          ("@ivy_atlas", 1, 1, (1, 1, 1), 0.7, {"alpha": True}),
    "shrub":        ("@shrub_atlas", 1, 1, (1, 1, 1), 0.8, {"alpha": True}),
    "far":          (None, 1, 1, (0.5, 0.5, 0.5), 0.95, {"vcol": True}),
    "sea":          (None, 1, 1, (0.03, 0.12, 0.18), 0.1, {}),
    "lamp":         (None, 1, 1, (1.0, 0.86, 0.62), 0.5, {"emit": 18.0}),
}
MATS = {}


def _tex_path(folder, kind):
    if folder.startswith("@"):
        return os.path.join(TEX_GEN, folder[1:] + ".png") if kind == "Color" else None
    p = os.path.join(TEX_SRC, folder, "%s_1K-JPG_%s.jpg" % (folder, kind))
    return p if os.path.exists(p) else None


def _img(path, noncolor=False):
    name = os.path.basename(path)
    img = bpy.data.images.get(name) or bpy.data.images.load(path, check_existing=True)
    if noncolor:
        img.colorspace_settings.name = "Non-Color"
    return img


def make_materials():
    for name, (folder, tu, tv, tint, rough, extra) in MAT_DEFS.items():
        mat = bpy.data.materials.new(name)
        nt = mat.node_tree
        nodes, links = nt.nodes, nt.links
        bsdf = nodes.get("Principled BSDF")
        bsdf.inputs["Roughness"].default_value = rough
        bsdf.inputs["Metallic"].default_value = extra.get("metal", 0.0)
        bsdf.inputs["Base Color"].default_value = (*tint, 1)
        if extra.get("transmission"):
            bsdf.inputs["Transmission Weight"].default_value = extra["transmission"]
        if folder:
            uv = nodes.new("ShaderNodeUVMap"); uv.uv_map = "UVMap"
            mp = nodes.new("ShaderNodeMapping")
            mp.inputs["Scale"].default_value = (1.0 / tu, 1.0 / tv, 1)
            links.new(uv.outputs["UV"], mp.inputs["Vector"])
            col = nodes.new("ShaderNodeTexImage"); col.image = _img(_tex_path(folder, "Color"))
            links.new(mp.outputs["Vector"], col.inputs["Vector"])
            color_out = col.outputs["Color"]
            if extra.get("blend"):
                col2 = nodes.new("ShaderNodeTexImage")
                col2.image = _img(_tex_path(extra["blend"], "Color"))
                links.new(mp.outputs["Vector"], col2.inputs["Vector"])
                attr = nodes.new("ShaderNodeVertexColor"); attr.layer_name = "Col"
                mix = nodes.new("ShaderNodeMix"); mix.data_type = "RGBA"
                links.new(attr.outputs["Color"], mix.inputs["Factor"])
                links.new(col.outputs["Color"], mix.inputs[6])
                links.new(col2.outputs["Color"], mix.inputs[7])
                color_out = mix.outputs[2]
            tintn = nodes.new("ShaderNodeMix"); tintn.data_type = "RGBA"; tintn.blend_type = "MULTIPLY"
            tintn.inputs["Factor"].default_value = 1.0
            links.new(color_out, tintn.inputs[6])
            tintn.inputs[7].default_value = (*tint, 1)
            links.new(tintn.outputs[2], bsdf.inputs["Base Color"])
            if extra.get("alpha"):
                links.new(col.outputs["Alpha"], bsdf.inputs["Alpha"])
                try:
                    mat.surface_render_method = "DITHERED"
                except Exception:
                    pass
            nrm_p = _tex_path(folder, "NormalGL")
            if nrm_p:
                nimg = nodes.new("ShaderNodeTexImage"); nimg.image = _img(nrm_p, True)
                links.new(mp.outputs["Vector"], nimg.inputs["Vector"])
                nm = nodes.new("ShaderNodeNormalMap"); nm.inputs["Strength"].default_value = 0.8
                nm.uv_map = "UVMap"
                links.new(nimg.outputs["Color"], nm.inputs["Color"])
                links.new(nm.outputs["Normal"], bsdf.inputs["Normal"])
        if extra.get("emit"):
            bsdf.inputs["Emission Color"].default_value = (*tint, 1)
            bsdf.inputs["Emission Strength"].default_value = extra["emit"]
        if extra.get("vcol"):
            attr = nodes.new("ShaderNodeVertexColor"); attr.layer_name = "Col"
            links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
        mat["tile"] = [tu, tv]
        MATS[name] = mat


# --------------------------------------------------------------------------------------------
# mesh builder (flat-shaded architecture, UV0 = metres projected per face)
# --------------------------------------------------------------------------------------------
class MB:
    def __init__(self):
        self.v = []
        self.f = []
        self.fm = []
        self.uv = []   # per face: list of (u,v) or None for auto
        self.col = []  # per face: list of rgba or None

    def face(self, pts, mat, uvs=None, col=None):
        i0 = len(self.v)
        self.v.extend([tuple(p) for p in pts])
        self.f.append(list(range(i0, i0 + len(pts))))
        self.fm.append(mat)
        self.uv.append(uvs)
        self.col.append(col)

    def quad(self, a, b, c, d, mat, uvs=None):
        self.face([a, b, c, d], mat, uvs)

    def box(self, x0, x1, y0, y1, z0, z1, mat, skip=()):
        p = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
             (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
        faces = {
            "zn": (0, 3, 2, 1), "zp": (4, 5, 6, 7),
            "yn": (0, 1, 5, 4), "yp": (2, 3, 7, 6),
            "xn": (3, 0, 4, 7), "xp": (1, 2, 6, 5),
        }
        for k, idx in faces.items():
            if k in skip:
                continue
            self.face([p[i] for i in idx], mat)

    def obox(self, cx, cy, hw, hd, z0, z1, rot, mat, skip=()):
        """Oriented box: centre, half sizes, rotation (rad) about Z."""
        c, s = math.cos(rot), math.sin(rot)

        def P(x, y, z):
            return (cx + x * c - y * s, cy + x * s + y * c, z)
        q = [P(-hw, -hd, z0), P(hw, -hd, z0), P(hw, hd, z0), P(-hw, hd, z0),
             P(-hw, -hd, z1), P(hw, -hd, z1), P(hw, hd, z1), P(-hw, hd, z1)]
        faces = {"zn": (0, 3, 2, 1), "zp": (4, 5, 6, 7), "yn": (0, 1, 5, 4), "yp": (2, 3, 7, 6),
                 "xn": (3, 0, 4, 7), "xp": (1, 2, 6, 5)}
        for k, idx in faces.items():
            if k in skip:
                continue
            self.face([q[i] for i in idx], mat)

    def cyl(self, cx, cy, r, z0, z1, mat, seg=10, cap=True):
        pts = [(cx + r * math.cos(2 * math.pi * i / seg), cy + r * math.sin(2 * math.pi * i / seg)) for i in range(seg)]
        for i in range(seg):
            a, b = pts[i], pts[(i + 1) % seg]
            u0, u1 = i / seg * 2 * math.pi * r, (i + 1) / seg * 2 * math.pi * r
            self.face([(a[0], a[1], z0), (b[0], b[1], z0), (b[0], b[1], z1), (a[0], a[1], z1)], mat,
                      [(u0, z0), (u1, z0), (u1, z1), (u0, z1)])
        if cap:
            self.face([(p[0], p[1], z1) for p in pts], mat)

    def to_object(self, name, smooth=False, collection=None):
        me = bpy.data.meshes.new(name)
        me.from_pydata(self.v, [], self.f)
        mats = []
        for m in self.fm:
            if m not in mats:
                mats.append(m)
        for m in mats:
            me.materials.append(MATS[m])
        me.polygons.foreach_set("material_index", [mats.index(m) for m in self.fm])
        # UV0: metres, planar projection by dominant normal axis
        uvl = me.uv_layers.new(name="UVMap")
        uvdata = []
        for fi, poly in enumerate(me.polygons):
            given = self.uv[fi]
            if given:
                uvdata.extend(given)
                continue
            n = poly.normal
            ax = max(range(3), key=lambda k: abs(n[k]))
            for vi in poly.vertices:
                x, y, z = self.v[vi]
                if ax == 2:
                    uvdata.append((x, y))
                elif ax == 1:
                    uvdata.append((x if n[1] < 0 else -x, z))
                else:
                    uvdata.append((-y if n[0] < 0 else y, z))
        flat = [c for uv in uvdata for c in uv]
        uvl.data.foreach_set("uv", flat)
        if any(c is not None for c in self.col):
            ca = me.color_attributes.new(name="Col", type="BYTE_COLOR", domain="CORNER")
            cols = []
            for fi, poly in enumerate(me.polygons):
                cc = self.col[fi]
                for k in range(len(poly.vertices)):
                    cols.extend(cc[k] if cc else (1, 1, 1, 1))
            ca.data.foreach_set("color", cols)
        me.validate()
        me.update()
        if smooth:
            me.shade_smooth()
        ob = bpy.data.objects.new(name, me)
        (collection or bpy.context.scene.collection).objects.link(ob)
        return ob


class Collision:
    """Simplified collision geometry (boxes, ramps, triangles)."""

    def __init__(self):
        self.mb = MB()

    def box(self, x0, x1, y0, y1, z0, z1):
        self.mb.box(x0, x1, y0, y1, z0, z1, "dark")

    def obox(self, *a, **k):
        self.mb.obox(*a, "dark", **k)

    def ramp(self, x0, x1, ya, za, yb, zb):
        """Walkable ramp along Y from (ya,za) to (yb,zb), solid down to z=min-0.2."""
        zl = min(za, zb) - 0.2
        m = self.mb
        m.face([(x0, ya, za), (x1, ya, za), (x1, yb, zb), (x0, yb, zb)], "dark")
        m.face([(x0, ya, zl), (x0, ya, za), (x0, yb, zb), (x0, yb, zl)], "dark")
        m.face([(x1, ya, zl), (x1, yb, zl), (x1, yb, zb), (x1, ya, za)], "dark")

    def wall_poly(self, pts, z0, z1, closed=True):
        n = len(pts)
        for i in range(n if closed else n - 1):
            a, b = pts[i], pts[(i + 1) % n]
            self.mb.face([(a[0], a[1], z0), (b[0], b[1], z0), (b[0], b[1], z1), (a[0], a[1], z1)], "dark")


COL = Collision()


# --------------------------------------------------------------------------------------------
# architecture
# --------------------------------------------------------------------------------------------
def arc_pts(xc, zs, r, n=10):
    return [(xc - r * math.cos(math.pi * k / n), zs + r * math.sin(math.pi * k / n)) for k in range(n + 1)]


def arcade_bay(mb, xl, xr, yf, yb, zb, spring, ztop, r, pw, mat, inner_mat, clip_l=False, clip_r=False):
    """One bay of the arcade frame: half piers each side + spandrel with semicircular arch."""
    xc = (xl + xr) / 2
    ol, orr = xl + pw / 2, xr - pw / 2           # opening edges
    xl2 = xl if not clip_l else xl + 0.0
    # piers (front faces, back faces, and opening-side faces)
    for (a, b) in ((xl2, ol), (orr, xr)):
        mb.quad((a, yf, zb), (b, yf, zb), (b, yf, ztop), (a, yf, ztop), mat)          # front
        mb.quad((b, yb, zb), (a, yb, zb), (a, yb, ztop), (b, yb, ztop), inner_mat)    # back (arcade side)
    mb.quad((ol, yf, zb), (ol, yb, zb), (ol, yb, spring), (ol, yf, spring), inner_mat)  # left jamb
    mb.quad((orr, yb, zb), (orr, yf, zb), (orr, yf, spring), (orr, yb, spring), inner_mat)
    # spandrels + intrados
    ap = arc_pts(xc, spring, r)
    for k in range(len(ap) - 1):
        (x0, z0), (x1, z1) = ap[k], ap[k + 1]
        mb.quad((x0, yf, z0), (x1, yf, z1), (x1, yf, ztop), (x0, yf, ztop), mat)
        mb.quad((x1, yb, z1), (x0, yb, z0), (x0, yb, ztop), (x1, yb, ztop), inner_mat)
        mb.quad((x1, yf, z1), (x0, yf, z0), (x0, yb, z0), (x1, yb, z1), inner_mat)
    if clip_l:
        mb.quad((xl, yb, zb), (xl, yf, zb), (xl, yf, ztop), (xl, yb, ztop), mat)
    if clip_r:
        mb.quad((xr, yf, zb), (xr, yb, zb), (xr, yb, ztop), (xr, yf, ztop), mat)


def wall_with_openings(mb, x0, x1, y, z0, z1, openings, mat, facing=-1, thick=0.3, inner_mat=None,
                       frames=True, kind_mat="wood_white"):
    """Planar wall along X at plane y (outer face), facing -Y (facing=-1) or +Y (facing=+1).

    openings: list of dicts {x0,x1,z0,z1,kind: 'door'|'window'|'open'|'glass_door'}
    Builds outer face as rectangles around openings, reveals, and the infill (frames + glass/door).
    """
    xs = sorted(set([x0, x1] + [o["x0"] for o in openings] + [o["x1"] for o in openings]))
    for i in range(len(xs) - 1):
        a, b = xs[i], xs[i + 1]
        m = (a + b) / 2
        covering = sorted([(o["z0"], o["z1"]) for o in openings if o["x0"] <= m <= o["x1"]])
        zc = z0
        segs = []
        for (oz0, oz1) in covering:
            if oz0 > zc:
                segs.append((zc, oz0))
            zc = max(zc, oz1)
        if zc < z1:
            segs.append((zc, z1))
        for (s0, s1) in segs:
            if facing < 0:
                mb.quad((a, y, s0), (b, y, s0), (b, y, s1), (a, y, s1), mat)
            else:
                mb.quad((b, y, s0), (a, y, s0), (a, y, s1), (b, y, s1), mat)
    d = thick * (-facing)   # direction into the wall
    rev = inner_mat or mat
    for o in openings:
        ox0, ox1, oz0, oz1 = o["x0"], o["x1"], o["z0"], o["z1"]
        yi = y + d
        s_rev = len(mb.f)
        # reveals
        if thick <= 0:
            pass
        elif facing < 0:
            mb.quad((ox0, yi, oz0), (ox0, y, oz0), (ox0, y, oz1), (ox0, yi, oz1), rev)
            mb.quad((ox1, y, oz0), (ox1, yi, oz0), (ox1, yi, oz1), (ox1, y, oz1), rev)
            mb.quad((ox0, y, oz1), (ox1, y, oz1), (ox1, yi, oz1), (ox0, yi, oz1), rev)
            if oz0 > z0 + 0.01:
                mb.quad((ox0, yi, oz0), (ox1, yi, oz0), (ox1, y, oz0), (ox0, y, oz0), rev)
        else:
            mb.quad((ox0, y, oz0), (ox0, yi, oz0), (ox0, yi, oz1), (ox0, y, oz1), rev)
            mb.quad((ox1, yi, oz0), (ox1, y, oz0), (ox1, y, oz1), (ox1, yi, oz1), rev)
            mb.quad((ox1, y, oz1), (ox0, y, oz1), (ox0, yi, oz1), (ox1, yi, oz1), rev)
            if oz0 > z0 + 0.01:
                mb.quad((ox1, yi, oz0), (ox0, yi, oz0), (ox0, y, oz0), (ox1, y, oz0), rev)
        oc = Vector(((ox0 + ox1) / 2, y + d / 2, (oz0 + oz1) / 2))
        fix_winding(mb, s_rev, lambda n, c, oc=oc: n.dot(oc - c) > 0)
        if o["kind"] == "open":
            continue
        # infill plane (glass or door leaf) at mid depth
        ym = y + d * 0.55
        sgn = 1 if facing < 0 else -1

        def plane(a, b, c0, c1, m_):
            if facing < 0:
                mb.quad((a, ym, c0), (b, ym, c0), (b, ym, c1), (a, ym, c1), m_)
            else:
                mb.quad((b, ym, c0), (a, ym, c0), (a, ym, c1), (b, ym, c1), m_)
        if o["kind"] == "door":
            tr = oz1 - 0.5
            plane(ox0, ox1, oz0, tr, kind_mat)
            plane(ox0, ox1, tr, oz1, "glass")
            bars_z = [tr]
            bars_x = [(ox0 + ox1) / 2]
            nzb = 2
        elif o["kind"] == "glass_door":
            plane(ox0, ox1, oz0, oz0 + 0.9, kind_mat)
            plane(ox0, ox1, oz0 + 0.9, oz1, "glass")
            bars_z = [oz0 + 0.9 + (oz1 - oz0 - 0.9) * t for t in (0.33, 0.66)]
            bars_x = [ox0 + (ox1 - ox0) * t for t in (0.25, 0.5, 0.75)]
        else:
            plane(ox0, ox1, oz0, oz1, "glass")
            nz = max(2, int(round((oz1 - oz0) / 0.45)))
            nx = max(2, int(round((ox1 - ox0) / 0.4)))
            bars_z = [oz0 + (oz1 - oz0) * k / nz for k in range(1, nz)]
            bars_x = [ox0 + (ox1 - ox0) * k / nx for k in range(1, nx)]
        if not frames:
            continue
        # frame + mullions as thin raised strips in front of the infill
        yf = ym - sgn * 0.035
        fw = 0.06

        def strip_h(zc, a, b):
            if facing < 0:
                mb.quad((a, yf, zc - fw / 2), (b, yf, zc - fw / 2), (b, yf, zc + fw / 2), (a, yf, zc + fw / 2), kind_mat)
            else:
                mb.quad((b, yf, zc - fw / 2), (a, yf, zc - fw / 2), (a, yf, zc + fw / 2), (b, yf, zc + fw / 2), kind_mat)

        def strip_v(xc, c0, c1):
            if facing < 0:
                mb.quad((xc - fw / 2, yf, c0), (xc + fw / 2, yf, c0), (xc + fw / 2, yf, c1), (xc - fw / 2, yf, c1), kind_mat)
            else:
                mb.quad((xc + fw / 2, yf, c0), (xc - fw / 2, yf, c0), (xc - fw / 2, yf, c1), (xc + fw / 2, yf, c1), kind_mat)
        for zc in bars_z + [oz0 + fw / 2, oz1 - fw / 2]:
            strip_h(zc, ox0, ox1)
        for xc in bars_x + [ox0 + fw / 2, ox1 - fw / 2]:
            strip_v(xc, oz0 + (0 if o["kind"] != "door" else 0), oz1)


def fix_winding(mb, start, outward):
    """Ensure faces added since `start` face the given outward test function(normal, centre)->bool."""
    for fi in range(start, len(mb.f)):
        pts = [Vector(mb.v[i]) for i in mb.f[fi]]
        n = (pts[1] - pts[0]).cross(pts[2] - pts[0])
        c = sum(pts, Vector()) / len(pts)
        if not outward(n, c):
            mb.f[fi] = mb.f[fi][::-1]


def roof_gable(mb, x0, x1, y0, y1, z_wall, pitch_deg, over_eave, over_gable, along="x", tiles=True, thick=0.14):
    """Gable roof with corrugated (桟瓦-like) tiles. along='x' => ridge parallel to X."""
    t = math.tan(math.radians(pitch_deg))
    if along == "x":
        a0, a1 = x0 - over_gable, x1 + over_gable       # along ridge
        b0, b1 = y0, y1                                 # across
    else:
        a0, a1 = y0 - over_gable, y1 + over_gable
        b0, b1 = x0, x1
    bc = (b0 + b1) / 2
    half = (b1 - b0) / 2
    zr = z_wall + half * t
    ze = z_wall - over_eave * t

    def P(a, b, z):
        return (a, b, z) if along == "x" else (b, a, z)
    pitch = 0.30
    seg = 4
    na = max(1, int((a1 - a0) / pitch * seg))
    for side in (-1, 1):
        be = bc + side * (half + over_eave)       # eave edge
        slope_len = math.hypot(half + over_eave, zr - ze)
        cols = []
        for i in range(na + 1):
            a = a0 + (a1 - a0) * i / na
            hump = 0.045 * abs(math.sin(math.pi * (a - a0) / pitch)) if tiles else 0.0
            cols.append((a, hump))
        for i in range(na):
            (aa, ha), (ab, hb) = cols[i], cols[i + 1]
            p1 = P(aa, be, ze + ha)
            p2 = P(ab, be, ze + hb)
            p3 = P(ab, bc, zr + hb)
            p4 = P(aa, bc, zr + ha)
            uv = [(aa, 0), (ab, 0), (ab, slope_len), (aa, slope_len)]
            if (side < 0) == (along == "x"):
                mb.face([p1, p2, p3, p4], "roof", uv)
            else:
                mb.face([p4, p3, p2, p1], "roof", uv[::-1])
        # underside of the eave overhang
        bw = bc + side * half
        zu_e, zu_w = ze - thick, z_wall - thick
        q = [P(a0, be, zu_e), P(a1, be, zu_e), P(a1, bw, zu_w), P(a0, bw, zu_w)]
        mb.face(q if (side > 0) == (along == "x") else q[::-1], "wood_white")
        # fascia at the eave
        q = [P(a0, be, zu_e), P(a1, be, zu_e), P(a1, be, ze + 0.05), P(a0, be, ze + 0.05)]
        mb.face(q if (side < 0) == (along == "x") else q[::-1], "wood_dark")
        # gable overhang soffits + barge boards
        for (ga, gw) in ((a0, a0 + over_gable), (a1, a1 - over_gable)):
            q = [P(ga, bc, zr - thick), P(gw, bc, zr - thick), P(gw, be, ze - thick), P(ga, be, ze - thick)]
            mb.face(q, "wood_white")
            mb.face(q[::-1], "wood_white")
            q = [P(ga, be, ze - thick), P(ga, bc, zr - thick), P(ga, bc, zr + 0.08), P(ga, be, ze + 0.08)]
            mb.face(q, "wood_dark")
            mb.face(q[::-1], "wood_dark")
    # ridge cap
    w = 0.22
    for side in (-1, 1):
        q = [P(a0, bc + side * w, zr + 0.02), P(a1, bc + side * w, zr + 0.02), P(a1, bc, zr + 0.22), P(a0, bc, zr + 0.22)]
        mb.face(q if (side < 0) == (along == "x") else q[::-1], "roof")
    for ga in (a0, a1):
        q = [P(ga, bc - w, zr + 0.02), P(ga, bc + w, zr + 0.02), P(ga, bc, zr + 0.38)]
        mb.face(q, "roof")
        mb.face(q[::-1], "roof")
    return zr


def gable_wall(mb, plane_a, b0, b1, z_wall, zr, mat, along="x", facing=1):
    """Triangle wall closing a gable. along='x': gable is in the YZ plane at x=plane_a."""
    bc = (b0 + b1) / 2
    if along == "x":
        pts = [(plane_a, b0, z_wall), (plane_a, b1, z_wall), (plane_a, bc, zr - 0.1)]
        if facing < 0:
            pts = pts[::-1]
    else:
        pts = [(b1, plane_a, z_wall), (b0, plane_a, z_wall), (bc, plane_a, zr - 0.1)]
        if facing > 0:
            pts = pts[::-1]
    mb.face(pts, mat)


def simple_block(mb, b, wall_mat="concrete", windows=True):
    """Single-storey annex building with optional gable roof; outer faces only."""
    x0, x1, y0, y1, ze = b["x0"], b["x1"], b["y0"], b["y1"], b["eave"]
    zb = b.get("raised", 0.0)
    roof = b["roof"]
    wall_mat = "wood_dark" if b.get("wood") else wall_mat
    ops_long = []
    if windows:
        n = max(1, int((x1 - x0) / 3.2))
        for k in range(n):
            cx = x0 + (x1 - x0) * (k + 0.5) / n
            ops_long.append({"x0": cx - 0.6, "x1": cx + 0.6, "z0": zb + 1.0, "z1": zb + 2.4, "kind": "window"})
    # front (-Y) wall
    front_ops = list(ops_long)
    if b["id"] in ("cabin", "annex", "kitchen", "rearwing"):
        cx = (x0 + x1) / 2
        front_ops = [o for o in front_ops if abs((o["x0"] + o["x1"]) / 2 - cx) > 1.0]
        front_ops.append({"x0": cx - 0.6, "x1": cx + 0.6, "z0": zb, "z1": zb + 2.3, "kind": "door"})
    wall_with_openings(mb, x0, x1, y0, -0.3, ze, front_ops, wall_mat, facing=-1, thick=0.2)
    wall_with_openings(mb, x0, x1, y1, -0.3, ze, ops_long, wall_mat, facing=1, thick=0.2)
    s = len(mb.f)
    for x, fac in ((x0, -1), (x1, 1)):
        mb.quad((x, y0, -0.3), (x, y1, -0.3), (x, y1, ze), (x, y0, ze), wall_mat)
    fix_winding(mb, s, lambda n, c: (n.x > 0) == (c.x > (x0 + x1) / 2))
    if roof == "flat":
        mb.box(x0 - 0.3, x1 + 0.3, y0 - 0.3, y1 + 0.3, ze, ze + 0.25, "concrete", skip=("zn",))
        mb.quad((x0 - 0.3, y0 - 0.3, ze), (x0 - 0.3, y1 + 0.3, ze), (x1 + 0.3, y1 + 0.3, ze), (x1 + 0.3, y0 - 0.3, ze), "plaster")
        zr = ze
    elif roof == "gable_x":
        pitch = math.degrees(math.atan((b["ridge"] - ze) / ((y1 - y0) / 2)))
        zr = roof_gable(mb, x0, x1, y0, y1, ze, pitch, 0.5, 0.3, along="x")
        gable_wall(mb, x0, y0, y1, ze, zr, wall_mat, "x", -1)
        gable_wall(mb, x1, y0, y1, ze, zr, wall_mat, "x", 1)
    else:
        pitch = math.degrees(math.atan((b["ridge"] - ze) / ((x1 - x0) / 2)))
        zr = roof_gable(mb, x0, x1, y0, y1, ze, pitch, 0.5, 0.3, along="y")
        gable_wall(mb, y0, x0, x1, ze, zr, wall_mat, "y", -1)
        gable_wall(mb, y1, x0, x1, ze, zr, wall_mat, "y", 1)
    COL.box(x0, x1, y0, y1, -1, ze)
    return zr


def build_main(mb, ivy_surfaces):
    x0, x1, y0, y1 = M["x0"], M["x1"], M["y0"], M["y1"]
    bay, pw, nb = M["bay"], M["pier"], M["bays"]
    xa = -nb * bay / 2                            # first pier centre (-15.3)
    yb = y0 + M["arcade_depth"]                    # arcade back wall face (-2.95)
    r = M["arch_r"]
    f2 = M["floor2"]
    z_band1, z_band1t = 4.05, 4.6
    eave = M["eave"]
    hb = M["hall_bay"]
    hx0, hx1 = -2.2, 2.2                            # hall interior
    piers = [xa + i * bay for i in range(nb + 1)]

    # ---- front arcade frames (two storeys)
    for fl, (zb, spring, ztop) in enumerate(((0.45, M["arch_spring1"], z_band1), (z_band1t, M["arch_spring2"], 8.0))):
        for i in range(nb):
            xl, xr = piers[i], piers[i + 1]
            arcade_bay(mb, xl if i else xa, xr, y0, y0 + pw, zb, spring, ztop, r, pw, "concrete", "plaster")
            if fl == 0 and i >= 7:
                ivy_surfaces.append(("front", xl, xr, y0, zb, ztop))
            if fl == 1 and (i >= 7 or i in (2, 3)):
                ivy_surfaces.append(("front", xl, xr, y0, zb, ztop if i >= 7 else spring))
    # floor bands
    mb.box(x0, x1, y0 - 0.06, y0 + pw, z_band1, z_band1t, "concrete", skip=("xn", "xp"))
    mb.box(x0, x1, y0 - 0.06, y0 + pw, 8.0, eave, "concrete", skip=("xn", "xp", "zp"))
    # plinth / floors
    mb.quad((xa, y0, 0.45), (-xa, y0, 0.45), (-xa, yb, 0.45), (xa, yb, 0.45), "floor")
    mb.quad((hx0, yb, 0.45), (hx1, yb, 0.45), (hx1, y1 - 0.3, 0.45), (hx0, y1 - 0.3, 0.45), "floor")
    for k, (dy, z) in enumerate(((0.9, 0.15), (0.6, 0.30), (0.3, 0.45))):
        mb.quad((xa, y0 - dy, z), (-xa, y0 - dy, z), (-xa, y0 - dy + 0.3, z), (xa, y0 - dy + 0.3, z), "stone")
        mb.quad((xa, y0 - dy, z - 0.15), (-xa, y0 - dy, z - 0.15), (-xa, y0 - dy, z), (xa, y0 - dy, z), "stone")
    COL.ramp(xa, -xa, y0 - 0.95, 0.0, y0, 0.45)
    # 1F ceiling = underside of the 2F slab; 2F floor
    mb.quad((xa, yb, 4.13), (-xa, yb, 4.13), (-xa, y0 + pw, 4.13), (xa, y0 + pw, 4.13), "plaster")
    mb.quad((xa, y0 + pw, f2), (-xa, y0 + pw, f2), (-xa, yb, f2), (xa, yb, f2), "floor")
    mb.quad((xa, y0 + 0.02, z_band1t), (-xa, y0 + 0.02, z_band1t), (-xa, y0 + pw, z_band1t), (xa, y0 + pw, z_band1t), "concrete")
    # band ledge faces between band top and 2F floor (inside)
    mb.quad((xa, y0 + pw, f2), (xa, y0 + pw, z_band1t), (-xa, y0 + pw, z_band1t), (-xa, y0 + pw, f2), "concrete")
    # 2F ceiling (white planks)
    mb.quad((xa, yb, 8.0), (-xa, yb, 8.0), (-xa, y0 + pw, 8.0), (xa, y0 + pw, 8.0), "wood_white",
            [(xa, yb), (-xa, yb), (-xa, y0 + pw), (xa, y0 + pw)])
    # balustrade on 2F
    for i in range(nb):
        xl, xr = piers[i] + pw / 2, piers[i + 1] - pw / 2
        mb.box(xl, xr, y0 + 0.05, y0 + 0.35, z_band1t, z_band1t + 0.1, "concrete", skip=("zn",))
        mb.box(xl, xr, y0 + 0.02, y0 + 0.38, 5.35, 5.5, "concrete")
        nbal = int((xr - xl) / 0.24)
        for k in range(nbal):
            cx = xl + (xr - xl) * (k + 0.5) / nbal
            mb.box(cx - 0.055, cx + 0.055, y0 + 0.14, y0 + 0.26, z_band1t + 0.1, 5.35, "concrete", skip=("zn", "zp"))
        COL.box(xl, xr, y0, y0 + 0.4, f2 - 0.1, f2 + 1.15)
    # piers in collision (both floors)
    for i, px in enumerate(piers):
        a = px - pw / 2 if i else x0
        b = px + pw / 2 if i < nb else x1
        COL.box(a, b, y0, y0 + pw, 0.0, eave)
    # ---- arcade back wall with doors / windows (1F and 2F)
    ops1, ops2 = [], []
    for i in range(nb):
        cx = xa + (i + 0.5) * bay
        if i == hb:
            ops1.append({"x0": -1.4, "x1": 1.4, "z0": 0.45, "z1": 3.45, "kind": "open"})
            ops2.append({"x0": -2.2, "x1": -1.0, "z0": f2, "z1": f2 + 2.4, "kind": "open"})
            ops2.append({"x0": 1.0, "x1": 2.2, "z0": f2, "z1": f2 + 2.4, "kind": "open"})
            continue
        if i % 2 == 0:
            ops1.append({"x0": cx - 0.75, "x1": cx + 0.75, "z0": 0.45, "z1": 0.45 + 2.6, "kind": "door"})
            ops2.append({"x0": cx - 0.8, "x1": cx + 0.8, "z0": f2 + 0.9, "z1": f2 + 2.7, "kind": "window"})
        else:
            ops1.append({"x0": cx - 0.8, "x1": cx + 0.8, "z0": 1.35, "z1": 3.15, "kind": "window"})
            ops2.append({"x0": cx - 0.7, "x1": cx + 0.7, "z0": f2, "z1": f2 + 2.5, "kind": "glass_door"})
    wall_with_openings(mb, xa, -xa, yb, 0.45, 4.13, ops1, "plaster", facing=-1, thick=0.3)
    wall_with_openings(mb, xa, -xa, yb, f2, 8.0, ops2, "plaster", facing=-1, thick=0.3)
    # collision: rooms either side of the hall + back wall pieces of hall
    COL.box(x0, hx0 - 0.2, yb, y1, 0, eave)
    COL.box(hx1 + 0.2, x1, yb, y1, 0, eave)
    COL.box(-1.0, 1.0, yb, yb + 0.3, f2 - 0.3, eave)                   # wall between the two 2F doors
    COL.box(hx0 - 0.2, -1.4, yb, yb + 0.3, 0, f2 - 0.3)               # 1F wall either side of the hall opening
    COL.box(1.4, hx1 + 0.2, yb, yb + 0.3, 0, f2 - 0.3)
    COL.box(-1.4, 1.4, yb, yb + 0.3, 3.45, f2 - 0.3)
    # ---- end walls (with high small windows) and gables
    for x, fac in ((x0, -1), (x1, 1)):
        s = len(mb.f)
        ops = [{"x0": -0.5, "x1": 0.5, "z0": 6.2, "z1": 7.2, "kind": "window"},
               {"x0": -0.4, "x1": 0.4, "z0": 2.0, "z1": 3.0, "kind": "window"}]
        tmp = MB()
        wall_with_openings(tmp, y0, y1, 0.0, -0.3, eave, [dict(o, x0=o["x0"] + 1.5, x1=o["x1"] + 1.5) for o in ops],
                           "concrete", facing=-1, thick=0.3)
        for fi, f in enumerate(tmp.f):
            pts = [(x - fac * tmp.v[vi][1], tmp.v[vi][0], tmp.v[vi][2]) for vi in f]
            mb.face(pts if fac > 0 else pts[::-1], tmp.fm[fi])
        COL.box(x0, xa, y0, y1, 0, eave) if fac < 0 else COL.box(-xa, x1, y0, y1, 0, eave)
        # end faces of the arcade (inner side of end walls)
        mb.quad((x - fac * 0.5, y0 + pw, 0.45), (x - fac * 0.5, yb, 0.45), (x - fac * 0.5, yb, 4.13), (x - fac * 0.5, y0 + pw, 4.13), "plaster")
        mb.quad((x - fac * 0.5, y0 + pw, f2), (x - fac * 0.5, yb, f2), (x - fac * 0.5, yb, 8.0), (x - fac * 0.5, y0 + pw, 8.0), "plaster")
        fix_winding(mb, len(mb.f) - 2, lambda n, c, fac=fac: n.x * fac < 0)
        ivy_surfaces.append(("end", x, fac, y0, y1, eave)) if fac > 0 else None
    # ---- rear facade
    opsr = []
    for i in range(nb):
        cx = xa + (i + 0.5) * bay
        if L["buildings"][3]["x0"] - 0.6 < cx < L["buildings"][3]["x1"] + 0.6:
            pass
        opsr.append({"x0": cx - 0.75, "x1": cx + 0.75, "z0": 1.4, "z1": 3.2, "kind": "window"})
        opsr.append({"x0": cx - 0.75, "x1": cx + 0.75, "z0": 5.4, "z1": 7.3, "kind": "window"})
    opsr = [o for o in opsr if not (-13.2 < (o["x0"] + o["x1"]) / 2 < -9.9 and o["z1"] < 4)]
    wall_with_openings(mb, x0, x1, y1, -0.3, eave, opsr, "concrete", facing=1, thick=0.3)
    # ---- roof
    zr = roof_gable(mb, x0, x1, y0, y1, eave, M["roof_pitch_deg"], M["roof_overhang"], 0.4, along="x")
    gable_wall(mb, x0, y0, y1, eave, zr, "concrete", "x", -1)
    gable_wall(mb, x1, y0, y1, eave, zr, "concrete", "x", 1)
    ivy_surfaces.append(("gable", x1, y0, y1, eave, zr))
    # ---- central hall with the three-way stair (推測重建)
    s = len(mb.f)
    for hx, fac in ((hx0, 1), (hx1, -1)):
        mb.quad((hx, yb + 0.3, 0.45), (hx, y1 - 0.3, 0.45), (hx, y1 - 0.3, 8.0), (hx, yb + 0.3, 8.0), "plaster")
    fix_winding(mb, s, lambda n, c: n.x * (-1 if c.x > 0 else 1) > 0)
    mb.quad((hx0, yb + 0.3, 8.0), (hx1, yb + 0.3, 8.0), (hx1, y1 - 0.3, 8.0), (hx0, y1 - 0.3, 8.0), "plaster")
    fix_winding(mb, len(mb.f) - 1, lambda n, c: n.z < 0)
    # inner face of the back wall (hall side) and rear wall (hall side, with windows)
    wall_with_openings(mb, hx0, hx1, yb + 0.3, 0.45, 8.0,
                       [{"x0": -1.4, "x1": 1.4, "z0": 0.45, "z1": 3.45, "kind": "open"},
                        {"x0": -2.2, "x1": -1.0, "z0": f2, "z1": f2 + 2.4, "kind": "open"},
                        {"x0": 1.0, "x1": 2.2, "z0": f2, "z1": f2 + 2.4, "kind": "open"}],
                       "plaster", facing=1, thick=0.0, frames=False)
    wall_with_openings(mb, hx0, hx1, y1 - 0.3, 0.45, 8.0,
                       [{"x0": -0.8, "x1": 0.8, "z0": 3.3, "z1": 4.9, "kind": "window"},
                        {"x0": -0.8, "x1": 0.8, "z0": 5.6, "z1": 7.4, "kind": "window"}],
                       "plaster", facing=-1, thick=0.0)
    COL.box(hx0 - 0.2, hx0, yb, y1, 0, eave)
    COL.box(hx1, hx1 + 0.2, yb, y1, 0, eave)
    COL.box(hx0, hx1, y1 - 0.3, y1, 0, eave)
    rise = (f2 - 0.45) / 24
    tread = 0.28
    ys = -2.4                     # foot of the central flight
    yl0 = ys + 12 * tread         # landing front edge (0.96)
    yl1 = 2.6
    zl = 0.45 + 12 * rise         # landing level (2.45)
    # central flight (x -1..1)
    for k in range(12):
        ya, yb_ = ys + k * tread, ys + (k + 1) * tread
        zt = 0.45 + (k + 1) * rise
        mb.quad((-1.0, ya, zt), (1.0, ya, zt), (1.0, yb_, zt), (-1.0, yb_, zt), "stone")
        mb.quad((-1.0, ya, zt - rise), (1.0, ya, zt - rise), (1.0, ya, zt), (-1.0, ya, zt), "stone")
    COL.ramp(-1.0, 1.0, ys - 0.05, 0.45, yl0, zl)
    # landing
    mb.quad((hx0, yl0, zl), (hx1, yl0, zl), (hx1, yl1, zl), (hx0, yl1, zl), "stone")
    mb.quad((hx0, yl1, 0.45), (hx0, yl1, zl), (hx1, yl1, zl), (hx1, yl1, 0.45), "stone")
    COL.box(hx0, hx1, yl0, yl1, 0, zl)
    COL.box(hx0, hx1, yl1, yl1 + 0.15, zl, zl + 1.1)                 # landing rear railing
    mb.box(hx0, hx1, yl1 - 0.12, yl1, zl, zl + 0.95, "concrete", skip=("zn",))
    # side flights (x ±1.0..2.2) rising toward the front, solid masonry below
    for sgn in (-1, 1):
        xa_, xb_ = (1.0, hx1) if sgn > 0 else (hx0, -1.0)
        for k in range(12):
            yt0, yt1 = yl0 - (k + 1) * tread, yl0 - k * tread
            zt = zl + (k + 1) * rise
            mb.quad((xa_, yt0, zt), (xb_, yt0, zt), (xb_, yt1, zt), (xa_, yt1, zt), "stone")
            mb.quad((xb_, yt1, zt - rise), (xa_, yt1, zt - rise), (xa_, yt1, zt), (xb_, yt1, zt), "stone")
            # inner side face toward the central flight
            xi = xa_ if sgn > 0 else xb_
            zc_lo = 0.45 + max(0, (yt0 - ys) / tread) * rise
            q = [(xi, yt0, zc_lo), (xi, yt1, zc_lo), (xi, yt1, zt), (xi, yt0, zt)]
            mb.face(q if sgn < 0 else q[::-1], "stone")
        COL.ramp(xa_, xb_, yl0, zl, ys, f2)
        mb.quad((xa_, ys, 0.45), (xb_, ys, 0.45), (xb_, ys, f2), (xa_, ys, f2), "stone")
        # balustrade wall along the inner edge, above the side flight (double sided + top)
        rail_x0, rail_x1 = (1.0, 1.15) if sgn > 0 else (-1.15, -1.0)
        for rx, flip in ((rail_x0, False), (rail_x1, True)):
            q = [(rx, ys, f2), (rx, yl0, zl), (rx, yl0, zl + 0.95), (rx, ys, f2 + 0.95)]
            mb.face(q[::-1] if flip else q, "concrete")
        mb.face([(rail_x0, ys, f2 + 0.95), (rail_x0, yl0, zl + 0.95), (rail_x1, yl0, zl + 0.95), (rail_x1, ys, f2 + 0.95)][::-1], "concrete")
        COL.mb.face([(rail_x0, ys, f2 + 1.1), (rail_x0, yl0, zl + 1.1), (rail_x0, yl0, zl - 0.5), (rail_x0, ys, f2 - 0.5)], "dark")
        COL.mb.face([(rail_x1, ys, f2 - 0.5), (rail_x1, yl0, zl - 0.5), (rail_x1, yl0, zl + 1.1), (rail_x1, ys, f2 + 1.1)], "dark")
        # the wedge itself (solid)
        COL.box(xa_, xb_, ys, yl0, 0, 0.5)
    # arrival strip on 2F inside the hall with a railing over the stairwell
    mb.quad((hx0, yb + 0.3, f2), (hx1, yb + 0.3, f2), (hx1, ys, f2), (hx0, ys, f2), "stone")
    mb.box(-1.0, 1.0, ys - 0.12, ys, f2, f2 + 0.95, "concrete")
    mb.quad((hx0, ys, 4.13), (hx1, ys, 4.13), (hx1, yb + 0.3, 4.13), (hx0, yb + 0.3, 4.13), "plaster")
    COL.box(hx0, hx1, yb + 0.3, ys, f2 - 0.3, f2)
    COL.box(-1.0, 1.0, ys - 0.15, ys, f2, f2 + 1.15)
    # 2F arcade floor collision + 1F floor
    COL.box(xa, -xa, y0, yb, f2 - 0.32, f2)
    COL.box(xa, -xa, y0, yb + 0.3, -0.5, 0.45)
    COL.box(hx0, hx1, yb, yl0, -0.5, 0.45)
    # pendant lamps (photo 7) and hall ceiling lights: emissive, they light the bake
    for i in range(nb):
        cx = xa + (i + 0.5) * bay
        yc = (y0 + pw + yb) / 2
        for zc in (3.85, 7.55):
            if i == hb and zc < 4:
                continue
            mb.box(cx - 0.012, cx + 0.012, yc - 0.012, yc + 0.012, zc + 0.12, zc + (0.28 if zc < 4 else 0.45), "metal_dark", skip=("zn", "zp"))
            mb.box(cx - 0.13, cx + 0.13, yc - 0.13, yc + 0.13, zc, zc + 0.12, "lamp")
    for yc in (-1.0, 3.0):
        mb.box(-0.5, 0.5, yc - 0.5, yc + 0.5, 7.9, 7.99, "lamp", skip=("zp",))
    # floor lights in front of the piers (black boxes)
    for px in piers[1:-1]:
        mb.box(px - 0.12, px + 0.12, y0 - 1.6, y0 - 1.36, 0.0, 0.18, "metal_dark", skip=("zn",))
    return zr


def build_annexes(mb, ivy_surfaces):
    for b in L["buildings"]:
        if b["id"] == "connector":
            mb.box(b["x0"], b["x1"], b["y0"] - 0.2, b["y1"] + 0.2, b["eave"], b["eave"] + 0.3, "concrete")
            continue
        if b["id"] == "link":
            mb.box(b["x0"] - 0.2, b["x1"] + 0.2, b["y0"], b["y1"], b["eave"], b["eave"] + 0.22, "concrete")
            for px in (b["x0"], b["x1"] - 0.25):
                for py in (b["y0"] + 1.5, b["y1"] - 0.25):
                    mb.box(px, px + 0.25, py, py + 0.25, 0, b["eave"], "concrete", skip=("zn", "zp"))
                    COL.box(px, px + 0.25, py, py + 0.25, 0, b["eave"])
            continue
        zr = simple_block(mb, b)
        if b.get("ivy"):
            ivy_surfaces.append(("block", b, zr))
        if b.get("chimney"):
            cx, cy = b["chimney"]
            mb.box(cx - 0.45, cx + 0.45, cy - 0.45, cy + 0.45, b["eave"] - 0.5, 7.3, "brick", skip=("zn",))
        if b.get("raised"):
            # steps to the door + posts under the raised floor
            cx = (b["x0"] + b["x1"]) / 2
            for k in range(4):
                z = (k + 1) * 0.15
                mb.box(cx - 0.9, cx + 0.9, b["y0"] - 1.2 + k * 0.3, b["y0"], z - 0.15, z, "wood_dark", skip=("zn",))


def deck_strip(mb, pts, w, z, mat, closed=False, edge=0.12):
    """Mitred strip along a polyline (no overlapping quads), UV u along the path."""
    P = [Vector((p[0], p[1], 0)) for p in pts]
    n = len(P)
    offs = []
    for i in range(n):
        if closed:
            d0 = (P[i] - P[i - 1]).normalized()
            d1 = (P[(i + 1) % n] - P[i]).normalized()
        else:
            d0 = (P[i] - P[i - 1]).normalized() if i > 0 else (P[1] - P[0]).normalized()
            d1 = (P[i + 1] - P[i]).normalized() if i < n - 1 else d0
        n0 = Vector((-d0.y, d0.x, 0))
        n1 = Vector((-d1.y, d1.x, 0))
        m = n0 + n1
        m = m.normalized() if m.length > 1e-6 else n0
        k = 1.0 / max(0.35, m.dot(n0))
        offs.append(m * (w / 2) * k)
    segs = n if closed else n - 1
    run = 0.0
    for i in range(segs):
        j = (i + 1) % n
        ln = (P[j] - P[i]).length
        q = [P[i] - offs[i], P[j] - offs[j], P[j] + offs[j], P[i] + offs[i]]
        uv = [(run, 0), (run + ln, 0), (run + ln, w), (run, w)] if mat == "deck" else None
        mb.face([(v.x, v.y, z) for v in q], mat, uv)
        if edge:
            for (u, v) in ((0, 1), (2, 3)):
                q0, q1 = q[u], q[v]
                mb.face([(q0.x, q0.y, z - edge - 0.05), (q1.x, q1.y, z - edge - 0.05), (q1.x, q1.y, z), (q0.x, q0.y, z)], "wood_dark")
        run += ln


def build_site(mb, gmb, pmb):
    # paths
    for p in L["paths"]:
        if p["kind"] == "deck":
            deck_strip(gmb, p["pts"], p["w"], 0.12, "deck", p.get("closed", False))
        else:
            deck_strip(gmb, p["pts"], p["w"], 0.03, "gravel", False, edge=0)
    # café terrace deck + pergola (green steel, translucent green roof)
    t = L["terrace"]
    gmb.box(t["x0"], t["x1"], t["y0"], t["y1"], 0.0, 0.12, "deck", skip=("zn",))
    px0, px1, py0, py1 = t["pergola"]
    nx = 4
    for i in range(nx + 1):
        x = px0 + (px1 - px0) * i / nx
        for y in (py0, py1):
            mb.box(x - 0.06, x + 0.06, y - 0.06, y + 0.06, 0.12, 2.75, "metal_green", skip=("zn",))
            COL.box(x - 0.08, x + 0.08, y - 0.08, y + 0.08, 0, 2.75)
    for y in (py0, py1):
        mb.box(px0 - 0.2, px1 + 0.2, y - 0.07, y + 0.07, 2.65, 2.85, "metal_green")
    for i in range(nx + 1):
        x = px0 + (px1 - px0) * i / nx
        mb.box(x - 0.05, x + 0.05, py0 - 0.3, py1 + 0.3, 2.85, 2.95, "metal_green")
    roof = [(px0 - 0.3, py0 - 0.4, 2.98), (px1 + 0.3, py0 - 0.4, 2.98), (px1 + 0.3, py1 + 0.4, 3.1), (px0 - 0.3, py1 + 0.4, 3.1)]
    mb.face(roof, "pergola_roof")
    mb.face(roof[::-1], "pergola_roof")
    # tables & chairs & loungers
    rnd = random.Random(7)
    for k, (tx, ty) in enumerate(((19.0, -9.0), (25.5, -8.5), (28.6, -6.6))):
        mb.cyl(tx, ty, 0.42, 0.72, 0.76, "white", seg=12)
        mb.cyl(tx, ty, 0.05, 0.12, 0.72, "metal_dark", seg=6, cap=False)
        COL.box(tx - 0.45, tx + 0.45, ty - 0.45, ty + 0.45, 0, 0.76)
        for a in (0.3, 0.3 + math.pi):
            cx, cy = tx + 0.75 * math.cos(a + k), ty + 0.75 * math.sin(a + k)
            mb.obox(cx, cy, 0.22, 0.22, 0.12, 0.55, a + k, "wood_dark")
            mb.obox(cx + 0.2 * math.cos(a + k), cy + 0.2 * math.sin(a + k), 0.03, 0.22, 0.55, 1.0, a + k, "wood_dark")
    for k in range(3):
        cx, cy = 17.4 + k * 1.1, -5.6
        mb.obox(cx, cy, 0.32, 0.9, 0.12, 0.42, 0, "white")
        mb.face([(cx - 0.32, cy + 0.5, 0.42), (cx + 0.32, cy + 0.5, 0.42), (cx + 0.32, cy + 0.95, 0.85), (cx - 0.32, cy + 0.95, 0.85)], "white")
        COL.box(cx - 0.33, cx + 0.33, cy - 0.9, cy + 0.9, 0, 0.45)
    # sea-view deck with railing
    sd = L["seadeck"]
    rot = math.radians(sd["rot"])
    cx, cy = sd["c"]
    hw, hd = sd["w"] / 2, sd["d"] / 2
    gmb.obox(cx, cy, hw, hd, 0.0, 0.15, rot, "deck", skip=("zn",))
    c, s = math.cos(rot), math.sin(rot)

    def P(x, y):
        return (cx + x * c - y * s, cy + x * s + y * c)
    rail = [P(-hw, hd * 0.2), P(-hw, -hd), P(hw, -hd), P(hw, hd * 0.2)]
    for i in range(3):
        a, b = rail[i], rail[i + 1]
        ln = math.dist(a, b)
        n = int(ln / 1.4) + 1
        for k in range(n + 1):
            q = (a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n)
            mb.box(q[0] - 0.05, q[0] + 0.05, q[1] - 0.05, q[1] + 0.05, 0.15, 1.05, "wood_dark", skip=("zn",))
        ang = math.atan2(b[1] - a[1], b[0] - a[0])
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        mb.obox(mid[0], mid[1], ln / 2 + 0.05, 0.06, 1.0, 1.08, ang, "wood_dark")
        mb.obox(mid[0], mid[1], ln / 2, 0.03, 0.55, 0.6, ang, "wood_dark")
        COL.obox(mid[0], mid[1], ln / 2 + 0.05, 0.1, 0.0, 1.2, ang)
    for k in (-1.6, 1.6):
        q = P(k, hd * 0.1)
        mb.obox(q[0], q[1], 0.8, 0.22, 0.15, 0.48, rot, "wood_dark")
        COL.obox(q[0], q[1], 0.8, 0.22, 0.0, 0.48, rot)
    # gate pillars + low walls + ticket booth
    gx, gy = L["gate"]["pos"]
    for py in (gy + 1.9, gy - 1.9):
        mb.box(gx - 0.5, gx + 0.5, py - 0.5, py + 0.5, 0, 2.2, "concrete", skip=("zn",))
        mb.box(gx - 0.58, gx + 0.58, py - 0.58, py + 0.58, 2.2, 2.34, "stone")
        mb.box(gx + 0.5, gx + 0.52, py - 0.22, py + 0.22, 1.25, 1.6, "bronze", skip=("zn",))
        COL.box(gx - 0.5, gx + 0.5, py - 0.5, py + 0.5, 0, 2.2)
    bx, by = L["booth"]
    mb.box(bx - 1.2, bx + 1.2, by - 0.9, by + 0.9, 0, 2.5, "wood_dark", skip=("zn",))
    mb.box(bx - 1.45, bx + 1.45, by - 1.15, by + 1.15, 2.5, 2.7, "roof")
    mb.box(bx + 1.2, bx + 1.21, by - 0.6, by + 0.6, 1.0, 2.0, "glass", skip=("zn",))
    COL.box(bx - 1.2, bx + 1.2, by - 0.9, by + 0.9, 0, 2.5)
    # air-raid shelter: concrete portal + dark recess (mound is built with the terrain)
    ax, ay = L["airraid"]["pos"]
    mb.box(ax - 0.4, ax, ay - 1.4, ay + 1.4, 0, 2.5, "concrete", skip=("zn", "xp"))
    mb.quad((ax - 0.4, ay - 0.7, 0.01), (ax - 0.4, ay + 0.7, 0.01), (ax + 1.8, ay + 0.7, 0.01), (ax + 1.8, ay - 0.7, 0.01), "dark")
    s = len(mb.f)
    for q in ((ax + 1.8, ay - 0.7), ):
        pass
    mb.face([(ax - 0.401, ay - 0.7, 0.0), (ax - 0.401, ay + 0.7, 0.0), (ax - 0.401, ay + 0.7, 1.9), (ax - 0.401, ay - 0.7, 1.9)], "dark")
    mb.box(ax - 0.9, ax - 0.4, ay - 2.4, ay - 1.4, 0, 1.2, "concrete", skip=("zn",))
    mb.box(ax - 0.9, ax - 0.4, ay + 1.4, ay + 2.4, 0, 1.2, "concrete", skip=("zn",))
    mb.box(ax - 0.42, ax - 0.4, ay - 0.35, ay + 0.35, 2.0, 2.3, "metal_dark")
    COL.box(ax - 0.4, ax + 3, ay - 2.4, ay + 2.4, 0, 2.5)
    # 奉安所 stone base (推測位置)
    hx, hy = L["hoan_base"]
    mb.box(hx - 0.9, hx + 0.9, hy - 0.9, hy + 0.9, 0, 0.3, "stone", skip=("zn",))
    mb.box(hx - 0.6, hx + 0.6, hy - 0.6, hy + 0.6, 0.3, 0.75, "stone", skip=("zn",))
    COL.box(hx - 0.9, hx + 0.9, hy - 0.9, hy + 0.9, 0, 0.75)
    # benches along the ring
    ring = L["paths"][0]["pts"]
    for k in (4, 7, 11, 15):
        a, b = ring[k], ring[k + 1]
        d = Vector((b[0] - a[0], b[1] - a[1], 0)).normalized()
        nrm = Vector((d.y, -d.x, 0))
        m_ = Vector(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 0)) + nrm * 1.6
        ang = math.atan2(d.y, d.x)
        mb.obox(m_.x, m_.y, 0.8, 0.22, 0.38, 0.45, ang, "wood_dark")
        mb.obox(m_.x, m_.y, 0.75, 0.18, 0.0, 0.38, ang, "stone", skip=("zn",))
        COL.obox(m_.x, m_.y, 0.8, 0.22, 0, 0.45, ang)
    # signpost by the annex (photo 4)
    mb.box(19.6, 19.72, -4.9, -4.78, 0, 2.6, "wood_dark", skip=("zn",))
    for k, z in enumerate((2.3, 1.95, 1.6, 1.25)):
        mb.box(19.72, 20.5 + (k % 2) * 0.2, -4.92, -4.76, z - 0.12, z + 0.12, "wood_dark")
    # boundary railing (wood) except the gate
    bd = L["boundary"]
    for i in range(len(bd) - 1):
        a, b = bd[i], bd[i + 1]
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        if math.dist(mid, (gx, gy)) < 3.5 or (abs(a[0] - 34.4) < 0.2 and abs(b[0] - 34.3) < 0.2):
            continue
        ln = math.dist(a, b)
        n = max(1, int(ln / 2.4))
        for k in range(n + 1):
            q = (a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n)
            if math.dist(q, (gx, gy)) < 2.6:
                continue
            pmb.box(q[0] - 0.05, q[0] + 0.05, q[1] - 0.05, q[1] + 0.05, -0.2, 0.95, "wood_dark", skip=("zn",))
        ang = math.atan2(b[1] - a[1], b[0] - a[0])
        pmb.obox(mid[0], mid[1], ln / 2, 0.035, 0.82, 0.9, ang, "wood_dark")
        pmb.obox(mid[0], mid[1], ln / 2, 0.035, 0.42, 0.48, ang, "wood_dark")
    # invisible boundary wall (walkable area incl. a pocket outside the gate)
    wb = []
    for p in bd[:-1]:
        wb.append(tuple(p))
        if abs(p[0] - 34.44) < 0.01 and abs(p[1] + 25.26) < 0.01:
            wb += [(34.4, -25.6), (41.0, -25.6), (41.0, -31.2), (36.8, -31.2)]
    wb = [q for q in wb if not (abs(q[0] - 34.31) < 0.01 and abs(q[1] + 27.54) < 0.01)]
    COL.wall_poly(wb, -5, 4)
    return wb


# --------------------------------------------------------------------------------------------
# terrain
# --------------------------------------------------------------------------------------------
CORE = (-108.0, 72.0, -72.0, 54.0)     # x0,x1,y0,y1 (multiples of 6)
OUTER = 330.0


def dirt_amount(x, y):
    """Vertex colour: 0 = lawn, 1 = bare soil/pine litter."""
    d = path_dist(x, y)
    v = 0.0
    if d < 1.6:
        v = max(v, 1 - smoothstep(0.0, 1.6, d))
    for t in TREE_POS:
        dd = math.hypot(x - t[0], y - t[1])
        if dd < 3.2:
            v = max(v, 0.55 * (1 - dd / 3.2))
    n = noise.noise(Vector((x * 0.08, y * 0.08, 0.3)))
    v = max(v, smoothstep(0.25, 0.6, n) * 0.5)
    if not point_in_poly(x, y, BOUNDARY):
        v = max(v, 0.35 + 0.3 * n)
    return max(0.0, min(1.0, v))


def grid_mesh(name, x0, x1, y0, y1, step, hfun, mat, lm_rect=None, hole=None, edge_match=None, colfn=None):
    nx, ny = int(round((x1 - x0) / step)), int(round((y1 - y0) / step))
    verts, faces, uv, uv2 = [], [], [], []
    for j in range(ny + 1):
        for i in range(nx + 1):
            x, y = x0 + i * step, y0 + j * step
            z = hfun(x, y)
            if edge_match and (i == 0 or j == 0 or i == nx or j == ny):
                z = edge_match(x, y)
            verts.append((x, y, z))
    idx = lambda i, j: j * (nx + 1) + i
    for j in range(ny):
        for i in range(nx):
            cx, cy = x0 + (i + 0.5) * step, y0 + (j + 0.5) * step
            if hole and hole[0] < cx < hole[1] and hole[2] < cy < hole[3]:
                continue
            faces.append((idx(i, j), idx(i + 1, j), idx(i + 1, j + 1), idx(i, j + 1)))
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.materials.append(MATS[mat])
    l0 = me.uv_layers.new(name="UVMap")
    l1 = me.uv_layers.new(name="LM")
    a, b = [], []
    rx0, rx1, ry0, ry1 = lm_rect or (x0, x1, y0, y1)
    for poly in me.polygons:
        for vi in poly.vertices:
            x, y, _ = verts[vi]
            a += [x, y]
            b += [(x - rx0) / (rx1 - rx0), (y - ry0) / (ry1 - ry0)]
    l0.data.foreach_set("uv", a)
    l1.data.foreach_set("uv", b)
    if colfn:
        ca = me.color_attributes.new(name="Col", type="BYTE_COLOR", domain="POINT")
        cols = []
        for (x, y, z) in verts:
            v = colfn(x, y)
            cols += [v, v, v, 1.0]
        ca.data.foreach_set("color", cols)
    me.update()
    me.shade_smooth()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def build_terrain():
    cx0, cx1, cy0, cy1 = CORE
    step = 1.0 if not ARGS["quick"] else 2.0
    coarse = 6.0

    def edge_match(x, y):
        # interpolate between coarse nodes along the core border to avoid cracks
        fx = (x - cx0) / coarse
        fy = (y - cy0) / coarse
        if abs(fx - round(fx)) < 1e-6 and abs(fy - round(fy)) < 1e-6:
            return terrain_h(x, y)
        if abs(y - cy0) < 1e-6 or abs(y - cy1) < 1e-6:
            xa = cx0 + math.floor(fx) * coarse
            t = (x - xa) / coarse
            return terrain_h(xa, y) * (1 - t) + terrain_h(xa + coarse, y) * t
        ya = cy0 + math.floor(fy) * coarse
        t = (y - ya) / coarse
        return terrain_h(x, ya) * (1 - t) + terrain_h(x, ya + coarse) * t
    core = grid_mesh("TerrainCore", cx0, cx1, cy0, cy1, step, terrain_h, "lawn", edge_match=edge_match, colfn=dirt_amount)
    outer = grid_mesh("TerrainOuter", -OUTER, OUTER, -OUTER, OUTER, coarse, terrain_h, "lawn_far",
                      hole=CORE, colfn=lambda x, y: 0.35 + 0.35 * noise.noise(Vector((x * 0.02, y * 0.02, 0))))
    # air-raid shelter mound (part of the core lawn material)
    ax, ay = L["airraid"]["pos"]
    mb = MB()
    seg, rings = 18, 6
    cxm, rx, ry, hz = ax + 2.7, 2.5, 3.2, 2.6
    for k in range(rings):
        for i in range(seg):
            a0, a1 = math.pi * 2 * i / seg, math.pi * 2 * (i + 1) / seg
            t0, t1 = k / rings, (k + 1) / rings
            def P(a, t):
                rr = math.cos(t * math.pi / 2)
                return (cxm + rx * rr * math.cos(a), ay + ry * rr * math.sin(a), -0.1 + hz * math.sin(t * math.pi / 2))
            mb.face([P(a0, t0), P(a1, t0), P(a1, t1), P(a0, t1)], "lawn",
                    col=[(0.4, 0.4, 0.4, 1)] * 4)
    mound = mb.to_object("Mound")
    # collision terrain (3 m), clipped to the walkable rectangle
    cmb = COL.mb
    st = 3.0
    xs = [cx0 + i * st for i in range(int((cx1 - cx0) / st) + 1)]
    ys = [cy0 + j * st for j in range(int((cy1 - cy0) / st) + 1)]
    for j in range(len(ys) - 1):
        for i in range(len(xs) - 1):
            p = [(xs[i], ys[j]), (xs[i + 1], ys[j]), (xs[i + 1], ys[j + 1]), (xs[i], ys[j + 1])]
            cmb.face([(x, y, terrain_h(x, y)) for x, y in p], "dark")
    return core, outer, mound


def build_far():
    """Far ring: city plain, coastline, sea floor, Meilun hill, Central Range backdrop (vertex coloured)."""
    mb = MB()
    rings = [OUTER, 520, 700, 1000, 1300, 1700, 2300, 3200, 4500, 6000]
    seg = 120
    def col_for(x, y, z):
        if z < -29:
            return (0.08, 0.14, 0.16, 1)
        if z < -27.6:
            return (0.55, 0.53, 0.47, 1)
        g = 0.35 + 0.25 * noise.noise(Vector((x * 0.004, y * 0.004, 0)))
        return (0.30 * g + 0.12, 0.36 * g + 0.15, 0.24 * g + 0.10, 1)
    for k in range(len(rings) - 1):
        for i in range(seg):
            a0, a1 = 2 * math.pi * i / seg, 2 * math.pi * (i + 1) / seg
            pts = []
            for (r, a) in ((rings[k], a0), (rings[k], a1), (rings[k + 1], a1), (rings[k + 1], a0)):
                if k == 0:
                    # square-ish inner edge hugging the outer terrain grid
                    c, s = math.cos(a), math.sin(a)
                    m = max(abs(c), abs(s))
                    r = r / m if r == rings[0] else r
                x, y = r * math.cos(a), r * math.sin(a)
                pts.append((x, y, far_h(x, y)))
            pts = pts[::-1]          # counter-clockwise from above -> faces up
            mb.face(pts, "far", col=[col_for(*p) for p in pts])
    # Central Range backdrop to the west / north-west (plan dir (-0.88, 0.48))
    seg2 = 90
    base = math.atan2(0.48, -0.88)
    for i in range(seg2):
        a0 = base - 1.5 + 3.0 * i / seg2
        a1 = base - 1.5 + 3.0 * (i + 1) / seg2
        def ridge(a):
            return 900 + 700 * (0.5 + 0.5 * noise.noise(Vector((a * 3.0, 0.5, 0)))) + 300 * noise.noise(Vector((a * 11, 2, 0)))
        R0, R1 = 7500.0, 9500.0
        p = [(R0 * math.cos(a0), R0 * math.sin(a0), -25), (R0 * math.cos(a1), R0 * math.sin(a1), -25),
             (R1 * math.cos(a1), R1 * math.sin(a1), ridge(a1)), (R1 * math.cos(a0), R1 * math.sin(a0), ridge(a0))]
        mb.face(p[::-1], "far", col=[(0.42, 0.50, 0.55, 1)] * 2 + [(0.35, 0.42, 0.40, 1)] * 2)
    # city blocks on the plain (simple boxes, vertex coloured)
    rnd = random.Random(42)
    placed = 0
    while placed < 220:
        r = rnd.uniform(380, 1300)
        a = rnd.uniform(0, 2 * math.pi)
        x, y = r * math.cos(a), r * math.sin(a)
        if x * SEA_DIR[0] + y * SEA_DIR[1] > 860 or math.hypot(x - 783, y - 441) < 330:
            continue
        z = far_h(x, y)
        w, d, h = rnd.uniform(8, 22), rnd.uniform(8, 22), rnd.uniform(6, 24)
        g = rnd.uniform(0.62, 0.86)
        col = (g, g * 0.98, g * 0.95, 1)
        s = len(mb.f)
        mb.obox(x, y, w / 2, d / 2, z - 1, z + h, a, "far", skip=("zn",))
        for fi in range(s, len(mb.f)):
            mb.col[fi] = [col] * 4
        placed += 1
    # Hualien harbour breakwaters
    hp = Vector((1350, -210, 0))
    for (dx, dy, ln, ang) in ((0, 0, 600, 0.4), (120, -260, 420, 1.6)):
        ox, oy = hp.x + dx, hp.y + dy
        s = len(mb.f)
        mb.obox(ox, oy, ln / 2, 12, -31, -26.5, ang, "far", skip=("zn",))
        for fi in range(s, len(mb.f)):
            mb.col[fi] = [(0.72, 0.72, 0.70, 1)] * 4
    far = mb.to_object("Far")
    sea = MB()
    R = 12000
    sea.face([(-R, -R, -30), (R, -R, -30), (R, R, -30), (-R, R, -30)], "sea", [(-R, -R), (R, -R), (R, R), (-R, R)])
    sea_ob = sea.to_object("Sea")
    return far, sea_ob


# --------------------------------------------------------------------------------------------
# vegetation
# --------------------------------------------------------------------------------------------
TREE_POS = []        # (x, y, scale) used by dirt + collision


def atlas_uv(cell, quad_uv):
    cx, cy = cell % 2, cell // 2
    return [((cx + u) * 0.5, 1 - (cy + 1 - v) * 0.5) for (u, v) in quad_uv]


def foliage_card(mb, centre, size, normal_hint, cell, rnd, tilt=0.0, ao=1.0):
    """A pair of crossed cards + one flat-ish card for a needle pad."""
    c = Vector(centre)
    yaw = rnd.uniform(0, math.pi)
    for k in range(2):
        a = yaw + k * math.pi / 2
        d = Vector((math.cos(a), math.sin(a), 0)) * size / 2
        up = Vector((0, 0, size * 0.32))
        p = [c - d - up, c + d - up, c + d + up, c - d + up]
        mb.face([tuple(q) for q in p], "needles", atlas_uv(cell, [(0, 0.15), (1, 0.15), (1, 0.85), (0, 0.85)]),
                col=[(ao, ao, ao, 1)] * 4)
    # flat pad (slightly tilted)
    d1 = Vector((math.cos(yaw), math.sin(yaw), tilt)) * size * 0.5
    d2 = Vector((-math.sin(yaw), math.cos(yaw), 0)) * size * 0.42
    p = [c - d1 - d2, c + d1 - d2, c + d1 + d2, c - d1 + d2]
    mb.face([tuple(q) for q in p], "needles", atlas_uv(cell, [(0, 0), (1, 0), (1, 1), (0, 1)]), col=[(ao, ao, ao, 1)] * 4)


def tube(mb, pts, radii, seg, mat, cap=False):
    """Tube along a polyline (list of Vectors) with per-point radii."""
    frames = []
    for i, p in enumerate(pts):
        t = (pts[min(i + 1, len(pts) - 1)] - pts[max(i - 1, 0)]).normalized()
        ref = Vector((0, 0, 1)) if abs(t.z) < 0.9 else Vector((1, 0, 0))
        n = t.cross(ref).normalized()
        b = t.cross(n).normalized()
        frames.append((n, b))
    vlen = 0
    for i in range(len(pts) - 1):
        seglen = (pts[i + 1] - pts[i]).length
        for k in range(seg):
            a0, a1 = 2 * math.pi * k / seg, 2 * math.pi * (k + 1) / seg
            q = []
            for (ii, a) in ((i, a0), (i, a1), (i + 1, a1), (i + 1, a0)):
                n, b = frames[ii]
                q.append(tuple(pts[ii] + (n * math.cos(a) + b * math.sin(a)) * radii[ii]))
            circ = 2 * math.pi * max(radii[i], 0.05)
            mb.face(q, mat, [(k / seg * circ, vlen), ((k + 1) / seg * circ, vlen),
                             ((k + 1) / seg * circ, vlen + seglen), (k / seg * circ, vlen + seglen)],
                    col=[(0.75, 0.75, 0.75, 1)] * 4)
        vlen += seglen


def make_pine(kind, seed, lod=0):
    """Ryukyu pine: leaning trunk, near-horizontal limbs ending in flat needle pads.
    Lean is along +X in model space. Returns MB."""
    rnd = random.Random(seed)
    mb = MB()
    if kind == "A":
        H, base_r, lean, crown0 = rnd.uniform(14, 16.5), 0.34, rnd.uniform(0.28, 0.4), 0.48
    elif kind == "B":
        H, base_r, lean, crown0 = rnd.uniform(11, 13), 0.38, rnd.uniform(0.12, 0.22), 0.38
    else:
        H, base_r, lean, crown0 = rnd.uniform(4, 5), 0.09, 0.08, 0.3
    nseg = 9 if lod == 0 else 5
    seg = 9 if lod == 0 else 5
    trunk = []
    for i in range(nseg + 1):
        t = i / nseg
        x = H * lean * (t ** 1.3) - H * lean * 0.35 * max(0, t - 0.7) ** 2   # sweeps back up near the top
        y = 0.4 * math.sin(t * 2.7 + seed) * t
        trunk.append(Vector((x, y, H * t)))
    radii = [base_r * (1.15 - 0.85 * (i / nseg) ** 0.8) for i in range(nseg + 1)]
    tube(mb, trunk, radii, seg, "bark")
    pads = []
    nb = (10 if kind == "A" else 12 if kind == "B" else 6) if lod == 0 else 6
    for k in range(nb):
        t = crown0 + (1 - crown0) * (k + 0.5) / nb + rnd.uniform(-0.04, 0.04)
        idx = t * nseg
        i0 = min(int(idx), nseg - 1)
        p0 = trunk[i0].lerp(trunk[i0 + 1], idx - i0)
        a = rnd.uniform(0, 2 * math.pi) if kind != "A" else rnd.uniform(-1.9, 1.9)
        ln = (H * (0.40 if kind == "B" else 0.30)) * (1.15 - 0.5 * (t - crown0) / (1 - crown0)) * rnd.uniform(0.8, 1.2)
        d = Vector((math.cos(a), math.sin(a), rnd.uniform(0.05, 0.3))).normalized()
        mid = p0 + d * ln * 0.5 + Vector((0, 0, ln * 0.05))
        end = p0 + d * ln + Vector((0, 0, ln * rnd.uniform(0.05, 0.2)))
        br = radii[i0] * 0.45
        if lod == 0:
            tube(mb, [p0, mid, end], [br, br * 0.6, br * 0.25], 5, "bark")
        # needle pads along the outer half of the limb
        npads = 10 if lod == 0 else 2
        for j in range(npads):
            tt = 0.45 + 0.55 * (j + 1) / npads
            pc = p0.lerp(end, tt) + Vector((rnd.uniform(-0.9, 0.9), rnd.uniform(-0.9, 0.9), rnd.uniform(0.0, 0.8)))
            pads.append((pc, ln))
    # top tuft
    pads.append((trunk[-1] + Vector((0, 0, 0.4)), H * 0.12))
    cen = sum((p for p, _ in pads), Vector()) / len(pads)
    zmin = min(p.z for p, _ in pads)
    zmax = max(p.z for p, _ in pads)
    for (pc, ln) in pads:
        size = (1.7 if kind == "B" else 1.5 if kind == "A" else 1.0) * rnd.uniform(0.8, 1.25)
        if lod:
            size *= 1.5
        h = (pc.z - zmin) / max(0.1, zmax - zmin)
        ao = 0.55 + 0.45 * h
        cell = 3 if lod else rnd.choice((0, 1, 0, 1, 2))
        foliage_card(mb, pc, size, None, cell, rnd, tilt=rnd.uniform(-0.15, 0.15), ao=ao)
    return mb, cen


def make_shrub(seed, lod=0):
    rnd = random.Random(seed)
    mb = MB()
    trunk = [Vector((0, 0, 0)), Vector((0.1, 0, 1.0)), Vector((0, 0.1, 1.8))]
    tube(mb, trunk, [0.12, 0.09, 0.05], 6, "bark")
    n = 26 if lod == 0 else 8
    cen = Vector((0, 0, 2.4))
    for k in range(n):
        u, v = rnd.uniform(-1, 1), rnd.uniform(0, 2 * math.pi)
        r = 1.5 * rnd.uniform(0.6, 1.0)
        p = cen + Vector((math.sqrt(1 - u * u) * math.cos(v) * r, math.sqrt(1 - u * u) * math.sin(v) * r, u * r * 0.85))
        size = rnd.uniform(1.1, 1.6) * (1.4 if lod else 1)
        a = rnd.uniform(0, math.pi)
        d = Vector((math.cos(a), math.sin(a), 0)) * size / 2
        up = Vector((0, 0, size / 2))
        ao = 0.55 + 0.45 * (u * 0.5 + 0.5)
        q = [p - d - up, p + d - up, p + d + up, p - d + up]
        mb.face([tuple(x) for x in q], "shrub", atlas_uv(k % 4, [(0, 0), (1, 0), (1, 1), (0, 1)]), col=[(ao, ao, ao, 1)] * 4)
    return mb, cen


def set_foliage_normals(ob, centre, mat_names):
    """Point normals of foliage cards away from the crown centre (soft volumetric shading)."""
    me = ob.data
    names = [m.name for m in me.materials]
    normals = []
    for poly in me.polygons:
        is_fol = names[poly.material_index].split(".")[0] in mat_names
        for li in poly.loop_indices:
            vi = me.loops[li].vertex_index
            if is_fol:
                v = me.vertices[vi].co - Vector(centre)
                v.z *= 0.6
                v += Vector((0, 0, 0.6 * v.length / 3 + 0.4))
                normals.append(v.normalized())
            else:
                normals.append(poly.normal.copy())
    me.normals_split_custom_set(normals)


TREE_TYPES = {}     # name -> {lod0: obj, lod1: obj}


def build_tree_types(coll):
    specs = [("A1", "A", 11), ("A2", "A", 12), ("B1", "B", 21), ("B2", "B", 22), ("C1", "C", 31)]
    for name, kind, seed in specs:
        TREE_TYPES[name] = {}
        for lod in (0, 1):
            mb, cen = make_pine(kind, seed, lod)
            ob = mb.to_object("Tree_%s_LOD%d" % (name, lod), collection=coll)
            set_foliage_normals(ob, cen, ("needles",))
            TREE_TYPES[name][lod] = ob
    for lod in (0, 1):
        mb, cen = make_shrub(41, lod)
        ob = mb.to_object("Tree_S1_LOD%d" % lod, collection=coll)
        set_foliage_normals(ob, cen, ("shrub",))
        TREE_TYPES.setdefault("S1", {})[lod] = ob


def tour_dist(x, y):
    pts = [p["p"] for p in TOUR["points"]]
    pts = [TOUR["start"]] + pts
    return poly_dist(x, y, [(p[0], p[1]) for p in pts], closed=False)[0]


def scatter_trees():
    rnd = random.Random(L["trees"]["seed"])
    inst = []
    clear = L["trees"]["keep_clear"]

    def ok(x, y, rmin):
        if not point_in_poly(x, y, BOUNDARY):
            return False
        if poly_dist(x, y, BOUNDARY)[0] < 1.0:
            return False
        if in_footprint(x, y, 2.5):
            return False
        t = L["terrace"]
        if t["x0"] - 1 < x < t["x1"] + 1 and t["y0"] - 1 < y < t["y1"] + 1:
            return False
        if path_dist(x, y) < 0.9 or tour_dist(x, y) < 1.8:
            return False
        for c in clear:
            if math.hypot(x - c["c"][0], y - c["c"][1]) < c["r"]:
                return False
        for q in inst:
            if math.hypot(x - q["x"], y - q["y"]) < rmin:
                return False
        return True
    for s in L["trees"]["signature"]:
        x, y = s["p"]
        lx, ly = s["lean"]
        rot = math.atan2(ly, lx)
        typ = s["t"] + ("1" if len(inst) % 2 == 0 else "2")
        inst.append({"type": typ, "x": x, "y": y, "z": terrain_h(x, y), "rot": rot, "s": s["s"], "zone": "garden"})
    tries = 0
    target = L["trees"]["garden_count"] if not ARGS["quick"] else 20
    while sum(1 for q in inst if q["zone"] == "garden") < target and tries < 20000:
        tries += 1
        x, y = rnd.uniform(-95, 38), rnd.uniform(-48, 40)
        if not ok(x, y, 5.5):
            continue
        typ = rnd.choice(["A1", "A2", "B1", "B2", "A1"])
        # leaning roughly away from the sea wind (toward -sea_dir) with variation
        rot = math.atan2(-SEA_DIR[1], -SEA_DIR[0]) + rnd.uniform(-1.2, 1.2)
        inst.append({"type": typ, "x": x, "y": y, "z": terrain_h(x, y), "rot": rot, "s": rnd.uniform(0.85, 1.12), "zone": "garden"})
    n_young = 0
    while n_young < L["trees"]["young_count"] and tries < 40000:
        tries += 1
        x, y = rnd.uniform(-60, 35), rnd.uniform(-45, 38)
        if not ok(x, y, 4.0):
            continue
        inst.append({"type": "C1", "x": x, "y": y, "z": terrain_h(x, y), "rot": rnd.uniform(0, 6.28), "s": rnd.uniform(0.9, 1.1), "zone": "garden"})
        n_young += 1
    for (x, y) in L["trees"]["shrubs"]:
        inst.append({"type": "S1", "x": x, "y": y, "z": terrain_h(x, y), "rot": rnd.uniform(0, 6.28), "s": rnd.uniform(0.9, 1.1), "zone": "garden"})
    # forest outside the garden: slopes to the south, Meilun hill side, along streets
    target_f = L["trees"]["forest_count"] if not ARGS["quick"] else 80
    nf = 0
    while nf < target_f and tries < 200000:
        tries += 1
        r = rnd.uniform(25, 300)
        a = rnd.uniform(0, 2 * math.pi)
        x, y = r * math.cos(a), r * math.sin(a)
        if point_in_poly(x, y, BOUNDARY) or poly_dist(x, y, BOUNDARY)[0] < 6:
            continue
        # avoid the neighbour building footprints near the street (OSM)
        if -78 < x < -20 and 60 < y < 98:
            continue
        s_r = x * RIVER_DIR[0] + y * RIVER_DIR[1]
        if 108 < s_r < 168:          # river channel
            continue
        dens = 0.35 + 0.65 * smoothstep(-60, 20, -s_r if s_r > 0 else 30)
        if rnd.random() > dens:
            continue
        typ = rnd.choice(["A1", "A2", "B1", "B2", "S1"])
        inst.append({"type": typ, "x": x, "y": y, "z": terrain_h(x, y), "rot": rnd.uniform(0, 6.28),
                     "s": rnd.uniform(0.8, 1.15), "zone": "forest"})
        nf += 1
    for q in inst:
        TREE_POS.append((q["x"], q["y"], q["s"]))
    return inst


def place_tree_instances(inst, coll):
    for k, q in enumerate(inst):
        src = TREE_TYPES[q["type"]][0 if q["zone"] == "garden" else 1]
        ob = bpy.data.objects.new("inst_%03d" % k, src.data)
        ob.location = (q["x"], q["y"], q["z"] - 0.15)
        ob.rotation_euler = (0, 0, q["rot"])
        ob.scale = (q["s"],) * 3
        coll.objects.link(ob)
        if q["zone"] == "garden" and q["type"] != "S1":
            # collision: tilted cylinder approximating the lowest 3 m of trunk
            r = (0.42 if q["type"].startswith("B") else 0.38) * q["s"] if q["type"] != "C1" else 0.12
            lean_x = 0.33 if q["type"].startswith("A") else 0.17
            cx, cy = q["x"], q["y"]
            dx, dy = math.cos(q["rot"]) * lean_x * 1.4, math.sin(q["rot"]) * lean_x * 1.4
            seg = 8
            for i in range(seg):
                a0, a1 = 2 * math.pi * i / seg, 2 * math.pi * (i + 1) / seg
                p = [(cx + r * math.cos(a0), cy + r * math.sin(a0), q["z"] - 0.5),
                     (cx + r * math.cos(a1), cy + r * math.sin(a1), q["z"] - 0.5),
                     (cx + dx + r * 0.9 * math.cos(a1), cy + dy + r * 0.9 * math.sin(a1), q["z"] + 3.2),
                     (cx + dx + r * 0.9 * math.cos(a0), cy + dy + r * 0.9 * math.sin(a0), q["z"] + 3.2)]
                COL.mb.face(p, "dark")


def build_ivy(surfaces):
    rnd = random.Random(5)
    mb = MB()

    def card(p, nrm, size):
        nrm = Vector(nrm).normalized()
        up = Vector((0, 0, 1))
        t = up.cross(nrm)
        if t.length < 1e-3:
            t = Vector((1, 0, 0))
        t.normalize()
        b = nrm.cross(t)
        a = rnd.uniform(0, 2 * math.pi)
        t2 = t * math.cos(a) + b * math.sin(a)
        b2 = nrm.cross(t2)
        c = Vector(p) + nrm * rnd.uniform(0.04, 0.16)
        h = size / 2
        q = [c - t2 * h - b2 * h, c + t2 * h - b2 * h, c + t2 * h + b2 * h, c - t2 * h + b2 * h]
        # tilt card slightly outward at the top so leaves overlap like shingles
        q[2] += nrm * 0.08
        q[3] += nrm * 0.08
        ao = rnd.uniform(0.6, 1.0)
        mb.face([tuple(x) for x in q], "ivy", atlas_uv(rnd.randrange(4), [(0, 0), (1, 0), (1, 1), (0, 1)]),
                col=[(ao, ao, ao, 1)] * 4)

    def coverage(x, y, z, density):
        n = noise.noise(Vector((x * 0.45, y * 0.45, z * 0.45)))
        return n + density - 0.5 > 0
    r = M["arch_r"]
    for s in surfaces:
        if s[0] == "front":
            _, xl, xr, y0, zb, ztop = s
            area = (xr - xl) * (ztop - zb)
            for k in range(int(area * 22)):
                x, z = rnd.uniform(xl, xr), rnd.uniform(zb, ztop)
                xc = (xl + xr) / 2
                spring = M["arch_spring1"] if zb < 1 else M["arch_spring2"]
                inside = (abs(x - xc) < 1.4 - 0.15 and z < spring) or (math.hypot(x - xc, z - spring) < r - 0.15 and z >= spring)
                if inside or not coverage(x, y0, z, 0.75):
                    continue
                card((x, y0, z), (0, -1, 0), rnd.uniform(0.4, 0.62))
        elif s[0] == "end":
            _, x, fac, y0, y1, eave = s
            for k in range(int((y1 - y0) * eave * 24)):
                y, z = rnd.uniform(y0, y1), rnd.uniform(0.0, eave)
                if not coverage(x, y, z, 0.95):
                    continue
                card((x, y, z), (fac, 0, 0), rnd.uniform(0.45, 0.65))
        elif s[0] == "gable":
            _, x, y0, y1, eave, zr = s
            yc = (y0 + y1) / 2
            for k in range(int((y1 - y0) * (zr - eave) * 12)):
                y, z = rnd.uniform(y0, y1), rnd.uniform(eave, zr)
                if z - eave > (zr - eave) * (1 - abs(y - yc) / ((y1 - y0) / 2)) - 0.2:
                    continue
                card((x, y, z), (1, 0, 0), rnd.uniform(0.45, 0.65))
        elif s[0] == "block":
            _, b, zr = s
            x0, x1, y0, y1, ze = b["x0"], b["x1"], b["y0"], b["y1"], b["eave"]
            dens = b["ivy"]
            faces = [((x0, y0), (x1, y0), (0, -1, 0)), ((x1, y0), (x1, y1), (1, 0, 0)),
                     ((x1, y1), (x0, y1), (0, 1, 0)), ((x0, y1), (x0, y0), (-1, 0, 0))]
            for (a, bb, nrm) in faces:
                ln = math.dist(a, bb)
                for k in range(int(ln * ze * 22)):
                    t, z = rnd.random(), rnd.uniform(0, ze)
                    x, y = a[0] + (bb[0] - a[0]) * t, a[1] + (bb[1] - a[1]) * t
                    if nrm[1] < 0 and abs(x - (x0 + x1) / 2) < 0.9 and z < 2.6:
                        continue    # keep the door free
                    if not coverage(x, y, z, dens):
                        continue
                    card((x, y, z), nrm, rnd.uniform(0.45, 0.65))
            if b["roof"] == "gable_y":
                xc = (x0 + x1) / 2
                for (yy, ny) in ((y0, -1), (y1, 1)):
                    for k in range(int((x1 - x0) * (zr - ze) * 12)):
                        x, z = rnd.uniform(x0, x1), rnd.uniform(ze, zr)
                        if z - ze > (zr - ze) * (1 - abs(x - xc) / ((x1 - x0) / 2)) - 0.2:
                            continue
                        card((x, yy, z), (0, ny, 0), rnd.uniform(0.45, 0.65))
    ob = mb.to_object("Ivy")
    # ivy normals: card normals blended toward the wall normal (already ~ wall normal)
    return ob


# --------------------------------------------------------------------------------------------
# lighting / world
# --------------------------------------------------------------------------------------------
def setup_world_and_sun():
    sc = bpy.context.scene
    world = bpy.data.worlds.new("Sky")
    sc.world = world
    nt = world.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new("ShaderNodeOutputWorld")
    bg = nt.nodes.new("ShaderNodeBackground")
    tc = nt.nodes.new("ShaderNodeTexCoord")
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    mr = nt.nodes.new("ShaderNodeMapRange")
    mr.inputs["From Min"].default_value = -1.0
    mr.inputs["From Max"].default_value = 1.0
    nt.links.new(tc.outputs["Generated"], sep.inputs[0])
    nt.links.new(sep.outputs["Z"], mr.inputs["Value"])
    nt.links.new(mr.outputs["Result"], ramp.inputs["Fac"])
    cr = ramp.color_ramp
    cr.elements[0].position = 0.0
    cr.elements[0].color = (0.30, 0.31, 0.30, 1)
    cr.elements[1].position = 1.0
    cr.elements[1].color = (0.12, 0.30, 0.72, 1)
    e = cr.elements.new(0.5)
    e.color = (0.80, 0.86, 0.92, 1)
    e = cr.elements.new(0.58)
    e.color = (0.42, 0.60, 0.88, 1)
    nt.links.new(ramp.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = 1.15
    nt.links.new(bg.outputs[0], out.inputs[0])
    world.light_settings.distance = 2.5          # AO distance for AO bakes
    sun_data = bpy.data.lights.new("Sun", "SUN")
    sun_data.energy = 3.6
    sun_data.angle = math.radians(2.2)
    sun_data.color = (1.0, 0.95, 0.86)
    sun = bpy.data.objects.new("Sun", sun_data)
    sc.collection.objects.link(sun)
    sun.rotation_euler = Vector(SUN_DIR).to_track_quat("Z", "Y").to_euler()


def setup_cycles(samples):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples
    sc.cycles.max_bounces = 4
    sc.cycles.diffuse_bounces = 3
    sc.cycles.glossy_bounces = 1
    sc.cycles.transmission_bounces = 2
    sc.cycles.transparent_max_bounces = 10
    sc.cycles.use_denoising = True
    try:
        sc.cycles.denoiser = "OPENIMAGEDENOISE"
    except Exception:
        pass
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.look = "None"


# --------------------------------------------------------------------------------------------
# baking
# --------------------------------------------------------------------------------------------
def chop_islands(ob, cell=4.0):
    """Cut the mesh on a 3D grid and split the cut edges so no UV island is longer than `cell` metres."""
    me = ob.data
    bm = bmesh.new()
    bm.from_mesh(me)
    cut_edges = set()
    for axis in range(3):
        lo = min(v.co[axis] for v in bm.verts)
        hi = max(v.co[axis] for v in bm.verts)
        k = math.floor(lo / cell) + 1
        while k * cell < hi:
            no = Vector((0, 0, 0)); no[axis] = 1
            co = Vector((0, 0, 0)); co[axis] = k * cell
            geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
            res = bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-5, plane_co=co, plane_no=no)
            for e in res["geom_cut"]:
                if isinstance(e, bmesh.types.BMEdge):
                    cut_edges.add(e)
            k += 1
    cut_edges = [e for e in cut_edges if e.is_valid]
    bmesh.ops.split_edges(bm, edges=cut_edges)
    bm.to_mesh(me)
    bm.free()
    me.update()


def add_lightmap_uv(ob, margin=0.004):
    me = ob.data
    if "LM" not in me.uv_layers:
        me.uv_layers.new(name="LM")
    me.uv_layers.active = me.uv_layers["LM"]
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(60), island_margin=margin, area_weight=0.0,
                             correct_aspect=True, scale_to_bounds=False)
    bpy.ops.uv.select_all(action="SELECT")
    try:
        bpy.ops.uv.pack_islands(udim_source="CLOSEST_UDIM", rotate=True, margin_method="FRACTION",
                                margin=margin, shape_method="CONCAVE")
    except TypeError:
        bpy.ops.uv.pack_islands(rotate=True, margin=margin)
    bpy.ops.object.mode_set(mode="OBJECT")
    me.uv_layers.active = me.uv_layers["UVMap"]


def bake_object(ob, name, size, kind="LM"):
    """Bake lightmap (irradiance) or AO for one object into BAKE_DIR/name.png (+ .exr for LM)."""
    img = bpy.data.images.new(name, size, size, alpha=False, float_buffer=(kind == "LM"))
    nodes_added = []
    for slot in ob.material_slots:
        nt = slot.material.node_tree
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = img
        nt.nodes.active = n
        nodes_added.append((nt, n))
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    sc = bpy.context.scene
    sc.render.bake.margin = 8
    sc.render.bake.use_clear = True
    sc.render.bake.target = "IMAGE_TEXTURES"
    t = time.time()
    if kind == "LM":
        bpy.ops.object.bake(type="DIFFUSE", pass_filter={"DIRECT", "INDIRECT"}, uv_layer="LM", margin=8)
    else:
        bpy.ops.object.bake(type="AO", uv_layer="LM", margin=8)
    log("baked", name, size, kind, "%.0fs" % (time.time() - t))
    import numpy as np
    px = np.empty(size * size * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    px = px.reshape(size, size, 4)
    rgb = px[..., :3]
    # light denoise: 3x3 box blur blended 50%
    pad = np.pad(rgb, ((1, 1), (1, 1), (0, 0)), mode="edge")
    blur = sum(pad[dy:dy + size, dx:dx + size] for dy in range(3) for dx in range(3)) / 9.0
    rgb = rgb * 0.45 + blur * 0.55
    if kind == "LM":
        enc = np.clip(rgb / LM_SCALE, 0, 1) ** (1 / 2.2)
    else:
        enc = np.clip(rgb, 0, 1)
    out = np.concatenate([enc, np.ones((size, size, 1), np.float32)], axis=2)
    img8 = bpy.data.images.new(name + "_8", size, size, alpha=False)
    img8.pixels.foreach_set(out.ravel())
    img8.filepath_raw = os.path.join(BAKE_DIR, name + ".png")
    img8.file_format = "PNG"
    img8.save()
    for nt, n in nodes_added:
        nt.nodes.remove(n)
    return img8


LM_SCALE = 4.0      # encoded = (irradiance / LM_SCALE) ^ (1/2.2)


# --------------------------------------------------------------------------------------------
# export
# --------------------------------------------------------------------------------------------
def gltf_export(path, objs):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    props = bpy.ops.export_scene.gltf.get_rna_type().properties.keys()
    kw = dict(filepath=path, export_format="GLB", use_selection=True, export_apply=True, export_yup=True,
              export_texcoords=True, export_normals=True, export_materials="EXPORT", export_image_format="NONE",
              export_vertex_color="ACTIVE", export_cameras=False, export_lights=False, export_extras=False,
              export_animations=False, export_skins=False, export_morph=False)
    kw = {k: v for k, v in kw.items() if k in props or k == "filepath"}
    bpy.ops.export_scene.gltf(**kw)
    log("exported", os.path.basename(path), "%.1f MB" % (os.path.getsize(path) / 1e6))


def to_three(x, y, z):
    return [round(x, 3), round(z, 3), round(-y, 3)]


# --------------------------------------------------------------------------------------------
# renders
# --------------------------------------------------------------------------------------------
def camera(name, loc, look, lens=24, ortho=None):
    cd = bpy.data.cameras.new(name)
    cd.lens = lens
    cd.clip_end = 20000
    if ortho:
        cd.type = "ORTHO"
        cd.ortho_scale = ortho
    ob = bpy.data.objects.new(name, cd)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = loc
    d = Vector(look) - Vector(loc)
    ob.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()
    return ob


def render_views(samples):
    sc = bpy.context.scene
    setup_cycles(samples)
    sc.render.resolution_x, sc.render.resolution_y = (1280, 720) if not ARGS["quick"] else (640, 360)
    sc.render.image_settings.file_format = "JPEG"
    sc.render.image_settings.quality = 88
    views = [
        ("01_front_lawn", (-6, -34, 1.6), (2, -4, 6.5), 22),
        ("02_balcony_sea", (10.2, -4.6, 6.05), (98, -53, 2.0), 20),
        ("03_aerial", (70, -95, 55), (-2, -6, 2), 30),
        ("04_west_pines", (-38, -30, 1.4), (-8, -2, 7), 18),
        ("05_stair_hall", (0.0, -5.0, 2.0), (0.0, 2.0, 3.8), 16),
    ]
    for name, loc, look, lens in views:
        cam = camera("cam_" + name, loc, look, lens)
        sc.camera = cam
        sc.render.filepath = os.path.join(OUT_RENDERS, name + ".jpg")
        t = time.time()
        bpy.ops.render.render(write_still=True)
        log("render", name, "%.0fs" % (time.time() - t))
    # minimap (top orthographic)
    mm = MINIMAP
    cx, cy = (mm[0] + mm[1]) / 2, (mm[2] + mm[3]) / 2
    cam = camera("cam_minimap", (cx, cy, 200), (cx, cy, 0), ortho=mm[1] - mm[0])
    cam.rotation_euler = (0, 0, 0)
    sc.camera = cam
    sc.render.resolution_x = sc.render.resolution_y = 1024
    sc.cycles.samples = max(16, samples // 3)
    sc.render.filepath = os.path.join(OUT_RENDERS, "minimap.jpg")
    bpy.ops.render.render(write_still=True)
    log("render minimap")


MINIMAP = (-100.0, 60.0, -84.0, 76.0)   # x0,x1,y0,y1 (square, 160 m)


# --------------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------------
def main():
    os.makedirs(OUT_MODELS, exist_ok=True)
    os.makedirs(OUT_RENDERS, exist_ok=True)
    os.makedirs(BAKE_DIR, exist_ok=True)
    os.makedirs(RAW_DIR, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.unit_settings.system = "METRIC"
    make_materials()
    setup_world_and_sun()

    trees_coll = bpy.data.collections.new("TreeTypes")
    sc.collection.children.link(trees_coll)
    inst_coll = bpy.data.collections.new("TreeInstances")
    sc.collection.children.link(inst_coll)

    log("trees: scatter")
    inst = scatter_trees()
    log("terrain")
    core, outer, mound = build_terrain()
    log("architecture")
    mb = MB()
    ivy_s = []
    build_main(mb, ivy_s)
    build_annexes(mb, ivy_s)
    gmb, pmb = MB(), MB()
    walk_boundary = build_site(mb, gmb, pmb)
    arch = mb.to_object("Arch")
    ground = gmb.to_object("Ground")
    props = pmb.to_object("Props")
    # Ground (paths, decks) re-uses the terrain lightmap through a planar LM UV over the core rectangle
    gl = ground.data.uv_layers.new(name="LM")
    cx0, cx1, cy0, cy1 = CORE
    uvs = []
    for poly in ground.data.polygons:
        for li in poly.loop_indices:
            co = ground.data.vertices[ground.data.loops[li].vertex_index].co
            uvs += [(co.x - cx0) / (cx1 - cx0), (co.y - cy0) / (cy1 - cy0)]
    gl.data.foreach_set("uv", uvs)
    # merge the mound into the core-lawn bake group via its own object (shares lawn material)
    ivy = build_ivy(ivy_s)
    far, sea = build_far()
    log("tree types")
    build_tree_types(trees_coll)
    place_tree_instances(inst, inst_coll)
    trees_coll.hide_render = True
    for o in trees_coll.objects:
        o.hide_render = True
    col = COL.mb.to_object("Collision")
    col.hide_render = True
    log("faces: arch %d, ivy %d, core %d, outer %d, far %d, collision %d" % (
        len(arch.data.polygons), len(ivy.data.polygons), len(core.data.polygons), len(outer.data.polygons),
        len(far.data.polygons), len(col.data.polygons)))

    log("lightmap UVs")
    chop_islands(arch, 4.0)
    add_lightmap_uv(arch, 0.002)
    add_lightmap_uv(mound, 0.01)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(HERE, "pine_garden.blend"))

    if ARGS["bake"]:
        setup_cycles(ARGS["samples"])
        q = ARGS["quick"]
        bake_object(arch, "lm_arch", 1024 if q else 2048, "LM")
        ground.hide_render = True
        bake_object(core, "lm_core", 1024 if q else 2048, "LM")
        bake_object(outer, "lm_outer", 512 if q else 1024, "LM")
        bake_object(mound, "lm_mound", 256, "LM")
        bpy.context.scene.cycles.samples = max(16, ARGS["samples"] // 2)
        bake_object(arch, "ao_arch", 512 if q else 1024, "AO")
        bake_object(core, "ao_core", 512 if q else 1024, "AO")
        ground.hide_render = False
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(HERE, "pine_garden.blend"))

    if ARGS["render"]:
        render_views(ARGS["samples"])

    if ARGS["export"]:
        gltf_export(os.path.join(RAW_DIR, "scene_raw.glb"), [arch, ground, props, core, outer, mound, ivy, far, sea])
        types = [o for d in TREE_TYPES.values() for o in d.values()]
        for o in types:
            o.hide_render = False
        gltf_export(os.path.join(RAW_DIR, "trees_raw.glb"), types)
        gltf_export(os.path.join(RAW_DIR, "collision_raw.glb"), [col])
        tj = [{"t": q["type"], "p": to_three(q["x"], q["y"], q["z"] - 0.15), "r": round(q["rot"], 3),
               "s": round(q["s"], 3), "z": q["zone"]} for q in inst]
        json.dump({"instances": tj}, open(os.path.join(OUT_MODELS, "trees.json"), "w"))
        sun3 = [SUN_DIR.x, SUN_DIR.z, -SUN_DIR.y]
        meta = {
            "frame": "three = (X, Z, -Y) of plan coords",
            "sun_dir": [round(v, 4) for v in sun3],
            "lm_scale": LM_SCALE,
            "lightmaps": {"Arch": ["lm_arch", "ao_arch"], "TerrainCore": ["lm_core", "ao_core"],
                          "TerrainOuter": ["lm_outer", None], "Mound": ["lm_mound", None], "Ground": ["lm_core", "ao_core"]},
            "lm_core_rect": list(CORE),
            "minimap": {"x0": MINIMAP[0], "x1": MINIMAP[1], "y0": MINIMAP[2], "y1": MINIMAP[3]},
            "walk_boundary": [[round(p[0], 2), round(p[1], 2)] for p in walk_boundary],
            "materials": {k: {"tile": [v[1], v[2]], "tint": v[3], "rough": v[4], "tex": v[0], **v[5]} for k, v in MAT_DEFS.items()},
        }
        json.dump(meta, open(os.path.join(OUT_MODELS, "scene_meta.json"), "w"), ensure_ascii=False, indent=1)
        # plan-view data for the docs plan / minimap overlays
        json.dump({"trees": [[round(q["x"], 2), round(q["y"], 2), q["type"], q["zone"]] for q in inst]},
                  open(os.path.join(HERE, "trees_plan.json"), "w"))
    log("done")


main()
