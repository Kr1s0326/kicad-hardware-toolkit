#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_part.py - 从一个 part_spec.json 生成完整的 KiCad 元件库。

    part_spec.json  ->  <lib>.kicad_sym          原理图符号
                    ->  <lib>.pretty/<fp>.kicad_mod   PCB 封装
                    ->  <lib>.groups.json        功能分组声明（给校验 skill 用）
                    ->  <lib>.fp.spec.json       尺寸检查项（给封装校验 skill 用）

**唯一真源**：引脚表 + 封装图纸数字全部来自 part_spec.json。改了 spec 就重生成，
不要手工去改产物 —— 手改的产物下次生成就丢了，而且没人知道哪个是对的。

本 skill 的边界
---------------
只保证"能被 KiCad 加载"（`kicad_io.loadable()`）。**不做正确性判断**：
不看图、不量 Gerber、不比对手册。那是 `kicad-check-sch-component` 和
`kicad-check-pcb-component` 的事。这个边界是刻意的 —— 创建侧一旦开始
"看完图觉得没问题"，校验就退化成自证。

几何约定（在官方库上校准过）
----------------------------
* 丝印框 = 本体/2 + 0.11；被焊盘裁掉的部分，边界 = 焊盘边缘 + 0.2 + 丝印半宽 0.06 = +0.26
* 装配层 = 本体的真实外形，Pin1 角倒角
* 外框 = max(本体, 焊盘外沿) + 0.25，是"十字形"多边形而不是矩形
* 圆角矩形焊盘用 roundrect_rratio = 圆角半径 / 焊盘短边

CLI
---
    python make_part.py part_spec.json --outdir out [--verify]
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHARED = os.path.normpath(os.path.join(HERE, "..", "..", "..", "shared"))
if SHARED not in sys.path:
    sys.path.insert(0, SHARED)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from cli import guard                             # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:                                       # noqa: BLE001
    pass

import kicad_io as kio
import toolchain                                  # noqa: E402

MIL = 0.0254


def plain(name):
    """~{CS} -> CS。groups.json 里必须用裸名字，否则校验侧对不上。"""
    return name.replace("~{", "").replace("}", "")


SILK_OFF = 0.11                 # 丝印离本体
SILK_W = 0.12
COPPER_CLR = 0.2                # KiCad 生成器的 silk/pad 间距
CRTYD = 0.25                    # 外框余量
SILK_MIN = 0.05                 # 短于这个的丝印残段丢掉


# ============================================================ 符号布局
def layout_symbol(spec):
    st = spec.get("symbol_style", {})
    pitch = st.get("pitch_mil", 200) * MIL
    gap = st.get("group_gap_mil", 400) * MIL
    plen = st.get("pin_length_mil", 100) * MIL
    hw = st.get("body_half_width_mil", 300) * MIL
    topm = st.get("top_margin_mil", 100) * MIL
    order_by_side = spec.get("groups", {})
    # top：每边第一个引脚对齐（KiCad 官方库的惯例，INA226 就是这样）
    # center: 每边各自居中
    align = st.get("side_align", "top")

    pins = spec["pins"]

    def sequence(side):
        sp = [p for p in pins if p.get("side") == side]
        order = order_by_side.get(side)
        if not order:
            return sp
        seq, seen = [], set()
        for g in order:
            for p in sp:
                if p.get("group") == g and id(p) not in seen:
                    seq.append(p); seen.add(id(p))
        for p in sp:                         # 漏在 order 之外的原样接在后面
            if id(p) not in seen:
                seq.append(p)
        return seq

    def span(seq):
        t, prev, first = 0.0, None, True
        for p in seq:
            if not first:
                t += gap if p.get("group") != prev else pitch
            prev, first = p.get("group"), False
        return t

    seqs = {s: sequence(s) for s in ("left", "right")}
    spans = {s: span(q) for s, q in seqs.items()}
    y_top = max(spans.values()) / 2.0 if align == "top" and spans else None

    placed, groups_out = [], {}
    for side in ("left", "right"):
        seq = seqs[side]
        if not seq:
            continue
        rot = 0 if side == "left" else 180
        y = y_top if y_top is not None else spans[side] / 2.0
        prev, first = None, True
        for p in seq:
            if not first:
                y -= gap if p.get("group") != prev else pitch
            x = -(hw + plen) if side == "left" else (hw + plen)
            placed.append(dict(p, x=round(x, 4), y=round(y, 4), rot=rot))
            prev, first = p.get("group"), False
        groups_out[side] = [[plain(q["name"]) for q in seq
                             if q.get("group") == g]
                            for g in (order_by_side.get(side) or [])]
        groups_out[side] = [g for g in groups_out[side] if g]

    side_pins = [p for p in placed if p["rot"] in (0, 180)]
    ys = [p["y"] for p in side_pins] or [0.0]
    bx0, bx1 = -hw, hw
    by0 = min(ys) - topm
    by1 = max(ys) + topm

    # 上/下电源引脚：贴在已经算好的本体上
    for want, rot, edge in (("top", 270, by1), ("bottom", 90, by0)):
        grp = [p for p in pins if p.get("side") == want]
        n = len(grp)
        for i, p in enumerate(grp):
            x = 0.0 if n == 1 else (i - (n - 1) / 2.0) * pitch
            y = edge + plen if want == "top" else edge - plen
            placed.append(dict(p, x=round(x, 4), y=round(y, 4), rot=rot))
        if grp:
            groups_out[want] = [[plain(p["name"]) for p in grp]]
    return {"pins": placed, "body": (bx0, by0, bx1, by1),
            "groups": groups_out,
            "params": {"pitch": pitch, "gap": gap, "plen": plen}}


def emit_symbol(spec, lay, sym_version):
    name = spec["symbol"]
    st = spec.get("symbol_style", {})
    font = st.get("font", 1.27)
    bx0, by0, bx1, by1 = lay["body"]
    P = []

    def prop(k, v, x, y, hide=False, justify=None):
        P.append('\t\t(property "%s" "%s"\n\t\t\t(at %s %s 0)\n'
                 '\t\t\t(show_name no)\n\t\t\t(do_not_autoplace no)\n%s\t\t)\n'
                 % (k, v, kio.num(x), kio.num(y),
                    ('\t\t\t(hide yes)\n' if hide else "")))

    prop("Reference", spec.get("reference", "U"), bx0, by1 + font * 1.0)
    prop("Value", name, bx1 * 0.5, by1 + font * 1.0)
    prop("Footprint", "%s:%s" % (spec["library"], spec["footprint"]),
         bx0, by0 - font * 1.0, hide=True)
    prop("Datasheet", spec.get("datasheet", ""), bx0, by0 - font * 2.0, hide=True)
    prop("Description", spec.get("description", ""), 0, 0, hide=True)
    prop("ki_keywords", spec.get("keywords", ""), 0, 0, hide=True)
    prop("ki_fp_filters", spec.get("fp_filters", "*"), 0, 0, hide=True)

    pins = ""
    for p in sorted(lay["pins"], key=lambda q: (len(q["number"]), q["number"])):
        nm = p["name"]
        pins += ('\t\t\t(pin %s line\n\t\t\t\t(at %s %s %d)\n\t\t\t\t(length %s)\n'
                 '\t\t\t\t(name "%s"\n\t\t\t\t\t(effects\n\t\t\t\t\t\t(font\n'
                 '\t\t\t\t\t\t\t(size %s %s)\n\t\t\t\t\t\t)\n\t\t\t\t\t)\n\t\t\t\t)\n'
                 '\t\t\t\t(number "%s"\n\t\t\t\t\t(effects\n\t\t\t\t\t\t(font\n'
                 '\t\t\t\t\t\t\t(size %s %s)\n\t\t\t\t\t\t)\n\t\t\t\t\t)\n\t\t\t\t)\n'
                 '\t\t\t)\n'
                 % (p["etype"], kio.num(p["x"]), kio.num(p["y"]), p["rot"],
                    kio.num(lay["params"]["plen"]), nm,
                    kio.num(font), kio.num(font), p["number"],
                    kio.num(font), kio.num(font)))

    return ('(kicad_symbol_lib\n\t(version %s)\n\t(generator "kicad_symbol_editor")\n'
            '\t(generator_version "%s")\n\t(symbol "%s"\n'
            '\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n'
            '\t\t(in_pos_files yes)\n\t\t(duplicate_pin_numbers_are_jumpers no)\n'
            '%s\t\t(symbol "%s_0_1"\n\t\t\t(rectangle\n\t\t\t\t(start %s %s)\n'
            '\t\t\t\t(end %s %s)\n\t\t\t\t(stroke\n\t\t\t\t\t(width 0.254)\n'
            '\t\t\t\t\t(type default)\n\t\t\t\t)\n\t\t\t\t(fill\n'
            '\t\t\t\t\t(type background)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n'
            '\t\t(symbol "%s_1_1"\n%s\t\t)\n\t\t(embedded_fonts no)\n\t)\n)\n'
            % (sym_version, spec.get("generator_version", "10.0"), name,
               "".join(P), name, kio.num(bx0), kio.num(by1),
               kio.num(bx1), kio.num(by0), name, pins))


# ============================================================ 封装布局
def layout_pads(pkg):
    sides = pkg.get("sides", ["left", "right"])
    n = int(pkg["pads_per_side"])
    pitch = float(pkg["pitch"])
    rx = float(pkg["row_span_x"]) / 2.0
    ry = float(pkg.get("row_span_y", pkg.get("row_span_x"))) / 2.0
    pad = pkg["pad"]
    pw, ph = float(pad["length"]), float(pad["width"])   # 横放的焊盘：长在 x

    def row(k):
        return [round((i - (k - 1) / 2.0) * pitch, 4) for i in range(k)]

    pads, num = [], 1
    if len(sides) == 2:                                  # 双排（SOIC/TSSOP/VSSOP）
        left, right = sides
        ys = row(n)
        # KiCad 封装里 +Y 是【向下】，所以 pad1（左上）的 y 是负的。
        # 一开始写成 -y，pad1 跑到左下角去了 —— 和 Pin1 三角标的方向正好相反。
        for y in ys:                                     # 左边：上 -> 下
            pads.append((str(num), -rx, y, pw, ph)); num += 1
        for y in reversed(ys):                           # 右边：下 -> 上
            pads.append((str(num), rx, y, pw, ph)); num += 1
    else:                                                # 四排（QFP/QFN）
        l, b, r, t = sides
        xs = row(n)
        for y in row(n):
            pads.append((str(num), -rx, y, pw, ph)); num += 1
        for x in xs:
            pads.append((str(num), x, ry, ph, pw)); num += 1
        for y in reversed(row(n)):
            pads.append((str(num), rx, y, pw, ph)); num += 1
        for x in reversed(xs):
            pads.append((str(num), x, -ry, ph, pw)); num += 1
    return pads


def _clip(line, blocked):
    """从 [a,b] 里减掉 blocked 区间，返回剩下的段（长度 > SILK_MIN）"""
    a, b = line
    segs = [(a, b)]
    for c, d in blocked:
        out = []
        for s0, s1 in segs:
            if d <= s0 or c >= s1:
                out.append((s0, s1)); continue
            if c > s0:
                out.append((s0, c))
            if d < s1:
                out.append((d, s1))
        segs = out
    return [s for s in segs if s[1] - s[0] > SILK_MIN]


def silk_lines(pads, body):
    """丝印框，被焊盘按 KiCad 的间距规则裁掉。在官方 MSOP-10 / SOIC-8 上校准过。"""
    bw, bh = body["w"], body["h"]
    hx, hy = bw / 2 + SILK_OFF, bh / 2 + SILK_OFF
    c = COPPER_CLR + SILK_W / 2
    out = []
    for Y in (-hy, hy):                                   # 上下两条横线
        blocked = [(x - w / 2 - c, x + w / 2 + c) for (_, x, y, w, h) in pads
                   if abs(Y - y) <= h / 2 + c]
        for s0, s1 in _clip((-hx, hx), blocked):
            out.append((s0, Y, s1, Y))
    for X in (-hx, hx):                                   # 左右两条竖线
        blocked = [(y - h / 2 - c, y + h / 2 + c) for (_, x, y, w, h) in pads
                   if abs(X - x) <= w / 2 + c]
        for s0, s1 in _clip((-hy, hy), blocked):
            out.append((X, s0, X, s1))
    return out


def courtyard_cross(pads, body):
    """外框 = max(本体, 焊盘外沿) + 0.25 的并集轮廓。

    不是矩形，是一个 12 段的“十字/凸字形”：中间一段按本体，两端按焊盘外沿。
    在官方 MSOP-10 与 SOIC-8 上逐段核对过，两边的 12 条线段完全对得上。

    踩过的坑：不能因为 PY < by 就把 PY 拉成 by —— PY < by 正是“两端比中间窄”
    这个形状本身。拉平了外框就退化成矩形，贴片机的避让区就不准了。
    """
    hx, hy = body["w"] / 2, body["h"] / 2
    bx, by = hx + CRTYD, hy + CRTYD
    PX = max((abs(x) + w / 2 for (_, x, y, w, h) in pads), default=hx) + CRTYD
    PY = max((abs(y) + h / 2 for (_, x, y, w, h) in pads), default=hy) + CRTYD
    # 十字形只在“焊盘在 x 方向伸出、在 y 方向比本体窄”时成立（双排 gullwing）。
    # 其余情况（四边都有焊盘的 QFP/QFN，或焊盘完全在本体内）就是普通矩形 ——
    # 但矩形必须是 max(本体, 焊盘外沿)！
    # 踩过的坑：这里曾写成 bx/by，于是 QFP 的外框（7.5mm）比焊盘外沿（10.4mm）
    # 还小 2.9mm，贴片机的避让区直接是错的。双排封装走十字分支所以看不出来。
    if PX <= bx + 1e-9 or PY >= by - 1e-9:
        X, Y = max(bx, PX), max(by, PY)
        return [(-X, -Y, X, -Y), (X, -Y, X, Y),
                (X, Y, -X, Y), (-X, Y, -X, -Y)]
    return [
        (-bx, -by, bx, -by),          # 1  中段上边
        (bx, -by, bx, -PY),           # 2
        (bx, -PY, PX, -PY),           # 3  右端块上边
        (PX, -PY, PX, PY),            # 4  右端块右边
        (PX, PY, bx, PY),             # 5
        (bx, PY, bx, by),             # 6
        (bx, by, -bx, by),            # 7  中段下边
        (-bx, by, -bx, PY),           # 8
        (-bx, PY, -PX, PY),           # 9  左端块下边
        (-PX, PY, -PX, -PY),          # 10 左端块左边
        (-PX, -PY, -bx, -PY),         # 11
        (-bx, -PY, -bx, -by),         # 12
    ]


def emit_footprint(spec, pads, fp_version, gen_version):
    pkg = spec["package"]
    body = pkg["body"]
    name = spec["footprint"]
    r = pkg["pad"].get("roundrect_rratio")
    corner = pkg.get("pad1_corner", "top-left")
    S = []

    def line(x0, y0, x1, y1, layer, w=0.12):
        S.append('\t(fp_line\n\t\t(start %s %s)\n\t\t(end %s %s)\n\t\t(stroke\n'
                 '\t\t\t(width %s)\n\t\t\t(type solid)\n\t\t)\n\t\t(layer "%s")\n\t)\n'
                 % (kio.num(x0), kio.num(y0), kio.num(x1), kio.num(y1),
                    kio.num(w), layer))

    hx, hy = body["w"] / 2, body["h"] / 2
    # 位号/Value 的文字位置：必须清过**所有**别的东西。
    #   ① 外框之外（否则四边封装的 QFP/QFN 会压到上/下排焊盘）
    #   ② 丝印之外，包括 Pin1 三角标（它比丝印框还往外伸 0.16）
    #   ③ 再留 KiCad 默认的丝印间距 0.2 + 文字半高
    # 前两版分别踩了 ① 和 ②：只按 body/2+0.95 放，双排封装看不出来；
    # 改成外框+0.5 之后，又和 Pin1 三角标只差 0.08mm，被 DRC 的 silk_overlap 抓到。
    cx = courtyard_cross(pads, body)
    crt_y = max(abs(v) for s in cx for v in (s[1], s[3]))
    marker_y = hy + SILK_OFF + 0.16          # Pin1 三角标最远到这儿
    text_half = 0.5                          # 位号用 (size 1 1)
    out_y = max(crt_y, marker_y) + 0.2 + text_half
    for x0, y0, x1, y1 in silk_lines(pads, body):
        line(x0, y0, x1, y1, "F.SilkS", SILK_W)

    # Pin1 三角标（与官方库同款：角点外 0.16/0.55 处）
    sx, sy = (-hx - SILK_OFF, -hy - SILK_OFF) if corner == "top-left" else \
             (hx + SILK_OFF, -hy - SILK_OFF)
    s = -1 if corner == "top-left" else 1
    S.append('\t(fp_poly\n\t\t(pts\n\t\t\t(xy %s %s)\n\t\t\t(xy %s %s)\n'
             '\t\t\t(xy %s %s)\n\t\t)\n\t\t(stroke\n\t\t\t(width %s)\n'
             '\t\t\t(type solid)\n\t\t)\n\t\t(fill yes)\n\t\t(layer "F.SilkS")\n\t)\n'
             % (kio.num(sx + s * 0.55), kio.num(sy + 0.17),
                kio.num(sx + s * 0.79), kio.num(sy - 0.16),
                kio.num(sx + s * 0.31), kio.num(sy - 0.16), kio.num(SILK_W)))

    cy = courtyard_cross(pads, body)
    for x0, y0, x1, y1 in cy:
        line(x0, y0, x1, y1, "F.CrtYd", 0.05)
    # 装配层本体 + Pin1 倒角
    ch = 0.75
    pts = [(-hx + ch, -hy), (hx, -hy), (hx, hy), (-hx, hy), (-hx, -hy + ch)]
    if corner != "top-left":
        pts = [(hx - ch, -hy), (hx, -hy + ch), (hx, hy), (-hx, hy), (-hx, -hy)]
    S.append('\t(fp_poly\n\t\t(pts\n%s\t\t)\n\t\t(stroke\n\t\t\t(width 0.1)\n'
             '\t\t\t(type solid)\n\t\t)\n\t\t(fill no)\n\t\t(layer "F.Fab")\n\t)\n'
             % "".join("\t\t\t(xy %s %s)\n" % (kio.num(a), kio.num(b))
                       for a, b in pts))
    S.append('\t(fp_text user "${REFERENCE}"\n\t\t(at 0 0 0)\n\t\t(layer "F.Fab")\n'
             '\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 0.75 0.75)\n'
             '\t\t\t\t(thickness 0.11)\n\t\t\t)\n\t\t)\n\t)\n')

    for num, x, y, w, h in pads:
        rratio = r if r is not None else round(0.25, 6)
        S.append('\t(pad "%s" smd roundrect\n\t\t(at %s %s)\n\t\t(size %s %s)\n'
                 '\t\t(layers "F.Cu" "F.Mask" "F.Paste")\n'
                 '\t\t(roundrect_rratio %s)\n\t)\n'
                 % (num, kio.num(x), kio.num(y), kio.num(w), kio.num(h),
                    kio.num(rratio)))

    model = pkg.get("model")
    # 把 ${KICAD10_3DMODEL_DIR} 里的版本号换成**本机实际装的** KiCad 主版本，
    # 否则生成的封装拿到别的 KiCad 版本上 3D 显示不出来。
    if model:
        v = toolchain.kicad_major()
        if v:                    # 读不到版本号就保留原样，不要编一个不存在的变量名
            model = re.sub(r"\$\{KICAD\d+_3DMODEL_DIR\}",
                           "${KICAD%d_3DMODEL_DIR}" % v, model)
    M = ""
    if model:
        M = ('\t(model "%s"\n\t\t(offset\n\t\t\t(xyz 0 0 0)\n\t\t)\n'
             '\t\t(scale\n\t\t\t(xyz 1 1 1)\n\t\t)\n\t\t(rotate\n'
             '\t\t\t(xyz 0 0 0)\n\t\t)\n\t)\n' % model)

    return ('(footprint "%s"\n\t(version %s)\n\t(generator "kicad-footprint-generator")\n'
            '\t(layer "F.Cu")\n\t(descr "%s")\n\t(tags "%s")\n'
            '\t(property "Reference" "%s"\n\t\t(at 0 %s 0)\n\t\t(layer "F.SilkS")\n'
            '\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1 1)\n'
            '\t\t\t\t(thickness 0.15)\n\t\t\t)\n\t\t)\n\t)\n'
            '\t(property "Value" "%s"\n\t\t(at 0 %s 0)\n\t\t(layer "F.Fab")\n'
            '\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1 1)\n'
            '\t\t\t\t(thickness 0.15)\n\t\t\t)\n\t\t)\n\t)\n'
            '\t(attr smd)\n\t(duplicate_pad_numbers_are_jumpers no)\n%s'
            '\t(embedded_fonts no)\n%s)\n'
            % (name, fp_version, pkg.get("descr", ""), pkg.get("tags", ""),
               "REF**", kio.num(-out_y), name, kio.num(out_y),
               "".join(S), M))


# ============================================================ 产出附带文件
def emit_groups(spec, lay):
    """分组 + **绘制规范**一起交给校验侧。

    带上 pitch/group_gap 是刻意的：校验侧的 --min-pitch/--group-gap 以前是独立
    默认值，改了 spec 的规范而不同步告诉校验侧，就会报一堆假 FAIL。
    （下划线开头的键会被 symbol_lint 当成元数据忽略。）
    """
    st = spec.get("symbol_style", {})
    gap = st.get("group_gap_mil", 400)
    g = {k: v for k, v in lay["groups"].items() if v}
    g["_style"] = {"pitch_mil": st.get("pitch_mil", 200),
                   "group_gap_mil": gap,
                   "grid_mil": 50}
    g["_说明"] = ("分组是【输入】。'组间 %g mil' 几何上不可判 —— "
                  "把 IN+ 挪一格就可能变成另一个同样合规的分组。"
                  % gap)
    return g


def emit_fp_spec(spec, pads):
    pkg = spec["package"]
    n = len(pads)
    xs = [p[1] for p in pads]
    rows = [
        {"symbol": "A", "requirement": pkg.get("height_req", "-"), "kind": "na",
         "note": "高度类尺寸，2D 文件无法测量"},
        {"symbol": "D", "requirement": "%s" % pkg["body"]["w"], "kind": "body_w",
         "nominal": pkg["body"]["w"], "note": "本体长度（要求值来自图纸）"},
        {"symbol": "E", "requirement": "%s" % pkg["body"]["h"], "kind": "body_h",
         "nominal": pkg["body"]["h"], "note": "本体宽度（要求值来自图纸）"},
        {"symbol": "e", "requirement": "%s" % pkg["pitch"], "kind": "lead_pitch",
         "nominal": pkg["pitch"]},
        {"symbol": "N", "requirement": "%d" % n, "kind": "lead_count", "nominal": n},
        {"symbol": "MD", "requirement": "%d" % pkg["pads_per_side"],
         "kind": "leads_per_side", "nominal": pkg["pads_per_side"]},
        {"symbol": "b", "requirement": "%s" % pkg["pad"]["width"],
         "kind": "lead_width", "nominal": pkg["pad"]["width"]},
        {"symbol": "L", "requirement": "%s" % pkg["pad"]["length"],
         "kind": "lead_length", "nominal": pkg["pad"]["length"]},
    ]
    if len(set(round(v, 3) for v in xs)) == 2:
        rows.append({"symbol": "列中心距",
                     "requirement": "%s" % pkg["row_span_x"], "kind": "pad_span_x",
                     "nominal": pkg["row_span_x"]})
    return {"title": "%s 封装尺寸测量表" % spec["symbol"],
            "component": spec.get("description", spec["symbol"]),
            "family": "peripheral", "output": "%s_report.xlsx" % spec["symbol"],
            "gerber_dir": "gerber", "rows": rows,
            "notes": ["要求值来自 part_spec.json（人工从图纸录入）；"
                      "实测值由校验 skill 反解析 Gerber 得到。",
                      "本文件由 make_part.py 生成，是**要求**而非实测结果。"]}


# ============================================================ main
def generate(spec_path, outdir):
    spec = json.load(open(spec_path, encoding="utf-8"))
    os.makedirs(outdir, exist_ok=True)
    fmts = kio.probe_formats()
    sym_v = fmts.get("sym_version", "20211014")
    fp_v = fmts.get("fp_version", "20211014")
    gen_v = fmts.get("generator_version", "10.0")

    lay = layout_symbol(spec)
    # 栅格自检：顶对齐时第一个引脚的 y = 最大跨度/2。若 pitch/group_gap 不是
    # 100 mil 的整数倍，半个跨度就可能落在 50 mil 网格之外 —— 引脚会连不上线，
    # 而画出来完全正常。这里显式提醒，别让用户从"ERC 报一堆 off_grid"倒推。
    warns = []
    grid = 50 * MIL               # 50 mil = 1.27 mm（KiCad 默认连接栅格）
    for q in lay["pins"]:
        for axis, v in (("x", q["x"]), ("y", q["y"])):
            if abs(v / grid - round(v / grid)) > 1e-6:
                warns.append("引脚 %s 的 %s=%.3f mm 不在 50 mil 栅格上"
                             % (q["number"], axis, v))
    if warns:
        st = spec.get("symbol_style", {})
        warns.append("原因通常是 symbol_style 的 pitch_mil(%s)/group_gap_mil(%s) "
                     "不是 100 的整数倍 —— 顶对齐后半个跨度会落在半格上。"
                     % (st.get("pitch_mil", 200), st.get("group_gap_mil", 400)))

    libdir = os.path.join(outdir, spec["library"])
    pretty = os.path.join(outdir, spec["library"] + ".pretty")
    os.makedirs(libdir, exist_ok=True)
    os.makedirs(pretty, exist_ok=True)

    sym_path = os.path.join(libdir, spec["library"] + ".kicad_sym")
    open(sym_path, "w", encoding="utf-8", newline="\n").write(
        emit_symbol(spec, lay, sym_v))

    pads = layout_pads(spec["package"])
    fp_path = os.path.join(pretty, spec["footprint"] + ".kicad_mod")
    open(fp_path, "w", encoding="utf-8", newline="\n").write(
        emit_footprint(spec, pads, fp_v, gen_v))

    grp = os.path.join(outdir, spec["library"] + ".groups.json")
    json.dump(emit_groups(spec, lay), open(grp, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    fsp = os.path.join(outdir, spec["library"] + ".fp.spec.json")
    json.dump(emit_fp_spec(spec, pads), open(fsp, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    return {"sym": sym_path, "fp": fp_path, "pretty": pretty,
            "groups": grp, "fp_spec": fsp, "layout": lay, "pads": pads,
            "spec": spec, "warnings": warns}


@guard
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--verify", action="store_true",
                    help="生成后跑 kicad-cli 确认能被加载")
    a = ap.parse_args()

    r = generate(a.spec, a.outdir)
    print("symbol  :", r["sym"])
    print("footprint:", r["fp"])
    print("groups  :", r["groups"])
    print("fp spec :", r["fp_spec"])
    x0, y0, x1, y1 = r["layout"]["body"]
    print("本体    : %.2f x %.2f mm" % (x1 - x0, y1 - y0))
    print("引脚    : %d" % len(r["layout"]["pins"]))
    for side in ("left", "right", "top", "bottom"):
        g = r["layout"]["groups"].get(side)
        if g:
            print("  %-6s 分组: %s" % (side, [" ".join(x) for x in g]))
    print("焊盘    : %d" % len(r["pads"]))
    for w in r["warnings"]:
        print("  [warn] %s" % w)

    if a.verify:
        print("\n--- 可加载性（只证明'能加载'，不证明'正确'）---")
        bad = False
        for kind, path in (("sym", r["sym"]), ("fp", r["pretty"])):
            ok, rc, msg = kio.loadable(path, kind)
            print("  %-4s %s  rc=%d  %s" % (kind, "OK " if ok else "BAD", rc, msg))
            bad = bad or not ok
        print("\n下一步（正确性由校验 skill 负责）:")
        print("  python <kicad-check-sch-component>/scripts/check_symbol.py %s \\" % r["sym"])
        print("         --outdir chk --groups %s --pdf <datasheet.pdf>" % r["groups"])
        print("  python <kicad-check-pcb-component>/scripts/check_footprint.py %s \\"
              % r["fp"])
        print("         --outdir chk2 --spec %s" % r["fp_spec"])
        return 2 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
