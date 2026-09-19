# 符号 house style 细则

手册**不规定**引脚的摆放。手册只给"pin 8 = VBUS，Analog input"。间距、分组、
朝向、本体大小全是内部规范 —— 所以这部分错了，手册比对是查不出来的。

## 1. 间距 —— 200 / 400，固定，不按器件改

| 项 | 值 | 说明 |
|---|---|---|
| 组内间距 `pitch_mil` | **200** | 同一功能组内相邻引脚 |
| 组间间距 `group_gap_mil` | **400** | 不同功能组之间 |

都用 mil。列在 `symbol_style` 里，**但默认值就是答案 —— 通常不用写**。

### "固定"的含义

同一个库里混着 100 mil 和 200 mil 的符号，读图的人每换一颗器件都要重新建立
比例感。全库一致比单个符号紧凑重要。

> 不拿"官方库用 100 mil"当理由改。属实 —— 量过 STM32F103C8Tx、
> ATmega328P、PCA9555、TCA9548A，官方一律组内 2.54mm / 组间 5.08mm ——
> 但那是 KiCad 的风格，不是我们的。我们跟自己的库对齐。代价要认：49 个引脚的器件按 200 mil 排，本体 **55.9 × 121.9 mm**
（100 mil 能压到 35.6 × 63.5 mm）。**不要为了缩小符号去改 pitch** ——
真嫌大就重排 `groups{}` 把两侧配平（把几个组从左边挪到右边），那是布局的事。
改了会被吵：`make_part.py` 的生成警告里有这一条（不是报错 —— 引脚真的多到
摆不下的器件留个口子，但不会静默）。

## 2. 分组是**语义**信息，由 AI/LLM 判

工具没法从坐标推出"VBUS 和 IN+ 不是同一种功能"。而且这条规则**几何上不可判**：
左侧原本

```
VBUS(12.7)  ─400mil─  IN+(2.54)  ─200mil─  IN−(−2.54)
```

把 `IN+` 提到 7.62 就变成

```
VBUS(12.7)  ─200mil─  IN+(7.62)  ─400mil─  IN−(−2.54)
```

两种分组**都满足**"组内 200 / 组间 400"。所以 `groups{}` 是**输入**，
生成器只负责把它翻译成坐标；校验侧也拿它当输入，并额外把
"几何推断的分组"和"声明的分组"对账。

## 3. 组内顺序 = pins[] 的书写顺序

想要右侧 `MOSI / MISO / SCLK / CS`，就在 `pins[]` 里按这个顺序写这四行。
组的先后由 `groups.right` 的数组顺序决定。

## 4. 每边对齐：默认顶对齐

```
side_align: "top"     # 每边第一个引脚 y 相同（默认）
side_align: "center"  # 每边各自上下居中
```

顶对齐是 KiCad 官方库的惯例。证据：官方 `Sensor_Energy:INA226` 的 左侧
`Vbus=7.62, Vin+=−2.54, Vin−=−5.08`，右侧 `A1=7.62`（最高）， `Vbus` 和 `A1`
都在 7.62 —— 两侧从同一条水平线往下排。顶对齐还有个实际好处：
左右两侧的第一个引脚在同一水平线上，跨侧读数容易。

## 4.5 pitch / group_gap 必须是 100 mil 的整数倍

不是审美要求，是**几何硬约束**。顶对齐时第一个引脚的 y = 最大跨度 / 2。若把
`pitch_mil` 设成 150、 `group_gap_mil` 设成 300：

```
最大跨度 = 3×150 + 300 = 750 mil     ->     第一个引脚 y = 375 mil = 9.525 mm
                                            9.525 / 1.27 = 7.5   ← 不在 50 mil 栅格上
```

后果：符号画出来完全正常，但引脚连不上线，ERC 会对每个引脚报
`endpoint_off_grid`。

`make_part.py` 生成后会自检并打印警告；`symbol_lint.py` 也会报 `off_grid`。
但最省事的办法是就让 `pitch_mil` / `group_gap_mil` 都是 100 的整数倍 （默认的
200 / 400 满足）。

## 5. 本体大小

由**跨度最大的一侧**决定：

```
by_top    = Y_top + top_margin_mil
by_bottom = Y_top − max(span_side) − top_margin_mil
```

`body_half_width_mil` 是手工给的（默认 300 mil = 7.62 mm，总宽 15.24 mm）。
给多少要看最长的那一对引脚名会不会撞上 —— 校验侧的 `symbol_lint.py` 有
`overflow` 估算（按 0.85×字号/字符），但**以渲染图为准**。

## 6. 引脚电气类型

| 手册 TYPE 列 | 描述里有 open-drain / open collector | KiCad etype |
|---|---|---|
| Digital input / Analog input / Input | | `input` |
| Digital output / Analog output / Output | 是 | `open_collector` |
| Digital output / Analog output / Output | 否 | `output` |
| Power supply / Ground | | `power_in` |

**类型错了画出来一模一样**，间距、栅格、本体全对。只有 ERC 会说话 ——
所以校验侧一定要跑 `sch_erc.py` 提取 KiCad 认定的类型再和手册比。
