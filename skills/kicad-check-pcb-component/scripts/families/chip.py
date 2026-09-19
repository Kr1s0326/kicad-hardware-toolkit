#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""families.chip - 两端子片式元件 / 二极管

    0402 / 0603 / 0805 / 1206 …（贴片电阻电容）
    SOD-123 / SOD-323 / SMA / SMB / SMC（二极管）
    MELF / 圆柱形

几何本质：只有 2 个端子，没有间距/矩阵概念，"跨距"就是两端子之间。
单独成族是因为它的量法只有三四个量，混进别的族里反而容易误判
（例如 2 个焊盘用网格或边引脚的算法会得到无意义的结果）。

图纸符号对照
    L              元件总长（含端子）        -> body_l   （丝印外框；无丝印时用端子外缘跨距）
    W              元件总宽                  -> body_w
    T              元件厚度（Z 向）          -> na
    a / b          端子宽度 / 长度           -> pad_w / pad_l
    e / g          端子间距（内侧间隙）       -> pad_gap
    端子跨距（中心到中心）                    -> pad_span
"""

from core import common
from core import draw as D
from core import geometry as G

FAMILY = "chip"
LABEL = "两端子片式元件（0402/0603/0805、SOD-123、MELF …）"
DESCRIPTION = "只有 2 个端子：端子尺寸、端子跨距、端子间隙、本体外形"

COUNT_KINDS = {"count", "hole_count"}

KINDS = {
    "count": "端子数（应为 2）",
    "pad_w": "端子宽度（垂直于跨距方向）",
    "pad_l": "端子长度（跨距方向）",
    "pad_span": "端子跨距（两端子中心距）",
    "pad_edge_span": "端子外缘跨距（最外缘到最外缘）",
    "pad_gap": "端子间隙（两内侧边缘距离）",
    "body_long": "本体长边 L（丝印外框，取 X/Y 长边，与摆放方向无关）",
    "body_short": "本体短边 W（丝印外框）",
    "body_w": "本体 X 向尺寸（丝印外框，core.common）",
    "body_h": "本体 Y 向尺寸（丝印外框，core.common）",
}


def detect(ctx):
    return len(ctx["pads"]) == 2


def prepare(ctx):
    pads = ctx["pads"]
    (x1, y1, w1, h1), (x2, y2, w2, h2) = pads[0], pads[1]
    horizontal = abs(x2 - x1) >= abs(y2 - y1)
    span = abs(x2 - x1) if horizontal else abs(y2 - y1)
    span_all = (G.pad_span(pads, 0) if horizontal else G.pad_span(pads, 1))
    lo = min(x1 - w1 / 2, x2 - w2 / 2) if horizontal else \
        min(y1 - h1 / 2, y2 - h2 / 2)
    hi = max(x1 + w1 / 2, x2 + w2 / 2) if horizontal else \
        max(y1 + h1 / 2, y2 + h2 / 2)
    inner = (max(x1 - w1 / 2, x2 - w2 / 2) - min(x1 + w1 / 2, x2 + w2 / 2)) \
        if horizontal else \
        (max(y1 - h1 / 2, y2 - h2 / 2) - min(y1 + h1 / 2, y2 + h2 / 2))
    ctx["chip"] = {
        "horizontal": horizontal,
        "span": span,
        "span_edges": span_all,                       # centre span == span here
        "out_edge_span": hi - lo,
        "gap": max(inner, 0.0),
        "pad_w": min(h1, h2) if horizontal else min(w1, w2),   # across the span
        "pad_l": min(w1, w2) if horizontal else min(h1, h2),   # along the span
        "n": len(pads),
    }
    ctx["pitch"] = span
    ctx["count"] = len(pads)


def measure(kind, ctx, row):
    c = ctx["chip"]
    if kind == "count":
        return c["n"], {}
    if kind == "pad_w":
        return c["pad_w"], {}
    if kind == "pad_l":
        return c["pad_l"], {}
    if kind == "pad_span" or kind == "pitch":
        return c["span"], {}
    if kind == "pad_edge_span":
        return c["out_edge_span"], {}
    if kind == "pad_gap":
        return c["gap"], {}
    if kind in ("body_long", "body_short"):
        if not ctx.get("body"):
            return None, {}
        x0, y0, x1, y1 = ctx["body"]
        w, h = x1 - x0, y1 - y0
        return (max(w, h) if kind == "body_long" else min(w, h)), {}
    return common.measure(kind, ctx, row)


def _view(ctx, pad=0.4):
    pads = ctx["pads"]
    ox, oy = ctx["origin"]
    xs = [p[0] - ox for p in pads]
    ys = [p[1] - oy for p in pads]
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def _draw(d, V, ctx):
    ox, oy = ctx["origin"]
    D.draw_pads(d, V, [(x - ox, y - oy, w, h) for x, y, w, h in ctx["pads"]])


def _extent(ctx):
    """-> (lo, hi) of the pads along the span axis (board coordinates)"""
    c = ctx["chip"]
    pads = ctx["pads"]
    if c["horizontal"]:
        lo = min(p[0] - p[2] / 2 for p in pads)
        hi = max(p[0] + p[2] / 2 for p in pads)
    else:
        lo = min(p[1] - p[3] / 2 for p in pads)
        hi = max(p[1] + p[3] / 2 for p in pads)
    return lo, hi


def panel(kind, ctx, row, value, extra, sym):
    c = ctx["chip"]
    ox, oy = ctx["origin"]
    V = D.View(_view(ctx, 0.5))
    if kind in ("pad_span", "pitch"):
        img, d = D.new_panel("%s  端子跨距 = %.4f mm" % (sym, value))
        _draw(d, V, ctx)
        ctr = [p[0] - ox if c["horizontal"] else p[1] - oy for p in ctx["pads"]]
        if c["horizontal"]:
            D.dim_h(d, V, min(ctr), max(ctr), V.win[1] + 0.3, "%.4f" % value)
        else:
            D.dim_v(d, V, min(ctr), max(ctr), V.win[0] + 0.3, "%.4f" % value)
        return img
    if kind == "pad_edge_span":
        lo, hi = _extent(ctx)
        img, d = D.new_panel("%s  端子外缘跨距 = %.4f mm" % (sym, value))
        _draw(d, V, ctx)
        if c["horizontal"]:
            D.dim_h(d, V, lo - ox, hi - ox, V.win[1] + 0.3, "%.4f" % value)
        else:
            D.dim_v(d, V, lo - oy, hi - oy, V.win[0] + 0.3, "%.4f" % value)
        return img
    if kind == "pad_gap":
        img, d = D.new_panel("%s  端子间隙 = %.4f mm" % (sym, value))
        _draw(d, V, ctx)
        p1, p2 = ctx["pads"]
        if c["horizontal"]:
            a = max(p1[0] - p1[2] / 2, p2[0] - p2[2] / 2) - ox
            b = min(p1[0] + p1[2] / 2, p2[0] + p2[2] / 2) - ox
            D.dim_h(d, V, min(a, b), max(a, b), p1[1] - oy - 0.25, "%.4f" % value)
        else:
            a = max(p1[1] - p1[3] / 2, p2[1] - p2[3] / 2) - oy
            b = min(p1[1] + p1[3] / 2, p2[1] + p2[3] / 2) - oy
            D.dim_v(d, V, min(a, b), max(a, b), p1[0] - ox - 0.25, "%.4f" % value)
        return img
    if kind in ("pad_w", "pad_l"):
        x, y, w, h = ctx["pads"][0]
        x, y = x - ox, y - oy
        img, d = D.new_panel("%s  %s = %.4f mm"
                             % (sym, "端子宽度" if kind == "pad_w" else "端子长度", value))
        _draw(d, V, ctx)
        if c["horizontal"]:
            D.dim_v(d, V, y - h / 2, y + h / 2, x - w * 1.4, "%.4f" % value)
        else:
            D.dim_h(d, V, x - w / 2, x + w / 2, y - h * 1.4, "%.4f" % value)
        return img
    if kind == "count":
        img, d = D.new_panel("%s  端子数 = %d" % (sym, value))
        _draw(d, V, ctx)
        D.put_label(d, (V.pt(V.win[0], V.win[1])[0] + 10,
                        V.pt(V.win[0], V.win[3])[1] + 10),
                    "实测计数 = %d" % value, D.BLUE)
        return img
    return common.panel(kind, ctx, row, value, extra, sym)
