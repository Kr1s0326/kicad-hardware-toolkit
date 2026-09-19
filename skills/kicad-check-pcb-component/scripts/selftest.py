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


TESTS = [
    ("core: gerber parser", test_gerber_parser),
    ("core: pad classification", test_classify),
    ("core: family dispatch", test_family_dispatch),
    ("family: grid_array (WLCSP)", test_grid_array),
    ("family: peripheral (QFN-32)", test_peripheral_qfn),
    ("family: peripheral (LQFP-48)", test_peripheral_qfp),
    ("family: peripheral (SOT-23)", test_peripheral_sot23),
    ("family: peripheral (SOIC-8)", test_peripheral_soic8),
    ("family: chip (0603)", test_chip_0603),
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
