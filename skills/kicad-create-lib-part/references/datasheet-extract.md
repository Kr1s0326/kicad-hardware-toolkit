# 从数据手册抽引脚表和封装数字

## 引脚表（-> `pins[]`）

TI 手册的引脚表在 "Pin Configuration and Functions" 一节，标记是
**`Table 5-1`**。抽法：`kicad-check-sch-component/scripts/pdf_pins.py`。

```bash
python ../kicad-check-sch-component/scripts/pdf_pins.py ina239.pdf --dump
```

### 坑：目录页也会命中

`"Pin Configuration and Functions"` 这个词在**目录页**也出现。用
`next(p for p in pages if "Pin Configuration and Functions" in text)` 会拿到
目录页，抽到 0 个引脚。必须用更独特的标记（`Table 5-1`）定位。

### 坑：en-dash 不是 hyphen

手册里的 `IN–` 用的是 en-dash（U+2013），不是 `-`。提取后要
`.replace("\u2013", "-")`，否则和符号里的 `IN-` 对不上。

### 电气类型要看描述列

手册 TYPE 列只写 "Digital output"，而 ALERT 实际是开漏。所以映射时
额外扫描述列里的 `open-drain` / `open collector`：

```
3  ALERT  Digital output  "Open-drain alert output, default state is active low."
   -> etype = open_collector      (不是 output)
```

抽完**必须把表打出来人看一眼**。映射规则是启发式的，不是真理。

## 封装数字（-> `package{}`）

两页，都在手册末尾的机械章节：

| 页标记 | 拿什么 |
|---|---|
| `PACKAGE OUTLINE` | 本体 D/E、高度 A、引脚宽 b、引脚跨距（tip-to-tip）、pitch |
| `EXAMPLE BOARD LAYOUT` | **Land Pattern**：焊盘长 × 宽、焊盘列中心距、圆角 R |
| `EXAMPLE STENCIL DESIGN` | 钢网开孔（一般与焊盘同） |

TI 的图号写在页面右下（INA239 是 `4221984/A`），**记进 spec 的注释里**，
以后要复核知道该翻哪张图。

### 读数要点

* `3.1 / 2.9` 这种成对出现的是 **MAX / MIN**，标称是 3.0。
  INA239 本体就这样，别读成"本体是 3.1"。
* `5.05 / 4.75 TYP` 同理：引脚跨距 MAX 5.05 / TYP 4.75。
* Land Pattern 里的 `(4.4)` 是**焊盘列中心距**，不是引脚跨距。
  两者差一个焊盘长度。
* `10X (1.45)` / `10X (0.3)` 是 10 个焊盘的长 × 宽。
* `(R0.05) TYP` 是焊盘圆角，转成 `roundrect_rratio` 要除以短边。

### 拿不到图怎么办

`pdfplumber` 抽不出机械图的数字（图上的数字是矢量文本，位置散乱）。
这一步基本靠人眼看图录入 —— **这没关系，但要意识到**：

> 校验侧只能证明"封装 = spec"。如果这个数录入时就看错了，
> 校验会一致地 PASS。

所以 spec 里每个数字旁边注明**来自哪张图的哪个标注**，
是这条链路里唯一能防抄错的办法。
