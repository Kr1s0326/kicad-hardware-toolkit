#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fit3d.py - 把"真实器件的 3D 模型"压到封装焊盘上，看引脚落不落得住。

这是所有校验里最直观、也最难造假的一条
--------------------------------------
Gerber 量测能证明"焊盘 = 图纸数字"，但它证明不了"图纸数字 = 这颗实物"。
3D 渲染把 STEP 模型（TI / KiCad 官方库里的真实封装外形）和本封装的焊盘
画在同一张图里：

    引脚全部落在焊盘上、Pin1 圆点对准丝印三角  -> 几何对
    引脚悬空 / 压到别的焊盘 / 偏出 1/3          -> 间距或跨距错了

前提：封装里的 (model "...") 指向真实存在的 STEP。若封装没挂模型，
本脚本会明确报出来，而不是静默出一张空图。

CLI
---
    python fit3d.py <fp.kicad_mod> <outdir> [--board-out b.kicad_pcb]
"""

import argparse
import glob
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
from cli import guard                             # noqa: E402

from core import board as board_mod                            # noqa: E402
import render                                                  # noqa: E402

# Windows 控制台常是 GBK；输出里若出现 GBK 以外的字符（↔ ✅ 之类）会直接抛
# UnicodeEncodeError 打断整个检查。这里保留原编码，只把无法映射的字符降级。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:                                       # noqa: BLE001
    pass


def model_refs(mod_path):
    txt = open(mod_path, encoding="utf-8").read()
    return re.findall(r'\(model\s+"([^"]+)"', txt)


def resolve_model(ref):
    """${KICADxx_3DMODEL_DIR}/... -> a real path, or None"""
    m = re.match(r"\$\{(\w+)\}/(.*)$", ref)
    if not m:
        return ref if os.path.exists(ref) else None
    var, rest = m.group(1), m.group(2)
    env = os.environ.get(var)
    if env and os.path.exists(os.path.join(env, rest)):
        return os.path.join(env, rest)
    # fall back: look next to the kicad-cli we found
    cli = render.kicad_cli()
    kicad_root = os.path.dirname(os.path.dirname(cli))          # .../KiCad/10.0
    for base in (os.path.join(kicad_root, "share", "kicad", "3dmodels"),
                 os.path.join(kicad_root, "3dmodels")):
        p = os.path.join(base, rest)
        if os.path.exists(p):
            return p
    # last resort: search the share tree for the basename
    base = os.path.join(kicad_root, "share", "kicad", "3dmodels")
    if os.path.isdir(base):
        hits = glob.glob(os.path.join(base, "**", os.path.basename(rest)),
                         recursive=True)
        if hits:
            return hits[0]
    return None


@guard
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mod")
    ap.add_argument("outdir")
    ap.add_argument("--board-out")
    ap.add_argument("--lib-id", default="CHECK:FOOTPRINT")
    a = ap.parse_args()

    os.makedirs(a.outdir, exist_ok=True)
    mod = os.path.abspath(a.mod)

    refs = model_refs(mod)
    print("model ref :", refs[0] if refs else "(封装没有挂 3D 模型)")
    ok = True
    resolved = []
    for r in refs:
        p = resolve_model(r)
        print("resolved  :", p if p else "** 找不到该 STEP **")
        resolved.append(p)
        ok = ok and bool(p)

    if not ok:
        # 模型缺失时**不渲染**。渲染出来的是一张"零件不在上面"的图，
        # 比没有图更危险 —— 有人会看一眼就以为对。
        print("\n3D 贴合无法验证：STEP 模型缺失。"
              "\n  修法：在封装里把 (model ...) 指向本机存在的步进文件，"
              "或从 KiCad 官方库/厂商渠道补上模型。")
        return 7

    pcb = a.board_out or os.path.join(a.outdir, "_fit_board.kicad_pcb")
    board_mod.write_pcb(pcb, mod, lib_id=a.lib_id)

    outs = []
    for name, kw in (("fit3d_top", dict(side="top", zoom=2.2)),
                     ("fit3d_iso", dict(side="top", zoom=1.8,
                                        rotate="-35,0,25"))):
        png = os.path.join(a.outdir, name + ".png")
        render.render_board_3d(pcb, png, **kw)
        outs.append(png)
        print("rendered  :", png)
    sheet = render.contact_sheet(outs, os.path.join(a.outdir, "fit3d_sheet.png"),
                                 cols=2, cell=700,
                                 title="3D fit: real part model on this footprint's pads")
    print("sheet     :", sheet)
    print("\n请打开上面两张图确认：每条引脚都落在焊盘上，Pin1 标记对齐。")
    return 0


if __name__ == "__main__":
    sys.exit(main())