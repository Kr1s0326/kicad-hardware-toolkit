#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""preview.py - render a 1:1 PNG mock-up of a generated report (no Excel needed).

    python preview.py report.xlsx [preview.png]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from core.report import preview_png      # noqa: E402

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    print(preview_png(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None))
