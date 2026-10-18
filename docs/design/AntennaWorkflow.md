# 业余无线电天线工作流与求解器评估

> 状态：架构方案与文档/源码级评估，2026-10-19。未选定生产求解器，未安装、运行求解器或 MMANA-GAL，未取得数值兼容性回执。
> 权威范围：天线领域需求、工程契约、MMANA 兼容边界、方向图、材料、预算与求解器准入。通用运行机制仍以 [AgentRuntime](AgentRuntime.md) 为准，传输与权限沿用 [MCP](../agent-capabilities/mcp/mcp-integration.md)。

## 1. 产品目标与不可替代的交付条件

用户讲用途、设备、场地、材料与现实限制；Agent 负责将其转成工程约束，选择方案、解释取舍，并判断计算证据是否支持制作。用户不必先知道天线类型、增益、前后比、馈电阻抗或单元数。

生产链路采用独立 MCP Server 和可脚本化求解器，不启动或自动操作 MMANA-GAL GUI。MMANA-GAL 仅在开发验证环境中作为 reference oracle；Windows/Wine 采集的参考结果与生产包分开管理。

首版范围是适合 thin-wire 方法的导线/细导体结构，覆盖 HF、6m、VHF、UHF 的适用模型。dipole、inverted-V、OCF/folded dipole、vertical/ground plane、J-pole、loop/delta/quad、Yagi、Moxon、collinear、phased array、加载/陷波、多频与自定义几何均属于目标范围，但不能把“几何能表达”标成“馈电、材料与数值行为已验证”。PCB、微带、喇叭、波导、复杂面导体、介质天线和手机内部天线不进入本后端。

实现顺序固定为直接集成、组合现有组件、薄适配，最后才是补缺。只有现有组件不能满足已定义的 MMANA-GAL 可验证兼容性要求时，才补对应缺失部分；代码组织偏好不构成重写理由。

最终方案必须同时具有以下证据，不能用其中一项替代其他项：

- 可复现：明确模型、材料、求解配置、软件版本与重算入口。
- 数值可靠：有适用性、收敛性、扫频及制造偏差分析。
- MMANA 可验证：可导入的 `.maa`、语义映射及对应兼容 profile；不以峰值相近代替方向图/电流对照。
- 可制作：具体材料、尺寸、公差、接头、馈电匹配、安装与调试说明。
- 可观察：保留原始方向图、计算证据、阶段预算和实际用量，不能只返回自然语言与图片。

“可验证”不等于“已实测”，“仿真收敛”不等于“满足需求”，“匹配良好”不等于“辐射效率良好”。实物验证另列状态，不由 LLM 自行宣布完成。

## 2. 求解器评估结论

### 2.1 候选与复用边界

| 候选 | 已查到的可复用能力 | MMANA 兼容缺口与接入成本 | 当前判断 |
| --- | --- | --- | --- |
| pymininec | MININEC3 路线；Python API/CLI；pulse 激励、负载、地面、远场 | 自带 taper 与 MMANA 参数不等价；端点连接及数值修正不同；需验证精确网格接入、运行性能和 Python 3.14 科学计算依赖 | 保留为适配候选，不能直接命名为 MMANA-compatible |
| nec2c | 非交互 CLI、NEC 输入输出，适合进程隔离计算 | NEC 的分段/激励/地面方法与 MMANA 不同；`.maa → NEC` 是模型转换，不是等价证明 | 保留为工程计算及独立交叉检查候选 |
| xnec2c | 当前手册有 batch、优化、CSV 电流/方向图输出 | GTK/Linux/UNIX 部署依赖及 Windows/headless 运行仍须核实；数值路线仍是 NEC2 | 不因带 GUI 排除，也不默认比 nec2c 更轻 |
| NEC2++ / PyNEC | 原生计算库和 CLI，适合参数化计算 | 与 MMANA 的物理/离散化差异仍存在；原生构建及 Python binding 维护需评估 | 作为 NEC 路线备选，不同时接入多套后端 |
| 原始 MMANA C++ | 公开历史实现，可用于研究语义、前处理与计算路径 | 不是当前 MMANA-GAL 源码；VCL/全局状态/GUI 日志耦合；许可标注需核对 | 先作参考；仅在适配方案不能满足关键语义时评估提取 |
| 原始 MININEC BASIC | 算法来源与独立基准 | 不包含所有 MMANA/GAL 修改和文件语义；移植与维护成本高 | 算法对照，不直接当生产实现 |
| mcp-nec2-antenna | 已有 MCP、参数化建模与 NEC2 调用 | 公开接口以五类模板为主，模型仅保存在进程内存；没有足够证据覆盖这里的兼容/预算/恢复要求 | 可评估复用或贡献改进，不能视为完整交付 |

依据：[pymininec 项目](https://github.com/schlatterbeck/pymininec)、[nec2c 作者说明](https://www.qsl.net/5b4az/pages/nec2.html)、[xnec2c 当前手册](https://www.xnec2c.org/)、[NEC2++](https://github.com/tmolteno/necpp)、[原始 MMANA](https://github.com/ja7ude/MMANA)、[现有 MCP 的架构](https://github.com/RFingAdam/mcp-nec2-antenna/blob/main/docs/architecture.md)。上表是接入判断，不是运行通过结论。

### 2.2 已确认不能直接等同的行为

1. **分段规则**：MMANA 使用 SEG 与 DM1/DM2/SC/EC；pymininec 的 taper 接口使用分段数、最小/最大长度与固定倍增规律。不能只将 `-1` 转成“双端 taper”就认为一致。[MMANA 原始说明](https://github.com/ja7ude/MMANA/blob/master/mmana.txt)、[pymininec taper 源码](https://raw.githubusercontent.com/schlatterbeck/pymininec/master/mininec/taper.py)
2. **pulse 与接点**：MMANA 的 `WnB/C/E` 及偏移基于分段后的 pulse 编排；pymininec 文档声明端点采用与最短段长度相关的容差连接。几何相同、线顺序不同或近似重合，都需要检查实际连接与源位置，不能把 wire 中点直接当 pulse。[MMANA 原始说明](https://github.com/ja7ude/MMANA/blob/master/mmana.txt)、[pymininec 几何说明](https://github.com/schlatterbeck/pymininec#geometry)
3. **负载系数**：MMANA 帮助以 A 为 Laplace 分子、B 为分母；pymininec 的 `Laplace_Load(a, b)` 相反。必须按意义映射，不能按字段名字透传；L/C/Q 的频率语义还须单独验证。[MMANA 负载说明](https://hamsoft.ca/pages/mmana-gal/mmana-gal_basic_help/MMANA-GAL%20basic%20help.htm)、[pymininec Laplace_Load 源码](https://raw.githubusercontent.com/schlatterbeck/pymininec/master/mininec/mininec.py)
4. **地面模型**：MMANA-GAL 公开帮助说明阻抗采用理想导电地面假设，实际地面参数用于远场；NEC2 GN 提供有限地面近似及 Sommerfeld/Norton。两者存在模型层差异，不能通过单位换算消除。[MMANA-GAL 帮助](https://hamsoft.ca/pages/mmana-gal/mmana-gal_basic_help/MMANA-GAL%20basic%20help.htm)、[NEC2 GN](https://www.nec2.org/part_3/cards/gn.html)
5. **激励位置**：NEC EX 的 wire tag/segment 激励不是 MMANA pulse 表达式；翻译需要保存位置、方向和复电压的映射证据。[NEC2 EX](https://www.nec2.org/part_3/cards/ex.html)
6. **原始 MMANA 不等于 GAL**：公开 MMANA 的 README 说明历史 C++ Builder 环境；计算源码仍直接写 GUI 日志。GAL 官方说明使用修改后的 MININEC3。不能只提取一个 `mininec3.cpp` 就宣称兼容。[MMANA README](https://github.com/ja7ude/MMANA)、[计算源码](https://github.com/ja7ude/MMANA/blob/master/mininec3.cpp)、[GAL 官方说明](https://gal-ana.de/basicmm/ru/)

参考版本必须冻结。官方页面当前列出 Basic 3.5.3.82，可作为待采集候选；只有实际采集时记录安装包/可执行文件 hash、配置和环境，才形成 oracle 身份，不能把旧帮助里的示例值当新版本 golden data。[官方版本说明](https://gal-ana.de/basicmm/ru/)

来源许可也必须在采用前核清：原始 MMANA 仓库 LICENSE 是 LGPL-2.1 文本，而相关源码头声明 LGPL-3.0-or-later；GAL Basic 官方页面限定私人非商业用途。这里记录来源差异，不作许可证兼容性结论，不下载分发 GAL，不复制代码进入主项目。独立进程不自动消除许可证义务。[MMANA LICENSE](https://github.com/ja7ude/MMANA/blob/master/LICENSE)、[源码声明](https://github.com/ja7ude/MMANA/blob/master/mininec3.cpp)、[GAL 使用范围](https://gal-ana.de/basicmm/ru/)

### 2.3 复用决策与最小补缺

| 能力 | 优先采用路径 | MaigoKite 保留的边界 |
| --- | --- | --- |
| 电磁求解 | 候选 solver 的公开 API、CLI 和现有输出 | 参数映射、适用性检查及结果单位/坐标规范化，不重新实现矩阵求解或远场积分 |
| MCP 工程接口 | 评估现有天线 MCP 能否配置、扩展或贡献上游；不足时以官方 SDK 薄封装现有 solver | 项目作用域、compact summary、artifact 引用与取消回执，不重写 MCP 协议 |
| 数值优化 | 先检查所选 solver 已有优化器；不满足目标/约束时组合现有优化库 | 有界变量、目标、约束、计算预算和候选评价函数，不重写优化算法 |
| 格式与 MMANA 语义 | 先找可复用的解析器、导出器和前处理实现，再验证精度与信息保留 | 仅补有证据的 `.maa`、segmentation/pulse、source/load 等缺口，不为统一接口重写已有转换器 |
| 图表与数值比较 | matplotlib、现有科学计算库和项目已有文件预览 | 坐标/单位契约、比较规则和领域标注，不重写绘图库或从图片反推数值 |
| 工作流执行 | 现有 LangGraph、Skill、MCP、Repository 和预算入口 | 需求到工程约束、阶段准入、材料依据与用户取舍，不另建运行框架 |

某个现有 MCP 仅支持少量模板，是扩展其模型输入与输出的调查起点，不是推倒重写的理由。MCP 层不合适也不否定其底层 solver；优化器不合适不否定求解器。逐项组合组件，避免“整套不完全匹配，所以整套自研”。表中的候选需要核实公开接口、版本、维护状态和许可，不将尚未找到的解析器当作已存在的依赖。

每项拟新增的兼容实现必须附一份简短差异记录：使用了哪个现有组件及版本、复现输入与 reference 条件、缺失行为或误差、为什么配置/公开 API/组件组合无法解决、最小补丁范围、回归证据和维护/退出条件。先修映射错误，再判断是否需要算法改动；不以“代码更整洁”“接口不统一”或“自己写更方便”通过门槛。领域需求和验收规则由产品定义，不借此重做通用基础设施。

## 3. 架构与状态所有权

```text
用户自然语言
  → Agent + 按阶段加载的领域 Skill
  → Design Brief / 候选 / 已确认约束
  → 工作流阶段准入与预算
  → 官方 MCP SDK
  → 天线 MCP：几何、优化任务、MMANA compatibility layer
  → 一个经评估选定的可脚本化 solver
  → 原始计算 artifact
       ├─ 兼容差异报告
       ├─ 有界工程摘要 → Agent 决策
       ├─ matplotlib → PNG / SVG → 用户
       └─ 制作包与重算输入

开发验证环境：MMANA-GAL → reference artifact → 同一差异比较器
```

Domain Reasoning 不新增常驻专家 Agent。Workflow Orchestrator 是天线领域阶段与准入逻辑，执行仍用现有 LangGraph。MCP 不选择用户偏好、不改需求、不替 Agent 接受工程结果。首版不建插件市场、通用工作流引擎或多后端注册框架。

建议同仓库独立 `extensions/antenna_design/`，包括领域契约/Skill、MCP 包及兼容资料；求解/绘图依赖属于扩展，不进入普通聊天的默认安装。涉及主应用的接线只承担现有产品服务调用，不能将电磁规则塞回 Core 或路由。

| 数据 | 权威所有者 | 其他层持有什么 |
| --- | --- | --- |
| Design Brief、选择、阶段、确认记录与跨回合预算 | 现有 Repository 承载的版本化领域记录；实施时补最小事务接口 | checkpoint 记录项目 ID/版本，不复制另一份可修改的事实 |
| 当前工具执行位置、提问/审批 interrupt | LangGraph checkpoint | UI 投影，不用聊天文字推断执行位置 |
| 几何、离散模型、solver 输入、结果和日志 | 当前工作区内不可变 revision artifact | Repository 存引用/hash，LLM 读有界摘要 |
| solver 进程与计算任务 | MCP Server 的本次计算资源 | Agent 只持有受作用域约束的 job ID |
| oracle、兼容 profile 与差异报告 | 开发验证资源及版本化报告 | 生产只使用明确覆盖范围的 profile，不调用 GUI |

项目绑定工作区文件夹与设计项目 ID；跨对话复用须显式选项目，不能通过长期记忆隐式带入其他工作流。修改需求或材料产生新 revision；旧模型、验证、图表保留可查但标为过期。一次推进在 Repository 现有事务边界内核对版本与证据引用；不新增 event sourcing。

### 当前接入缺口

- `langgraph_runtime.py` 当前新回合只继承 checkpoint 的 messages，不继承任意自定义领域 state。因此不能假设加一个 AgentState 字段就得到跨回合设计项目。
- 当前 interrupted 自动重放范围仍受限于文件工具；不能直接开放任意 MCP replay。重启先查询 MCP job/结果，以相同操作身份核对，不自动重新优化。
- 当前 MCP Client 默认请求限时 30 秒，取消本地等待不保证服务器已停止。长计算需要显式计算任务/取消回执，不靠增大超时掩盖生命周期。
- `MCPToolWrapper` 目前将完整结果序列化为 ToolMessage。天线 Server 必须只返回摘要；主应用对该扩展的输入边界仍需做大小/类型校验，不能信任远端自律。
- 已有模型 usage 与任务调用预算，但还没有这里的跨回合 workflow/stage 预算关联；现有图像/文件查看也不代表任意 MCP artifact 已自动接通聊天预览。

以上是实现准入项，本轮不改 Runtime、API、数据库或 GUI。

## 4. Design Brief 与阶段规则

Brief 包含用途/频段/收发、设备、架设、空间、材料、偏好、硬约束与未知项。字段允许缺失；频率与长度等采用显式单位，内部统一 SI，展示可用 MHz/mm。每个影响设计的事实记录来源、确认状态、适用条件和 revision；保留消息引用供审计，不把原话再抄一份进每次 prompt。

`known`、`important_unknown`、`optional_unknown` 随阶段变化。关键未知附带“会改变哪个决定”；Agent 只追问当前决策所需信息。用户不知道时，给通俗选项或提出明确假设/范围；不得假装用户确认过。建议默认最多三个候选，只正式仿真一个主方案和必要时一个备选。

| 阶段 | 准入与退出条件 |
| --- | --- |
| DISCOVERY | 识别用途与现实限制，只追问影响决策的未知 |
| CONCEPT | 候选有选择依据、取舍和冲突说明，用户确认主要取舍 |
| DESIGN_READY | 模型输入完整；适用性、材料依据及关键假设已检查 |
| SIMULATING | 已为不可变 revision 提交计算，有 job 身份与计算预算 |
| VALIDATING | 有真实完整结果，执行数值、兼容、需求与容差检查 |
| VALIDATED | 当前 revision 的必需检查通过；条件性结论单列，不冒充全部通过 |
| BUILD_READY | 制作包、材料、公差、装配与测量计划完整，且不存在阻断性未验证假设 |

等待用户、失败与取消是执行状态，不扩充为大量设计阶段。改变方案回到适当阶段，不以 phase 字符串替代证据。`VALIDATED` 的 profile 覆盖验证与“当前这个设计已跑过 MMANA oracle”必须分别报告；没有 per-design 回执不能声称已在 GAL 实算通过。涉及 profile 外行为时阻止最终验证通过。

## 5. MMANA compatibility layer

兼容层属于工程 MCP，包含文件编解码、MMANA 语义前处理、后端映射和结果规范化，不包含用户访谈或新运行时。

### 5.1 三个独立兼容门槛

1. **文件兼容**：`.maa` 能由目标版本读取；导入/导出不丢已支持的模型信息。
2. **语义兼容**：网格、pulse、连接、源/负载、地面和材料解释与目标 profile 一致。
3. **数值兼容**：相同条件下达到预先约定的误差范围，覆盖阻抗、电流和方向图等结果。

不得合并成一个 `compatible: true`。报告使用 `not_evaluated / pass / fail / unsupported`，携带 reference profile、覆盖特性和报告引用。未验证模式可以用于研究，但不提供正式“MMANA-compatible”交付标识；严格模式不得静默降级到普通 NEC2。

### 5.2 保留语义，不做有损共同子集

内部区分工程模型、原始 MAA 文档与离散计算模型，分别记录 hash。工程模型可含导线/管材/负载/材料引用；MAA 文档保留 wire 顺序、SEG、DM 参数、源表达式、附加高度、地面和材料枚举、未知区块及编码信息。

- 文件编码按 profile 显式声明；不靠不可见的编码猜测改写标题或注释。UI 单位与文件单位分开验证。
- 未识别字段保留原文仅供 round-trip；若可能影响计算，报告 unsupported，不能继续求解后声称兼容。
- 不把负半径引用、零半径特殊结构或组合管径无条件当成普通正半径导线；实现前逐项核对目标版本语义。
- 分段产生可审计 segment/pulse map：wire ID、顺序、端点、长度、方向、连接、局部/全局 pulse 索引和 ground 标记。
- 源/负载先解析成明确物理位置及 pulse，再转后端表示；不以最近 segment 猜测代替精确映射。重网格后重算映射。
- L/C/Q、Laplace、有限电导和匹配网络必须保留类型、单位、频率依赖与来源；不支持的项不能无声丢弃。
- 扫频记录几何是否固定、分段参考频率及是否逐频重网格；应以 oracle 结果核实，不自行假定。

`mmana_compatible` 与 `engineering_native` 是计算条件，不是外观开关。后者可以采用不同真实地面或损耗处理，但必须生成独立的模型条件/结果。不能从一个模式拿阻抗、另一个拿增益拼成同一报告，也不能为匹配 oracle 隐藏现实材料损耗。

## 6. 方向图是一等数值结果

数据流固定为 `solver → raw far-field → comparison / metrics / renderer / export`。任何后续分析均读取数值 artifact，不从 PNG/SVG 反推数据。首版使用扩展内 matplotlib 非交互绘图；不引入图像生成模型或生产 GUI。交互式 Plotly/3D 延后。

### 6.1 数值契约

`far_field.json` 至少包含 schema 版本、模型/条件/hash、solver/profile、frequency_hz、角度与增益定义、采样域/步长/端点，以及样本数组。规范坐标使用右手系：+Z 向上，θ 为自 +Z 的天顶角，φ 在 XY 平面由 +X 朝 +Y 增大；`elevation_deg = 90 - theta_deg`。地理北向必须由安装朝向另作映射，不把 +X 自动称为北。

每个样本保留 θ/φ、total_gain_dbi；可用时保留 H/V gain 和复远场分量。缺失极化数据用 null 与原因，不填零；线性功率可相加，不能直接将两个 dB 数相加。规范化适配必须记录 solver 的原始角度、极化基和复相位约定；在极点处理基方向歧义。电流与远场的相位参考、RMS/峰值电压约定不得靠绘图层猜测。

绝对增益与 normalized gain 分别保存语义：normalized 是相对指定峰值的 dB，不是 dBi。标明 reference peak 数值、频率、采样域以及是整域峰值还是当前切面峰值。仅有两个切面时，只能报告“已采样范围最大值”，不能宣称三维全局最大值。物理零场不能写非标准 JSON 的 Infinity；用显式 zero-field/null 状态和原因，显示下限仅在绘图参数中应用。

MMANA 的电流、远场和扫频有 CSV 导出，far-field 使用角度及极化增益；这是 oracle 数值入口，不以截图读数代替。最终各字段仍须以冻结的 GAL 版本实际导出确认。[MMANA 数据导出说明](https://hamsoft.ca/pages/mmana-gal/mmana-gal_basic_help/MMANA-GAL%20basic%20help.htm)

### 6.2 切面与渲染

- Azimuth 图必须标明固定 elevation/θ；`elevation = 0°` 与主瓣仰角切面不同，不默认混用。
- Elevation 图标明所在垂直面及前/后方向；跨过天顶后 φ 的翻转按坐标变换处理，不简单将 θ 扫描复制成 360°。
- 任意 2D 切面用平面基向量/法向与角度范围定义。若原网格不足，显式选择补算或仅作显示插值，记录方法；插值数据不能冒充原始 solver/reference 样本。
- absolute 与 normalized 两种图均支持；图上标注频率、切面、极化、尺度、增益单位、最大值、主瓣及前后方向。负 dBi 不直接当 matplotlib 的负半径，径向偏移/裁切下限仅影响显示并明确标注。
- 主瓣方向、切面半功率波束宽度、后瓣、副瓣由确定性数值分析生成。网格太粗、交点缺失、并列主瓣或仅部分角域时报告分辨率/歧义，不编造精确指标。
- F/B 保存“反向单点”或“后扇区最大值”的定义和角域，不能相互替换。副瓣相对哪个主瓣、搜索角域和排除主瓣区间也须明确。
- 同一 raw artifact 可离线重画。PNG 用于聊天预览、SVG 用于报告；UI 通过工作区授权文件引用读取，不将 base64 图像塞入默认模型输入。SVG 按不可信文件处理，不直接插入可执行 DOM。

Agent 以这些指标解释方向性、仰角和使用场景，不据天线增益保证通信距离；卫星、DX、本地通信的传播、对端和安装条件仍是外部假设。

## 7. 实际材料与可制作性

材料是模型输入，不是最后报告里的一句“使用铜线”。不要求用户提供全部专业参数，由 Agent 从明确型号/数据表/测量补齐；无法确定时记录范围与假设，再判断是否影响设计结论。

| 对象 | 必需记录的工程信息 | 缺失时的处理 |
| --- | --- | --- |
| 导线/管材 | 材料/合金、实测直径或半径、长度、必要时壁厚/镀层/磁性、电导率或电阻率来源与条件 | 不把铜包钢、铝管、不锈钢均视作理想铜线；不支持的材料模型显式拒绝或限定近似 |
| 绝缘与支撑 | 绝缘外径/材料、可用介电参数、安装接触位置；支架、boom、墙面和近邻金属 | 简化细线后端不能精确覆盖所有介质/表面耦合；必要时换模型或要求现场验证 |
| 馈线和接头 | 型号、长度、阻抗、损耗随频率、速度因子、转接件/连接方式 | 馈点阻抗与电台端输入分开报告；不能把匹配网络/馈线后的 SWR 当裸天线 SWR |
| 电感/电容/陷波器 | 实际 L/C、Q/ESR、工作频率、公差、自谐振和额定电压/电流/温升条件 | 不能以无限 Q 替代未知器件；损耗/功率承受不足列为阻断项 |
| 制作与安装 | 下料/装配公差、搭接和接点、拉线/下垂、架设高度/方向、应力与户外防护 | 执行敏感性分析；未进行机械/热分析不得声称结构与功率安全已认证 |

有限电导可利用 NEC LD；pymininec 有 skin-effect/insulation load，但这不证明所有管材、镀层、介质损耗都已支持。公开 `Insulation_Load` 接口只有半径与相对介电常数，不能凭它宣称已包含任意损耗角正切。模型能力应逐项声明。[NEC LD](https://www.nec2.org/part_3/cards/ld.html)、[pymininec 材料负载实现](https://raw.githubusercontent.com/schlatterbeck/pymininec/master/mininec/mininec.py)

每条材料依据记录来源、日期/版本、单位、频率/温度等适用条件和测量不确定度。替换材料必须产生新 revision 并重算相关结果。至少比较标称值与关键尺寸/材料范围；结果对未知项敏感时，先提出测量或采购确认，不将假设冻结成已验证事实。

制作包给出材料清单、尺寸图、参考轴、端点/馈点位置、连接/匹配方式、预留修剪量与调试顺序。测量计划说明仪器能力、校准面、夹具/馈线处理、扫频范围与安装状态；VNA 的匹配测量不能单独验证方向图或绝对增益。未实测的性能明确标为预测；不自动控制电台发射。

## 8. MCP 接口与计算生命周期

保留高层 `antenna.model_create / model_validate / solve / sweep / pattern / optimize / export`，实际注册名遵从既有 MCP 名称规范化。`model_create` 可接规范模型或 `.maa` 导入；`export` 负责 `.maa` 与报告包，避免另外生成几十个 CRUD 工具。

- `model_validate` 返回结构/语义/适用性检查，不等同于用户需求验收。
- `optimize` 一次接收 variables、bounds、objectives、constraints、seed 与计算预算。数值搜索复用成熟算法（实施时评估 SciPy 或候选后端优化能力），内部绝不调用 LLM，不使用可执行表达式或任意 shell 目标函数。
- 长计算必须有 job_id、输入 hash、进度、结果引用与明确取消语义。优先核实所用 SDK 的稳定任务能力；若不适合，只为计算增加一个 `antenna.job(action=status|cancel)` 入口，原生 Task/子进程机制承担生命周期，不扩展成另一套工作流平台。
- 提交身份由可信宿主生成并绑定项目/版本，不以模型编出的 ID 授权。重复提交先核对身份与参数；超时/重启查询结果，不盲目重复优化。
- Server 持有并回收 solver 进程；请求取消只表示本地已停止等待，直到服务器确认计算已结束才标记 job cancelled。不承诺 SDK 当前没有的远端取消保证。
- CPU/内存/时间/输出文件量与 solver_runs 分别设限，Windows/Linux 的实际限制能力单列；独立 MCP 进程不等于 OS 沙箱。

只返回有界 summary、重要 warnings/diagnostics 和授权 artifact 引用。`pattern` 的 cut/region 查询支持角域、极化、频率、分页/抽样上限；只需要指标时不返回数组。请求完整矩阵返回文件引用，不进入 ToolMessage。日志在失败诊断时按范围读取、限量并脱敏。

## 9. Token 与计算预算

### 9.1 区分决策回合和实际请求

目标是通常 5–8 个有意义的设计决策回合，不承诺固定 token 数。一次回合可能包含工具返回后的模型请求；重试、Schema 修复、摘要、安全审查及可选子 Agent 都消耗预算，不能从统计里消失。默认不为该工作流启动子 Agent。

建议首个预算 profile 如下，均是待测默认值，不是当前配置项或实测最优值：

| 限制 | 建议初值 | 计数边界 |
| --- | --- | --- |
| max_llm_rounds | 8 | 高层决策回合，跨聊天回合累计 |
| max_model_requests | 16 | 所有实际模型请求尝试，包括辅助调用和失败；SDK 隐藏重试不得绕过 |
| max_total_model_tokens | 64000 | 累计输入/输出预算；缓存输入仍属于上下文 token，不按免费处理 |
| max_design_iterations | 3 | 包括初始正式设计，最多两次 LLM 设计级修正 |
| max_concepts | 3 | 保留的概念方案 |
| max_simulated_concepts | 2 | 一个主方案、必要时一个备选，共享总预算 |
| max_history_tokens | 1536 | 模型本次请求的最近必要对话，不是持久历史上限 |
| max_tool_result_tokens | 1536 | 单次反馈上限，另设同次模型请求的工具反馈总额 |
| max_solver_runs | 1000 | 全工作流累计频率点求解次数，扫频/重网格/重试/验证都计入 |

每次调用还必须有输入与输出上限；取用户预算、阶段预算、剩余总额、模型实际窗口/输出能力的共同约束。Brief、当前设计、工具 Schema、Skill、最近消息均计入输入，不能只限制 history。预留验证与收尾预算，优化器不得耗尽全部额度。

执行前原子预留请求额度及输入估算/输出上限，返回后用 Provider usage 对账。没有精确 tokenizer 或 Provider usage 时标明 estimate，保守保留预留额度，不能记为零或宣称计费级精确硬上限；预算身份与已耗额度跨重启、重试和新聊天回合保留。不可控制的 SDK 自动重试须关闭或纳入最坏尝试数。输出/推理上限的实际可控性按 Provider 能力报告。

达到限额由确定性代码拒绝新请求，保留结果并说明冲突，不能再追加一轮“最后总结”偷偷超支；收尾额度不足时由模板呈现已有结论。用户显式增加预算形成有审计的变更，不靠开始新 task 重置额度。

### 9.2 Prompt 与数据流

模型主要读取当前结构化 state、当前阶段 Skill、最近未被吸收的用户信息与最新 compact result。确认过的原话在产品历史中保留审计，但不与 Brief、摘要三份重复注入；必须保留待完成的工具调用/结果配对和未处理用户纠正，不能靠字符串截断丢掉协议语义。

阶段资源只加载访谈、方案选择、建模、验证或制作中的必要部分。MMANA 格式、求解数学和材料数据按主题检索，首版用本地 Skill resources/明确章节，不为此建设向量平台。固定前缀/工具 Schema 稳定排列，变化 state 放后部；不为缓存命中保留已失效材料或无关历史。

优化器执行数百次计算只返回有限的最佳/Pareto 候选摘要与完整结果引用；solver log、迭代日志、矩阵、完整方向图默认永不进入 prompt。matplotlib 直接读取 artifact，不需要 LLM 给每个采样点写绘图指令；PNG 展示给用户也不意味着自动送进视觉模型。

`compact result` 至少包括 revision、状态、频率范围、最差/标称 SWR、馈点阻抗摘要、绝对增益、主瓣/切面波束宽度、F/B/副瓣及其定义、硬约束违反项、重要假设、材料风险、数值与兼容验证状态、artifact 引用和消耗。必须保留关键失败条件，不能因限长把 warnings 截掉伪装成功；超限改为分类摘要与细节引用。

### 9.3 Telemetry

复用现有 model.call/model.usage 及 Repository，关联 workflow_id、stage、brief/model revision、request/job ID，补齐跨回合汇总；不新增事件总线或独立账本框架。

分别记录实际/估计 input_tokens、output_tokens、已提供的 cached/reasoning token、model_requests、llm_rounds、MCP calls、solver_runs、solver 时间/峰值资源、每阶段 token、失败/重试与剩余额度。Provider 已含在 output 中的 reasoning token 不再次相加。一次进程包含多个频点时按频点计 solver_runs，MCP status 查询只增加 MCP calls，不增加 LLM calls。

算力预算与 token 预算相互独立：禁止用更多 LLM 往返替代便宜的数值迭代。profile benchmark 同时记录设计正确性、MMANA 差异、材料/制造约束、求解时间和阶段 token；不以最低 token 单项排名牺牲结果质量。

## 10. 可复现 artifact 与参考验证

### 10.1 制作包

```text
design.maa
design_brief.json
model.json
materials.json
simulation.json
reproduce.json
manifest.json
validation.json
construction.md
pattern/
  far_field.json
  azimuth.svg
  elevation.svg
  azimuth.png
  elevation.png
logs/
  solver.log
```

所有文件绑定同一设计 revision；manifest 记录相对路径、MIME、大小与内容 hash。路径必须在当前项目根内，拒绝穿越与符号链接逃逸；重新渲染生成新 artifact，不覆盖旧证据。

`reproduce.json` 记录 solver/compatibility/renderer 版本、输入文件、单位、材料来源、网格/pulse map 引用、频率/角网格、地面/激励、优化器算法/版本/seed、停止条件、实际候选序列或最终选中输入、依赖锁/原生构建/数学库及重算命令。不得让用户下载后执行任意随模型提供的命令；使用已安装后端的固定入口和参数校验。

明确分开“重算最终设计”与“重跑优化搜索”。前者必须可独立执行；后者受并行调度/浮点/时间停止条件影响，不能只保存 seed 就承诺完全相同。兼容回归固定数学库、精度、线程数与评价次数；跨平台比较采用有依据的误差范围，不默认 bit-for-bit。

### 10.2 Oracle 数据与差异指标

参考样本须记录 GAL 版本/二进制 hash、OS/Wine 版本、locale、完整 `.maa`、隐藏/全局设置、激励、电压相位、地面、材料、分段、参考阻抗、角度采样及匹配/插值选项。直接导出数据；不以截图、手册示例或另一求解器输出冒充 reference。Windows 与 Wine 数据先独立核对，再决定能否归入同一 profile。

先比较语义，再比较数值；源/负载映射或网格不符时直接报告，不靠电流插值或整体旋转掩盖。

| 对象 | 差异报告 |
| --- | --- |
| 阻抗 | 每频点 R/X、绝对和相对误差；近零分量不用相对误差单独裁决 |
| SWR | 相同参考阻抗与参考面，保留原始值、无穷/无定义状态和带宽/谐振偏移 |
| 电流 | 对应 pulse 的复电流、幅相与绝对/相对误差；明确电压幅值、方向与相位参考 |
| 方向图 | 相同频率/角域/角网格/极化/地面下，逐点绝对 dBi 差、RMS、最大误差及对应角度 |
| 派生指标 | 最大 gain、主瓣方向、切面波束宽度、F/B、主要副瓣差异；并列峰值不能强行认定唯一方向 |
| normalized pattern | 归一化基准与 peak 的差异单列；不能分别归一化后掩盖绝对增益偏差 |

规则网格的 RMS 定义为有效成对样本差值的均方根；如果另报球面积加权误差，单独标识权重，不能与普通 RMS 混用。深零点的 dB 差可能失真，应同时报告线性功率误差与预先声明的比较下限/掩码和有效点数；不删除失败角度或后调阈值。采样不一致时先报告 mismatch，必要的显示重采样另存，不能计入同网格兼容通过。

数值兼容与物理收敛分别验证。为对齐 oracle 固定网格进行兼容对照；另加密网格/角域做工程可靠性检查，不用“网格更细所以不同”免除兼容要求。误差阈值按测量精度、导出分辨率与工程敏感性预先制定；当前没有运行数据，不填写虚构的误差或通过率。

### 10.3 参考样本覆盖

首组选择可诊断的少量模型而非大量随机设计：自由空间偶极子、V/弯折、闭环与多线接点、地接垂直、低架水平与不同 ground、VHF/UHF Yagi、异径/单端 taper、源偏移与多源相位、RLC/Q/陷波/Laplace、多频扫频。另包含不支持介质/面结构、非法几何、预算耗尽和原始数据缺失的拒绝案例。

先用极简样本隔离分段、pulse、负载与单位差异，再用代表性天线验证组合行为。每类同时记录兼容覆盖和工程适用范围；不能用 HF dipole 的通过替代 UHF Yagi、加载天线或任意自定义结构。

## 11. 下一步选型门槛

本轮只交付设计与静态评估。后续如获准进行小规模验证，按以下顺序决定实现路径：

1. 冻结一个 GAL reference 版本与合法采集环境，建立原始数据/配置清单；先确认 `.maa`、CSV 与所需设置可完整导出。
2. 对同一极简模型先比较文件、分段、pulse、激励和负载，不先调结果误差。pymininec 与原始 MMANA 都检查；NEC 路线用于区分适配错误与方法差异。
3. 比较数值、材料、方向图与执行成本；确定 Windows/Linux、Python 3.14、超时/取消、重算和预算能否满足产品要求。
4. **直接采用**：所需语义和误差均满足，无需维护内部算法补丁。
5. **组合与兼容适配**：先组合已存在的 solver、MCP、优化器与格式处理能力；剩余差异限定在文件、分段、源/负载映射与明确的结果转换。优先公开 API/上游贡献，不长期覆写私有数值内核。每项补缺提交第 2.3 节的差异记录。
6. **提取兼容核心**：只有重现实验证明关键差异发生在现有后端不能适配的内核，并且原始源码确能改善目标兼容、许可清楚、维护成本可承受时采用。仅补或替换已定位的不兼容计算部分，保留其余已验证组件；必要时才提取更大的核心。先保留计算行为移除 GUI 耦合，不同步重写公式或优化算法；仍须 GAL oracle 对照。

若没有候选满足硬要求，结论是尚不可交付 MMANA-compatible 模式，而不是扩大容差、降低材料要求或以图片相似冒充成功。兼容实现不合适时应收缩明确支持的模型特性，而非静默降级。

首版最终交付必须能从制作包重算，直接展示 2D 图并打开原始文件，报告材料与模型限制，并在预算内完成或明确说明不可行。没有生产后端、真实兼容回执和工作流闭环之前，不在产品中展示已完成的天线设计模式。
