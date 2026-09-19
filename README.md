# kicad-hardware-toolkit

从数据手册生成 KiCad 元件（原理图符号 + PCB 封装），并对产物做独立校验。

```
part_spec.json ──> kicad-create-lib-part ──┬──> LIB.kicad_sym
   ▲                                       ├──> LIB.pretty/FP.kicad_mod
   │                                       ├──> LIB.groups.json      ─┐
 数据手册                                   └──> LIB.fp.spec.json    ─┤
                                                                    │
                          LIB.kicad_sym   ──> kicad-check-sch-component
                          LIB.kicad_mod   ──> kicad-check-pcb-component
```

校验脚本以 Gerber、Excellon、网表、STEP 模型为输入，不读生成器写出的文本本身。

## 组件

| 组件 | 职责 | 不做 |
|---|---|---|
| `kicad-create-lib-part` | 由 `part_spec.json` 生成 `.kicad_sym`、`.kicad_mod`、以及校验所需的 `groups.json` / `fp.spec.json` | 不做正确性判断，不含任何渲染代码 |
| `kicad-check-sch-component` | 校验符号：绘制规范、网表、ERC、与手册比对、渲染、引脚契约 | 不生成任何文件 |
| `kicad-check-pcb-component` | 校验封装：Gerber 量测、DRC、3D 实物贴合、渲染、引脚契约 | 不生成任何文件 |

生成器与校验器分离是硬约束：若生成侧也做"看着没问题"的判断，校验即退化为自证。
生成器只保证产物能被 `kicad-cli` 加载。

## 校验通路

封装校验由五条互不重叠的通路组成。任一条单独运行都无法发现其余通路能发现的问题。

| 通路 | 输入 | 可发现的缺陷 |
|---|---|---|
| 量测 | Gerber / Excellon 反解 | 焊盘尺寸、跨距、间距错误 |
| 规则 | `kicad-cli pcb drc` | 丝印压阻焊、外框缺失或过小、间距不足 |
| 实物 | `kicad-cli pcb render` + STEP 模型 | 图纸数字与实际器件不符 |
| 目视 | SVG 转 PNG 后人工查看 | 布局意图类错误（唯一覆盖此类的通路） |
| 契约 | 焊盘号与符号引脚号交叉 | 两侧编号不一致 |

符号校验的通路为：绘制规范（纯几何）、`sch export netlist`、`sch erc`、与手册
Table 5-1 三方比对、渲染目视。其中 ERC 是唯一能发现电气类型错误的手段
（类型写错不影响任何几何量测）。

验收要求：未查看渲染图不得声称"已验证"。查看清单见
[`shared/render-and-look.md`](shared/render-and-look.md)。

## 环境要求

| 依赖 | 用途 | 获取方式 |
|---|---|---|
| Python ≥ 3.9 | 全部脚本 | — |
| `kicad-cli` ≥ 8 | Gerber/网表导出、DRC/ERC、3D 渲染 | 随 KiCad 安装 |
| Chromium 内核浏览器 | SVG 转 PNG（无头模式） | Chrome 或 Edge |
| `pdfplumber` | 从手册 PDF 抽取引脚表 | `pip install -r requirements.txt` |
| `openpyxl` | 尺寸测量表输出 | 同上 |
| `Pillow` | 测量图绘制与拼接 | 同上 |

工具路径自动探测，可用 `KICAD_CLI` / `CHROME` 覆盖。设置的值无效时脚本直接报错，
不回退到自动探测。

```bash
python shared/toolchain.py        # 打印探测到的路径
```

## 安装

pi：

```bash
pi install /path/to/kicad-hardware-toolkit
```

其他 Agent Skills 实现（Claude Code / Codex 等）—— 三个 skill 使用标准目录结构，
指向即可：

```bash
ln -s /path/to/kicad-hardware-toolkit/skills/kicad-create-lib-part ~/.claude/skills/
```

不使用 agent 时可直接调用脚本，见下节。

## 使用

### 1. 编写 spec

以 `skills/kicad-create-lib-part/assets/part_spec_template.json` 为模板。
三个部分需要人工从图纸录入：

- `pins[]` —— 手册 Pin Functions 表的引脚号、名称、电气类型、所在边
- `groups{}` —— 功能分组（手册不提供此信息，须人工判断）
- `package{}` —— 手册 Package Outline 与 Example Board Layout 的尺寸

组内顺序由 `pins[]` 的书写顺序决定，组间顺序由 `groups{}` 决定。

### 2. 生成

```bash
TK=/path/to/kicad-hardware-toolkit
python $TK/skills/kicad-create-lib-part/scripts/make_part.py spec.json --outdir out --verify
```

`--verify` 调用 `kicad-cli` 确认产物可加载，并打印后续校验命令。

### 3. 校验

```bash
python $TK/skills/kicad-check-sch-component/scripts/check_symbol.py \
       out/LIB/LIB.kicad_sym --outdir chk_sch \
       --pdf datasheet.pdf --groups out/LIB.groups.json \
       --footprint out/LIB.pretty/FP.kicad_mod

python $TK/skills/kicad-check-pcb-component/scripts/check_footprint.py \
       out/LIB.pretty/FP.kicad_mod --outdir chk_pcb \
       --spec out/LIB.fp.spec.json --symbol out/LIB/LIB.kicad_sym
```

两个命令各产出一份 `EVIDENCE.md`（逐项结论与数据来源）和 `look_sheet.png`
（所有图拼成一张）。审阅 `look_sheet.png`、`fit3d_*.png` 后再下结论。

## 退出码

| 脚本 | 0 | 非 0 |
|---|---|---|
| `make_part.py` | 成功 | 2 输入/参数错误；3 产物无法被 kicad-cli 加载 |
| `check_footprint.py` | 全部通过 | 6 存在 NG 项 |
| `check_symbol.py` | 全部通过 | 9 存在 NG 项 |
| `measure_component.py` | 无 NG 行 | 1 有 NG 行；2 找不到 Gerber |
| `drc.py` | 无相关违规 | 6 存在违规 |
| `fit3d.py` | 已渲染 3D 贴合图 | 7 STEP 模型缺失（不渲染） |
| `pinmap.py` | 编号一一对应 | 8 不一致 |
| `symbol_lint.py` | 无 FAIL | 1 存在 FAIL |
| `pdf_pins.py` | 引脚类型全已映射 | 1 存在未映射项 |
| `selftest.py`（三个） | 全部通过 | 1 存在失败用例 |

所有入口的未捕获异常统一由 `shared/cli.py` 转换为可读信息并返回 2；
加 `--traceback` 或设 `TOOLKIT_TRACEBACK=1` 可取完整堆栈。

## 设计约定

以下三条影响使用方式，非实现细节。

**分组必须显式声明。** "组间 400 mil" 这条规则在几何上不可判定：左侧
`VBUS(12.7) ─400mil─ IN+(2.54) ─200mil─ IN−(−2.54)` 中将 `IN+` 移至 7.62，
即变为 `200mil / 400mil`，两种排列都满足"组内 200 / 组间 400"，仅分组不同。
因此分组是输入而非推断结果。未提供分组时脚本会明确报告该规则未校验，
并把几何推断出的分组与声明的分组对账，不一致即判 FAIL。

**间距取值须为 100 mil 的整数倍。** 顶对齐时首个引脚的 y 坐标为最大跨度的一半。
`pitch_mil=150` / `group_gap_mil=300` 会产生 9.525 mm，不在 50 mil 连接栅格上，
导致引脚无法连线，而符号外观完全正常。`make_part.py` 生成后会自检并告警。

**判定同时依据返回码与输出文本。** `kicad-cli pcb drc` 在存在违规时返回码可能为 0
（除非加 `--exit-code-violations`）；加载失败返回 2，成功时输出"未更新"。
仅凭返回码会漏判。

## 目录结构

```
kicad-hardware-toolkit/
├── package.json                    pi 包清单
├── requirements.txt
├── .github/workflows/test.yml      CI
├── shared/                         两个校验 skill 共用，各一份
│   ├── toolchain.py                外部工具定位
│   ├── cli.py                      入口错误处理与退出码
│   ├── render.py                   渲染：SVG→PNG、3D
│   ├── pinmap.py                   引脚↔焊盘契约
│   └── render-and-look.md          目视清单与结论措辞规范
└── skills/
    ├── kicad-create-lib-part/
    │   ├── SKILL.md
    │   ├── assets/part_spec_template.json
    │   ├── references/             symbol-rules, footprint-rules,
    │   │                           kicad-formats, datasheet-extract
    │   └── scripts/                make_part.py, kicad_io.py, selftest.py
    ├── kicad-check-sch-component/
    │   ├── SKILL.md
    │   ├── assets/groups_template.json
    │   ├── references/checks.md
    │   └── scripts/                check_symbol, symbol_lint, sch_build,
    │                               sch_netlist, sch_erc, pdf_pins, selftest
    └── kicad-check-pcb-component/
        ├── SKILL.md
        ├── references/families.md
        └── scripts/
            ├── check_footprint, measure_component, drc, fit3d,
            │   selftest, build_testboards, crop_spec_table
            ├── core/               gerber, geometry, spec, draw,
            │                       common, report, board
            └── families/           grid_array, peripheral, chip
```

`core/` 与封装类型无关；`families/` 按封装族划分（网格阵列 / 边引脚 / 两端子），
定位问题时据此缩小范围。

## 测试

三个 selftest 不需要 CAD 文件与网络。量测用例仅依赖标准库；
绘制相关用例需要 Pillow，缺失时标记为跳过而非通过。

```bash
python skills/kicad-check-pcb-component/scripts/selftest.py    # 75 项
python skills/kicad-check-sch-component/scripts/selftest.py    # 24 项
python skills/kicad-create-lib-part/scripts/selftest.py        # 50 项
```

针对真实 KiCad 库封装的集成测试（需要 KiCad）：

```bash
python skills/kicad-check-pcb-component/scripts/build_testboards.py /tmp/tb --run --3d
```

CI 在每次 push 时运行上述三个 selftest、pyflakes，以及 SKILL.md frontmatter 校验
（名称须为小写连字符且与目录名一致 —— 这是 Agent Skills 标准的要求，pi 本身较宽松）。

## 已知限制

- **无实物验证。** 最终确认需要把器件焊到板上。
- **图纸录入错误无法检出。** 量测只能证明"封装与 spec 一致"。若 spec 中的数值录入
  有误，所有校验会一致通过。spec 中每个数值均注明来源图纸标注，是唯一的缓解手段。
- **符号的跨引脚电气冲突未检查。** 当前为"孤立符号 ERC + 电气类型提取"，
  检出短路类问题需要构造测试台原理图。
- **四边封装（QFP / QFN）** 已有 selftest 覆盖，但尚未用真实厂家图纸走完整流程。
- **Z 向尺寸不可测。** 2D Gerber 无该方向几何，一律报"待测"。
- **封装族覆盖不均。** 边引脚与两端子族经真实数据回归；网格阵列族仅有合成测试。

## 许可

MIT
