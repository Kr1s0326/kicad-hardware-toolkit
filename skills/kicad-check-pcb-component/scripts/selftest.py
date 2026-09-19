#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest.py - per-family self test, no CAD files and no Gerbers needed.

    python selftest.py            # run everything
    python selftest.py qfn        # only tests whose name contains "qfn"

Each test builds a synthetic pad set (exactly what a real footprint produces),
runs the family module on it and asserts the measured numbers.  When something
breaks, the failing line names the family and the quantity, e.g.

    FAIL  peripheral / qfn-32: lead_pitch   measured 0.5000 want 0.5000

so you immediately know whether to look in families/peripheral.py, in
core/gerber.py (parsing) or in core/report.py (layout).
"""

import os
import sys
import traceback


HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from core import draw as D                                        # noqa: E402
from core import gerber as G                                      # noqa: E402
from core import geometry                                         # noqa: E402

RESULTS = []
SKIPPED = []


def have_pil():
    """Pillow 只在画图时用得到；量测逻辑不需要它。

    没有 Pillow 时，量测用例照常跑（那才是这个文件的主要价值），
    画图用例记为"跳过"并明确打印 —— 而不是假装通过，也不是直接失败。
    """
    try:
        import PIL                                        # noqa: F401
        return PIL is not None
    except ImportError:
        return False


PIL = have_pil()


def panels(fam, ctx, items):
    """跑一遍 panel() 确保画图不崩；没装 Pillow 就跳过"""
    if not PIL:
        SKIPPED.append("panels (需要 Pillow)")
        return
    for k, r in items:
        v, ex = fam.measure(k, ctx, r)
        fam.panel(k, ctx, r, v, ex, k)


# ---------------------------------------------------------------------------
# synthetic pad builders (what a real copper Gerber would flash)
# ---------------------------------------------------------------------------
def wlcsp_like():
    """49-ball staggered WLCSP, SG-XFWLB-49 numbers"""
    rowy = {"A": -1.242, "B": -0.962, "C": -0.682, "D": -0.341, "E": 0.0,
            "F": 0.341, "G": 0.682, "H": 0.962, "J": 1.242}
    pads = []
    for i, r in enumerate("ABCDEFGHJ", start=1):
        for c in range(1, 12):
            if (i + c) % 2 or (r == "A" and c == 1):
                continue
            pads.append((30 + (c - 6) * 0.21, 20 + rowy[r], 0.22, 0.22))
    return pads


def qfn32_like():
    """QFN-32 5x5mm P0.5mm with a 3.1x3.1mm exposed pad (8 leads per side)"""
    cx, cy, n, p = 20.0, 15.0, 8, 0.5
    half = (n - 1) / 2 * p                        # 1.75
    pads = [(cx, cy, 3.1, 3.1)]                   # exposed pad
    for k in range(n):
        off = -half + k * p
        pads.append((cx - 2.55, cy + off, 1.3, 0.28))     # left   (L x b)
        pads.append((cx + 2.55, cy + off, 1.3, 0.28))     # right
        pads.append((cx + off, cy - 2.55, 0.28, 1.3))     # top
        pads.append((cx + off, cy + 2.55, 0.28, 1.3))     # bottom
    return pads


def lqfp48_like():
    """LQFP-48 7x7mm P0.5mm body, pads 1.5 x 0.3, span 9.0mm centre to centre"""
    cx, cy, n, p = 20.0, 15.0, 12, 0.5
    half = (n - 1) / 2 * p                        # 2.75
    span = 4.5                                    # centre to centre / 2
    pads = []
    for k in range(n):
        off = -half + k * p
        pads.append((cx - span, cy + off, 1.5, 0.3))
        pads.append((cx + span, cy + off, 1.5, 0.3))
        pads.append((cx + off, cy - span, 0.3, 1.5))
        pads.append((cx + off, cy + span, 0.3, 1.5))
    return pads


def sot23_like():
    """SOT-23: 2 pads on one side, 1 on the other, 0.95mm pitch, 2.9mm span"""
    cx, cy = 20.0, 15.0
    return [(cx - 1.1, cy - 0.95, 0.9, 0.8),
            (cx - 1.1, cy, 0.9, 0.8),
            (cx + 1.1, cy - 0.475, 0.9, 0.8)]


def soic8_like():
    """SOIC-8 3.9x4.9mm P1.27mm, pads 1.55 x 0.6, span 5.4mm centre to centre"""
    cx, cy, n, p = 20.0, 15.0, 4, 1.27
    half = (n - 1) / 2 * p                        # 1.905
    return [(cx - 2.7, cy - half + k * p, 1.55, 0.6) for k in range(n)] + \
           [(cx + 2.7, cy - half + k * p, 1.55, 0.6) for k in range(n)]


def chip0603_like():
    """0603: two 0.8 x 0.95mm terminals with 1.6mm centre distance"""
    cx, cy = 20.0, 15.0
    return [(cx - 0.8, cy, 0.9, 0.95), (cx + 0.8, cy, 0.9, 0.95)]


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------
def check(name, got, want, tol=1e-6):
    ok = got is not None and abs(got - want) <= tol
    RESULTS.append((ok, name, got, want))
    return ok


def check_exact(name, got, want):
    ok = got == want
    RESULTS.append((ok, name, got, want))
    return ok


def ctx_of(pads, body=None, drills=(), board=None):
    xs = [p[0] for p in pads]
    ys = [p[1] for p in pads]
    c = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
    ctx = {"pads": pads, "origin": c, "body": body, "drills": list(drills),
           "board": board, "paste_circles": [], "paste_aperture": 0.0,
           "_collinear_tol": 0.05}
    if body:
        ctx["edge_segs"] = []
    return ctx


def test_grid_array():
    from families import grid_array as fam
    pads = wlcsp_like()
    ctx = ctx_of(pads, body=(28.55905, 18.4488, 31.44095, 21.5512))
    assert fam.detect(ctx), "WLCSP not detected as a grid array"
    fam.prepare(ctx)
    m = lambda k, r=None: fam.measure(k, ctx, r or {})[0]
    check("grid_array/wlcsp: pad count", m("count"), 49)
    check("grid_array/wlcsp: array_w (D1)", m("array_w"), 2.100, 1e-4)
    check("grid_array/wlcsp: array_h (E1)", m("array_h"), 2.484, 1e-4)
    check("grid_array/wlcsp: matrix_cols (MD)", m("matrix_cols"), 11)
    check("grid_array/wlcsp: matrix_rows (ME)", m("matrix_rows"), 9)
    check("grid_array/wlcsp: ball dia", m("dia"), 0.220, 1e-4)
    check("grid_array/wlcsp: pitch (eD)", m("pitch"), 0.420, 1e-4)
    check("grid_array/wlcsp: eE3s E->G",
          m("row_span", {"from": "E", "to": "G"}), 0.682, 1e-4)
    check("grid_array/wlcsp: eE2s F->H",
          m("row_span", {"from": "F", "to": "H"}), 0.621, 1e-4)
    check("grid_array/wlcsp: eE1s G->J",
          m("row_span", {"from": "G", "to": "J"}), 0.560, 1e-4)
    check("grid_array/wlcsp: eS1 diag_min", m("diag_min"), 0.350, 1e-4)
    check("grid_array/wlcsp: eS2 diag_max", m("diag_max"), 0.4005, 2e-4)
    check("grid_array/wlcsp: SD", m("sd"), 0.210, 1e-4)
    check("grid_array/wlcsp: SE", m("se"), 0.000, 1e-4)
    check("grid_array/wlcsp: body_w", m("body_w"), 2.8819, 1e-4)
    check("grid_array/wlcsp: body_h", m("body_h"), 3.1024, 1e-4)
    # panels must not crash
    panels(fam, ctx, [("array_w", {}), ("count", {}), ("pitch", {}),
                      ("row_span", {"from": "E", "to": "G"}), ("sd", {}),
                      ("se", {}), ("matrix_cols", {}), ("dia", {}), ("na", {})])
    return True


def test_peripheral_qfn():
    from families import peripheral as fam
    pads = qfn32_like()
    ctx = ctx_of(pads, body=(16.4, 11.4, 23.6, 18.6))
    assert fam.detect(ctx), "QFN not detected as a peripheral package"
    fam.prepare(ctx)
    m = lambda k, r=None: fam.measure(k, ctx, r or {})[0]
    check("peripheral/qfn-32: lead pitch e", m("lead_pitch"), 0.500, 1e-4)
    check("peripheral/qfn-32: lead_count N", m("lead_count"), 32)
    check("peripheral/qfn-32: count (all pads)", m("count"), 33)
    check("peripheral/qfn-32: leads_per_side MD", m("leads_per_side"), 8)
    check("peripheral/qfn-32: side_count", m("side_count"), 4)
    check("peripheral/qfn-32: pad_span_x", m("pad_span_x"), 5.100, 1e-4)
    check("peripheral/qfn-32: pad_edge_span_x", m("pad_edge_span_x"), 6.400, 1e-4)
    check("peripheral/qfn-32: lead_width b", m("lead_width"), 0.280, 1e-4)
    check("peripheral/qfn-32: lead_length L", m("lead_length"), 1.300, 1e-4)
    check("peripheral/qfn-32: ep_x D2", m("ep_x"), 3.100, 1e-4)
    check("peripheral/qfn-32: ep_y E2", m("ep_y"), 3.100, 1e-4)
    check("peripheral/qfn-32: body_w", m("body_w"), 7.200, 1e-4)
    panels(fam, ctx, [(k, {}) for k in (
        "lead_pitch", "lead_count", "leads_per_side", "side_count", "pad_span_x",
        "pad_span_y", "pad_edge_span_x", "pad_edge_span_y", "lead_width",
        "lead_length", "ep_x", "ep_y", "count", "side_count")])
    return True


def test_peripheral_qfp():
    from families import peripheral as fam
    ctx = ctx_of(lqfp48_like(), body=(16.5, 11.5, 23.5, 18.5))
    assert fam.detect(ctx), "LQFP not detected as a peripheral package"
    fam.prepare(ctx)
    m = lambda k: fam.measure(k, ctx, {})[0]
    check("peripheral/lqfp-48: lead pitch e", m("lead_pitch"), 0.500, 1e-4)
    check("peripheral/lqfp-48: lead_count N", m("lead_count"), 48)
    check("peripheral/lqfp-48: leads_per_side", m("leads_per_side"), 12)
    check("peripheral/lqfp-48: side_count", m("side_count"), 4)
    check("peripheral/lqfp-48: pad_span_x", m("pad_span_x"), 9.000, 1e-4)
    check("peripheral/lqfp-48: pad_edge_span_x", m("pad_edge_span_x"), 10.500, 1e-4)
    check("peripheral/lqfp-48: lead_width b", m("lead_width"), 0.300, 1e-4)
    check("peripheral/lqfp-48: lead_length L", m("lead_length"), 1.500, 1e-4)
    check_exact("peripheral/lqfp-48: ep_x (none)", m("ep_x"), None)
    return True


def test_peripheral_sot23():
    from families import peripheral as fam
    ctx = ctx_of(sot23_like(), body=(17.75, 13.85, 22.25, 16.15))
    assert fam.detect(ctx), "SOT-23 not detected as a peripheral package"
    fam.prepare(ctx)
    m = lambda k: fam.measure(k, ctx, {})[0]
    check("peripheral/sot23: lead pitch e", m("lead_pitch"), 0.950, 1e-4)
    check("peripheral/sot23: lead_count N", m("lead_count"), 3)
    check("peripheral/sot23: side_count", m("side_count"), 2)
    check("peripheral/sot23: pad_span_x", m("pad_span_x"), 2.200, 1e-4)
    check("peripheral/sot23: lead_width b", m("lead_width"), 0.800, 1e-4)
    check("peripheral/sot23: lead_length L", m("lead_length"), 0.900, 1e-4)
    panels(fam, ctx, [(k, {}) for k in
                      ("lead_pitch", "lead_width", "lead_length", "pad_span_x", "count")])
    return True


def test_peripheral_soic8():
    from families import peripheral as fam
    ctx = ctx_of(soic8_like(), body=(17.15, 12.05, 22.85, 17.95))
    assert fam.detect(ctx), "SOIC-8 not detected as a peripheral package"
    fam.prepare(ctx)
    m = lambda k: fam.measure(k, ctx, {})[0]
    check("peripheral/soic8: lead pitch e", m("lead_pitch"), 1.270, 1e-4)
    check("peripheral/soic8: lead_count N", m("lead_count"), 8)
    check("peripheral/soic8: side_count", m("side_count"), 2)
    check("peripheral/soic8: leads_per_side", m("leads_per_side"), 4)
    check("peripheral/soic8: pad_span_x", m("pad_span_x"), 5.400, 1e-4)
    return True


def test_chip_0603():
    from families import chip as fam
    ctx = ctx_of(chip0603_like(), body=(19.0, 14.35, 21.0, 15.65))
    assert fam.detect(ctx), "0603 not detected as a chip package"
    fam.prepare(ctx)
    m = lambda k: fam.measure(k, ctx, {})[0]
    check("chip/0603: count", m("count"), 2)
    check("chip/0603: pad_span", m("pad_span"), 1.600, 1e-4)
    check("chip/0603: pad_edge_span", m("pad_edge_span"), 2.500, 1e-4)
    check("chip/0603: pad_gap", m("pad_gap"), 0.700, 1e-4)
    check("chip/0603: pad_l", m("pad_l"), 0.900, 1e-4)
    check("chip/0603: pad_w", m("pad_w"), 0.950, 1e-4)
    check("chip/0603: body_w (X)", m("body_w"), 2.000, 1e-4)
    check("chip/0603: body_long L", m("body_long"), 2.000, 1e-4)
    check("chip/0603: body_short W", m("body_short"), 1.300, 1e-4)
    panels(fam, ctx, [(k, {}) for k in
                      ("pad_span", "pad_gap", "pad_w", "pad_l", "count", "pad_edge_span")])
    return True


# ---------------------------------------------------------------------------
# core checks: gerber parser, classification
# ---------------------------------------------------------------------------
def test_gerber_parser():
    """write a tiny Gerber the way KiCad does and read it back"""
    import tempfile
    txt = """G04 test*
%FSLAX46Y46*%
%MOMM*%
%ADD10C,0.220000*%
%ADD11C,2.500000*%
D10*
X1000000Y-2000000D03*
X3000000Y-2000000D03*
D11*
X6250000Y-5000000D02*
G75*
G02*
X3750000Y-5000000I-1250000J0D01*
G01*
M02*
"""
    with tempfile.NamedTemporaryFile("w", suffix=".gbr", delete=False) as fh:
        fh.write(txt)
        p = fh.name
    ap, pr = G.parse_gerber(p, flip_y=True)
    pads = G.flash_pads(ap, pr)
    check("core/gerber: flash count", len(pads), 2)
    check("core/gerber: flash x", pads[0][0], 1.0, 1e-9)
    check("core/gerber: flash y (flip)", pads[0][1], 2.0, 1e-9)
    check("core/gerber: aperture size", pads[0][2], 0.22, 1e-9)
    circles = G.stroked_circles(pr, ap)
    check("core/gerber: stroked circle count", len(circles), 1)
    if circles:
        cx, cy, r = circles[0]
        check("core/gerber: circle centre x", cx, 5.0, 1e-6)
        check("core/gerber: circle centre y", cy, 5.0, 1e-6)
        check("core/gerber: circle outer r", r, 2.5, 1e-6)
    os.unlink(p)


def test_classify():
    check_exact("core/geometry: WLCSP -> grid",
                geometry.classify_pads(wlcsp_like())["type"], "grid")
    check_exact("core/geometry: QFN -> peripheral",
                geometry.classify_pads(qfn32_like())["type"], "peripheral")
    check_exact("core/geometry: SOIC -> peripheral",
                geometry.classify_pads(soic8_like())["type"], "peripheral")
    check_exact("core/geometry: QFN exposed pad detected",
                geometry.classify_pads(qfn32_like())["exposed"] is not None, True)


def test_family_dispatch():
    """auto detection must pick the right family for every fixture"""
    from families import chip, grid_array, peripheral
    for pads, want in ((wlcsp_like(), "grid_array"),
                       (qfn32_like(), "peripheral"),
                       (lqfp48_like(), "peripheral"),
                       (sot23_like(), "peripheral"),
                       (soic8_like(), "peripheral"),
                       (chip0603_like(), "chip")):
        got = None
        for fam in (chip, grid_array, peripheral):     # same order as the driver
            if fam.detect(ctx_of(pads)):
                got = fam.FAMILY
                break
        check_exact("dispatch/%s" % want, got, want)


def test_pad_shape_and_panels():
    """两个会让报告**静默画错 / 画空**的坑。

    ① 光圈形状在解析时被丢掉，画图只能靠"长宽相等就当圆"去猜 ——
       QFN 的 4x4 方形散热焊盘因此被画成圆形。
    ② 面板的 View 窗口用绝对坐标、焊盘按相对坐标绘制，两者永不相交 ——
       e / b / L / 列中心距 四个面板全是空白，只剩一条尺寸线悬着。

    测试方法上的两个教训（写成断言前踩过）：
      * 不能只测 `_is_round()` —— 那样即使 draw_pads() 根本没用它也会绿。
        必须**真的画一张图**，再看方形焊盘的角上有没有填充（圆会留白）。
      * 不能只数"灰像素" —— 标题文字的抗锯齿边缘也算灰的，阈值弱到
        面板全空都能通过。必须数**恰好等于焊盘填充色**的像素。
    """
    from core import gerber as G
    from families import peripheral as fam

    FILL = D.PAD_FILL

    def count_fill(im):
        """恰好等于焊盘填充色的像素数（抗锯齿的灰不会精确命中）"""
        return sum(1 for px in im.convert("RGB").getdata() if px == FILL)

    # ① 形状要跟到 Pad 上，而且 draw_pads 必须真的按它画
    aps = {10: ("C", [0.3]), 11: ("R", [0.8, 0.2]), 12: ("O", [1.0, 0.5])}
    prims = [("flash", 10, 0.0, 0.0), ("flash", 11, 1.0, 0.0),
             ("flash", 12, 2.0, 0.0)]
    got = [getattr(q, "shape", "?") for q in G.flash_pads(aps, prims)]
    check_exact("pad_shape/① flash_pads 带出光圈形状", got,
                ["circle", "rect", "obround"])
    check_exact("pad_shape/① 平移后形状不丢",
                G.Pad(1, 2, 3, 4, "rect").moved(1, 1).shape, "rect")
    check_exact("pad_shape/① geometry.move_pads 也保留形状",
                geometry.move_pads([G.Pad(1, 2, 3, 4, "rect")], 1, 1)[0].shape,
                "rect")

    # 真的画一张：4x4 方形焊盘，看它的**角**有没有被填充
    # （内切圆会留白，方矩形会填满 —— 这是决定性的区分）
    def corner_filled(shape):
        img, d = D.new_panel("shape probe")
        V = D.View((-3.0, -3.0, 3.0, 3.0))
        D.draw_pads(d, V, [G.Pad(0.0, 0.0, 4.0, 4.0, shape)])
        cx, cy = V.pt(-1.85, -1.85)          # 逼近角点、但在边框内
        return img.convert("RGB").getpixel((int(cx), int(cy))) == FILL

    check_exact("pad_shape/① 方形焊盘画成方（角上有料）",
                corner_filled("rect"), True)
    check_exact("pad_shape/① 圆角矩形按方画（角上有料）",
                corner_filled("roundrect"), True)
    check_exact("pad_shape/① 圆形焊盘画成圆（角上留白）",
                corner_filled("circle"), False)
    check_exact("pad_shape/① 无形状信息时退回长宽猜测",
                D._is_round("", 0.3, 0.3), True)

    # ② 面板要真的把焊盘画出来（数恰好等于填充色的像素）
    qfn = qfn32_like()
    ctx = ctx_of(qfn, body=(16.4, 11.4, 23.6, 18.6))
    fam.prepare(ctx)
    check_exact("panel_pads/② 合成 ctx 的 origin 非零（否则测不出坐标系 bug）",
                ctx["origin"] != (0.0, 0.0), True)
    for kind, val in (("lead_pitch", 0.5), ("lead_width", 0.28),
                      ("lead_length", 1.3), ("pad_span_x", 5.1)):
        n = count_fill(fam.panel(kind, ctx, {}, val, {}, "T"))
        check_exact("panel_pads/② %s 面板画出了焊盘（填充像素 %d）" % (kind, n),
                    n > 200, True)

    # ③ 每张测量图都必须：画出整颗器件的焊盘环 + 高亮被量的部分。
    #    踩过的坑：
    #      * 有几个面板只放两个焊盘的放大图，看图的人不知道量的是整颗
    #        器件里的哪儿；
    #      * draw_pads 支持 hi 高亮，但 peripheral 族从来没传过 ——
    #        e / b / L 三张图长得一模一样，分不出在量哪个；
    #      * D / E 两张走 core/common.py，连散热焊盘都没标（一会儿蓝一会儿灰）。
    HILITE, EXPOSED = D.PAD_HI, D.PAD_FILL_EXPOSED
    counts = {k: (0, 0) for k in ("lead_pitch", "lead_width", "lead_length",
                                  "body_w", "ep_x")}
    for k, v in (("lead_pitch", 0.5), ("lead_width", 0.28),
                 ("lead_length", 1.3), ("body_w", 7.2), ("ep_x", 3.1)):
        px = list(fam.panel(k, ctx, {}, v, {}, "T").convert("RGB").getdata())
        counts[k] = (sum(1 for q in px if q == HILITE),
                     sum(1 for q in px if q == EXPOSED))
    # 哪些面板该出现哪种标记：
    #   e / b / L 量的是普通焊盘 -> 红色高亮 PAD_HI
    #   ep_x 量的是散热焊盘     -> 蓝色 PAD_FILL_EXPOSED（蓝色在绘制时覆盖红色，
    #                             所以散热盘永远不会是红的）
    #   body_w 量的是**本体**   -> 本来就不该高亮任何焊盘；但整颗 IC 视图里
    #                             散热盘仍应被标出来（以前走 common.py 时是灰的）
    for k in ("lead_pitch", "lead_width", "lead_length"):
        hi, ex = counts[k]
        check_exact("panel_hl/③ %s 用红色高亮被量的焊盘（%d 像素）" % (k, hi),
                    hi > 60, True)
    check_exact("panel_hl/③ ep_x 用蓝色标出散热焊盘（%d 像素）"
                % counts["ep_x"][1], counts["ep_x"][1] > 60, True)
    check_exact("panel_hl/③ body_w 不高亮任何焊盘（量的是本体，%d 像素）"
                % counts["body_w"][0], counts["body_w"][0] == 0, True)
    check_exact("panel_hl/③ body_w 仍标出散热焊盘（%d 像素）"
                % counts["body_w"][1], counts["body_w"][1] > 60, True)

    # ④ 尺寸线必须量在**正确的轴**上。
    #    踩过的坑：lead_length 的两支写反了 —— 左/右引脚本该沿 x 量长边，
    #    却用了 dim_v，画出一条 0.8 的**竖直线**，视觉上跨两个间距，
    #    看着像在量 pitch。数值碰巧对，图是错的。
    #    直接拦住 dim_h / dim_v 的调用，比看图可靠。
    def dim_calls(kind, value):
        got = []
        oh, ov = D.dim_h, D.dim_v
        D.dim_h = lambda d, V, a, b, *ar, **kw: got.append(("h", round(abs(b - a), 6)))
        D.dim_v = lambda d, V, a, b, *ar, **kw: got.append(("v", round(abs(b - a), 6)))
        try:
            fam.panel(kind, ctx, {}, value, {}, "T")
        finally:
            D.dim_h, D.dim_v = oh, ov
        return got

    # 面板挑中的那个焊盘在哪条边上，决定了长边/窄边各沿哪个轴：
    #   左/右引脚：长边沿 x -> L 用 h、窄边沿 y -> b 用 v
    #   上/下引脚：长边沿 y -> L 用 v、窄边沿 x -> b 用 h
    # （这里**不能写死**期望的轴 —— 一开始写死了，结果面板按 _leads() 的顺序
    #   挑到上边焊盘，测试反而把对的代码判成错的。）
    _sel = min(fam._leads(ctx), key=lambda q: min(q[2], q[3]))
    _w, _h, _side = max(_sel[2], _sel[3]), min(_sel[2], _sel[3]), _sel[4]
    _tb = _side in ("top", "bottom")
    check_exact("panel_axis/③ 选中焊盘在 %s 边 (长边沿 %s)"
                % (_side, "y" if _tb else "x"), True, True)
    check_exact("panel_axis/③ b 量窄边 -> %s，跨度 %.2f"
                % ("水平" if _tb else "竖直", _h),
                dim_calls("lead_width", _h), [("h" if _tb else "v", round(_h, 6))])
    check_exact("panel_axis/③ L 量长边 -> %s，跨度 %.2f"
                % ("竖直" if _tb else "水平", _w),
                dim_calls("lead_length", _w), [("v" if _tb else "h", round(_w, 6))])


def test_ep_buried_pads():
    """散热焊盘**里面**的焊盘不能算焊盘。

    docstring 一直写着 "an exposed / thermal pad does not count"，但代码
    只排除了 EP 本身，没排除 EP **里面**的 —— 于是带 EP 过孔的 QFN 被判成
    grid_array，接着拿去量 array_w / matrix_cols，得到一堆看着像模像样的
    废话（而不是报错）。失败方式是静默的，所以必须有这条用例。

    平时碰不到（官方库的 paste-only 分块不入铜层），但铜层里放过孔就中招。
    """
    from core import geometry as GE
    pads = qfn32_like()
    r = GE.classify_pads(pads)
    check_exact("buried: 纯 QFN-32 是 peripheral", r["type"], "peripheral")
    check_exact("buried: 纯 QFN-32 的焊盘数", r["npads_excl_exposed"], 32)
    check_exact("buried: 纯 QFN-32 没有埋进去的", r["n_buried"], 0)

    vias = pads + [(20.0, 15.0, 0.3, 0.3), (19.5, 15.5, 0.3, 0.3)]
    r2 = GE.classify_pads(vias)
    check_exact("buried: ★ EP 里塞两个铜过孔仍是 peripheral",
                r2["type"], "peripheral")
    check_exact("buried: ★ 过孔不计入焊盘数", r2["npads_excl_exposed"], 32)
    check_exact("buried: ★ 过孔被计为 buried", r2["n_buried"], 2)

    # 边界：EP 外面的大焊盘不能被误伤（用面积阈值就会误伤）
    edge = pads + [(20.0, 15.0 + 3.1 / 2 + 0.5, 0.3, 0.3)]
    r3 = GE.classify_pads(edge)
    check_exact("buried: EP 边上的焊盘不算 buried", r3["n_buried"], 0)


def test_verdict_states():
    """判定三态，以及“无法判定”绝不当成通过。

    以前 verdict() 在行里没有 nominal/min/max 时直接 return "PASS"。
    spec 里字段名写错、值写成字符串、整行漏了 —— 全部**静默通过**。
    """
    from core import spec as S

    def raiser(rows):
        try:
            S.validate_rows(rows)
            return None
        except SystemExit as e:
            return e.code

    check_exact("verdict: 正常 nominal -> 可判",
                raiser([{"symbol": "D", "kind": "body_w", "nominal": 3.0}]), None)
    check_exact("verdict: ★ 缺 nominal -> 报错退出（code 2）",
                raiser([{"symbol": "D", "kind": "body_w"}]), 2)
    check_exact("verdict: ★ nominal 写成字符串 -> 报错退出",
                raiser([{"symbol": "D", "kind": "body_w", "nominal": "3.0"}]), 2)
    check_exact("verdict: ★ 没有 kind -> 报错退出",
                raiser([{"symbol": "X"}]), 2)
    check_exact("verdict: min+max 也算有阈值",
                raiser([{"symbol": "D", "kind": "body_w", "min": 2.9,
                         "max": 3.1}]), None)
    check_exact("verdict: kind=na 豁免（它本来就不参与判定）",
                raiser([{"symbol": "A", "kind": "na"}]), None)

    # 绕过校验时的兜底：宁可“待测”，绝不 PASS
    check_exact("verdict: 绕过校验时也不会给 PASS",
                S.verdict("body_w", {"symbol": "D", "kind": "body_w"}, 3.0, 0.005),
                "待测")
    check_exact("verdict: 超差就是 NG",
                S.verdict("body_w", {"kind": "body_w", "nominal": 3.0}, 3.5, 0.005),
                "NG")
    check_exact("verdict: 量不到 -> 待测",
                S.verdict("body_w", {"kind": "body_w", "nominal": 3.0}, None, 0.005),
                "待测")


def test_drc_classification():
    """DRC 违规的分类。两类是“测试板的默认规则不适合”，不是封装缺陷。

    不分类的后果：官方封装都被报 NG。实测 38 个官方封装里，
    不开这些分类时有 5 个报 clear、4 个报 silk_overlap —— 全是真的但
    都不是“封装画错了”。
    """
    import drc as D
    cases = [
        ({"kind": "lib_footprint_issues", "msg": "当前配置中不包含封装库",
          "where": "@ Footprint REF**"}, True),
        ({"kind": "silk_overlap", "msg": "Silkscreen clearance",
          "where": "@(1,1): Reference field of REF** | @(2,2): Polygon of REF**"}, True),
        # 丝印压到焊盘上：这是真缺陷，必须阻断
        ({"kind": "silk_over_copper", "msg": "Silkscreen clearance",
          "where": "@(1,1): Reference field of REF** | @(2,2): Pad 1"}, False),
        ({"kind": "clearance", "msg": "间距违规", "where": "@(1,1): Pad"}, False),
        ({"kind": "courtyards_overlap", "msg": "外框重叠", "where": ""}, False),
        ({"kind": "drill_out_of_range", "msg": "孔径超出范围", "where": "@(1,1)"}, True),
    ]
    for v, want in cases:
        check_exact("drc: %-18s -> %s" % (v["kind"], "不阻断" if want else "阻断"),
                    D.is_benign(v), want)
    # 理由要说人话，不能只给个 True
    why = D.benign_reason(cases[1][0]) or ""
    check_exact("drc: 非阻断项带得出理由（不是只给个 True）", "位号" in why, True)


def test_panels_do_not_crash():
    """测不到时应当给“测不到”的面板，而不是 TypeError / ZeroDivisionError。

    这两条以前真的崩：body=None 时解包 NoneType；paste_hole_dia=0 时
    View 除以 0。崩溃会把整个检查包一起带走。
    """
    from core import common
    pads = qfn32_like()
    ctx = ctx_of(pads, body=None)
    ctx["pads"] = pads
    ctx["body"] = None
    ctx["board"] = None
    ctx["drills"] = []
    for kind, val in (("body_w", None), ("body_h", None),
                      ("paste_hole_dia", 0.0)):
        try:
            img = common.panel(kind, ctx, {}, val, {}, "T")
            check_exact("panel: %s 不崩" % kind,
                        img is not None and img.size[0] > 0, True)
        except Exception as e:                          # noqa: BLE001
            check_exact("panel: %s 不崩" % kind, False,
                        "%s: %s" % (type(e).__name__, e))


def test_drc_pad_clearance_rule():
    """焊盘短路间距规则必须从 spec（要求侧）推出。

    0.4mm 节距的 WLCSP：相邻球心 0.35mm、焊盘 0.22mm，边到边只剩 0.13mm。
    KiCad 默认的 0.2mm 网络间距拿上去跑，会报 79 项“间距违规” ——
    而那是一族封装根本达不到的要求，那 79 项一个缺陷都不是。

    关键：下界取自 **spec** 而不是被测封装自己。取自封装自己的话
    规则永远成立，DRC 就退化成空跑。
    """
    from core import spec as S
    grid = {"rows": [
        {"kind": "dia", "nominal": 0.218},
        {"kind": "diag_min", "nominal": 0.350},
        {"kind": "pitch", "nominal": 0.420},
    ]}
    # 最近的两个球是斜向的 -> 用 diag_min，不是 pitch
    check("drc/pad_gap: WLCSP 取斜向球距", S.min_pad_gap(grid), 0.350 - 0.218, 1e-9)

    per = {"rows": [
        {"kind": "lead_width", "nominal": 0.30},
        {"kind": "lead_pitch", "nominal": 0.50},
    ]}
    check("drc/pad_gap: 引脚族取同侧节距-脚宽",
          S.min_pad_gap(per), 0.500 - 0.300, 1e-9)

    check_exact("drc/pad_gap: 没有可用的 kind -> None",
                S.min_pad_gap({"rows": [{"kind": "body_w", "nominal": 3.0}]}),
                None)
    check_exact("drc/pad_gap: rows 缺失 -> None", S.min_pad_gap({}), None)

    # 规则文件：值要比 spec 算出来的略低（KiCad 判 actual < min，浮点末位
    # 会让正好相等的情况误报），但低得不能多，否则放过真问题。
    import tempfile
    d = tempfile.mkdtemp()
    p = os.path.join(d, "board.kicad_pcb")
    open(p, "w").write("")
    dru = S.write_dru(p, 0.130)
    check_exact("drc/dru: 写在 <board>.kicad_dru（KiCad 自动加载）",
                os.path.basename(dru), "board.kicad_dru")
    txt = open(dru, encoding="utf-8").read()
    check("drc/dru: 约束写成 0.125mm（0.130 - 5um）",
          "(min 0.125mm)" in txt, True)
    check("drc/dru: 只限 Pad-Pad，不放过其他间距",
          "A.Type == 'Pad' && B.Type == 'Pad'" in txt, True)


TESTS = [
    ("core: gerber parser", test_gerber_parser),
    ("core: EP-buried pads", test_ep_buried_pads),
    ("core: verdict states", test_verdict_states),
    ("core: no-crash panels", test_panels_do_not_crash),
    ("drc: violation classes", test_drc_classification),
    ("drc: pad clearance rule", test_drc_pad_clearance_rule),
    ("core: pad classification", test_classify),
    ("core: family dispatch", test_family_dispatch),
    ("family: grid_array (WLCSP)", test_grid_array),
    ("family: peripheral (QFN-32)", test_peripheral_qfn),
    ("family: peripheral (LQFP-48)", test_peripheral_qfp),
    ("family: peripheral (SOT-23)", test_peripheral_sot23),
    ("family: peripheral (SOIC-8)", test_peripheral_soic8),
    ("family: chip (0603)", test_chip_0603),
    ("pad shape / panels", test_pad_shape_and_panels),
]


def main():
    pat = sys.argv[1].lower() if len(sys.argv) > 1 else ""
    failed = 0
    for name, fn in TESTS:
        if pat and pat not in name.lower():
            continue
        before = len(RESULTS)
        try:
            fn()
            status = "ok"
        except Exception:                              # noqa: BLE001
            status = "CRASH"
            traceback.print_exc()
        bad = [r for r in RESULTS[before:] if not r[0]]
        if status == "ok" and not bad:
            print("PASS  %-30s (%d checks)" % (name, len(RESULTS) - before))
        else:
            failed += 1
            print("FAIL  %-30s %s" % (name, status))
            for _, what, got, want in bad:
                print("        %-42s measured %s  want %s" % (what, got, want))
    total = len(RESULTS)
    nbad = len([r for r in RESULTS if not r[0]])
    if SKIPPED:
        print("\n跳过: %s   （没装 Pillow —— 量测用例不受影响，"
              "画图路径未验证）" % ", ".join(sorted(set(SKIPPED))))
    print("\n%d checks, %d failed, %d test group(s) failed"
          % (total, nbad, failed))
    return 1 if (failed or nbad) else 0


if __name__ == "__main__":
    sys.exit(main())
