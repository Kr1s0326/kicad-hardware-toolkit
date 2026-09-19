#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render.py - 把 KiCad 的矢量输出渲染成 PNG，供人眼审阅。

为什么需要它
------------
kicad-cli 只能导出 SVG，不能直接出位图；而"看一眼"是唯一能抓住
布局意图类错误（Pin1 朝向、丝印压盘、文字重叠、引脚没对齐）的手段。
数值校验对这些完全无感。

两个来源，两个用途
------------------
  fp / sym / sch  -> SVG (kicad-cli)  -> PNG (headless chrome)   看 2D 绘图
  pcb render      -> PNG (kicad-cli 3D) 直接出                     看实物贴合

`pcb render` 会把 3D 模型（真实器件的 STEP）叠在焊盘上，
所以它是"几何是否对"的最強证据 —— 引脚有没有落在焊盘上，一眼可见。

CLI
---
    python render.py svg2png <in.svg> <out.png> [--width 900]
    python render.py fp  <lib.pretty> <outdir>
    python render.py sym <lib.kicad_sym> <outdir>
    python render.py sch <board.kicad_sch> <outdir>
    python render.py pcb <board.kicad_pcb> <out.png> [--side top|bottom|left|right|front|back]
                    [--rotate "rx,ry,rz"] [--zoom 2.0] [--size 1400x1000]
    python render.py which                      # 打印找到的 chrome / kicad-cli
"""

import argparse
import glob
import os
import re
import sys

# Windows 控制台常是 GBK；输出里若出现 GBK 以外的字符（↔ ✅ 之类）会直接抛
# UnicodeEncodeError 打断整个检查。这里保留原编码，只把无法映射的字符降级。
try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:                                       # noqa: BLE001
    pass

# ------------------------------------------------------------------ 工具定位
# 全部委托给 toolchain（唯一的定位实现）。这里保留同名薄封装，
# 是为了不破坏已有的 import 和 render.py which 子命令。
import toolchain as _tc
from cli import guard                             # noqa: E402

KICAD_CLI_CANDIDATES = _tc.KICAD_CLI_CANDIDATES      # 兼容旧引用
CHROME_CANDIDATES = _tc.CHROME_CANDIDATES


def kicad_cli():
    return _tc.kicad_cli()


def chrome():
    return _tc.chrome()


def run(cmd, **kw):
    return _tc.run(cmd, **kw)


# ------------------------------------------------------------------ SVG -> PNG
def svg_size(svg):
    """read width/height off the root <svg> (kicad-cli writes mm)"""
    head = open(svg, encoding="utf-8", errors="replace").read(4000)
    m = re.search(r'width="([\d.]+)mm"\s+height="([\d.]+)mm"', head)
    if m:
        return float(m.group(1)), float(m.group(2))
    return 100.0, 100.0


def svg2png(svg, png, width=900, pad=8):
    """rasterise with headless chrome.

    chrome refuses to write to a *relative* path from --screenshot, so the
    output is always resolved to an absolute one first.
    """
    svg = os.path.abspath(svg)
    png = os.path.abspath(png)
    w, h = svg_size(svg)
    ph = int(round(width * h / w)) + 2 * pad
    html = os.path.join(os.path.dirname(png),
                        "_wrap_%s.html" % re.sub(r"\W+", "_", os.path.basename(png)))
    with open(html, "w", encoding="utf-8") as f:
        f.write('<html><body style="margin:0;background:#fff">'
                '<img src="%s" style="width:%dpx;height:auto;display:block">'
                "</body></html>" % (svg.replace("\\", "/").replace(" ", "%20"),
                                    width))
    r = run([chrome(), "--headless", "--disable-gpu", "--no-sandbox",
             "--hide-scrollbars", "--default-background-color=FFFFFFFF",
             "--screenshot=" + png, "--window-size=%d,%d" % (width + 10, ph),
             "file:///" + html.replace("\\", "/").replace(" ", "%20")])
    os.remove(html)
    if not os.path.exists(png):
        raise SystemExit("svg2png failed: %s\n%s" % (svg, (r.stderr or "")[:400]))
    return png


def png_size(png):
    try:
        from PIL import Image
        with Image.open(png) as im:
            return im.size
    except Exception:                                   # noqa: BLE001
        return None


# ------------------------------------------------------------------ 各层渲染
def render_lib(kind, lib, outdir, layers=None):
    """kind = 'fp' (a .pretty dir) or 'sym' (a .kicad_sym file).
    Returns the list of SVGs kicad-cli produced (it makes one per footprint /
    per symbol unit)."""
    os.makedirs(outdir, exist_ok=True)
    cmd = [kicad_cli(), kind, "export", "svg", "--output", outdir]
    if layers:
        cmd += ["--layers", layers]
    cmd.append(lib)
    r = run(cmd)
    svgs = sorted(glob.glob(os.path.join(outdir, "*.svg")))
    if not svgs:
        raise SystemExit("export failed: %s\n%s" % (" ".join(cmd),
                                                    (r.stdout + r.stderr)[:500]))
    return svgs


def render_board_3d(pcb, png, side="top", rotate=None, zoom=2.0,
                    size=(1400, 1000), quality="high", perspective=False):
    # 注意 --perspective 是不带值的开关；写成 "--perspective false" 会被当成
    # 多出来的位置参数，kicad-cli 直接报 "Maximum number of positional arguments"
    cmd = [kicad_cli(), "pcb", "render", "-o", os.path.abspath(png),
           "--side", side, "--width", str(size[0]), "--height", str(size[1]),
           "--quality", quality, "--background", "opaque",
           "--zoom", str(zoom)]
    if rotate:
        cmd += ["--rotate", rotate]
    if perspective:
        cmd.append("--perspective")
    cmd.append(os.path.abspath(pcb))
    r = run(cmd)
    if not os.path.exists(png):
        raise SystemExit("3D render failed:\n%s" % (r.stdout + r.stderr)[:500])
    return png


def contact_sheet(pngs, out, cols=None, cell=520, title=None):
    """stitch several renders into one image so a single look covers them all"""
    from PIL import Image, ImageDraw
    imgs = [Image.open(p).convert("RGB") for p in pngs if os.path.exists(p)]
    if not imgs:
        return None
    cols = cols or min(3, len(imgs))
    rows = (len(imgs) + cols - 1) // cols
    scaled = []
    for im in imgs:
        w, h = im.size
        s = min(cell / w, cell / h)
        scaled.append(im.resize((max(1, int(w * s)), max(1, int(h * s)))))
    cw = max(i.size[0] for i in scaled) + 12
    ch = max(i.size[1] for i in scaled) + 12
    top = 28 if title else 0
    sheet = Image.new("RGB", (cols * cw, top + rows * ch), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    if title:
        d.text((6, 6), title, fill=(0, 0, 0))
    for i, im in enumerate(scaled):
        sheet.paste(im, ((i % cols) * cw + 6, top + (i // cols) * ch + 6))
    sheet.save(out)
    return out


# ------------------------------------------------------------------ CLI
@guard
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("svg2png")
    s.add_argument("svg"); s.add_argument("png"); s.add_argument("--width", type=int, default=900)

    for k in ("fp", "sym", "sch"):
        s = sub.add_parser(k)
        s.add_argument("lib"); s.add_argument("outdir")
        s.add_argument("--layers")

    s = sub.add_parser("pcb")
    s.add_argument("pcb"); s.add_argument("png")
    s.add_argument("--side", default="top")
    s.add_argument("--rotate")
    s.add_argument("--zoom", type=float, default=2.0)
    s.add_argument("--size", default="1400x1000")

    sub.add_parser("which")
    a = ap.parse_args()

    if a.cmd == "which":
        print("kicad-cli:", kicad_cli())
        try:
            print("chrome   :", chrome())
        except SystemExit as e:
            print("chrome   :", e)
        return

    if a.cmd == "svg2png":
        print(svg2png(a.svg, a.png, a.width), png_size(a.png))
    elif a.cmd in ("fp", "sym", "sch"):
        svgs = render_lib(a.cmd, a.lib, a.outdir, a.layers)
        for s_ in svgs:
            png = os.path.splitext(s_)[0] + ".png"
            svg2png(s_, png)
            print("  %s  ->  %s" % (os.path.basename(s_), os.path.basename(png)))
    elif a.cmd == "pcb":
        w, h = (int(v) for v in a.size.lower().split("x"))
        print(render_board_3d(a.pcb, a.png, a.side, a.rotate, a.zoom, (w, h)))


if __name__ == "__main__":
    sys.exit(main())