# KiCad s-expression 格式坑

## 版本号不要凭记忆写

```bash
python scripts/kicad_io.py probe
# sym_version          20251024
# fp_version           20260206
# kicad_cli            C:\Program Files\KiCad\10.0\bin\kicad-cli.exe
```

（第三行是本机 kicad-cli 的实际路径，随安装位置变。`probe_formats()` 字典里
另有 `generator_version` 键，但 `probe` 子命令**不打印它** —— 文档这里曾经写成
会打印，别被误导。）

`probe_formats()` 去读**本机官方库文件**的 `(version …)` token，不靠记忆。
换 KiCad 版本后重跑一次就行。

## 加载失败 ≠ 非零返回码

```bash
kicad-cli sym upgrade good.kicad_sym   # rc=0  符号库未更新
kicad-cli sym upgrade bad.kicad_sym    # rc=2  无法加载库
```

`kicad_io.loadable()` 同时看 rc 和消息文本。**只看 rc 不够**，也不够健壮。

顺带一个真实教训：`cmd | head -1; echo $?` 里的 `$?` 是 `head` 的返回值，
不是 `cmd` 的。加管道要 `PIPESTATUS`。

## 符号库结构

```
(kicad_symbol_lib
  (version …) (generator "kicad_symbol_editor") (generator_version "…")
  (symbol "INA239"                        <- 父块：只放属性和子块
    (exclude_from_sim no) (in_bom yes) (on_board yes)
    (in_pos_files yes)
    (duplicate_pin_numbers_are_jumpers no)
    (property "Reference" "U" …)
    (symbol "INA239_0_1"  (rectangle …))  <- 子块：图形
    (symbol "INA239_1_1"  (pin …) …)      <- 子块：引脚
    (embedded_fonts no)
  )
)
```

**引脚必须放在子块 `NAME_1_1` 里面。** 放进父块里 KiCad 不会报错，
但网表里会少引脚 —— 这是 `sch_netlist.py` 能抓到、纯文本检查抓不到的错。

拷进原理图时（`sch_build.py`）：父符号带上库前缀 `LIB:NAME`，
**子块保持裸名** `NAME_0_1`。和 KiCad 自己写出来的一致。

## 封装结构

```
(footprint "NAME"
  (version …) (generator "kicad-footprint-generator")
  (layer "F.Cu") (descr "…") (tags "…")
  (property "Reference" "REF**" (at 0 -2.45 0) (layer "F.SilkS") …)
  (property "Value" … (layer "F.Fab") …)
  (attr smd)
  (duplicate_pad_numbers_are_jumpers no)
  … fp_line / fp_poly / fp_text …
  (pad "1" smd roundrect (at …) (size …) (layers "F.Cu" "F.Mask" "F.Paste")
       (roundrect_rratio 0.166667))
  (embedded_fonts no)
  (model "…" (offset (xyz 0 0 0)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))
)
```

## 内联成板子时要删的 token

`.kicad_mod` 里合法的这些东西，放进 `.kicad_pcb` 的 footprint 块里是非法的：

```
\t(version …)          \t(generator …)
\t(generator_version …) \t(embedded_fonts no)
```

删掉，再补上 `(at x y)` + `(uuid …)`。见 `kicad-check-pcb-component/scripts/core/board.py`。

## 要板子才能做的事

`kicad-cli` 对 `.kicad_mod` **只能导 SVG**。Gerber 导出、DRC、3D 渲染都要求
`.kicad_pcb`。所以校验封装必须先内联成一块最小板子。

## 原理图里放符号的栅格

放符号的位置**必须落在连接栅格上**（默认 1.27 mm = 50 mil）。
放在 `(100, 100)` 时 100/1.27 = 78.74，不在栅格上，ERC 会对**每个引脚**报
`endpoint_off_grid` —— 那是尺子的问题，不是符号的问题。`sch_build.py`
现在会自动吸附到 1.27 mm。

## Windows 控制台与字符

控制台多是 GBK。输出里出现 GBK 以外的字符（`↔`、`✅`）会直接抛
`UnicodeEncodeError` 打断整个检查。两条都做：

1. 打印内容保持 GBK 安全（用 `<->` 不用 `↔`）；
2. `sys.stdout.reconfigure(errors="replace")` 兜底。
