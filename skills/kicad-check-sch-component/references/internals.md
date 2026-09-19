# 内部结构与排错（维护用）

> 这份是给**改这套代码的人**看的。**拿它校验符号不需要读这里** ——
> 用法看 [../SKILL.md](../SKILL.md)。

## 目录结构

```
skills/kicad-check-sch-component/
├── scripts/
│   ├── check_symbol.py   ★ 总入口：跑完 A~E，出证据包
│   ├── symbol_lint.py    A：规矩检查（纯几何，无需 KiCad，可单独用）
│   ├── sch_build.py      把 .kicad_sym 塞进最小 .kicad_sch
│   ├── sch_netlist.py    B：导网表 + 解析 KiCad 眼中的引脚
│   ├── sch_erc.py        C：跑 ERC + 提取电气类型 + 分类违规
│   ├── pdf_pins.py       从手册 PDF 抽引脚表（含 open-drain 识别）
│   ├── pin_report.py     引脚比对表 xlsx（由 cmp_pins.json 生成）
│   └── selftest.py       41 项黄金测试（纯标准库，不需要 CAD）
├── references/
│   ├── checks.md         每一项检查的数据来源、判定、以及做不到的部分
│   └── internals.md      （本文）
└── assets/
    └── groups_template.json  分组声明模板（把这个填了再跑）

<toolkit>/shared/          ← 三个 skill 共用；**不在本 skill 的 scripts/ 下**
├── render.py             D：kicad-cli 出 SVG -> headless chrome 出 PNG
├── pinmap.py             E：引脚↔焊盘编号契约
├── kitext.py             KiCad 文本渲染常数（与 make_part.py 共用）
├── cli.py / toolchain.py 统一异常包装、找 kicad-cli
└── render-and-look.md    出图命令 + 踩过的坑 + 看图清单
```

> **常见踩坑：** `render.py` / `pinmap.py` 不在本 skill 的 `scripts/` 里。
> 直接 `python scripts/render.py` 会 `No such file or directory`。
> 从本目录用 `../../shared/render.py`。

## 出问题先看哪

| 症状 | 文件 |
|---|---|
| 间距/分组判断不对 | `symbol_lint.py` 的 `side_of()` / `root_of()`（rot 的含义容易搞反） |
| **四角文字交叠漏报** | `symbol_lint.py` 的 overflow 角落段 + `shared/kitext.py` 的常数 |
| 网表里少引脚 / 名字不对 | `sch_build.py`（符号块切分）+ 检查 `.kicad_sym` 本身 |
| ERC 报一堆 `endpoint_off_grid` | `sch_build.py` 的放置点没吸附到 1.27 mm 栅格 |
| ERC 该报的没报 | `sch_erc.py` 的 `EXPECTED` 集合把真信号滤掉了 |
| 手册引脚抽不出来 | `pdf_pins.py` 的 `ROW` 正则 / `--page-table` 标记；或改用 `--pins-json` |
| "类型未声明"大面积出现 | `pin_report.verdict()` 的三态判定 —— 要求表没有 etype 列时的正常结果 |
| 图出不来 | `shared/render.py`（见 `shared/render-and-look.md` 的坑表） |

改了任何东西之后跑：

```bash
python scripts/selftest.py        # 41 项，纯标准库
```

## 共享代码

`toolchain.py`（找 kicad-cli/chrome）、`cli.py`（统一错误处理）、
`render.py`、`pinmap.py`、`kitext.py`、`render-and-look.md` 都在
**`<toolkit>/shared/`**，只有一份。skill 通过 `../../shared/` 引用
（脚本里由 `SHARED` 常量解析）。

**改一处就够，不存在漂移。**

> `kitext.py` 装的是 KiCad 文本渲染常数（字符宽 / 名字离边距离 / 竖排半宽）。
> 生成侧 `make_part.py` 用它算本体得多宽，本 skill 的 `symbol_lint` 用它反推
> 两截文字会不会撞。**两边必须同组** —— 各写一份的话，生成侧按一套值留了空间、
> 校验侧按另一套值判断，明明压着字却报"无疑问项"。

## 本地验证时的一个坑：`__pycache__`

做"改坏一处、看测试会不会红"这种验证时，如果改动是**等长替换**，文件大小不变；
一旦 mtime 落在同一时间刻度上，Python 会**复用旧字节码** —— 你会看到"改了却没
生效"，很容易误判成"测试没用"。先清：

```bash
find . -name __pycache__ -type d -exec rm -rf {} +
```

CI 每次都是全新 checkout，不受影响。
