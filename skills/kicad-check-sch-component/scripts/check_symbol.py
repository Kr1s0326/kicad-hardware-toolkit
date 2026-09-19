#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_symbol.py - 原理图符号校验总入口：一条命令产出完整证据包。

    <outdir>/
    ├── EVIDENCE.md            逐项结论 + 每条的数据来源 + 必须人看的图
    ├── look_sheet.png         ★ 图拼一张，一次看完
    ├── lint.txt               规矩检查（间距/分组/栅格/本体）
    ├── netlist.net            KiCad 自己解析符号后导出的网表
    ├── erc.rpt                ERC 原始报告
    ├── sym/<name>.png         ★ 符号渲染图
    └── cmp_pins.json          PDF <-> 网表 <-> ERC 的三方比对明细

四条通路，缺一条就有一类错误抓不到
----------------------------------
    A 规矩    纯几何          间距/分组/栅格/本体/名字溢出（手册不规定，只能查规范）
    B 网表    KiCad 读符号     引脚号/名的结构错
    C ERC     KiCad 判电气     引脚电气类型错（画出来一模一样，尺寸也全对）
    D 目视    渲染后肉眼      分组观感、名字压字、Pin1 朝向、本体比例
    E 契约    与封装交叉      与符号侧另一半合起来才成立

CLI
---
    python check_symbol.py <lib.kicad_sym> --outdir out [选项]

    --symbol INA239         库里有多个符号时指定
    --pdf ina239.pdf        有则与手册 Table 5-1 三方比对（强烈建议给）
    --pdf-table "Table 5-1" 手册里表的标记
    --groups g.json         分组声明（不给则"组间间距"规则无法真正校验）
    --min-pitch 200         组内最小间距，mil
    --group-gap 400         组间最小间距，mil
    --footprint fp.kicad_mod 有则做 引脚<->焊盘 契约交叉
"""

import argparse
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

import render                                          # noqa: E402


def step(n, title):
    print("\n" + "=" * 66)
    print("[%d] %s" % (n, title))
    print("=" * 66)


def norm(name):
    """~{CS} / CS  ->  CS ；IN– / IN-  ->  IN-"""
    return name.replace("~{", "").replace("}", "").replace("\u2013", "-").strip()


@guard
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lib")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--symbol")
    ap.add_argument("--pdf", help="数据手册 PDF（自动抽引脚表）")
    ap.add_argument("--pins-json", dest="pins_json",
                    help="要求表 {\"A11\": {\"name\":..,\"etype\":..}, ...}；"
                         "与 --pdf 二选一，用于 pdf_pins 认不了的排版/语言")
    ap.add_argument("--pdf-table", default="Table 5-1")
    ap.add_argument("--groups", help="分组声明（必填）。来自创建侧刚生成的 groups.json")
    # 默认 None：优先用命令行显式给的值，其次用 groups.json 里的 _style，
    # 最后才回退到 200/400。这样改了创建侧的规范，校验侧自动跟上。
    ap.add_argument("--min-pitch", type=float, default=None, help="mil，默认取 groups.json 的 _style，否则 200")
    ap.add_argument("--group-gap", type=float, default=None, help="mil，默认取 groups.json 的 _style，否则 400")
    ap.add_argument("--grid", type=float, default=50.0, help="mil")
    ap.add_argument("--footprint")
    ap.add_argument("--judgments",
                    help="创建侧产出的 <LIB>.judgments.md（eType/分组的判断依据）；"
                         "不给就去 .kicad_sym 旁边找")
    a = ap.parse_args()

    # 要求侧必备。本 skill 的结论全部是「手册 ↔ KiCad 自己的解读」的对比：
    # 没有要求表，就只剩下一堆几何量测与网表导出 —— 数字都对，但**没人知道
    # 对不对**。而汇总行与退出码看上去还是绿的，那比不跑更坏。
    require(bool(a.pdf or a.pins_json),
            "必须给要求表：--pdf（数据手册）或 --pins-json（人工录入的要求表）。\n"
            "\n本 skill 的核心产出是『手册引脚表 ↔ 网表 ↔ ERC』三方比对。\n"
            "没有要求表就无法比对，只剩几何量测和网表导出，那不算校验。\n"
            "如果只是想过一遍绘制规范，用 symbol_lint.py（它不需要手册）。")
    # 分组同样是要输入的：“组间 400 mil” 这条规则**几何上不可判** ——
    # 把 IN+ 挪一格就能得到另一个同样合规的分组。没有声明就只能几何推断，
    # 而推断结果只能拿来对账，不能当真。
    require(bool(a.groups),
            "必须给 --groups（分组声明）。\n"
            "\n『组间 400 mil』几何上不可判：左侧 VBUS|IN+|IN- 把 IN+ 挪一格，\n"
            "间距就从 400/200 变成 200/400 —— 两种都合规，只是分组不同。\n"
            "所以分组是**输入**。正常流程里创建侧的 groups.json 就是它。")

    lib = os.path.abspath(a.lib)
    out = os.path.abspath(a.outdir)
    os.makedirs(out, exist_ok=True)
    evidence, look = [], []
    cmp_rows = []

    def ev(item, result, source, detail=""):
        evidence.append({"item": item, "result": result, "source": source,
                         "detail": detail})

    # ------------------------------------------------------------ A 规矩
    step(1, "规矩检查  (通路 A: 纯几何，手册不规定，只能查规范)")
    import symbol_lint as sl
    dec = json.load(open(a.groups, encoding="utf-8")) if a.groups else None
    # 创建侧会把绘制规范一起写进 groups.json 的 _style 里，这里跟上，
    # 免得改了 spec 的规范而校验侧还用旧默认值 -> 一堆假 FAIL。
    style = (dec or {}).get("_style") if isinstance(dec, dict) else None
    style = style if isinstance(style, dict) else {}
    min_pitch = a.min_pitch if a.min_pitch is not None else float(style.get("pitch_mil", 200.0))
    group_gap = a.group_gap if a.group_gap is not None else float(style.get("group_gap_mil", 400.0))
    if style and a.min_pitch is None and a.group_gap is None:
        print("  (规范取自 groups.json: 组内 %g mil, 组间 %g mil)"
              % (min_pitch, group_gap))
    if dec is None:
        print("  [!] 没给 --groups：分组只能几何推断，"
              "「组间 %g mil」这条规则**无法真正校验**" % group_gap)
    elif not any(isinstance(dec.get(s), list) for s in
                 ("left", "right", "top", "bottom")):
        # 文件加载了但里面没有认得出来的边 -> 与没给等价。
        # 不能静默：用户会以为分组查过了，实际根本没查。
        print("  [!] --groups %s 里没有 left/right/top/bottom 任何一个，"
              "等同于没给分组 —— 「组间 %g mil」这条规则**未校验**。\n"
              "      正确的形状见 assets/groups_template.json"
              % (os.path.basename(a.groups), group_gap))
    r = sl.lint(lib, a.symbol, min_pitch, group_gap, a.grid, declared=dec)
    lines = ["symbol: %s" % r["symbol"], "body: %s" % (r["body"],), ""]
    for side, g in r["sides"].items():
        lines.append("%-6s base=%.1f mil gaps=%s groups=%s"
                     % (side, g["base_pitch_mil"],
                        [round(v, 1) for v in g["gaps_mil"]], g["groups"]))
    for kind, msg in r["issues"]:
        lines.append("FAIL %-12s %s" % (kind, msg))
    for kind, msg in r["warnings"]:
        lines.append("warn %-12s %s" % (kind, msg))
    open(os.path.join(out, "lint.txt"), "w", encoding="utf-8").write(
        "\n".join(lines) + "\n")
    for kind, msg in r["issues"]:
        print("  [FAIL] %-12s %s" % (kind, msg))
    for kind, msg in r["warnings"]:
        print("  [warn] %-12s %s" % (kind, msg))
    if not r["issues"] and not r["warnings"]:
        print("  无疑问项")
    ev("规矩(间距/分组/栅格/本体)", "PASS" if not r["issues"] else "NG",
       "纯几何解析 .kicad_sym",
       "%d FAIL / %d warn；分组声明: %s" % (len(r["issues"]), len(r["warnings"]),
                                          "有" if dec else "无(规则无法真正校验)"))

    # ------------------------------------------------------------ B 网表
    step(2, "网表  (通路 B: KiCad 自己解析这个符号)")
    import sch_netlist as snl
    nl = snl.export_netlist(lib, out, a.symbol)
    print("  sch:", nl["sch"])
    print("  KiCad 解析出 %d 个引脚: %s"
          % (len(nl["pins"]), " ".join("%s=%s" % (k, v) for k, v in
                                       sorted(nl["pins"].items(),
                                              key=lambda kv: (len(kv[0]), kv[0])))))
    print("  Footprint 字段:", nl["fields"].get("Footprint", "(空)") or "(空)")
    ev("网表(引脚号/名)", "见比对" if a.pdf else "PASS", "kicad-cli sch export netlist",
       "%d 引脚" % len(nl["pins"]))

    # ------------------------------------------------------------ C ERC
    step(3, "ERC  (通路 C: KiCad 判定引脚电气类型)")
    import sch_erc as se
    er = se.run_erc(nl["sch"])
    odd = [v for v in er["violations"] if not se.is_expected(v)]
    print("  违规 %d 项，其中孤立符号必然出现 %d，需要看的 %d"
          % (len(er["violations"]), len(er["violations"]) - len(odd), len(odd)))
    for v in odd:
        print("    [需注意] %s: %s" % (v["kind"], v["msg"][:60]))
    ev("ERC", "PASS" if not odd else "NG", "kicad-cli sch erc",
       "%d 项需注意(已扣掉孤立符号必然项)" % len(odd))

    # ------------------------------------------- 要求表（手册）三方比对
    # “要求”一列的来源有两个，互斥：
    #   --pdf        pdf_pins.py 自动从手册 PDF 抽（仅限它能认的排版/语言）
    #   --pins-json  人工录好的引脚表（手册是中文/其他版式时用这个）
    # 后者是必要的退路：pdf_pins 的 ROW 正则只认 TI 那套英文类型词，
    # 碰到 Espressif 的 模拟/电源/IO 就抽不出任何东西（实测算过）。
    pdf_pins = {}
    if a.pdf or a.pins_json:
        if a.pins_json:
            step(4, "与手册三方比对  (手工录入的要求表 <-> 网表 <-> ERC)")
            raw = json.load(open(a.pins_json, encoding="utf-8"))
            src = os.path.basename(a.pins_json)
            for k, v in raw.items():
                pdf_pins[str(k)] = v if isinstance(v, dict) else {
                    "name": v[0] if isinstance(v, (list, tuple)) else str(v),
                    "etype": v[1] if isinstance(v, (list, tuple)) and len(v) > 1 else "-"}
            print("  要求表: %s（%d 个引脚，人工录入）" % (src, len(pdf_pins)))
        else:
            step(4, "与数据手册三方比对  (PDF <-> 网表 <-> ERC)")
            import pdf_pins as pp
            pdf_pins = pp.extract(os.path.abspath(a.pdf), a.pdf_table)["pins"]
            src = os.path.basename(a.pdf) + " / " + a.pdf_table
        nums = sorted(set(list(pdf_pins) + list(nl["pins"])),
                      key=lambda s: (len(s), s))
        bad = 0
        for n in nums:
            p = pdf_pins.get(n, {})
            k = nl["pins"].get(n, "")
            e = er["pins"].get(n, {})
            name_ok = norm(p.get("name", "")) == norm(k) if p and k else False
            # 三态：True 对 / False 错 / None 要求一侧未声明。
            # 手册不给 TYPE 列时（很多 MCU 的引脚表就只有球号 + 信号名），
            # 拿 None 去比 ERC 推导出的类型会**每个引脚都报 NG**，汇总变成
            # “49/49 不一致”，而名字其实全对。假 NG 比不查更糟。
            want_t = p.get("etype") if p else None
            if want_t in (None, "", "-"):
                type_ok = None if (p and e) else False
            else:
                type_ok = (want_t == e.get("etype")) if (p and e) else False
            if not name_ok or type_ok is False:
                bad += 1
            cmp_rows.append({"pin": n, "pdf_name": p.get("name", "-"),
                             "netlist_name": k or "-",
                             "pdf_etype": p.get("etype", "-"),
                             "erc_etype": e.get("etype", "-"),
                             "name_ok": name_ok, "type_ok": type_ok,
                             "pdf_type_raw": p.get("type_raw", "-")})
        print("  %-4s %-8s %-10s %-8s | %-14s %-14s %s"
              % ("pin", "PDF名", "网表名", "名?", "PDF电气", "ERC电气", "类?"))
        for c in cmp_rows:
            print("  %-4s %-8s %-10s %-8s | %-14s %-14s %s"
                  % (c["pin"], c["pdf_name"], c["netlist_name"],
                     "ok" if c["name_ok"] else "NG",
                     c["pdf_etype"], c["erc_etype"],
                     {True: "ok", False: "NG", None: "未声明"}[c["type_ok"]]))
        json.dump(cmp_rows, open(os.path.join(out, "cmp_pins.json"), "w",
                                 encoding="utf-8"), ensure_ascii=False, indent=2)
        # 引脚比对表（xlsx）—— 与封装侧的尺寸测量表同一套样式与判定习惯
        import pin_report as pr
        xlsx = pr.build_xlsx(
            cmp_rows, os.path.join(out, r["symbol"] + "_pins.xlsx"),
            title=r["symbol"],
            notes=[
                "要求列（手册名 / 手册类型）：来自 " + src + "。",
                "实测列（网表名 / ERC类型）：kicad-cli sch export netlist 与 sch erc 的输出。",
                "两列都不取自 .kicad_sym 的文本，所以能抓到「文本看着对、KiCad 读出来不对」的错。",
                "任一侧缺这个引脚即判 NG（少引脚与多引脚都是真错）。",
                "规格检查（间距 / 分组 / 栅格 / 本体）的结果在 lint.txt 与 EVIDENCE.md 里。",
            ])
        print("  引脚比对表:", xlsx)
        n_na = sum(1 for c in cmp_rows if c["type_ok"] is None)
        ev("引脚比对表(xlsx)",
           "PASS" if bad == 0 else "NG",
           "pdfplumber 读手册 -> 网表名 + ERC 类型（都不读我的 .kicad_sym）",
           "%d/%d 引脚不一致 -> %s" % (bad, len(nums), os.path.basename(xlsx)))
        ev("手册比对(号/名/电气类型)",
           "PASS" if bad == 0 else "NG",
           "同上，明细见引脚比对表",
           "%d/%d 引脚不一致%s"
           % (bad, len(nums),
              "；%d 项电气类型未声明（要求表没有类型列），未参与判定" % n_na
              if n_na else ""))
    else:
        print("\n(未给 --pdf：跳过与手册的比对 —— 这是最重要的一条，建议补上)")
        ev("引脚比对表(xlsx)", "未做", "-", "需要 --pdf（没有手册就没有「要求」一列）")
        ev("手册比对", "未做", "-", "需要 --pdf")

    # ------------------------------------------------------------ D 目视
    step(5, "渲染  (通路 D: 供肉眼审)")
    svgdir = os.path.join(out, "sym")
    svgs = render.render_lib("sym", lib, svgdir)
    for s in svgs:
        png = os.path.splitext(s)[0] + ".png"
        render.svg2png(s, png, width=760)
        print("  ->", os.path.basename(png))
        look.append(png)
    ev("渲染目视", "待目视", "kicad-cli sym export svg -> PNG",
       "分组观感 / 名字压字 / 电源脚朝向 / 本体比例")

    # ------------------------------------------------------------ E 契约
    step(6, "引脚<->焊盘 契约  (通路 E: 与封装交叉)")
    if a.footprint:
        import pinmap as pm
        r2 = pm.check_footprint(a.footprint)
        pads = set(r2["pads"])
        syms = set(nl["pins"])
        miss_pad = sorted(syms - pads, key=lambda s: (len(s), s))
        print("  符号有、封装没有的引脚:", miss_pad or "无")
        ev("引脚<->焊盘契约", "PASS" if not miss_pad else "NG",
           "网表引脚集 vs .kicad_mod 焊盘集",
           "符号 %d 引脚 / 封装 %d 焊盘" % (len(syms), len(pads)))
    else:
        print("  (未给 --footprint，跳过)")
        ev("引脚<->焊盘契约", "未做", "-", "需要 --footprint")

    # ------------------------------------------------------------ 汇总
    step(7, "汇总")
    if look:
        sheet = os.path.join(out, "look_sheet.png")
        render.contact_sheet(look, sheet, cols=min(3, len(look)), cell=620,
                             title="%s - look at this" % r["symbol"])
        print("look sheet:", sheet)
    # 判断依据：`etype` / `groups` 手册里没有，是判出来的。把依据带进证据包，
    # 复核的人不必把手册重读一遍再判一次。
    jdg = a.judgments
    if not jdg:
        d = os.path.dirname(lib)
        guess = os.path.join(os.path.dirname(d), os.path.basename(d) + ".judgments.md")
        jdg = guess if os.path.exists(guess) else None
    jd_text = ""
    if jdg and os.path.exists(jdg):
        ev("判断依据(eType/分组)", "见文档", os.path.basename(jdg),
           "**不是检查项** —— 创建侧写下的判断依据，供复核时追溯")
        jd_text = open(jdg, encoding="utf-8").read()
    else:
        ev("判断依据(eType/分组)", "未做", "-",
           "没有 judgments.md —— eType 与分组是怎么判的无从追溯"
           "（创建侧的 make_part.py 会生成）")

    md = os.path.join(out, "EVIDENCE.md")
    with open(md, "w", encoding="utf-8") as f:
        f.write(evidence_head("符号校验证据包 - %s" % r["symbol"],
                  "符号文件: `%s`" % lib, evidence))
        f.write("| 检查项 | 结果 | 数据来源（独立性） | 明细 |\n|---|---|---|---|\n")
        for e in evidence:
            f.write("| %s | %s | %s | %s |\n"
                    % (e["item"], e["result"], e["source"], e["detail"]))
        f.write("\n## 必须人看的图\n\n")
        for p in look:
            f.write("* `%s`\n" % os.path.relpath(p, out).replace("\\", "/"))
        f.write("\n看图要点见 `references/render-and-look.md`。\n")
        if jd_text:
            f.write(chr(10) + "---" + chr(10) * 2)
            f.write(jd_text if jd_text.endswith(chr(10)) else jd_text + chr(10))
    print("evidence:", md)

    sys.exit(summary(evidence, 9,
                     "下一步: 打开 look_sheet.png 与 EVIDENCE.md，逐图确认后再下结论。"))


if __name__ == "__main__":
    sys.exit(main())