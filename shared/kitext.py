#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kitext.py - KiCad 符号里「文字占多大地方」的几个常数。

为什么单独一个模块
------------------
生成侧（make_part.py 的 layout_symbol）要用它算出本体得多宽，四个角的
文字才不会叠在一起；校验侧（symbol_lint.py 的 overflow）要用它反推
「这两截文字会不会撞上」。**两边必须用同一组数** —— 否则生成侧按一套
值留了空间，校验侧按另一套值判断，检查会静默失效：明明压着字，却报
"无疑问项"。（CY8C6245 上就中过：lint 漏算了 NAME_OFF，
估出的名字比实际短 0.67mm，刚好躲过判定，而渲染图上 XRES 被 VDDD
盖掉了整整一个字符。）

这些数是怎么来的
----------------
不是估的，是从 kicad-cli 导出的 SVG/PNG 上量的：

    CHAR_W_RATIO  一个字符的宽度 / 字号      量得 4 字符 = 4.14mm @ 字号 1.27
    NAME_OFF      引脚名离本体边缘的偏移      量得 0.85mm（KiCad 的
                  （KiCad 把它画在本体内侧，  pin name offset 加上描边）
                  不是贴边）                -> 实测 0.85mm
    VERT_HALF     竖排文字的半宽（上/下边    字号 1.27 + 描边 0.15 再留一点
                  引脚的引脚名是竖着写的）    -> 0.78mm

字号变了这些值会同比例变：CHAR_W_RATIO 和 VERT_HALF 乘字号，
NAME_OFF 是绝对偏移（KiCad 的默认 pin name offset 不随字号缩放）。
"""

__all__ = ["CHAR_W_RATIO", "NAME_OFF", "VERT_HALF", "text_len", "vert_half"]

CHAR_W_RATIO = 0.815        # 字符宽 / 字号
NAME_OFF = 0.85             # mm，引脚名离本体边缘
VERT_HALF = 0.78            # mm @ 字号 1.27，竖排文字半宽


def text_len(name, font):
    """一串引脚名占多宽（mm）。"""
    return len(name or "") * font * CHAR_W_RATIO


def vert_half(font):
    """竖排文字的半宽（mm）。按字号缩放。"""
    return VERT_HALF * (font / 1.27)
