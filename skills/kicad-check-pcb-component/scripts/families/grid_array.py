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
def _full_view(ctx, mx=0.30, my=0.50, gap_px=16):
    """整颗器件的视图窗口 + 尺寸线/标签的位置（供测量图用）。

    与 peripheral 族同一套约定：**测量图一律以整颗 IC 为背景**，
    看图的人才知道"量的是整颗器件里的哪一处"。

    踩过的坑：以前每个 kind 各给一个"刚好框住那几个球"的窗口 ——
    Øb 只看 2 个球、eS1 只看 3 个球。结果同一份报告里一半的图有整颗球阵、
    一半只有局部，看的人没法把"0.35"对应回"哪两个球"，也看不出它占整颗的
    哪个位置。修了 peripheral 族却漏了这一族。

    横向余量给得比纵向小：球阵通常是竖的（MD 11 x ME 9），而面板是 470x330
    的横图 —— 数组按高度定标后左右会白白空出一大片，正好拿来放标签。

    返回 (View, pad_x0, pad_y1)："焊盘外沿"的左缘与世界坐标的下沿。
    标签必须贴在 pad_x0 左边，否则它的白色底板会盖掉被高亮的球。
    """
    ox, oy = ctx["origin"]
    P = G.move_pads(ctx["pads"], ox, oy)
    x0 = min(p[0] - p[2] / 2 for p in P)
    x1 = max(p[0] + p[2] / 2 for p in P)
    y0 = min(p[1] - p[3] / 2 for p in P)
    y1 = max(p[1] + p[3] / 2 for p in P)
    V = D.View((x0 - mx, y0 - my, x1 + mx, y1 + my))
    return V, x0, y1


def _hi_idx(P, pts):
    """把世界坐标点集翻成 draw_pads() 的 hi 索引集"""
    out = set()
    for i, q in enumerate(P):
        for px, py in pts:
            if abs(q[0] - px) < 1e-6 and abs(q[1] - py) < 1e-6:
                out.add(i)
    return out


def panel(kind, ctx, row, value, extra, sym):
    a = ctx["array"]
    ox, oy = ctx["origin"]
    P = G.move_pads(ctx["pads"], ox, oy)
    xs = [p[0] for p in P]
    ys = [p[1] for p in P]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    V, pad_x0, pad_y1 = _full_view(ctx)

    def _label_out(d, text, colour=D.RED):
        """把数值贴到球阵左侧的空白带里（右对齐，不压焊盘）

        put_label() 收的是**像素**坐标。
        """
        px = V.pt(pad_x0, (y0 + y1) / 2)
        d.text((px[0] - 12, px[1] - 9), text, fill=colour, font=D.font(15),
               anchor="rm")

    if kind in ("array_w", "array_h"):
        if kind == "array_w":
            ttl = "%s  阵列 D 向跨距 %.4f mm" % (sym, value)
            dim, yb = D.dim_h, y0 - 0.34
            lo, hi = x0, x1
            mid_x, mid_y = (x0 + x1) / 2, yb
        else:
            ttl = "%s  阵列 E 向跨距 %.4f mm" % (sym, value)
            dim = D.dim_v
            lo, hi = y0, y1
            mid_x, mid_y = pad_x0 - 0.09, (y0 + y1) / 2
        img, d = D.new_panel(ttl)
        D.draw_pads(d, V, P)
        if kind == "array_w":
            dim(d, V, lo, hi, mid_y, "")
            px = V.pt(mid_x, mid_y)
            d.text((px[0], px[1] - 26), "%.4f" % value, fill=D.RED,
                   font=D.font(15), anchor="mm")
        else:
            dim(d, V, lo, hi, mid_x, "")
            px = V.pt(mid_x, mid_y)
            d.text((px[0] - 12, px[1]), "%.4f" % value, fill=D.RED,
                   font=D.font(15), anchor="rm")
        return img

    if kind in ("matrix_cols", "matrix_rows", "count"):
        t = {"matrix_cols": "%s = %d 列位（矩阵列位，含空位）" % (sym, value),
             "matrix_rows": "%s = %d 行位（矩阵行位）" % (sym, value),
             "count": "%s = %d 个焊盘（逐个计数，逐个高亮）" % (sym, value)}[kind]
        img, d = D.new_panel(t)
        hi = set(range(len(P))) if kind == "count" else None
        D.draw_pads(d, V, P, hi=hi)
        if kind == "matrix_cols":
            for c in sorted({round(p[0], 4) for p in P}):
                d.line([V.pt(c, y0 - 0.34), V.pt(c, y1 + 0.34)], fill=D.BLUE,
                       width=1)
        elif kind == "matrix_rows":
            for rr in sorted({round(p[1], 4) for p in P}):
                d.line([V.pt(x0 - 0.34, rr), V.pt(x1 + 0.34, rr)], fill=D.BLUE,
                       width=1)
        _label_out(d, "%d" % value, D.BLUE)
        return img

    if kind == "dia":
        cx, cy, w, _ = P[0]
        img, d = D.new_panel("%s  焊球直径 / 开孔 Ø%.3f mm" % (sym, value))
        D.draw_pads(d, V, P, hi=_hi_idx(P, [(cx, cy)]))
        c = V.pt(cx, cy)
        r = w / 2 * V.s
        d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], outline=D.RED,
                  width=2)
        D.dim_h(d, V, cx - w / 2, cx + w / 2, cy, "")
        _label_out(d, "Ø%.4f" % value)
        return img

    if kind == "pitch":
        ry = a["row_keys"][0] - oy
        row_pads = sorted([p for p in P if abs(p[1] - ry) < 1e-6])
        p1, p2 = row_pads[0][0], row_pads[1][0]
        img, d = D.new_panel("%s  球栅距 eD（同排相邻）%.3f mm" % (sym, value))
        D.draw_pads(d, V, P, hi=_hi_idx(P, [(p1, ry), (p2, ry)]))
        D.dim_h(d, V, p1, p2, ry - 0.30, "")
        px = V.pt((p1 + p2) / 2, ry - 0.30)
        d.text((px[0], px[1] - 26), "%.4f" % value, fill=D.RED,
               font=D.font(15), anchor="mm")
        return img

    if kind == "row_span":
        ya, yb = extra["y1"] - oy, extra["y2"] - oy
        img, d = D.new_panel("%s  %s→%s 两排球心距 = %.3f mm"
                             % (sym, extra["from"], extra["to"], value))
        D.draw_pads(d, V, P, hi=_hi_idx(P, [(p[0], p[1]) for p in P
                                            if abs(p[1] - ya) < 1e-6
                                            or abs(p[1] - yb) < 1e-6]))
        D.dim_v(d, V, ya, yb, pad_x0 - 0.09, "")
        _label_out(d, "%.4f" % value)
        return img

    if kind in ("diag_min", "diag_max"):
        pair = None
        for xa, ya, *_ in P:
            for xb, yb, *_ in P:
                if yb >= ya - 1e-9 or ya - yb > 3 * a["pitch"]:
                    continue
                if abs(math.dist((xa, ya), (xb, yb)) - value) < 0.004 and xb > xa:
                    pair = ((xa, ya), (xb, yb))
        if pair is None:
            pair = ((x0, y0), (x1, y1))
        (pa, pb) = pair
        what = "斜向最近球间距（外侧排）" if kind == "diag_min" \
            else "斜向最近球间距（中间排）"
        img, d = D.new_panel("%s  %s = %.4f mm" % (sym, what, value))
        D.draw_pads(d, V, P, hi=_hi_idx(P, [pa, pb]))
        D.dim_diag(d, V, pa, pb, "")
        _label_out(d, "%.4f" % value)
        return img

    if kind == "sd":
        ry = a["row_keys"][0] - oy
        row_pads = sorted([p[0] for p in P if abs(p[1] - ry) < 1e-6])
        mid = row_pads[len(row_pads) // 2]
        img, d = D.new_panel("%s  外排中心球到基准 B = %.3f mm" % (sym, value))
        D.draw_pads(d, V, P, hi=_hi_idx(P, [(mid, ry)]))
        D.dashed(d, V, 0, y0 - 0.34, 0, y1 + 0.34)
        D.dim_h(d, V, 0, mid, ry - 0.30, "")
        px = V.pt(0, y0 - 0.34)
        d.text((px[0] + 5, px[1]), "基准 B", fill=D.BLUE, font=D.font(15))
        _label_out(d, "%.4f" % value)
        return img

    if kind == "se":
        img, d = D.new_panel("%s  阵列中心到基准 A = %.3f mm" % (sym, value))
        D.draw_pads(d, V, P)
        D.dashed(d, V, pad_x0 - 0.30, 0, x1 + 0.30, 0)
        D.dim_v(d, V, -0.04, 0.04, pad_x0 - 0.09, "")
        c = V.pt(0, 0)
        d.ellipse([c[0] - 4, c[1] - 4, c[0] + 4, c[1] + 4], outline=D.RED,
                  width=2)
        if abs(value) < 1e-6:
            px = V.pt(0, y1 + 0.30)
            d.text((px[0] + 6, px[1]), "阵列中心与基准 A 重合 → 0.000",
                   fill=D.BLUE, font=D.font(15))
        _label_out(d, "%.4f" % value)
        return img

    return common.panel(kind, ctx, row, value, extra, sym)
