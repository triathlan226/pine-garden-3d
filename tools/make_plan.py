"""Generate docs/plan.svg (site plan) from blender/layout.json, the scattered tree list and content/*.json.

    python tools/make_plan.py
"""
import json
import math
import os

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
L = json.load(open(os.path.join(ROOT, "blender", "layout.json"), encoding="utf-8"))
H = json.load(open(os.path.join(ROOT, "content", "hotspots.json"), encoding="utf-8"))["hotspots"]
T = json.load(open(os.path.join(ROOT, "content", "tour.json"), encoding="utf-8"))
trees_path = os.path.join(ROOT, "blender", "trees_plan.json")
TREES = json.load(open(trees_path))["trees"] if os.path.exists(trees_path) else []

X0, X1, Y0, Y1 = -100, 50, -56, 46
S = 7.0          # px per metre
W, HH = (X1 - X0) * S, (Y1 - Y0) * S + 70
CAT = {"史實": "#2f7a5f", "流傳說法": "#b5701d", "推測重建": "#6a5aa8"}


def P(x, y):
    return (round((x - X0) * S, 1), round((Y1 - y) * S, 1))


def poly(pts, **a):
    d = " ".join("%s,%s" % P(*p) for p in pts)
    attrs = " ".join('%s="%s"' % (k.replace("_", "-"), v) for k, v in a.items())
    return '<polygon points="%s" %s/>' % (d, attrs)


def line(pts, **a):
    d = " ".join("%s,%s" % P(*p) for p in pts)
    attrs = " ".join('%s="%s"' % (k.replace("_", "-"), v) for k, v in a.items())
    return '<polyline points="%s" fill="none" %s/>' % (d, attrs)


def rect(x0, x1, y0, y1, **a):
    return poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], **a)


def text(x, y, s, size=13, **a):
    px, py = P(x, y)
    attrs = " ".join('%s="%s"' % (k.replace("_", "-"), v) for k, v in a.items())
    return '<text x="%s" y="%s" font-size="%s" %s>%s</text>' % (px, py, size, attrs, s)


out = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" font-family="Noto Sans TC, Microsoft JhengHei, sans-serif">' % (W, HH)]
out.append('<rect width="100%" height="100%" fill="#f5f0e6"/>')
# slope beyond the south boundary
out.append(poly([(X0, Y0), (X1, Y0), (X1, -42), (36.95, -40.76), (34.55, -48.25), (1.23, -37.87), (-31.37, -26.03), (-61.17, -0.29), (-93.53, 25.3), (X0, 25)], fill="#d9d2bf"))
out.append(text(-70, -40, "往美崙溪 ↓ 坡降約 18 m", 14, fill="#7a6f57"))
out.append(poly(L["boundary"], fill="#dfe8c9", stroke="#6f7f55", stroke_width=2))
for p in L["paths"]:
    col = "#a77a4f" if p["kind"] == "deck" else "#c9bda1"
    pts = p["pts"] + ([p["pts"][0]] if p.get("closed") else [])
    out.append(line(pts, stroke=col, stroke_width=p["w"] * S, stroke_linejoin="round", stroke_linecap="round", opacity=0.9))
for x, y, typ, zone in TREES:
    if zone != "garden" or not (X0 < x < X1 and Y0 < y < Y1):
        continue
    r = 3.2 if typ.startswith("B") else 2.6 if typ.startswith("A") else 1.4
    out.append('<circle cx="%s" cy="%s" r="%s" fill="#3f6b3a" opacity="0.35"/>' % (*P(x, y), r * S))
    out.append('<circle cx="%s" cy="%s" r="2.2" fill="#2d4a28"/>' % P(x, y))
m = L["main"]
out.append(rect(m["x0"], m["x1"], m["y0"], m["y1"], fill="#9a9a94", stroke="#333", stroke_width=1.5))
out.append(rect(m["x0"] + 0.5, m["x1"] - 0.5, m["y0"], m["y0"] + m["arcade_depth"], fill="#c9c6bc", stroke="#555", stroke_width=0.8))
xa = -m["bays"] * m["bay"] / 2
for i in range(m["bays"] + 1):
    x = xa + i * m["bay"]
    out.append(rect(x - 0.3, x + 0.3, m["y0"], m["y0"] + 0.6, fill="#555"))
out.append(rect(-2.2, 2.2, m["y0"] + m["arcade_depth"], m["y1"] - 0.3, fill="#b8b4a8", stroke="#555", stroke_width=0.8))
out.append(text(-14, 1.2, "主建築（兩層・9 跨拱廊）", 14, fill="#fff", font_weight="bold"))
out.append(text(-1.9, 4.2, "樓梯間", 11, fill="#333"))
out.append(text(-14, -4.9, "一／二樓拱廊", 11, fill="#333"))
for b in L["buildings"]:
    out.append(rect(b["x0"], b["x1"], b["y0"], b["y1"], fill="#8f8a80" if not b.get("wood") else "#8a6a4c", stroke="#333", stroke_width=1))
    cx, cy = (b["x0"] + b["x1"]) / 2, (b["y0"] + b["y1"]) / 2
    if b["id"] in ("kitchen", "cabin", "annex"):
        out.append(text(cx - len(b["name"]) * 0.75, cy - 0.6, b["name"], 11, fill="#fff"))
t = L["terrace"]
out.append(rect(t["x0"], t["x1"], t["y0"], t["y1"], fill="#c49a6c", opacity=0.7))
out.append(text(t["x0"] + 0.5, t["y0"] - 1.6, "咖啡露台（棚架）", 11, fill="#6b4a2a"))
sd = L["seadeck"]
out.append(text(sd["c"][0] - 6, sd["c"][1] - 4.5, "觀海平台", 11, fill="#6b4a2a"))
ax, ay = L["airraid"]["pos"]
out.append('<ellipse cx="%s" cy="%s" rx="%s" ry="%s" fill="#7f9a5a" stroke="#4d5f37"/>' % (*P(ax + 2.7, ay), 2.5 * S, 3.2 * S))
out.append(text(ax - 9.5, ay - 4.2, "防空洞（推測位置）", 11, fill="#4d5f37"))
hx, hy = L["hoan_base"]
out.append(rect(hx - 0.9, hx + 0.9, hy - 0.9, hy + 0.9, fill="#777"))
out.append(text(hx - 9, hy + 2.2, "奉安所示意（推測）", 11, fill="#555"))
gx, gy = L["gate"]["pos"]
out.append(text(gx - 9.5, gy + 3.4, "入口・售票", 12, fill="#333", font_weight="bold"))
# tour route
route = [T["start"]] + [q["p"] for q in T["points"]]
out.append(line([(p[0], p[1]) for p in route], stroke="#c7843a", stroke_width=2.2, stroke_dasharray="7 5"))
order = [q["stop"] for q in T["points"] if q.get("stop")]
for h in H:
    n = order.index(h["id"]) + 1
    x, y = P(h["plan"][0], h["plan"][1])
    out.append('<circle cx="%s" cy="%s" r="11" fill="#fff"/><circle cx="%s" cy="%s" r="9" fill="%s"/>' % (x, y, x, y, CAT[h["category"]]))
    out.append('<text x="%s" y="%s" font-size="11" fill="#fff" text-anchor="middle" font-weight="bold">%d</text>' % (x, y + 4, n))
# sea / north arrows
sx, sy = 40, 40
a = math.radians(-L["north_bearing_of_plus_x"])
nx, ny = math.cos(a), -math.sin(a)
cx, cy = P(sx, sy)
out.append('<g transform="translate(%s,%s)"><circle r="20" fill="#fff" stroke="#333"/><line x1="0" y1="0" x2="%s" y2="%s" stroke="#333" stroke-width="3"/><text x="%s" y="%s" font-size="13" text-anchor="middle" font-weight="bold">N</text></g>'
           % (cx, cy, nx * 16, -ny * 16, nx * 30, -ny * 30 + 4))
ex, ey = L["sea_dir"]
cx, cy = P(43, -47)
dx, dy = ex * 32, -ey * 32   # SVG y grows downward
out.append('<g transform="translate(%s,%s)"><line x1="%s" y1="%s" x2="%s" y2="%s" stroke="#2a6f97" stroke-width="3" marker-end="url(#ar)"/></g>' % (cx, cy, -dx, -dy, dx, dy))
out.insert(1, '<defs><marker id="ar" markerWidth="8" markerHeight="8" refX="4" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#2a6f97"/></marker></defs>')
out.append(text(18, -53.5, "太平洋・花蓮港方向（約 1 km）", 12, fill="#2a6f97"))
# legend + scale
y0 = (Y1 - Y0) * S + 18
items = [("#2f7a5f", "史實"), ("#b5701d", "流傳說法"), ("#6a5aa8", "推測重建"), ("#c7843a", "自動導覽路線（虛線）"), ("#a77a4f", "木棧道"), ("#3f6b3a", "琉球松")]
xx = 16
for c, s in items:
    out.append('<rect x="%d" y="%d" width="14" height="14" fill="%s"/><text x="%d" y="%d" font-size="13">%s</text>' % (xx, y0, c, xx + 20, y0 + 12, s))
    xx += 30 + len(s) * 14
out.append('<rect x="%d" y="%d" width="%d" height="5" fill="#333"/><text x="%d" y="%d" font-size="12">10 m</text>' % (W - 120, y0 + 30, 10 * S, W - 120, y0 + 26))
out.append('<text x="16" y="%d" font-size="11" fill="#666">平面座標：+X 沿建物長軸（方位 61.5°），+Y 朝背面。輪廓依 OpenStreetMap（ODbL）與照片推估。</text>' % (y0 + 40))
out.append("</svg>")
os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
open(os.path.join(ROOT, "docs", "plan.svg"), "w", encoding="utf-8").write("\n".join(out))
print("wrote docs/plan.svg")
