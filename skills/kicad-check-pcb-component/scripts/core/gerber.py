#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.gerber - RS-274X (Gerber) and Excellon (drill) reading.

Package agnostic: this module knows nothing about WLCSP, QFN or 0402, it only
turns manufacturing files into coordinates.  Bug here -> every family is
affected, so it is covered by scripts/selftest.py first.

Coordinate convention
---------------------
Coordinates come back exactly as written in the file.  `flip_y=True` mirrors Y
(many CAD tools write Gerber Y negated) which is what the report pictures want:
X grows right, Y grows **down**, so the first pad row is the top one.
"""

import math
import re

__all__ = ["parse_gerber", "parse_drill", "aperture_size", "flashes",
           "flash_pads", "lines", "region_bbox", "stroked_circles"]


def aperture_size(apertures, code):
    """-> (width, height) of an aperture, or (0, 0) when unknown.

    Handles the standard shapes (C = circle, R = rect, O = obround,
    P = polygon) and the ``RoundRect`` aperture *macro* that KiCad emits for
    every rounded-rectangle SMD pad - for a macro the drawn size is the corner
    box plus the corner radius on both sides.
    """
    a = apertures.get(code)
    if not a:
        return 0.0, 0.0
    shape, params = a
    if shape == "C" or shape == "P":
        return params[0], params[0]
    if shape in ("R", "O"):
        return params[0], params[1]
    if "ROUNDRECT" in shape.upper() and len(params) >= 9:
        # RoundRect: radius, x1,y1, x2,y2, x3,y3, x4,y4, rotation
        r = params[0]
        xs = params[1:9:2]
        ys = params[2:9:2]
        return (max(xs) - min(xs)) + 2 * r, (max(ys) - min(ys)) + 2 * r
    if len(params) >= 2 and "RECT" in shape.upper():
        return abs(params[0]), abs(params[1])
    return 0.0, 0.0


def _arc(ap, x0, y0, x1, y1, i, j, cw, n=64):
    """multi quadrant arc -> list of straight segments"""
    cx, cy = x0 + i, y0 + j
    r = math.hypot(x0 - cx, y0 - cy)
    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    if cw:
        while a1 >= a0:
            a1 -= 2 * math.pi
    else:
        while a1 <= a0:
            a1 += 2 * math.pi
    out, prev = [], (x0, y0)
    for k in range(1, n + 1):
        a = a0 + (a1 - a0) * k / n
        p = (cx + r * math.cos(a), cy + r * math.sin(a))
        out.append(("line", ap, prev[0], prev[1], p[0], p[1]))
        prev = p
    return out


def parse_gerber(path, flip_y=False):
    """-> (apertures, primitives)

    apertures   : {code: (shape, [params])}
    primitives  : ('flash', ap, x, y) | ('line', ap, x0, y0, x1, y1) |
                  ('region', ap, [(x, y), ...])
    """
    apertures, prims = {}, []
    cur, x, y = None, 0.0, 0.0
    scale, interp = 1e-6, "L"
    in_region, pts = False, []

    def P(px, py):
        """gerber point -> caller coordinates (Y flip only, never X)"""
        return (px, -py) if flip_y else (px, py)

    for raw in open(path, encoding="utf-8", errors="replace"):
        for stmt in [s.strip() for s in raw.strip().split("*") if s.strip()]:
            if stmt.startswith("G04"):
                continue
            m = re.fullmatch(r"%FSLA?X(\d)(\d)Y(\d)(\d)%?", stmt)
            if m:
                scale = 10.0 ** (-int(m.group(2)))
                continue
            m = re.fullmatch(r"%MO(MM|IN)%?", stmt)
            if m:
                scale = (1e-6 if m.group(1) == "MM" else 25.4e-6) * \
                        (1 if scale == 1e-6 else 1)
                continue
            m = re.fullmatch(r"%ADD(\d+)([A-Za-z]+),([-\d.X]+)%?", stmt)
            if m:
                apertures[int(m.group(1))] = (
                    m.group(2).upper(),
                    [float(v) for v in m.group(3).split("X")])
                continue
            m = re.fullmatch(r"D(\d+)", stmt)
            if m:
                cur = int(m.group(1))
                continue
            if stmt == "G36":
                in_region, pts = True, []
                continue
            if stmt == "G37":
                if len(pts) > 2:
                    prims.append(("region", cur, list(pts)))
                in_region, pts = False, []
                continue
            if stmt == "G01":
                interp = "L"
                continue
            if stmt in ("G02", "G03"):
                interp = stmt
                continue
            if stmt in ("G75", "G71", "G70", "G90", "LPD", "M02", "M00"):
                continue
            m = re.fullmatch(
                r"(?:X(-?\d+))?(?:Y(-?\d+))?(?:I(-?\d+))?(?:J(-?\d+))?D0?([123])",
                stmt)
            if not m:
                continue
            nx = int(m.group(1)) * scale if m.group(1) else x
            ny = int(m.group(2)) * scale if m.group(2) else y
            i = int(m.group(3)) * scale if m.group(3) else 0.0
            j = int(m.group(4)) * scale if m.group(4) else 0.0
            op = m.group(5)
            if op == "2":
                x, y = nx, ny
                if in_region:
                    pts = [P(x, y)]
            elif op == "1":
                if interp == "L":
                    a, b = P(x, y), P(nx, ny)
                    prims.append(("line", cur, a[0], a[1], b[0], b[1]))
                else:
                    a, b = P(x, y), P(nx, ny)
                    # mirroring Y negates the J offset and swaps CW/CCW
                    prims += _arc(cur, a[0], a[1], b[0], b[1], i,
                                  -j if flip_y else j,
                                  (interp == "G02") != flip_y)
                if in_region:
                    pts.append(P(nx, ny))
                x, y = nx, ny
            else:
                prims.append(("flash", cur, *P(nx, ny)))
                x, y = nx, ny
    return apertures, prims


def filter_pads(pads, mode="auto"):
    """Drop what is not a package pad.

    mode="auto" (default)
        * the biggest pad that is clearly larger than the rest is the exposed /
          thermal pad -> keep it, but drop everything that sits inside its
          outline (via arrays, paste-divided patches, ...)
        * drop pads that are much smaller than the typical pad (text dots,
          tiny fiducials, stitching vias)
    mode="mode"   keep only the most frequent aperture size (legacy behaviour)
    mode=<float>  keep only that aperture size
    """
    if not pads:
        return pads
    if mode == "mode":
        sizes = {}
        for p in pads:
            sizes[round(p[2], 4)] = sizes.get(round(p[2], 4), 0) + 1
        keep = max(sizes, key=sizes.get)
        return [p for p in pads if abs(round(p[2], 4) - keep) < 1e-6]
    if isinstance(mode, (int, float)) and not isinstance(mode, bool):
        return [p for p in pads if abs(p[2] - float(mode)) < 1e-3]

    areas = sorted(p[2] * p[3] for p in pads)
    med = areas[len(areas) // 2] or 1e-9
    big = max(pads, key=lambda p: p[2] * p[3])
    out = []
    for p in pads:
        if p is not big and p[2] * p[3] >= 4 * med:
            continue                                   # a second huge pad
        if p is not big and big[2] * big[3] >= 4 * med:
            if (abs(p[0] - big[0]) <= big[2] / 2 + 1e-6
                    and abs(p[1] - big[1]) <= big[3] / 2 + 1e-6):
                continue                               # inside the exposed pad
        if med > 0 and p is not big and p[2] * p[3] < 0.1 * med:
            continue                                   # noise
        out.append(p)
    return out


def flashes(prims):
    """-> [(x, y)] of all flashed positions"""
    return [(p[2], p[3]) for p in prims if p[0] == "flash"]


def flash_pads(apertures, prims):
    """-> [(x, y, width, height)] of all flashed pads"""
    out = []
    for p in prims:
        if p[0] == "flash":
            w, h = aperture_size(apertures, p[1])
            out.append((p[2], p[3], w, h))
    return out


def lines(prims, min_len=0.0):
    """-> [(x0, y0, x1, y1, length)] of drawn segments longer than min_len"""
    out = []
    for p in prims:
        if p[0] == "line":
            ln = math.dist((p[2], p[3]), (p[4], p[5]))
            if ln >= min_len:
                out.append((p[2], p[3], p[4], p[5], ln))
    return out


def region_bbox(prims):
    xs, ys = [], []
    for p in prims:
        if p[0] == "region":
            for x, y in p[2]:
                xs.append(x)
                ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def stroked_circles(prims, apertures, tol=0.01, min_seg=4):
    """Detect graphic circles drawn with a round aperture.

    A filled/stroked circle (a stencil locating hole, a fiducial, a mounting
    hole drawn on silk, ...) is emitted as one or more arcs stroked with a
    round aperture.  The *drawn* diameter is path_diameter + aperture, which is
    not the aperture size - this helper returns the real outer geometry.

    -> [(cx, cy, outer_radius)]
    """
    chains, cur = [], []
    for p in prims:
        if p[0] != "line":
            if cur:
                chains.append(cur)
                cur = []
            continue
        if (cur and cur[-1][1] == p[1]
                and abs(cur[-1][4] - p[2]) < 1e-6
                and abs(cur[-1][5] - p[3]) < 1e-6):
            cur.append(p)
        else:
            if cur:
                chains.append(cur)
            cur = [p]
    if cur:
        chains.append(cur)

    out = []
    for ch in chains:
        if len(ch) < min_seg:
            continue
        pts = [(c[2], c[3]) for c in ch] + [(ch[-1][4], ch[-1][5])]
        (x1, y1) = pts[0]
        (x2, y2) = pts[len(pts) // 3]
        (x3, y3) = pts[2 * len(pts) // 3]
        d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
        if abs(d) < 1e-9:
            continue                                   # collinear -> not a circle
        s1, s2, s3 = x1 * x1 + y1 * y1, x2 * x2 + y2 * y2, x3 * x3 + y3 * y3
        ux = (s1 * (y2 - y3) + s2 * (y3 - y1) + s3 * (y1 - y2)) / d
        uy = (s1 * (x3 - x2) + s2 * (x1 - x3) + s3 * (x2 - x1)) / d
        r = math.dist((ux, uy), pts[0])
        if not (1e-4 < r < 200):
            continue
        if any(abs(math.dist((ux, uy), p) - r) > max(tol, 0.02 * r) for p in pts):
            continue
        w, _ = aperture_size(apertures, ch[0][1])
        out.append((ux, uy, r + w / 2))
    # merge the duplicates coming from multi-arc circles
    uniq = []
    for c in out:
        if not any(math.dist(c[:2], u[:2]) < tol and abs(c[2] - u[2]) < tol
                   for u in uniq):
            uniq.append(c)
    return uniq


# ---------------------------------------------------------------------------
# Excellon drill
# ---------------------------------------------------------------------------
def parse_drill(path, flip_y=False):
    """-> [(x, y, diameter)]  (metric / decimal / absolute files as KiCad writes)"""
    tools, hits, cur = {}, [], None
    for raw in open(path, encoding="utf-8", errors="replace"):
        s = raw.strip()
        m = re.fullmatch(r"T(\d+)C([\d.]+)", s)
        if m:
            tools[int(m.group(1))] = float(m.group(2))
            continue
        m = re.fullmatch(r"T(\d+)", s)
        if m:
            cur = int(m.group(1)) or None
            continue
        m = re.fullmatch(r"X(-?[\d.]+)Y(-?[\d.]+)", s)
        if m and cur:
            x, y = float(m.group(1)), float(m.group(2))
            hits.append((x, -y if flip_y else y, tools.get(cur, 0.0)))
    return hits


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def dist(a, b):
    return math.dist(a, b)


