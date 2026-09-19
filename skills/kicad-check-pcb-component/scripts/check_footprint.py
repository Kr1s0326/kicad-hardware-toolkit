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
from cli import evidence_head, guard, require, summary   # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:                                       # noqa: BLE001
    pass

from core import board as board_mod                    # noqa: E402
from core import spec as spec_mod                      # noqa: E402
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
    ap.add_argument("--spec", required=True,
                    help="要求表（必填）。每一行的「要求:数值 / 要求:图片」都来自它；"
                         "DRC 的焊盘间距规则也从它推出")
    ap.add_argument("--symbol")
    ap.add_argument("--symbol-name")
    ap.add_argument("--name")
    ap.add_argument("--lib-id", default="CHECK:FOOTPRINT")
    ap.add_argument("--no-3d", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    # 没有要求表就没法校验，只有自测。
    #
    # 本 skill 的每一条结论都是「要求 ↔ 实测」的对比：报告里那两列「要求」
    # 来自 spec，测量值要和它比才有意义；连 DRC 的焊盘间距规则都是从 spec
    # 推出来的（精细节距封装用 KiCad 默认的 0.2mm，会报一屏假违规）。
    # 缺了 spec，工具仍能把 Gerber 量得很准，但**量出来的数字没人知道对不对**，
    # 而汇总行与退出码看上去还是绿的 —— 那比不跑更坏。
    require(os.path.isfile(os.path.abspath(a.spec)),
            "找不到 --spec 指定的要求表：\n  %s\n"
            "\n本 skill 的每一条结论都是「要求 ↔ 实测」的对比，缺了要求表就无法校验。\n"
            "生成侧（kicad-create-lib-part）的 --verify 会把该给的 spec 路径打出来。"
            % os.path.abspath(a.spec))

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

    # 焊盘间短路间距规则。KiCad 的默认值是 0.2mm，那是给 1.27mm 节距的普通
    # 封装用的；0.4mm 节距的 WLCSP 根本达不到（相邻球心 0.35mm、焊盘 0.22mm
    # -> 边到边 0.13mm），拿默认值跑会报一屏“间距违规”，而那**一个缺陷都不是**。
    # 下界从 spec（图纸要求）推，不是从被测封装自己推 —— 取自封装自己的话
    # 规则永远成立，DRC 就成了空跑。
    _sp = json.load(open(os.path.abspath(a.spec), encoding="utf-8"))
    gap = spec_mod.min_pad_gap(_sp)
    if gap and gap > 0:
        dru = spec_mod.write_dru(pcb, gap)
        print("drc rules: %s  (pad-to-pad >= %.3fmm，由 spec 推出)"
              % (os.path.basename(dru), gap - 0.005))
    else:
        # 推不出来（spec 里没有 dia/lead_width 这类能定出相邻焊盘间距的行）。
        # 不能默不作声地落回 0.2mm —— 那会对精细节距封装报一屏假违规，
        # 而报告里看不出“这条规则其实没定制过”。
        print("drc rules: （推不出）—— spec 里缺少能定出相邻焊盘间距的行\n"
              "           （球阵族需 dia + diag_min/pitch；引脚族需 lead_width + lead_pitch），\n"
              "           将使用 KiCad 默认的 0.2mm 间距规则，精细节距封装可能报假违规。")
        ev("DRC 间距规则", "未做", "core/spec.py: min_pad_gap()",
           "spec 里缺 dia+diag_min/pitch（球阵）或 lead_width+lead_pitch（引脚），"
           "推不出相邻焊盘的最小间距 —— 本次用了 KiCad 默认 0.2mm。"
           "若 DRC 报“间距违规”而尺寸表全 PASS，先查这里。")

    # ---------------------------------------------------------- 1 Gerber
    step(1, "Gerber 反解量测  (独立通路 A: 量的是制造数据，不是封装文件)")
    gdir = os.path.join(out, "gerber")
    files = export_gerbers(pcb, gdir)
    print("exported %d files -> %s" % (len(files), gdir))
    xlsx = None
    spec = os.path.abspath(a.spec)
    sp = json.load(open(spec, encoding="utf-8"))
    sp.setdefault("gerber_dir", gdir)
    tmp = os.path.join(out, "_spec_resolved.json")
    # spec 会被复制到 outdir 再跑，于是里面**相对路径的基准变成了 outdir**。
    # 结果：用户在 spec 旁边写的 req_image / spec_table_image 全部找不到，
    # 而 build_xlsx 里 os.path.exists() 不通过就**静默跳过** —— 报告里
    # "要求:图片" 列一片空白，还没有任何提示。
    # 所以在复制前把这几类路径按**原 spec 所在目录**解析成绝对路径。
    sd = os.path.dirname(spec)
    def _abs(v):
        return v if (not v or os.path.isabs(v)) else os.path.normpath(
            os.path.join(sd, v))
    for key in ("spec_table_image", "gerber_dir", "img_dir"):
        if sp.get(key):
            sp[key] = _abs(sp[key])
    for r in sp.get("rows", []):
        if r.get("req_image"):
            r["req_image"] = _abs(r["req_image"])
    sp["gerber_dir"] = gdir
    json.dump(sp, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    r = run([sys.executable, os.path.join(HERE, "measure_component.py"), tmp,
             "--out", os.path.join(out, title + "_report.xlsx"),
             "--img-dir", os.path.join(out, "measure")])
    print(r.stdout or r.stderr)
    xlsx = os.path.join(out, title + "_report.xlsx")
    # measure_component 有 NG 行时返回 1。**必须把结论传进 evidence**，
    # 否则 N 行 NG 也会报"NG 项 0"，CI 直接放过。
    n_ng = len([l for l in (r.stdout or "").splitlines()
                if l.rstrip().endswith("NG") or " NG " in l])
    ev("尺寸测量表", "NG" if r.returncode else "PASS",
       "Gerber/Excellon 反解",
       "%d 行 NG  -> %s" % (n_ng, xlsx) if n_ng else xlsx)
    look += sorted(glob.glob(os.path.join(out, "measure", "meas_*.png")))[:6]

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
        # 不阻断，但**要说出来是什么**——以前只打一列 kind，看不出为什么不算。
        print("  另 %d 项不阻断（不是封装缺陷）：" % len(benign))
        for v in benign:
            print("    [%s] %s" % (v["kind"], drc_mod.benign_reason(v)))
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
        f.write(evidence_head("封装校验证据包 - %s" % title,
                  "封装文件: `%s`" % mod, evidence))
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

    sys.exit(summary(evidence, 6,
                     "下一步: 打开 look_sheet.png 与 EVIDENCE.md，逐图确认后再下结论。"))


if __name__ == "__main__":
    sys.exit(main())