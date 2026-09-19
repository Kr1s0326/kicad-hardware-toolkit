# kicad-hardware-toolkit

从**一份数据手册**生成 KiCad 元件库，并且**独立地证明它是对的**。

```
数据手册 PDF ──┐
              ├─> kicad-create-lib-part ──> .kicad_sym + .kicad_mod
封装图纸 ─────┘                              │
                                            ├─> kicad-check-sch-component  (符号校验)
                                            └─> kicad-check-pcb-component  (封装校验)
```

## 三个 skill

| skill | 干什么 | 边界 |
|---|---|---|
| `kicad-create-lib-part` | 从 `part_spec.json` 生成原理图符号 + PCB 封装 + 两者的链接 | **只保证"能被 KiCad 加载"** |
| `kicad-check-sch-component` | 校验符号：规矩 / 网表 / ERC / 手册比对 / 目视 / 引脚契约 | **只做正确性判断** |
| `kicad-check-pcb-component` | 校验封装：Gerber 量测 / DRC / 3D 实物贴合 / 目视 / 引脚契约 | **只做正确性判断** |

### 为什么创建和校验是分开的

> **创建保证"可加载"，校验保证"正确"。**

如果创建侧也开始"看完图觉得没问题"，校验就退化成**自证** —— 拿自己刚写的东西
验自己。这是正确性风险，不是臃肿问题。所以 `kicad-create-lib-part` 里
**一行渲染代码都没有**。

### 为什么校验是四条通路

**任何一条单独跑，都发现不了另外几条能发现的问题。**

| 通路 | 手段 | 抓什么 | 抓不到 |
|---|---|---|---|
| A 量测 | Gerber/Excellon 反解 | 焊盘/跨距/间距 的数字错 | 丝印压盘、Pin1 画反、引脚悬空 |
| B 规则 | `pcb drc` / `sch erc` | 丝印压阻焊、外框缺失、**引脚电气类型错** | 数字错（规则不知道图纸） |
| C 3D/网表 | 真实 STEP 压焊盘 / KiCad 自己导的网表 | 图纸数字不是这颗实物 / 结构错 | 布局意图 |
| D 目视 | kicad-cli 出 SVG → PNG，人看 | **布局意图类错误**（唯一能覆盖这类的手段） | 需要人 |
| E 契约 | 引脚号 ↔ 焊盘号 交叉 | 两边编号对不上 | 名字、类型 |

**铁律：没看过图，不许说"已验证"。** 详见 [`shared/render-and-look.md`](shared/render-and-look.md)。

## 安装

### 作为 pi 包

```bash
pi install /path/to/kicad-hardware-toolkit
```

### 给其他 Agent Skills 实现（Claude Code / Codex / …）

三个 skill 就是标准的 Agent Skills 目录结构（`SKILL.md` + `scripts/` + `references/`），
直接挂到对应 harness 的 skills 目录即可：

```bash
ln -s /path/to/kicad-hardware-toolkit/skills/kicad-create-lib-part   ~/.claude/skills/
```

### 不用 AI，终端直接跑

脚本是普通 Python，只依赖 `kicad-cli`：

```bash
pip install -r requirements.txt

python shared/render.py which          # 检查 kicad-cli / chrome 是否找得到
python skills/kicad-create-lib-part/scripts/make_part.py spec.json --outdir out --verify
```

## 依赖

| 依赖 | 用途 | 备注 |
|---|---|---|
| `kicad-cli` | Gerber/网表导出、DRC/ERC、3D 渲染 | 随 KiCad 安装 |
| chrome / edge | 无头模式把 SVG 转 PNG | 渲染目视那一步需要 |
| `pdfplumber` | 从手册 PDF 抽引脚表 | |
| `openpyxl` | 尺寸测量表 xlsx | |
| `Pillow` | 渲染图拼接 | |

路径可用环境变量覆盖：`KICAD_CLI=...`、`CHROME=...`。

## 一次完整流程

```bash
TK=/path/to/kicad-hardware-toolkit

# 1) 写 spec（引脚表来自手册，封装数字来自封装图）
cp $TK/skills/kicad-create-lib-part/assets/part_spec_template.json my_part.json
#    填 pins[] / groups{} / package{}

# 2) 生成
python $TK/skills/kicad-create-lib-part/scripts/make_part.py my_part.json --outdir out --verify
#    产出 symbol + footprint + groups.json + fp.spec.json

# 3) 校验（两条命令，--verify 会直接打出来）
python $TK/skills/kicad-check-sch-component/scripts/check_symbol.py \
       out/LIB/LIB.kicad_sym --outdir chk --pdf ds.pdf \
       --groups out/LIB.groups.json --footprint out/LIB.pretty/FP.kicad_mod

python $TK/skills/kicad-check-pcb-component/scripts/check_footprint.py \
       out/LIB.pretty/FP.kicad_mod --outdir chk2 --spec out/LIB.fp.spec.json \
       --symbol out/LIB/LIB.kicad_sym

# 4) 看图（必做）
#    chk/look_sheet.png   chk2/look_sheet.png   chk2/fit3d_*.png
```

## 这个工具箱里最该知道的三件事

### 1. 「组间 400 mil」几何上不可判

左 `VBUS(12.7) ─400mil─ IN+(2.54) ─200mil─ IN−(−2.54)`
把 `IN+` 提到 7.62 就变成 `200mil / 400mil` —— 两种**都满足**"组内 200 / 组间 400"。

所以**分组是输入**（`groups.json`），不是推断出来的。不给分组时工具会明确说
"该规则无法真正校验"，而不是假装通过。给了之后还会把**几何推断的分组**和
**声明的分组**对账。

### 2. 创建的产物必须交出"要求"

`make_part.py` 会顺手生成 `groups.json` 和 `fp.spec.json` 交给校验侧。
但校验侧的量测**从不读这两个文件里的实测值** —— 它反解 Gerber / 网表。
这样"要求"和"实测"始终是两个独立来源。

### 3. `kicad-cli` 的判定不能只看返回码

- `pcb drc` 有违规时返回码也可能是 0（除非加 `--exit-code-violations`）
- 加载失败返回 2，但成功时的消息是"未更新"

所以判定一律同时看 rc **和**消息文本。

## 目录

```
kicad-hardware-toolkit/
├── package.json                    pi 包清单
├── requirements.txt
├── .github/workflows/test.yml      CI：3 个 selftest + pyflakes + frontmatter 校验
├── shared/                         共享代码，只有一份
│   ├── toolchain.py                外部工具定位（kicad-cli / chrome / KiCad share）
│   ├── cli.py                      入口统一错误处理 + 退出码
│   ├── render.py                   渲染：SVG→PNG / 3D
│   ├── pinmap.py                   引脚↔焊盘 契约
│   └── render-and-look.md          看图清单与结论措辞规范
└── skills/
    ├── kicad-create-lib-part/
    │   ├── SKILL.md
    │   ├── assets/part_spec_template.json
    │   ├── references/{symbol-rules,footprint-rules,kicad-formats,datasheet-extract}.md
    │   └── scripts/{make_part,kicad_io}.py
    ├── kicad-check-sch-component/
    │   ├── SKILL.md
    │   ├── assets/groups_template.json
    │   ├── references/checks.md
    │   └── scripts/{check_symbol,symbol_lint,sch_build,sch_netlist,sch_erc,pdf_pins}.py
    └── kicad-check-pcb-component/
        ├── SKILL.md
        ├── references/families.md
        └── scripts/{check_footprint,measure_component,drc,fit3d,
                     selftest,build_testboards,crop_spec_table}.py
            └── core/{gerber,geometry,spec,draw,common,report,board}.py
            └── families/{grid_array,peripheral,chip}.py
```

## 自测

三个 selftest 都**不需要 CAD、秒级**；量测用例纯标准库，
只有"画图不能崩"那部分要 Pillow（没装就标成跳过，不假装通过）：

```bash
python skills/kicad-check-pcb-component/scripts/selftest.py    # 75 项
python skills/kicad-check-sch-component/scripts/selftest.py    # 22 项
python skills/kicad-create-lib-part/scripts/selftest.py        # 50 项
python skills/kicad-check-pcb-component/scripts/build_testboards.py --run --3d  # 真实库集成测试
```

CI 每次 push 自动跑这三个 + pyflakes + skill frontmatter 校验。

## 已知边界（做不到的）

* **没有实物验证。** 最终确认还是要把芯片真贴上去。
* **图纸抄录风险仍在。** 量测只能证明"封装 = spec"。spec 里录错了，量测会一致地 PASS。
  所以 spec 里每个数字旁边都注明来自哪张图的哪个标注。
* **符号的跨引脚电气冲突**（两个输出短接这类）还没查 —— 现在是"孤立符号 ERC +
  电气类型提取"，要做需要搭测试台原理图。
* **四边封装（QFP/QFN）已有 selftest 覆盖**，但还没有拿真实厂家的 QFP 图纸
  （而不只是合成参数）走一遍完整流程。
* **Z 向尺寸测不了**（2D Gerber 没有该方向几何），一律报"待测"。

## License

MIT
