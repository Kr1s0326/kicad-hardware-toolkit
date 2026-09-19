#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
e2e.py - 端到端回归：一份 spec 进，NG=0 出。

为什么单独一个文件，不塞进某个 skill 的 selftest
------------------------------------------------
三个 selftest 各测一边：create 只测"几何算得对不对"，两个 check 只测
"单个测量函数对不对"。**没有任何一项把两半接起来跑过** —— 于是
`groups.json` 的 `_style` 字段名、`fp.spec.json` 的 `kind` 命名、
退出码约定这些**跨 skill 的契约**一旦漂移，两边各自的测试都是绿的。

这个脚本就是那条接缝的测试：

    spec ──make_part──> 符号 + 封装 + groups.json + fp.spec.json
                              │
                              └──两个 check──> NG 必须为 0

它同时是唯一会真正跑 3D 贴合（fit3d）与 DRC 的地方 —— 那两个通路
在别处全是 skip。

需要本机装了 KiCad（kicad-cli）。没有就**大声跳过**，不静默通过。

CLI
---
    python e2e.py [--keep]        # --keep 保留临时目录，便于看图
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:                                       # noqa: BLE001
    pass

CREATE = os.path.join(HERE, "skills", "kicad-create-lib-part", "scripts")
CHK_SCH = os.path.join(HERE, "skills", "kicad-check-sch-component", "scripts")
CHK_PCB = os.path.join(HERE, "skills", "kicad-check-pcb-component", "scripts")


def spec_ssop10():
    """一颗 10 脚双排封装 —— 小、快，但走完所有通路。

    引脚号故意用 1..10、名字带一组 SPI 和一组电源，好让分组/类型/契约
    三条通路都有东西可查。
    """
    pins = []
    for num, nm, et, side, grp in (
            ("1", "~{CS}", "input", "right", "spi"),
            ("2", "MISO", "output", "right", "spi"),
            ("3", "SCLK", "input", "right", "spi"),
            ("4", "MOSI", "input", "right", "spi"),
            ("5", "~{ALERT}", "open_collector", "right", "int"),
            ("10", "IN+", "input", "left", "shunt"),
            ("9", "IN-", "input", "left", "shunt"),
            ("8", "VBUS", "input", "left", "bus"),
            ("6", "VS", "power_in", "top", None),
            ("7", "GND", "power_in", "bottom", None)):
        p = {"number": num, "name": nm, "etype": et, "side": side}
        if grp:
            p["group"] = grp
        pins.append(p)
    return {
        "library": "E2E", "symbol": "E2EPART", "footprint": "E2EFP",
        "reference": "U", "description": "e2e synthetic part",
        "datasheet": "n/a",
        "symbol_style": {"pitch_mil": 200, "group_gap_mil": 400,
                         "pin_length_mil": 100, "body_half_width_mil": 300,
                         "top_margin_mil": 100, "font": 1.27,
                         "side_align": "top"},
        "pins": pins,
        "groups": {"left": ["bus", "shunt"], "right": ["spi", "int"]},
        "judgments": {"etype_rules": ["synthetic fixture"]},
        "package": {
            "family": "peripheral", "sides": ["left", "right"],
            "pads_per_side": 5, "pitch": 0.5, "row_span_x": 4.4,
            "pad": {"length": 1.45, "width": 0.3, "roundrect_rratio": 0.166667},
            "body": {"w": 3.0, "h": 3.0},
            "descr": "", "tags": "", "model": "",
        },
    }


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=1800, **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true",
                    help="保留临时目录（看图用）")
    a = ap.parse_args()

    # 早检查：没有 KiCad 就没什么可测的。**大声跳过**，
    # 别学 fit3d 那样静默 —— 静默跳过正是这条通路烂掉几个月的原因。
    try:
        sys.path.insert(0, os.path.join(HERE, "shared"))
        import toolchain
        toolchain.kicad_cli()
    except SystemExit as e:
        print("跳过：本机没有 kicad-cli（e2e 需要真实 KiCad 才能跑）。")
        print("      %s" % str(e).splitlines()[0])
        return 0

    tmp = tempfile.mkdtemp(prefix="kicad_e2e_")
    fails = []

    def check(name, cond, detail=""):
        print("  [%s] %s%s" % ("ok  " if cond else "FAIL", name,
                               "" if cond else "   <- " + str(detail)[:150]))
        if not cond:
            fails.append(name)

    try:
        out = os.path.join(tmp, "out")
        spec = os.path.join(tmp, "spec.json")
        with open(spec, "w", encoding="utf-8") as f:
            json.dump(spec_ssop10(), f, ensure_ascii=False, indent=2)

        # ---------------------------------------------------------- 生成
        print("\n=== 1. make_part ===")
        r = run([sys.executable, os.path.join(CREATE, "make_part.py"),
                 spec, "--outdir", out, "--verify"])
        check("生成 rc=0", r.returncode == 0, (r.stderr or r.stdout)[-300:])
        sym = os.path.join(out, "E2E", "E2E.kicad_sym")
        fp = os.path.join(out, "E2E.pretty", "E2EFP.kicad_mod")
        for p in (sym, fp,
                  os.path.join(out, "E2E.groups.json"),
                  os.path.join(out, "E2E.fp.spec.json"),
                  os.path.join(out, "E2E.judgments.md")):
            check("产出 %s" % os.path.basename(p), os.path.exists(p))

        # ------------------------------------------------- 符号侧：必须 NG=0
        print("\n=== 2. check_symbol ===")
        pins = os.path.join(tmp, "pins.json")
        with open(pins, "w", encoding="utf-8") as f:
            json.dump({p["number"]: {"name": p["name"], "etype": p["etype"]}
                       for p in spec_ssop10()["pins"]}, f, ensure_ascii=False)
        r = run([sys.executable, os.path.join(CHK_SCH, "check_symbol.py"), sym,
                 "--outdir", os.path.join(tmp, "chk_sch"), "--symbol", "E2EPART",
                 "--groups", os.path.join(out, "E2E.groups.json"),
                 "--pins-json", pins, "--footprint", fp])
        check("check_symbol rc=0", r.returncode == 0, (r.stdout or "")[-400:])

        # ------------------------------------------------- 封装侧：必须 NG=0
        print("\n=== 3. check_footprint ===")
        r = run([sys.executable, os.path.join(CHK_PCB, "check_footprint.py"), fp,
                 "--outdir", os.path.join(tmp, "chk_pcb"),
                 "--spec", os.path.join(out, "E2E.fp.spec.json"), "--symbol", sym])
        check("check_footprint rc=0", r.returncode == 0, (r.stdout or "")[-500:])

        # --------------------------------- 3D 贴合：这条通路在别处全是 skip
        print("\n=== 4. 3D 贴合（另找一颗挂了 STEP 的官方封装）===")
        try:
            import toolchain
            share = toolchain.kicad_share(required=True)
            cand = None
            for p in (os.path.join(share, "footprints", "Package_TO_SOT_SMD.pretty",
                                   "SOT-23.kicad_mod"),):
                if os.path.exists(p):
                    cand = p
                    break
            if cand:
                r = run([sys.executable, os.path.join(CHK_PCB, "fit3d.py"),
                         cand, os.path.join(tmp, "f3")])
                check("fit3d rc=0（模型存在时应能渲染）", r.returncode == 0,
                      (r.stdout or "")[-300:])
                check("fit3d 产出了贴图",
                      os.path.exists(os.path.join(tmp, "f3", "fit3d_top.png")))
            else:
                print("  [skip] 本机官方库里没有可用的 SOT-23 测试样本")
        except SystemExit as e:
            print("  [skip] 找不到 KiCad share 目录：%s" % str(e).splitlines()[0])

    finally:
        if a.keep:
            print("\n保留目录：%s" % tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 66)
    if fails:
        print("  e2e 失败 %d 项：" % len(fails))
        for f in fails:
            print("    - %s" % f)
    else:
        print("  e2e 通过：spec -> 生成 -> 两个校验，NG 全为 0")
    print("=" * 66)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
