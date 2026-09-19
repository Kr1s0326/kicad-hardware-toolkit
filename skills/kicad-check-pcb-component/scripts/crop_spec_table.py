#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
crop_spec_table.py - cut the individual rows out of a screenshot of a
dimension table (the "要求:图片" column of the inspection report).

    python crop_spec_table.py table.png out_dir --skip 2 --names A,A1,D,E
    python crop_spec_table.py table.png out_dir --probe      # only report the bands

Grid detection: pixel rows that are dark over >60% of the table width are taken
as horizontal rules; the bands between them are the table rows.  `--skip` drops
the header bands (a classic JEP95/JEDEC table has 2: SYMBOL|DIMENSIONS and
MIN|NOM|MAX).
"""

import argparse
import os
import sys

import numpy as np
from PIL import Image


def detect_bands(path, dark=140, coverage=0.6, gap=2):
    """-> (list of (y_top, y_bottom) bands, (x_left, x_right) table extent)"""
    a = np.array(Image.open(path).convert("L"))
    d = a < dark
    rows = np.where(d.sum(axis=1) > a.shape[1] * coverage)[0]
    groups = []
    for y in rows:
        if groups and y - groups[-1][-1] <= gap:
            groups[-1].append(y)
        else:
            groups.append([y])
    ys = [int(np.mean(g)) for g in groups]
    cols = np.where(d.sum(axis=0) > a.shape[0] * 0.5)[0]
    if len(cols) < 2:
        cols = np.where(d.sum(axis=0) > 0)[0]
    bands = list(zip(ys[:-1], ys[1:]))
    return bands, (int(cols.min()), int(cols.max()))


def crop_rows(path, out_dir, skip=2, names=None, pad=2, scale=None):
    bands, (xl, xr) = detect_bands(path)
    im = Image.open(path).convert("RGB")
    os.makedirs(out_dir, exist_ok=True)
    out = []
    for i, (t, b) in enumerate(bands[skip:]):
        name = names[i] if names and i < len(names) else "%02d" % i
        c = im.crop((xl, t + pad, xr, b - pad))
        if scale:
            c = c.resize((scale[0], scale[1]), Image.LANCZOS)
        f = os.path.join(out_dir, "req_%02d_%s.png" % (i, name))
        c.save(f)
        out.append(f)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("out_dir")
    ap.add_argument("--skip", type=int, default=2, help="header bands to drop")
    ap.add_argument("--names", default="", help="comma separated row names")
    ap.add_argument("--size", default="430x34", help="output size WxH")
    ap.add_argument("--probe", action="store_true", help="only print the bands")
    a = ap.parse_args()

    bands, ext = detect_bands(a.image)
    print("table extent x: %d..%d, %d bands" % (ext[0], ext[1], len(bands)))
    if a.probe:
        for i, (t, b) in enumerate(bands):
            print("  band %2d: y %4d..%4d  (h=%d)" % (i, t, b, b - t))
        return
    w, h = (int(v) for v in a.size.lower().split("x"))
    names = [n for n in a.names.split(",") if n] or None
    files = crop_rows(a.image, a.out_dir, a.skip, names, scale=(w, h))
    for f in files:
        print("wrote", f)
    if len(files) < 2:
        sys.exit("warning: very few rows detected - check --skip / the image")


if __name__ == "__main__":
    main()
