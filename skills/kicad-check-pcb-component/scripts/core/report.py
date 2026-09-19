#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""core.report - turn measured rows into the Excel inspection report.

    index | 要求:图片 | 要求:数值 | 实际测量:图片 | 实际测量:数值 | 结果

Plus a second sheet documenting what the measurement pictures show.  Package
agnostic - it only needs rows, pictures and verdicts.
"""

import os

__all__ = ["build_xlsx", "preview_png"]

HEAD = ["index", "要求:图片", "要求:数值", "实际测量:图片", "实际测量:数值", "结果"]
COLW = [54, 439, 215, 481, 369, 105]
ROW_H, HEAD_PX = 333, 34
RES_FILL = {"PASS": ("C6EFCE", "006100"), "NG": ("FFC7CE", "9C0006")}
# NB: openpyxl colours carry no "#"; preview_png adds it for PIL


def build_xlsx(spec, rows, values, results, req_imgs, meas_imgs, out):
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    wb = Workbook()
    ws = wb.active
    ws.title = spec.get("sheet_name", "尺寸测量")
    thin = Side(style="thin", color="999999")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    heads = spec.get("columns", HEAD)
    ws.append(heads)
    for c in range(1, len(heads) + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = Font(bold=True, size=12, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="305496")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border
    ws.row_dimensions[1].height = 24

    for i, row in enumerate(rows):
        r = i + 2
        ws.cell(row=r, column=1, value=i + 1).alignment = Alignment(
            horizontal="center", vertical="center")
        ws.cell(row=r, column=3, value=row.get("requirement", "")).alignment = \
            Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.cell(row=r, column=5, value=values[i]).alignment = Alignment(
            horizontal="left", vertical="center", wrap_text=True)
        res = results[i]
        rc = ws.cell(row=r, column=6, value=res)
        bg, fg = RES_FILL.get(res, ("EDEDED", "808080"))
        rc.font = Font(bold=True, size=16, color=fg)
        rc.fill = PatternFill("solid", fgColor=bg)
        rc.alignment = Alignment(horizontal="center", vertical="center")
        for c in range(1, 7):
            ws.cell(row=r, column=c).border = border
        ws.row_dimensions[r].height = spec.get("row_height", 250)
        if req_imgs[i] and os.path.exists(req_imgs[i]):
            ws.add_image(XLImage(req_imgs[i]), "B%d" % r)
        ws.add_image(XLImage(meas_imgs[i]), "D%d" % r)

    for col, w in zip("ABCDEF", COLW[1:] and (7, 62, 30, 68, 52, 12)):
        ws.column_dimensions[col].width = w

    n = len(rows) + 3
    for k, t in enumerate(spec.get("notes", [])):
        c = ws.cell(row=n + k, column=1, value=t)
        c.font = Font(size=9, italic=True, color="555555")
        c.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"

    leg = spec.get("legend")
    if leg:
        ws2 = wb.create_sheet(leg.get("sheet", "图例说明"))
        rr = 1
        ws2.cell(row=rr, column=1, value=leg.get("head", "图例说明")).font = \
            Font(bold=True, size=14, color="305496")
        rr += 1
        if leg.get("intro"):
            ws2.cell(row=rr, column=1, value=leg["intro"]).alignment = \
                Alignment(vertical="top", wrap_text=True)
            ws2.merge_cells(start_row=rr, start_column=1, end_row=rr + 1,
                            end_column=5)
            rr += 3
        for group in leg.get("tables", []):
            ws2.cell(row=rr, column=1, value=group.get("title", "")).font = \
                Font(bold=True, size=11)
            rr += 1
            for j, vals in enumerate(group["rows"]):
                for k2, t in enumerate(vals, start=1):
                    cell = ws2.cell(row=rr + j, column=k2, value=t)
                    cell.alignment = Alignment(vertical="top", wrap_text=True)
                    if j == 0:
                        cell.font = Font(bold=True, size=11)
            rr += len(group["rows"]) + 1
        for col, w in zip("ABCDE", (22, 46, 28, 24, 24)):
            ws2.column_dimensions[col].width = w

    try:
        wb.save(out)
    except PermissionError:
        base, ext = os.path.splitext(out)
        out = base + "(new)" + ext
        wb.save(out)
        print("note: the target file is open in Excel/WPS, saved as", out)
    return out


def preview_png(xlsx, out=None, img_dir=None):
    """render a 1:1 PNG mock-up so the layout can be checked without Excel"""
    import re

    from PIL import Image, ImageDraw
    from openpyxl import load_workbook

    from .draw import font

    F, FB = font(15), font(16)
    ws = load_workbook(xlsx).active
    img_dir = img_dir or os.path.join(os.path.dirname(os.path.abspath(xlsx)),
                                      "measure")
    n = 0
    for r in range(2, ws.max_row + 1):
        if ws.cell(row=r, column=3).value:
            n = r - 1
    if not n:
        return None
    W, H = sum(COLW), HEAD_PX + ROW_H * n
    img = Image.new("RGB", (W, H + 1), "white")
    d = ImageDraw.Draw(img)
    x = 0
    for w in COLW[:-1]:
        x += w
        d.line([(x, 0), (x, H)], fill="#bbbbbb")
    d.rectangle([0, 0, W - 1, HEAD_PX - 1], fill="#305496")
    for cy in range(n + 1):
        d.line([(0, HEAD_PX + cy * ROW_H), (W, HEAD_PX + cy * ROW_H)],
               fill="#bbbbbb")
    x = 4
    for i in range(6):
        d.text((x, 8), str(ws.cell(row=1, column=i + 1).value or ""),
               fill="white", font=FB)
        x += COLW[i]
    for r in range(2, 2 + n):
        y = HEAD_PX + (r - 2) * ROW_H
        v = [ws.cell(row=r, column=c).value for c in range(1, 7)]
        d.text((10, y + 10), str(v[0]), fill="black", font=F)
        d.text((COLW[0] + 12, y + 10), str(v[2] or ""), fill="black", font=F)
        for k, t in enumerate(str(v[4] or "").split("\n")):
            d.text((sum(COLW[:4]) + 8, y + 10 + k * 22), t, fill="black", font=F)
        xr = sum(COLW[:5])
        bg, fg = RES_FILL.get(v[5], ("EDEDED", "808080"))     # xlsx: no "#"
        d.rectangle([xr + 6, y + ROW_H / 2 - 22, xr + COLW[5] - 6,
                     y + ROW_H / 2 + 22], fill="#" + bg)
        tw = d.textlength(str(v[5] or ""), font=FB)
        d.text((xr + (COLW[5] - tw) / 2, y + ROW_H / 2 - 12), str(v[5] or ""),
               fill="#" + fg, font=FB)
    for fn in sorted(os.listdir(img_dir)):
        m = re.match(r"(req|meas)_(\d+)_", fn)
        if not m or int(m.group(2)) >= n:
            continue
        i = int(m.group(2))
        pos = ((COLW[0] + 4, HEAD_PX + i * ROW_H + 55) if m.group(1) == "req"
               else (COLW[0] + COLW[1] + COLW[2] + 4, HEAD_PX + i * ROW_H + 2))
        img.paste(Image.open(os.path.join(img_dir, fn)), pos)
    out = out or os.path.splitext(xlsx)[0] + "_preview.png"
    img.save(out)
    return out
