#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_footprint.py - PCB 封装校验总入口：一条命令产出完整证据包。

跑完会得到

    <outdir>/
    ├── EVIDENCE.md            证据清单 + 每条结论的数据来源 + 需要人看的图
    ├── board.kicad_pcb        把封装内联成的最小板
    ├── gerber/                F.Cu / F.Paste / F.Mask / F.SilkS / F.Fab / Edge.Cuts + Excellon
    ├── <name>_report.xlsx     尺寸测量表（若给了 spec.json）
    ├── drc.rpt                DRC 原始报告
    ├── fit3d_top.png          真实器件 3D 模型压在本封装焊盘上
    ├── fit3d_iso.png
    ├── fp/<name>.png          2D 绘图（丝印/铜/装配/外框）
    └── look_sheet.png         ★ 把所有图拼成一张，供人一次看完

为什么是这几项，各自的独立性
----------------------------
    尺寸表    量 Gerber            -> 焊盘数字对不对
    DRC       跑 KiCad 自己的规则   -> 丝印压不压盘、外框全不全
    3D 贴合   真实 STEP 压焊盘      -> 图纸数字是不是这颗实物
    2D 看图   渲染后肉眼审          -> 布局意图类错误（数字全对也可能丑/不能用）
    pinmap    与符号交叉           -> 编号契约

前四项互相独立：任何一项单独跑都不会发现另外三项能发现的问题。

CLI
---
    python check_footprint.py <fp.kicad_mod> --outdir out [选项]

    --spec  a.spec.json     有则出尺寸测量表（要求值来自图纸）
    --symbol lib.kicad_sym  有则做完整的 引脚<->焊盘 契约检查
    --name  INA239          报告标题用
    --no-3d                 跳过 3D（没装模型或不需要时）
    --quiet
"""

import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# 共享代码（render / pinmap）在 <toolkit>/shared/。
# 本文件在 <toolkit>/skills/<skill>/scripts/，所以往上三层就是 toolkit 根。
SHARED = os.path.normpath(os.path.join(HERE, "..", "..", "..", "shared"))
for _p in (HERE, SHARED):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from cli import guard                             # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:                                       # noqa: BLE001
    pass

from core import board as board_mod                    # noqa: E402
import render                                          # noqa: E402
from render import kicad_cli, run                      # noqa: E402


def step(n, title):
    print("\n" + "=" * 66)
    print("[%d] %s" % (n, title))
    print("=" * 66)


def export_gerbers(pcb, gdir):
    os.makedirs(gdir, exist_ok=True)
    run([kicad_cli(), "pcb", "export", "gerbers", "-o", gdir, "--layers",
         "F.Cu,F.Paste,F.Mask,F.SilkS,F.Fab,Edge.Cuts", "--no-x2",
         "--no-netlist", pcb])
    run([kicad_cli(), "pcb", "export", "drill", "-o", gdir, "--format",
         "excellon", "--excellon-separate-th", pcb])
    return sorted(glob.glob(os.path.join(gdir, "*")))


@guard
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mod")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--spec")
    ap.add_argument("--symbol")
    ap.add_argument("--symbol-name")
    ap.add_argument("--name")
    ap.add_argument("--lib-id", default="CHECK:FOOTPRINT")
    ap.add_argument("--no-3d", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    mod = os.path.abspath(a.mod)
    out = os.path.abspath(a.outdir)
    os.makedirs(out, exist_ok=True)
    title = a.name or os.path.splitext(os.path.basename(mod))[0]

    evidence, look = [], []

    def ev(name, status, how, detail=""):
        evidence.append({"item": name, "result": status, "source": how,
                         "detail": detail})

    # ---------------------------------------------------------------- 板
    step(0, "把封装内联成最小板 (后面三条通路都要用板子)")
    pcb = os.path.join(out, "board.kicad_pcb")
    board_mod.write_pcb(pcb, mod, lib_id=a.lib_id)
    print("board:", pcb)

    # ---------------------------------------------------------- 1 Gerber
    step(1, "Gerber 反解量测  (独立通路 A: 量的是制造数据，不是封装文件)")
    gdir = os.path.join(out, "gerber")
    files = export_gerbers(pcb, gdir)
    print("exported %d files -> %s" % (len(files), gdir))
    xlsx = None
    if a.spec:
        spec = os.path.abspath(a.spec)
        sp = json.load(open(spec, encoding="utf-8"))
        sp.setdefault("gerber_dir", gdir)
        # 把 gerber_dir 写成绝对路径，免得在别的目录下跑
        tmp = os.path.join(out, "_spec_resolved.json")
        sp["gerber_dir"] = gdir
        json.dump(sp, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        r = run([sys.executable, os.path.join(HERE, "measure_component.py"), tmp,
                 "--out", os.path.join(out, title + "_report.xlsx"),
                 "--img-dir", os.path.join(out, "measure")])
        print(r.stdout or r.stderr)
        xlsx = os.path.join(out, title + "_report.xlsx")
        ev("尺寸测量表", "见报告", "Gerber/Excellon 反解", xlsx)
        look += sorted(glob.glob(os.path.join(out, "measure", "meas_*.png")))[:6]
    else:
        print("(未给 --spec，跳过尺寸测量表)")
        ev("尺寸测量表", "未做", "-", "需要 --spec")

    # ---------------------------------------------------------------- 2 DRC
    step(2, "DRC  (独立通路 B: KiCad 自己的规则引擎)")
    import drc as drc_mod
    d = drc_mod.run_drc(pcb, os.path.join(out, "drc.rpt"))
    blocking = [v for v in d["violations"] if not drc_mod.is_benign(v)]
    benign = [v for v in d["violations"] if drc_mod.is_benign(v)]
    for v in blocking:
        print("  [%s] %s: %s" % (v["severity"], v["kind"], v["msg"][:70]))
        if v["where"]:
            print("         ", v["where"])
    if not blocking:
        print("  none - 丝印不压盘 / 外框完整 / 无间距违规")
    if benign:
        # 比如"测试板没注册封装库"—— 与封装质量无关，不刷屏
        print("  (另 %d 项与被测封装无关，已忽略: %s)"
              % (len(benign), ", ".join(sorted({v["kind"] for v in benign}))))
    ev("DRC", "PASS" if not blocking else "NG", "kicad-cli pcb drc",
       "%d 项相关违规" % len(blocking))

    # ------------------------------------------------------------ 3 3D 贴合
    if not a.no_3d:
        step(3, "3D 实物贴合  (独立通路 C: 真实 STEP 模型压焊盘)")
        import fit3d as fit_mod
        refs = fit_mod.model_refs(mod)
        res = [fit_mod.resolve_model(r) for r in refs]
        if not refs:
            print("  封装没有挂 3D 模型 -> 跳过（建议在封装里补 (model ...)）")
            ev("3D 贴合", "未做", "-", "封装未挂 model")
        elif not all(res):
            print("  模型路径解析不到:", refs)
            ev("3D 贴合", "未做", "-", "STEP 找不到")
        else:
            for nm, kw in (("fit3d_top", dict(side="top", zoom=2.2)),
                           ("fit3d_iso", dict(side="top", zoom=1.8,
                                              rotate="-35,0,25"))):
                render.render_board_3d(pcb, os.path.join(out, nm + ".png"), **kw)
            look += [os.path.join(out, "fit3d_top.png"),
                     os.path.join(out, "fit3d_iso.png")]
            print("  rendered fit3d_top.png / fit3d_iso.png")
            ev("3D 贴合", "待目视", "kicad-cli pcb render + 真实 STEP",
               "引脚是否落满焊盘、Pin1 是否对齐")

    # ------------------------------------------------------------ 4 2D 看图
    step(4, "2D 绘图  (供肉眼审: 丝印/铜/装配/外框)")
    svgdir = os.path.join(out, "fp")
    svgs = render.render_lib("fp", os.path.dirname(mod),
                             svgdir, "F.Cu,F.SilkS,F.Fab,F.CrtYd,F.Mask")
    mine = [s for s in svgs if os.path.splitext(os.path.basename(mod))[0] in s] or svgs
    for s in mine:
        png = os.path.splitext(s)[0] + ".png"
        render.svg2png(s, png)
        print("  ->", os.path.basename(png))
        look.append(png)
    ev("2D 绘图", "待目视", "kicad-cli fp export svg -> PNG", "fp/")

    # ------------------------------------------------------------ 5 契约
    step(5, "引脚<->焊盘 编号契约  (与符号交叉)")
    import pinmap as pm
    r = pm.check_footprint(mod)
    pads = sorted(r["pads"], key=lambda s: (len(s), s))
    print("  焊盘编号: %s" % ", ".join(pads))
    for p in r["problems"]:
        print("  [FAIL] %s" % p)
    # pinmap 在 shared/，不在本 skill 的 scripts/ 里
    cmd = [sys.executable, os.path.join(SHARED, "pinmap.py"), "--footprint", mod]
    if a.symbol:
        cmd += ["--symbol", os.path.abspath(a.symbol)]
        if a.symbol_name:
            cmd += ["--symbol-name", a.symbol_name]
    rr = run(cmd)
    print(rr.stdout or rr.stderr)
    ev("引脚<->焊盘契约", "PASS" if rr.returncode == 0 else "NG",
       "解析 .kicad_mod 焊盘编号" + (" + 与符号交叉" if a.symbol else "（仅封装侧半）"),
       "%d 个焊盘" % len(pads))

    # ------------------------------------------------------------ 看图汇总
    step(6, "汇总")
    if look:
        sheet = os.path.join(out, "look_sheet.png")
        render.contact_sheet(look, sheet, cols=3, cell=560,
                             title="%s - look at this" % title)
        print("look sheet:", sheet)

    md = os.path.join(out, "EVIDENCE.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write("# 封装校验证据包 - %s\n\n" % title)
        f.write("封装文件: `%s`\n\n" % mod)
        f.write("| 检查项 | 结果 | 数据来源（独立性） | 明细 |\n|---|---|---|---|\n")
        for e in evidence:
            f.write("| %s | %s | %s | %s |\n"
                    % (e["item"], e["result"], e["source"], e["detail"]))
        f.write("\n## 必须人看的图\n\n")
        f.write("数值全对不代表能用。下面这些图要真的打开看：\n\n")
        for p in look:
            f.write("* `%s`\n" % os.path.relpath(p, out).replace("\\", "/"))
        f.write("\n看图要点见 `references/render-and-look.md`。\n")
    print("evidence:", md)

    hard = [e for e in evidence if e["result"] == "NG"]
    print("\n" + ("=" * 66))
    print("  NG 项: %d" % len(hard))
    for e in hard:
        print("    - %s (%s)" % (e["item"], e["detail"]))
    print("  下一步: 打开 look_sheet.png 与 EVIDENCE.md，逐图确认后再下结论。")
    print("=" * 66)
    sys.exit(6 if hard else 0)


if __name__ == "__main__":
    sys.exit(main())