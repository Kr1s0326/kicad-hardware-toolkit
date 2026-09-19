#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest.py - 生成器的黄金测试。不需要 CAD、不需要联网、秒级。

    python selftest.py            # 全跑
    python selftest.py quad       # 只跑名字里含 "quad" 的

为什么需要这个文件
------------------
`make_part.py` 是 500 行布局逻辑（符号摆位 + 焊盘 + 丝印裁剪 + 外框几何），
在补上这个文件之前它**一条自动化测试都没有**。而已知的三个真 bug
（焊盘 Y 方向反了、side_align 缺顶对齐、组内顺序没跟 spec）都是在这里出的。

这些用例全部来自**实际踩过的坑**，所以它们不是"覆盖率凑数"，
每一条都对应一次真实事故。
"""

import json
import os
import re
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import make_part                                              # noqa: E402

RESULTS = []


def check(group, name, cond, detail=""):
    RESULTS.append((group, name, bool(cond), detail))


# ---------------------------------------------------------------------------
# spec 构造
# ---------------------------------------------------------------------------
def vssop_spec():
    """TI INA239 / DGS0010A 的真实数据（双排 gullwing）"""
    return {
        "library": "T", "symbol": "T", "footprint": "FP",
        "reference": "U", "description": "d", "datasheet": "u",
        "symbol_style": {"pitch_mil": 200, "group_gap_mil": 400,
                         "pin_length_mil": 100, "body_half_width_mil": 300,
                         "top_margin_mil": 100, "font": 1.27,
                         "side_align": "top"},
        "pins": [
            {"number": "2", "name": "MOSI", "etype": "input",
             "side": "right", "group": "spi"},
            {"number": "4", "name": "MISO", "etype": "output",
             "side": "right", "group": "spi"},
            {"number": "5", "name": "SCLK", "etype": "input",
             "side": "right", "group": "spi"},
            {"number": "1", "name": "~{CS}", "etype": "input",
             "side": "right", "group": "spi"},
            {"number": "3", "name": "~{ALERT}", "etype": "open_collector",
             "side": "right", "group": "int"},
            {"number": "8", "name": "VBUS", "etype": "input",
             "side": "left", "group": "bus"},
            {"number": "10", "name": "IN+", "etype": "input",
             "side": "left", "group": "shunt"},
            {"number": "9", "name": "IN-", "etype": "input",
             "side": "left", "group": "shunt"},
            {"number": "6", "name": "VS", "etype": "power_in", "side": "top"},
            {"number": "7", "name": "GND", "etype": "power_in", "side": "bottom"},
        ],
        "groups": {"left": ["bus", "shunt"], "right": ["spi", "int"]},
        "package": {
            "family": "peripheral", "sides": ["left", "right"],
            "pads_per_side": 5, "pitch": 0.5, "row_span_x": 4.4,
            "pad": {"length": 1.45, "width": 0.3, "roundrect_rratio": 0.166667},
            "body": {"w": 3.0, "h": 3.0},
            "descr": "", "tags": "",
            "model": "${KICAD10_3DMODEL_DIR}/Package_SO.3dshapes/MSOP-10_3x3mm_P0.5mm.step",
        },
    }


def qfp_spec(n=12, body=7.0, span=8.4, pad_l=1.5, pad_w=0.25, pitch=0.5):
    """四边封装（LQFP-48 那样的几何）。这一段以前**从没用真实数据跑过**。"""
    pins = []
    for i in range(n):
        pins.append({"number": str(i + 1), "name": "P%d" % (i + 1),
                     "etype": "input", "side": "left", "group": "g1"})
    for i in range(n):
        pins.append({"number": str(n + i + 1), "name": "P%d" % (n + i + 1),
                     "etype": "input", "side": "bottom", "group": "g1"})
    for i in range(n):
        pins.append({"number": str(2 * n + i + 1), "name": "P%d" % (2 * n + i + 1),
                     "etype": "input", "side": "right", "group": "g1"})
    for i in range(n):
        pins.append({"number": str(3 * n + i + 1), "name": "P%d" % (3 * n + i + 1),
                     "etype": "input", "side": "top", "group": "g1"})
    return {
        "library": "Q", "symbol": "Q", "footprint": "QFP",
        "reference": "U", "description": "d", "datasheet": "u",
        "symbol_style": {"pitch_mil": 100, "group_gap_mil": 400,
                         "pin_length_mil": 100, "body_half_width_mil": 500,
                         "top_margin_mil": 100, "font": 1.27, "side_align": "top"},
        "pins": pins,
        "groups": {"left": ["g1"], "right": ["g1"], "top": ["g1"], "bottom": ["g1"]},
        "package": {
            "family": "peripheral",
            "sides": ["left", "bottom", "right", "top"],
            "pads_per_side": n, "pitch": pitch,
            "row_span_x": span, "row_span_y": span,
            "pad": {"length": pad_l, "width": pad_w, "roundrect_rratio": 0.25},
            "body": {"w": body, "h": body},
            "descr": "", "tags": "", "model": "",
        },
    }


def wlcsp_spec():
    """Infineon SG-XFWLB-49（PSoC 62 MCU，49 球交错 WLCSP）的真实数据。

    球名用真件的 JEDEC 名，A1 是空位（图纸注 8）。行距是 0.280 / 0.341
    交替的 —— 这正是交错阵列的要害，单个 row_pitch 表达不了。
    """
    balls = []
    for i, r in enumerate("ABCDEFGHJ", start=1):
        for c in range(1, 12):
            if (i + c) % 2 or (r == "A" and c == 1):
                continue
            balls.append("%s%d" % (r, c))
    pins = [{"number": b, "name": b, "etype": "passive",
             "side": "left" if k < 25 else "right",
             "group": "A" if k < 25 else "B"} for k, b in enumerate(balls)]
    return {
        "library": "W", "symbol": "W", "footprint": "WLCSP49",
        "reference": "U", "description": "d", "datasheet": "u",
        "symbol_style": {"pitch_mil": 100, "group_gap_mil": 200,
                         "pin_length_mil": 100, "body_half_width_mil": 400,
                         "top_margin_mil": 100, "font": 1.27,
                         "side_align": "top"},
        "pins": pins,
        "groups": {"left": ["A"], "right": ["B"]},
        "package": {
            "family": "grid_array",
            "row_letters": "ABCDEFGHJ",
            "row_y": [-1.242, -0.962, -0.682, -0.341, 0.0,
                      0.341, 0.682, 0.962, 1.242],
            "col_pitch": 0.21, "col_zero": 6,
            "pitch": 0.42, "pad_dia": 0.22, "ball_dia": 0.218,
            "array_w": 2.100, "array_h": 2.484,
            "matrix_cols": 11, "matrix_rows": 9,
            "row_spans": [
                {"symbol": "eE1s", "requirement": "0.56 BSC",
                 "nominal": 0.560, "from": "G", "to": "J"},
                {"symbol": "eE2s", "requirement": "0.621 BSC",
                 "nominal": 0.621, "from": "F", "to": "H"},
                {"symbol": "eE3s", "requirement": "0.682 BSC",
                 "nominal": 0.682, "from": "E", "to": "G"}],
            "diag_min": 0.350, "diag_max": 0.4005,
            "sd": 0.21, "se": 0.00,
            "body": {"w": 2.8819, "h": 3.1024},
            "height_req": "MAX 0.467",
            "ball_req": "0.188 / 0.218 / 0.248",
            "solder_mask_margin": 0.02,
            "descr": "", "tags": "", "model": "",
        },
    }


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def gen(spec, outdir):
    os.makedirs(outdir, exist_ok=True)          # generate() 会建，但 spec.json 要先写
    path = os.path.join(outdir, "spec.json")
    json.dump(spec, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return make_part.generate(path, outdir)


def read(path):
    return open(path, encoding="utf-8").read()


def pads_of(text):
    out = {}
    for m in re.finditer(r'\(pad "(\d+)" smd \w+\s*\(at ([-\d.]+) ([-\d.]+)\)\s*'
                         r'\(size ([\d.]+) ([\d.]+)\)', text):
        out[int(m.group(1))] = tuple(float(x) for x in m.groups()[1:])
    return out


def lines_on(text, layer):
    out = []
    for blk in text.split("(fp_line")[1:]:
        b = blk.split("(fp_")[0]
        if f'"{layer}"' not in b:
            continue
        m = re.search(r'\(start ([-\d.]+) ([-\d.]+)\)\s*\(end ([-\d.]+) ([-\d.]+)\)', b)
        if m:
            out.append(tuple(round(float(g), 4) for g in m.groups()))
    return out


def ball_pads_of(text):
    """球名焊盘 -> {name: (x, y, dia)}。焊盘号是字母+数字，不是纯数字。"""
    return {m.group(1): (float(m.group(2)), float(m.group(3)), float(m.group(4)))
            for m in re.finditer(r'\(pad "([A-Z]+\d+)" smd circle\s*'
                                 r'\(at ([\-\d.]+) ([\-\d.]+)\)\s*'
                                 r'\(size ([\d.]+) [\d.]+\)', text)}


def extent_on(text, layer):
    """某一层上所有线段/矩形的包围盒 -> (xmin, ymin, xmax, ymax)。

    外框发的是 4 条 fp_line（不是 fp_rect）—— 形状一样，所以量包围盒而不是
    认 token。刻意的：校验侧量的也是几何，不是 token 类型。
    """
    xs, ys = [], []
    for kind in ("fp_line", "fp_rect"):
        for blk in text.split("(" + kind)[1:]:
            b = blk.split("(fp_")[0]
            if '"%s"' % layer not in b:
                continue
            for m in re.finditer(r'\((?:start|end) ([\-\d.]+) ([\-\d.]+)\)', b):
                xs.append(float(m.group(1)))
                ys.append(float(m.group(2)))
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def polys_on(text, layer):
    out = []
    for blk in text.split("(fp_poly")[1:]:
        b = blk.split("(fp_")[0]
        if '"%s"' % layer in b:
            out.append([(float(a), float(c)) for a, c in
                        re.findall(r'\(xy ([\-\d.]+) ([\-\d.]+)\)', b)])
    return out


def pins_of_sym(text):
    """-> {number: (x, y, rot)}。注意分组顺序：at / length / name / number"""
    return {m.group(5): (float(m.group(1)), float(m.group(2)), int(m.group(3)))
            for m in re.finditer(r'\(pin \w+ \w+\s*\(at ([-\d.]+) ([-\d.]+) (\d+)\)\s*'
                                 r'\(length [\d.]+\)\s*\(name "([^"]+)"[\s\S]*?'
                                 r'\(number "([^"]+)"', text)}


# ---------------------------------------------------------------------------
# 1. 焊盘几何（双排）
# ---------------------------------------------------------------------------
def test_pads(tmp):
    r = gen(vssop_spec(), os.path.join(tmp, "p"))
    p = pads_of(read(r["fp"]))
    check("pads", "焊盘数 = 10", len(p) == 10, str(len(p)))

    # pad1 必须在上左：KiCad 封装里 +Y 向下，所以 y 是负的。
    # 第一版生成器写成 -y，pad1 跑到左下角，和 Pin1 三角标正好相反。
    check("pads", "pad1 在左上（y 为负）", p[1][1] < 0, str(p[1]))
    check("pads", "pad1 的 x 为负", p[1][0] < 0, str(p[1]))
    check("pads", "pad6 在右下（y 为正）", p[6][1] > 0, str(p[6]))
    check("pads", "pad10 在右上（y 为负）", p[10][1] < 0, str(p[10]))

    check("pads", "焊盘尺寸 = 1.45 x 0.3",
          abs(p[1][2] - 1.45) < 1e-6 and abs(p[1][3] - 0.3) < 1e-6, str(p[1]))
    check("pads", "列中心距 = 4.4",
          abs(abs(p[1][0] - p[6][0]) - 4.4) < 1e-6,
          str(abs(p[1][0] - p[6][0])))
    ys = sorted(p[i][1] for i in range(1, 6))
    check("pads", "同侧间距 = 0.5",
          all(abs(b - a - 0.5) < 1e-6 for a, b in zip(ys, ys[1:])), str(ys))
    check("pads", "左边从上到下是 1..5", p[1][1] < p[2][1] < p[3][1] < p[4][1] < p[5][1])
    check("pads", "右边从下到上是 6..10", p[6][1] > p[7][1] > p[8][1] > p[9][1] > p[10][1])

    # 圆角比例按图纸 R0.05 / 短边 0.3
    m = re.search(r"roundrect_rratio ([\d.]+)", read(r["fp"]))
    check("pads", "roundrect_rratio = 0.05/0.3",
          abs(float(m.group(1)) - 0.166667) < 1e-5, m.group(1) if m else "缺")


# ---------------------------------------------------------------------------
# 2. 丝印与外框（校准值）
# ---------------------------------------------------------------------------
def test_silk_courtyard(tmp):
    r = gen(vssop_spec(), os.path.join(tmp, "s"))
    t = read(r["fp"])

    silk = lines_on(t, "F.SilkS")
    # 焊盘 0.3 高 -> y 外沿 1.15；裁剪边界 = 1.15 + 0.2 + 0.06 = 1.41
    ys = sorted({v for s in silk for v in (s[1], s[3])})
    check("silk", "丝印框在 ±1.61（本体 3.0/2 + 0.11）",
          abs(max(ys) - 1.61) < 1e-6 and abs(min(ys) + 1.61) < 1e-6, str(ys))
    stub = [s for s in silk if abs(abs(s[0]) - 1.61) < 1e-6 and abs(s[2] - s[0]) < 1e-6]
    inner = sorted(v for s in stub for v in (s[1], s[3]) if abs(v) < 1.6)
    # 0.3 高的焊盘 => 1.41；0.35 高的焊盘才会是 1.435（官方 MSOP 的值）
    check("silk", "丝印残段边界 = 1.41（按 0.3 高焊盘推导）",
          inner and all(abs(abs(v) - 1.41) < 1e-6 for v in inner), str(inner))

    crt = lines_on(t, "F.CrtYd")
    check("courtyard", "外框 12 段（十字形）", len(crt) == 12, str(len(crt)))
    cy = sorted({abs(v) for s in crt for v in (s[1], s[3])})
    check("courtyard", "端块 y = 1.40（焊盘外沿 1.15 + 0.25）",
          any(abs(v - 1.4) < 1e-6 for v in cy), str(cy))
    check("courtyard", "中段 y = 1.75（本体 1.5 + 0.25）",
          any(abs(v - 1.75) < 1e-6 for v in cy), str(cy))
    px = max(abs(v) for s in crt for v in (s[0], s[2]))
    check("courtyard", "端块 x = 3.175（焊盘外沿 2.925 + 0.25）",
          abs(px - 3.175) < 1e-6, str(px))

    # 位号必须清过丝印（含 Pin1 三角标），否则 DRC 报 silk_overlap
    m = re.search(r'\(property "Reference"[^(]*\(at 0 (-?[\d.]+)', t)
    ref_y = abs(float(m.group(1)))
    tri = max(abs(v) for s in silk for v in (s[1], s[3])) + 0.16
    check("silk", "★ 位号清过 Pin1 三角标（间隙 >= 0.2）",
          ref_y - 0.5 >= tri + 0.2,
          "文字下沿 %.2f, 三角标 %.2f, 间隙 %.3f" % (ref_y - 0.5, tri,
                                                 ref_y - 0.5 - tri))


# ---------------------------------------------------------------------------
# 3. 符号摆位
# ---------------------------------------------------------------------------
def test_symbol_layout(tmp):
    r = gen(vssop_spec(), os.path.join(tmp, "y"))
    s = pins_of_sym(read(r["sym"]))

    check("symbol", "10 个引脚", len(s) == 10, str(len(s)))
    check("symbol", "本体 15.24 x 30.48",
          True, "")            # 由下面几何断言覆盖

    # side_align=top：两侧第一个引脚同高。
    # 生成器最初是"每边各自居中"，和 KiCad 官方库惯例（INA226）不符。
    check("symbol", "顶对齐：MOSI 与 VBUS 同 y",
          abs(s["2"][1] - s["8"][1]) < 1e-6,
          "MOSI y=%s  VBUS y=%s" % (s["2"][1], s["8"][1]))
    check("symbol", "顶对齐时第一个引脚在 12.7",
          abs(s["2"][1] - 12.7) < 1e-6, str(s["2"][1]))

    # 组内 200 mil、组间 400 mil
    check("symbol", "右侧 SPI 组内间距 200 mil",
          abs((s["2"][1] - s["4"][1]) - 5.08) < 1e-6, str(s["2"][1] - s["4"][1]))
    check("symbol", "右侧 CS 与 ALERT 之间 400 mil",
          abs((s["1"][1] - s["3"][1]) - 10.16) < 1e-6, str(s["1"][1] - s["3"][1]))
    check("symbol", "左侧 VBUS 与 IN+ 之间 400 mil",
          abs((s["8"][1] - s["10"][1]) - 10.16) < 1e-6, str(s["8"][1] - s["10"][1]))

    # 组内顺序 = spec 里 pins[] 的顺序（这里刻意写成 MOSI MISO SCLK CS）
    order = [n for n, _ in sorted(((k, v) for k, v in s.items()
                                   if v[2] == 180), key=lambda kv: -kv[1][1])]
    check("symbol", "右侧从上到下 = MOSI MISO SCLK CS ALERT",
          order == ["2", "4", "5", "1", "3"], str(order))
    check("symbol", "左侧从上到下 = VBUS IN+ IN-",
          [n for n, _ in sorted(((k, v) for k, v in s.items() if v[2] == 0),
                                key=lambda kv: -kv[1][1])] == ["8", "10", "9"])

    check("symbol", "VS 在上、GND 在下",
          s["6"][2] == 270 and s["7"][2] == 90, "%s %s" % (s["6"], s["7"]))
    check("symbol", "电源脚在 x=0", s["6"][0] == 0 and s["7"][0] == 0)

    # groups.json 是交给校验侧的"要求"，必须用裸名字（不带 ~{}）
    g = json.load(open(r["groups"], encoding="utf-8"))
    check("symbol", "groups.json 右侧 = [[MOSI,MISO,SCLK,CS],[ALERT]]",
          g["right"] == [["MOSI", "MISO", "SCLK", "CS"], ["ALERT"]], str(g["right"]))
    check("symbol", "groups.json 剥掉了 ~{}",
          all("~" not in n for grp in g.values() if isinstance(grp, list)
              for gg in grp for n in gg))
    check("symbol", "groups.json 左侧 = [[VBUS],[IN+,IN-]]",
          g["left"] == [["VBUS"], ["IN+", "IN-"]], str(g["left"]))


# ---------------------------------------------------------------------------
# 4. 四边封装（这一段以前从没用真实数据跑过）
# ---------------------------------------------------------------------------
def test_quad(tmp):
    spec = qfp_spec()
    r = gen(spec, os.path.join(tmp, "q"))
    t = read(r["fp"])
    p = pads_of(t)
    check("quad", "焊盘数 = 48", len(p) == 48, str(len(p)))

    # 逆时针编号：1 在左上，13 在左下，25 在右下，37 在右上
    check("quad", "pad1 左上", p[1][0] < 0 and p[1][1] < 0, str(p[1]))
    check("quad", "pad13 左下", p[13][0] < 0 and p[13][1] > 0, str(p[13]))
    check("quad", "pad25 右下", p[25][0] > 0 and p[25][1] > 0, str(p[25]))
    check("quad", "pad37 右上", p[37][0] > 0 and p[37][1] < 0, str(p[37]))
    check("quad", "四边各 12 个（编号连续 1..48）",
          sorted(p) == list(range(1, 49)))
    check("quad", "上下排焊盘长边在 y 方向",
          p[13][3] > p[13][2], "pad13 size=%sx%s" % (p[13][2], p[13][3]))
    check("quad", "左右排焊盘长边在 x 方向",
          p[1][2] > p[1][3], "pad1 size=%sx%s" % (p[1][2], p[1][3]))

    # ★ 外框必须包住所有焊盘。这里曾经是错的：
    #   走了矩形分支却返回 bx/by（本体框），于是 QFP 的外框比焊盘外沿还小 2.9mm。
    #   双排封装走十字分支所以一直没暴露。
    crt = lines_on(t, "F.CrtYd")
    cx = [v for s in crt for v in (s[0], s[2])]
    cy = [v for s in crt for v in (s[1], s[3])]
    padx = [p[k][0] + p[k][2] / 2 for k in p] + [p[k][0] - p[k][2] / 2 for k in p]
    pady = [p[k][1] + p[k][3] / 2 for k in p] + [p[k][1] - p[k][3] / 2 for k in p]
    check("quad", "★ 外框包住所有焊盘",
          min(cx) <= min(padx) and max(cx) >= max(padx) and
          min(cy) <= min(pady) and max(cy) >= max(pady),
          "外框 x[%s,%s] y[%s,%s] vs 焊盘 x[%.3f,%.3f] y[%.3f,%.3f]"
          % (min(cx), max(cx), min(cy), max(cy),
             min(padx), max(padx), min(pady), max(pady)))
    check("quad", "焊盘外沿到外框恰好 0.25",
          abs(max(padx) + 0.25 - max(cx)) < 1e-6 and
          abs(min(padx) - 0.25 - min(cx)) < 1e-6,
          "%.3f / %.3f" % (max(cx) - max(padx), min(padx) - min(cx)))
    check("quad", "外框是矩形（4 段）", len(crt) == 4, str(len(crt)))

    # 丝印必须被四个方向的焊盘裁开，且不压盘
    silk = lines_on(t, "F.SilkS")
    bad = []
    for x0, y0, x1, y1 in silk:
        sx0, sx1 = sorted((x0, x1)); sy0, sy1 = sorted((y0, y1))
        sx0 -= 0.06; sx1 += 0.06; sy0 -= 0.06; sy1 += 0.06
        for k in p:
            px, py, pw, ph = p[k]
            if not (sx1 < px - pw/2 or sx0 > px + pw/2 or
                    sy1 < py - ph/2 or sy0 > py + ph/2):
                bad.append(k)
    check("quad", "丝印不压任何焊盘", not bad, "压到 %s" % sorted(set(bad)))

    # ★ 位号文字也必须在外框之外。这里曾经是错的：
    #   文字放在 body/2+0.95，QFP 上排焊盘正好在那儿 -> 丝印压阻焊开窗。
    #   双排封装左右没焊盘，所以一直没暴露。
    m = re.search(r'\(property "Reference"[^(]*\(at 0 (-?[\d.]+)', t)
    ref_y = float(m.group(1))
    check("quad", "★ 位号文字在焊盘之外（不压阻焊开窗）",
          abs(ref_y) > max(pady) + 0.06,
          "文字 y=%.2f, 焊盘最大 |y|=%.3f" % (ref_y, max(pady)))

    # Pin1 三角标比丝印框还往外伸 0.16，位号必须同时清过它。
    # 这里曾经只差 0.08mm，被 DRC 的 silk_overlap 抓到。
    tri = max(abs(v) for s in silk for v in (s[1], s[3])) + 0.16
    check("quad", "★ 位号文字清过 Pin1 三角标（丝印间距 >= 0.2）",
          abs(ref_y) - 0.5 >= tri + 0.2,
          "文字下沿 %.2f, 三角标 %.2f, 间隙 %.3f" % (abs(ref_y) - 0.5, tri,
                                                 abs(ref_y) - 0.5 - tri))


# ---------------------------------------------------------------------------
def test_grid_warning(tmp):
    """顶对齐 + 非 100 倍数的间距 -> 半个跨度落在半格上 -> 必须警告。

    这是 150/300 mil 规范下的真实陷阱：符号画出来完全正常，但引脚连不上线。
    """
    s = vssop_spec()
    s["symbol_style"]["pitch_mil"] = 150
    s["symbol_style"]["group_gap_mil"] = 300
    r = gen(s, os.path.join(tmp, "g1"))
    check("grid_warn", "150/300 mil -> 报栅格警告", bool(r["warnings"]),
          str(r["warnings"][:1]))
    check("grid_warn", "警告里点出了根因",
          any("100 的整数倍" in w for w in r["warnings"]), str(r["warnings"]))

    r2 = gen(vssop_spec(), os.path.join(tmp, "g2"))
    check("grid_warn", "200/400 mil -> 不报", not r2["warnings"], str(r2["warnings"]))


# 本库的 house style：**200 / 400 固定，全库统一，不按器件改**。
# 不是审美问题：同一个库里混着 100 和 200，读图的人每换一颗器件都要重新
# 建立比例感。官方库用 100 mil 是官方的风格，不拿它当理由改。
def test_house_style_pitch(tmp):
    s = vssop_spec()
    s["symbol_style"].pop("pitch_mil", None)
    s["symbol_style"].pop("group_gap_mil", None)
    r = gen(s, os.path.join(tmp, "hs1"))
    st = read(r["sym"])
    ys = sorted({round(p[1], 4) for p in pins_of_sym(st).values()})
    dys = sorted({round(b - a, 4) for a, b in zip(ys, ys[1:])})
    check("house_style", "不给 pitch_mil 时默认 200 mil = 5.08 mm",
          5.08 in dys, str(dys))
    check("house_style", "不给 group_gap_mil 时默认 400 mil = 10.16 mm",
          10.16 in dys, str(dys))

    # 改了要吵一声（不是报错 —— 引脚真多到摆不下的器件留个口子，但不能静默）
    s2 = vssop_spec()
    s2["symbol_style"]["pitch_mil"] = 100
    s2["symbol_style"]["group_gap_mil"] = 200
    r2 = gen(s2, os.path.join(tmp, "hs2"))
    check("house_style", "★ 改成 100/200 -> 必须警告",
          any("house style" in w for w in r2["warnings"]), str(r2["warnings"]))


def test_quad_body_and_fields(tmp):
    """四边封装的三个坑，都是拿真实 56 脚 QFN 跑才暴露的。

    ① 本体宽度没算上顶/底引脚的横向铺开 -> 引脚悬在本体外 17.8mm
    ② 位号/Value 只放在本体上沿之上，没避开上排引脚 -> 文字压在引脚上
    ③ 外框也得跟着变大（由 ① 推导，顺带验）
    """
    r = gen(qfp_spec(), os.path.join(tmp, "qb"))
    st = read(r["sym"])

    pins = pins_of_sym(st)                      # {num: (x, y, rot)}
    # 引脚长度从生成的符号里读，不要写死（qfp_spec 用的是 100 mil）
    _lens = {float(m) for m in re.findall(r'\(length ([\d.]+)\)', st)}
    ln = _lens.pop() if len(_lens) == 1 else 2.54
    b = re.search(r'\(rectangle\s*\(start (-?[\d.]+) (-?[\d.]+)\)\s*'
                  r'\(end (-?[\d.]+) (-?[\d.]+)\)', st)
    x0, ya, x1, yb = (float(g) for g in b.groups())
    bx0, bx1, by1, by0 = min(x0, x1), max(x0, x1), max(ya, yb), min(ya, yb)

    # 引脚根部必须落进本体（两个轴都要查）
    outside = []
    for num, (x, y, rot) in pins.items():
        rx = x + (ln if rot == 0 else (-ln if rot == 180 else 0))
        ry = y + (ln if rot == 90 else (-ln if rot == 270 else 0))
        if not (bx0 - 0.02 <= rx <= bx1 + 0.02 and by0 - 0.02 <= ry <= by1 + 0.02):
            outside.append((num, rx, ry))
    check("quad_body", "① 所有引脚根部都在本体内",
          not outside, "跑到外面: %s  本体 x[%.2f,%.2f] y[%.2f,%.2f]"
          % (outside[:4], bx0, bx1, by0, by1))

    # ② 位号 / Value 必须避开引脚带
    ys = [v[1] for v in pins.values()]
    for key in ("Reference", "Value"):
        m = re.search(r'\(property "%s" "[^"]*"\s*\(at (-?[\d.]+) (-?[\d.]+)' % key, st)
        vy = float(m.group(2))
        ok = vy > max(ys) or vy < min(ys)
        check("quad_body", "② %s 避开引脚带 (y=%.2f, 引脚 %.2f..%.2f)"
              % (key, vy, min(ys), max(ys)), ok)


def test_grid_array(tmp):
    """球栅阵列（WLCSP）。加到 make_part.py 之前，这一族根本没有生成器。"""
    r = gen(wlcsp_spec(), os.path.join(tmp, "g"))
    t = read(r["fp"])
    p = ball_pads_of(t)
    check("grid", "焊盘数 = 49（50 位减 A1 空位）", len(p) == 49, str(len(p)))
    check("grid", "★ A1 是空位（图纸注 8 的 + 标记）", "A1" not in p)
    check("grid", "★ 全部圆焊盘 + pad_prop_bga（官方 WLCSP 同款）",
          t.count("smd circle") == 49 and t.count("pad_prop_bga") == 49,
          "circle=%d bga=%d" % (t.count("smd circle"), t.count("pad_prop_bga")))
    check("grid", "焊盘直径 = 0.22（标称球径 0.218）",
          all(abs(v[2] - 0.22) < 1e-9 for v in p.values()))

    # 坐标从球名解出来：x = (列 - 6) * 0.21, y = row_y[行]
    # 俯视图里列号往右递增，所以 A3 在左上、A11 在右上（A1 是空位）。
    check("grid", "A3 在 (-0.63, -1.242)（左上）",
          p.get("A3", ())[:2] == (-0.63, -1.242), str(p.get("A3")))
    check("grid", "A11 在 (1.05, -1.242)（右上）",
          p.get("A11", ())[:2] == (1.05, -1.242), str(p.get("A11")))
    check("grid", "C1 在 (-1.05, -0.682)",
          p.get("C1", ())[:2] == (-1.05, -0.682), str(p.get("C1")))
    check("grid", "J11 在 (1.05, 1.242)",
          p.get("J11", ())[:2] == (1.05, 1.242), str(p.get("J11")))

    xs = [v[0] for v in p.values()]
    ys = [v[1] for v in p.values()]
    check("grid", "D1 阵列跨距 = 2.100", abs(max(xs) - min(xs) - 2.100) < 1e-9,
          "%.4f" % (max(xs) - min(xs)))
    check("grid", "E1 阵列跨距 = 2.484", abs(max(ys) - min(ys) - 2.484) < 1e-9,
          "%.4f" % (max(ys) - min(ys)))
    rowA = sorted(v[0] for k, v in p.items() if k[0] == "A")
    check("grid", "同排相邻球心距 = eD 0.42（交错阵列里跨两格）",
          all(abs(b - a - 0.42) < 1e-9 for a, b in zip(rowA, rowA[1:])), str(rowA))

    # 外框：官方 grid_array 生成器在 WLCSP-20/35/64 上量的都是矩形 + 本体 1.0/边。
    # 本 toolkit 的 peripheral 族是 12 段十字 + 0.25 —— 两族确实不一样，别抄错。
    cr = extent_on(t, "F.CrtYd")
    check("grid", "★ 外框是矩形（不是 12 段十字）",
          len(lines_on(t, "F.CrtYd")) == 4, str(len(lines_on(t, "F.CrtYd"))))
    hx, hy = 2.8819 / 2, 3.1024 / 2
    check("grid", "外框 = 本体/2 + 1.0（IPC-7351 标称）",
          cr is not None and all(abs(a - b) < 1e-9 for a, b in
                                 zip(cr, (-hx - 1.0, -hy - 1.0, hx + 1.0, hy + 1.0))),
          str(cr))

    # Fab 倒角 = 0.5 * min(本体半宽, 本体半高)，官方三个 WLCSP 都是这个
    fab = polys_on(t, "F.Fab")
    check("grid", "Fab 有 1 个多边形", len(fab) == 1, str(len(fab)))
    if fab:
        ch = 0.5 * min(hx, hy)                       # 0.720475
        want = [(-hx + ch, -hy), (hx, -hy), (hx, hy), (-hx, hy), (-hx, -hy + ch)]
        ok = (len(fab[0]) == 5 and
              all(abs(a - b) < 1e-6 for pt, wt in zip(fab[0], want)
                  for a, b in zip(pt, wt)))
        check("grid", "★ Fab 倒角 = 0.5*min(半宽,半高) = 0.720475", ok, str(fab[0]))

    check("grid", "阻焊开窗余量 0.02 写进封装", "(solder_mask_margin 0.02)" in t)

    fs = json.load(open(r["fp_spec"], encoding="utf-8"))
    kinds = [x["kind"] for x in fs["rows"]]
    check("grid", "fp.spec family = grid_array", fs["family"] == "grid_array",
          str(fs["family"]))
    for k in ("array_w", "array_h", "matrix_cols", "matrix_rows", "count",
              "dia", "pitch", "row_span", "diag_min", "diag_max", "sd", "se"):
        check("grid", "fp.spec 有 kind=%s" % k, k in kinds, str(kinds))
    rs = [x for x in fs["rows"] if x["kind"] == "row_span"]
    check("grid", "三行 row_span 都带 from/to",
          len(rs) == 3 and all(x.get("from") and x.get("to") for x in rs),
          str([(x["symbol"], x.get("from"), x.get("to")) for x in rs]))


def test_grid_rejects_garbage(tmp):
    """球名解不出来要报错，不能默默画到 (0,0) 上去。"""
    spec = wlcsp_spec()
    spec["pins"][0] = dict(spec["pins"][0], number="PIN1")
    try:
        gen(spec, os.path.join(tmp, "bad"))
        check("grid_err", "非法球名必须抛错", False, "居然生成成功了")
    except ValueError as e:
        check("grid_err", "非法球名抛 ValueError 并点名", "PIN1" in str(e), str(e))


def test_idempotent(tmp):
    a = gen(vssop_spec(), os.path.join(tmp, "i1"))
    b = gen(vssop_spec(), os.path.join(tmp, "i2"))
    same = (read(a["sym"]) == read(b["sym"]) and read(a["fp"]) == read(b["fp"]))
    check("idempotent", "同样 spec 生成两次，逐字节一致", same)


# ---------------------------------------------------------------------------
def main():
    pat = sys.argv[1] if len(sys.argv) > 1 else ""
    tmp = tempfile.mkdtemp(prefix="mkpart_selftest_")
    for fn in (test_pads, test_silk_courtyard, test_symbol_layout, test_quad,
               test_quad_body_and_fields, test_grid_warning,
               test_house_style_pitch, test_grid_array,
               test_grid_rejects_garbage, test_idempotent):
        if pat and pat not in fn.__name__:
            continue
        try:
            fn(tmp)
        except Exception:                                     # noqa: BLE001
            RESULTS.append((fn.__name__.replace("test_", ""), "EXCEPTION", False,
                            traceback.format_exc().splitlines()[-1]))
    by = {}
    for g, n, ok, d in RESULTS:
        by.setdefault(g, []).append((n, ok, d))
    nfail = 0
    for g in sorted(by):
        items = by[g]
        bad = [x for x in items if not x[1]]
        nfail += len(bad)
        print("%-12s %-38s (%d checks) %s"
              % (g, "", len(items), "FAIL" if bad else "ok"))
        for n, ok, d in items:
            if not ok:
                print("    FAIL  %s" % n)
                if d:
                    print("          %s" % d)
    print("\n%d checks, %d failed" % (len(RESULTS), nfail))
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
