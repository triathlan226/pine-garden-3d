"""Generate self-made alpha atlases for foliage cards (pine needles, ivy, shrub, grass).

Run with system Python (needs Pillow + numpy):
    python tools/make_atlases.py
Outputs PNGs into blender/textures_gen/. Each atlas is 1024x1024, 2x2 cells.
"""
import math
import os
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

OUT = os.path.join(os.path.dirname(__file__), "..", "blender", "textures_gen")
os.makedirs(OUT, exist_ok=True)
S = 1024
C = S // 2


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def pine_cell(seed, kind):
    """One foliage clump of two-needle pine (Pinus luchuensis) seen from the side."""
    rnd = random.Random(seed)
    img = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    dark, mid, light = (22, 46, 24), (44, 78, 38), (98, 128, 62)
    if kind == "clump":
        # many needle bursts inside a flattened ellipse (pine pads are flat-topped)
        n = 650
        for i in range(n):
            # sample inside ellipse, denser toward the middle-top
            while True:
                x, y = rnd.uniform(-1, 1), rnd.uniform(-1, 1)
                if x * x + (y * 1.25) ** 2 < 0.9:
                    break
            cx, cy = C / 2 + x * C * 0.46, C / 2 + y * C * 0.36
            t = 0.5 - y * 0.45 + rnd.uniform(-0.2, 0.2)  # lighter on top
            t = max(0, min(1, t))
            col = lerp(dark, light, t) if t > 0.5 else lerp(dark, mid, t * 2)
            nl = rnd.randint(16, 26)
            length = rnd.uniform(30, 56)
            for k in range(nl):
                a = -math.pi / 2 + rnd.uniform(-1.6, 1.6)
                ln = length * rnd.uniform(0.6, 1.0)
                d.line([(cx, cy), (cx + math.cos(a) * ln, cy + math.sin(a) * ln * 0.8)],
                       fill=col + (255,), width=3)
        # a few twigs
        for i in range(6):
            x0 = C / 2 + rnd.uniform(-60, 60)
            d.line([(C / 2, C * 0.85), (x0 + rnd.uniform(-90, 90), C * 0.45 + rnd.uniform(-40, 40))],
                   fill=(58, 44, 32, 255), width=4)
    elif kind == "spray":
        # a branchlet: twig with needle tufts along it
        x0, y0 = C * 0.08, C * 0.62
        x1, y1 = C * 0.95, C * 0.38
        d.line([(x0, y0), (x1, y1)], fill=(62, 46, 34, 255), width=6)
        for i in range(55):
            t = rnd.uniform(0.05, 1.0)
            cx, cy = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t + rnd.uniform(-12, 12)
            col = lerp(dark, light, rnd.uniform(0.2, 0.9))
            for k in range(18):
                a = rnd.uniform(0, 2 * math.pi)
                ln = rnd.uniform(16, 40) * (0.5 + 0.5 * t)
                d.line([(cx, cy), (cx + math.cos(a) * ln, cy + math.sin(a) * ln * 0.7)],
                       fill=col + (255,), width=2)
    else:  # "far": soft blobby silhouette for LOD1 cards
        for i in range(70):
            x, y = max(-0.33, min(0.33, rnd.gauss(0, 0.16))), max(-0.3, min(0.3, rnd.gauss(0, 0.12)))
            r = rnd.uniform(22, 48)
            cx, cy = C / 2 + x * C, C / 2 + y * C
            t = max(0, min(1, 0.55 - y * 1.4 + rnd.uniform(-0.15, 0.15)))
            col = lerp(dark, light, t * 0.8)
            d.ellipse([cx - r, cy - r * 0.7, cx + r, cy + r * 0.7], fill=col + (255,))
        img = img.filter(ImageFilter.GaussianBlur(1.2))
        # break the outline into needle-like fringe
        a = np.array(img)
        noise = np.random.default_rng(seed).random(a.shape[:2])
        a[..., 3] = np.where((a[..., 3] > 30) & (noise > 0.12), 255, 0)
        img = Image.fromarray(a)
    return img


def leaf_cell(seed, palette, leaf_fn, count, size):
    rnd = random.Random(seed)
    img = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i in range(count):
        x, y = rnd.uniform(0.06, 0.94) * C, rnd.uniform(0.06, 0.94) * C
        # keep a round-ish patch: fade density at the corners
        r = math.hypot(x / C - 0.5, y / C - 0.5)
        if r > 0.47 or rnd.random() < r * 0.9:
            continue
        col = lerp(palette[0], palette[1], rnd.random())
        leaf_fn(d, x, y, size * rnd.uniform(0.7, 1.25), rnd.uniform(0, 2 * math.pi), col, rnd)
    return img


def ivy_leaf(d, x, y, s, a, col, rnd):
    # three-lobed leaf (Boston ivy / Parthenocissus look)
    pts = []
    for k in range(40):
        t = k / 40 * 2 * math.pi
        rr = s * (0.65 + 0.35 * abs(math.cos(1.5 * t)))
        pts.append((x + math.cos(t + a) * rr, y + math.sin(t + a) * rr))
    d.polygon(pts, fill=col + (255,))
    shade = tuple(max(0, c - 28) for c in col)
    d.line([(x, y), (x + math.cos(a) * s * 0.9, y + math.sin(a) * s * 0.9)], fill=shade + (255,), width=2)


def round_leaf(d, x, y, s, a, col, rnd):
    w, h = s, s * 0.55
    pts = []
    for k in range(24):
        t = k / 24 * 2 * math.pi
        px, py = math.cos(t) * w, math.sin(t) * h
        pts.append((x + px * math.cos(a) - py * math.sin(a), y + px * math.sin(a) + py * math.cos(a)))
    d.polygon(pts, fill=col + (255,))


def grass_cell(seed, palette):
    rnd = random.Random(seed)
    img = Image.new("RGBA", (C, C), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i in range(140):
        x0 = rnd.uniform(0.1, 0.9) * C
        h = rnd.uniform(0.35, 0.95) * C
        bend = rnd.uniform(-0.25, 0.25) * C
        col = lerp(palette[0], palette[1], rnd.random())
        pts = []
        for k in range(8):
            t = k / 7
            pts.append((x0 + bend * t * t, C - h * t))
        w = rnd.uniform(4, 8)
        for k in range(7):
            d.line([pts[k], pts[k + 1]], fill=col + (255,), width=max(1, int(w * (1 - k / 8))))
    return img


def atlas(cells, name):
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    for i, c in enumerate(cells):
        out.paste(c, ((i % 2) * C, (i // 2) * C))
    # bleed colour into transparent texels so mipmaps don't go dark at edges
    a = np.array(out).astype(np.float32)
    alpha = a[..., 3:4] / 255.0
    rgb = a[..., :3]
    premul = Image.fromarray((rgb * alpha).astype(np.uint8))
    blurred = np.array(premul.filter(ImageFilter.GaussianBlur(10))).astype(np.float32)
    wsum = np.array(Image.fromarray((alpha[..., 0] * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(10))).astype(np.float32)[..., None] / 255.0
    fill = blurred / np.maximum(wsum, 0.02)
    rgb = rgb * alpha + np.clip(fill, 0, 255) * (1 - alpha)
    a[..., :3] = rgb
    Image.fromarray(a.astype(np.uint8)).save(os.path.join(OUT, name))
    print("wrote", name)


if __name__ == "__main__":
    np.random.seed(1)
    atlas([pine_cell(1, "clump"), pine_cell(2, "clump"), pine_cell(3, "spray"), pine_cell(4, "far")],
          "pine_atlas.png")
    ivy_pal = ((40, 82, 28), (118, 160, 58))
    atlas([leaf_cell(10 + i, ivy_pal, ivy_leaf, 260, 26) for i in range(4)], "ivy_atlas.png")
    shrub_pal = ((30, 66, 30), (96, 136, 60))
    atlas([leaf_cell(20 + i, shrub_pal, round_leaf, 900, 22) for i in range(4)], "shrub_atlas.png")
    grass_pal = ((54, 92, 30), (140, 168, 70))
    atlas([grass_cell(30 + i, grass_pal) for i in range(4)], "grass_atlas.png")
