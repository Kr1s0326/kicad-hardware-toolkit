#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sch_erc.py - 独立通路 B：让 ERC 吐出 KiCad 认定的**引脚电气类型**。

关键认识
--------
孤立放一个符号跑 ERC，必然报一堆 pin_not_connected / pin_not_driven ——
那是"符号没接线"的必然结果，不是符号的缺陷。**这些要判为 expected。**

真正有价值的是 ERC 报告里每一行长得像

    @(89.84 mm, 87.30 mm): Symbol U1 Pin 8 [VBUS, Input, Line]

方括号里的 NAME / TYPE / STYLE 是 **Eeschema 解析符号后认定的电气类型**。
拿它和手册 Table 5-1 的 TYPE 列比，就独立地验证了"电气类型有没有写错" ——
而电气类型写错，尺寸校验和目视都看不出来（画出来一模一样）。

CLI
---
    python sch_erc.py <sch.kicad_sch> [--json out.json]
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

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:                                       # noqa: BLE001
    pass

from render import kicad_cli, run                       # noqa: E402

# 孤立符号跑 ERC 必然出现的项 —— 与符号质量无关
EXPECTED = {
    "pin_not_connected", "pin_not_driven", "power_pin_not_driven",
    "lib_symbol_issues", "footprint_link_issues", "lib_footprint_issues",
    "simulation_model_issue", "no_connect_connected",
}

# ERC 的英文类型 -> KiCad 符号里的 electrical type token
TYPE_MAP = {
    "input": "input",
    "output": "output",
    "bidirectional": "bidirectional",
    "tri state": "tri_state",
    "tri-state": "tri_state",
    "passive": "passive",
    "free": "free",
    "unspecified": "unspecified",
    "power input": "power_in",
    "power output": "power_out",
    "open collector": "open_collector",
    "open emitter": "open_emitter",
    "no connection": "no_connect",
    "no connect": "no_connect",
    "input power": "power_in",
}

VIOL_RE = re.compile(r"^\[(?P<kind>[^\]]+)\]:\s*(?P<msg>.*)$", re.M)
# @(x mm, y mm): Symbol U1 Pin 8 [VBUS, Input, Line]
PIN_RE = re.compile(r"Symbol\s+\S+\s+Pin\s+(\S+)\s+\[([^\]]*)\]")


def parse(text):
    """-> (violations, pins{num: {'name','type_raw','etype','style'}})"""
    viol, pins, cur = [], {}, None
    for raw in text.splitlines():
        line = raw.strip()
        m = VIOL_RE.match(line)
        if m:
            cur = {"kind": m.group("kind").strip(), "msg": m.group("msg").strip(),
                   "severity": "warning", "where": ""}
            viol.append(cur)
            continue
        if line.startswith("@"):
            if cur:
                cur["where"] = line
            for pm in PIN_RE.finditer(line):
                num, inside = pm.group(1), pm.group(2)
                parts = [p.strip() for p in inside.split(",")]
                pins[num] = {"name": parts[0] if parts else "",
                             "type_raw": parts[1] if len(parts) > 1 else "",
                             "style": parts[2] if len(parts) > 2 else "",
                             "etype": TYPE_MAP.get(
                                 (parts[1] if len(parts) > 1 else "").lower(), "?")}
    return viol, pins


def run_erc(sch, report=None):
    report = report or os.path.splitext(sch)[0] + ".erc.rpt"
    r = run([kicad_cli(), "sch", "erc", "-o", os.path.abspath(report),
             "--severity-all", os.path.abspath(sch)])
    txt = open(report, encoding="utf-8", errors="replace").read() \
        if os.path.exists(report) else ""
    txt += "\n" + (r.stdout or "") + (r.stderr or "")
    viol, pins = parse(txt)
    return {"report": report, "text": txt, "violations": viol, "pins": pins,
            "rc": r.returncode}


def is_expected(v):
    return v["kind"] in EXPECTED


@guard
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sch")
    ap.add_argument("--json")
    a = ap.parse_args()
    res = run_erc(a.sch)
    exp = [v for v in res["violations"] if is_expected(v)]
    odd = [v for v in res["violations"] if not is_expected(v)]
    print("report :", res["report"])
    print("违规   : %d（其中孤立符号必然出现的 %d，需要看的 %d）"
          % (len(res["violations"]), len(exp), len(odd)))
    for v in odd:
        print("  [需注意] %s: %s" % (v["kind"], v["msg"][:70]))
        if v["where"]:
            print("          ", v["where"])
    if not odd:
        print("  除孤立符号必然项外，无其它违规")
    print("\nKiCad 认定的引脚电气类型:")
    for n in sorted(res["pins"], key=lambda s: (len(s), s)):
        p = res["pins"][n]
        print("  %-4s %-10s %-16s -> %s" % (n, p["name"], p["type_raw"], p["etype"]))
    if a.json:
        json.dump(res, open(a.json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    return 0 if not odd else 9


if __name__ == "__main__":
    sys.exit(main())
