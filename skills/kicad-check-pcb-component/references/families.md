# 封装家族与 spec.json 参考

`measure_component.py` 读一个 JSON spec。本文件说明每个字段、每个家族的
`kind`，以及图纸符号 → 测量项的对照和判定规则。

---

## 1. 选哪个家族

| family | 几何特征 | 覆盖封装 | 判断依据（自动判别时） |
|---|---|---|---|
| `grid_array` | 焊盘铺满底面，成二维行列 | WLCSP / CSP / BGA / LGA / μBGA | 外框**内部**（去掉散热焊盘后）还有焊盘 |
| `peripheral` | 焊盘贴在封装**外缘**（2 边或 4 边） | QFN / DFN / QFP / LQFP / TQFP / SOIC / SOP / SSOP / TSSOP / MSOP / SOT-23 / SOT-223 / DPAK | 只有 2 个焊盘以外，内部是空的 |
| `chip` | 只有 2 个端子 | 0402 / 0603 / 0805 / 1206 / SOD-123 / SMA / MELF | 焊盘数 == 2 |

自动判别顺序：`chip` → `grid_array` → `peripheral`（最具体的先判断）。
不确定时用 `--family <name>` 强制指定，或用 `--list-families` 看清单。

**为什么这样分**：数学不同。
- 网格阵列算的是矩阵位数、行列网格、斜向球距、基准偏移；
- 边引脚算的是引脚间距、跨距、脚宽/脚长、散热焊盘；
- 两端子只有一个"端子跨距/间隙"。
SOT-23 与 QFN/QFP 同属 `peripheral`（都是边引脚），代码共用一份，
所以修"引脚间距"只需改 `families/peripheral.py` 一个地方。

---

## 2. 顶层字段

| key | 默认 | 说明 |
|---|---|---|
| `title` | 元器件尺寸测量表 | 表名 |
| `component` | – | 被测对象描述（打印出来） |
| `family` | 自动判别 | `grid_array` / `peripheral` / `chip` |
| `output` | dimension_report.xlsx | 输出文件名（相对 spec 所在目录） |
| `sheet_name` | 尺寸测量 | 第一个工作表名 |
| `img_dir` | measure | 图片输出目录 |
| `tolerance` | 0.005 | 全局判定公差（mm） |
| `gerber_dir` | `<spec>/gerber` | 制造文件目录 |
| `files` | 自动查找 | 显式指定各层文件：`cu` `silk` `fab` `paste` `mask` `edge` `drill` |
| `pad_filter` | `auto` | 焊盘过滤：`auto`（默认，去掉散热盘内部的过孔/贴片分块、极小杂点）；`mode`（只留最多的光圈尺寸，旧行为）；数字（只留该光圈） |
| `collinear_tol` | 0.05 | 归并同行/同列的坐标容差（mm） |
| `body_min_len` | 0.5 | 视为本体的线段最小长度（mm），会再乘一个相对阈值 |
| `body_rel_len` | 0.4 | 相对阈值：线段长度 ≥ 0.4 × 最长线段才算本体 |
| `body_search` | 自动 | 本体搜索半径（默认 `max(4, 0.9×阵列尺寸)`） |
| `spec_table_image` | – | 图纸尺寸表截图，用于裁出「要求:图片」列 |
| `spec_table_skip` | 2 | 截图里表头占几行（经典 JEP95 表是 2 行） |
| `row_height` | 250 | Excel 行高（磅） |
| `notes` | [] | 表下方备注文字 |
| `legend` | – | 第二个工作表（图例说明）的内容 |
| `rows` | **必须** | 逐行检查项 |

## 3. 行（rows）字段

```json
{ "symbol": "eE3s",              // 显示名
  "requirement": "0.682 BSC",    // 「要求:数值」列文字
  "kind": "row_span",            // 测量类型，见 §4
  "nominal": 0.682,              // 期望值 -> 算偏差 + PASS/NG
  "tol": 0.005,                  // 覆盖全局公差
  "from": "E", "to": "G",        // kind 专用参数
  "min": 0.188, "max": 0.248,    // 有 min/max 时按区间判定（Øb）
  "note": "G 排 至 E 排球心距",   // 实测值下方文字
  "req_image": "img/req.png",    // 单独指定「要求:图片」（覆盖裁图）
  "side": "left",                // leads_per_side 指定哪一边
  "compare": "count" }           // 强制按整数比较
```

## 4. 各家族的 kind

### 4.1 通用（所有家族，实现在 core/common.py）

| kind | 测量 | 典型符号 |
|---|---|---|
| `na` | 不测（Z 向） | A / A1 / A2 / A3 / T |
| `body_w` `body_h` | 本体外框 X/Y 尺寸（丝印优先、无则装配层） | D / E |
| `board_w` `board_h` | 板框尺寸 | – |
| `hole_count` `hole_dia` | 钻孔个数 / 最大孔径 | – |
| `hole_offset` `hole_edge` | 孔心到板边 / 孔边到板边 | – |
| `paste_hole_dia` | 钢网层最大实心圆直径（描边扫掠后的真实直径） | – |

### 4.2 `grid_array`（families/grid_array.py）

| kind | 测量 | 典型符号 |
|---|---|---|
| `array_w` `array_h` | 球阵列跨距（最外两排球心距） | D1 / E1 |
| `matrix_cols` `matrix_rows` | 矩阵位（列位/行位，含空位） | MD / ME |
| `count` | 球数 | N |
| `dia` | 球径（焊盘/钢网开孔直径） | Øb |
| `pitch` | 球栅距（同排相邻球心距） | eD |
| `row_span` | 指定两排球心距，`from`/`to` 用 JEDEC 行字母（A B C D E F G H J K L M N P R T U V W Y，无 I O Q S X Z）或 0 基序号（0 = 最外排） | eE1s / eE2s / eE3s … |
| `diag_min` `diag_max` | 斜向最近球间距（外排 / 中间排） | eS1 / eS2 |
| `sd` | 外排中心球相对基准 B（本体中心线 X）的偏移 | SD |
| `se` | 阵列中心相对基准 A（本体中心线 Y）的偏移 | SE |

### 4.3 `peripheral`（families/peripheral.py）

| kind | 测量 | 典型符号 |
|---|---|---|
| `lead_pitch`（= `pitch`） | 引脚间距（同一边相邻引脚中心距） | e |
| `lead_count` | 引脚总数（**不含**散热焊盘） | N |
| `count` | 焊盘总数（含散热焊盘） | – |
| `leads_per_side` | 每边引脚数，可加 `"side": "left/right/top/bottom"` | MD / ME |
| `side_count` | 有引脚的边数（2 或 4） | – |
| `pad_span_x` `pad_span_y` | 两排引脚中心距 | – |
| `pad_edge_span_x` `pad_edge_span_y` | 引脚跨距（外缘到外缘） | D1/E1、terminal span |
| `lead_width` | 引脚宽度 b（焊盘窄边） | b |
| `lead_length` | 引脚长度 L（焊盘长边） | L |
| `ep_x` `ep_y` | 散热焊盘尺寸 | D2 / E2 |

### 4.4 `chip`（families/chip.py）

| kind | 测量 | 典型符号 |
|---|---|---|
| `count` | 端子数（应为 2） | – |
| `pad_span`（= `pitch`） | 端子跨距（中心到中心） | e |
| `pad_edge_span` | 端子外缘跨距 | – |
| `pad_gap` | 端子间隙（两内侧边距离） | g |
| `pad_w` | 端子宽度（垂直跨距方向） | a |
| `pad_l` | 端子长度（跨距方向） | b |
| `body_long` `body_short` | 本体长边/短边（与摆放方向无关） | L / W |

## 5. 判定规则

| 情况 | 结果 |
|---|---|
| `kind == "na"` 或测不到 | `待测` |
| 计数类（`count` / `matrix_*` / `lead_count` / `leads_per_side` / `side_count` / `hole_count`） | 整数完全相等 → PASS |
| 给了 `min`/`max`（如 Øb） | 落在区间内 → PASS |
| 其他给了 `nominal` | `abs(实测 − nominal) ≤ tol` → PASS |
| 没给 `nominal` | 只报数，判 PASS（用于没有标称值的行） |

## 6. 常见坑（都已在代码里处理，改代码时别破坏）

* **圆角矩形焊盘**：KiCad 用光圈宏 `%AMRoundRect` 画，光圈尺寸不等于焊盘尺寸，真实尺寸 = 四角方框 + 2×圆角半径（见 `core/gerber.py: aperture_size`）。
* **散热焊盘上的过孔阵列**：KiCad 的 QFN 封装在散热盘上放了 3×3 无编号焊盘，必须丢掉，否则会被判成"网格阵列"（见 `core/gerber.py: filter_pads`）。
* **边引脚的"哪一边"**：必须按**最近边**判定，用"四分之一带宽"会把 QFP 的
  上下排引脚判到左右边去（见 `core/geometry.py: classify_pads`）。
* **本体外框**：丝印/装配层上还有元件位号、数值文字，字号大时笔画可能和小封装
  的本体线一样长，所以本体 = **最大的闭合矩形**（两条最长水平线 + 能闭合的
  两条竖线），找不到才退化成"长线段包围盒"（见 `core/spec.py: body_rect`）。
* **多段圆弧组成的圆**：钢网定位孔是"实心圆"用圆光圈描边画出来的，要按
  `路径半径 + 光圈/2` 还原真实直径（见 `core/gerber.py: stroked_circles`）。
* **镜像**：图纸多是 bottom view，封装是 top view。跨距/间距/计数与镜像无关，但阵列不对称（如 A1 空球）时要在报告里注明假设的朝向。
