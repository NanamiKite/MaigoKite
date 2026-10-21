# 馈电位置与坐标语义采集

这组仍使用一根 1 m、半径 1 mm、145 MHz 的无损自由空间导线，只改变馈电位置、整体朝向或分段方法。目的是确认模型含义能正确映射，不再追求消除 CSV 两位小数带来的方向图 RMS。不是制作方案，也不连接发射机。

## 三例计算结果

三例的 GAL 另存模型、电流 CSV、方向图 CSV 和结果截图均已收到。另存模型与输入的数值参数一致；只有数字格式和分隔空白变化。参考版本沿用用户报告的 v1.2.0.20。前两例复用原候选，不重算；自动分段在实际网格到齐后，通过公开 taper 参数做了一次有界求解。

| 案例 | GAL 截图阻抗，Ω | pymininec 阻抗，Ω | 总场 RMS，dB | 结论 |
| --- | --- | --- | --- | --- |
| 偏移馈电 | `90.52+j4.64` | `90.505657+j4.600240` | 0.002794 | `w1c3` 对应第 13 个 pulse、X=0.15 m；馈点复电流与截图阻抗相互印证 |
| 斜向导线 | `72.97+j5.822` | `72.961721+j5.795923` | 0.002892 | 19 个 pulse 的 XYZ 一致；同角度下 V/H/Total 对应，无明显旋转或分量对调 |
| 自动分段 | `74.57+j11.24` | `74.565239+j11.226422` | 0.002772 | 41 个 pulse；公开 taper 接口可表达本例网格，但段数来自参考，不是自动映射已完成 |

偏移馈电总场的 2662 个有效样本在候选同样保留两位小数后全部一致。斜向导线总场为 2658/2664，自动分段为 2646/2662；三例 V/H/Total 均无数值下限标记不一致。斜向导线的 V/H RMS 分别为 0.002878/0.002962 dB；这验证了本例的分量增益映射，不证明极化相位、旋向或任意几何的全部语义。

电流仍有导出舍入区间外的残差：偏移、斜向、自动分段的复电流残差下界 RMS 分别为 2.016、1.687、0.358 μA。截图电抗与候选相差约 0.03976、0.02608、0.01358 Ω，须考虑各截图的显示位数，不能把它们都归为浮点噪声。比较保留差异，不调整频率、源、材料或参考结果，也不设事后容差宣布 PASS。

### 自动分段的适配边界

GAL 在本例 SEG=-1、DM1=800、DM2=80、SC=2、EC=1 下产生 41 个 pulse。保持一根导线、半径和端点不变，先仅检查三个公开参数组合的网格；这里固定的 **42 段来自参考 pulse 数加一**，不是从配置独立推导。

| pymininec taper 参数 | 超出 CSV 坐标舍入区间的 pulse | 与打印坐标的最大距离 |
| --- | --- | --- |
| 默认端部长度 | 20/41 | 1.500 mm |
| `min_t=λ/800`，不指定 `max_t` | 0/41 | 0.475 mm |
| `min_t=λ/800, max_t=λ/80` | 40/41 | 21.638 mm |

CSV 坐标保留 0.001 m，最近舍入区间为 ±0.5 mm。只有第二组进入后续求解；其端部长度使用候选自身 `λ=299.8/145 m`，没有拟合电流或方向图。其余两组只检查网格，没有额外求解。求解仍由原 pymininec 完成，未拆导线、覆写私有方法或修改内核。[公开 taper 接口](https://github.com/schlatterbeck/pymininec/blob/master/mininec/taper.py)

因此本轮证明的是“现成接口能表达这组观测网格并给出接近结果”，不是“MMANA 自动分段已复刻”。段数/余量分配、SC/EC 的内部规则仍未知，但不再把复刻它们作为工程兼容的前置条件。后续使用各自原生网格核对物理馈点、收敛性和工程结果；不把 DM2 直接改名为 `max_t`，也不为坐标小数一致而重写求解器。网格低于 CSV 分辨率的差异仍不可见。[MMANA 分段说明](https://hamsoft.ca/pages/mmana-gal/mmana-gal_basic_help/MMANA-GAL%20basic%20help.htm)

完整数据保存在 `artifacts/semantics-review-20261019/`：三例原始参考、手工转录截图数值及其图片 hash、两例旧候选的引用、新 taper 候选原始结果、逐点差异、水平/垂直 PNG/SVG、网格比较图、脚本快照和 SHA-256 manifest。参考 CSV 另导出为 `reference-far-field.json`，保留 V/H/Total、原始下限标记及归一化总场，原 CSV 不变。

复算本轮分析：

```text
<spike-python> experiments/mmana_compatibility/review_semantics.py \
  --candidate experiments/mmana_compatibility/artifacts/semantics-20261019 \
  --reference /mnt/hgfs/vmSnycFile/mmana-a02-semantics-20261019 \
  --screenshot-values experiments/mmana_compatibility/artifacts/semantics-review-20261019/reference/screenshot-values.json \
  --output <不存在的新目录>
```

该入口新增 solver 调用为 1，程序内部 LLM 调用为 0。仍不具备实际材料、地面、接点与负载的覆盖，不开放生产 MMANA-compatible 模式。以下保留本组三例的原始采集方法，**当前不需要重新采集**。

## 原始采集步骤

用现有 **MMANA-GAL v1.2.0.20** 打开同目录的三个 `.maa`，不用重新手输几何。

| 文件 | 唯一变更 | 要确认的事情 |
| --- | --- | --- |
| `A02-offset-uniform20.maa` | 源由 `w1c` 改为 `w1c3` | 在原方向导线上从中心往正端偏移 3 个 pulse；预期坐标为 `(0.15, 0, 0)` m |
| `A02-oblique-uniform20.maa` | 整根导线转为 `(-0.18,-0.24,-0.4) → (0.18,0.24,0.4)` m | 长度仍为 1 m、中心源；检查 θ/φ、水平/垂直分量与切面方向是否对应 |
| `A02-auto-segmentation.maa` | SEG 从 20 改为 -1 | 采集 GAL 真正生成的 pulse 数和坐标；不预先猜分段数或用候选网格替代 |

前两例预期为 19 个 pulse。`w1c3` 中间是数字 1，末尾直接写 3；采用帮助中的中心偏移记法，不额外加括号。该位置是待参考核对的映射，不是已经验证的 GAL 行为。[MMANA-GAL 馈电与分段帮助](https://hamsoft.ca/pages/mmana-gal/mmana-gal_basic_help/MMANA-GAL%20basic%20help.htm)

三个文件共同保持：自由空间、无损、1 V/0°、50 Ω 参考阻抗、无负载、附加高度 0；DM1=800、DM2=80、SC=2、EC=1。不改频率、半径和材料，不优化或调谐。斜放导线的负 Z 在自由空间不是“埋在地下”，不要因此抬高或改地面条件。

## 一次交接所需数据

对每个输入保留以下文件，使用相同前缀，例如 `A02-offset-uniform20-current.csv`：

1. 计算后另存 `<前缀>-gal.maa`，不覆盖输入。
2. 原始电流 `<前缀>-current.csv`，保留 pulse、XYZ、实部、虚部。
3. 原始方向图 `<前缀>-pattern.csv`，沿用上一轮导出，保留角度和 V/H/Total。若角网格不同，原样交接，不插值或删点。
4. 结果表截图 `<前缀>-result.png`，能看清频率、R、X、SWR、Ga 和工况。若操作方便，可再提供馈点在天线视图中的位置截图。

计算报错或 pulse 数异常时保留原始错误和文件，不通过调整参数让它接近候选结果。参考仍放回本交接目录；之前 A01 的参考不用重做。

## 候选边界与复算

前两例通过同一个固定均匀网格 probe 计算，按实际坐标选择源；原始远场、电流、solver 文本、PNG/SVG 与环境 hash 分开保存。旋转例还与已有 X 轴基线比较阻抗，检查候选自身的旋转一致性；这不代替 GAL 对照，旋转后的离散角网格也不保证正好采到真实峰值。

首次准备第三例时只有参考输入，没有伪造候选值。当前新增的参考辅助求解保留在独立目录，不改写那次 `NOT_COMPUTED_UNMAPPED_SEGMENTATION` 记录。MMANA 的 DM1/DM2/SC/EC 与 pymininec 的 taper 参数不等价，不擅自改成均匀分段或拆成多根导线。

在仓库中按已有实验 README 创建独立环境后运行：

```text
<spike-python> experiments/mmana_compatibility/semantics.py \
  --baseline experiments/mmana_compatibility/artifacts \
  --output <不存在的新目录>
```

这个采集准备入口仅求解前两例，无内部 LLM 调用，不导入 MaigoKite 或 LangGraph。数值产物保存在实验忽略目录，不加入生产依赖。新增的参考辅助求解使用上面的 `review_semantics.py`，两次采集状态分别保留。

前两例也可单独复用已有比较入口，无需重跑 solver。每例各执行一次，替换下面的前缀与路径：

```text
<comparison-python> experiments/mmana_compatibility/compare.py \
  --candidate <候选产物目录>/<前缀> \
  --current-csv <参考目录>/<前缀>-current.csv \
  --pattern-csv <参考目录>/<前缀>-pattern.csv \
  --reference-maa <参考目录>/<前缀>-gal.maa \
  --reference-version 1.2.0.20 \
  --output <不存在的新比较目录>
```

比较入口按候选输入检查 pulse 数、编号、XYZ 与角网格，保留两位小数舍入基线。均匀网格继续要求坐标精确对应；显式 tapered 候选才按 CSV 各坐标字段的打印精度检查，不做坐标插值。它只保存关联 `.maa`；本轮另存模型与输入的参数核对、截图阻抗和馈点电流交叉检查由 `review_semantics.py` 完成。
