# 从数据手册抽引脚表和封装数字

## 引脚表（-> `pins[]`）

### 先试自动抽

TI 手册的引脚表在 "Pin Configuration and Functions" 一节，标记是
**`Table 5-1`**。抽法：`kicad-check-sch-component/scripts/pdf_pins.py`。

```bash
python ../kicad-check-sch-component/scripts/pdf_pins.py ina239.pdf --dump
```

### 抽不出来怎么办 —— 常见，不是例外

`pdf_pins.py` 认的是"有 TYPE 列的英文引脚表"（TI 那套）。以下都认不出来：

| 情况 | 例子 | 征兆 |
|---|---|---|
| 表里没有 TYPE 列 | Infineon PSoC 62 的 `Table 7`（只有 `信号 / 100-TQFP / 68-QFN / 49-WLCSP`）| `找不到引脚表页` |
| 中文/其他版式 | Espressif ESP32-S3 中文手册 | 抽到 0 条 或乱码 |
| 下标被拆成两个词 | PSoC 的 `V_DDD` 在 PDF 里是 `V` + `DDD`，且第二块**基线更低** | 信号名缺头少尾 |
| 页面是矢量图 | 尺寸图上的数字 | 手动读图 |

**退路不是"手抄一遍"，是写个专用解析器。** 用 pdfplumber 直接读**词坐标**：

```python
words = page.extract_words()          # 每个词带 x0/x1/top，不依赖表格识别
# 1) 先用无下标的列（如球位列 A11/C7）找出每行的主 top
# 2) 再把 ±8pt 内的词都归到那一行 —— 下标块比主行低 4pt 左右
# 3) 按 x 区间把词切成列，拼接信号名
```

完整例子见 `D:/Project/Current Monitor/Infineon 6245/src/extract_table7.py`。

**这样做还有个额外好处：** 它和手抄的那份是**两条独立的路**，逐条比对
就能抳出手抄错误 —— 而直接手抄只有一条路，错了无从发现。

抽完（不管是哪种方式）把表打出来**逐条过一遍**：映射规则是启发式的，不是真理。

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

抽完**把表打出来逐条核一遍**（500 行的表也要核）。映射规则是启发式的，不是真理。

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
做法是把那一页**渲染成 PNG**（`page.to_image(resolution=300)`）再读数 ——
由 AI/LLM 读图录入，不是"只能人工"。**但要意识到**：

> 校验侧只能证明"封装 = spec"。如果这个数录入时就看错了，
> 校验会一致地 PASS。

所以 spec 里每个数字旁边注明**来自哪张图的哪个标注**，
是这条链路里唯一能防抄错的办法。
