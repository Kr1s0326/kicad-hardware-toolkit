#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""families.grid_array - 网格阵列类封装

    WLCSP / CSP / BGA / LGA / μBGA / FlipChip

几何本质：焊盘排成二维阵列（行列网格），封装四边/整个底面都有球。
本文件负责这一族的**专用测量**与**专用测量图**；与封装无关的测量
（本体尺寸、板框、定位孔、钢网孔、Z 向不可测项）在 core.common 里。

图纸符号对照（JEP95 / JEDEC 球栅阵列）
    D, E        本体外形尺寸 -> body_w / body_h            (core.common)
    D1, E1      球阵列跨距（最外两排球心距） -> array_w / array_h
    MD, ME      矩阵位数（列位 / 行位）       -> matrix_cols / matrix_rows
    N           球数                          -> count
    Øb          球径（焊盘/钢网开孔直径）      -> dia
    eD          球栅距（同排相邻球心距）        -> pitch
    eE1s/eE2s…  指定两排球心距                 -> row_span (from/to)
    eS1, eS2    斜向最近球间距（交错阵列）      -> diag_min / diag_max
    SD, SE      基准偏移                       -> sd / se
"""

import math

from core import common
from core import draw as D
from core import geometry as G

FAMILY = "grid_array"
LABEL = "网格阵列类（WLCSP / CSP / BGA / LGA）"
DESCRIPTION = "焊盘成二维阵列，含矩阵位数、阵列跨距、斜向球距、基准偏移等球栅阵列专有项"

COUNT_KINDS = {"count", "matrix_cols", "matrix_rows", "hole_count"}

KINDS = {
    "array_w": "球阵列 D 向跨距（最外两排球心距）",
    "array_h": "球阵列 E 向跨距",
    "matrix_cols": "D 向矩阵位数（含空位）",
    "matrix_rows": "E 向矩阵位数",
    "count": "焊球总数",
    "dia": "焊球 / 焊盘直径",
    "pitch": "球栅距（同排相邻球心距）",
    "row_span": "指定两排球心距（from/to 用 JEDEC 行字母）",
    "diag_min": "斜向最近球间距（外侧排）",
    "diag_max": "斜向最近球间距（中间排）",
    "sd": "外排中心球到基准 B 的偏移",
    "se": "阵列中心到基准 A 的偏移",
}


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------
def measure_array(pads, collinear_tol=0.05):
    """Grid-array specific measurements of a pad set (mm in -> numbers out)."""
    xs = [p[0] for p in pads]
    ys = [p[1] for p in pads]
    rows = G.group_rows(pads, collinear_tol)
    cols = G.group_cols(pads, collinear_tol)

    pitches = []
    for rx in rows.values():
        rx = sorted(rx)
        pitches += [b - a for a, b in zip(rx, rx[1:])]
    pitch = min(pitches) if pitches else 0.0

    # column grid pitch: for a staggered (checkerboard) array this is half the
    # in-row pitch, and MD counts grid positions, not balls
    uxs = sorted({round(p[0], 4) for p in pads})
    col_pitch = min([b - a for a, b in zip(uxs, uxs[1:])] or [pitch])

    # diagonal pitch = for every pad, the smallest distance to an adjacent row
    keys = list(rows)
    near = []
    for i in range(len(keys) - 1):
        y1, y2 = keys[i], keys[i + 1]
        if abs(y1 - y2) > 3 * pitch:
            continue
        for x1 in rows[y1]:
            near.append(min(math.dist((x1, y1), (x2, y2)) for x2 in rows[y2]))
    diag = sorted(round(v, 6) for v in near)

    return {
        "n": len(pads),
        "array_w": max(xs) - min(xs),
        "array_h": max(ys) - min(ys),
        "centre": ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2),
        "rows": rows,
        "row_keys": keys,                      # top -> bottom
        "n_rows": len(keys),
        "n_cols": len(cols),
        "col_pitch": col_pitch,
        "matrix_cols": int(round((max(xs) - min(xs)) / col_pitch)) + 1
        if col_pitch else 1,
        "pitch": pitch,
        "diag_min": diag[0] if diag else 0.0,
        "diag_max": diag[-1] if diag else 0.0,
        "dia": max(p[2] for p in pads),
    }


def detect(ctx):
    """a grid array has pads in the interior of its own bounding box
    (an exposed/thermal pad does not count - see geometry.classify_pads)"""
    return G.classify_pads(ctx["pads"], ctx.get("_collinear_tol", 0.05))["type"] == "grid"


def prepare(ctx):
    ctx["array"] = measure_array(ctx["pads"],
                                 ctx.get("_collinear_tol", 0.05))
    a = ctx["array"]
    ctx["pitch"] = a["pitch"]
    ctx["count"] = a["n"]


def _row(ctx, key):
    keys = ctx["array"]["row_keys"]
    if isinstance(key, int) or (isinstance(key, str) and key.lstrip("-").isdigit()):
        return keys[int(key)]
    return keys[G_ROW_LETTERS.index(str(key).upper())]


G_ROW_LETTERS = "ABCDEFGHJKLMNPRTUVWY"          # JEDEC: no I, O, Q, S, X, Z


# ---------------------------------------------------------------------------
# measurement
# ---------------------------------------------------------------------------
def measure(kind, ctx, row):
    a = ctx["array"]
    pads = ctx["pads"]
    ox, oy = ctx["origin"]
    if kind == "array_w":
        return a["array_w"], {}
    if kind == "array_h":
        return a["array_h"], {}
    if kind == "matrix_cols":
        return a["matrix_cols"], {}
    if kind == "matrix_rows":
        return a["n_rows"], {}
    if kind == "count":
        return a["n"], {}
    if kind == "dia":
        return a["dia"], {}
    if kind == "pitch":
        return a["pitch"], {}
    if kind == "row_span":
        y1, y2 = _row(ctx, row.get("from", 0)), _row(ctx, row.get("to", -1))
        return abs(y1 - y2), {"y1": y1, "y2": y2, "from": row.get("from"),
                              "to": row.get("to")}
    if kind == "diag_min":
        return a["diag_min"], {}
    if kind == "diag_max":
        return a["diag_max"], {}
    if kind == "sd":
        y = a["row_keys"][0]
        xs = sorted(a["rows"][y])
        return abs(xs[len(xs) // 2] - ox), {"row": y}
    if kind == "se":
        ys = [p[1] for p in pads]
        return abs((min(ys) + max(ys)) / 2 - oy), {}
    return common.measure(kind, ctx, row)


# ---------------------------------------------------------------------------
# pictures
# ---------------------------------------------------------------------------
def panel(kind, ctx, row, value, extra, sym):
    a = ctx["array"]
    pads = ctx["pads"]
    ox, oy = ctx["origin"]
    P = [(x - ox, y - oy, w, h) for x, y, w, h in pads]
    xs = [p[0] for p in P]
    ys = [p[1] for p in P]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)

    if kind in ("array_w", "array_h"):
        if kind == "array_w":
            V = D.View((x0 - 0.45, y0 - 0.55, x1 + 0.45, y1 + 0.25))
            img, d = D.new_panel("%s  阵列宽 %.4f mm" % (sym, value))
            D.draw_pads(d, V, P)
            D.dim_h(d, V, x0, x1, y0 - 0.35, "%.4f" % value)
        else:
            V = D.View((x0 - 0.9, y0 - 0.45, x1 + 0.3, y1 + 0.45))
            img, d = D.new_panel("%s  阵列高 %.4f mm" % (sym, value))
            D.draw_pads(d, V, P)
            D.dim_v(d, V, y0, y1, x0 - 0.45, "%.4f" % value)
        return img

    if kind in ("matrix_cols", "matrix_rows", "count"):
        t = {"matrix_cols": "%s = %d 列（矩阵列位）" % (sym, value),
             "matrix_rows": "%s = %d 行（矩阵行位）" % (sym, value),
             "count": "%s = %d 个焊盘（逐个计数）" % (sym, value)}[kind]
        V = D.View((x0 - 0.45, y0 - 0.45, x1 + 0.45, y1 + 0.45))
        img, d = D.new_panel(t)
        D.draw_pads(d, V, P)
        if kind == "matrix_cols":
            for c in sorted({round(p[0], 4) for p in P}):
                d.line([V.pt(c, y0 - 0.28), V.pt(c, y1 + 0.28)], fill=D.BLUE, width=1)
            D.put_label(d, (V.pt(x0, y0)[0], V.pt(0, y1 + 0.34)[1]),
                        "%d 个列位" % value, D.BLUE)
        elif kind == "matrix_rows":
            for rr in sorted({round(p[1], 4) for p in P}):
                d.line([V.pt(x0 - 0.28, rr), V.pt(x1 + 0.28, rr)], fill=D.BLUE, width=1)
            D.put_label(d, (V.pt(x0 - 0.3, y0)[0], V.pt(0, y1 + 0.34)[1]),
                        "%d 个行位" % value, D.BLUE)
        else:
            D.put_label(d, (V.pt(x0, y0)[0], V.pt(0, y1 + 0.34)[1]),
                        "实测计数 = %d" % value, D.BLUE)
        return img

    if kind == "dia":
        cx, cy, w, _ = P[0]
        V = D.View((cx - 2 * w, cy - 1.8 * w, cx + 2 * w, cy + 1.8 * w))
        img, d = D.new_panel("%s  焊球 / 开孔直径 %.3f mm" % (sym, value))
        D.draw_pads(d, V, P)
        c = V.pt(cx, cy)
        r = w / 2 * V.s
        d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], outline=D.RED, width=2)
        D.dim_h(d, V, cx - w / 2, cx + w / 2, cy, "Ø%.4f" % value)
        return img

    if kind == "pitch":
        ry = a["row_keys"][0] - oy
        row_pads = sorted([p for p in P if abs(p[1] - ry) < 1e-6])
        p1, p2 = row_pads[0][0], row_pads[1][0]
        V = D.View((p1 - 0.22, ry - 0.35, p2 + 0.22, ry + 0.28))
        img, d = D.new_panel("%s  同排球间距 %.3f mm" % (sym, value))
        D.draw_pads(d, V, P)
        D.dim_h(d, V, p1, p2, ry - 0.16, "%.4f" % value)
        return img

    if kind == "row_span":
        y1, y2 = extra["y1"] - oy, extra["y2"] - oy
        V = D.View((x0 - 0.9, min(y1, y2) - 0.35, x1 + 0.3, max(y1, y2) + 0.35))
        img, d = D.new_panel("%s  %s→%s = %.3f mm"
                             % (sym, extra["from"], extra["to"], value))
        D.draw_pads(d, V, P)
        for p in P:
            if abs(p[1] - y1) < 1e-6:
                c = V.pt(p[0], p[1])
                d.ellipse([c[0] - 6, c[1] - 6, c[0] + 6, c[1] + 6],
                          outline=D.RED, width=2)
        D.dim_v(d, V, y1, y2, x0 - 0.45, "%.4f" % value)
        return img

    if kind in ("diag_min", "diag_max"):
        target, pair = value, None
        for xa, ya, *_ in P:
            for xb, yb, *_ in P:
                if yb >= ya - 1e-9 or ya - yb > 3 * a["pitch"]:
                    continue
                if abs(math.dist((xa, ya), (xb, yb)) - target) < 0.004 and xb > xa:
                    pair = ((xa, ya), (xb, yb))
        if pair is None:
            pair = ((x0, y0), (x1, y1))
        (pa, pb) = pair
        V = D.View((min(pa[0], pb[0]) - 0.22, min(pa[1], pb[1]) - 0.22,
                    max(pa[0], pb[0]) + 0.22, max(pa[1], pb[1]) + 0.22))
        img, d = D.new_panel("%s  斜向球间距 %.4f mm" % (sym, value))
        D.draw_pads(d, V, P)
        D.dim_diag(d, V, pa, pb, "%.4f" % value)
        return img

    if kind == "sd":
        ry = a["row_keys"][0] - oy
        row_pads = sorted([p[0] for p in P if abs(p[1] - ry) < 1e-6])
        mid = row_pads[len(row_pads) // 2]
        V = D.View((mid - 0.75, ry - 0.5, 0.6, ry + 0.5))
        img, d = D.new_panel("%s  外排中心球到基准 = %.3f mm" % (sym, value))
        D.draw_pads(d, V, P)
        D.dashed(d, V, 0, ry - 0.45, 0, ry + 0.45)
        D.dim_h(d, V, 0, mid, ry - 0.3, "%.4f" % value)
        D.put_label(d, (V.pt(0, ry + 0.42)[0] + 6, V.pt(0, ry + 0.42)[1]),
                    "基准 B", D.BLUE)
        return img

    if kind == "se":
        V = D.View((x0 - 0.5, -0.45, x1 + 0.5, 0.45))
        img, d = D.new_panel("%s  阵列中心到基准 = %.3f mm" % (sym, value))
        D.draw_pads(d, V, P)
        D.dashed(d, V, x0 - 0.45, 0, x1 + 0.45, 0)
        D.dim_v(d, V, -0.05, 0.05, x0 - 0.35, "%.4f" % value)
        c = V.pt(0, 0)
        d.ellipse([c[0] - 4, c[1] - 4, c[0] + 4, c[1] + 4], outline=D.RED, width=2)
        if abs(value) < 1e-6:
            D.put_label(d, (V.pt(x0 - 0.4, 0)[0], V.pt(0, 0.3)[1]),
                        "阵列中心与基准重合 → 0.000", D.BLUE)
        return img

    return common.panel(kind, ctx, row, value, extra, sym)
