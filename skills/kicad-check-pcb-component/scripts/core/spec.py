#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.spec - read spec.json, locate the manufacturing files, build the
measurement context that every family works on.

The context is package agnostic; a family adds its own derived geometry in
`family.prepare(ctx)`.
"""

import glob
import json
import math
import os
import sys

from core import gerber as G

__all__ = ["load_spec", "build_context", "verdict", "validate_rows",
           "FILE_ALIASES", "min_pad_gap", "DRU_TEMPLATE"]

# 自定义 DRC 规则。KiCad 会自动加载与 .kicad_pcb 同名的 .kicad_dru。
DRU_TEMPLATE = """(version 1)
# 由 kicad-check-pcb-component 自动生成，**不是**被测封装的一部分。
#
# 为什么需要它：短路间距规则的默认值是 0.2mm，那是给 1.27mm 节距的
# 普通封装用的。0.4mm 节距的 WLCSP，相邻球心 0.35mm、焊盘 0.22mm，
# 边到边只有 0.13mm —— 拿默认值去跑，会报 79 项“间距违规”，
# 但那些一个缺陷都不是，是**规则的默认值不适合这一族封装**。
#
# 下界取自 spec（即手册图纸的要求），不是取自被测封装自己：
# 若取自封装自己，规则永远通过，DRC 就退化成空跑。
(rule "pad-to-pad clearance (from spec)"
	(constraint clearance (min %smm))
	(condition "A.Type == 'Pad' && B.Type == 'Pad'"))
"""


def min_pad_gap(spec):
    """从 spec 的**要求值**算出焊盘之间最小允许的铜间隔（mm）。

    球阵族：min(eS1 斜向球距, eD 球栅距) - Øb
    引脚族：e 节距 - b 脚宽

    算不出来就返回 None（不写规则文件，用 KiCad 的默认值）。
    """
    by = {}
    for r in spec.get("rows") or []:
        if r.get("nominal") is not None and r.get("kind"):
            by.setdefault(r["kind"], r["nominal"])
    try:
        if "dia" in by:                       # 球阵：最近的两个球是斜向的
            near = [v for k, v in (("diag_min", by.get("diag_min")),
                                   ("pitch", by.get("pitch"))) if v]
            size = float(by["dia"])
        elif "lead_width" in by:               # 引脚式：最近的两个脚在同侧
            near = [float(v) for k, v in (("lead_pitch", by.get("lead_pitch")),)
                    if v]
            size = float(by["lead_width"])
        else:
            return None
        if not near:
            return None
        return min(float(v) for v in near) - size
    except (TypeError, ValueError):
        return None

FILE_ALIASES = {
    "cu":    ["f_cu", "f.cu", "gtl", "top.gbr", "top_copper", "_cu."],
    "silk":  ["f_silkscreen", "f_silk", "f.silks", "gto", "silkscreen", "legend"],
    "paste": ["f_paste", "f.paste", "gtp", "paste", "stencil", "cream"],
    "mask":  ["f_mask", "f.mask", "gts", "soldermask"],
    "edge":  ["edge_cuts", "edge.cuts", "gm1", "outline", "profile", "board"],
    "fab":   ["f_fab", "f.fab", "assembly", "fab"],
    "drill": ["npth", "drill", ".drl", "drl"],
}
_NOT_GERBER = (".png", ".jpg", ".jpeg", ".svg", ".pdf", ".json", ".txt", ".md",
               ".kicad_pcb", ".kicad_mod", ".csv", ".xlsx")


def write_dru(board_path, gap):
    """把自定义规则写到 <board>.kicad_dru（与 board.kicad_pcb 同名）。

    gap 减 5µm：KiCad 判的是 actual < min，而 actual 是浮点算出来的，
    正好相等时可能因为末位误差报违规。5µm 远小于制造公差，不会放过真问题。
    """
    dru = os.path.splitext(board_path)[0] + ".kicad_dru"
    with open(dru, "w", encoding="utf-8", newline="\n") as f:
        f.write(DRU_TEMPLATE % ("%.3f" % (float(gap) - 0.005)))
    return dru


def load_spec(path):
    path = os.path.abspath(path)
    with open(path, encoding="utf-8") as fh:
        spec = json.load(fh)
    spec["_path"] = path
    spec["_root"] = os.path.dirname(path)
    if "rows" not in spec:
        raise SystemExit("spec has no 'rows'")
    return spec


def find_file(folder, key, aliases=None):
    if not folder or not os.path.isdir(folder):
        return None
    names = sorted(os.listdir(folder))
    for pat in (aliases or FILE_ALIASES[key]):
        for n in names:
            if pat in n.lower() and not n.lower().endswith(_NOT_GERBER):
                return os.path.join(folder, n)
    return None


def _resolve(spec, key):
    root = spec["_root"]
    f = spec.get("files", {}).get(key)
    if not f:
        folder = spec.get("gerber_dir", "gerber")
        if not os.path.isabs(folder):
            folder = os.path.join(root, folder)
        return find_file(folder, key)
    if not os.path.isabs(f):
        f = os.path.join(root, f)
    return f if os.path.exists(f) else (glob.glob(f) or [None])[0]


def body_rect(gerber, centre, min_len=0.5, radius=4.0, rel=0.4, tol=0.08):
    """Find the package body outline on a silk / fab layer.

    A footprint layer also carries text (reference, value) whose strokes can be
    as long as a small package's body line, so a pure length threshold is not
    enough.  The body is the largest *closed rectangle*: the two longest
    horizontal segments define the top/bottom, and it is accepted only when
    vertical segments close the left and right side.  Falls back to the
    bounding box of the long segments when no rectangle is found.
    """
    if not gerber:
        return None
    try:
        ap, pr = G.parse_gerber(gerber, flip_y=True)
    except Exception as e:                                  # noqa: BLE001
        # 以前这里是裸的 `except Exception: return None` —— 于是“Gerber 解析
        # 不动”和“图纸里真没有本体”变成同一个结果，下游只看得到“本体测不到”，
        # 没人知道是解析器挂了。解析失败必须出声。
        print("  [warn] 本体外框：%s 解析失败（%s: %s）—— 本体尺寸将报“待测”"
              % (os.path.basename(gerber), type(e).__name__, e), file=sys.stderr)
        return None
    seg = []
    for x0, y0, x1, y1, ln in G.lines(pr, min_len):
        if (math.dist((x0, y0), centre) <= radius
                and math.dist((x1, y1), centre) <= radius):
            seg.append((x0, y0, x1, y1, ln))
    if len(seg) < 2:
        return None

    hz = sorted([s for s in seg if abs(s[1] - s[3]) <= tol], key=lambda s: -s[4])
    vt = [s for s in seg if abs(s[0] - s[2]) <= tol]
    if len(hz) >= 2 and len(vt) >= 2:
        a, b = hz[0], hz[1]
        ya, yb = a[1], b[3]
        y0, y1 = min(ya, yb), max(ya, yb)
        x0 = min(a[0], a[2], b[0], b[2])
        x1 = max(a[0], a[2], b[0], b[2])
        if x1 - x0 > 2 * tol and y1 - y0 > 2 * tol:
            ok = 0
            for side in (x0, x1):
                for s in vt:
                    sx = (s[0] + s[2]) / 2
                    sy0, sy1 = min(s[1], s[3]), max(s[1], s[3])
                    if (abs(sx - side) <= 0.15
                            and min(sy1, y1) - max(sy0, y0) >= 0.6 * (y1 - y0)):
                        ok += 1
                        break
            if ok >= 2:
                return x0, y0, x1, y1

    lmax = max(s[4] for s in seg)                  # fallback: long segments
    keep = [s for s in seg if s[4] >= max(min_len, rel * lmax)]
    if len(keep) < 2:
        return None
    xs = [v for s in keep for v in (s[0], s[2])]
    ys = [v for s in keep for v in (s[1], s[3])]
    return min(xs), min(ys), max(xs), max(ys)


def build_context(spec):
    """-> ctx dict (pads, body, board, drills, paste, origin, files)

    前置：spec 里要有 `_root`（load_spec() 会加）。直接拿一个裸 dict 调这里
    以前会 KeyError: '_root' —— 函数签名看不出这个隐含前置条件，所以在这里
    兜一下：没有就按 spec 自带的 _path 或当前目录算。
    """
    if "_root" not in spec:
        spec = dict(spec)
        spec["_root"] = os.path.dirname(os.path.abspath(spec.get("_path") or "."))
    ctx = {"files": {k: _resolve(spec, k) for k in FILE_ALIASES}}
    f = ctx["files"]
    if not f["cu"]:
        # 注意：SystemExit("字符串") 的退出码恒为 1，无法区分"输入错"和"有 NG 行"。
        # 这里先把话说清楚，再用数字码退出。
        print("找不到铜层 Gerber。\n"
              "  gerber_dir = %s\n"
              "  修法：先用 kicad-cli pcb export gerbers 导出，或把 spec 里的\n"
              "       gerber_dir 指向已导出的目录；也可以直接跑\n"
              "       check_footprint.py，它会自己建板并导出。"
              % (spec.get("gerber_dir") or "(未设置)"), file=sys.stderr)
        raise SystemExit(2)

    ap, pr = G.parse_gerber(f["cu"], flip_y=True)
    pads = G.flash_pads(ap, pr)
    pads = G.filter_pads(pads, spec.get("pad_filter", "auto"))
    if not pads:
        raise SystemExit("no pads after filtering - check pad_filter / files.cu")
    ctx["pads"] = pads

    xs = [p[0] for p in pads]
    ys = [p[1] for p in pads]
    acentre = ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
    # the package outline: prefer the fab layer (always a clean rectangle and
    # never carries the board silkscreen text), fall back to the silkscreen.
    # The search radius grows with the pad array so a big QFP is still found.
    radius = spec.get("body_search") or max(4.0, 0.9 * (max(xs) - min(xs)),
                                            0.9 * (max(ys) - min(ys)))
    rect = None
    for key in ("fab", "silk"):
        rect = body_rect(f.get(key), acentre, spec.get("body_min_len", 0.5),
                         radius, spec.get("body_rel_len", 0.4))
        if rect:
            break
    ctx["body"] = rect
    ctx["origin"] = (((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)
                     if rect else acentre)

    ctx["drills"] = G.parse_drill(f["drill"], flip_y=True) if f["drill"] else []
    if f["edge"]:
        _, pr = G.parse_gerber(f["edge"], flip_y=True)
        segs = [(p[2], p[3], p[4], p[5]) for p in pr if p[0] == "line"]
        ctx["edge_segs"] = segs
        if segs:
            ex = [v for s in segs for v in (s[0], s[2])]
            ey = [v for s in segs for v in (s[1], s[3])]
            ctx["board"] = (min(ex), min(ey), max(ex), max(ey))
    if f["paste"]:
        ap2, pr2 = G.parse_gerber(f["paste"], flip_y=True)
        circles = G.stroked_circles(pr2, ap2)
        big = max((2 * c[2] for c in circles), default=0.0)
        for shape, params in ap2.values():
            if params and shape in ("C", "P"):
                big = max(big, params[0])
        ctx["paste_circles"] = circles
        ctx["paste_aperture"] = big
    return ctx


def validate_rows(rows):
    """每行必须带一个可比较的“要求”，否则报错退出。

    背景：以前 verdict() 在拿不到 nominal/min/max 时直接 return "PASS"。
    后果是 spec 里字段名写错、值写成字符串、或者整行漏了 —— 全部**静默通过**。
    一个检查工具静默放行，比它没跑还坏。

    哪些行不需要要求值：
      * kind == "na"（Z 向尺寸，2D 量不了，本来就只报待测）
      * 显式写了 na_result（同样是不参与判定的行）
    其余每一行必须至少有 nominal / min+max 其中之一。
    """
    bad = []
    for i, r in enumerate(rows):
        kind = r.get("kind")
        if not kind:
            bad.append((i, r.get("symbol", "?"), "没有 kind 字段"))
            continue
        if kind == "na" or r.get("na_result"):
            continue
        has = (r.get("nominal") is not None
               or (r.get("min") is not None and r.get("max") is not None))
        if not has:
            bad.append((i, r.get("symbol", "?"),
                        "kind=%s 但既没有 nominal，也没有 min+max" % kind))
        else:
            for k in ("nominal", "min", "max", "tol"):
                v = r.get(k)
                if v is not None and not isinstance(v, (int, float)):
                    bad.append((i, r.get("symbol", "?"),
                                "%s 不是数字（%r）—— 写成字符串了？" % (k, v)))
    if not bad:
        return
    lines = "\n".join("  第 %d 行 %-8s %s" % b for b in bad)
    # SystemExit("字符串") 的退出码恒为 1，而 1 在本工具里表示“有 NG 行”。
    # 这是输入错，必须是 2 —— 先用 print 把话说清楚，再用数字码退出。
    print("spec 里有 %d 行的“要求”不可用：\n%s\n\n"
          "每一行必须给出可比较的阈值：nominal，或 min+max。\n"
          "（kind 写 `na` 的行例外 —— 它们本来就不参与判定。）\n"
          "没有阈值的行无法判定，而“无法判定”绝不能当成通过。"
          % (len(bad), lines), file=sys.stderr)
    raise SystemExit(2)


def verdict(kind, row, value, tol, count_kinds=()):
    """PASS / NG / 待测 for one measured row"""
    if kind == "na" or value is None:
        return row.get("na_result", "待测")
    if kind in count_kinds or row.get("compare") == "count":
        return "PASS" if int(round(value)) == int(row["nominal"]) else "NG"
    lo, hi = row.get("min"), row.get("max")
    if lo is not None and hi is not None:
        return "PASS" if lo - 1e-9 <= value <= hi + 1e-9 else "NG"
    nom = row.get("nominal")
    if nom is None:
        # 正常情况下 validate_rows() 已经把它拦在门外了。走到这里说明
        # 有人绕过了校验 —— 宁可报“待测”也不能报 PASS。
        print("  [warn] 行 %r 没有可比较的要求值，判“待测”（请补 nominal/min/max）"
              % row.get("symbol", "?"))
        return "待测"
    return "PASS" if abs(value - nom) <= row.get("tol", tol) + 1e-9 else "NG"


def fmt(v, kind=None):
    if v is None:
        return "N/A"
    if kind in ("count", "matrix_cols", "matrix_rows", "hole_count",
                "leads_per_side", "side_count"):
        return "%d" % round(v)
    if abs(v) >= 100:
        return "%.1f" % v
    if abs(v) >= 10:
        return "%.2f" % v
    if abs(v) >= 1:
        return "%.4f" % v
    return "%.4f" % v
