#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""families.peripheral - 四边/两边引脚类封装（引脚在"边"上）

    QFN / DFN / QFP / LQFP / TQFP / SOIC / SOP / SSOP / TSSOP / MSOP /
    SOT-23 / SOT-223 / DPAK …

几何本质：焊盘贴在封装**外缘**（2 边或 4 边），中间是空的或只有一块散热焊盘。
和 grid_array（焊盘铺满底面）是两套完全不同的量法与画法，所以单独一个文件。

图纸符号对照（JEDEC 四边/两边引脚）
    D, E            本体外形尺寸            -> body_w / body_h        (core.common)
    D1, E1 或 D, E  引脚跨距（外缘到外缘）   -> pad_edge_span_x / _y
    D2, E2          散热焊盘（exposed pad） -> ep_x / ep_y
    b               引脚宽度                -> lead_width
    L               引脚长度（焊盘长）       -> lead_length
    e               引脚间距                -> lead_pitch
    MD, ME          每边引脚数              -> leads_per_side
    N               引脚总数                -> lead_count
    A, A1, A2, A3   高度类（Z 向）           -> na                     (core.common)
"""

from core import common
from core import draw as D
from core import geometry as G

FAMILY = "peripheral"
LABEL = "四边/两边引脚类（QFN / QFP / SOIC / SOT-23 …）"
DESCRIPTION = "焊盘贴在封装外缘（2 边或 4 边），含引脚间距、跨距、脚宽/脚长、散热焊盘等专有项"

COUNT_KINDS = {"count", "lead_count", "leads_per_side", "side_count",
               "hole_count"}

KINDS = {
    "lead_pitch": "引脚间距（同一边相邻引脚中心距）",
    "lead_count": "引脚总数（不含散热焊盘）",
    "leads_per_side": "每边引脚数（可用 side 指定 left/right/top/bottom）",
    "side_count": "有引脚的边数（2 = SOIC/SOT-23，4 = QFN/QFP）",
    "pad_span_x": "左右两排引脚的跨距（中心到中心）",
    "pad_span_y": "上下两排引脚的跨距（中心到中心）",
    "pad_edge_span_x": "引脚跨距（左外缘到右外缘）",
    "pad_edge_span_y": "引脚跨距（上外缘到下外缘）",
    "lead_width": "引脚宽度 b（焊盘窄边）",
    "lead_length": "引脚长度 L（焊盘长边）",
    "ep_x": "散热焊盘宽度 D2",
    "ep_y": "散热焊盘高度 E2",
    "count": "焊盘总数（含散热焊盘）",
    "pitch": "引脚间距（= lead_pitch）",
    "body_w": "本体宽度（core.common）",
    "body_h": "本体高度（core.common）",
}


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------
def detect(ctx):
    """pads sit on the outside edges: the middle of the bounding box is empty"""
    return G.classify_pads(ctx["pads"], ctx.get("_collinear_tol", 0.05))["type"]         == "peripheral"


def prepare(ctx):
    pads = ctx["pads"]
    info = G.classify_pads(pads, ctx.get("_collinear_tol", 0.05))
    if info["type"] != "peripheral":                 # still usable, best effort
        info.setdefault("sides", {})
    ctx["peri"] = info
    ctx["pitch"] = info.get("lead_pitch", 0.0)
    ctx["count"] = len(pads)


def _leads(ctx):
    """side pads only (no exposed pad), normalised so w >= h"""
    info = ctx["peri"]
    out = []
    for side, v in info["sides"].items():
        for p in v:
            x, y, w, h = p
            if w < h:
                w, h = h, w
            out.append((x, y, w, h, side))
    if not out:                                       # fall back: all pads
        for p in ctx["pads"]:
            x, y, w, h = p
            out.append((x, y, max(w, h), min(w, h), "?"))
    return out


def _side_pads(ctx, side, vertical):
    return [p for p in _leads(ctx) if p[4] == side]


# ---------------------------------------------------------------------------
# measurement
# ---------------------------------------------------------------------------
def measure(kind, ctx, row):
    info = ctx["peri"]
    if kind == "lead_pitch" or kind == "pitch":
        return info.get("lead_pitch", 0.0), {}
    if kind == "lead_count":
        return info.get("n", len(ctx["pads"])), {}
    if kind == "count":
        return len(ctx["pads"]), {}
    if kind == "leads_per_side":
        side = row.get("side")
        if side:
            return len(info["sides"].get(side, [])), {"side": side}
        return info.get("leads_per_side", 0), {"all": info.get("leads_per_side_all", {})}
    if kind == "side_count":
        return info.get("n_sides", 0), {}
    if kind == "pad_span_x":
        return info.get("pad_span_x", 0.0), {}
    if kind == "pad_span_y":
        return info.get("pad_span_y", 0.0), {}
    if kind == "pad_edge_span_x":
        return info.get("pad_edge_span_x", 0.0), {}
    if kind == "pad_edge_span_y":
        return info.get("pad_edge_span_y", 0.0), {}
    if kind == "lead_width":
        return info.get("lead_width", 0.0), {}
    if kind == "lead_length":
        return info.get("lead_length", 0.0), {}
    if kind in ("ep_x", "ep_y"):
        ep = info.get("exposed")
        if not ep:
            return None, {}
        return (ep["w"] if kind == "ep_x" else ep["h"]), {"exposed": ep}
    return common.measure(kind, ctx, row)


# ---------------------------------------------------------------------------
# pictures
# ---------------------------------------------------------------------------
def _map_view(ctx, pad=0.5):
    pads = ctx["pads"]
    ox, oy = ctx["origin"]
    xs = [p[0] - ox for p in pads]
    ys = [p[1] - oy for p in pads]
    return (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)


def _draw_map(d, V, ctx):
    ox, oy = ctx["origin"]
    P = G.move_pads(ctx["pads"], ox, oy)
    exposed = ctx["peri"].get("exposed")
    exposed = (exposed["x"] - ox, exposed["y"] - oy, exposed["w"],
               exposed["h"], exposed.get("shape", "")) if exposed else None
    D.draw_pads(d, V, P, exposed=exposed)
    return P


def panel(kind, ctx, row, value, extra, sym):
    info = ctx["peri"]
    # 绘制路径（_draw_map / _map_view）用的是**减掉 origin 的相对坐标**，
    # 而 info["sides"] / _leads() 里存的是**绝对坐标**。
    # 踩过的坑：几个面板直接拿绝对坐标去建 View 窗口（还传绝对值给尺寸线），
    # 于是窗口和焊盘永不相交 —— e / b / L / 列中心距 四个面板全是空白，
    # 只剩一条尺寸线悬在图上。拿真实封装逐张看图才发现的。
    _ox, _oy = ctx["origin"]

    def _rel(p):
        return (p[0] - _ox, p[1] - _oy) + tuple(p[2:])

    if kind == "lead_pitch" or kind == "pitch":
        # two neighbouring leads of the left side (or the first side available)
        side = "left" if info["sides"].get("left") else next(iter(info["sides"]))
        leads = sorted(_side_pads(ctx, side, False), key=lambda p: p[1])
        if len(leads) < 2:
            leads = sorted(_leads(ctx), key=lambda p: p[1])
        a, b = _rel(leads[0]), _rel(leads[1])
        vertical = side in ("left", "right")
        if vertical:
            V = D.View((a[0] - a[2] * 2.2, a[1] - a[3] * 1.8,
                        a[0] + a[2] * 2.2, b[1] + b[3] * 1.8))
            img, d = D.new_panel("%s  引脚间距 e = %.3f mm" % (sym, value))
            _draw_map(d, V, ctx)
            D.dim_v(d, V, a[1], b[1], a[0] - a[2] * 1.5, "%.4f" % value)
        else:
            V = D.View((a[0] - a[2] * 1.8, a[1] - a[3] * 2.2,
                        b[0] + b[2] * 1.8, a[1] + a[3] * 2.2))
            img, d = D.new_panel("%s  引脚间距 e = %.3f mm" % (sym, value))
            _draw_map(d, V, ctx)
            D.dim_h(d, V, a[0], b[0], a[1] - a[3] * 1.5, "%.4f" % value)
        return img

    if kind in ("pad_span_x", "pad_edge_span_x", "pad_span_y", "pad_edge_span_y"):
        edge = "edge" in kind
        axis = 0 if kind.endswith("_x") else 1
        pad = [_rel(q) for q in _leads(ctx)]
        lo = min(p[axis] - (p[2] if axis == 0 else p[3]) / 2 for p in pad) \
            if edge else min(p[axis] for p in pad)
        hi = max(p[axis] + (p[2] if axis == 0 else p[3]) / 2 for p in pad) \
            if edge else max(p[axis] for p in pad)
        V = D.View(_map_view(ctx, 0.6))
        ttl = "%s  %s = %.4f mm" % (sym, "引脚跨距（外缘）" if edge else "引脚跨距（中心）",
                                    value)
        img, d = D.new_panel(ttl)
        _draw_map(d, V, ctx)
        if axis == 0:
            D.dim_h(d, V, lo, hi, V.win[1] + 0.25, "%.4f" % value)
        else:
            D.dim_v(d, V, lo, hi, V.win[0] + 0.25, "%.4f" % value)
        return img

    if kind in ("lead_width", "lead_length"):
        leads = [_rel(q) for q in _leads(ctx)]
        p = min(leads, key=lambda q: min(q[2], q[3]))
        x, y, w, h, side = p
        V = D.View((x - w * 2, y - h * 2, x + w * 2, y + h * 2))
        img, d = D.new_panel("%s  %s = %.3f mm"
                             % (sym, "引脚宽度 b" if kind == "lead_width"
                                else "引脚长度 L", value))
        _draw_map(d, V, ctx)
        if kind == "lead_width":
            D.dim_h(d, V, x - h / 2, x + h / 2, y - h * 1.2, "%.4f" % value) \
                if side in ("top", "bottom") else \
                D.dim_v(d, V, y - h / 2, y + h / 2, x - w * 1.2, "%.4f" % value)
        else:
            D.dim_h(d, V, x - w / 2, x + w / 2, y - h * 1.2, "%.4f" % value) \
                if side in ("top", "bottom") else \
                D.dim_v(d, V, y - w / 2, y + w / 2, x - w * 1.2, "%.4f" % value)
        return img

    if kind in ("ep_x", "ep_y"):
        ep = info["exposed"]
        ox, oy = ctx["origin"]
        x, y = ep["x"] - ox, ep["y"] - oy
        V = D.View((x - ep["w"], y - ep["h"], x + ep["w"], y + ep["h"]))
        img, d = D.new_panel("%s  散热焊盘 %s = %.3f mm"
                             % (sym, "D2" if kind == "ep_x" else "E2", value))
        _draw_map(d, V, ctx)
        if kind == "ep_x":
            D.dim_h(d, V, x - ep["w"] / 2, x + ep["w"] / 2, y - ep["h"] * 0.7,
                    "%.4f" % value)
        else:
            D.dim_v(d, V, y - ep["h"] / 2, y + ep["h"] / 2, x - ep["w"] * 0.7,
                    "%.4f" % value)
        return img

    if kind in ("leads_per_side", "side_count", "lead_count", "count"):
        V = D.View(_map_view(ctx, 0.4))
        if kind == "leads_per_side":
            ttl = "%s  每边引脚数 = %d" % (sym, value)
        elif kind == "side_count":
            ttl = "%s  有引脚的边数 = %d" % (sym, value)
        else:
            ttl = "%s  引脚总数 = %d（不含散热焊盘）" % (sym, value) \
                if kind == "lead_count" else "%s  焊盘总数 = %d" % (sym, value)
        img, d = D.new_panel(ttl)
        _draw_map(d, V, ctx)
        if kind in ("leads_per_side", "side_count"):
            for side, n in sorted(info.get("leads_per_side_all", {}).items()):
                pos = {"left": (0.06, 0.5), "right": (0.94, 0.5),
                       "top": (0.5, 0.04), "bottom": (0.5, 0.96)}[side]
                c = V.pt(V.win[0] + (V.win[2] - V.win[0]) * pos[0],
                         V.win[1] + (V.win[3] - V.win[1]) * pos[1])
                D.put_label(d, (c[0] - 14, c[1] - 10),
                            "%s %d" % ({"left": "左", "right": "右",
                                        "top": "上", "bottom": "下"}[side], n),
                            D.BLUE)
        else:
            D.put_label(d, (V.pt(V.win[0], V.win[1])[0] + 10,
                            V.pt(V.win[0], V.win[3])[1] + 10),
                        "实测计数 = %d" % value, D.BLUE)
        return img

    return common.panel(kind, ctx, row, value, extra, sym)
