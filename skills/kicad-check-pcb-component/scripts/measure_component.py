#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
measure_component.py - 元器件尺寸检查主程序

    index | 要求:图片 | 要求:数值 | 实际测量:图片 | 实际测量:数值 | 结果

按封装家族分流：spec.json 里 `"family": "grid_array" | "peripheral" | "chip"`，
不写就自动判别。族里的专用测量/画图在 families/<name>.py，与封装无关的部分在
core/。详见 ../SKILL.md 与 ../references/families.md。

    python measure_component.py spec.json [--out report.xlsx] [--img-dir imgs] [--preview]
    python measure_component.py --list-families
"""

import argparse
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHARED = os.path.normpath(os.path.join(HERE, "..", "..", "..", "shared"))
if SHARED not in sys.path:
    sys.path.insert(0, SHARED)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from cli import guard                             # noqa: E402

from core import common                                    # noqa: E402
from core import report, spec as spec_mod                  # noqa: E402

FAMILIES = ("grid_array", "peripheral", "chip")


def load_family(name):
    return importlib.import_module("families.%s" % name)


def pick_family(ctx, wanted):
    if wanted:
        if wanted not in FAMILIES:
            raise SystemExit("unknown family %r (choose from %s)"
                             % (wanted, ", ".join(FAMILIES)))
        return load_family(wanted)
    for name in FAMILIES:                     # most specific first
        fam = load_family(name)
        if fam.detect(ctx):
            return fam
    raise SystemExit("could not auto-detect the package family - "
                     "set \"family\" in spec.json")


@guard
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", nargs="?")
    ap.add_argument("--out")
    ap.add_argument("--img-dir")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--family", help="force a family")
    ap.add_argument("--list-families", action="store_true")
    a = ap.parse_args()

    if a.list_families:
        for name in FAMILIES:
            fam = load_family(name)
            print("%-12s %s" % (name, fam.LABEL))
            print("             %s" % fam.DESCRIPTION)
            print("             专有测量项: %s" % ", ".join(sorted(fam.KINDS)))
        return
    if not a.spec:
        ap.error("spec.json is required")

    spec = spec_mod.load_spec(a.spec)
    root = spec["_root"]
    ctx = spec_mod.build_context(spec)
    ctx["_collinear_tol"] = spec.get("collinear_tol", 0.05)

    fam = pick_family(ctx, a.family or spec.get("family"))
    fam.prepare(ctx)
    print("component :", spec.get("component", ""))
    print("family    : %s  (%s)" % (fam.FAMILY, fam.LABEL))
    print("pads      : %d" % len(ctx["pads"]))

    rows = spec["rows"]
    tol = spec.get("tolerance", 0.005)
    img_dir = a.img_dir or os.path.join(root, spec.get("img_dir", "measure"))
    os.makedirs(img_dir, exist_ok=True)

    # ---- 要求:图片 —— 从图纸表格截图里按行裁切 ---------------------------
    req_imgs = [None] * len(rows)
    shot = spec.get("spec_table_image")
    if shot:
        if not os.path.isabs(shot):
            shot = os.path.join(root, shot)
        if os.path.exists(shot):
            try:
                from crop_spec_table import crop_rows
                names = [r["symbol"].replace("Ø", "Ob") for r in rows]
                got = crop_rows(shot, img_dir, spec.get("spec_table_skip", 2),
                                names, scale=(430, 34))
                for i in range(min(len(rows), len(got))):
                    req_imgs[i] = got[i]
            except Exception as e:                    # noqa: BLE001
                print("warning: cannot crop the spec table:", e)
        else:
            print("warning: spec_table_image not found:", shot)
    for i, r in enumerate(rows):
        if r.get("req_image"):
            f = r["req_image"] if os.path.isabs(r["req_image"]) else \
                os.path.join(root, r["req_image"])
            if not os.path.exists(f):
                # 静默跳过会让「要求:图片」列一片空白，却没有任何提示
                print("warning: %s 的 req_image 找不到: %s" % (r["symbol"], f))
            else:
                # 归一化到 img_dir/req_NN_*.png：report.preview_png 就是按这个
                # 命名去贴图的，逐行 req_image 不归一化就不出现在预览里。
                ext = os.path.splitext(f)[1] or ".png"
                norm = os.path.join(img_dir, "req_%02d_%s%s"
                                    % (i, r["symbol"].replace("Ø", "Ob")
                                       .replace("/", "_"), ext))
                try:
                    if os.path.abspath(f) != os.path.abspath(norm):
                        import shutil
                        shutil.copyfile(f, norm)
                except Exception as e:                        # noqa: BLE001
                    print("warning: 要求图片无法归一到 %s: %s" % (norm, e))
                req_imgs[i] = norm

    # ---- 测量 + 画图 + 判定 ----------------------------------------------
    values, results, meas_imgs = [], [], []
    for i, r in enumerate(rows):
        kind = r["kind"]
        try:
            value, extra = fam.measure(kind, ctx, r)
        except Exception as e:                        # noqa: BLE001
            print("  !! %s (%s) failed: %s" % (r["symbol"], kind, e))
            value, extra = None, {}
        mf = os.path.join(img_dir, "meas_%02d_%s.png"
                          % (i, r["symbol"].replace("Ø", "Ob").replace("/", "_")))
        try:
            fam.panel(kind, ctx, r, value, extra, r["symbol"]).save(mf)
        except Exception as e:                        # noqa: BLE001
            print("  !! %s picture failed: %s" % (r["symbol"], e))
            common.panel(kind, ctx, r, value, extra, r["symbol"]).save(mf)
        meas_imgs.append(mf)

        res = spec_mod.verdict(kind, r, value, tol, fam.COUNT_KINDS)
        results.append(res)
        if kind == "na" or value is None:
            txt = r.get("na_text", "无法测量（2D 设计文件不含该方向几何）")
        elif kind in fam.COUNT_KINDS:
            txt = "实测 %d" % round(value)
        else:
            txt = ("Ø%.3f mm" % value if kind in ("dia", "hole_dia")
                   else "%.4f mm" % value)
            if r.get("nominal") is not None:
                d = value - r["nominal"]
                if abs(d) < 5e-5:
                    d = 0.0
                txt += "  偏差 %+.4f" % d
        if r.get("note"):
            txt += "\n" + r["note"]
        values.append(txt)

    print("--- measured ---")
    for r, v, res in zip(rows, values, results):
        print("  %-8s %-22s %-14s %s"
              % (r["symbol"], r.get("requirement", ""),
                 v.split("\n")[0][:14], res))

    out = a.out or os.path.join(root, spec.get("output", "dimension_report.xlsx"))
    out = report.build_xlsx(spec, rows, values, results, req_imgs, meas_imgs, out)
    print("\nreport:", out)
    print("images:", img_dir)
    if a.preview:
        png = report.preview_png(out, img_dir=img_dir)
        print("preview:", png)
    # 退出码必须反映判定结果：有 NG 行就非 0。
    # 以前这里不返回，于是 check_footprint 拿到 rc=0，NG 就传不出去 ——
    # 8 行 NG 的封装照样"通过"。
    n_ng = sum(1 for x in results if x == "NG")
    return 1 if n_ng else 0


if __name__ == "__main__":
    sys.exit(main())