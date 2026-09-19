#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
toolchain.py - 外部工具定位（kicad-cli / chrome / KiCad share）。

为什么要有这个文件
------------------
"去哪儿找 kicad-cli / chrome / KiCad 库目录" 这件事，之前在三处各写了一遍
（render.py、kicad_io.py、build_testboards.py），三份候选列表互不相同，
其中 build_testboards.py 还硬编码了 `C:\\Program Files\\KiCad\\10.0\\...` ——
升级 KiCad 或换台机器就直接挂。现在只有这一份。

两个刻意的行为
--------------
1. **KiCad share 目录从 kicad-cli 的位置推导**，不写版本号。
   找到 `…/KiCad/10.0/bin/kicad-cli.exe` 就知道 share 在 `…/KiCad/10.0/share/kicad`。
   升级到 11.0 自动跟上，不用改代码。

2. **环境变量指向不存在的路径时报错，不静默回退。**
   以前 `KICAD_CLI=/wrong/path` 会悄悄用回自动探测的结果，用户以为自己的设置生效了。

CLI
---
    python toolchain.py            # 打印找到的路径（诊断用）
"""

import os
import re
import shutil
import subprocess
import sys
from cli import guard                             # noqa: E402

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:                                       # noqa: BLE001
    pass

# 只是"猜"的起点；真正的定位优先走环境变量和 PATH
KICAD_CLI_CANDIDATES = [
    r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe",
    r"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe",
    r"C:\Program Files\KiCad\8.0\bin\kicad-cli.exe",
    "/usr/bin/kicad-cli",
    "/usr/local/bin/kicad-cli",
    "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
]

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

KICAD_SHARE_CANDIDATES = [
    r"C:\Program Files\KiCad\10.0\share\kicad",
    r"C:\Program Files\KiCad\9.0\share\kicad",
    r"C:\Program Files\KiCad\8.0\share\kicad",
    "/usr/share/kicad",
    "/usr/local/share/kicad",
    "/Applications/KiCad/KiCad.app/Contents/SharedSupport",
]

_CACHE = {}


def _resolve(env_name, candidates, what, hint):
    """环境变量 > 候选路径 > PATH；环境变量设了但无效则报错（不回退）。"""
    val = os.environ.get(env_name)
    if val:
        if os.path.exists(val):
            return val
        raise SystemExit(
            "环境变量 %s 指向的路径不存在：\n  %s\n"
            "因为你显式设置了它，这里**不会**回退到自动探测。\n"
            "如果想让自动探测生效，请清掉这个环境变量。" % (env_name, val))
    for c in candidates:
        if os.path.exists(c):
            return c
    seen, names = set(), []
    for c in candidates:
        n = os.path.basename(c)
        if n not in seen:
            seen.add(n)
            names.append(n)
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    raise SystemExit("找不到 %s。\n%s\n（也可以用环境变量 %s=/path/to/it 指定）"
                     % (what, hint, env_name))


def kicad_cli():
    if "kicad_cli" not in _CACHE:
        _CACHE["kicad_cli"] = _resolve(
            "KICAD_CLI", KICAD_CLI_CANDIDATES, "kicad-cli",
            "kicad-cli 随 KiCad 一起安装；请确认 KiCad 已装且版本 >= 8。")
    return _CACHE["kicad_cli"]


def chrome():
    if "chrome" not in _CACHE:
        _CACHE["chrome"] = _resolve(
            "CHROME", CHROME_CANDIDATES, "chrome / edge / chromium",
            "SVG 转 PNG 需要一个基于 Chromium 的浏览器（无头模式）。")
    return _CACHE["chrome"]


def kicad_share(required=False):
    """KiCad 的 share/kicad 目录（含 symbols / footprints / 3dmodels）。

    优先从 kicad-cli 的位置推导 —— 这样升级 KiCad 不用改代码。
        找到 …/KiCad/10.0/bin/kicad-cli.exe
        推出 …/KiCad/10.0/share/kicad
    """
    if "share" in _CACHE:
        return _CACHE["share"]
    out = None
    try:
        cli = kicad_cli()
        root = os.path.dirname(os.path.dirname(os.path.abspath(cli)))
        for sub in (os.path.join("share", "kicad"), "SharedSupport"):
            cand = os.path.join(root, sub)
            if os.path.isdir(cand):
                out = cand
                break
    except SystemExit:
        # kicad-cli 完全没找到时，share 还可以靠候选列表猜。
        # 但如果用户**显式**设错了 KICAD_CLI，错误必须冒出来，不能吞。
        if os.environ.get("KICAD_CLI"):
            raise
    if out is None:
        out = next((c for c in KICAD_SHARE_CANDIDATES if os.path.isdir(c)), None)
    if out is None and required:
        raise SystemExit(
            "找不到 KiCad 的 share/kicad 目录（库文件所在处）。\n"
            "它应该和 kicad-cli 在同一个 KiCad 安装目录下。")
    _CACHE["share"] = out
    return out


def render_dir(hint="3dmodels"):
    """share/kicad/3dmodels 之类的子目录"""
    sh = kicad_share()
    return os.path.join(sh, hint) if sh else None


def kicad_major():
    """本机 KiCad 的主版本号（如 10）。从 kicad-cli 的路径里读，读不到返回 None。

    用途：生成封装时把 ${KICAD10_3DMODEL_DIR} 里的版本号写成本机实际版本，
    这样产出的文件在当前 KiCad 上 3D 能正常显示。
    """
    try:
        cli = os.path.abspath(kicad_cli())
    except SystemExit:
        return None
    m = re.search(r"[\\/]KiCad[\\/](\d+)\.", cli)
    if m:
        return int(m.group(1))
    m = re.search(r"kicad[- ]?(\d+)\.", cli, re.I)
    return int(m.group(1)) if m else None


def run(cmd, **kw):
    """统一的子进程调用：文本模式、utf-8、容错解码"""
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


@guard
def main():
    def show(label, fn):
        try:
            print("  %-14s %s" % (label, fn()))
        except SystemExit as e:
            print("  %-14s ** %s" % (label, str(e).splitlines()[0]))
    print("外部工具定位：")
    show("kicad-cli", kicad_cli)
    show("chrome", chrome)
    show("KiCad share", kicad_share)
    sh = kicad_share()
    if sh:
        for sub in ("symbols", "footprints", "3dmodels"):
            p = os.path.join(sh, sub)
            mark = "有" if os.path.isdir(p) else "** 缺 **"
            print("  %-14s %s  %s" % (sub, mark, p))
    print("\n覆盖方式：KICAD_CLI=/path CHROME=/path  "
          "（设了无效路径会直接报错，不会静默回退）")


if __name__ == "__main__":
    sys.exit(main())