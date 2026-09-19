# 渲染 + 目视 规范（render-and-look）

> 本文件在 `<toolkit>/shared/`，**只有一份**，被两个校验 skill 共同引用。
> 路径：`<toolkit>/shared/render-and-look.md`

---

## 1. 数值校验的盲区

数值校验有一个系统性盲区：它只检查你让它检查的东西。

| 已发生的真实例子 | 数值校验的反应 |
|---|---|
| Pin1 标记画在右上角而不是左上角 | 全 PASS（编号全对） |
| 丝印框压在阻焊开窗上 | 全 PASS（尺寸全对） |
| 四角的引脚名互相重叠成一团 | 全 PASS（间距"合规"：29 项尺寸、网表、ERC 全绿）|
| 3D 里引脚悬空、没落到焊盘上 | 全 PASS（焊盘数字来自图纸，图纸是对的） |
| 测量图画的是"另外两个焊盘" | 全 PASS（数字算对了，只是标在了错的东西上） |

> 反例也是例子：什么看图片看不出来。
> **电气类型看不出来。** 把 `MISO` 标成 `input`，渲染图和间距/栅格/本体
> 全部正常；反过来把电源脚标成 `input`，图上也只是个普通线段。
> 引脚**图形样式**（line / inverted / clock）确实画得出来，但那是
> `(pin <etype> <style>)` 里的第二个 token，与电气类型无关。
> 看图无法验证电气类型，那是 ERC 的职责。

**这些只能看出来。** 渲染 + 目视是唯一能覆盖这类错误的手段，所以它是校验流程里
**不可省略**的一步，不是"有空再看"。

---

## 2. 铁律

1. 没看过图，不许说"已验证"。报告里只能写"数值项 PASS，目视项待确认"。措辞上要区分：`数值 PASS` ≠ `已验证`。
2. **看图要用工具真的把 PNG 读进来**（能看图的模型直接读；不能看的就把图交给用户，并明确写出"需要你确认这几张"）。
3. 图必须来自 KiCad 的输出，不能来自我手写的文本。
   SVG/PNG 由 kicad-cli 生成 —— 那是另一套实现重新算一遍坐标，
   等于换了个引擎复核。自己画的示意图不算证据。
4. **一张图一个结论。** 看图时逐条对照下面的清单，不要"看着还行"就过。

---

## 3. 出图命令

全部走 `render.py`，不要手搓命令。

> 路径容易写错：`render.py` 在 `<toolkit>/shared/` 下，不在任何 skill 的
> `scripts/` 里。从某个 skill 的目录里跑，相对路径是 `../../shared/render.py`；
> 下面用 `RENDER` 代表它的完整路径，以免歧义。
>
> （一般用不着手跑 —— `check_footprint.py` / `check_symbol.py` 自己会调它
> 把图出到 `outdir/`。这里列出来是为了单独重画某一张时用。）

```bash
RENDER=<toolkit>/shared/render.py

# 2D：封装（丝印/铜/装配/外框）
python $RENDER fp  <lib.pretty>      <outdir> --layers F.Cu,F.SilkS,F.Fab,F.CrtYd,F.Mask

# 2D：符号
python $RENDER sym <lib.kicad_sym>   <outdir>

# 2D：整张原理图
python $RENDER sch <board.kicad_sch> <outdir>

# 3D：真实器件模型压焊盘（封装独有，最有价值的一张）
python $RENDER pcb <board.kicad_pcb> <out.png> --side top --zoom 2.2
python $RENDER pcb <board.kicad_pcb> <out_iso.png> --side top --zoom 1.8 --rotate "-35,0,25"

# SVG -> PNG（单独用时）
python $RENDER svg2png <in.svg> <out.png> --width 900
```

### 路径探测

```bash
python $RENDER which      # 打印找到的 kicad-cli 和 chrome
```
找不到时用环境变量覆盖：`KICAD_CLI=...`、`CHROME=...`。

### 已知陷阱（已处理，改动时勿破坏）

| 坑 | 现象 | 处理 |
|---|---|---|
| chrome 不认相对路径 | `Failed to write file: 拒绝访问 (0x5)` | `svg2png` 一律 `abspath()` 后再写 |
| `--perspective` 是不带值的开关 | `Maximum number of positional arguments exceeded` | 只在需要时加 `--perspective`，绝不写 `--perspective false` |
| GBK 控制台 | 输出含 `↔` / `✅` 时 `UnicodeEncodeError` 直接崩 | 打印内容保持 GBK 安全；并 `sys.stdout.reconfigure(errors="replace")` 兜底 |
| `pcb render` 必须给板子 | 传 `.kicad_mod` 报错 | 先用 `core/board.py` 把封装内联成 `.kicad_pcb` |
| 封装没挂模型 | 3D 图里零件缺失，但不报错 | `fit3d.py` 显式解析 `(model ...)`，解析不到就报，不静默出图 |

---

## 4. 看图清单

### 4.1 封装（PCB）

**A. 3D 贴合图**（`fit3d_top.png` / `fit3d_iso.png`）— 逐条问：

- [ ] 每一条引脚都**落在**焊盘上？有没有悬空 / 只搭到一半 / 压到隔壁焊盘？
- [ ] 引脚根部（heel）和端部（toe）的富余量看起来左右对称吗？
- [ ] Pin1 圆点（或倒角）是否对准丝印的 Pin1 三角标？
- [ ] 本体有没有明显超出丝印框（丝印应该在体外沿 +0.11 mm 左右）？
- [ ] 引脚数对不对？（数一遍，别信脚标）

**B. 2D 图**（`fp/<name>.png`）— 逐条问：

- [ ] 丝印框是闭合的？有没有断口？（有断口是故意的除外）
- [ ] 丝印线有没有压在任何焊盘上？
- [ ] Pin1 标记在正确的角上？朝向和 datasheet 的顶视图一致？
- [ ] 外框（courtyard）完整闭合、把**所有**焊盘都包住？
- [ ] 焊盘编号（若有显示）和 datasheet 顶视图一致？
- [ ] 位号 / Value 文字有没有压到焊盘或超出外框？

**C. 尺寸测量表的图**（`measure/meas_*.png`）— 抽查 2~3 张：

- [ ] 标红的那两个东西，确实是这一项要量的吗？（曾经出现"量了错的边"）
- [ ] 有无明显画错（比如 N 的计数图里少圈了焊盘）？

### 4.2 原理图符号

- [ ] 引脚**分组**对不对？同功能的是否挨在一起、不同功能之间是否留了大间隔？
- [ ] 间距是否符合你的规范（组内 200 mil / 组间 400 mil）？
- [ ] 电源 / 地引脚的位置合理吗？（VS 朝上、GND 朝下）
- [ ] 引脚**编号**和 datasheet 的 Figure（顶视图）一致？逐个数。
- [ ] 低有效信号（CS / ALERT）有没有**上划线**？
- [ ] 所有引脚名都完整可读？有没有互相压字、或超出本体框？
- [ ] 本体大小是否和引脚数相称？（引脚挤在本体边缘 = 间距不够）
- [ ] `Reference` / `Value` 是否和图元重叠？
- [ ] 引脚图形样式对么？（下划线 / 时钟、开漏等）—— 注意：电气类型
      （input / power_in / open_collector）在图上根本画不出来，本工具
      也一律用 `line`。看图无法验证电气类型，那是 ERC 的职责。
- [ ] 有没有多余的、不该存在的引脚？

---

## 5. 结论措辞

看图之后，报告里的措辞要能区分证据强度：

| 措辞 | 什么时候能这么写 |
|---|---|
| `数值 PASS（Gerber 反解，偏差 0.0000）` | 尺寸测量表跑过、无 NG |
| `DRC PASS（0 违规）` | DRC 跑过 |
| `目视确认：引脚落盘、Pin1 对齐、丝印干净` | **真的看过图之后** |
| `待目视` | 图出了但还没看 —— 不许写成"已验证" |

一条完整结论长得像这样：

> 封装 `VSSOP-10_3x3mm_P0.5mm`：
> 尺寸 10 项全 PASS（Gerber 反解，最大偏差 0.0000 mm）；
> DRC 0 违规；引脚编号 1..10 与符号一一对应；
> 3D 贴合图已目视：10 条引脚全部落到焊盘上，Pin1 圆点对准丝印三角。
> **未做的事**：Z 向高度（2D 无几何）、实物贴装验证。最后那句"未做的事"很重要 —— 校验报告的价值一半在它没说能保证什么。

---

## 6. 不要把这条通路和创建混在一起

渲染目视**只在校验 skill 里做**。

- 创建 skill 只保证"文件能被 KiCad 加载"，不看图、不做正确性判断。
- 一旦创建侧也开始"看完图觉得没问题"，它就变成了自证，独立性就没了。边界：创建保证"可加载"，校验保证"正确"。
