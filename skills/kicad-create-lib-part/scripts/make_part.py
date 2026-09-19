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
import math
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

# 四角净空用的常数在 shared/kitext.py —— 校验侧的 symbol_lint 也要用同一组，
# 各写一份迟早会漂移，然后检查就静默失效了。
from kitext import NAME_OFF, text_len, vert_half               # noqa: E402

CORNER_GAP = 0.25               # 两排名字在四角要留的净空


# ============================================================ 输入校验
SIDES = ("left", "right", "top", "bottom")


def validate(spec):
    """在算任何几何之前，先把 spec 里的值域查一遍。

    为什么必需：缺字段已经被 KeyError 拦住了，但**值写错**以前全部静默通过，
    而且产生的是“看着正常”的错误产物。实测过三个：

        side = "middle"          -> 该引脚既不属于任何边，被**静默丢掉**（49->48），
                                    而工具照常打印“引脚 : 48”
        package.col_pitch = 0    -> 49 个焊盘全叠到 x=0，工具打印“焊盘 : 49”
        groups 里写不存在的组名   -> groups.json 里那一项变 null，无任何提示

    生成器一旦交出错误产物，下游要么崩溃、要么量出一堆看着像模像样的数字。
    在这里拦比在下游猜便宜得多。
    """
    errs = []

    def need_num(where, v, positive=True):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            errs.append("%s = %r 不是数字" % (where, v))
        elif positive and not v > 0:
            errs.append("%s = %s 必须大于 0" % (where, v))
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    pins = spec.get("pins")
    if not isinstance(pins, list) or not pins:
        print("spec.pins 为空或不是数组 —— 没有引脚就生成不出元件。",
              file=sys.stderr)
        raise SystemExit(2)

    declared = spec.get("groups", {}) or {}
    for side in declared:
        if side not in SIDES:
            errs.append("groups 里有认不得的边 %r（只能是 %s）"
                        % (side, "/".join(SIDES)))

    seen_num, used = set(), set()
    for i, p in enumerate(pins):
        num = str(p.get("number", ""))
        if not num:
            errs.append("第 %d 个引脚没有 number" % i)
        elif num in seen_num:
            errs.append("引脚号 %r 重复" % num)
        seen_num.add(num)
        side = p.get("side")
        if side not in SIDES:
            # 这条最阴：不认识的边 -> 该引脚不会落入任何一侧 -> 凭空消失。
            errs.append("引脚 %s 的 side = %r 认不得（只能是 %s）—— "
                        "它会被静默丢掉，而不是报错"
                        % (num or i, side, "/".join(SIDES)))
            continue
        used.add((side, p.get("group")))
        if declared.get(side) and p.get("group") not in declared[side]:
            errs.append("引脚 %s 的 group=%r 不在 groups[%s] 里：%s"
                        % (num, p.get("group"), side, declared[side]))

    # 声明了却没有任何引脚的组 -> 画出来是个空组
    for side, gs in declared.items():
        if side not in SIDES:
            continue
        for g in (gs or []):
            if (side, g) not in used:
                errs.append("groups[%s] 里的 %r 没有任何引脚" % (side, g))

    pkg = spec.get("package") or {}
    for k in ("pitch", "col_pitch"):
        if k in pkg:
            need_num("package.%s" % k, pkg[k])
    for k in ("array_w", "array_h"):
        if k in pkg:
            need_num("package.%s" % k, pkg[k])
    if "body" in pkg:
        for k in ("w", "h"):
            if k in pkg["body"]:
                need_num("package.body.%s" % k, pkg["body"][k])
    if pkg.get("family") != "grid_array" and "row_span_x" in pkg:
        need_num("package.row_span_x", pkg["row_span_x"])

    for i, p in enumerate(pins):
        r = p.get("reason")
        if r is not None and not isinstance(r, str):
            errs.append("引脚 %s 的 reason 不是字符串" % p.get("number", i))
    j = spec.get("judgments")
    if j is not None and not isinstance(j, dict):
        errs.append("judgments 应当是对象（含 etype_rules / group_rules）")

    st = spec.get("symbol_style", {})
    for k in ("pitch_mil", "group_gap_mil", "pin_length_mil"):
        if k in st:
            need_num("symbol_style.%s" % k, st[k])

    if errs:
        print("spec 有 %d 处值不对：\n%s\n"
              % (len(errs), "\n".join("  - " + e for e in errs)), file=sys.stderr)
        print("这些值写错时，以前的后果是**静默产出错误产物**，"
              "而不是报错。对照 assets/part_spec_template.json 改。", file=sys.stderr)
        raise SystemExit(2)


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

    # 上/下边的引脚是沿 x 铺开的，本体宽度必须容得下它们。
    # 踩过的坑：以前直接用 body_half_width_mil 定宽，56 脚的 QFN 上
    # 顶/底各 14 脚铺开 ±33mm，而本体只有 ±7.62mm —— 引脚悬在本体外 17.8mm，
    # 而当时 symbol_lint 的 body_margin 只查 y 不查 x，一声不响。
    def tb_span(side):
        n = len([p for p in pins if p.get("side") == side])
        return 0.0 if n <= 1 else (n - 1) * pitch

    tb_need = max(tb_span("top"), tb_span("bottom")) / 2.0
    if tb_need:
        hw = max(hw, tb_need + topm)

    # 四角不能压字。上/下排的引脚名是**竖着**写的，从本体上/下边沿往里伸；
    # 左/右排的引脚名横着写，从左/右边沿往里伸 —— 四角就是这两排抢的地方。
    # 上面那条只保证“引脚塞得进本体宽度”，完全没管名字占多宽，于是本体够宽、
    # 引脚都在里面，名字却叠在一起（ESP32-S3 和 CY8C6245 都中过）。
    # 判据：左边名字的右端  <  最左那个上/下排名字的左端。
    #   -hw + NAME_OFF + w_side + CORNER_GAP < -tb_need - VERT_HALF
    if tb_need:
        font = st.get("font", 1.27)
        w_side = max([text_len(plain(p["name"]), font) for p in pins
                      if p.get("side") in ("left", "right")] or [0.0])
        hw = max(hw, NAME_OFF + w_side + CORNER_GAP + tb_need + vert_half(font))

    # 本体半宽必须落在栅格上。引脚根部 x = ±(hw + plen)，hw 只要取了非整格
    # 的值，**整排引脚**就一起掉到栅格外 —— 画出来完全正常，却一根线也连不上。
    # 上面几条取 max 的约束都可能算出非整格的值，所以最后统一往上取整。
    # （CY8C6245 第一次加四角净空时算到 17.22mm，38 个引脚全掉出栅格。）
    _grid = 50 * MIL
    hw = math.ceil(hw / _grid - 1e-9) * _grid

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

    # 位号 / Value 要避开引脚，而不只是避开本体。
    # 踩过的坑：原来写在 by1 + font（本体上沿之上 1.27mm），双排封装没上/下引脚
    # 所以没事；四边封装的上排引脚恰好占着那段（本体沿 35.56 → 引脚末 38.1），
    # 于是 ESP32-S3 的 Value 文字和 52/53 号引脚名叠在一起。
    _ys = [p["y"] for p in lay["pins"]] or [0.0]
    top = max(by1, max(_ys)) + font
    bot = min(by0, min(_ys)) - font
    prop("Reference", spec.get("reference", "U"), bx0, top)
    prop("Value", name, bx1, bot)
    prop("Footprint", "%s:%s" % (spec["library"], spec["footprint"]),
         bx0, bot - font, hide=True)
    prop("Datasheet", spec.get("datasheet", ""), bx0, bot - font * 2.0, hide=True)
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
BALL_RE = re.compile(r"^([A-Z]+)([0-9]+)$")


def layout_ball_grid(pkg, numbers):
    """球栅阵列（WLCSP / CSP / BGA / LGA）的焊盘位置。

    焊盘**不是算出来的，是从引脚号解出来的**：JEDEC 球名（"A11"、"C7"）本身
    就把行列编进去了。所以符号的引脚号和封装的焊盘号不可能对不上 —— 它们
    本来就是同一个字符串，不存在"两处各写一遍然后慢慢漂移"这种事。

        x = (列号 - col_zero) * col_pitch
        y = row_y[行字母]

    行间距给的是一整张表而不是一个数：交错阵列（SG-XFWLB-49）的行距是
    0.280 / 0.341 交替的，单个 row_pitch 表达不了。
    """
    letters, row_y = pkg["row_letters"], pkg["row_y"]
    if len(letters) != len(row_y):
        raise ValueError("row_letters 有 %d 个字母，row_y 有 %d 个数，对不上"
                         % (len(letters), len(row_y)))
    ry = dict(zip(letters, row_y))
    cp = float(pkg["col_pitch"])
    c0 = float(pkg.get("col_zero", 0))
    d = float(pkg.get("pad_dia", pkg.get("ball_dia", 0.25)))

    pads, bad = [], []
    for num in numbers:
        m = BALL_RE.match(str(num))
        if not m or m.group(1) not in ry:
            bad.append(str(num))
            continue
        pads.append((str(num), round((int(m.group(2)) - c0) * cp, 4),
                     float(ry[m.group(1)]), d, d))
    if bad:
        raise ValueError("这些引脚号不是合法球名（<行字母><列号>）：%s"
                         % "、".join(bad[:8]))
    return pads


def layout_pads(spec):
    pkg = spec["package"]
    if pkg.get("family") == "grid_array":
        return layout_ball_grid(pkg, [p["number"] for p in spec["pins"]])
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


def ep_geometry(pkg):
    """散热焊盘（QFN/QFP 的 EPAD）的几何。-> dict 或 None。

    为什么单独一个函数：EP 不在任何一条边上，也不是 roundrect 普通焊盘 ——
    它需要：
      * 铜箔矩形焊盘，带 pad_prop_heatsink + zone_connect 2（KiCad 惯例）
      * **不带 F.Paste**：钢网改用分块，否则整片开窗会让芯片浮起/空洞
      * n×m 块仅 F.Paste 的贴片，按 EP 尺寸分格推导

    分块尺寸的取法在 KiCad 官方 QFN-56 (4mm EP) 上校准过：
        pitch = size / n        ->  4/3 = 1.3333
        patch = pitch * 0.8     ->  1.0667  （官方写 1.07）
    即覆盖率 9*1.067^2/16 = 0.64，与官方一致。
    """
    ep = pkg.get("ep")
    if not ep:
        return None
    sx, sy = float(ep["size_x"]), float(ep["size_y"])
    out = {"number": str(ep.get("number", "57")), "x": 0.0, "y": 0.0,
           "w": sx, "h": sy, "patches": []}
    g = ep.get("paste")
    if g:
        nx, ny = int(g.get("n", 3)), int(g.get("m", g.get("n", 3)))
        ratio = float(g.get("patch_ratio", 0.8))
        pw, ph = sx / nx * ratio, sy / ny * ratio
        px, py = sx / nx, sy / ny
        for i in range(nx):
            for j in range(ny):
                out["patches"].append((round((i - (nx - 1) / 2.0) * px, 4),
                                       round((j - (ny - 1) / 2.0) * py, 4),
                                       round(pw, 4), round(ph, 4)))
    return out


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


def courtyard_cross(pads, body, margin=CRTYD):
    """外框 = max(本体, 焊盘外沿) + margin 的并集轮廓。

    不是矩形，是一个 12 段的“十字/凸字形”：中间一段按本体，两端按焊盘外沿。
    在官方 MSOP-10 与 SOIC-8 上逐段核对过，两边的 12 条线段完全对得上。

    踩过的坑：不能因为 PY < by 就把 PY 拉成 by —— PY < by 正是“两端比中间窄”
    这个形状本身。拉平了外框就退化成矩形，贴片机的避让区就不准了。
    """
    hx, hy = body["w"] / 2, body["h"] / 2
    bx, by = hx + margin, hy + margin
    PX = max((abs(x) + w / 2 for (_, x, y, w, h) in pads), default=hx) + margin
    PY = max((abs(y) + h / 2 for (_, x, y, w, h) in pads), default=hy) + margin
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
    grid = pkg.get("family") == "grid_array"
    r = pkg.get("pad", {}).get("roundrect_rratio")
    corner = pkg.get("pad1_corner", "top-left")
    # 球栅阵列的外框余量按 IPC-7351 标称取 1.0mm/边 —— 官方 package/grid_array
    # 生成器在 WLCSP-20/35/64 上量的都是本体 +1.00（球阵封装要留返修空间）。
    # 其余封装是 0.25。这个数不是拍的，是三个官方封装逐值对出来的。
    cmargin = float(pkg.get("courtyard_margin", 1.0 if grid else CRTYD))
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
    cx = courtyard_cross(pads, body, cmargin)
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

    cy = courtyard_cross(pads, body, cmargin)
    for x0, y0, x1, y1 in cy:
        line(x0, y0, x1, y1, "F.CrtYd", 0.05)
    # 装配层本体 + Pin1 倒角。
    # 官方 WLCSP 的倒角是 0.5 * min(本体半宽, 本体半高) —— WLCSP-20/35/64 三个
    # 都对上；peripheral 族才是固定的 0.75。
    ch = 0.5 * min(hx, hy) if grid else 0.75
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
        if grid:
            # 球阵焊盘是**圆**的，不是圆角矩形；pad_prop_bga 让 KiCad
            # 在 3D 与丝印检查里按器件而不是按 SMD 处理（官方 WLCSP 同款）。
            S.append('\t(pad "%s" smd circle\n\t\t(at %s %s)\n\t\t(size %s %s)\n'
                     '\t\t(property pad_prop_bga)\n'
                     '\t\t(layers "F.Cu" "F.Mask" "F.Paste")\n\t)\n'
                     % (num, kio.num(x), kio.num(y), kio.num(w), kio.num(h)))
            continue
        rratio = r if r is not None else round(0.25, 6)
        S.append('\t(pad "%s" smd roundrect\n\t\t(at %s %s)\n\t\t(size %s %s)\n'
                 '\t\t(layers "F.Cu" "F.Mask" "F.Paste")\n'
                 '\t\t(roundrect_rratio %s)\n\t)\n'
                 % (num, kio.num(x), kio.num(y), kio.num(w), kio.num(h),
                    kio.num(rratio)))

    # 散热焊盘：铜箔矩形，**不带 F.Paste**；钢网另外分块
    ep = ep_geometry(pkg)
    if ep:
        S.append('\t(pad "%s" smd rect\n\t\t(at %s %s)\n\t\t(size %s %s)\n'
                 '\t\t(property pad_prop_heatsink)\n'
                 '\t\t(layers "F.Cu" "F.Mask")\n\t\t(zone_connect 2)\n\t)\n'
                 % (ep["number"], kio.num(ep["x"]), kio.num(ep["y"]),
                    kio.num(ep["w"]), kio.num(ep["h"])))
        for px, py, pw, ph in ep["patches"]:
            S.append('\t(pad "" smd roundrect\n\t\t(at %s %s)\n\t\t(size %s %s)\n'
                     '\t\t(layers "F.Paste")\n\t\t(roundrect_rratio 0.233645)\n\t)\n'
                     % (kio.num(px), kio.num(py), kio.num(pw), kio.num(ph)))

    model = pkg.get("model")
    # 把 ${KICAD10_3DMODEL_DIR} 里的版本号换成**本机实际装的** KiCad 主版本，
    # 否则生成的封装拿到别的 KiCad 版本上 3D 显示不出来。
    if model:
        v = toolchain.kicad_major()
        if v:                    # 读不到版本号就保留原样，不要编一个不存在的变量名
            model = re.sub(r"\$\{KICAD\d+_3DMODEL_DIR\}",
                           "${KICAD%d_3DMODEL_DIR}" % v, model)
    # 阻焊开窗余量：球阵封装按球径取（官方 WLCSP 是 0.02 / 0.05，随球径变），
    # spec 里给多少写多少，没给就不写这个 token。
    mask = pkg.get("solder_mask_margin")
    M = ""
    if model:
        M = ('\t(model "%s"\n\t\t(offset\n\t\t\t(xyz 0 0 0)\n\t\t)\n'
             '\t\t(scale\n\t\t\t(xyz 1 1 1)\n\t\t)\n\t\t(rotate\n'
             '\t\t\t(xyz 0 0 0)\n\t\t)\n\t)\n' % model)

    return ('(footprint "%s"\n\t(version %s)\n\t(generator "kicad-footprint-generator")\n'
            '\t(layer "F.Cu")\n\t(descr "%s")\n\t(tags "%s")\n%s'
            '\t(property "Reference" "%s"\n\t\t(at 0 %s 0)\n\t\t(layer "F.SilkS")\n'
            '\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1 1)\n'
            '\t\t\t\t(thickness 0.15)\n\t\t\t)\n\t\t)\n\t)\n'
            '\t(property "Value" "%s"\n\t\t(at 0 %s 0)\n\t\t(layer "F.Fab")\n'
            '\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1 1)\n'
            '\t\t\t\t(thickness 0.15)\n\t\t\t)\n\t\t)\n\t)\n'
            '\t(attr smd)\n\t(duplicate_pad_numbers_are_jumpers no)\n%s'
            '\t(embedded_fonts no)\n%s)\n'
            % (name, fp_version, pkg.get("descr", ""), pkg.get("tags", ""),
               ("\t(solder_mask_margin %s)\n" % kio.num(mask)) if mask else "",
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


# 需要写依据的电气类型：这几个不是"看名字就知道"的，判错了图上完全看不出来，
# 而 ERC 只能验"符号内部自洽"，验不了"这个脚该不该是 passive"。
NEEDS_REASON = ("passive", "open_collector", "open_emitter", "unspecified",
                "tri_state", "power_out")


def emit_judgments(spec, lay):
    """把 AI/LLM 的判断依据写成一份可复核的表。

    为什么需要：`etype` 和 `groups` 手册里没有，是从上下文判的。以前 spec 里
    只留下**结果**（power_in / p2），没有任何"为什么" —— 三个月后要复核，
    只能把手册重读一遍再判一次，而且没法判断"当时判得对不对"。

    这份文件让判断可审计：规则（共用）写在 `spec.judgments` 里，
    例外逐条写在 `pins[].reason` 上，最后按引脚列成表。
    """
    j = spec.get("judgments", {}) or {}
    pins = lay["pins"]
    groups = lay["groups"]
    g_of = {}
    for side, gs in groups.items():
        for gi, names in enumerate(gs):
            for nm in names:
                g_of[nm] = "%s / 第 %d 组" % (side, gi + 1)

    L = ["# %s —— 符号判断依据" % spec["symbol"], ""]
    L.append("`etype`、`side`、`groups` 手册里都没有，是从手册上下文判的。")
    L.append("判错了**画出来一模一样**，只能由 ERC（类型）、契约（号↔盘）、目视（摆放）证伪。")
    L.append("所以把依据留在这里，供复核。")
    L.append("")
    L.append("来源：`%s`（`spec.judgments` 与 `pins[].reason`）" % spec.get("datasheet", "-"))
    L.append("")

    for title, key in (("电气类型规则", "etype_rules"), ("功能分组规则", "group_rules"),
                       ("摆放规则", "side_rules")):
        rules = j.get(key) or []
        if rules:
            L.append("## %s" % title)
            L.append("")
            for r in rules:
                L.append("* %s" % r)
            L.append("")

    lack = [p for p in pins if p.get("etype") in NEEDS_REASON and not p.get("reason")]
    L.append("## 逐引脚")
    L.append("")
    L.append("| 球号 | 名称 | 电气类型 | 分组 | 依据 |")
    L.append("|---|---|---|---|---|")
    for p in sorted(pins, key=lambda q: (len(q["number"]), q["number"])):
        r = p.get("reason") or ("规则见上" if p.get("etype") not in NEEDS_REASON
                                else "**（未写，需补）**")
        L.append("| %s | %s | `%s` | %s | %s |"
                 % (p["number"], plain(p["name"]), p["etype"],
                    g_of.get(plain(p["name"]), p.get("side", "-")), r))
    L.append("")
    if lack:
        L.append("## 待补依据")
        L.append("")
        L.append("下面这些引脚的电气类型不是\"看名字就知道\"的，但 spec 里没写 `reason`：")
        L.append("")
        for p in lack:
            L.append("* `%s` %s -> `%s`" % (p["number"], plain(p["name"]), p["etype"]))
        L.append("")
        L.append("（`%s` 这类类型靠 ERC 验不出来，只能靠人看依据。）"
                 % " / ".join("`%s`" % x for x in NEEDS_REASON))
        L.append("")
    return "\n".join(L)


def emit_fp_spec_grid(spec, pads):
    """球栅阵列的尺寸表，走 JEDEC 那一套符号。

    与 peripheral 族（e / b / L / D / E）是两套完全不同的量：球阵没有“引脚长度”，
    却多出阵列跨距 D1/E1、矩阵位数 MD/ME、球栅距 eD、指定两排间距 eE?s、
    斜向球距 eS?、基准偏移 SD/SE。kind 由校验侧的 grid_array family 消费。
    """
    pkg = spec["package"]
    body = pkg["body"]
    n = len(pads)
    rows = [
        {"symbol": "A", "requirement": pkg.get("height_req", "-"), "kind": "na",
         "note": "高度类尺寸（A / A1），2D 文件无法测量"},
        {"symbol": "D", "requirement": "%s" % body["w"], "kind": "body_w",
         "nominal": body["w"], "note": "本体长度（要求值来自图纸）"},
        {"symbol": "E", "requirement": "%s" % body["h"], "kind": "body_h",
         "nominal": body["h"], "note": "本体宽度（要求值来自图纸）"},
        {"symbol": "D1", "requirement": "%s" % pkg["array_w"],
         "kind": "array_w", "nominal": float(pkg["array_w"]),
         "note": "球阵列 D 向跨距（最外两排球心距）"},
        {"symbol": "E1", "requirement": "%s" % pkg["array_h"],
         "kind": "array_h", "nominal": float(pkg["array_h"]),
         "note": "球阵列 E 向跨距"},
        {"symbol": "MD", "requirement": "%d" % pkg["matrix_cols"],
         "kind": "matrix_cols", "nominal": int(pkg["matrix_cols"]),
         "note": "D 向矩阵位数（含 A1 空位，不是球数）"},
        {"symbol": "ME", "requirement": "%d" % pkg["matrix_rows"],
         "kind": "matrix_rows", "nominal": int(pkg["matrix_rows"])},
        {"symbol": "N", "requirement": "%d" % n, "kind": "count",
         "nominal": n},
        {"symbol": "\u00d8b",
         "requirement": pkg.get("ball_req", "%s" % pkg["pad_dia"]),
         "kind": "dia", "nominal": float(pkg["pad_dia"]),
         "note": "焊球直径（实测的是焊盘/钢网圆形孔径）"},
        {"symbol": "eD", "requirement": "%s" % pkg["pitch"], "kind": "pitch",
         "nominal": float(pkg["pitch"]), "note": "球栅距（同排相邻球心距）"},
    ]
    for r in pkg.get("row_spans", []):
        rows.append({"symbol": r["symbol"], "requirement": r["requirement"],
                     "kind": "row_span", "nominal": float(r["nominal"]),
                     "from": r["from"], "to": r["to"],
                     "note": "%s->%s 两排球心距" % (r["from"], r["to"])})
    rows += [
        {"symbol": "eS1", "requirement": "%s" % pkg["diag_min"],
         "kind": "diag_min", "nominal": float(pkg["diag_min"]),
         "note": "斜向最近球间距（外侧排）"},
        {"symbol": "eS2", "requirement": "%s" % pkg["diag_max"],
         "kind": "diag_max", "nominal": float(pkg["diag_max"]),
         "note": "斜向最近球间距（中间排）"},
        {"symbol": "SD", "requirement": "%s" % pkg["sd"], "kind": "sd",
         "nominal": float(pkg["sd"]),
         "note": "外排中心球到基准 B 的偏移"},
        {"symbol": "SE", "requirement": "%s" % pkg["se"], "kind": "se",
         "nominal": float(pkg["se"]), "note": "阵列中心到基准 A 的偏移"},
    ]
    return {"title": "%s 封装尺寸测量表" % spec["symbol"],
            "component": spec.get("description", spec["symbol"]),
            "family": "grid_array", "output": "%s_report.xlsx" % spec["symbol"],
            "gerber_dir": "gerber", "rows": rows,
            "notes": ["要求值来自 part_spec.json（人工从图纸录入）；"
                      "实测值由校验 skill 反解析 Gerber 得到。",
                      "本文件由 make_part.py 生成，是**要求**而非实测结果。",
                      "A1 空位：图纸注 8 的 + 标记，不占球但占矩阵位。"]}


def emit_fp_spec(spec, pads):
    pkg = spec["package"]
    if pkg.get("family") == "grid_array":
        return emit_fp_spec_grid(spec, pads)
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
    ep = pkg.get("ep")
    if ep:                              # 散热焊盘也要进尺寸表
        rows.append({"symbol": "D2", "requirement": "%s" % ep["size_x"],
                     "kind": "ep_x", "nominal": float(ep["size_x"]),
                     "note": "散热焊盘宽（None 焊盘）"})
        rows.append({"symbol": "E2", "requirement": "%s" % ep["size_y"],
                     "kind": "ep_y", "nominal": float(ep["size_y"]),
                     "note": "散热焊盘高（None 焊盘）"})
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
    validate(spec)
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

    # house style 是 200 / 400 **固定的**，不是每颗器件的参数。
    # 不是审美问题：同一个库里混着 100 mil 和 200 mil 的符号，读图的人每换
    # 一颗器件都要重新建立比例感。官方库用 100 mil 是官方的风格，不是我们的。
    # 这里不报错（留个口子给引脚真的多到摆不下的器件），但要吵一声。
    _st = spec.get("symbol_style", {})
    _pm, _gg = _st.get("pitch_mil", 200), _st.get("group_gap_mil", 400)
    if (_pm, _gg) != (200, 400):
        warns.append("symbol_style 是 %s/%s mil —— house style 固定 200/400。"
                     "全库统一比单个符号紧凑重要；真嫌大就重排 groups{} 把两侧配平，"
                     "不要改 pitch。" % (_pm, _gg))

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

    pads = layout_pads(spec)
    fp_path = os.path.join(pretty, spec["footprint"] + ".kicad_mod")
    open(fp_path, "w", encoding="utf-8", newline="\n").write(
        emit_footprint(spec, pads, fp_v, gen_v))

    grp = os.path.join(outdir, spec["library"] + ".groups.json")
    json.dump(emit_groups(spec, lay), open(grp, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    fsp = os.path.join(outdir, spec["library"] + ".fp.spec.json")
    json.dump(emit_fp_spec(spec, pads), open(fsp, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    # 判断依据跟着产物走 —— 校验侧会把它带进证据包，复核时不必重读手册。
    jdg = os.path.join(outdir, spec["library"] + ".judgments.md")
    with open(jdg, "w", encoding="utf-8", newline="\n") as f:
        f.write(emit_judgments(spec, lay))

    return {"sym": sym_path, "fp": fp_path, "pretty": pretty,
            "groups": grp, "fp_spec": fsp, "judgments": jdg,
            "layout": lay, "pads": pads, "spec": spec, "warnings": warns}


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
    _ep = ep_geometry(r["spec"]["package"])
    print("焊盘    : %d%s" % (len(r["pads"]),
                             " + 散热焊盘 %s" % _ep["number"] if _ep else ""))
    if _ep and _ep["patches"]:
        print("          散热焊盘 %.2fx%.2f, 钢网 %d 块"
              % (_ep["w"], _ep["h"], len(_ep["patches"])))
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
        # 3 = 产物生成出来了但 kicad-cli 加载不了（区别于 2 = 输入/参数错）
        return 3 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
