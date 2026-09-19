# 内部结构与排错（维护用）

> 这份是给**改这套代码的人**看的。**拿它跑封装校验不需要读这里** ——
> 用法看 [../SKILL.md](../SKILL.md)。

## 目录结构

```
skills/kicad-check-pcb-component/scripts/
├── core/                    ← 与封装无关，很少改动
│   ├── gerber.py            RS-274X + Excellon 解析（含 RoundRect 宏光圈）
│   ├── geometry.py          焊盘分组 / grid、peripheral、chip 归类
│   ├── spec.py              spec.json -> context；min_pad_gap() / write_dru()
│   ├── draw.py              字体、视图、尺寸线、焊盘绘制
│   ├── common.py            与族无关的 kind（body/board/hole/stencil/na）
│   ├── report.py            xlsx + 图例页 + PNG 预览
│   └── board.py             把 .kicad_mod 内联成最小 .kicad_pcb
├── families/                ← 一个封装家族一个文件
│   ├── grid_array.py        WLCSP / CSP / BGA / LGA
│   ├── peripheral.py        QFN / QFP / SOIC / TSSOP / SOT-23 / DPAK
│   └── chip.py              0402 / 0603 / 0805 / SOD-123 / MELF
├── check_footprint.py       ★ 总入口：跑完 A~E，出证据包
├── measure_component.py     ★ 只出尺寸测量表（可单独用）
├── drc.py                   B：DRC 包装 + 违规解析分类
├── fit3d.py                 C：3D 实物贴合
├── selftest.py              各族黄金测试（129 项），不需要 CAD 文件
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

## 出问题先看哪

| 症状 | 文件 |
|---|---|
| 每个封装坐标/尺寸都不对 | `core/gerber.py`（解析） |
| 某个封装被归错族（QFN 判成 grid 等） | `core/geometry.classify_pads()` 的 `_inside_ep` |
| 本体外框 / 板框 / 孔数不对 | `core/spec.py`、`core/common.py` |
| 只有一个族量得离谱 | 那个 `families/*.py` |
| Excel 版式 / 判定颜色 / 预览 | `core/report.py` |
| 某一族的图画得不对 | 那个 `families/*.py` 的 `panel()` |
| DRC 报一堆假"间距违规" | `core/spec.py` 的 `min_pad_gap()` / `write_dru()` |
| 板子跑不了 DRC / 3D | `core/board.py` |
| 图出不来 / 出来是空白 | `shared/render.py` |
| 改了代码但行为没变 | **先删 `__pycache__`**（见下） |

改了任何东西之后跑：

```bash
python scripts/selftest.py                    # 129 项，不需要 CAD
python scripts/build_testboards.py --run      # 真实 QFN/QFP/SOT-23/SOIC-8/0603 Gerber
```

## 共享代码

`toolchain.py`（找 kicad-cli/chrome）、`cli.py`（统一错误处理）、
`render.py`、`pinmap.py`、`kitext.py`、`render-and-look.md` 都在
**`<toolkit>/shared/`**，只有一份。skill 通过 `../../shared/` 引用 （脚本里由
`SHARED` 常量解析）。改一处就够，不存在漂移。

> `kitext.py` 装的是 KiCad 文本渲染常数（字符宽 / 名字离边距离 / 竖排半宽）。
> 生成侧 `make_part.py` 用它算本体得多宽，校验侧 `symbol_lint.py` 用它反推
> 两截文字会不会撞。**两边必须同组** —— 各写一份的话，生成侧按一套值留了空间、
> 校验侧按另一套值判断，明明压着字却报"无疑问项"。

## 本地验证时的一个坑：`__pycache__`

做"改坏一处、看测试会不会红"这种反向验证时，如果改动是**等长替换** （例如把
`dim_h` 和 `dim_v` 对调），文件大小不变；一旦 mtime 落在同一时间 刻度上，
Python 会**复用旧字节码** —— 你会看到"改了却没生效"，很容易误判成 "测试没用"。
本仓库已经因此误判过一次。本地做反向验证前先清：

```bash
find . -name __pycache__ -type d -exec rm -rf {} +
```

CI 每次都是全新 checkout，不受影响。

## 文件名里的两个坑（Windows）给逐行 `req_image` 起名时，**不要只用符号名**。

### 大小写不敏感

Windows 上 `ref_E.png` 与 `ref_e.png` 是**同一个文件**。用符号名命名时：

```
ref_D.png   ref_E.png   ref_D2.png   ref_E2.png   ref_e.png   ref_b.png   ref_L.png
                              └─────────── 这两个是同一个文件 ───────────┘
```

后写的那张会把先写的**静默覆盖**。真实后果：ESP32-S3 的尺寸表里 "E 本体高"
那一行的「要求:图片」显示成了手册的 "e 0.400 BSC" 行 ——
两个不同的尺寸项配错了图，而所有数值与判定都是对的。

**用行序号前缀**：`req_%02d_%s.png`（`req_02_E.png` / `req_03_e.png`）。
这也正是 `measure_component.py` 归一化时用的命名，天然不会撞。

### 逐张读回校验会掩盖覆盖

循环里每写一张就立刻读回来对 md5，**当时那一刻是对的** ——
覆盖发生在后面那一次写入。所以要么最后统一校验一遍（并断言 **所有文件的 md5
互不相同**），要么直接把 md5 集合去重后比大小。
