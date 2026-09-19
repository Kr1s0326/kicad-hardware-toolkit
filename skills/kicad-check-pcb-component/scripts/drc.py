#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
drc.py - 用 kicad-cli 的 DRC 抓"尺寸表抓不到"的那类封装缺陷。

尺寸测量表只能证明焊盘数字对。下面这些它完全无感：

    丝印压在阻焊开窗上   -> 装配时字被擦掉 / 虚焊
    外框（courtyard）缺失或太小 -> 贴片机撞件、布局挤件
    焊盘间距不足         -> 蚀刻/贴装困难

DRC 正好覆盖这几项。所以它是尺寸校验的**互补**，不是重复。

重要：kicad-cli 的 DRC 即使发现违规，**进程返回码也可能是 0**
（只有加了 --exit-code-violations 才是非 0）。所以必须解析报告正文，
不能只看 returncode —— 这一点在 SKILL.md 里也强调了。

CLI
---
    python drc.py <board.kicad_pcb>                 # 跑，打印分类结果
    python drc.py <footprint.kicad_mod> --board-out b.kicad_pcb
    python drc.py <board.kicad_pcb> --json out.json

    # 退出码：0 = 无阻断性违规，6 = 有
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
from cli import guard                             # noqa: E402

from core import board as board_mod                            # noqa: E402
from render import kicad_cli, run                              # noqa: E402

# Windows 控制台常是 GBK；输出里若出现 GBK 以外的字符（↔ ✅ 之类）会直接抛
# UnicodeEncodeError 打断整个检查。这里保留原编码，只把无法映射的字符降级。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:                                       # noqa: BLE001
    pass

# DRC 报的是英文/中文都可能（跟随 KiCad 界面语言），两类关键词都收
SEV = {"error": "error", "错误": "error",
       "warning": "warning", "警告": "warning",
       "exclusion": "excluded", "排除": "excluded"}

# 与封装质量无关、不值得阻断的项
BENIGN = ("lib_footprint_issues", "not in the library", "封装库")

# 位号 / 值 字段的名字。KiCad 的 DRC 报告里出现这些词，说明这一方是**标注文字**。
FIELD_WORDS = ("reference field", "value field", "reference field of",
               "位号字段", "值字段", "reference field of ref",
               "reference field of val")

VIOL_RE = re.compile(r"^\[(?P<kind>[^\]]+)\]:\s*(?P<msg>.*)$", re.M)


def parse_report(text):
    """-> [{'kind','msg','severity','where'}]"""
    out = []
    cur = None
    for raw in text.splitlines():
        line = raw.rstrip()
        m = VIOL_RE.match(line.strip())
        if m:
            cur = {"kind": m.group("kind").strip(), "msg": m.group("msg").strip(),
                   "severity": "warning", "where": ""}
            out.append(cur)
            continue
        low = line.strip().lower()
        for k, v in SEV.items():
            if cur is not None and low == k:
                cur["severity"] = v
        if cur is not None and line.strip().startswith("@"):
            # 一条违规有两方（@ 行出现两次）。以前只留最后一行，于是
            # “谁碰了谁”丢掉一半 —— 下面按“位号字段 vs 丝印”分类就做不了。
            cur["where"] = (cur["where"] + " | " + line.strip()).lstrip(" |")
    return out


def benign_reason(v):
    """-> 非阻断的理由；应当阻断则返回 None。

    两类不算封装缺陷：

    1. 测试板没注册封装库 —— 与封装本身无关（lib_footprint_issues）。

    2. **丝印文字与丝印记号相交，而其中一方是位号/值字段**。这是 KiCad 官方
       封装里很常见的现象（实测 38 个官方封装里 4 个中招），但它是**标注
       摆放**问题：位号文字的位置存在封装里、上板后由用户按需移动，不属于
       封装的制造性。真正的缺陷是“丝印压到焊盘上”（silk_over_copper），
       那条仍然阻断。

    注意“不算 NG”不等于“不说”：这两类会原样打进报告，只是不当阻断。
    """
    blob = (v["kind"] + " " + v["msg"]).lower()
    if any(b.lower() in blob for b in BENIGN):
        return "测试板没注册封装库，与封装质量无关"
    # 只放 silk_overlap（丝印 vs 丝印），**绝不放 silk_over_copper**
    # （丝印/文字压在焊盘上）—— 后者是真的制造缺陷：阻焊开窗被压、
    # 文字印在焊盘上会被抹掉。“双方都是丝印”才是可移动的标注摆放问题。
    # （这条一开始写宽了，是 selftest 里的 silk_over_copper 用例抳回来的。）
    w = (v.get("where") or "").lower()
    if ("silk_overlap" in blob and "silk_over_copper" not in blob
            and any(t in w for t in FIELD_WORDS)):
        return "位号/值文字的摆放问题（上板后可移动），不是封装几何缺陷"
    # 孔径超范围：测试板上没有工艺参数，KiCad 用的是默认最小孔径 0.3mm。
    # QFN 热过孔常见 0.2mm —— 那是需要特定工艺，不是**封装画错**。
    # 真需要卡这个就得把工艺参数写进 spec（目前 schema 里没有）。
    if "drill_out_of_range" in blob or "孔径超出范围" in blob:
        return ("测试板没有工艺参数，KiCad 用了默认最小孔径；"
                "孔径本身是封装设计值，需按你的板厂能力判")
    return None


def is_benign(v):
    return benign_reason(v) is not None


def run_drc(pcb, report=None, severity_all=True):
    report = report or os.path.splitext(pcb)[0] + ".drc.rpt"
    cmd = [kicad_cli(), "pcb", "drc", "-o", os.path.abspath(report),
           "--severity-all", "--exit-code-violations", os.path.abspath(pcb)]
    r = run(cmd)
    txt = ""
    if os.path.exists(report):
        txt = open(report, encoding="utf-8", errors="replace").read()
    txt += "\n" + (r.stdout or "") + (r.stderr or "")
    return {"rc": r.returncode, "report": report, "text": txt,
            "violations": parse_report(txt)}


@guard
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help=".kicad_pcb, or a .kicad_mod to wrap first")
    ap.add_argument("--board-out", help="where to write the wrap board")
    ap.add_argument("--lib-id", help="LIB:NAME to give the embedded footprint")
    ap.add_argument("--json")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    pcb = a.input
    if a.input.endswith(".kicad_mod"):
        pcb = a.board_out or os.path.join(os.path.dirname(os.path.abspath(a.input)),
                                          "_drc_board.kicad_pcb")
        board_mod.write_pcb(pcb, a.input, a.lib_id)

    res = run_drc(pcb)
    blocking = [v for v in res["violations"] if not is_benign(v)]

    if not a.quiet:
        print("board    :", pcb)
        print("report   :", res["report"])
        print("violations: %d (其中与封装质量相关 %d)"
              % (len(res["violations"]), len(blocking)))
        for v in res["violations"]:
            flag = "  (忽略)" if is_benign(v) else ""
            print("  [%s] %s: %s%s" % (v["severity"], v["kind"], v["msg"][:70], flag))
            if v["where"]:
                print("        ", v["where"])
        if not res["violations"]:
            print("  none - 丝印不压盘、外框完整、无间距违规")

    if a.json:
        json.dump(res, open(a.json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        print("json:", a.json)

    sys.exit(6 if blocking else 0)


if __name__ == "__main__":
    sys.exit(main())