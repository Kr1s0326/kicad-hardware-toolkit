#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sch_build.py - 把 .kicad_sym 里的一个符号塞进一张最小原理图。

为什么要绕这一圈
----------------
符号校验也需要一条"独立数据通路"，和封装的 Gerber 反解对等：

    封装：我写 .kicad_mod  ->  kicad-cli 导出 Gerber  ->  量 Gerber
    符号：我写 .kicad_sym  ->  kicad-cli 导出网表    ->  读网表

网表里的 pin 列表是 **KiCad 自己解析那个符号**得到的。所以拿网表去和
数据手册比，就绕开了"我自己写的文本"，能抓到解析层面的错
（比如引脚写在子单元外面、number 写重了、符号块结构坏了）。

同时这张原理图还能跑 ERC —— 电气类型（power_in / open_collector / output）
配错的符号，只有 ERC 会说话。

CLI
---
    python sch_build.py <lib.kicad_sym> <out.kicad_sch> [--symbol NAME]
                        [--lib-id LIB] [--ref U1] [--at 100,100]
"""

import argparse
import os
import re
import sys
import uuid


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


def symbol_block(sym_path, sym_name=None):
    """取出 .kicad_sym 里某一个顶层符号的完整文本块"""
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
    end = nxt.start() if nxt else txt.rindex("\n)")
    return sym_name, txt[start + 1:end + 1]


def indent(block, n):
    pad = "\t" * n
    return "".join(pad + l if l.strip() else l for l in block.splitlines(True))


def properties_of(block):
    """从符号块里抠出属性，供实例化时复用位置"""
    out = {}
    for m in re.finditer(r'\(property "([^"]+)"\s*\n?\s*"([^"]*)"', block):
        out.setdefault(m.group(1), m.group(2))
    return out


def pin_numbers(block):
    return re.findall(r'\(number "([^"]+)"', block)


def build(sym_path, out_sch, sym_name=None, lib_id=None, ref="U1",
          at=(101.6, 101.6), project="check"):
    name, blk = symbol_block(sym_path, sym_name)
    lib_id = lib_id or ("CHECK:" + name)

    # 放置点必须落在连接栅格上，否则 ERC 会对每个引脚报 endpoint_off_grid，
    # 而那是尺子的问题，不是符号的问题。1.27 mm = 50 mil（KiCad 默认栅格）。
    grid = 1.27
    at = (round(at[0] / grid) * grid, round(at[1] / grid) * grid)

    # lib_symbols 里父符号带库前缀，子单元保持裸名 —— 和 KiCad 自己的写法一致
    libblk = blk.replace('(symbol "%s"' % name, '(symbol "%s"' % lib_id, 1)

    props = properties_of(blk)
    fp = props.get("Footprint", "")
    ds = props.get("Datasheet", "")
    u = str(uuid.uuid4())
    su = str(uuid.uuid4())

    def prop(key, val, x, y, hide=False, size=1.27):
        return ('\t\t(property "%s" "%s"\n\t\t\t(at %g %g 0)\n%s\t\t\t(effects\n'
                '\t\t\t\t(font\n\t\t\t\t\t(size %g %g)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n'
                % (key, val, x, y, "\t\t\t(hide yes)\n" if hide else "", size, size))

    pins = "".join('\t\t(pin "%s"\n\t\t\t(uuid "%s")\n\t\t)\n'
                   % (n, uuid.uuid4()) for n in pin_numbers(blk))

    body = (
        '(kicad_sch\n'
        '\t(version 20250610)\n'
        '\t(generator "eeschema")\n'
        '\t(generator_version "10.0")\n'
        '\t(uuid "%s")\n'
        '\t(paper "A4")\n'
        '\t(lib_symbols\n%s\t)\n'
        '\t(symbol\n'
        '\t\t(lib_id "%s")\n'
        '\t\t(at %g %g 0)\n'
        '\t\t(unit 1)\n'
        '\t\t(exclude_from_sim no)\n'
        '\t\t(in_bom yes)\n'
        '\t\t(on_board yes)\n'
        '\t\t(dnp no)\n'
        '\t\t(uuid "%s")\n'
        '%s%s%s%s'
        '%s'
        '\t\t(instances\n'
        '\t\t\t(project "%s"\n'
        '\t\t\t\t(path "/%s"\n'
        '\t\t\t\t\t(reference "%s")\n'
        '\t\t\t\t\t(unit 1)\n'
        '\t\t\t\t)\n'
        '\t\t\t)\n'
        '\t\t)\n'
        '\t)\n'
        '\t(sheet_instances\n'
        '\t\t(path "/"\n'
        '\t\t\t(page "1")\n'
        '\t\t)\n'
        '\t)\n'
        '\t(embedded_fonts no)\n'
        ')\n'
        % (str(uuid.uuid4()),
           indent(libblk, 2),
           lib_id, at[0], at[1], u,
           prop("Reference", ref, at[0], at[1] - 5.08),
           prop("Value", name, at[0], at[1] - 7.62),
           prop("Footprint", fp, at[0], at[1], hide=True),
           prop("Datasheet", ds, at[0], at[1], hide=True),
           pins, project, su, ref))

    os.makedirs(os.path.dirname(os.path.abspath(out_sch)), exist_ok=True)
    with open(out_sch, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    return {"symbol": name, "lib_id": lib_id, "ref": ref,
            "pins": pin_numbers(blk), "sch": out_sch}


@guard
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lib")
    ap.add_argument("out")
    ap.add_argument("--symbol")
    ap.add_argument("--lib-id")
    ap.add_argument("--ref", default="U1")
    ap.add_argument("--at", default="101.6,101.6")
    a = ap.parse_args()
    x, y = (float(v) for v in a.at.split(","))
    r = build(a.lib, a.out, a.symbol, a.lib_id, a.ref, (x, y))
    print("symbol :", r["symbol"])
    print("lib_id :", r["lib_id"])
    print("pins   :", ",".join(r["pins"]))
    print("sch    :", r["sch"])


if __name__ == "__main__":
    sys.exit(main())