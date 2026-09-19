#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
selftest.py - 符号校验的黄金测试。不需要 CAD、不需要联网、秒级。

    python selftest.py            # 全跑
    python selftest.py pitch      # 只跑名字里含 "pitch" 的

为什么需要这个文件
------------------
`symbol_lint.py` 是 350+ 行规则逻辑（间距 / 分组 / 栅格 / 本体 / 名字溢出），
在补上这个文件之前它**一条自动化测试都没有** —— 只有开发时在聊天里手跑的
几个负向用例。改坏它不会有人知道。

测试用的是**合成符号**（当场拼出 .kicad_sym 文本），不是真实库文件，
所以谁都能跑，CI 里跑也不挑环境。

每个用例都明确写出"期望抓到什么"：
    期望 FAIL 的用例如果**没**报错，等于规则失效 → 记为失败。
    （只测"正确的能通过"是不够的 —— 一个永远返回 PASS 的 linter 也能过。）
"""

import os
import re
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

SHARED = os.path.normpath(os.path.join(HERE, "..", "..", "..", "shared"))
if SHARED not in sys.path:
    sys.path.insert(0, SHARED)

import pin_report                                             # noqa: E402
import symbol_lint                                            # noqa: E402
from pinmap import pins_of                                    # noqa: E402

RESULTS = []


# ---------------------------------------------------------------------------
# 合成符号：直接拼 .kicad_sym 文本
# ---------------------------------------------------------------------------
def make_symbol(pins, body=None, name="SYNTH"):
    """pins: [(number, name, etype, x, y, rot, length)]

    body: (x0, y0, x1, y1)，None 则自动算一个能包住所有引脚根部的
    """
    if body is None:
        xs, ys = [], []
        for _, _, _, x, y, rot, ln in pins:
            rx = x + ln if rot == 0 else (x - ln if rot == 180 else x)
            ry = y + ln if rot == 90 else (y - ln if rot == 270 else y)
            xs.append(rx); ys.append(ry)
        body = (min(xs) - 2.54, min(ys) - 2.54, max(xs) + 2.54, max(ys) + 2.54)
    x0, y0, x1, y1 = body

    p = []
    for num, nm, et, x, y, rot, ln in pins:
        p.append('\t\t\t(pin %s line\n\t\t\t\t(at %s %s %d)\n\t\t\t\t(length %s)\n'
                 '\t\t\t\t(name "%s"\n\t\t\t\t\t(effects\n\t\t\t\t\t\t(font\n'
                 '\t\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t\t)\n\t\t\t\t\t)\n\t\t\t\t)\n'
                 '\t\t\t\t(number "%s"\n\t\t\t\t\t(effects\n\t\t\t\t\t\t(font\n'
                 '\t\t\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t\t\t)\n\t\t\t\t\t)\n\t\t\t\t)\n'
                 '\t\t\t)\n' % (et, x, y, rot, ln, nm, num))
    return ('(kicad_symbol_lib\n\t(version 20251024)\n\t(generator "t")\n'
            '\t(symbol "%s"\n\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n'
            '\t\t(on_board yes)\n\t\t(property "Reference" "U"\n'
            '\t\t\t(at 0 0 0)\n\t\t\t(effects\n\t\t\t\t(font\n'
            '\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n'
            '\t\t(property "Value" "%s"\n\t\t\t(at 0 0 0)\n\t\t\t(effects\n'
            '\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n'
            '\t\t(symbol "%s_0_1"\n\t\t\t(rectangle\n\t\t\t\t(start %s %s)\n'
            '\t\t\t\t(end %s %s)\n\t\t\t\t(stroke\n\t\t\t\t\t(width 0.254)\n'
            '\t\t\t\t\t(type default)\n\t\t\t\t)\n\t\t\t\t(fill\n'
            '\t\t\t\t\t(type background)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n'
            '\t\t(symbol "%s_1_1"\n%s\t\t)\n\t\t(embedded_fonts no)\n\t)\n)\n'
            % (name, name, name, x0, y1, x1, y0, name, "".join(p)))


def side_pins(prefix, n, x, rot, y0, pitch, group_of=None, etype="input"):
    """一边的 n 个引脚，从 y0 开始往下排"""
    out = []
    for i in range(n):
        out.append(("%s%d" % (prefix, i + 1), "%s%d" % (prefix, i + 1), etype,
                    x, y0 - i * pitch, rot, 2.54))
    return out


# ---------------------------------------------------------------------------
# 测试框架
# ---------------------------------------------------------------------------
def check(group, name, cond, detail=""):
    RESULTS.append((group, name, bool(cond), detail))


def expect(result, group, name, kind, want=True):
    """result 是 symbol_lint.lint() 的返回值；检查 issues 里有没有某个 kind"""
    kinds = [k for k, _ in result["issues"]]
    got = kind in kinds
    check(group, name, got == want,
          "" if got == want else
          ("期望 %s %s，实际 issues=%s" % ("出现" if want else "不出现", kind, kinds)))


def run(path, groups=None, **kw):
    return symbol_lint.lint(path, min_pitch_mil=kw.pop("min_pitch", 200.0),
                            group_gap_mil=kw.pop("group_gap", 400.0),
                            grid_mil=kw.pop("grid", 50.0),
                            declared=groups)


# ---------------------------------------------------------------------------
# 用例：间距 / 分组
# ---------------------------------------------------------------------------
def test_pitch_and_groups(tmp):
    """左 VBUS | IN+ IN−；右 A B C D | E —— 组内 200mil、组间 400mil"""
    # 用真实 INA239 的几何做正例
    pins = [
        ("8", "VBUS", "input", -10.16, 12.7, 0, 2.54),
        ("10", "IN+", "input", -10.16, 2.54, 0, 2.54),
        ("9", "IN-", "input", -10.16, -2.54, 0, 2.54),
        ("2", "MOSI", "input", 10.16, 12.7, 180, 2.54),
        ("4", "MISO", "output", 10.16, 7.62, 180, 2.54),
        ("5", "SCLK", "input", 10.16, 2.54, 180, 2.54),
        ("1", "CS", "input", 10.16, -2.54, 180, 2.54),
        ("3", "ALERT", "open_collector", 10.16, -12.7, 180, 2.54),
        ("6", "VS", "power_in", 0.0, 17.78, 270, 2.54),
        ("7", "GND", "power_in", 0.0, -17.78, 90, 2.54),
    ]
    G = {"left": [["VBUS"], ["IN+", "IN-"]],
         "right": [["MOSI", "MISO", "SCLK", "CS"], ["ALERT"]]}
    f = os.path.join(tmp, "good.kicad_sym")
    open(f, "w", encoding="utf-8").write(make_symbol(pins))
    r = run(f, G)
    check("pitch", "正例: 无任何 FAIL", not r["issues"], str(r["issues"]))
    check("pitch", "正例: 基础间距 = 200 mil",
          abs(r["sides"]["right"]["base_pitch_mil"] - 200) < 0.01,
          str(r["sides"]["right"]["base_pitch_mil"]))
    check("pitch", "正例: 右侧推断分组 = 声明分组",
          r["sides"]["right"]["groups"] == [["MOSI", "MISO", "SCLK", "CS"],
                                            ["ALERT"]],
          str(r["sides"]["right"]["groups"]))
    check("pitch", "正例: 左侧推断分组 = 声明分组",
          r["sides"]["left"]["groups"] == [["VBUS"], ["IN+", "IN-"]],
          str(r["sides"]["left"]["groups"]))

    # 负例 1：组内间距压到 100 mil
    bad = [list(p) for p in pins]
    for p in bad:
        if p[1] == "MISO":
            p[4] = 10.16          # 12.7 - 2.54 => 100 mil
    f2 = os.path.join(tmp, "pitch.kicad_sym")
    open(f2, "w", encoding="utf-8").write(make_symbol([tuple(p) for p in bad]))
    expect(run(f2, G), "pitch", "负例: 组内 100 mil -> 报 pitch", "pitch")

    # 负例 2：组间空隙压到 200 mil（这是**几何上不可判**的那个坑）
    bad = [list(p) for p in pins]
    for p in bad:
        if p[1] == "IN+":
            p[4] = 7.62           # VBUS 12.7 -> IN+ 7.62 => 组间只剩 200 mil
    f3 = os.path.join(tmp, "gap.kicad_sym")
    open(f3, "w", encoding="utf-8").write(make_symbol([tuple(p) for p in bad]))
    r3 = run(f3, G)
    expect(r3, "pitch", "负例: 组间 200 mil -> 报 group_gap", "group_gap")
    expect(r3, "pitch", "负例: 几何推断分组 != 声明分组 -> 报 groups",
           "groups")

    # 负例 3：**不给**声明分组时，工具必须说这条规则无法真正校验
    r4 = run(f3, None)
    check("pitch", "不给分组时不误报 group_gap",
          "group_gap" not in [k for k, _ in r4["issues"]],
          str([k for k, _ in r4["issues"]]))
    check("pitch", "不给分组时标记 inferred_only",
          all(g.get("inferred_only") for g in r4["sides"].values()))


# ---------------------------------------------------------------------------
def test_groups_shape(tmp):
    """形状不对的 groups.json 必须被当成"没给分组"并明确提示。

    以前会被**静默忽略**：用户以为分组查过了，其实「组间 400 mil」根本没校验。
    """
    # lint() 层面：无法识别的边 -> declared.get(side) 是 None -> 与没给等价
    pins = [("1", "A", "input", -7.62, 2.54, 0, 2.54),
            ("2", "B", "input", -7.62, -2.54, 0, 2.54),
            ("3", "C", "output", 7.62, 0.0, 180, 2.54)]
    f = os.path.join(tmp, "shape.kicad_sym")
    open(f, "w", encoding="utf-8").write(make_symbol(pins))
    r = run(f, {"nonsense": [["A"]]})
    check("groups_shape", "形状不对时不误报 group_gap",
          "group_gap" not in [k for k, _ in r["issues"]],
          str([k for k, _ in r["issues"]]))
    check("groups_shape", "形状不对时标记 inferred_only（上层据此提示）",
          all(g.get("inferred_only") for g in r["sides"].values()),
          str(r["sides"]))


def test_grid_duplicate_body(tmp):
    base = [("1", "A", "input", -7.62, 2.54, 0, 2.54),
            ("2", "B", "input", -7.62, -2.54, 0, 2.54),
            ("3", "C", "output", 7.62, 0.0, 180, 2.54)]

    f = os.path.join(tmp, "base.kicad_sym")
    open(f, "w", encoding="utf-8").write(make_symbol(base))
    check("grid", "正例: 无疑问项", not run(f)["issues"])

    # 重复引脚号
    dup = [list(p) for p in base]
    dup[1][0] = "1"
    f2 = os.path.join(tmp, "dup.kicad_sym")
    open(f2, "w", encoding="utf-8").write(make_symbol([tuple(p) for p in dup]))
    expect(run(f2), "grid", "负例: 引脚号重复 -> duplicate", "duplicate")

    # 引脚坐标不在 50 mil 栅格上
    off = [list(p) for p in base]
    off[0][4] = 2.4
    f3 = os.path.join(tmp, "off.kicad_sym")
    open(f3, "w", encoding="utf-8").write(make_symbol([tuple(p) for p in off]))
    r3 = run(f3)
    expect(r3, "grid", "负例: 坐标 2.4mm -> off_grid", "off_grid")
    check("grid", "off_grid 指出了具体引脚",
          any("1" in msg for k, msg in r3["issues"] if k == "off_grid"),
          str(r3["issues"]))

    # 引脚太短，根部接不到本体。
    # 注意：本体必须**显式固定**下，否则自动算出的本体会跟着缩，永远碰不上。
    short = [list(p) for p in base]
    short[0][6] = 0.5
    f4 = os.path.join(tmp, "short.kicad_sym")
    open(f4, "w", encoding="utf-8").write(
        make_symbol([tuple(p) for p in short],
                    body=(-6.0, -5.0, 6.0, 5.0)))
    r4 = run(f4)
    expect(r4, "grid", "负例: 引脚 0.5mm 接不到本体 -> body_margin",
           "body_margin")
    check("grid", "body_margin 指出了具体引脚",
          any("1" in msg for k, msg in r4["issues"] if k == "body_margin"),
          str(r4["issues"]))

    # 引脚长度不一致
    f5 = os.path.join(tmp, "len.kicad_sym")
    vary = [list(p) for p in base]
    vary[0][6] = 3.81
    # 本体要跟着变，否则会先报 body_margin
    open(f5, "w", encoding="utf-8").write(
        make_symbol([tuple(p) for p in vary],
                    body=(-12.7, -7.62, 7.62, 7.62)))
    r5 = run(f5)
    check("grid", "引脚长度不一致 -> pin_length 警告",
          "pin_length" in [k for k, _ in r5["warnings"]],
          str([k for k, _ in r5["warnings"]]))


# ---------------------------------------------------------------------------
def test_pins_of(tmp):
    """pinmap.pins_of 必须能穿过子块结构拿到引脚（父块里没有引脚）"""
    pins = [("1", "~{CS}", "input", 10.16, 5.08, 180, 2.54),
            ("2", "MOSI", "input", 10.16, 0.0, 180, 2.54),
            ("3", "ALERT", "open_collector", 10.16, -5.08, 180, 2.54)]
    f = os.path.join(tmp, "pins.kicad_sym")
    open(f, "w", encoding="utf-8").write(make_symbol(pins))
    name, got = pins_of(f)
    check("pins_of", "解析到 3 个引脚", len(got) == 3, str(sorted(got)))
    check("pins_of", "引脚号正确", set(got) == {"1", "2", "3"}, str(sorted(got)))
    check("pins_of", "~{CS} 的波浪号被剥掉", got.get("1", ("",))[0] == "CS",
          str(got.get("1")))
    check("pins_of", "符号名正确", name == "SYNTH", name)


# ---------------------------------------------------------------------------
def test_hidden_and_stacked(tmp):
    """两个真实世界的坑，都会让工具**静默给出错的结果**。

    ① 带 (hide yes) 的引脚：重复电源脚的常规画法。长正则按顺序串
       at/length/name/number 会整条失配，静默丢引脚。
       实测 KiCad 官方 ESP32-S3 因此从 57 少成 55 —— 官方符号配官方封装
       竟然报「封装有、符号没有的焊盘: 3, 56」。
    ② 堆叠引脚：同侧同坐标。间隙为 0 会让 base=0，随后 g/base 除零崩溃。
    """
    pins = [("1", "A", "input", -7.62, 2.54, 0, 2.54),
            ("2", "VDD", "power_in", -7.62, 0.0, 0, 2.54),
            ("3", "VDD", "passive", -7.62, 0.0, 0, 2.54),   # 与 2 同位
            ("4", "B", "output", 7.62, 0.0, 180, 2.54)]
    txt = make_symbol(pins)
    # 给 pin 3 插一个 (hide yes)，位置放在 length 之后、name 之前 ——
    # 正是会打爆“把 at/length/name/number 按顺序串起来”那种正则的地方。
    # 用正则插入而不写死字面量（免得依赖 make_symbol 的坐标格式）。
    txt2, n = re.subn(r'(\(pin passive line\n(?:\s*\([^\n]*\n)*?\s*\(length [\d.]+\)\n)',
                      r'\1\t\t\t\t(hide yes)\n', txt, count=1)
    assert n == 1, "测试自身出错：没能插入 (hide yes)"
    txt = txt2
    f = os.path.join(tmp, "stacked.kicad_sym")
    open(f, "w", encoding="utf-8").write(txt)

    _n, got = pins_of(f)
    check("hidden/stacked", "① 带 (hide yes) 的引脚不被丢掉（4 个）",
          set(got) == {"1", "2", "3", "4"}, str(sorted(got)))

    r = run(f)                       # ② 不能崩
    check("hidden/stacked", "② 堆叠引脚不导致崩溃", True)
    check("hidden/stacked", "② 堆叠被识别并告警",
          "stacked" in [k for k, _ in r["warnings"]],
          str([k for k, _ in r["warnings"]]))
    check("hidden/stacked", "② 基础间距仍能量出（2.54mm = 100 mil）",
          abs(r["sides"]["left"]["base_pitch_mil"] - 100.0) < 0.1,
          str(r["sides"]["left"].get("base_pitch_mil")))


def test_body_margin_both_axes(tmp):
    """body_margin 必须查两个轴。

    踩过的坑：以前只查引脚"贴着的那一个轴"，于是本体宽 15.24mm 却排着
    ±33mm 的顶边引脚（悬空 17.8mm）竟然全部 PASS。真实案例是 56 脚 QFN。
    """
    body = (-7.62, -10.16, 7.62, 10.16)
    # 顶边引脚贴在 y=10.16 上（y 轴没问题），但 x=25.4 远在本体之外
    pins = [("1", "A", "input", -7.62, 0.0, 0, 2.54),
            ("2", "B", "output", 7.62, 0.0, 180, 2.54),
            ("3", "C", "input", 25.4, 12.7, 270, 2.54)]
    f = os.path.join(tmp, "wide_top.kicad_sym")
    open(f, "w", encoding="utf-8").write(make_symbol(pins, body=body))
    r = run(f)
    msgs = [m for k, m in r["issues"] if k == "body_margin"]
    check("body_margin", "顶边引脚 x 超出本体 -> 报出",
          any("左右范围" in m for m in msgs), str(msgs))
    check("body_margin", "报的是引脚 3", any("引脚 3" in m for m in msgs), str(msgs))


def test_pin_report_verdict(tmp):
    """引脚比对表的判定：只有"两边都有 + 名字一致 + 类型一致"才算 PASS。

    这里的每一行都对应一种真实错误：
      类型错  —— 画出来一模一样，只有 ERC 能发现
      名字错  —— 几何全对，只有名字比对能发现
      缺引脚  —— 手册有、符号没有；或反过来
    """
    v = pin_report.verdict
    ok = {"pin": "1", "pdf_name": "MOSI", "netlist_name": "MOSI",
          "pdf_etype": "input", "erc_etype": "input",
          "name_ok": True, "type_ok": True}
    check("pin_report", "全对 -> PASS", v(ok) == "PASS", v(ok))

    bad_type = dict(ok, erc_etype="output", type_ok=False)
    check("pin_report", "电气类型不符 -> NG", v(bad_type) == "NG", v(bad_type))

    bad_name = dict(ok, netlist_name="MISO", name_ok=False)
    check("pin_report", "名字不符 -> NG", v(bad_name) == "NG", v(bad_name))

    only_pdf = dict(ok, netlist_name="-", name_ok=False)
    check("pin_report", "手册有、网表没有 -> NG", v(only_pdf) == "NG", v(only_pdf))

    only_net = dict(ok, pdf_name="-", name_ok=False)
    check("pin_report", "网表有、手册没有 -> NG", v(only_net) == "NG", v(only_net))

    # 表格形状必须和需求一致（列名是给用户看的接口）
    check("pin_report", "表头 = pin|手册名|网表名|手册类型|ERC类型|结果",
          pin_report.HEAD == ["pin", "手册名", "网表名", "手册类型", "ERC类型", "结果"],
          str(pin_report.HEAD))


def test_multi_unit(tmp):
    """带 INA239_0_1 / INA239_1_1 子块 + 另一个符号，不能把后面的符号算进来"""
    pins = [("1", "A", "input", -7.62, 0.0, 0, 2.54)]
    body = make_symbol(pins, name="UNIT_A")
    other = make_symbol([("9", "ZZ", "input", -7.62, 0.0, 0, 2.54)], name="UNIT_B")
    # 两个符号放进同一个库
    lib = body.replace("(embedded_fonts no)\n\t)\n)\n", "(embedded_fonts no)\n\t)\n")
    lib += other.replace("(kicad_symbol_lib\n\t(version 20251024)\n\t(generator \"t\")\n", "")
    f = os.path.join(tmp, "multi.kicad_sym")
    open(f, "w", encoding="utf-8").write(lib)
    name, got = pins_of(f, "UNIT_A")
    check("multi_unit", "只取 UNIT_A 的引脚", set(got) == {"1"}, str(sorted(got)))
    n2, got2 = pins_of(f, "UNIT_B")
    check("multi_unit", "UNIT_B 也能单独取", set(got2) == {"9"}, str(sorted(got2)))


# ---------------------------------------------------------------------------
def main():
    pat = sys.argv[1] if len(sys.argv) > 1 else ""
    with tempfile.TemporaryDirectory() as tmp:
        for fn in (test_pitch_and_groups, test_groups_shape,
                   test_grid_duplicate_body, test_pins_of, test_pin_report_verdict,
                   test_hidden_and_stacked, test_body_margin_both_axes,
                   test_multi_unit):
            if pat and pat not in fn.__name__:
                continue
            try:
                fn(tmp)
            except Exception:                                 # noqa: BLE001
                RESULTS.append((fn.__name__.replace("test_", ""), "EXCEPTION", False,
                                traceback.format_exc().splitlines()[-1]))
    by = {}
    for g, n, ok, d in RESULTS:
        by.setdefault(g, []).append((n, ok, d))
    nfail = 0
    for g in sorted(by):
        items = by[g]
        bad = [x for x in items if not x[1]]
        nfail += len(bad)
        print("%-12s %-38s (%d checks) %s"
              % (g, "", len(items), "FAIL" if bad else "ok"))
        for n, ok, d in items:
            if not ok:
                print("    FAIL  %s" % n)
                if d:
                    print("          %s" % d)
    print("\n%d checks, %d failed" % (len(RESULTS), nfail))
    return 1 if nfail else 0


if __name__ == "__main__":
    sys.exit(main())
