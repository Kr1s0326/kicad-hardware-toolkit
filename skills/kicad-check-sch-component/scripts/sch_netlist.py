#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sch_netlist.py - 独立通路 A：让 KiCad 自己读那个符号，然后读它的输出。

    我写 .kicad_sym  ->  sch_build 建最小原理图  ->  kicad-cli 导网表
                     ->  解析网表里的 pin num/name

网表里的引脚是 **Eeschema 解析 .kicad_sym 之后给出的**，不是我读自己的文本。
所以拿它和手册比，能抓到"文本看着对、KiCad 读出来不对"的错：
引脚写在了子单元外面、number 重复、符号块结构坏了、被 embedded_fonts 之类截断……

CLI
---
    python sch_netlist.py <lib.kicad_sym> <workdir> [--symbol NAME]
                          [--footprint FP] [--json out.json]
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# 共享代码（render / pinmap）在 <toolkit>/shared/。
# 本文件在 <toolkit>/skills/<skill>/scripts/，所以往上三层就是 toolkit 根。
SHARED = os.path.normpath(os.path.join(HERE, "..", "..", "..", "shared"))
for _p in (HERE, SHARED):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:                                       # noqa: BLE001
    pass

import sch_build                                        # noqa: E402
from render import kicad_cli, run                       # noqa: E402


def export_netlist(lib, workdir, symbol=None, footprint=None, lib_id=None):
    """-> dict(sch, net, pins{num:name}, fields{}, libparts[])"""
    os.makedirs(workdir, exist_ok=True)
    base = symbol or os.path.splitext(os.path.basename(lib))[0]
    sch = os.path.join(workdir, base + ".kicad_sch")
    net = os.path.join(workdir, base + ".net")
    info = sch_build.build(lib, sch, symbol, lib_id)

    r = run([kicad_cli(), "sch", "export", "netlist", "-o", net, sch,
             "--format", "kicadsexpr"])
    if not os.path.exists(net):
        raise SystemExit("netlist export failed:\n%s" % (r.stdout + r.stderr)[:500])

    txt = open(net, encoding="utf-8", errors="replace").read()
    # 只看 libparts 段（实例段里的 pin 是搭线结果，不是库定义）
    i = txt.find("(libparts")
    blk = txt[i:] if i >= 0 else txt

    pins, fields = {}, {}
    for m in re.finditer(r'\(pin\s*\n\s*\(num "([^"]+)"\)\s*\n\s*\(name "([^"]+)"\)',
                         blk):
        pins.setdefault(m.group(1), m.group(2))
    for m in re.finditer(r'\(field\s*\n\s*\(name "([^"]+)"\)\s*"([^"]*)"', blk):
        fields.setdefault(m.group(1), m.group(2))

    return {"sch": sch, "net": net, "pins": pins, "fields": fields,
            "symbol": info["symbol"], "lib_id": info["lib_id"],
            "footprint": footprint or fields.get("Footprint", "")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lib")
    ap.add_argument("workdir")
    ap.add_argument("--symbol")
    ap.add_argument("--lib-id")
    ap.add_argument("--footprint")
    ap.add_argument("--json")
    a = ap.parse_args()

    r = export_netlist(a.lib, a.workdir, a.symbol, a.footprint, a.lib_id)
    print("symbol :", r["symbol"])
    print("lib_id :", r["lib_id"])
    print("sch    :", r["sch"])
    print("net    :", r["net"])
    print("Footprint 字段:", r["fields"].get("Footprint", "(空)"))
    print("Datasheet 字段:", r["fields"].get("Datasheet", "(空)"))
    print("KiCad 解析出的引脚:")
    for n in sorted(r["pins"], key=lambda s: (len(s), s)):
        print("  %-4s %s" % (n, r["pins"][n]))
    if a.json:
        json.dump(r, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return r


if __name__ == "__main__":
    main()
