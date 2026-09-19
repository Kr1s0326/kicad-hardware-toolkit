---
name: kicad-check-sch-component
description: Verifies a KiCad schematic symbol against its datasheet through four independent evidence paths - (1) house-style lint on geometry (pin pitch, functional grouping, grid alignment, body fit, name overflow), (2) a netlist that KiCad itself exports after parsing the symbol, (3) ERC to extract the electrical types KiCad actually inferred, (4) a rendered PNG for human eyeball review - plus the pin-number <-> pad-number contract cross-check against a footprint. Reports a three-way comparison table PDF pin table vs netlist names vs ERC electrical types with PASS / NG. Use when asked to check, verify, lint or review a schematic symbol / 原理图符号 / .kicad_sym, to confirm pins match the datasheet, to enforce symbol drawing rules (200 mil within a functional group, 400 mil between groups), or to find pins that are off-grid, mis-typed, duplicated or overlapping.
license: MIT
---

# 原理图符号校验（KiCad）

把 *"这是数据手册的引脚表 + 这是我画的符号"* 变成一份自证的证据包。

```bash
python scripts/check_symbol.py <lib.kicad_sym> --outdir out \
       --symbol INA239 --pdf ina239.pdf \
       --groups assets/groups_template.json \
       --footprint INA239.pretty/VSSP-10.kicad_mod
```

产出：

```
out/
├── EVIDENCE.md      逐项结论 + 每条的数据来源 + 必须人看的图
├── look_sheet.png   ★ 符号渲染图
├── lint.txt         规矩检查明细（各边间距 / 分组）
├── INA239.kicad_sch 自动搭的最小原理图（下面 B/C 两条通路都用它）
├── INA239.net       ★ KiCad 自己解析符号后导出的网表
├── erc.rpt          ERC 原始报告
├── cmp_pins.json    PDF <-> 网表 <-> ERC 三方比对明细
└── sym/*.png        ★ 渲染图
```

## 为什么必须是四条通路

**电气类型画出来完全一样。** 把 `MISO` 标成 `input`，渲染图、间距、栅格、
本体大小全都正常 —— 只有 ERC 会说。反过来，"组间 400 mil" 这种规矩
ERC 和网表都看不见。**没有任何一条通路能替代另一条。**

**要求一侧可以只给名字。** 很多 MCU 手册的引脚表只有球号 + 信号名，
压根没有 TYPE 列（Infineon PSoC 62 的 Table 7 就是这样）。这时
"电气类型"既不算 PASS 也不算 NG **而是“类型未声明”** —— 那是"要求一侧
根本不存在"，不是"符号错了"。

踩过的坑：以前直接拿 `None` 去比 ERC 推导出的类型，于是**每个引脚都报 NG**，
汇总变成"49/49 不一致"，而名字其实 49 个全对。**假 NG 比不查更糟**：
看报告的人会以为符号错了。现在 xlsx 里是第三种判定（黄色），汇总单独计数。

| 通路 | 手段 | 抓什么 | 抓不到 |
|---|---|---|---|
| **A 规矩** | 纯几何解析 | 间距 / 分组 / 栅格 / 本体 / **四角文字交叠** | 引脚名、电气类型 |
| **B 网表** | `sch export netlist` | 引脚号/名的结构错（KiCad 读不出） | 位置、电气类型 |
| **C ERC** | `sch erc` | **电气类型**、跨引脚电气冲突 | 位置、名字 |
| **D 目视** | 渲染 → PNG | 分组观感、压字、本体比例 | 需要人 |
| **E 契约** | 与封装交叉 | 引脚号 ↔ 焊盘号 | 名字、类型 |

**铁律：没看过图，不许说"已验证"。** 清单见
[../../shared/render-and-look.md](../../shared/render-and-look.md)。

### 四角文字交叠（`overflow`）

四边封装里，上/下排的引脚名是**竖着**写的、左/右排的是**横着**写的，
两者在四个角抢同一块地方。判据：

```
左侧名字的右端  <  最左那个上/下排名字的左端
```

**名字不是贴边画的** —— KiCad 把它画在本体内侧 `NAME_OFF = 0.85mm` 处。
漏掉这个偏移，估出的名字就短 0.67mm，明明压着字却报"无疑问项"。
常数放在 [`shared/kitext.py`](../../shared/kitext.py)，**与生成侧
`make_part.py` 共用同一组** —— 各写一份迟早漂移，然后检查静默失效。

这条只进 `warnings`（咨询项）：它不影响电气，但影响可读性 ——
渲染图上 XRES 被 VDDD 盖掉一个字符时，ERC 和网表都是全绿的。

---

## 最重要的一条限制：分组是**输入**

> **「组间 400 mil」这条规则几何上不可判。**

实测：左侧原本 `VBUS(12.7) ─400mil─ IN+(2.54) ─200mil─ IN−(−2.54)`，
把 `IN+` 提到 7.62 就变成 `VBUS(12.7) ─200mil─ IN+(7.62) ─400mil─ IN−(−2.54)`
—— 间距变成了 200/400，**两种分组都满足"组内 200 / 组间 400"**。
光看坐标，工具分不清"合规的另一个分组"和"你搭错了"。

所以必须用 `--groups` 把语义分组告诉工具（模板见
[assets/groups_template.json](assets/groups_template.json)）。
不给的话工具会明确打印"该规则无法真正校验"，而不是假装通过。

给了分组之后，工具会额外做一件事：**把几何推断出的分组和声明的分组对账**，
不一致直接 FAIL。这条对账才是"我以为的分组"和"画出来的分组"真正对上。

---

## 布局

```
scripts/
├── check_symbol.py     ★ 总入口：跑完 5 条通路，出证据包
├── symbol_lint.py      A：规矩检查（纯几何，无需 KiCad，可单独用）
├── sch_build.py        把 .kicad_sym 塞进最小 .kicad_sch
├── sch_netlist.py      B：导网表 + 解析 KiCad 眼中的引脚
├── sch_erc.py          C：跑 ERC + 提取电气类型 + 分类违规
├── pdf_pins.py         从手册 PDF 抽 Table 5-1（含 open-drain 识别）
├── render.py           D：kicad-cli 出 SVG -> headless chrome 出 PNG
├── selftest.py         22 项黄金测试（纯标准库，不需要 CAD）
├── pinmap.py           E：引脚↔焊盘编号契约
└── (render.py / pinmap.py 已移到 <toolkit>/shared/，只有一份)
references/
├── checks.md               每一项检查的数据来源、判定、以及做不到的部分
└── render-and-look.md      出图命令 + 踩过的坑 + 看图清单（在 shared/，只有一份）
assets/
└── groups_template.json    分组声明模板（把这个填了再跑）
```

**出问题先看哪**

| 症状 | 文件 |
|---|---|
| 间距/分组判断不对 | `symbol_lint.py` 的 `side_of()` / `root_of()`（rot 的含义容易搞反） |
| 网表里少引脚 / 名字不对 | `sch_build.py`（符号块切分）+ 检查 `.kicad_sym` 本身 |
| ERC 报一堆 `endpoint_off_grid` | `sch_build.py` 的放置点没吸附到 1.27 mm 栅格 |
| ERC 该报的没报 | `sch_erc.py` 的 `EXPECTED` 集合把真信号滤掉了 |
| 手册引脚抽不出来 | `pdf_pins.py` 的 `ROW` 正则 / `--page-table` 标记 |
| 图出不来 | `render.py`（见 render-and-look.md 的坑表） |

## 工作流

1. **先填分组 —— 由 AI/LLM 判。** 拿 `assets/groups_template.json`，按手册的
   引脚功能（引脚名、电源域、复用功能表）把每个引脚的分组写清楚。
   这一步工具做不了，也正是"组间 400 mil"能被校验的前提。
2. **跑。**
   ```bash
   python scripts/check_symbol.py lib.kicad_sym --outdir out \
          --pdf ds.pdf --groups groups.json --footprint fp.kicad_mod
   python scripts/symbol_lint.py lib.kicad_sym --groups groups.json   # 只查规矩时
   ```
3. **读三方比对表。** `pin | PDF名 | 网表名 | 名? | PDF电气 | ERC电气 | 类?`
   —— 每一行都是"手册 ↔ KiCad 自己的解读"。
4. **看图。** 打开 `look_sheet.png`，按 render-and-look.md §4.2 的清单逐条确认。
5. **写结论。** 区分 `规矩 PASS` / `手册比对 PASS` / `目视确认` / `待目视` / `未做`。

## 各项命令单独用

```bash
python scripts/symbol_lint.py lib.kicad_sym [--groups g.json] [--min-pitch 200] [--group-gap 400]
python scripts/pdf_pins.py ds.pdf [--page-table "Table 5-1"] [--dump] [--overrides o.json]
python scripts/sch_netlist.py lib.kicad_sym workdir [--symbol NAME]
python scripts/sch_erc.py sch.kicad_sch
python scripts/pinmap.py --symbol lib.kicad_sym [--footprint fp.kicad_mod]
python scripts/render.py sym lib.kicad_sym outdir
```

退出码：`symbol_lint` / `check_symbol` = 1/9 表示有 FAIL；`sch_erc` = 9 表示有
非必然项；`pinmap` = 8 表示契约不成立。

## 与其它 skill 的关系

| skill | 边界 |
|---|---|
| `kicad-create-lib-part` | 只负责**生成** `.kicad_sym` / `.kicad_mod`，只保证"能被 KiCad 加载"。不看图、不做正确性判断、不读手册做比对。 |
| `kicad-check-pcb-component` | 校验 PCB 封装。引脚↔焊盘契约由两边共同成立。 |

**创建保证"可加载"，校验保证"正确"。** 别把这两件事混进一个 skill。

## 共享代码

`toolchain.py`（找 kicad-cli/chrome）、`cli.py`（统一错误处理）、
`render.py`、`pinmap.py`、`render-and-look.md` 都在 **`<toolkit>/shared/`**，只有一份。skill 通过 `../../shared/` 引用（脚本里由 `SHARED` 常量解析）。

**改一处就够，不存在漂移。**
