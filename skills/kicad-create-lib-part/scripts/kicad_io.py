#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kicad_io.py - KiCad s-expression 的写出原语 + 可加载性检查。

设计原则
--------
1. **只写"能被 KiCad 加载"的文本，不判断"是否正确"。**
   正确性是 kicad-check-* 两个 skill 的事。本模块只保证产出的文件
   能被 kicad-cli 解析 —— 否则等于没产出。

2. **版本号写死当前安装的 KiCad 的格式版本。**
   写老版本号 KiCad 会自己升级，但生成时就写对更省事。
   `probe_formats()` 会去读本机官方库文件，拿到真实版本号，不靠记忆。

3. **格式细节都参照本机官方库的真实文件**（Amplifier_Current.kicad_sym、
   Package_SO.pretty/*.kicad_mod），不凭印象写 token。

CLI
---
    python kicad_io.py probe                 # 打印本机 KiCad 的格式版本
    python kicad_io.py loadable <path>       # 检查文件/库能否被 kicad-cli 加载
"""

import glob
import os
import re
import shutil
import subprocess
import sys

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:                                       # noqa: BLE001
    pass

KICAD_CLI_CANDIDATES = [
    r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe",
    r"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe",
    r"C:\Program Files\KiCad\8.0\bin\kicad-cli.exe",
    "/usr/bin/kicad-cli", "/usr/local/bin/kicad-cli",
]
KICAD_SHARE_CANDIDATES = [
    r"C:\Program Files\KiCad\10.0\share\kicad",
    r"C:\Program Files\KiCad\9.0\share\kicad",
    "/usr/share/kicad",
]

MIL = 0.0254


def kicad_cli():
    p = os.environ.get("KICAD_CLI")
    if p and os.path.exists(p):
        return p
    for c in KICAD_CLI_CANDIDATES:
        if os.path.exists(c):
            return c
    w = shutil.which("kicad-cli")
    if w:
        return w
    raise SystemExit("kicad-cli not found - set the KICAD_CLI env var")


def kicad_share():
    for c in KICAD_SHARE_CANDIDATES:
        if os.path.isdir(c):
            return c
    return None


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


# --------------------------------------------------------------- 版本探测
def probe_formats():
    """去读本机官方库文件的真实 version token，不靠记忆填日期"""
    sh = kicad_share()
    out = {}
    if not sh:
        return out
    for name in ("Amplifier_Current", "Sensor_Energy", "Device"):
        p = os.path.join(sh, "symbols", name + ".kicad_sym")
        if os.path.exists(p):
            m = re.search(r'\(version (\d+)\)', open(p, encoding="utf-8").read(2000))
            if m:
                out["sym_version"] = m.group(1)
                break
    for rel in ("Package_SO.pretty/SOIC-8_3.9x4.9mm_P1.27mm.kicad_mod",
                "Resistor_SMD.pretty/R_0603_1608Metric.kicad_mod"):
        p = os.path.join(sh, "footprints", rel)
        if os.path.exists(p):
            txt = open(p, encoding="utf-8").read(3000)
            m = re.search(r'\(version (\d+)\)', txt)
            g = re.search(r'\(generator_version "([^"]*)"', txt)
            if m:
                out["fp_version"] = m.group(1)
            if g:
                out["generator_version"] = g.group(1)
            break
    out["kicad_cli"] = kicad_cli()
    return out


# --------------------------------------------------------------- 写出原语
def num(v):
    """数字格式化：去掉尾巴零，避免 2.5400000000000005 这种"""
    if isinstance(v, int):
        return str(v)
    s = ("%.6f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def ind(block, n=1):
    pad = "\t" * n
    return "".join(pad + l if l.strip() else l for l in block.splitlines(True))


def lines(*items):
    return "".join(i if i.endswith("\n") else i + "\n" for i in items if i)


def effects(size=1.27, hide=False, justify=None, thickness=None):
    s = ["\t\t\t(effects", "\t\t\t\t(font"]
    s.append("\t\t\t\t\t(size %s %s)" % (num(size), num(size)))
    if thickness:
        s.append("\t\t\t\t\t(thickness %s)" % num(thickness))
    s.append("\t\t\t\t)")
    if justify:
        s.append("\t\t\t\t(justify %s)" % justify)
    if hide:
        s.append("\t\t\t\t(hide yes)")
    s.append("\t\t\t)")
    return "\n".join(s)


# --------------------------------------------------------------- 可加载性
def loadable(path, kind=None):
    """跑 kicad-cli 的解析器，判断产出物能不能被 KiCad 加载。

    注意：**不能只看 returncode** —— 必须同时看输出信息。
    `已使用最新格式成功保存` / `未更新` = 好；`无法加载` = 坏。
    这里的 rc 在两种情况下都是可信的（kicad-cli 对加载失败返回 2），
    但仍然把消息一起返回，方便报错时定位。
    """
    kind = kind or ("sym" if path.endswith(".kicad_sym") else "fp")
    r = run([kicad_cli(), kind, "upgrade", os.path.abspath(path)])
    msg = ((r.stdout or "") + (r.stderr or "")).strip().replace("\r", "")
    first = msg.splitlines()[0] if msg else ""
    bad = ("无法加载" in msg) or ("could not" in msg.lower()) or r.returncode != 0
    return (not bad), r.returncode, first


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "probe":
        for k, v in probe_formats().items():
            print("%-20s %s" % (k, v))
    elif cmd == "loadable":
        ok, rc, msg = loadable(sys.argv[2])
        print("%s  rc=%d  %s" % ("OK  " if ok else "BAD ", rc, msg))
        return 0 if ok else 2
    else:
        print(__doc__)


if __name__ == "__main__":
    sys.exit(main() or 0)
