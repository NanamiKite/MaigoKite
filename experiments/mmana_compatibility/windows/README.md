# Windows MMANA 自动采集

## 当前批次已完成，无需重跑

原六例及补缺批次 **23/23 项**均已收齐并完成数值对照。最新返回包为 `mmana-20261019T064455Z-kcx0lq_p.zip`，收尾状态 `COMPLETE`，274 个文件与目录及 manifest 一致。**不要重跑默认命令、`-NextBatch` 或 `-Sweep`。** 以下命令保留作复现说明，不是新增任务：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\capture.ps1 -NextBatch
```

该命令选择 GAL EXE 后固定执行 **23 次**：三个新几何完整采集，加五个旧工况各四个缺失频点；已有 7.1 MHz 结果直接复用。不是新增 23 类天线。配方和清单都随包提供，`recipes.py` 必须与 `capture.py` 放在一起；环境和 GUI 操作复用已跑通版本，不重新安装 Agent 或新增自动化依赖。

- G01：两条 10 m 导线连接的倒 V，7.1 MHz，接点 `w1e`。
- G02：四条 10 m 导线闭合方环，7.1 MHz，第一边中心 `w1c`。
- G03：三单元 Yagi，145 MHz，驱动单元 `w2c`。
- 扫频补点：五种工程工况各 `6.80、6.95、7.25、7.40 MHz`；不重新采 7.1 MHz。

新几何均为自由空间、无损，用于检查连接和方向图，不是制作方案。已校验 GAL 另存文件、模型/计算双频率和原始导出；程序实际版本 1.2.0.20、EXE hash 与旧六例一致。候选侧九次原计算及六次固定网格诊断已完成，**数值差异及限制见[工程评估](../ENGINEERING.md)**。方环日志 88 pulse 而 CSV 导出 87 行的问题单独记录，不能把缺行补造为验证通过；其他成功原始结果照常保留使用。

先查看清单而不打开 GUI：`python capture.py --next-batch --list-plan`。也可 `-NextBatch -Case G03-yagi` 只采一个新模型，或 `-NextBatch -Case E05-soil` 只补该工况四个频点。遇到失败仍停止并保留原始证据，不自动循环重试。`-NextBatch` 不与 `-Sweep` / `-InspectOnly` 组合。

下面保留默认六例的完整说明和历史故障证据，供复现，**不是本轮要重跑的步骤**。

在 Windows 上打开 PowerShell，进入本目录后运行下面一条命令，选择你已经安装的 **MMANA-GAL 可执行文件**。无需安装或启动 MaigoKite。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\capture.ps1
```

Windows PowerShell 5.1 和 PowerShell 7 均有启动入口；机器需已有 Python 3.10 或更新版本。首次运行会联网安装 `pywinauto`、Pillow 及其依赖到 `%LOCALAPPDATA%\MaigoKite-MMANA-Capture\venv`，之后复用，不改系统 Python 或 Agent 环境。没有 Python 时脚本会提示，不会自行安装系统软件。可用 `-Python "C:\Python312\python.exe"` 指定首次建环境的解释器。

先保存并关闭已有 MMANA 窗口。运行期间保持 Windows 桌面解锁，不操作自动化窗口。**无需管理员权限**。按 Ctrl+C 停止；不要直接关闭 PowerShell 窗口，否则 Python 来不及清理。

## 采集范围

默认自动准备五个工程工况，以及一次自由空间网格加密，共六次主频计算：

- 自由空间无损、铜线、中心串联 10 Ω、理想地面、有损地面。
- 共同几何为长 20 m、半径 0.5 mm、Z=5 m 的直偶极子，`w1c`、1 V、0°，7.1 MHz，参考阻抗 50 Ω，附加高度 0。
- 自动分段使用 GAL 自己的 DM1/DM2=800/80；加密例为 1600/160。不追求与候选求解器相同 pulse 数。
- 铜线通过材料选项名称选择，不假定 GAL 的铜预设等于候选侧的电导率。
- 有损地面尝试填写 εr=13、电导率 **5 mS/m**、高度 0、无地网、单一介质；无法识别编辑器、存在额外介质或读回不符就停止，不沿用默认土壤。

每例保存 GAL 另存的 `model.maa`、计算日志、结果与设置截图、原始电流 CSV、原始远场 CSV、方向图截图。程序核对另存模型的几何、源、负载、分段和环境字段，并检查 CSV 数据列和角网格；不修改或归一化原始数据。方向图导出请求 5° 步长，自由空间 θ=0…180°、有地面 θ=0…90°，φ=0…355°。

**六例主频采集已收齐并完成数值对照。** `053755Z-8t78q4op` 完成前五例，`055645Z-b9grnwah` 成功补采 E05，均来自实际文件版本 1.2.0.20、同一 EXE hash。最新 E05 的四个土壤值、单一介质及无地网在重新打开后核对成功，模型、电流和远场均已导出，收尾状态为 `COMPLETE`。这证明两次组合的六例采集完成，不冒充最新版脚本一次连续跑完六例；可选 `-Sweep` 尚未验证。

干净参考包为 `results/reference-capture-20261019.zip`，保留原始文件和两次来源元数据。旧失败/取消包已从有效结果目录移出，混合包中五例有效数据先经 SHA-256 校验保留。完整数值结论见[工程评估](../ENGINEERING.md)。下面是历史故障定位记录，不是当前仍阻塞的清单；旧 MMANA 源码仍不等同于 GAL 二进制。

2026-10-19 的 `051513Z-hbmg6xgl` 诊断确认：模型标题已经出现，但整个 `TPageControl` 及其子页面仍隐藏，截图只有工具栏。旧启动判定只检查控件存在，切页却只查可见控件，导致提前退出等待并报 `found 0`。当前启动和切页共用判定：主窗口、页面容器及一个活动页都可见且可用，才允许继续；加载期间的缺失或隐藏继续等待，多容器歧义及错误对话框仍立即停止。不强制显示控件，也不通过固定延时假定加载完成。异步发送的地面单选及地网取消操作同样等待实际选中状态。后续成功采集已走过这些步骤。

后续 `052349Z-5v3fnosw` 已走过启动和设置，停在另存模型：实际文件名框是 `edt1 / Edit / 1152`，旧脚本仅识别 `cmb13 / 1148`。当前同时支持这两种 [Windows 标准文件名控件](https://learn.microsoft.com/en-us/windows/win32/dlgbox/open-and-save-as-dialog-boxes#explorer-style-control-identifiers)，等待输入框与确认按钮可见、可用；只匹配对话框直接拥有的控件，再在文件名组合框内定位编辑器，不对任意输入框或任意编号为 1 的子控件操作。模型另存和电流 CSV 导出共用此逻辑，均等待实际文件稳定后再读取。

`052800Z-l7ajob0j` 已生成 GAL 另存的 `model.maa`，但发现频率不一致：计算页是 7.100 MHz，结构页和保存文件仍是 7.150 MHz；其余模型字段与配方一致。GAL 区分模型频率和计算频率，只设置计算页不足以保存正确模型。当前先设置结构页中与已知天线名称同一行的频率，再设置计算频率，分别记录读回值；保存后仍严格核对 `.maa`，开始计算前再查计算页。不改配方、不覆盖 GAL 输出来掩盖差异。六例成功参考均已核对为 7.1 MHz。

`053755Z-8t78q4op` 已完成前五例的模型保存、计算及原始电流/远场 CSV 导出，收尾归档状态为 `COMPLETE`；仅 E05 在地面设置阶段停止。这版中文 GAL 把地网开关显示为“地面介质类型”，其关联的数量/半径编辑器已从截图与控件树确认。现结合 `TMediaDlgBox`、唯一复选框、所在组内两个径向线编辑器识别该布局，不能只按中文关键词判为未知，也不能忽略其他未知选项。关闭地网后要求复选框未选中、数量/半径编辑器均禁用；确认土壤设置后重新打开逐项核对，并拒绝额外介质。控件含义与提交逻辑参考 [公开 MMANA 地面设置源码](https://github.com/ja7ude/MMANA/blob/153ad346ee8748ca778d7d8926339a540ef40238/MediaDlg.cpp)，仍以 GAL 实际读回为准。

`054442Z-7u_e_a42` 确认前述地网识别已通过，失败在第 3 个可编辑字段 `X(m)`：脚本要求清空，GAL 实际读回 `0.0`。这是采集器错误地把旧 MMANA 的空值表示当成了 GAL 必须满足的条件，不是介电常数或电导率错误。当前单一介质配方明确填写并严格读回 `13、5、0、0`（εr、mS/m、X 米、高度米）；保存后再次验证，不接受任意 X 值，也不跳过第二介质或地网检查。[GAL 帮助](https://hamsoft.ca/pages/mmana-gal/mmana-gal_basic_help/MMANA-GAL%20basic%20help.htm) 将 X/R 距离用于多介质分界；[原始 MMANA 求解入口](https://github.com/ja7ude/MMANA/blob/153ad346ee8748ca778d7d8926339a540ef40238/mininec3.cpp#L2060) 也单独处理最后介质的外延，不能把“必须空白”当作通用兼容条件。结果中 `x_m` 仅记录 GUI 原值，不解释为土壤在 0 米结束。`soil-readback.json` 逐项记录期望值、配置前值及提交/重新打开后的实际文本；最新 E05 已成功完成这些核对。

`054958Z-5os6o9xs` 的 `soil-readback.json` 已记录四项正确值 `13、5、0、0`。失败后仍是可用的地面设置对话框，表格编辑器已隐藏、确定按钮可用、没有额外验证弹窗；模拟鼠标消息未使确认完成。当前仅将地面设置的确定/取消改为 pywinauto 公共 `notify_parent(BN_CLICKED)`：它使用真实按钮 HWND 和 control ID 向父窗口投递标准 [WM_COMMAND / BN_CLICKED 通知](https://learn.microsoft.com/en-us/windows/win32/controls/bn-clicked)，仍执行 GAL 的按钮逻辑，不直接调用 EndDialog、隐藏窗口或假定数字 IDOK。关闭后必须同时满足对话框消失和主窗口恢复可用；出现其他弹窗立即报告其内容。确认后的重新打开校验保留，等待上限仍为 15 秒。最新 `055645Z-b9grnwah` 已在 Windows 上实际走通确认、重新打开和导出，脚本 SHA-256 为 `03021b821896a50b2ef0ba4a4b0b11c446239a796ddd56151200e72e50caa586`。

成功状态也只是 `CAPTURED_REVIEW_REQUIRED`，不是“兼容通过”。地面列语义、材料常数、方向图坐标定义与数值结果仍须复核。

## 结果怎么交回

结束后终端打印 ZIP 的完整路径，文件位于脚本旁边的 `results`。**把整个 ZIP 放回共享文件夹即可，不用逐张截图、逐个改名。** 失败或取消也保留已完成结果；失败包中的窗口截图、控件树、菜单名称和错误信息用于修正 GUI 适配。

`run.json` 记录实际 EXE 版本、EXE 和脚本 SHA-256、依赖版本和逐例状态；`manifest.json` 保存文件校验值。截图限于本次 MMANA 的窗口，不抓整个桌面；仍建议发送前查看内容。ZIP 不包含 MMANA 程序本身，也不上传任何内容。

脚本复制程序和语言资源到独立临时目录，仅操作副本和生成的模型。已有 MMANA 进程不会被接管或终止。正常结束、异常、Ctrl+C 时关闭本次拥有的进程，先将采集文件和错误记录保存到 `results`，再清理程序副本。进程关闭失败、结果复制失败或目录被占用（例如 `WinError 32`）时保留剩余工作目录、记录原因，仍尝试生成 ZIP；清理异常不覆盖原始采集错误，不终止其他程序来抢占目录。不完整收尾会以非零退出码提示。若 ZIP 本身写入失败，终端打印结果目录以便直接取回。每次运行使用新目录，不覆盖旧参考。

## 可选参数

已知程序路径时可以省去选择框：

```powershell
.\capture.ps1 -Exe 'C:\MMANA-GAL\MMANA-GAL.exe'
```

仅补采有损地面工况，不必重跑前五例：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\capture.ps1 -Case E05-soil
```

`-Case` 只限定本次工况，仍生成新的结果目录和 ZIP；不覆盖、不合并或重新标记先前结果。Python 入口对应 `--case E05-soil`，不指定时仍采集全部六例。

采集主频后，再采集五个工况各 12 个附加频点：

```powershell
.\capture.ps1 -Sweep
```

这会在 6.8…7.4 MHz 间按 0.05 MHz 步长逐点实算，总计 66 次计算。每个附加频点保存原始电流、结果日志和模型，不额外导出整球方向图；不是调用 GUI 的插值估算。遇到第一项失败即停止，不自动反复重试。

只采窗口与控件、不开始计算：

```powershell
.\capture.ps1 -InspectOnly
```

## 工具边界

控件操作不以抢占桌面前台为前提：页面通过整数索引切换，普通按钮通过发往自身 HWND 的鼠标消息触发，地面设置的确定/取消使用上述按钮通知，菜单通过实际菜单项的 command ID 异步触发。不能改回 `MenuItem.select()`：pywinauto 0.6.9 的该方法内部仍会调用 `SetForegroundWindow`。异步菜单命令还避免了脚本阻塞在自己需要继续处理的保存对话框上。

输入框使用 `set_edit_text()`；结构页与计算页频率均提交组合框通知，并以 GAL 另存文件中的值复核。土壤表格仅用定向 `send_keystrokes()` 导航，等待编辑器出现并逐项读回，不把发送按键视为填写成功。仍需保持桌面解锁、窗口未最小化以采集截图；没有宣称支持锁屏或无桌面运行。32/64 位提示不被屏蔽，页签操作避免跨位数的文本结构体消息；这与 Windows 拒绝抢前台是两类不同问题。

截图复用已安装的 Pillow 12 `ImageGrab.grab(window=HWND)`，只针对本次启动的 MMANA 窗口，不再按桌面矩形截取覆盖其上的其他应用。窗口渲染仍需人工检查；图片不替代原始 CSV 或另存模型校验。

GUI 自动化仅用于开发阶段的 reference oracle 采集。生产 MCP、领域包和优化器都不导入这个目录；没有新增 Agent 工具、工作流引擎或 MMANA 生产依赖。

复用 [pywinauto Windows 控件接口](https://pywinauto.readthedocs.io/en/latest/code/pywinauto.application.html)。CSV 的原生导出能力见 [MMANA-GAL 帮助](https://hamsoft.ca/pages/mmana-gal/mmana-gal_basic_help/MMANA-GAL%20basic%20help.htm)；控件布局参考 [原始 MMANA 的 FarSet 表单](https://github.com/ja7ude/MMANA/blob/153ad346ee8748ca778d7d8926339a540ef40238/FarSet.cpp)。这里只读取格式与控件含义，不复制求解器实现。
