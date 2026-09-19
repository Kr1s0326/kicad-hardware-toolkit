#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.draw - the shared picture primitives of the report.

Package agnostic: grey pad shapes, a body rectangle, datum lines, red
dimension lines with white-backed labels, dashed helpers.  A family module
composes these into one measurement picture per spec row.
"""

from PIL import Image, ImageDraw, ImageFont

__all__ = ["font", "View", "new_panel", "put_label", "dim_h", "dim_v",
           "dim_diag", "draw_pads", "dashed", "rect_pads", "PANEL", "RED",
           "BLUE", "GREY", "PAD_FILL"]

RED = (210, 30, 30)
BLUE = (60, 90, 220)
GREY = (120, 120, 120)
PAD_FILL = (130, 130, 130)
PAD_HI = (200, 90, 90)
PAD_FILL_EXPOSED = (90, 110, 190)

PANEL = (470, 330)
TITLE_H = 26

_CANDIDATES = (
    (r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc"),
    (r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simhei.ttf"),
    ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/PingFang.ttc"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
     "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)


def font(size, bold=False):
    for b, r in _CANDIDATES:
        try:
            return ImageFont.truetype(b if bold else r, size)
        except Exception:
            pass
    return ImageFont.load_default()


class View:
    """mm -> pixel transform for one panel (Y grows downwards in both)"""

    def __init__(self, win, size=PANEL):
        self.win = win
        self.w, self.h = size
        x0, y0, x1, y1 = win
        self.s = min((self.w - 46) / (x1 - x0), (self.h - TITLE_H - 46) / (y1 - y0))
        self.ox = (self.w - (x1 - x0) * self.s) / 2
        self.oy = TITLE_H + (self.h - TITLE_H - (y1 - y0) * self.s) / 2

    def pt(self, x, y):
        return (self.ox + (x - self.win[0]) * self.s,
                self.oy + (y - self.win[1]) * self.s)


def new_panel(title, size=PANEL):
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, size[0] - 1, size[1] - 1], outline=(210, 210, 210))
    d.text((8, 6), title, fill=(0, 0, 0), font=font(15))
    return img, d


def put_label(d, xy, text, colour=RED, size=15, centre=False):
    f = font(size)
    w = d.textlength(text, font=f)
    x, y = xy
    if centre:
        x -= w / 2
    d.rectangle([x - 3, y - 2, x + w + 3, y + size + 3], fill="white")
    d.text((x, y), text, fill=colour, font=f)


def dim_h(d, V, x1, x2, y, text):
    p1, p2 = V.pt(x1, y), V.pt(x2, y)
    d.line([p1, p2], fill=RED, width=2)
    for p in (p1, p2):
        d.line([p[0], p[1] - 8, p[0], p[1] + 8], fill=RED, width=2)
    put_label(d, ((p1[0] + p2[0]) / 2, p1[1] - 22), text, centre=True)


def dim_v(d, V, y1, y2, x, text):
    p1, p2 = V.pt(x, y1), V.pt(x, y2)
    d.line([p1, p2], fill=RED, width=2)
    for p in (p1, p2):
        d.line([p[0] - 8, p[1], p[0] + 8, p[1]], fill=RED, width=2)
    put_label(d, (p1[0], (p1[1] + p2[1]) / 2 - 9), text)


def dim_diag(d, V, a, b, text):
    pa, pb = V.pt(*a), V.pt(*b)
    d.line([pa, pb], fill=RED, width=2)
    for p in (pa, pb):
        d.ellipse([p[0] - 4, p[1] - 4, p[0] + 4, p[1] + 4], outline=RED, width=2)
    put_label(d, ((pa[0] + pb[0]) / 2 + 10, (pa[1] + pb[1]) / 2 - 10), text)


def rect_pads(pads, tol=0.02):
    """-> [(x, y, w, h)] with w > h for every pad (angle normalised)"""
    out = []
    for x, y, w, h in pads:
        if w < h - tol:
            w, h = h, w
        out.append((x, y, w, h))
    return out


def draw_pads(d, V, pads, hi=None, exposed=None, colour=PAD_FILL):
    """grey pad map; circles stay circles, rectangular apertures become rects"""
    wx0, wy0, wx1, wy1 = V.win
    for i, (x, y, w, h) in enumerate(pads):
        if not (wx0 <= x <= wx1 and wy0 <= y <= wy1):      # keep off title/border
            continue
        w = w or 0.2
        h = h or w
        fill = PAD_HI if hi and i in hi else colour
        if exposed is not None and (x, y) == (exposed[0], exposed[1]):
            fill = PAD_FILL_EXPOSED
        cx, cy = V.pt(x, y)
        if abs(w - h) < max(0.02, 0.02 * w):
            r = max(2.0, w / 2 * V.s)
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill,
                      outline=(0, 0, 0))
        else:
            rx, ry = w / 2 * V.s, h / 2 * V.s
            d.rectangle([cx - rx, cy - ry, cx + rx, cy + ry], fill=fill,
                        outline=(0, 0, 0))
    if exposed is not None:
        x, y, w, h = exposed
        if wx0 <= x <= wx1 and wy0 <= y <= wy1:
            cx, cy = V.pt(x, y)
            rx, ry = w / 2 * V.s, h / 2 * V.s
            d.rectangle([cx - rx, cy - ry, cx + rx, cy + ry], outline=BLUE,
                        width=2)


def dashed(d, V, x0, y0, x1, y1, colour=BLUE):
    p1, p2 = V.pt(x0, y0), V.pt(x1, y1)
    n = max(2, int(((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2) ** 0.5 / 8))
    for i in range(0, n, 2):
        a = (p1[0] + (p2[0] - p1[0]) * i / n, p1[1] + (p2[1] - p1[1]) * i / n)
        b = (p1[0] + (p2[0] - p1[0]) * min(n, i + 1) / n,
             p1[1] + (p2[1] - p1[1]) * min(n, i + 1) / n)
        d.line([a, b], fill=colour, width=2)
