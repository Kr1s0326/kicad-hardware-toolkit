#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.geometry - pad topology helpers shared by the package families.

`classify_pads()` decides which family a pad set belongs to (grid array vs
peripheral leads vs 2-terminal chip).  Everything else here is plain geometry
and is reused by all families.
"""

import math

__all__ = ["dist", "group_rows", "group_cols", "classify_pads", "pad_span",
           "min_pitch", "move_pads"]


def move_pads(pads, dx, dy):
    """平移一组焊盘。

    如果焊盘带着形状（core.gerber.Pad），平移后保留它 —— 否则画图又会退回
    "长宽相等就当圆"的猜测，QFN 的方形散热盘会被画成圆的。
    普通四元组也照常支持（返回普通四元组）。
    """
    return [p.moved(dx, dy) if hasattr(p, "moved")
            else (p[0] - dx, p[1] - dy, p[2], p[3]) for p in pads]


def dist(a, b):
    return math.dist(a, b)


def group_rows(pads, tol=0.05):
    """group pads into rows by Y -> {rounded_y: [x, ...]} sorted by ascending Y
    (i.e. top -> bottom once the usual Y flip has been applied)"""
    rows = {}
    for x, y, *_ in pads:
        for k in rows:
            if abs(k - y) <= tol:
                rows[k].append(x)
                break
        else:
            rows[round(y, 6)] = [x]
    return {k: sorted(v) for k, v in sorted(rows.items())}


def group_cols(pads, tol=0.05):
    """group pads into columns by X -> {rounded_x: [y, ...]} sorted left -> right"""
    cols = {}
    for x, y, *_ in pads:
        for k in cols:
            if abs(k - x) <= tol:
                cols[k].append(y)
                break
        else:
            cols[round(x, 6)] = [y]
    return {k: sorted(v) for k, v in sorted(cols.items())}


# ---------------------------------------------------------------------------
# generic package measurements
# ---------------------------------------------------------------------------
def classify_pads(pads, tol=0.05):
    """Tell a 2-D grid array (BGA/WLCSP/LGA) from a peripheral package
    (QFN/QFP/SOIC/SOT) and measure the side-based geometry.

    pads : [(x, y, w, h)]

    -> dict(type = "grid" | "peripheral", plus for peripheral packages:
            sides {left/right/top/bottom: [(x, y, w, h), ...]},
            n_sides, leads_per_side, leads_per_side_all, lead_pitch,
            pad_span_x/y, pad_edge_span_x/y, lead_width, lead_length,
            exposed {w, h} or None, npads_excl_exposed)
    """
    if not pads:
        raise ValueError("no pads")
    xs = [p[0] for p in pads]
    ys = [p[1] for p in pads]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    W, H = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)

    # an exposed / thermal pad is a pad with a much larger area than the rest
    areas = sorted(p[2] * p[3] for p in pads)
    med = areas[len(areas) // 2] or 1e-9
    exposed, normal = None, []
    for p in pads:
        if p[2] * p[3] >= 4 * med and (exposed is None or p[2] * p[3] > exposed[2] * exposed[3]):
            exposed = p
    for p in pads:
        if p is not exposed:
            normal.append(p)

    inner = [p for p in normal
             if x0 + 0.25 * W < p[0] < x1 - 0.25 * W
             and y0 + 0.25 * H < p[1] < y1 - 0.25 * H]
    out = {"type": "peripheral" if not inner else "grid", "exposed": None,
           "npads_excl_exposed": len(normal)}
    if exposed is not None:
        out["exposed"] = {"w": exposed[2], "h": exposed[3],
                          "x": exposed[0], "y": exposed[1],
                          "shape": getattr(exposed, "shape", "")}
    if out["type"] == "grid":
        return out

    sides = {"left": [], "right": [], "top": [], "bottom": []}
    for p in normal:
        # nearest edge wins - a quarter-band test mis-assigns the leads that
        # sit near a corner (e.g. the top/bottom leads of a QFP)
        d = {"left": p[0] - x0, "right": x1 - p[0],
             "top": p[1] - y0, "bottom": y1 - p[1]}
        sides[min(d, key=d.get)].append(p)
    sides = {k: sorted(v, key=lambda p: (p[1], p[0])) for k, v in sides.items()
             if v}

    pitches = []
    for v in sides.values():
        for horizontal in (0, 1):
            q = sorted(v, key=lambda p: p[horizontal])
            for a, b in zip(q, q[1:]):
                d = math.dist((a[0], a[1]), (b[0], b[1]))
                if d > 1e-6:
                    pitches.append(d)
    # the lead pitch is the smallest *and* most frequent spacing on a side
    lead_pitch = 0.0
    if pitches:
        rounded = [round(p, 4) for p in pitches]
        lead_pitch = min(set(rounded), key=lambda v: (rounded.count(v) * -1, v))

    def span(axis, edge=False):
        vals = []
        for p in normal:
            half = p[2] / 2 if axis == 0 else p[3] / 2
            vals.append((p[axis] - half, p[axis] + half))
        if not vals:
            return 0.0
        lo = min(v[0] for v in vals)
        hi = max(v[1] for v in vals)
        if edge:
            return hi - lo
        return max(p[axis] for p in normal) - min(p[axis] for p in normal)

    lw = [min(p[2], p[3]) for p in normal]
    ll = [max(p[2], p[3]) for p in normal]
    n_side = [len(v) for v in sides.values()]
    out.update({
        "sides": sides,
        "n_sides": len(sides),
        "leads_per_side": max(set(n_side), key=n_side.count) if n_side else 0,
        "leads_per_side_all": {k: len(v) for k, v in sides.items()},
        "lead_pitch": lead_pitch,
        "pad_span_x": span(0),
        "pad_span_y": span(1),
        "pad_edge_span_x": span(0, True),
        "pad_edge_span_y": span(1, True),
        "lead_width": min(lw) if lw else 0.0,
        "lead_length": max(ll) if ll else 0.0,
        "n": len(normal),
    })
    return out



def pad_span(pads, axis, edge=False):
    """centre span (axis 0 = X, 1 = Y), or outer edge span when edge=True"""
    if not pads:
        return 0.0
    lo = min(p[axis] - (p[2] / 2 if axis == 0 else p[3] / 2) for p in pads)
    hi = max(p[axis] + (p[2] / 2 if axis == 0 else p[3] / 2) for p in pads)
    if edge:
        return hi - lo
    return max(p[axis] for p in pads) - min(p[axis] for p in pads)


def min_pitch(pads, axis=0):
    """smallest centre distance between two pads that share the other axis"""
    out = []
    for grid in (0, 1):
        groups = {}
        for p in pads:
            groups.setdefault(round(p[1 - grid], 4), []).append(p)
        for g in groups.values():
            g = sorted(g, key=lambda p: p[grid])
            out += [b[grid] - a[grid] for a, b in zip(g, g[1:])]
    return min([v for v in out if v > 1e-6], default=0.0)
