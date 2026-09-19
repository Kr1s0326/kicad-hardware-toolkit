# 内部结构与排错（维护用）

> 这份是给**改这套代码的人**看的。**拿它生成元件不需要读这里** ——
> 用法看 [../SKILL.md](../SKILL.md)。

## 目录结构

```
skills/kicad-create-lib-part/
├── scripts/
│   ├── make_part.py    ★ 总入口：spec -> 符号 + 封装 + groups.json + fp.spec.json
│   ├── kicad_io.py     格式版本探测（probe）与可加载性检查（loadable）
│   └── selftest.py     102 项黄金测试（纯标准库，不需要 CAD）
├── references/         symbol-rules / footprint-rules / kicad-formats /
│                       datasheet-extract / internals（本文）
└── assets/part_spec_template.json

<toolkit>/shared/        ← 三个 skill 共用；**不在本 skill 的 scripts/ 下**
└── kitext.py           KiCad 文本渲染常数（与 symbol_lint.py 共用）
```

本 skill 里没有一行渲染代码。那是有意的 —— 见 SKILL.md「本 skill 的边界」。

### `kitext.py` 必须两边共用

它装的是 KiCad 文本渲染常数：字符宽比、引脚名离本体边缘的偏移、竖排文字半宽。

* `make_part.layout_symbol()` 用它算**本体得多宽**四角才不压字；
* `symbol_lint` 的 `overflow` 用它反推**两截文字会不会撞**。

**两边必须同一组值。** 各写一份的话，生成侧按一套值留了空间、校验侧按另一套值
判断，检查就静默失效：明明压着字，却报"无疑问项"。（CY8C6245 上就这么漏掉的：
lint 漏算"名字离本体边缘 0.85mm"这个偏移，估出的名字短了 0.67mm，
刚好躲过判定。）

## 出问题先看哪

| 症状 | 文件 |
|---|---|
| 引脚分组/间距不对 | `make_part.py` 的 `layout_symbol()` / `sequence()` |
| 焊盘编号方向反了 | `layout_pads()`（+Y 向下的坑） |
| 焊盘位置不对（球阵族） | `layout_ball_grid()`（从 JEDEC 球名解行列） |
| 丝印压到焊盘上 | `silk_lines()` 的 `COPPER_CLR` / `SILK_W` |
| 外框形状不对 | `courtyard_cross()`（**不能**因为 PY<by 就把 PY 拉平） |
| 外框余量不对（球阵族） | `emit_footprint()` 的 `cmargin`（球阵是 1.0，不是 `CRTYD`） |
| 生成的符号/封装 KiCad 加载不了 | `emit_symbol()` / `emit_footprint()` 的 token |
| 四角的引脚名叠在一起 | `layout_symbol()` 的四角净空段（常数在 `shared/kitext.py`）|
| 引脚全掉在 50 mil 栅格外 | `layout_symbol()` 末尾的 `hw` 向上取整 |
| 版本号不对 | `kicad_io.probe_formats()` |

改了任何东西之后跑：

```bash
python scripts/selftest.py        # 102 项，纯标准库
```

## 本地反向验证时的一个坑：`__pycache__`

做"改坏一处、看测试会不会红"这种验证时，如果改动是**等长替换**，文件大小不变；
一旦 mtime 落在同一时间刻度上，Python 会**复用旧字节码** —— 你会看到"改了却没
生效"，很容易误判成"测试没用"。先清：

```bash
find . -name __pycache__ -type d -exec rm -rf {} +
```

CI 每次都是全新 checkout，不受影响。
