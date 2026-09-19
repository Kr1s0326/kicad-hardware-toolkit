#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pdf_pins.py - 从数据手册 PDF 里抠出引脚表（Table 5-1 那类）。

这是"要求"一侧的数据来源。它和 .kicad_sym 完全无关 —— 两个独立来源比对，
才能说明符号是照着手册做的，而不是照着我自己的记忆做的。

映射规则是启发式的，别当真理
----------------------------
手册的 TYPE 列写得很粗（"Digital output"），而 KiCad 需要区分
output / open_collector。所以映射时会看**描述列**：

    "Digital output" + 描述里含 "open-drain" / "open collector"  -> open_collector
    "Power supply" / "Ground"                                    -> power_in
    "Digital input" / "Analog input" / "Input"                   -> input
    "Digital output" / "Analog output" / "Output"                -> output

手册写法千奇百怪，所以：
  * `--dump` 把抽出来的表原样打出来，**必须人看一眼**再下结论；
  * `--overrides x.json` 用 {"pin": {"3": "open_collector"}} 覆盖个别行；
  * 抽不出来时报错，不猜。

CLI
---
    python pdf_pins.py <datasheet.pdf> [--page-table "Table 5-1"] [--dump]
                       [--json out.json] [--overrides x.json]
"""

import argparse
import json
import os
import re
import sys


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

TYPES = ("Digital input", "Digital output", "Power supply", "Ground",
         "Analog input", "Analog output", "Input", "Output",
         "Open-drain output", "Open drain output")

NAME = r"(?:IN\+|IN-|IN\u2013|[A-Za-z][A-Za-z0-9_+\-/]{0,11})"
ROW = re.compile(
    r"^(?P<num>\d{1,3})\s+(?P<name>" + NAME + r")\s+"
    r"(?P<type>" + "|".join(re.escape(t) for t in TYPES) + r")(?P<desc>.*)$",
    re.M)

OPEN_DRAIN = re.compile(r"open[\s-]?(?:drain|collector)", re.I)

MAP = {
    "digital input": "input", "analog input": "input", "input": "input",
    "digital output": "output", "analog output": "output", "output": "output",
    "open-drain output": "open_collector", "open drain output": "open_collector",
    "power supply": "power_in", "ground": "power_in",
}


def find_page(pdf, marker="Table 5-1"):
    """目录页也含 'Pin Configuration and Functions'，所以要用更独特的标记定位"""
    for i, p in enumerate(pdf.pages):
        t = p.extract_text() or ""
        if marker in t:
            return i, t
    for i, p in enumerate(pdf.pages):
        t = p.extract_text() or ""
        if "Pin Configuration and Functions" in t and "...." not in t:
            return i, t
    return None, ""


def extract(pdf_path, marker="Table 5-1", overrides=None):
    import pdfplumber
    overrides = overrides or {}
    with pdfplumber.open(pdf_path) as pdf:
        idx, text = find_page(pdf, marker)
        if idx is None:
            raise SystemExit("找不到引脚表页（试过 '%s'）。用 --dump 或手动给表。" % marker)
        pinout = ""
        m = re.search(r"^\s*(?:Not to scale\s*)?", text)
        # 顶视图那几行（CS 1 10 IN+）也一并留下，给人对照
        for line in text.splitlines():
            if re.match(r"^[A-Za-z~/\-+\u2013]+\s+\d+\s+\d+\s+[A-Za-z~/\-+\u2013]+\s*$", line.strip()):
                pinout += line.strip() + "\n"

        pins = {}
        for m in ROW.finditer(text):
            num, name, typ, desc = (m.group("num"), m.group("name"),
                                    m.group("type"), m.group("desc"))
            name = name.replace("\u2013", "-")
            etype = MAP.get(typ.lower(), "?")
            if etype == "output" and OPEN_DRAIN.search(desc):
                etype = "open_collector"
            pins[num] = {"name": name, "type_raw": typ, "desc": desc.strip(),
                         "etype": etype}
        for num, et in (overrides.get("pin") or {}).items():
            if num in pins:
                pins[num]["etype"] = et
                pins[num]["etype_source"] = "override"
        return {"page": idx + 1, "pins": pins, "pinout_drawing": pinout,
                "source": os.path.basename(pdf_path)}


@guard
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--page-table", default="Table 5-1")
    ap.add_argument("--overrides")
    ap.add_argument("--json")
    ap.add_argument("--dump", action="store_true")
    a = ap.parse_args()

    ov = json.load(open(a.overrides, encoding="utf-8")) if a.overrides else None
    r = extract(a.pdf, a.page_table, ov)
    print("pdf        :", r["source"])
    print("引脚表所在页:", r["page"])
    if r["pinout_drawing"]:
        print("图纸顶视图（供人工对照）:")
        for l in r["pinout_drawing"].splitlines():
            print("   ", l)
    if not r["pins"]:
        raise SystemExit("没抽到任何引脚行 —— 手册排版可能不同，"
                         "用 --dump 看原文，或改用 --overrides 手写")
    print("\n抽出的引脚表:")
    print("  %-4s %-8s %-16s %-18s %s" % ("pin", "name", "type(手册)", "etype(KiCad)", "desc"))
    for n in sorted(r["pins"], key=lambda s: (len(s), s)):
        p = r["pins"][n]
        print("  %-4s %-8s %-16s %-18s %s"
              % (n, p["name"], p["type_raw"], p["etype"], p["desc"][:40]))
    bad = [n for n, p in r["pins"].items() if p["etype"] == "?"]
    if bad:
        print("\n[!] 这些引脚的电气类型没能映射，需要人工确认或 --overrides: %s" % bad)
    if a.dump:
        import pdfplumber
        with pdfplumber.open(a.pdf) as pdf:
            print("\n---- 原文 ----")
            print(pdf.pages[r["page"] - 1].extract_text())
    if a.json:
        json.dump(r, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print("\njson:", a.json)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
