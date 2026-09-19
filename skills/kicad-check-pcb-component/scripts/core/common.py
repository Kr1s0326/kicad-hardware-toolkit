#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.common - the measurements and pictures that are *not* package specific.

Every family falls back to `common.measure` / `common.panel` for these kinds:

    na              Z-axis items (package height / stand-off) - cannot be
                    measured on a 2-D Gerber set
    body_w/body_h   package body outline (long silk or fab segments)
    board_w/board_h board outline size
    hole_count/hole_dia/hole_offset/hole_edge    locating / mounting holes
    paste_hole_dia  largest drawn circle on the paste layer (stencil hole)
"""


from core import draw as D
from core import geometry as G

__all__ = ["KINDS", "measure", "panel", "na_panel"]

KINDS = {
    "na": "无（Z 向尺寸，2D 文件无法测量）",
    "body_w": "封装本体宽度（丝印/装配层外框）",
    "body_h": "封装本体高度",
    "board_w": "板框宽度",
    "board_h": "板框高度",
    "hole_count": "钻孔个数",
    "hole_dia": "最大钻孔直径",
    "hole_offset": "孔中心到板边距离",
    "hole_edge": "孔边缘到板边余料",
    "paste_hole_dia": "钢网层最大实心圆直径",
}


def measure(kind, ctx, row):
    if kind == "na":
        return None, {}
    if kind in ("body_w", "body_h"):
        if not ctx.get("body"):
            return None, {}
        x0, y0, x1, y1 = ctx["body"]
        return (x1 - x0 if kind == "body_w" else y1 - y0), {}
    if kind in ("board_w", "board_h"):
        if not ctx.get("board"):
            return None, {}
        x0, y0, x1, y1 = ctx["board"]
        return (x1 - x0 if kind == "board_w" else y1 - y0), {}
    if kind in ("hole_count", "hole_dia"):
        if not ctx.get("drills"):
            return None, {}
        if kind == "hole_count":
            return len(ctx["drills"]), {}
        return max(h[2] for h in ctx["drills"]), {}
    if kind in ("hole_offset", "hole_edge"):
        if not ctx.get("drills") or not ctx.get("board"):
            return None, {}
        x0, y0, x1, y1 = ctx["board"]
        v = min(min(hx - x0, x1 - hx, hy - y0, y1 - hy)
                for hx, hy, _ in ctx["drills"])
        if kind == "hole_edge":
            v -= max(h[2] for h in ctx["drills"]) / 2
        return v, {}
    if kind == "paste_hole_dia":
        circles = ctx.get("paste_circles") or []
        return max([2 * c[2] for c in circles] + [ctx.get("paste_aperture", 0.0)]), {}
    return None, {}


def na_panel(sym, row):
    img, d = D.new_panel("%s - Z 方向（厚度 / 球高）" % sym)
    d.text((20, 56), "2D PCB / Gerber 文件中不存在此方向的几何", fill=D.RED,
           font=D.font(15))
    d.text((20, 80), "无法用本设计文件测量，需 3D 模型或实物测量", fill=D.RED,
           font=D.font(15))
    for k, t in enumerate((row.get("note") or "").split("\n")):
        d.text((20, 126 + 22 * k), t, fill=(0, 0, 0), font=D.font(15))
    bx, by = 60, 210
    d.rectangle([bx, by, bx + 300, by + 40], outline=(0, 0, 0))
    for i in range(4):
        d.ellipse([bx + 30 + i * 75, by + 40, bx + 60 + i * 75, by + 62],
                  outline=(0, 0, 0))
    d.line([bx - 20, by, bx - 20, by + 62], fill=D.RED, width=2)
    d.line([bx - 28, by, bx - 12, by], fill=D.RED, width=2)
    d.line([bx - 28, by + 62, bx - 12, by + 62], fill=D.RED, width=2)
    D.put_label(d, (bx - 48, by + 20), "A")
    d.line([bx + 330, by + 40, bx + 330, by + 62], fill=D.RED, width=2)
    D.put_label(d, (bx + 336, by + 43), "A1")
    d.text((20, 292), "(示意图，非测量值)", fill=D.GREY, font=D.font(15))
    return img


def _exposed(ctx):
    """散热焊盘的**相对**坐标（带形状），没有就 None。

    peripheral 族的 _draw_map 一直会传 exposed，所以 N/MD/D2/E2 那几张里
    散热盘是蓝色的；而 D/E 这两张走的是本文件，从来没传 —— 于是同一份报告里
    散热盘一会儿蓝一会儿灰。用户对着 D 面板问"这种焊盘为什么不标出来"，
    指的就是这个。
    """
    ep = (ctx.get("peri") or {}).get("exposed")
    if not ep:
        return None
    ox, oy = ctx["origin"]
    return (ep["x"] - ox, ep["y"] - oy, ep["w"], ep["h"], ep.get("shape", ""))


def panel(kind, ctx, row, value, extra, sym):
    pads = ctx["pads"]
    ox, oy = ctx["origin"]
    exp = _exposed(ctx)
    if kind == "na":
        return na_panel(sym, row)
    if kind in ("body_w", "body_h"):
        bx0, by0, bx1, by1 = ctx["body"]
        bx0, by0, bx1, by1 = bx0 - ox, by0 - oy, bx1 - ox, by1 - oy
        if kind == "body_w":
            V = D.View((bx0 - 0.45, by0 - 0.85, bx1 + 0.45, by1 + 0.3))
            img, d = D.new_panel("%s  本体宽 %.4f mm" % (sym, value))
            d.rectangle([V.pt(bx0, by0), V.pt(bx1, by1)], outline=D.BLUE, width=2)
            D.draw_pads(d, V, G.move_pads(pads, ox, oy), exposed=exp)
            D.dim_h(d, V, bx0, bx1, by0 - 0.4, "%.4f" % value)
        else:
            V = D.View((bx0 - 1.3, by0 - 0.3, bx1 + 1.3, by1 + 0.3))
            img, d = D.new_panel("%s  本体高 %.4f mm" % (sym, value))
            d.rectangle([V.pt(bx0, by0), V.pt(bx1, by1)], outline=D.BLUE, width=2)
            D.draw_pads(d, V, G.move_pads(pads, ox, oy), exposed=exp)
            D.dim_v(d, V, by0, by1, bx0 - 0.5, "%.4f" % value)
        return img
    if kind in ("board_w", "board_h", "hole_count", "hole_dia", "hole_offset",
                "hole_edge"):
        return _board_panel(kind, ctx, sym, value)
    if kind == "paste_hole_dia":
        V = D.View((-value, -value, value, value))
        img, d = D.new_panel("%s  钢网开孔实心圆 Ø%.3f mm" % (sym, value))
        c = V.pt(0, 0)
        r = value / 2 * V.s
        d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], outline=D.RED, width=3)
        D.put_label(d, (c[0] + 8, c[1] - 10), "Ø%.4f" % value)
        return img
    # generic fallback: pad map + value
    xs = [p[0] for p in pads]
    ys = [p[1] for p in pads]
    V = D.View((min(xs) - ox - 0.4, min(ys) - oy - 0.4,
                max(xs) - ox + 0.4, max(ys) - oy + 0.4))
    img, d = D.new_panel("%s = %s" % (sym, value))
    D.draw_pads(d, V, G.move_pads(pads, ox, oy), exposed=exp)
    return img


def _board_panel(kind, ctx, sym, value):
    bx0, by0, bx1, by1 = ctx.get("board", (0, 0, 10, 10))
    drills = ctx.get("drills", [])
    V = D.View((bx0, by0, bx1, by1))
    ttl = {"board_w": "板框宽度", "board_h": "板框高度", "hole_count": "钻孔个数",
           "hole_dia": "最大钻孔直径", "hole_offset": "孔心到板边",
           "hole_edge": "孔边到板边"}[kind]
    img, d = D.new_panel("%s  %s = %s" % (sym, ttl, value))
    for x0, y0, x1, y1 in ctx.get("edge_segs", []):
        d.line([V.pt(x0, y0), V.pt(x1, y1)], fill=(0, 0, 0), width=2)
    if not ctx.get("edge_segs"):
        d.rectangle([V.pt(bx0, by0), V.pt(bx1, by1)], outline=(0, 0, 0), width=2)
    for hx, hy, hd in drills:
        c = V.pt(hx, hy)
        r = hd / 2 * V.s
        d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r],
                  fill=(235, 235, 235), outline=D.GREY, width=2)
    W, H = bx1 - bx0, by1 - by0
    if drills and kind in ("hole_offset", "hole_edge", "hole_dia"):
        hx, hy, hd = drills[0]
        if kind == "hole_dia":
            c = V.pt(hx, hy)
            r = hd / 2 * V.s
            d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], outline=D.RED,
                      width=3)
            D.dim_h(d, V, hx - hd / 2, hx + hd / 2, hy, "Ø%.4f" % value)
        else:
            left = (hx - bx0) < (bx1 - hx)
            xe = (hx - hd / 2) if kind == "hole_edge" else hx
            D.dim_h(d, V, bx0 if left else bx1, xe, hy - hd * 0.9, "%s" % value)
    elif kind == "board_w":
        D.dim_h(d, V, bx0, bx1, by0 - 0.06 * H, "%s" % value)
    elif kind == "board_h":
        D.dim_v(d, V, by0, by1, bx0 - 0.06 * W, "%s" % value)
    return img
