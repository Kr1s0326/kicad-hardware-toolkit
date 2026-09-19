#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
symbol_lint.py - 符号的"规矩"检查（纯几何，不需要 KiCad）。

校验符号有两类完全不同的错：

    类 A  和手册对不上     ->  pin 号 / 名字 / 电气类型   -> 靠网表 + ERC + PDF 比
    类 B  和 house style 对不上 ->  间距 / 分组 / 栅格 / 本体大小 / 名字溢出
                              -> 手册根本没规定，只能查规范，就是本文件

类 B 的错误有个特点：**画出来都很好看，数值上也"没错"**，只有量规矩才抓得到。
所以它必须独立成一层。

能查到什么
----------
    duplicate      引脚号重复
    off_grid       引脚端点没落在连接栅格上（会导致连不上线）
    pin_length     引脚长度不一致
    pitch          同侧基础间距 < 规定值（默认 200 mil）
    group_gap      不同功能组之间的间距 < 规定值（默认 400 mil）
    multiple       间距不是基础间距的整数倍（出现"半格"错位）
    body_margin    本体离引脚太近
    overflow       左右两侧的引脚名挤在一起 / 超出本体
    no_power       没有 power_in 引脚
    fields         位号/Value 压在本体内

分组是**推**出来的，不是读出来的
--------------------------------
脚本只能看出"这里有一道更大的空隙"，看不出"VBUS 和 IN+ 是不是不同功能"。
所以它会把推断出的分组打出来，**需要人确认这个分组对不对**。
语义分组是查不了的，别假装能查。

CLI
---
    python symbol_lint.py <lib.kicad_sym> [--symbol NAME]
                          [--min-pitch 200] [--group-gap 400] [--grid 50]
                          [--json out.json]
    单位是 mil（200 mil = 5.08 mm）。
"""

import argparse
import json
import re
import sys

import os

HERE = os.path.dirname(os.path.abspath(__file__))
SHARED = os.path.normpath(os.path.join(HERE, "..", "..", "..", "shared"))
if SHARED not in sys.path:
    sys.path.insert(0, SHARED)
from cli import guard                             # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:                                       # noqa: BLE001
    pass

MIL = 0.0254


# ------------------------------------------------------------------ 解析
def parse_symbol(sym_path, sym_name=None):
    txt = open(sym_path, encoding="utf-8").read()
    tops = re.findall(r'\n\t\(symbol "([^"]+)"', txt)
    if not tops:
        raise SystemExit("no symbol in " + sym_path)
    if sym_name is None:
        cands = [n for n in tops if not re.search(r"_\d+_\d+$", n)]
        sym_name = cands[0] if cands else tops[0]
    start = txt.index('\n\t(symbol "%s"' % sym_name)
    nxt = re.compile(r'\n\t\(symbol "(?!%s_)' % re.escape(sym_name)).search(
        txt, start + 1)
    blk = txt[start:nxt.start() if nxt else len(txt)]

    m = re.search(r'\(rectangle\s*\n\s*\(start (-?[\d.]+) (-?[\d.]+)\)\s*\n'
                  r'\s*\(end (-?[\d.]+) (-?[\d.]+)\)', blk)
    body = None
    if m:
        x0, y0, x1, y1 = (float(g) for g in m.groups())
        body = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

    pins = []
    for pm in re.finditer(
            r'\(pin (\w+) \w+\s*\n\s*\(at (-?[\d.]+) (-?[\d.]+) (\d+)\)\s*\n'
            r'\s*\(length ([\d.]+)\)\s*\n\s*\(name "([^"]+)"[\s\S]*?'
            r'\(number "([^"]+)"', blk):
        etype, x, y, rot, ln, name, num = pm.groups()
        pins.append({"etype": etype, "x": float(x), "y": float(y),
                     "rot": int(rot), "length": float(ln),
                     "name": name.replace("~{", "").replace("}", ""),
                     "raw_name": name, "number": num})

    # 位号 / Value 属性位置
    fields = {}
    for fm in re.finditer(r'\(property "(Reference|Value)" "([^"]*)"\s*\n'
                          r'\s*\(at (-?[\d.]+) (-?[\d.]+) (-?\d+)\)', blk):
        fields[fm.group(1)] = (fm.group(2), float(fm.group(3)), float(fm.group(4)))
    return sym_name, body, pins, fields


def side_of(p):
    """引脚挂在哪一边。

    rot 是**引脚从连接点指向本体的方向**，不是它所在的边：
        rot 0   -> 朝右伸 -> 挂在本体左边
        rot 180 -> 朝左伸 -> 挂在本体右边
        rot 90  -> 朝上伸 -> 挂在本体下边
        rot 270 -> 朝下伸 -> 挂在本体上边
    一开始写反了，导致左右颠倒、间距全负。
    """
    return {0: "left", 180: "right", 90: "bottom", 270: "top"}.get(p["rot"], "?")


def root_of(p):
    """引脚靠本体那一端的坐标（连接点 + 长度 朝本体方向）"""
    if p["rot"] == 0:
        return (p["x"] + p["length"], p["y"])
    if p["rot"] == 180:
        return (p["x"] - p["length"], p["y"])
    if p["rot"] == 90:
        return (p["x"], p["y"] + p["length"])
    if p["rot"] == 270:
        return (p["x"], p["y"] - p["length"])
    return (p["x"], p["y"])


# ------------------------------------------------------------------ 检查
def lint(sym_path, sym_name=None, min_pitch_mil=200.0, group_gap_mil=400.0,
         grid_mil=50.0, font=1.27, declared=None):
    """declared: {'left': [['VBUS'], ['IN+','IN-']], 'right': [...], ...}

    为什么分组必须由外部给定
    -------------------------
    “组间 400 mil” 这条规则**几何上不可判**。例：左侧原为 VBUS(12.7) |
    IN+(2.54) IN-(-2.54)，把 IN+ 提到 7.62 之后，几何上变成
    {VBUS, IN+} | {IN-} —— 依然满足“组内 200 / 组间 400”。
    光看坐标，工具无法区分“符合规范的另一个分组”和“你搭错了”。
    所以分组是**输入**，不是推断出来的；推断结果只用来对账。
    """
    name, body, pins, fields = parse_symbol(sym_path, sym_name)
    grid = grid_mil * MIL
    min_pitch = min_pitch_mil * MIL
    group_gap = group_gap_mil * MIL
    issues, warns, groups = [], [], {}

    def near(v, ref, tol=0.006):
        return abs(v - ref) <= tol

    # --- 编号
    seen = set()
    for p in pins:
        if p["number"] in seen:
            issues.append(("duplicate", "引脚号 %s 重复" % p["number"]))
        seen.add(p["number"])

    # --- 栅格 / 长度
    for p in pins:
        for axis, v in (("x", p["x"]), ("y", p["y"])):
            if not near(v / grid, round(v / grid), tol=1e-4):
                issues.append(("off_grid", "引脚 %s 的 %s=%g mm 不在 %g mil 栅格上"
                               % (p["number"], axis, v, grid_mil)))
    lens = {round(p["length"], 4) for p in pins}
    if len(lens) > 1:
        warns.append(("pin_length", "引脚长度不一致: %s mm" % sorted(lens)))

    # --- 分侧看间距
    for side in ("left", "right", "top", "bottom"):
        grp = [p for p in pins if side_of(p) == side]
        if not grp:
            continue
        axis = "y" if side in ("left", "right") else "x"
        desc = side in ("left", "right")          # 左/右按 y 从大到小（上->下）
        grp.sort(key=lambda p: -p[axis] if desc else p[axis])
        pos = [p[axis] for p in grp]
        gaps = [round(abs(b - a), 4) for a, b in zip(pos, pos[1:])]
        if not gaps:
            continue
        base = min(gaps)
        groups[side] = {"base_pitch_mm": base, "base_pitch_mil": base / MIL,
                        "pins": [(p["number"], p["name"], p[axis]) for p in grp],
                        "gaps_mil": [g / MIL for g in gaps]}
        if base < min_pitch - 1e-6:
            issues.append(("pitch", "%s 边基础间距 %.1f mil < 规定 %.0f mil"
                           % (side, base / MIL, min_pitch_mil)))
        for g in gaps:
            k = g / base
            if not near(k, round(k), tol=0.02):
                issues.append(("multiple", "%s 边间距 %.3f mm 不是基础间距 %.3f mm 的整数倍"
                               % (side, g, base)))
            elif round(k) >= 2 and g < group_gap - 1e-6 and not declared:
                # 只在没给分组时才靠几何猜；给了分组就以声明为准（见下面）
                issues.append(("group_gap",
                               "%s 边有 %.1f mil 的空隙（%d 倍基础间距）< 组间规定 %.0f mil"
                               % (side, g / MIL, round(k), group_gap_mil)))
        # 推断分组（仅供对账）
        segs, cur = [], [grp[0]]
        for p, g in zip(grp[1:], gaps):
            if g / base >= 1.5:
                segs.append(cur)
                cur = []
            cur.append(p)
        segs.append(cur)
        inferred = [[p["name"] for p in s] for s in segs]
        groups[side]["groups"] = inferred

        # ---- 声明的分组才是要求 ----
        dec = (declared or {}).get(side)
        if dec:
            flat = [n for g in dec for n in g]
            got = [p["name"] for p in grp]
            if sorted(flat) != sorted(got):
                issues.append(("groups",
                               "%s 边声明的分组 %s 与该边实际引脚 %s 不是同一集合"
                               % (side, flat, got)))
            else:
                idx = {p["name"]: i for i, p in enumerate(grp)}
                for gi, g in enumerate(dec):
                    ii = sorted(idx[n] for n in g)
                    within = [gaps[i] for i in range(ii[0], ii[-1])]
                    for w in within:
                        if w < min_pitch - 1e-6:
                            issues.append(("pitch",
                                           "%s 边组%d %s 内部间距 %.1f mil < 规定 %.0f mil"
                                           % (side, gi + 1, g, w / MIL, min_pitch_mil)))
                    if len(set(round(w, 4) for w in within)) > 1:
                        issues.append(("pitch",
                                       "%s 边组%d %s 内部间距不一致: %s mil"
                                       % (side, gi + 1, g,
                                          [round(w / MIL, 1) for w in within])))
                for gi in range(len(dec) - 1):
                    a = max(idx[n] for n in dec[gi])
                    b = min(idx[n] for n in dec[gi + 1])
                    sep = sum(gaps[a:b]) if b > a else 0.0
                    if sep < group_gap - 1e-6:
                        issues.append(("group_gap",
                                       "%s 边组%d%s 与组%d%s 之间只有 %.1f mil < 规定 %.0f mil"
                                       % (side, gi + 1, dec[gi], gi + 2, dec[gi + 1],
                                          sep / MIL, group_gap_mil)))
                if inferred != dec:
                    issues.append(("groups",
                                   "%s 边几何上推断出的分组 %s ≠ 声明的分组 %s"
                                   % (side, inferred, dec)))
        elif not declared:
            # 没给分组：只能按启发式报，并明确说明这是推断
            for g in inferred:
                if len(g) > 1:
                    pass
            groups[side]["inferred_only"] = True

    # --- 本体
    if body:
        bx0, by0, bx1, by1 = body
        tol = 0.02
        # 引脚靠本体那一端必须落在本体边界上或内部。
        # 注意：引脚根部**贴在本体边缘**才是对的，所以不能用"留余量"去查。
        for p in pins:
            rx, ry = root_of(p)
            s = side_of(p)
            if s in ("left", "right"):
                if not (bx0 - tol <= rx <= bx1 + tol):
                    issues.append(("body_margin",
                                   "引脚 %s 根部 x=%.2f 没接到本体 (%.2f..%.2f)"
                                   % (p["number"], rx, bx0, bx1)))
            elif s in ("top", "bottom"):
                if not (by0 - tol <= ry <= by1 + tol):
                    issues.append(("body_margin",
                                   "引脚 %s 根部 y=%.2f 没接到本体 (%.2f..%.2f)"
                                   % (p["number"], ry, by0, by1)))
        # 引脚离本体角落太近：只是提醒（手工画的符号常有）
        m = 50.0 * MIL          # 50 mil
        for side, grp in (("left", [p for p in pins if side_of(p) == "left"]),
                          ("right", [p for p in pins if side_of(p) == "right"])):
            if grp:
                ys = [p["y"] for p in grp]
                if min(min(ys) - by0, by1 - max(ys)) < m - 1e-6:
                    warns.append(("body_margin",
                                  "%s 边最边上的引脚离本体上/下边缘只有 %.2f mm"
                                  % (side, min(min(ys) - by0, by1 - max(ys)))))
        # --- 名字溢出：同一行左右两个名字会不会撞上
        bw = bx1 - bx0
        rows = {}
        for p in pins:
            if side_of(p) in ("left", "right"):
                rows.setdefault(round(p["y"], 3), {})[side_of(p)] = p
        for y, r in rows.items():
            lw = len(r["left"]["name"]) * font * 0.85 if "left" in r else 0.0
            rw = len(r["right"]["name"]) * font * 0.85 if "right" in r else 0.0
            need = lw + rw + 2 * font                     # 名字各留 1 个字宽的间隙
            if need > bw:
                warns.append(("overflow",
                              "y=%.2f 行: 左'%s' + 右'%s' 估算需要 %.1f mm > 本体 %.1f mm"
                              % (y,
                                 r.get("left", {}).get("name", "-"),
                                 r.get("right", {}).get("name", "-"), need, bw)))

    # --- 电源
    if not any(p["etype"] == "power_in" for p in pins):
        warns.append(("no_power", "没有 power_in 引脚"))

    # --- 位号 / Value
    if body:
        bx0, by0, bx1, by1 = body
        for k, (v, x, y) in fields.items():
            if bx0 < x < bx1 and by0 < y < by1:
                warns.append(("fields", "%s (%s) 落在本体内部 (%.2f, %.2f)" % (k, v, x, y)))

    return {"symbol": name, "body": body, "pins": pins,
            "issues": issues, "warnings": warns, "sides": groups}


@guard
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lib")
    ap.add_argument("--symbol")
    ap.add_argument("--min-pitch", type=float, default=None,
                    help="mil；不给则取 groups.json 的 _style，再否则 200")
    ap.add_argument("--group-gap", type=float, default=None,
                    help="mil；不给则取 groups.json 的 _style，再否则 400")
    ap.add_argument("--grid", type=float, default=50.0, help="mil")
    ap.add_argument("--groups", help="分组 JSON：{边: [[组内引脚名...], ...]}")
    ap.add_argument("--json")
    a = ap.parse_args()

    dec = json.load(open(a.groups, encoding="utf-8")) if a.groups else None
    style = (dec or {}).get("_style") if isinstance(dec, dict) else None
    style = style if isinstance(style, dict) else {}
    mp = a.min_pitch if a.min_pitch is not None else float(style.get("pitch_mil", 200.0))
    gg = a.group_gap if a.group_gap is not None else float(style.get("group_gap_mil", 400.0))
    r = lint(a.lib, a.symbol, mp, gg, a.grid, declared=dec)
    print("symbol :", r["symbol"])
    print("pins   : %d" % len(r["pins"]))
    if r["body"]:
        x0, y0, x1, y1 = r["body"]
        print("body   : %.2f x %.2f mm  (x %.2f..%.2f, y %.2f..%.2f)"
              % (x1 - x0, y1 - y0, x0, x1, y0, y1))
    print("规范   : 组内 >= %.0f mil, 组间 >= %.0f mil, 栅格 %.0f mil%s"
          % (mp, gg, a.grid, "  (取自 groups.json)" if style else ""))

    print("\n--- 各边间距 ---")
    for side, g in r["sides"].items():
        print("  %-6s 基础间距 %6.1f mil   相邻间距(mil): %s"
              % (side, g["base_pitch_mil"],
                 " ".join("%.0f" % v for v in g["gaps_mil"])))
        for i, seg in enumerate(g["groups"]):
            print("           组%d: %s" % (i + 1, " ".join(seg)))
        if g.get("inferred_only"):
            print("           [!] 上面是**几何推断**的分组，没给 --groups；"
                  "语义对不对必须你确认")

    print("\n--- 结果 ---")
    if not r["issues"] and not r["warnings"]:
        print("  无疑问项")
    for kind, msg in r["issues"]:
        print("  [FAIL] %-12s %s" % (kind, msg))
    for kind, msg in r["warnings"]:
        print("  [warn] %-12s %s" % (kind, msg))
    if a.json:
        json.dump(r, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("\njson:", a.json)
    return 1 if r["issues"] else 0


if __name__ == "__main__":
    sys.exit(main())
