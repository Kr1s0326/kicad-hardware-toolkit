---
name: kicad-create-lib-part
description: Generates a complete KiCad library part - a schematic symbol (.kicad_sym) plus a PCB footprint (.kicad_mod) plus the pin-number/pad-number link - from one part_spec.json that holds the datasheet pin table and the package drawing numbers. Handles symbol house style (200 mil within a functional group, 400 mil between groups, top-aligned sides, power pins on top/bottom, declared pin ordering) and footprint conventions (land-pattern pads, silkscreen clipped around pads, fab body outline, 12-segment courtyard, 3D model link) and also emits the groups.json and dimension spec the check skills consume. Use when asked to create, generate, write or add a KiCad symbol, footprint, 原理图符号, PCB 封装, .kicad_sym, .kicad_mod, or a new part in a library from a datasheet or a package drawing.
license: MIT
---

# 生成 KiCad 元件库（符号 + 封装）

**一个 spec 进，一套库出。**

```bash
python scripts/make_part.py part_spec.json --outdir out --verify
```

```
out/
├── <LIB>/<LIB>.kicad_sym             原理图符号
├── <LIB>.pretty/<FP>.kicad_mod       PCB 封装
├── <LIB>.groups.json                 功能分组声明   -> 给 kicad-check-sch-component
└── <LIB>.fp.spec.json                尺寸检查项     -> 给 kicad-check-pcb-component
```

最后两个是刻意一起生成的：**创建者必须把"要求"交出来，校验者才能独立地量。**

## 本 skill 的边界（最重要的一节）

> **创建保证"能被 KiCad 加载"，校验保证"正确"。**

| 做 | 不做 |
|---|---|
| 从 spec 生成 s-expression | 渲染看图 |
| 跑 `kicad-cli … upgrade` 确认能被解析 | 反解 Gerber 量尺寸 |
| 报告生成了什么 | 和手册比对引脚 |
| | ERC / DRC |
| | 3D 实物贴合 |

**这是刻意的，不是偷懒。** 创建侧一旦开始"看完图觉得没问题"，校验就退化成自证，
独立性就没了。所以本 skill 里没有一行渲染代码。

正确性交给：

```bash
# 符号：规矩 / 网表 / ERC / 手册比对 / 目视 / 契约
python ../kicad-check-sch-component/scripts/check_symbol.py out/<LIB>/<LIB>.kicad_sym \
       --outdir chk --groups out/<LIB>.groups.json --pdf <datasheet.pdf> \
       --footprint out/<LIB>.pretty/<FP>.kicad_mod

# 封装：Gerber 量测 / DRC / 3D 贴合 / 目视 / 契约
python ../kicad-check-pcb-component/scripts/check_footprint.py \
       out/<LIB>.pretty/<FP>.kicad_mod --outdir chk2 --spec out/<LIB>.fp.spec.json \
       --symbol out/<LIB>/<LIB>.kicad_sym
```

`make_part.py --verify` 跑完会直接把上面两条命令打出来。

## 唯一真源：part_spec.json

模板：[assets/part_spec_template.json](assets/part_spec_template.json)（里面填的就是 TI INA239 的真实数据）。

```
pins[]          ← 手册的 Pin Functions 表（号 / 名 / 电气类型）
groups{}        ← 哪些引脚是同一功能（手册不写，人判断）
symbol_style{}  ← house style（间距 / 分组间距 / 引脚长 / 本体宽 / 对齐方式）
package{}       ← 手册的 Package Outline + Example Board Layout 图
```

**改了 spec 就重生成，不要手工去改产物** —— 手改的产物下次生成就丢了，
而且没人知道哪个是对的。

### 引脚顺序 = spec 里的数组顺序

组内先后由 `pins[]` 的书写顺序决定，组间先后由 `groups{side}` 决定。

想要右侧是 `MOSI / MISO / SCLK / CS`，就把这四行按这个顺序写进 `pins[]`；
想让它自成一个功能组、和 `ALERT` 隔开 400 mil，就给它们同一个 `group` 名，
并把该组放进 `groups.right` 的第一位。

## 布局规则（都在官方库上校准过）

### 符号

| 规则 | 值 | 依据 |
|---|---|---|
| 组内间距 | `pitch_mil`，默认 200 | house style |
| 组间间距 | `group_gap_mil`，默认 400 | house style |
| 引脚长度 | `pin_length_mil`，默认 100 | KiCad 惯例 |
| 每边对齐 | `side_align`，默认 `top` | **KiCad 官方库惯例**（INA226 就是这样：Vbus 和 A1 都在 7.62） |
| 上/下电源脚 | `side` 写 `top` / `bottom`，x=0 | 惯例 |
| 本体 | 由最高的一侧 + 上下各 100 mil 余量推出 | – |

`side_align: "center"` 可改成每边各自居中。**顶对齐更常见**，因为左右两侧的
第一个引脚会对上，跨侧读数更容易。

### 封装

| 元素 | 规则 | 校准依据 |
|---|---|---|
| 焊盘 | 来自 Land Pattern，`roundrect_rratio = 圆角半径 / 焊盘短边` | 图纸标注 |
| 焊盘 Y 方向 | **+Y 是向下**，所以 pad1（左上）的 y 是负的 | JEDEC 编号惯例 |
| 丝印框 | 本体/2 + 0.11 | 官方 MSOP-10 与 SOIC-8 逐值对上 |
| 丝印被焊盘裁掉 | 裁掉边界 = 焊盘边缘 + 0.2 + 丝印半宽 0.06 = **+0.26** | 同上，两个封装都吻合 |
| 装配层 | 本体真实外形，pin1 角倒角 0.75 | KiCad 惯例 |
| 外框 | `max(本体, 焊盘外沿) + 0.25`，**12 段十字形而非矩形** | 官方 MSOP-10 与 SOIC-8 都是 12 段，逐段对上 |
| Pin1 三角标 | 丝印角点外 0.16 / 0.55 | 官方库同款 |

## 输出可加载性检查

```bash
python scripts/kicad_io.py probe                  # 本机 KiCad 的格式版本
python scripts/kicad_io.py loadable <path>        # 能否被解析（rc=2 表示不能）
```

`probe` 会去**读本机官方库的真实 version token**，不靠记忆填日期 ——
这个仓库里所有版本号都是从 `Amplifier_Current.kicad_sym` /
`Package_SO.pretty/*.kicad_mod` 现场读出来的。

**判定不能只看 returncode。** kicad-cli 加载失败返回 2，但成功时消息是
`符号库未更新` / `已使用最新格式成功保存`，两种要一起看。

## 出问题先看哪

| 症状 | 文件 |
|---|---|
| 引脚分组/间距不对 | `make_part.py` 的 `layout_symbol()` / `sequence()` |
| 焊盘编号方向反了 | `layout_pads()`（+Y 向下的坑） |
| 丝印压到焊盘上 | `silk_lines()` 的 `COPPER_CLR` / `SILK_W` |
| 外框形状不对 | `courtyard_cross()`（**不能**因为 PY<by 就把 PY 拉平） |
| 生成的符号/封装 KiCad 加载不了 | `emit_symbol()` / `emit_footprint()` 的 token |
| 版本号不对 | `kicad_io.probe_formats()` |

## 一次完整的流程

1. 读手册：Pin Functions 表 → `pins[]`；Package Outline + Land Pattern → `package{}`。
   怎么抽见 [references/datasheet-extract.md](references/datasheet-extract.md)。
2. 判断功能分组 → `groups{}`。（**这一步是人的活**：手册只说"pin 8 是 Analog input"，
   不说"它和 IN+/IN− 不是一回事"。）
3. 生成：`python scripts/make_part.py spec.json --outdir out --verify`
4. 校验：把 `--verify` 打出来的两条命令跑掉。
5. 看图：打开校验产生的 `look_sheet.png`。

参考文档：

* [references/symbol-rules.md](references/symbol-rules.md) —— 符号的 house style 细则
* [references/footprint-rules.md](references/footprint-rules.md) —— 封装的几何细则与校准数据
* [references/kicad-formats.md](references/kicad-formats.md) —— s-expression 的格式坑
* [references/datasheet-extract.md](references/datasheet-extract.md) —— 从 PDF 抽表和抽图
