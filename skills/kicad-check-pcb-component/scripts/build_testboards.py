#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_testboards.py - build tiny real boards from installed KiCad library
footprints (one package per board), export their Gerbers and write a spec.json
for each, so every family can be verified against real manufacturing data.

    python build_testboards.py [outdir]         # default: ./_testdata
    python build_testboards.py --run            # build + measure all of them

Requires KiCad (for kicad-cli) and its footprint libraries.  This is the
"integration test" that complements selftest.py (which needs no CAD at all).
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
KICAD_SHARE = r"C:\Program Files\KiCad\10.0\share\kicad"
KICAD_CLI = r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe"

# package -> (kicad footprint, family, board size)
CASES = {
    "qfn32":  ("Package_DFN_QFN.pretty/QFN-32-1EP_5x5mm_P0.5mm_EP3.1x3.1mm.kicad_mod",
               "peripheral", (30.0, 24.0)),
    "lqfp48": ("Package_QFP.pretty/LQFP-48_7x7mm_P0.5mm.kicad_mod",
               "peripheral", (30.0, 24.0)),
    "sot23":  ("Package_TO_SOT_SMD.pretty/SOT-23.kicad_mod",
               "peripheral", (20.0, 16.0)),
    "soic8":  ("Package_SO.pretty/SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod",
               "peripheral", (20.0, 16.0)),
    "r0603":  ("Resistor_SMD.pretty/R_0603_1608Metric.kicad_mod",
               "chip", (20.0, 16.0)),
}

LAYERS = """	(layers
		(0 "F.Cu" signal)
		(2 "B.Cu" signal)
		(9 "F.Adhes" user "F.Adhesive")
		(11 "B.Adhes" user "B.Adhesive")
		(13 "F.Paste" user)
		(15 "B.Paste" user)
		(5 "F.SilkS" user "F.Silkscreen")
		(7 "B.SilkS" user "B.Silkscreen")
		(1 "F.Mask" user)
		(3 "B.Mask" user)
		(17 "Dwgs.User" user "User.Drawings")
		(19 "Cmts.User" user "User.Comments")
		(21 "Eco1.User" user "User.Eco1")
		(23 "Eco2.User" user "User.Eco2")
		(25 "Edge.Cuts" user)
		(27 "Margin" user)
		(31 "F.CrtYd" user "F.Courtyard")
		(29 "B.CrtYd" user "B.Courtyard")
		(35 "F.Fab" user)
		(33 "B.Fab" user)
	)"""


def lib_footprint(rel):
    p = os.path.join(KICAD_SHARE, "footprints", rel)
    if not os.path.exists(p):
        cands = glob.glob(os.path.join(KICAD_SHARE, "footprints", "**",
                                       os.path.basename(rel)), recursive=True)
        if not cands:
            raise SystemExit("footprint not found: " + rel)
        p = cands[0]
    return p


def embed_footprint(mod_path, at):
    txt = open(mod_path, encoding="utf-8").read()
    for pat in (r"\n\t\(version [^)]*\)", r"\n\t\(generator [^)]*\)",
                r"\n\t\(generator_version [^)]*\)", r"\n\t\(embedded_fonts no\)"):
        txt = re.sub(pat, "", txt)
    txt = txt.replace('\t(layer "F.Cu")\n',
                      '\t(layer "F.Cu")\n\t(at %g %g)\n' % at, 1)
    return txt


def write_pcb(path, mod_path, at, board):
    w, h = board
    body = embed_footprint(mod_path, at)
    edge = "".join(
        '\t(gr_line\n\t\t(start %g %g)\n\t\t(end %g %g)\n'
        '\t\t(stroke\n\t\t\t(width 0.1)\n\t\t\t(type solid)\n\t\t)\n'
        '\t\t(layer "Edge.Cuts")\n\t\t(uuid "00000000-0000-0000-0000-%012d")\n\t)\n'
        % (x1, y1, x2, y2, 100 + i)
        for i, (x1, y1, x2, y2) in enumerate(
            [(0, 0, w, 0), (w, 0, w, h), (w, h, 0, h), (0, h, 0, 0)]))
    open(path, "w", encoding="utf-8", newline="\n").write(
        '(kicad_pcb\n\t(version 20260206)\n\t(generator "pcbnew")\n'
        '\t(generator_version "10.0")\n\t(general\n\t\t(thickness 1.6)\n'
        '\t\t(legacy_teardrops no)\n\t)\n\t(paper "User" %g %g)\n%s\n'
        '\t(setup\n\t\t(pad_to_mask_clearance 0)\n\t)\n%s\t(embedded_fonts no)\n)\n'
        % (w, h, LAYERS, body + edge))


def pads_of(mod_path):
    """read the numbered pads straight out of the .kicad_mod.

    The expected geometry of the test - an independent code path from the
    Gerber reader, so a mistake in core/gerber.py shows up as a FAIL here.
    Unnumbered pads (the KiCad QFN footprints add a via array on the exposed
    pad) are skipped, as is anything sitting inside the exposed pad.
    """
    txt = open(mod_path, encoding="utf-8").read()
    out = []
    pat = (r'\(pad "([^"]*)" (\S+) (\S+)\s*\n\s*\(at (-?[\d.]+) (-?[\d.]+)'
           r'(?: -?[\d.]+)?\)\s*\n\s*\(size ([\d.]+) ([\d.]+)\)')
    for m in re.finditer(pat, txt):
        if not m.group(1):                       # unnumbered = via / EP patch
            continue
        out.append((float(m.group(4)), float(m.group(5)),
                    float(m.group(6)), float(m.group(7))))
    if not out:
        return out
    areas = sorted(p[2] * p[3] for p in out)
    med = areas[len(areas) // 2] or 1e-9
    big = max(out, key=lambda p: p[2] * p[3])
    if big[2] * big[3] >= 4 * med:               # drop pads inside the EP
        out = [p for p in out if p is big or not (
            abs(p[0] - big[0]) <= big[2] / 2 + 1e-6
            and abs(p[1] - big[1]) <= big[3] / 2 + 1e-6)]
    return out


def make_case(name, out):
    rel, family, board = CASES[name]
    mod = lib_footprint(rel)
    d = os.path.join(out, name)
    os.makedirs(os.path.join(d, "gerber"), exist_ok=True)
    pcb = os.path.join(d, name + ".kicad_pcb")
    write_pcb(pcb, mod, (board[0] / 2, board[1] / 2), board)
    if os.path.exists(KICAD_CLI):
        subprocess.run([KICAD_CLI, "pcb", "export", "gerbers", "-o",
                        os.path.join(d, "gerber"), "--layers",
                        "F.Cu,F.Paste,F.Mask,F.SilkS,F.Fab,Edge.Cuts",
                        "--no-x2", "--no-netlist", pcb], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([KICAD_CLI, "pcb", "export", "drill", "-o",
                        os.path.join(d, "gerber"), "--format", "excellon",
                        "--excellon-separate-th", pcb], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for f in glob.glob(os.path.join(d, "gerber", "*PTH.drl")):
            os.remove(f)
    write_spec(name, d, family, mod, board)
    return d


def write_spec(name, d, family, mod, board):
    """expectations: package level numbers from the footprint name, pad level
    numbers read straight out of the .kicad_mod (so the test is not circular -
    the driver measures the *Gerber*, not the footprint file)"""
    pads = pads_of(mod)
    w, h = board
    rows = [
        {"symbol": "A", "requirement": "-", "kind": "na",
         "note": "高度类尺寸，2D 文件无法测量"},
    ]
    if family == "chip":
        pl = sorted(p[2] for p in pads)
        pw = sorted(p[3] for p in pads)
        ctr = sorted(p[0] for p in pads)
        rows += [
            {"symbol": "N", "requirement": "2", "kind": "count", "nominal": 2},
            {"symbol": "e", "requirement": "%.3f" % (ctr[-1] - ctr[0]),
             "kind": "pad_span", "nominal": round(ctr[-1] - ctr[0], 4)},
            {"symbol": "b", "requirement": "%.3f" % min(pw), "kind": "pad_l",
             "nominal": round(min(pl), 4)},
            {"symbol": "a", "requirement": "%.3f" % min(pw), "kind": "pad_w",
             "nominal": round(min(pw), 4)},
            {"symbol": "L", "requirement": "-", "kind": "body_long"},
            {"symbol": "W", "requirement": "-", "kind": "body_short"},
        ]
    else:
        xs = [p[0] for p in pads]
        ys = [p[1] for p in pads]
        ws = [p[2] for p in pads]
        hs = [p[3] for p in pads]
        ep = [p for p in pads if p[2] * p[3] > 4 * (sorted(w * h for w, h in
                                                        zip(ws, hs))[len(pads) // 2])]
        leads = [p for p in pads if p not in ep]
        pitch = _pitch(leads or pads)
        lead_w = min(min(p[2], p[3]) for p in (leads or pads))
        lead_l = max(max(p[2], p[3]) for p in (leads or pads))
        rows += [
            {"symbol": "D", "requirement": "-", "kind": "body_w"},
            {"symbol": "E", "requirement": "-", "kind": "body_h"},
            {"symbol": "e", "requirement": "%.3f" % pitch, "kind": "lead_pitch",
             "nominal": round(pitch, 4)},
            {"symbol": "N", "requirement": "%d" % (len(pads) - len(ep)),
             "kind": "lead_count", "nominal": len(pads) - len(ep)},
            {"symbol": "b", "requirement": "%.3f" % lead_w, "kind": "lead_width",
             "nominal": round(lead_w, 4)},
            {"symbol": "L", "requirement": "%.3f" % lead_l, "kind": "lead_length",
             "nominal": round(lead_l, 4)},
            {"symbol": "e_span", "requirement": "%.3f" % (max(xs) - min(xs)),
             "kind": "pad_span_x", "nominal": round(max(xs) - min(xs), 4)},
        ]
        if ep:
            rows += [
                {"symbol": "D2", "requirement": "%.3f" % ep[0][2], "kind": "ep_x",
                 "nominal": round(ep[0][2], 4)},
                {"symbol": "E2", "requirement": "%.3f" % ep[0][3], "kind": "ep_y",
                 "nominal": round(ep[0][3], 4)},
            ]
    spec = {
        "title": "元器件尺寸测量表（测试例：%s）" % name,
        "component": "KiCad library footprint %s" % os.path.basename(mod),
        "family": family,
        "output": "%s_report.xlsx" % name,
        "gerber_dir": "gerber",
        "rows": rows,
        "notes": ["本测试例由 build_testboards.py 自动生成：板子是真实 .kicad_pcb，"
                  "Gerber 由 kicad-cli 导出，要求值取自封装名/封装文件。"],
    }
    open(os.path.join(d, name + ".spec.json"), "w", encoding="utf-8",
         newline="\n").write(json.dumps(spec, ensure_ascii=False, indent=2))
    return spec


def _pitch(pads):
    """lead pitch: smallest repeated centre spacing *within one side*"""
    xs = [p[0] for p in pads]
    ys = [p[1] for p in pads]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    groups = {}
    for p in pads:
        d = {"L": p[0] - x0, "R": x1 - p[0], "T": p[1] - y0, "B": y1 - p[1]}
        groups.setdefault(min(d, key=d.get), []).append(p)
    out = []
    for side, g in groups.items():
        axis = 1 if side in ("L", "R") else 0
        vals = sorted(round(q[axis], 4) for q in g)
        out += [round(v2 - v1, 4) for v1, v2 in zip(vals, vals[1:])]
    out = [v for v in out if v > 0.05]
    if not out:
        return 0.0
    return min(set(out), key=lambda v: (out.count(v) * -1, v))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir", nargs="?", default=os.path.join(HERE, "_testdata"))
    ap.add_argument("--run", action="store_true", help="also measure each case")
    ap.add_argument("--3d", dest="three_d", action="store_true",
                    help="also run DRC + 3D fit + render for each case")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    dirs = []
    for name in CASES:
        if a.only and a.only != name:
            continue
        d = make_case(name, a.outdir)
        dirs.append((name, d))
        print("built %-8s -> %s" % (name, d))
    if a.run:
        print()
        for name, d in dirs:
            spec = os.path.join(d, name + ".spec.json")
            rc = subprocess.run([sys.executable,
                                 os.path.join(HERE, "measure_component.py"), spec],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            out = rc.stdout.decode("utf-8", "replace")
            head = [l for l in out.splitlines() if l.startswith("family")
                    or l.startswith("report")]
            bad = [l for l in out.splitlines() if " NG " in l or l.rstrip().endswith("NG")]
            print("== %s ==" % name)
            for l in head:
                print("  ", l)
            print("   NG rows:", len(bad) or "none")
            for l in bad:
                print("      ", l)
    if a.three_d:
        print("\n--- 附加通路：DRC / 渲染 / 3D 贴合 / 引脚契约 ---")
        for name, d in dirs:
            pcb = os.path.join(d, name + ".kicad_pcb")
            mod = CASES[name][0]
            mod = os.path.join(KICAD_SHARE, "footprints", mod)
            print("== %s ==" % name)
            r = subprocess.run([sys.executable, os.path.join(HERE, "drc.py"), pcb],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            txt = r.stdout.decode("utf-8", "replace")
            line = [l for l in txt.splitlines() if l.startswith("violations")]
            print("   drc     rc=%d %s" % (r.returncode, line[0] if line else ""))
            r = subprocess.run([sys.executable, os.path.join(
                                    os.path.dirname(HERE), "..", "..", "shared",
                                    "pinmap.py"),
                                "--footprint", mod],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            print("   pinmap  rc=%d" % r.returncode)
            if os.path.exists(mod):
                r = subprocess.run([sys.executable, os.path.join(HERE, "fit3d.py"),
                                    mod, os.path.join(d, "fit3d")],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                txt = r.stdout.decode("utf-8", "replace")
                mm = [l for l in txt.splitlines() if l.startswith("model ref")]
                print("   fit3d   rc=%d %s" % (r.returncode, mm[0] if mm else ""))


if __name__ == "__main__":
    main()
