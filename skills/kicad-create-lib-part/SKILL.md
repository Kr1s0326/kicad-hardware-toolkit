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
groups{}        ← 哪些引脚是同一功能（手册不写，由 AI/LLM 判断）
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
| 组内间距 | `pitch_mil` = **200，固定** | **本库的 house style，不按器件改** |
| 组间间距 | `group_gap_mil` = **400，固定** | 同上 |
| 引脚长度 | `pin_length_mil`，默认 100 | KiCad 惯例 |
| 每边对齐 | `side_align`，默认 `top` | **KiCad 官方库惯例**（INA226 就是这样：Vbus 和 A1 都在 7.62） |
| 上/下电源脚 | `side` 写 `top` / `bottom`，x=0 | 惯例 |
| 本体 | 由最高的一侧 + 上下各 100 mil 余量推出 | – |

### 200 / 400 是固定的，不要改成 100

> **这是本库的风格约定，不是参数。全库统一，不按器件改。**

理由不是"哪个更好看"：同一个库里混着 100 mil 和 200 mil 的符号，
读图的人每换一颗器件都要重新建立比例感。一致性比单个符号的紧凑更重要。

**不要拿"官方库用 100 mil"当理由改。** 确实如此 —— 量过 STM32F103C8Tx、
ATmega328P、PCA9555、TCA9548A，官方一律组内 2.54mm / 组间 5.08mm ——
但**那是 KiCad 的风格，不是我们的**。我们跟自己的库对齐，不跟官方库对齐。

代价要认：引脚多时符号会很大。49 个引脚按 200 mil 排，本体 **55.9 × 121.9 mm**
（100 mil 能压到 35.6 × 63.5 mm）。**不要为了缩小符号去改 pitch**；
真嫌大就重新分配引脚到哪条边（把 `groups{}` 的两侧配平），那是布局的事。

历史：CY8C6245 第一版曾改成 100 mil，与库里的 ESP32-S3（200 mil）不一致，
已改回。`build_spec.py` 的自检现在会把非 200/400 当错误报出来。

### 本体宽度由三个约束一起定（不是只靠 `body_half_width_mil` 拍）

`layout_symbol()` 取三者最大，**最后统一向上取整到 50 mil**：

1. spec 给的 `body_half_width_mil`
2. 上/下排引脚铺得开：`tb_span/2 + top_margin`
3. **四角不能压字**：`NAME_OFF + 最长的左/右引脚名 + CORNER_GAP + tb_span/2 + 竖排半宽`

第 3 条踩过两次坑（ESP32-S3 与 CY8C6245）。上/下排的引脚名是**竖着**写的，
从本体上、下边沿往里伸；左/右排的名字横着写，从左、右边沿往里伸 —— 四角就是
这两排抢的地方。只保证"引脚塞得进本体宽度"是不够的：本体够宽、引脚都在里面，
**名字却叠在一起**。

常数放在 [`shared/kitext.py`](../../shared/kitext.py)，**生成侧和校验侧的
`symbol_lint` 共用同一组** —— 各写一份迟早会漂移，然后检查就静默失效：
明明压着字，却报"无疑问项"。（CY8C6245 上就是这么漏掉的：lint 漏算
"名字离本体边缘 0.85mm"这个偏移，估出的名字短了 0.67mm，刚好躲过判定。）

**取整那一步不能省。** 引脚根部 x = `±(hw + pin_length)`，`hw` 只要取了
非整格的值，**整排引脚**就一起掉到 50 mil 栅格外 —— 画出来完全正常，
却一根线也连不上，只有 ERC 会报一屏 `endpoint_off_grid`。
（CY8C6245 算到 17.22mm，38 个引脚全掉出栅格。）

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

### 球栅阵列（`package.family = "grid_array"`）

WLCSP / CSP / BGA / LGA 走另一套几何，**不要照搬上面那张表**。在
`Package_CSP.pretty` 的三个官方 WLCSP（Anpec-20 / Maxim-35 / Efinix-64）上量的：

| 元素 | 球阵族 | 引脚族（上面那套） |
|---|---|---|
| 焊盘 | **`smd circle` + `(property pad_prop_bga)`** | `smd roundrect` |
| 外框 | **矩形，本体 +1.0mm/边**（IPC-7351 标称，球阵要留返修空间） | 12 段十字，+0.25 |
| 装配层倒角 | **`0.5 × min(本体半宽, 本体半高)`** | 固定 0.75 |
| 阻焊开窗 | `(solder_mask_margin)`，按球径取（官方 0.02 / 0.05） | 不写 |
| 丝印 | 本体/2 + 0.11，被圆焊盘裁 | 同 |

**焊盘位置不是算出来的，是从引脚号解出来的。** JEDEC 球名（`A11`、`C7`）
本身就把行列编进去了，所以 `layout_pads()` 直接解析 `pins[].number`：

```json
"package": {
  "family": "grid_array",
  "row_letters": "ABCDEFGHJ",            // JEDEC 行字母，无 I/O/Q/S/X/Z
  "row_y": [-1.242, -0.962, -0.682, -0.341, 0.0, 0.341, 0.682, 0.962, 1.242],
  "col_pitch": 0.21, "col_zero": 6,        // x = (列号 - col_zero) * col_pitch
  "pitch": 0.42, "array_w": 2.100, "array_h": 2.484,
  "matrix_cols": 11, "matrix_rows": 9,
  "ball_dia": 0.218, "pad_dia": 0.22,
  "row_spans": [{"symbol":"eE1s","nominal":0.560,"from":"G","to":"J"}],
  "diag_min": 0.350, "diag_max": 0.4005, "sd": 0.21, "se": 0.00
}
```

**`row_y` 给的是一整张表，不是一个行距。** 交错阵列（SG-XFWLB-49）的行距是
0.280 / 0.341 交替的，单个 `row_pitch` 表达不了 —— 而图纸恰恰是用交替行距
才凑出 E1=2.484 和 eE1s/eE2s/eE3s 三个数的。

`build_spec.py` 里的自检会拿 `sqrt(col_pitch² + 行距²)` 反算 eS1/eS2 与图纸对账：
对得上，说明行距表没抄错。这个判据不依赖眼睛。

A1 通常是**空位**（图纸注明 `+` = depopulated），但矩阵位数 MD/ME 仍把它算进去 ——
所以 `N = MD × ME − 空位数`，别用 N 去反推 MD。

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
| 四角的引脚名叠在一起 | `layout_symbol()` 的四角净空段（常数在 `shared/kitext.py`）|
| 引脚全掉在 50 mil 栅格外 | `layout_symbol()` 末尾的 `hw` 向上取整 |
| 版本号不对 | `kicad_io.probe_formats()` |

## 一次完整的流程

1. 读手册：Pin Functions 表 → `pins[]`；Package Outline + Land Pattern → `package{}`。
   怎么抽见 [references/datasheet-extract.md](references/datasheet-extract.md)。
2. **判断语义** → `pins[].etype`、`pins[].side`、`groups{}`。
   这三样手册里都没有，**由 AI/LLM 从手册上下文判**（引脚名、所属电源域、
   复用功能表、电气特性章节）。工具算不出来 —— 几何上看不出"VBUS 和 IN+ 不是一回事"。
   判错了画出来一模一样，只有 ERC（类型）、契约（号↔盘）和目视（摆放）能证伪。
3. 生成：`python scripts/make_part.py spec.json --outdir out --verify`
4. 校验：把 `--verify` 打出来的两条命令跑掉。
5. 看图：打开校验产生的 `look_sheet.png`。

参考文档：

* [references/symbol-rules.md](references/symbol-rules.md) —— 符号的 house style 细则
* [references/footprint-rules.md](references/footprint-rules.md) —— 封装的几何细则与校准数据
* [references/kicad-formats.md](references/kicad-formats.md) —— s-expression 的格式坑
* [references/datasheet-extract.md](references/datasheet-extract.md) —— 从 PDF 抽表和抽图
