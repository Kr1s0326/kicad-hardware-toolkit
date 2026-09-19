---
name: kicad-check-pcb-component
description: Verifies a KiCad PCB footprint / package against its datasheet through four independent evidence paths - (1) dimension table measured by re-parsing the design's Gerber + Excellon output, (2) DRC for silkscreen-over-pad and courtyard defects, (3) 3D fit of the real part model onto the pads, (4) rendered PNG for human eyeball review - and writes an Excel inspection report shaped as index | 要求:图片 | 要求:数值 | 实际测量:图片 | 实际测量:数值 | 结果 with PASS / NG / 待测 plus an EVIDENCE.md bundle. Also checks the pin-number <-> pad-number contract against a schematic symbol. Split by package family - grid_array (WLCSP / CSP / BGA / LGA), peripheral (QFN / QFP / SOIC / TSSOP / SOT-23 / DPAK), chip (0402 / 0603 / 0805 / SOD-123 / MELF) - so a bug can be traced to one family. Use when asked to measure, check, verify or report component/package/footprint/stencil/PCB dimensions, to build a 元器件尺寸测量表 / 尺寸检查表 / dimension inspection report, to check a footprint renders correctly, or to compare a design against a drawing/datasheet.
license: MIT
---

# PCB 封装校验（KiCad）

把 *"这是数据手册的封装图 + 这是我做的封装"* 变成一份自证的证据包：

| index | 要求:图片 | 要求:数值 | 实际测量:图片 | 实际测量:数值 | 结果 |
|---|---|---|---|---|---|
| 1 | *(从图纸表格裁出来的一行)* | 2.8819 BSC | *(标注后的实测图)* | 2.8819 mm 偏差 +0.0000 | PASS |

图纸上的数字**一个都不抄**：每个"实际测量"值都是重新反解析 Gerber / 钻孔文件算出来的。

## 一条命令

```bash
python scripts/check_footprint.py <fp.kicad_mod> --outdir out \
       [--spec a.spec.json] [--symbol lib.kicad_sym] [--name INA239]
```

产出：

```
out/
├── EVIDENCE.md          逐项结论 + 每条的数据来源 + 必须人看的图
├── look_sheet.png       ★ 所有图拼一张，一眼看完
├── board.kicad_pcb      封装内联成的最小板（Gerber 导出 / DRC / 3D 都要用它）
├── board.kicad_dru      自定义短路间距规则（**由 spec 推出**，不是手写）
├── gerber/              F.Cu / F.Paste / F.Mask / F.SilkS / F.Fab / Edge.Cuts + Excellon
├── <name>_report.xlsx   尺寸测量表
├── drc.rpt              DRC 原始报告
├── fit3d_top.png        ★ 真实器件 3D 模型压在本封装焊盘上
├── fit3d_iso.png        ★
├── fp/<name>.png        ★ 2D 绘图（丝印/铜/装配/外框）
└── measure/meas_*.png    每一项尺寸的实测标注图
```

## 四条独立通路 —— 为什么是四条

**任何一条单独跑，都发现不了另外三条能发现的问题。**

| 通路 | 手段 | 抓什么 | 抓不到 |
|---|---|---|---|
| **A 尺寸** | Gerber/Excellon 反解 | 焊盘/跨距/间距/本体的数字错 | 丝印压盘、Pin1 画反、引脚悬空 |
| **B DRC** | `kicad-cli pcb drc` | 丝印压阻焊、外框缺失/过小、间距不足 | 焊盘数字错（规则不知道图纸） |
| **C 3D 贴合** | `pcb render` + 真实 STEP | 图纸数字与实物不符、跨距错 | Z 向以下的细节、电气问题 |
| **D 目视** | kicad-cli 出 SVG → PNG | 布局意图类错误（**唯一能覆盖这类的手段**） | 需要人；模型必须真的看图 |

外加一条跨 artifact 的：

| **E 契约** | 解析焊盘编号 ± 与符号交叉 | 引脚号 ↔ 焊盘号 对不上 | 引脚名、电气类型（那是符号侧的活） |

**铁律：没看过图，不许说"已验证"。** 详细清单见
[../../shared/render-and-look.md](../../shared/render-and-look.md)。

### 焊盘间距规则要从 spec 推，不能用默认值

KiCad 的短路间距默认 **0.2mm**，那是给 1.27mm 节距的普通封装用的。
0.4mm 节距的 WLCSP：相邻球心 0.35mm、焊盘 0.22mm，边到边只剩 **0.13mm** ——
拿默认值跑 DRC，会报 79 项"间距违规"，**而那一项缺陷都不是**：那是这一族
封装根本达不到的要求。真板子上必须放宽，校验侧也得跟着放宽，否则测的是
一个不可能满足的条件。

所以 `check_footprint.py` 会给测试板写一份 `board.kicad_dru`（KiCad 会自动
加载与 `.kicad_pcb` 同名的规则文件），下界由 `core/spec.py: min_pad_gap()`
算出：

```
球阵族：min(eS1 斜向球距, eD 球栅距) - Øb
引脚族：e 节距 - b 脚宽
```

**关键是它取自 spec（图纸要求），不是取自被测封装自己。** 取自封装自己的话，
规则永远成立，DRC 就退化成空跑。约束限 `A.Type == 'Pad' && B.Type == 'Pad'`，
不影响其他间距项的检查。

### 测量图必须以整颗器件为背景

每个 `panel()` 都走 `_full_view(ctx)`：窗口是**整颗器件**，被量的那几个焊盘
**高亮成红色**。不要给每个 kind 单独裁一个"刚好框住那几个焊盘"的窗口 ——
那样同一份报告里一半的图有整颗器件、一半只有局部，看的人没法把
"0.35" 对应回"哪两个球"。（peripheral 族先修了，grid_array 族漏了很久。）

标签要贴在**焊盘外沿左边**（`put_label(..., right=True)` 或 `anchor="rm"`）：
标签的白色底板会盖掉被高亮的焊盘，看上去就是"只高亮了一个"。
位置必须按实际焊盘外沿算，**不能拍脑袋给个比例**。
`put_label()` 收的是**像素**坐标。

---

## 布局 —— core + 一族一个文件

```
scripts/
├── core/                    ← 与封装无关，很少改动
│   ├── gerber.py            RS-274X + Excellon 解析（含 RoundRect 宏光圈）
│   ├── geometry.py          焊盘分组 / grid、peripheral、chip 归类
│   ├── spec.py              spec.json -> context
│   ├── draw.py              字体、视图、尺寸线、焊盘绘制
│   ├── common.py            与族无关的 kind（body/board/hole/stencil/na）
│   ├── report.py            xlsx + 图例页 + PNG 预览
│   └── board.py             把 .kicad_mod 内联成最小 .kicad_pcb
├── families/                ← 一个封装家族一个文件
│   ├── grid_array.py        WLCSP / CSP / BGA / LGA
│   ├── peripheral.py        QFN / QFP / SOIC / TSSOP / SOT-23 / DPAK
│   └── chip.py              0402 / 0603 / 0805 / SOD-123 / MELF
├── check_footprint.py       ★ 总入口：跑完 N 条通路，出证据包
├── measure_component.py     ★ 只出尺寸测量表（可单独用）
├── drc.py                   B：DRC 包装 + 违规解析分类
├── fit3d.py                 C：3D 实物贴合
├── selftest.py              各族黄金测试，不需要 CAD 文件
├── build_testboards.py      从 KiCad 官方库造真实测试板
└── crop_spec_table.py       从图纸截图里裁出「要求:图片」列

<toolkit>/shared/            ← 三个 skill 共用；**不在本 skill 的 scripts/ 下**
├── render.py                D：kicad-cli 出 SVG -> headless chrome 出 PNG
├── pinmap.py                E：引脚↔焊盘编号契约
├── kitext.py                KiCad 文本渲染常数（与 make_part.py 共用）
├── cli.py                   统一异常包装 / 退出码
├── toolchain.py             找 kicad-cli、KiCad 安装目录
└── render-and-look.md       看图规范（两个校验 skill 共用）
```

> **常见踩坑：** `render.py` / `pinmap.py` 不在本 skill 的 `scripts/` 里。
> 直接 `python scripts/render.py` 会 `No such file or directory`。
> 从本目录用 `../../shared/render.py`。

**出问题先看哪**

| 症状 | 文件 |
|---|---|
| 每个封装坐标/尺寸都不对 | `core/gerber.py`（解析） |
| 某个封装被归错族 | `core/geometry.py` |
| 本体外框 / 板框 / 孔数不对 | `core/spec.py`、`core/common.py` |
| 只有一个族量得离谱 | 那个 `families/*.py` |
| Excel 版式 / 判定颜色 / 预览 | `core/report.py` |
| 某一族的图画得不对 | 那个 `families/*.py` 的 `panel()` |
| 板子跑不了 DRC / 3D | `core/board.py` |
| 图出不来 / 出来是空白 | `shared/render.py` |
| 改了代码但行为没变 | **先删 `__pycache__`**（见下） |

改了任何东西之后跑：

```bash
python scripts/selftest.py                    # 110 项，不需要 CAD
python scripts/build_testboards.py --run      # 真实 QFN/QFP/SOT-23/SOIC-8/0603 Gerber
```

## 工作流

1. **拿到要求表。** 用户给图纸截图就读出每一行（`SYMBOL` + `MIN/NOM/MAX` 或 `BSC`）；
   给的是 PDF/PNG 就在里面找表。**做符号校验时别去读创建 skill 写的东西** —— 独立通路。
2. **定位制造数据。** 有 `gerber/` 就直接用；只有 `.kicad_pcb` / `.kicad_mod` 就跑
   `check_footprint.py`（它会自己建板、导出 Gerber 和钻孔）。
3. **写 spec.json。** 一行一个检查项，`kind` 见
   [references/families.md](references/families.md)；
   完整例子见 [assets/spec_template.json](assets/spec_template.json)。
4. **跑。**
   ```bash
   python scripts/check_footprint.py fp.kicad_mod --outdir out --spec a.spec.json
   python scripts/measure_component.py spec.json --preview      # 只要尺寸表时
   python scripts/measure_component.py --list-families
   ```
5. **看图。** 打开 `look_sheet.png` 和 `fit3d_*.png`，按
   [../../shared/render-and-look.md](../../shared/render-and-look.md) 的清单逐条确认。
6. **写结论。** 区分 `数值 PASS` / `目视确认` / `待目视` / `未做`（Z 向、实物贴装）。

## 家族速查

| family | 封装 | 专有 kind |
|---|---|---|
| `grid_array` | WLCSP, CSP, BGA, LGA | `array_w/h`(D1/E1), `matrix_cols/rows`(MD/ME), `count`(N), `dia`(Øb), `pitch`(eD), `row_span`(eE1s/eE2s/…), `diag_min/max`(eS1/eS2), `sd/se` |
| `peripheral` | QFN, QFP, SOIC, TSSOP, SOT-23, DPAK | `lead_pitch`(e), `lead_count`(N), `leads_per_side`(MD/ME), `side_count`, `pad_span_x/y`, `pad_edge_span_x/y`(D1/E1), `lead_width`(b), `lead_length`(L), `ep_x/ep_y`(D2/E2) |
| `chip` | 0402…1206, SOD-123, MELF | `count`, `pad_span`, `pad_edge_span`, `pad_gap`, `pad_w`, `pad_l`, `body_long/short` |
| 全家族通用 | – | `body_w/h`, `board_w/h`, `hole_count/dia/offset/edge`, `paste_hole_dia`, `na` |

## 必须遵守的规则

* **BSC / 基本尺寸是精确值。** 做对了偏差就是 0.0000 mm；`"tolerance": 0.005` 只是吸收
  Gerber 的纳米取整。比这大得多 = 设计真的错了。
* **Z 向项目永不臆造 PASS**（`A`、`A1`、`A2`、`T`、高度）：2D Gerber 没有这个方向的几何
  → `"kind": "na"` 报 待测。
* **球径是区间**（MIN/NOM/MAX）：量设计用的焊盘/钢网开孔，判它是否落在区间内。
* **交错（棋盘）阵列**：`MD` 数的是*网格位*（`round(array_w / 列网格) + 1`），
  斜向间距是到相邻排的最近邻距离（`eS1` 外排 / `eS2` 内排）。
  图纸本身可能自相矛盾 ~0.001 mm —— 以明确标注的值为准，并在 `note` 里写原因。
* **QFN 散热焊盘**：图纸的 `N` 不含它，所以 N 用 `lead_count`，要含它才用 `count`
  （QFN-32 实测：`count` = 33、`lead_count` = 32）。
  **落在 EP 里面的焊盘（过孔阵列 / paste 分块）不计入焊盘数**，见
  `classify_pads` 的 `n_buried`。
  > 平时碰不到：官方库 QFN 的 EP 分块多是 **paste-only**（只在 F.Paste 层），
  > 而测量只读 **F_Cu**，所以它们根本不出现。但**铜层**里放过孔就会中招 ——
  > 修之前那种封装会被静默判成 `grid_array`，然后拿去量 `array_w` /
  > `matrix_cols`，得到一堆看着像模像样的废话。现在按"完全落在 EP 内缩
  > 1µm 的框里"判定（**不用面积阈值** —— 那会误伤 EP 边上的大焊盘）。
* **数据只从 Gerber 量，绝不从封装文件读** —— 这是整件事的意义所在（能抓到导出/出图错误）。
* **`kicad-cli` 的 DRC 即使有违规，进程返回码也可能是 0**（除了加 `--exit-code-violations`）。
  必须解析报告正文，不能只看 returncode。`drc.py` 已经处理。
* **`fit3d` 必须显式解析 `(model ...)` 并确认 STEP 存在**，解析不到就报错，
  不许静默出一张没有零件的 3D 图。
* 报告在 Excel/WPS 里开着时，脚本会另存为 `…(new).xlsx`。

## 渲染 + 目视

见 [../../shared/render-and-look.md](../../shared/render-and-look.md)：出图命令、
踩过的坑、封装/符号各自的看图清单、以及"结论该怎么措辞"。

一句话：**数值全对不代表能用。**

## 输出

* Sheet 1：`index | 要求:图片 | 要求:数值 | 实际测量:图片 | 实际测量:数值 | 结果`
  （PASS 绿 / NG 红 / 待测 灰）。
* Sheet 2：说明「实际测量:图片」画的是什么（图例 + 每行的画法）。与
  `families/*.py` 的 `panel()` 保持同步。
* `measure/` 保留全部 2×N 张图备查。
* `EVIDENCE.md` + `look_sheet.png`：结论、数据来源、必须人看的图。

## 与其它 skill 的关系

| skill | 边界 |
|---|---|
| `kicad-create-lib-part` | 只负责**生成** `.kicad_sym` / `.kicad_mod`，只保证"能被 KiCad 加载"。不看图、不做正确性判断。 |
| `kicad-check-sch-component` | 校验原理图符号。本 skill 查"每个焊盘都有对应引脚"，它查"每个引脚都有对应焊盘" —— 两半合起来契约才成立。 |

**创建保证"可加载"，校验保证"正确"。** 别把这两件事混进一个 skill。

## 共享代码

`toolchain.py`（找 kicad-cli/chrome）、`cli.py`（统一错误处理）、
`render.py`、`pinmap.py`、`kitext.py`、`render-and-look.md` 都在
**`<toolkit>/shared/`**，只有一份。skill 通过 `../../shared/` 引用
（脚本里由 `SHARED` 常量解析）。

**改一处就够，不存在漂移。**

> `kitext.py` 装的是 KiCad 文本渲染常数（字符宽 / 名字离边距离 / 竖排半宽）。
> 生成侧 `make_part.py` 用它算本体得多宽，校验侧 `symbol_lint.py` 用它反推
> 两截文字会不会撞。**两边必须同组** —— 各写一份的话，生成侧按一套值留了空间、
> 校验侧按另一套值判断，明明压着字却报"无疑问項"。

## 本地验证时的一个坑：`__pycache__`

做"改坏一处、看测试会不会红"这种反向验证时，如果改动是**等长替换**
（例如把 `dim_h` 和 `dim_v` 对调），文件大小不变；一旦 mtime 落在同一时间
刻度上，Python 会**复用旧字节码** —— 你会看到"改了却没生效"，很容易误判成
"测试没用"。

本仓库已经因此误判过一次。本地做反向验证前先清：

```bash
find . -name __pycache__ -type d -exec rm -rf {} +
```

CI 每次都是全新 checkout，不受影响。

## 文件名里的两个坑（Windows）

给逐行 `req_image` 起名时，**不要只用符号名**。

### 大小写不敏感

Windows 上 `ref_E.png` 与 `ref_e.png` 是**同一个文件**。用符号名命名时：

```
ref_D.png   ref_E.png   ref_D2.png   ref_E2.png   ref_e.png   ref_b.png   ref_L.png
                              └─────────── 这两个是同一个文件 ───────────┘
```

后写的那张会把先写的**静默覆盖**。真实后果：ESP32-S3 的尺寸表里
"E 本体高" 那一行的「要求:图片」显示成了手册的 "e 0.400 BSC" 行 ——
两个不同的尺寸项张冠李戴，而所有数值和判定都是对的，只有图错了。

**用行序号前缀**：`req_%02d_%s.png`（`req_02_E.png` / `req_03_e.png`）。
这也正是 `measure_component.py` 归一化时用的命名，天然不会撞。

### 逐张读回校验会掩盖覆盖

循环里每写一张就立刻读回来对 md5，**当时那一刻是对的** ——
覆盖发生在后面那一次写入。所以要么最后统一校验一遍（并断言
**所有文件的 md5 互不相同**），要么直接把 md5 集合去重后比大小。
