#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pinmap.py - 符号引脚 <-> 封装焊盘 的编号契约。

这是唯一真正的"跨 artifact 耦合点"：符号里的 pin number 必须和封装里的
pad number 对得上。它不属于创建，属于校验 —— 而且刻意做成**两半**：

    符号侧查：每个 pin 都有对应 pad      (kicad-check-sch-component)
    封装侧查：每个 pad 都有对应 pin      (kicad-check-pcb-component)

两侧各查一半，谁都不能单独宣布"契约成立"。这样即使两个 skill 的结论都来自
自己的数据通路，交叉之后仍然是一个真正的独立校验。

CLI
---
    python pinmap.py --footprint fp.kicad_mod
    python pinmap.py --symbol lib.kicad_sym [--symbol-name INA239]
    python pinmap.py --symbol lib.kicad_sym --footprint fp.kicad_mod

退出码：0 = 通过，8 = 契约不成立
"""

import argparse
import os
import re
import sys

# Windows 控制台常是 GBK；输出里若出现 GBK 以外的字符（↔ ✅ 之类）会直接抛
# UnicodeEncodeError 打断整个检查。这里保留原编码，只把无法映射的字符降级。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:                                       # noqa: BLE001
    pass


# ------------------------------------------------------------------ 读取
def pads_of(mod_path):
    """-> ({number: (x, y, w, h)}, [unnumbered])"""
    txt = open(mod_path, encoding="utf-8").read()
    out, unnum = {}, []
    pat = (r'\(pad "([^"]*)" (\S+) (\S+)\s*\n\s*\(at (-?[\d.]+) (-?[\d.]+)'
           r'(?: -?[\d.]+)?\)\s*\n\s*\(size ([\d.]+) ([\d.]+)\)')
    for m in re.finditer(pat, txt):
        num = m.group(1)
        rec = tuple(float(m.group(i)) for i in (4, 5, 6, 7))
        if not num:
            unnum.append(rec)
        else:
            out.setdefault(num, rec)
    return out, unnum


def pins_of(sym_path, sym_name=None):
    """-> (resolved_name, {number: (name, None)}) for one symbol in a .kicad_sym

    一个符号在文件里是若干块的集合：
        \t(symbol "INA239"       <- 父块，只放属性
        \t(symbol "INA239_0_1"   <- 图形
        \t(symbol "INA239_1_1"   <- 引脚
    所以要取 "父块 + 所有 INA239_* 子块"，直到下一个顶层符号。
    """
    txt = open(sym_path, encoding="utf-8").read()
    tops = re.findall(r'\n\t\(symbol "([^"]+)"', txt)
    if not tops:
        raise SystemExit("no symbol found in " + sym_path)
    if sym_name is None:
        cands = [n for n in tops if not re.search(r"_\d+_\d+$", n)]
        sym_name = cands[0] if cands else tops[0]

    start = txt.index('\n\t(symbol "%s"' % sym_name)
    nxt = re.compile(r'\n\t\(symbol "(?!%s_)' % re.escape(sym_name)).search(
        txt, start + 1)
    blk = txt[start:nxt.start() if nxt else len(txt)]

    out = {}
    for m in re.finditer(r'\(pin \w+ \w+\s*\n\s*\(at [-\d.]+ [-\d.]+ \d+\)\s*\n'
                         r'\s*\(length [\d.]+\)\s*\n\s*\(name "([^"]+)"[\s\S]*?'
                         r'\(number "([^"]+)"', blk):
        out.setdefault(m.group(2),
                       (m.group(1).replace("~{", "").replace("}", ""), None))
    return sym_name, out


def pad_numbers(mod_path):
    return set(pads_of(mod_path)[0])


# ------------------------------------------------------------------ 检查
def check_footprint(mod_path):
    pads, unnum = pads_of(mod_path)
    problems = []
    nums = list(pads)
    if not nums:
        problems.append("封装里没有任何带编号的焊盘")
    if len(nums) != len(set(nums)):
        problems.append("焊盘编号有重复")
    # 编号连续性：IPC 习惯是从 1 连续到 N（除非有意用字母/多排）
    if nums and all(n.isdigit() for n in nums):
        got = sorted(int(n) for n in nums)
        if got != list(range(1, len(got) + 1)):
            problems.append("焊盘编号不连续: %s" % got)
    return {"pads": pads, "unnumbered": unnum, "problems": problems}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--footprint")
    ap.add_argument("--symbol")
    ap.add_argument("--symbol-name")
    a = ap.parse_args()
    if not (a.footprint or a.symbol):
        ap.error("give --footprint and/or --symbol")

    ok = True
    padset = symset = None

    if a.footprint:
        r = check_footprint(a.footprint)
        padset = set(r["pads"])
        print("封装: %s" % os.path.basename(a.footprint))
        print("  焊盘编号: %s" % ", ".join(sorted(padset, key=lambda s: (len(s), s))))
        if r["unnumbered"]:
            print("  无名焊盘: %d 个（散热盘过孔/分块，正常）" % len(r["unnumbered"]))
        for p in r["problems"]:
            print("  [FAIL] %s" % p)
            ok = False

    if a.symbol:
        name, pins = pins_of(a.symbol, a.symbol_name)
        symset = set(pins)
        print("符号: %s  (%s)" % (name, os.path.basename(a.symbol)))
        print("  引脚编号: %s" % ", ".join(sorted(symset, key=lambda s: (len(s), s))))
        if not symset:
            print("  [FAIL] 没有解析到任何引脚")
            ok = False

    if padset is not None and symset is not None:
        print("契约:")
        miss_pad = sorted(symset - padset, key=lambda s: (len(s), s))
        miss_pin = sorted(padset - symset, key=lambda s: (len(s), s))
        print("  符号有、封装没有的引脚: %s" % (miss_pad or "无"))
        print("  封装有、符号没有的焊盘: %s" % (miss_pin or "无"))
        if miss_pad or miss_pin:
            ok = False
        print("  [%s] 引脚<->焊盘编号一一对应" % ("PASS" if ok else "FAIL"))

    print("\n" + ("契约检查通过" if ok else "契约检查未通过"))
    sys.exit(0 if ok else 8)


if __name__ == "__main__":
    main()
