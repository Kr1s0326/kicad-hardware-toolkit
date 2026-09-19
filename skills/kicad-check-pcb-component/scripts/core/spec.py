#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.spec - read spec.json, locate the manufacturing files, build the
measurement context that every family works on.

The context is package agnostic; a family adds its own derived geometry in
`family.prepare(ctx)`.
"""

import glob
import json
import math
import os

from core import gerber as G

__all__ = ["load_spec", "build_context", "verdict", "FILE_ALIASES"]

FILE_ALIASES = {
    "cu":    ["f_cu", "f.cu", "gtl", "top.gbr", "top_copper", "_cu."],
    "silk":  ["f_silkscreen", "f_silk", "f.silks", "gto", "silkscreen", "legend"],
    "paste": ["f_paste", "f.paste", "gtp", "paste", "stencil", "cream"],
    "mask":  ["f_mask", "f.mask", "gts", "soldermask"],
    "edge":  ["edge_cuts", "edge.cuts", "gm1", "outline", "profile", "board"],
    "fab":   ["f_fab", "f.fab", "assembly", "fab"],
    "drill": ["npth", "drill", ".drl", "drl"],
}
_NOT_GERBER = (".png", ".jpg", ".jpeg", ".svg", ".pdf", ".json", ".txt", ".md",
               ".kicad_pcb", ".kicad_mod", ".csv", ".xlsx")


def load_spec(path):
    path = os.path.abspath(path)
    with open(path, encoding="utf-8") as fh:
        spec = json.load(fh)
    spec["_path"] = path
    spec["_root"] = os.path.dirname(path)
    if "rows" not in spec:
        raise SystemExit("spec has no 'rows'")
    return spec


def find_file(folder, key, aliases=None):
    if not folder or not os.path.isdir(folder):
        return None
    names = sorted(os.listdir(folder))
    for pat in (aliases or FILE_ALIASES[key]):
        for n in names:
            if pat in n.lower() and not n.lower().endswith(_NOT_GERBER):
                return os.path.join(folder, n)
    return None


def _resolve(spec, key):
    root = spec["_root"]
    f = spec.get("files", {}).get(key)
    if not f:
        folder = spec.get("gerber_dir", "gerber")
        if not os.path.isabs(folder):
            folder = os.path.join(root, folder)
        return find_file(folder, key)
    if not os.path.isabs(f):
        f = os.path.join(root, f)
    return f if os.path.exists(f) else (glob.glob(f) or [None])[0]


def body_rect(gerber, centre, min_len=0.5, radius=4.0, rel=0.4, tol=0.08):
    """Find the package body outline on a silk / fab layer.

    A footprint layer also carries text (reference, value) whose strokes can be
    as long as a small package's body line, so a pure length threshold is not
    enough.  The body is the largest *closed rectangle*: the two longest
    horizontal segments define the top/bottom, and it is accepted only when
    vertical segments close the left and right side.  Falls back to the
    bounding box of the long segments when no rectangle is found.
    """
    if not gerber:
        return None
    try:
        ap, pr = G.parse_gerber(gerber, flip_y=True)
    except Exception:
        return None
    seg = []
    for x0, y0, x1, y1, ln in G.lines(pr, min_len):
        if (math.dist((x0, y0), centre) <= radius
                and math.dist((x1, y1), centre) <= radius):
            seg.append((x0, y0, x1, y1, ln))
    if len(seg) < 2:
        return None

    hz = sorted([s for s in seg if abs(s[1] - s[3]) <= tol], key=lambda s: -s[4])
    vt = [s for s in seg if abs(s[0] - s[2]) <= tol]
    if len(hz) >= 2 and len(vt) >= 2:
        a, b = hz[0], hz[1]
        ya, yb = a[1], b[3]
        y0, y1 = min(ya, yb), max(ya, yb)
        x0 = min(a[0], a[2], b[0], b[2])
        x1 = max(a[0], a[2], b[0], b[2])
        if x1 - x0 > 2 * tol and y1 - y0 > 2 * tol:
            ok = 0
            for side in (x0, x1):
                for s in vt:
                    sx = (s[0] + s[2]) / 2
                    sy0, sy1 = min(s[1], s[3]), max(s[1], s[3])
                    if (abs(sx - side) <= 0.15
                            and min(sy1, y1) - max(sy0, y0) >= 0.6 * (y1 - y0)):
                        ok += 1
                        break
            if ok >= 2:
                return x0, y0, x1, y1

    lmax = max(s[4] for s in seg)                  # fallback: long segments
    keep = [s for s in seg if s[4] >= max(min_len, rel * lmax)]
    if len(keep) < 2:
        return None
    xs = [v for s in keep for v in (s[0], s[2])]
    ys = [v for s in keep for v in (s[1], s[3])]
    return min(xs), min(ys), max(xs), max(ys)


def build_context(spec):
    """-> ctx dict (pads, body, board, drills, paste, origin, files)"""
    ctx = {"files": {k: _resolve(spec, k) for k in FILE_ALIASES}}
    f = ctx["files"]
    if not f["cu"]:
        raise SystemExit("no copper Gerber found - set gerber_dir or files.cu")

    ap, pr = G.parse_gerber(f["cu"], flip_y=True)
    pads = G.flash_pads(ap, pr)
    pads = G.filter_pads(pads, spec.get("pad_filter", "auto"))
    if not pads:
        raise SystemExit("no pads after filtering - check pad_filter / files.cu")
    ctx["pads"] = pads

    xs = [p[0] for p in pads]
    ys = [p[1] for p in pads]
    acentre = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
    # the package outline: prefer the fab layer (always a clean rectangle and
    # never carries the board silkscreen text), fall back to the silkscreen.
    # The search radius grows with the pad array so a big QFP is still found.
    radius = spec.get("body_search") or max(4.0, 0.9 * (max(xs) - min(xs)),
                                            0.9 * (max(ys) - min(ys)))
    rect = None
    for key in ("fab", "silk"):
        rect = body_rect(f.get(key), acentre, spec.get("body_min_len", 0.5),
                         radius, spec.get("body_rel_len", 0.4))
        if rect:
            break
    ctx["body"] = rect
    ctx["origin"] = (((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)
                     if rect else acentre)

    ctx["drills"] = G.parse_drill(f["drill"], flip_y=True) if f["drill"] else []
    if f["edge"]:
        _, pr = G.parse_gerber(f["edge"], flip_y=True)
        segs = [(p[2], p[3], p[4], p[5]) for p in pr if p[0] == "line"]
        ctx["edge_segs"] = segs
        if segs:
            ex = [v for s in segs for v in (s[0], s[2])]
            ey = [v for s in segs for v in (s[1], s[3])]
            ctx["board"] = (min(ex), min(ey), max(ex), max(ey))
    if f["paste"]:
        ap2, pr2 = G.parse_gerber(f["paste"], flip_y=True)
        circles = G.stroked_circles(pr2, ap2)
        big = max((2 * c[2] for c in circles), default=0.0)
        for shape, params in ap2.values():
            if params and shape in ("C", "P"):
                big = max(big, params[0])
        ctx["paste_circles"] = circles
        ctx["paste_aperture"] = big
    return ctx


def verdict(kind, row, value, tol, count_kinds=()):
    """PASS / NG / 待测 for one measured row"""
    if kind == "na" or value is None:
        return row.get("na_result", "待测")
    if kind in count_kinds or row.get("compare") == "count":
        return "PASS" if int(round(value)) == int(row["nominal"]) else "NG"
    lo, hi = row.get("min"), row.get("max")
    if lo is not None and hi is not None:
        return "PASS" if lo - 1e-9 <= value <= hi + 1e-9 else "NG"
    nom = row.get("nominal")
    if nom is None:
        return "PASS"
    return "PASS" if abs(value - nom) <= row.get("tol", tol) + 1e-9 else "NG"


def fmt(v, kind=None):
    if v is None:
        return "N/A"
    if kind in ("count", "matrix_cols", "matrix_rows", "hole_count",
                "leads_per_side", "side_count"):
        return "%d" % round(v)
    if abs(v) >= 100:
        return "%.1f" % v
    if abs(v) >= 10:
        return "%.2f" % v
    if abs(v) >= 1:
        return "%.4f" % v
    return "%.4f" % v
