#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
board.py - 把单个封装塞进一块最小板子，以便拿到真实的制造/3D 数据。

为什么非要建板子
----------------
封装校验的三条独立通路都需要"板子"这个容器：

    Gerber 反解   kicad-cli pcb export gerbers   需要 .kicad_pcb
    DRC           kicad-cli pcb drc              需要 .kicad_pcb
    3D 实物贴合   kicad-cli pcb render           需要 .kicad_pcb

而 kicad-cli 对 .kicad_mod 只能导 SVG（2D 绘图），导不出 Gerber、
跑不了 DRC、更做不了 3D。所以板子是绕不开的。

本模块只做一件事：把 .kicad_mod 内联成 .kicad_pcb。
板框默认只比封装外框大一点点 —— 够跑 DRC，又不引入无关走线。
"""

import os
import re

LAYERS = """\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(9 "F.Adhes" user "F.Adhesive")
\t\t(11 "B.Adhes" user "B.Adhesive")
\t\t(13 "F.Paste" user)
\t\t(15 "B.Paste" user)
\t\t(5 "F.SilkS" user "F.Silkscreen")
\t\t(7 "B.SilkS" user "B.Silkscreen")
\t\t(1 "F.Mask" user)
\t\t(3 "B.Mask" user)
\t\t(17 "Dwgs.User" user "User.Drawings")
\t\t(19 "Cmts.User" user "User.Comments")
\t\t(21 "Eco1.User" user "User.Eco1")
\t\t(23 "Eco2.User" user "User.Eco2")
\t\t(25 "Edge.Cuts" user)
\t\t(27 "Margin" user)
\t\t(31 "F.CrtYd" user "F.Courtyard")
\t\t(29 "B.CrtYd" user "B.Courtyard")
\t\t(35 "F.Fab" user)
\t\t(33 "B.Fab" user)
\t)"""

# 板级语句里不能出现的、只在 .kicad_mod 里合法的顶层 token
_DROP = (r"\n\t\(version [^)]*\)", r"\n\t\(generator [^)]*\)",
         r"\n\t\(generator_version [^)]*\)", r"\n\t\(embedded_fonts no\)")


def embed_footprint(mod_path, at=(10.0, 10.0), lib_id=None):
    """读 .kicad_mod，返回可以放进 (kicad_pcb ...) 的 footprint 块"""
    txt = open(mod_path, encoding="utf-8").read()
    for pat in _DROP:
        txt = re.sub(pat, "", txt)
    if lib_id:
        txt = re.sub(r'^\(footprint "([^"]*)"', r'(footprint "%s"' % lib_id, txt, count=1)
    txt = txt.replace('\t(layer "F.Cu")\n',
                      '\t(layer "F.Cu")\n\t(at %g %g)\n'
                      '\t(uuid "11111111-2222-3333-4444-555555555555")\n'
                      % (at[0], at[1]), 1)
    return txt


def footprint_bbox(mod_path):
    """(w, h) of the copper extents, from the pad list (mm)"""
    txt = open(mod_path, encoding="utf-8").read()
    xs, ys = [], []
    for m in re.finditer(r'\(pad "[^"]*" \S+ \S+\s*\n\s*\(at (-?[\d.]+) (-?[\d.]+)'
                         r'(?: -?[\d.]+)?\)\s*\n\s*\(size ([\d.]+) ([\d.]+)\)', txt):
        x, y, w, h = (float(m.group(i)) for i in (1, 2, 3, 4))
        xs += [x - w / 2, x + w / 2]
        ys += [y - h / 2, y + h / 2]
    if not xs:
        return 10.0, 10.0
    return max(xs) - min(xs), max(ys) - min(ys)


def wrap_edges(w, h, margin):
    """板框从 (margin, margin) 开始，尺寸 w x h"""
    x0, y0, x1, y1 = margin, margin, margin + w, margin + h
    pts = [(x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)]
    return [n for n in pts], "".join(
        '\t(gr_line\n\t\t(start %g %g)\n\t\t(end %g %g)\n\t\t(stroke\n'
        '\t\t\t(width 0.1)\n\t\t\t(type solid)\n\t\t)\n'
        '\t\t(layer "Edge.Cuts")\n\t\t(uuid "00000000-0000-0000-0000-%012d")\n\t)\n'
        % (a, b, c, d, 100 + i)
        for i, (a, b, c, d) in enumerate(pts))


def write_pcb(out_pcb, mod_path, lib_id=None, margin=3.0, thickness=1.6):
    """board just big enough to hold the footprint, centred.

    注意：板框从 (margin, margin) 开始画，所以封装中心必须也加上 margin ——
    否则封装会偏出板心，DRC 会报一堆 copper_edge_clearance 假违规。
    """
    w, h = footprint_bbox(mod_path)
    bw, bh = w + 2 * margin, h + 2 * margin
    at = (margin + bw / 2, margin + bh / 2)
    _, edges = wrap_edges(bw, bh, margin)
    body = embed_footprint(mod_path, at, lib_id)
    txt = ('(kicad_pcb\n\t(version 20260206)\n\t(generator "pcbnew")\n'
           '\t(generator_version "10.0")\n\t(general\n\t\t(thickness %g)\n'
           '\t\t(legacy_teardrops no)\n\t)\n\t(paper "User" %g %g)\n%s\n'
           '\t(setup\n\t\t(pad_to_mask_clearance 0)\n\t)\n%s%s\t(embedded_fonts no)\n)\n'
           % (thickness, bw, bh, LAYERS, body, edges))
    os.makedirs(os.path.dirname(os.path.abspath(out_pcb)), exist_ok=True)
    with open(out_pcb, "w", encoding="utf-8", newline="\n") as f:
        f.write(txt)
    return out_pcb, (bw, bh)
