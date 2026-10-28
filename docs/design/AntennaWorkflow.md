# 业余无线电天线工作流与求解器评估

> 本文说明独立天线包的工程契约、MMANA 兼容范围、方向图、材料和预算，以及求解器选择。
> 当前采用未修改的 pymininec，通过公开接口适配，不自研数值内核。MaigoKite 是可选宿主，接入遵循 [运行时设计](AgentRuntime.md) 和 [MCP 集成](../agent-capabilities/mcp/mcp-integration.md)；领域包不依赖这些产品 API。

当前实现包括 [独立 `antenna-design` 工程入口](../../extensions/antenna_design/README.md)、可选 MCP Server 和宿主结果适配。它接受显式 JSON 或受限 MAA 输入，通过公开 solver API 计算，保存复电流、原始远场、PNG/SVG 和完整性清单，并限制单次计算进程的运行时间。经批准后，宿主可将结果导入当前对话目录并展示图片。包不依赖主应用或 LangGraph；Linux 的计算、取消、取回和导入已有定向运行记录，不代表完整工程验证或 Windows 验收。

已有数值可以取回、补画或导出，不必因缺图重新求解。数值阶段先保存并校验，绘图失败不丢弃完整数值；`render_status` 与工程验证状态分开。随包 Skill 用现有文件工具记录需求、输入版本和 run_id，供后续继续，不充当阶段执行器。MCP 工具超时可按服务设置。

一个操作员指定的结果根目录对应一个项目。独立 SQLite 保存求解准入和限额，准入先提交，失败或取消不退回，跨回合和重启保留；查询、复用和补画不扣次数。`antenna.project_status` 根据项目记录、当前 AnyIO owner 和产物校验返回状态，不另存一套 worker 状态机。LLM/token 预算、完整阶段准入和 AV680 加载结构支持仍未实现。

2026-10-19 的求解器评估已完成 A01/A02、六例主频和 23 项多导线/扫频参考的对照，也检查了冻结接线的复电流幅相及五点材料表示。这些结果支持继续采用 pymininec，但正式工程验收 profile、方环缺失接点和更广的材料/结构覆盖尚未完成，不能称为全面兼容认证。详情见[工程评估](../../experiments/mmana_compatibility/ENGINEERING.md)及[实验范围](../../experiments/mmana_compatibility/README.md)。

独立 MCP 已提供“规划次数→尺寸搜索→两档网格复核→补画→候选包”，内部不调用 LLM。自动需求访谈、工作流级 LLM/token 预算和完整制造验收仍未交付。Linux 恢复验收包括两个 Core 进程间审批续接、MCP 重启取回、补画/取消、冲突保护、图片 API 和历史工具事件；25 项恢复检查没有新增求解。后续搜索交付及固定匹配另有定向记录，不能与自然语言自主编排、完整 GUI、Windows 或强杀恢复混为一项通过，详见 [路线图](../project/Roadmap.md#2026-10-23-天线恢复与生产调用验收)。

绘图证据与计算证据分别记录：原 `environment.json` 随数值保留；`render-environment.json` 与 `render-source/antenna_workflow/` 记录当前绘图实现和依赖。补画准备失败在已创建的新目录内尽力保存安全失败回执，不覆盖源文件、不退还预留或自动重放。

`antenna.evaluate` 比较同一几何、馈点和数值设置的已有频点，从原始样本读取前向增益与 F/B，不拿峰值或图片显示值代替。参考值、设计目标、原生 R+jX、直连 SWR 和匹配后结果分别报告。缺失点与下一批提案按项目预算快照计算缺口，不启动求解、不预留次数。输入要求见[目标评价](../../extensions/antenna_design/README.md#计算后的目标评价)。

配套 Skill 将“性能目标原创设计”与“已有实物复算”分开：初始设计不再被原厂尺寸阻塞；每次计算后保存指标差额、证据和下一步。预算用尽不阻止评价、候选文件导出/取回或准备下一批，但不自动加额度；三频中心点仍不能代表全带性能。

CLI `connect` 生成本机 MCP + Skill 联合配置；宿主使用原设置 API 保存和应用，同名覆盖与工具范围变化必须确认。`antenna.export / get_delivery` 将同设计的已有数值整理成固定文件和 ZIP，服务保存在 `.deliveries`，不增加求解次数。宿主只校验并导入资源，通过鉴权接口下载，不解压 ZIP。原 Runtime、Repository 和审批不变，交付状态仍为候选未验证。

用户可微调频点并提供架设条件：`prepare_revision` 读取旧模型，显式更新 targets、刚体旋转、馈点高度、地面和材料；旧 run_id 不可继续充当新结果。建筑/屋顶/桅杆文字只保留为未模拟条件，不以自然语言注释替代物理模型。逐频匹配使用独立 `matching` 电路入口，复用 scikit-rf Circuit 与 SciPy least_squares；输入为已核验的原生 R+jX，输出拓扑和理想 L/C，并通过 design/export 进入报告及 ZIP。各频点须切换或调谐，不是固定多波段网络，不改变原辐射评价或制作状态。[成熟组件依据](https://scikit-rf-official.readthedocs.io/en/latest/examples/matching/Impedance%20Matching.html)

固定元件复核使用 `evaluate_matching`：相同 L/C 应用于各个已保存的负载阻抗，不逐频重调、不猜测点间阻抗。可选 SWR 门槛来自调用方，缺省只报告数值。export.matching_checks 归档每组输入、逐点结果、辐射对照和原始证据，且要求与交付模型的物理 hash/系统阻抗一致。它验证的是理想电路的离散样本，不改变 radiation_status，也不证明连续带宽、元件损耗、公差、巴伦或功率。

补充判断：四变体总场 RMS 主要受 CSV 的 0.01 dB 导出步长影响；候选同样保留两位小数后有 98.5%～100% 的有效样本与参考相同。该量级不构成继续修改内核或自研求解器的依据。保留电流/电抗差异和未覆盖语义，不把当前模型的方向图接近等同于全面 MMANA 兼容。

## 1. 产品目标与不可替代的交付条件

用户讲用途、设备、场地、材料与现实限制；Agent 负责将其转成工程约束，选择方案、解释取舍，并判断计算证据是否支持制作。用户不必先知道天线类型、增益、前后比、馈电阻抗或单元数。

生产链路采用独立 MCP Server 和可脚本化求解器，不启动或自动操作 MMANA-GAL GUI。MMANA-GAL 仅在开发验证环境中作为 reference oracle；Windows/Wine 采集的参考结果与生产包分开管理。

首版范围是适合 thin-wire 方法的导线/细导体结构，覆盖 HF、6m、VHF、UHF 的适用模型。dipole、inverted-V、OCF/folded dipole、vertical/ground plane、J-pole、loop/delta/quad、Yagi、Moxon、collinear、phased array、加载/陷波、多频与自定义几何均属于目标范围，但不能把“几何能表达”标成“馈电、材料与数值行为已验证”。PCB、微带、喇叭、波导、复杂面导体、介质天线和手机内部天线不进入本后端。

实现顺序固定为直接集成、组合现有组件、薄适配，最后才是补缺。只有现有组件不能满足已定义的 MMANA-GAL 可验证兼容性要求时，才补对应缺失部分；代码组织偏好不构成重写理由。

天线核心工作流不得依赖 MaigoKite 专有 Runtime API，也不得依赖 LangGraph、LangChain 的状态、消息或执行对象。同仓库开发不改变依赖方向：宿主适配领域包，领域包不导入宿主。LangGraph 仅可作为可替换的执行适配器，不是核心包的必装依赖。后续独立建仓只移动包、资料与构建元数据，不需要重新设计领域流程、数据契约或执行边界。

最终方案必须同时具有以下证据，不能用其中一项替代其他项：

- 可复现：明确模型、材料、求解配置、软件版本与重算入口。
- 数值可靠：有适用性、收敛性、扫频及制造偏差分析。
- MMANA 可验证：可导入的 `.maa`、物理语义映射及对应兼容 profile；核对阻抗、带宽、电流分布与方向图的工程结论，不仅比较最大增益，也不要求逐点数值相等。
- 可制作：具体材料、尺寸、公差、接头、馈电匹配、安装与调试说明。
- 可观察：保留原始方向图、计算证据、阶段预算和实际用量，不能只返回自然语言与图片。

“可验证”不等于“已实测”，“仿真收敛”不等于“满足需求”，“匹配良好”不等于“辐射效率良好”。实物验证另列状态，不由 LLM 自行宣布完成。

## 2. 求解器评估结论

### 2.1 候选与复用边界

| 候选 | 已查到的可复用能力 | MMANA 兼容缺口与接入成本 | 当前判断 |
| --- | --- | --- | --- |
| pymininec | MININEC3 路线；Python API/CLI；pulse 激励、负载、地面、远场 | 自带 taper 与 MMANA 参数不等价；需核对物理馈点/接点、材料/负载、独立网格收敛及部署成本 | 技术选型采用 1.2.0，明确子集的独立适配已编码；不要求重现 GAL 内部网格，尚未生产验收 |
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

本次参考端采用用户于 2026-10-19 提供的 **MMANA-GAL v1.2.0.20**，候选求解器为 **pymininec 1.2.0**，两者版本不能混用。此前调查的其他 GAL 发布版不代表本次采集版本，也不要求用户升级后覆盖当前参考数据。版本来源和对应输入/结果 hash 记录在 [reference_identity.json](../../experiments/mmana_compatibility/reference_identity.json)。正式冻结 reference 时再补可执行文件 hash、配置和运行环境；当前版本信息足以继续差异分析，不以缺少二进制 hash 暂停数值调查。

来源许可也必须在采用前核清：原始 MMANA 仓库 LICENSE 是 LGPL-2.1 文本，而相关源码头声明 LGPL-3.0-or-later；GAL Basic 官方页面限定私人非商业用途。这里记录来源差异，不作许可证兼容性结论，不下载分发 GAL，不复制代码进入主项目。独立进程不自动消除许可证义务。[MMANA LICENSE](https://github.com/ja7ude/MMANA/blob/master/LICENSE)、[源码声明](https://github.com/ja7ude/MMANA/blob/master/mininec3.cpp)、[GAL 使用范围](https://gal-ana.de/basicmm/ru/)

### 2.3 复用决策与最小补缺

| 能力 | 优先采用路径 | 领域包与宿主的边界 |
| --- | --- | --- |
| 电磁求解 | 候选 solver 的公开 API、CLI 和现有输出 | 参数映射、适用性检查及结果单位/坐标规范化，不重新实现矩阵求解或远场积分 |
| MCP 工程接口 | 评估现有天线 MCP 能否配置、扩展或贡献上游；不足时以官方 SDK 薄封装现有 solver | 项目作用域、compact summary、artifact 引用与取消回执，不重写 MCP 协议 |
| 数值优化 | 先检查所选 solver 已有优化器；不满足目标/约束时组合现有优化库 | 有界变量、目标、约束、计算预算和候选评价函数，不重写优化算法 |
| 格式与 MMANA 语义 | 先找可复用的解析器、导出器和前处理实现，再验证精度与信息保留 | 仅补有证据的 `.maa`、segmentation/pulse、source/load 等缺口，不为统一接口重写已有转换器 |
| 图表与数值比较 | matplotlib、现有科学计算库；宿主复用各自的文件预览 | 领域包提供坐标/单位契约、比较规则和 artifact；宿主提供显示，不反向依赖 GUI |
| 工作流执行 | 宿主复用现有执行能力；LangGraph 是可选适配，不是领域依赖 | 领域包拥有数据契约、阶段准入与预算规则；宿主承担暂停恢复、权限和存储，不另建运行框架 |

某个现有 MCP 仅支持少量模板，是扩展其模型输入与输出的调查起点，不是推倒重写的理由。MCP 层不合适也不否定其底层 solver；优化器不合适不否定求解器。逐项组合组件，避免“整套不完全匹配，所以整套自研”。表中的候选需要核实公开接口、版本、维护状态和许可，不将尚未找到的解析器当作已存在的依赖。

每项拟新增的兼容实现必须附一份简短差异记录：使用了哪个现有组件及版本、复现输入与 reference 条件、缺失行为或误差、为什么配置/公开 API/组件组合无法解决、最小补丁范围、回归证据和维护/退出条件。先修映射错误，再判断是否需要算法改动；不以“代码更整洁”“接口不统一”或“自己写更方便”通过门槛。领域需求和验收规则由产品定义，不借此重做通用基础设施。

### 2.4 具体组件与接口核查

| 组件 | 可复用入口与证据 | 采用边界 |
| --- | --- | --- |
| pymininec | `register_source(source, pulse, geo_tag)` 使用从零开始的 pulse 索引；`compute_far_field(zenith_angle, azimuth_angle)` 与 `Far_Field_Pattern` 提供角网格、复场和增益数组 | 不解析格式化日志来取代原始数值；先核对 pulse 映射与输出轴顺序。精确自定义网格是否有稳定扩展入口仍未确定，不直接改写对象内部字段 [源码](https://raw.githubusercontent.com/schlatterbeck/pymininec/master/mininec/mininec.py) |
| plot-antenna | 提供公开 `Gain_Data.from_gains` / `Gain_Plot` 用法，支持方位/仰角切面、指定切面角度和无窗口文件输出 | 优先评估复用其 matplotlib 路径。需核实所选版本的 PNG/SVG、归一化、依赖和任意切面支持；不能为现有两个切面重写绘图库 [项目与 API](https://github.com/schlatterbeck/plot-antenna) |
| xnec2c 内置优化 | 支持 batch/headless，Simplex/PSO、加权目标与 `.opt` 配置 | 如选 NEC 路线先评估；加权目标不是 Pareto 保证，最大迭代数也不是频点求解次数硬限额 [手册](https://www.xnec2c.org/) |
| antenna-optimizer | 通过 PyNEC 与 PGApack/pgapy 做天线优化，已有多目标/NSGA-III、约束与馈线建模 | 适合 NEC 路线调查；不是可直接替换成任意 solver 的通用入口。需计入 pgapy/PyNEC 原生依赖成本，不与另一个优化框架同时默认安装 [项目](https://github.com/schlatterbeck/antenna-optimizer)、[依赖声明](https://raw.githubusercontent.com/schlatterbeck/antenna-optimizer/master/pyproject.toml) |
| SciPy differential_evolution | 支持 bounds、constraints、rng 和 callback，适合将已有 solver 作为评价函数 | 不新写搜索算法；仅当后端自带优化不适合时组合。其单目标输出不能包装成真正 Pareto 前沿 [官方 API](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.differential_evolution.html) |
| YAAS | 已有 `.maa` 导入、项目文件、NEC 导出和实测扫频比较；方向图功能属于其开发分支范围 | 导入限制为自由空间、单个居中无偏移源、无集中负载等，并明确采用均匀 NEC 网格近似 MMANA taper。可调查格式处理/拒绝规则复用，不能作为严格兼容核心；导入不代表支持 `.maa` 导出 [项目说明](https://github.com/ftomasczik/yaas-antenna-simulator) |
| mcp-nec2-antenna | 九个工具覆盖五种建模模板、仿真、NEC 导出与查询 | 已列出的接口没有通用 wire 导入、`.maa` 往返或优化入口；先查扩展点，不将缺少入口误判为必须重写 NEC 调用 [工具列表](https://github.com/RFingAdam/mcp-nec2-antenna#tools) |

这些是具体复用候选，不因 README 自称稳定或存在发布版本就判为已满足生产条件。采用时固定 release/commit、依赖锁和许可证清单，并核实 Python 3.14 与 Windows/Linux；网页指向 `main/master` 的本次调查不能充当可复现的构建锁。YAAS 文档声明的 Python 3.13 环境尤其不能直接计为本项目 3.14 验收通过。

新增两个必须在适配入口暴露的行为：pymininec `Wire.compute_segments()` 捕获 `Taper_Error` 后会退回均匀分段，因此必须记录实际网格和回退，重新检查适用性/收敛，而非声称请求的 taper 已执行；其远场增益对低于阈值的样本使用 `-999`，应保留原始标记并转换为“低于数值下限”，不得当成真实 -999 dBi 或确定的物理零场。[对应实现](https://raw.githubusercontent.com/schlatterbeck/pymininec/master/mininec/mininec.py)

求解器采用 pymininec，独立薄适配已实现；nec2c 仍是未接入的交叉检查候选。两个静态切面使用 matplotlib，后续交互 3D 优先复用现成工具。参数搜索已选择 SciPy；antenna-optimizer 的 PyNEC/PGApack 路线会额外引入求解器和原生依赖，不在当前路径同时安装。现有天线 MCP 绑定 NEC 不构成重建多 solver 平台的理由；目前也没有证据支持自研数值内核、优化算法或绘图库。

### 2.5 采用组合与停止条件

当前组合是 `pymininec + 必要的 MMANA 语义适配 + SciPy + matplotlib + 官方 MCP SDK`，匹配计算另复用 scikit-rf。它已有独立工程入口和参数搜索，不代表整个产品栈已通过 GAL 验证。NEC 路线仍作为独立对照候选；只有相应兼容 profile 通过，才能作为该范围的生产后端。

| 方案 | 预期维护面与成本 | 决策门槛 |
| --- | --- | --- |
| 直接使用 mcp-nec2-antenna | 接线成本低，但通用几何、持久化、预算和严格兼容仍有缺口 | 先核实公开扩展点与上游合作；不能用五种模板替代完整目标范围 |
| 组合现有 solver、优化与绘图组件 | 中等适配成本，保留领域契约与数值语义映射，不维护数值内核 | 物理位置、连接、材料和场定义正确，独立网格收敛，工程结果满足选定 profile；否则暂停该 profile |
| 提取原始 MMANA 计算部分 | 成本高，需承担历史平台耦合、数值维护与来源许可核对 | 必须先证明前两条路径不能满足关键语义，且提取确有收益；当前不进入实施 |

成熟度分层判断：MCP 官方 SDK、LangGraph、SciPy、matplotlib 提供可复用基础设施；专业 solver/格式工具仍需各自的数值和平台证据；天线 MCP 的存在不等于它已经是满足本工作流的成熟完整实现。不以 star 数、README 示例或项目自述替代验证。

现有 MCP 的架构文档明确使用内存模型映射，重启丢失；当前 README 已声明从 Apache-2.0 改为 AGPL-3.0-or-later，而架构页仍保留旧许可文字。采用前必须锁定具体提交并核对 LICENSE，不能从旧说明推定可直接复制。这里不作许可证适用结论。[架构](https://github.com/RFingAdam/mcp-nec2-antenna/blob/main/docs/architecture.md)、[当前项目说明](https://github.com/RFingAdam/mcp-nec2-antenna)

## 3. 架构与状态所有权

下图包含完整领域流程的目标结构；当前只实现其中的工程计算、项目准入、资源交付和宿主适配。Brief、阶段规则与模型决策接口的实现范围在后文分别标注。

```text
MaigoKite UI / Agent                    独立 CLI / 其他宿主
          ↓                                   ↓
MaigoKite adapter                      对应的薄 adapter
  可选 LangGraph 执行                     CLI / 其他执行方式
          └───────────┬───────────────────────┘
                      ↓
不依赖执行框架的 antenna_workflow 核心
  ├─ 领域契约、Design Brief、阶段规则与预算
  ├─ 按阶段加载的领域资源与决策请求
  └─ 工程请求 / 有界结果 / artifact 契约
                      ↓
工程适配器：官方 MCP SDK 或本地公开调用
                      ↓
独立天线 MCP → 现有 solver / optimizer / MMANA compatibility
                      ↓
原始计算 artifact → 数值验证 / 有界摘要 / PNG、SVG / 制作包

开发验证环境：MMANA-GAL → reference artifact → 同一差异比较器
```

### 3.1 包边界与依赖方向

领域层不新增常驻专家 Agent。需求、候选取舍、材料依据和结果判据属于领域契约；已接入的输入与求解预算由确定性代码检查，完整阶段准入仍是后续目标。调用模型、调度聊天、暂停与恢复由宿主承担。核心函数和类型不含 LangGraph、LangChain、MaigoKite 或前端 DTO，不注入整个 Runtime，也不重建通用执行引擎。

同仓库的 `extensions/antenna_design/` 已建立独立 Python distribution：自有 `pyproject.toml`、`uv.lock`、LICENSE、README、输入示例和 `src/antenna_workflow/`。基础安装只含 Pydantic，求解/绘图放在 `engineering` extra，官方 MCP SDK 放在 `mcp` extra，不进入普通聊天默认安装。当前工程 CLI 与可选 MCP stdio 入口共用 worker；没有创建尚未实现的领域阶段、持久化或执行框架空壳。MaigoKite 结果 adapter 已位于主应用集成层，只按版本化 MCP 传输回执接线。

MCP 当前注册 16 个高层工具，完整清单和参数只在[独立包说明](../../extensions/antenna_design/README.md#高层工具)维护。`solve` 一次只做一次数值计算；`optimize / design` 可在项目限额内执行多个候选和频点。单实例只有一个计算槽，默认 worker 时限 20 秒，宿主工具时限默认 30 秒，可分别配置。

`get_result` 取回原 run_id，不需原始输入；`render` 将核验过的数值复制到新 output_run_id 后补画，不调用 solver、不改旧目录。数值完整但图片不完整时可先发布数值，缺图不进入资源清单。`cancel` 按运行编号和可选调用身份停止 worker，清理后才确认；活跃状态来自进程所有权，历史结果来自磁盘校验。Linux 已有真实 MCP/solver 和生产 Core 显式工具记录，不等于自然语言、GUI 或跨平台验收，也没有注册主应用专用天线模式。

`antenna-design` 是本地 distribution 名，`antenna_workflow` 是导入命名空间，不表示已经公开发布。后续包内职责如下；这是实现边界，不要求一次创建全部目录或空类：

| 包内位置 | 拥有的职责 | 不应依赖 |
| --- | --- | --- |
| `contracts` / `domain` | Brief、材料依据、候选、阶段准入、预算与结果判据 | MaigoKite、HTTP/UI、LangGraph 执行对象 |
| 可选执行适配器 | 将领域步骤交给现有执行器；MaigoKite 可用 LangGraph，独立 CLI 不必使用它 | 核心反向导入、`AgentCore` 私有方法 |
| `project_store` / `workflow` | 标准 SQLite 保存项目身份、求解准入、补画来源、额度修改，以及有限设计作业的已提交证据里程碑；完整 Brief 仍待实现 | 主应用 Repository、聊天 checkpoint 或活跃 worker 状态 |
| `identity` | 分别标识物理设计、仿真输入及比较口径；保留旧 hash 语义 | 以当前依赖版本补造历史数值来源 |
| `assessment` / `delivery_render` / `delivery` | 分别负责工程证据判断、文档绘制、证据完整性与不可变归档 | 导出层私自调整目标、重跑计算或授予制作验收 |
| `engineering` | 所选 solver/optimizer 的公开接口适配、计算产物与高层 MCP handlers | 聊天历史、LLM 决策、宿主权限实现 |
| `mmana` / `patterns` | 必要的兼容映射与差异规则、现有绘图组件的有界适配 | GUI 自动化、图片反推原始数据 |
| `resources` | 分阶段领域知识、Schema、材料/兼容资料 | 宿主 SkillRegistry 才能读取的格式 |

MaigoKite 的 `python/agent/integrations/antenna.py` 负责结果契约、权限查询、产物落盘和本次计算的停止确认，不导入独立包或实现求解。显式选择 `result_adapter=antenna_design` 后，获准的 `solve / get_result / render` 导入计算结果，`design / export / get_delivery` 导入候选包；默认仍走通用 MCP。目录以命名空间、结果编号和资源 hash 标识，重复取回补齐同一副本，不覆盖用户修改，每次写入重新检查权限。模型侧的紧凑评价/搜索回执由 `antenna_receipts.py` 处理。文件、图片和下载复用现有工作区 API，步骤见[配置指南](../user-guide/Configuration.md#81-天线计算与方向图)。

LangGraph 依赖只存在于外部执行适配器，其 `thread_id`、interrupt、Command、checkpointer 不进入核心 API。MCP server 入口不导入执行适配器，也不强制安装模型 Provider；科学计算在工程进程执行。主应用 wheel 仅包含 `python/agent`，独立包有自己的构建元数据与资源声明，须独立安装，不能只把目录放进仓库就认为会随应用安装。[当前打包声明](../../pyproject.toml)

领域包及天线 MCP 不导入 `agent.core`、`agent.runtime`、`agent.repository`、权限管理器、全局配置或产品事件模块；不使用动态导入、环境探测或 `sys.path` 修改绕过边界。配置、模型调用能力、存储和受限文件访问由组合入口显式提供。导入包本身不启动服务、不连接 MaigoKite 数据库、不读取宿主密钥。

随包 `skills/antenna-design/SKILL.md` 可通过 `antenna-design skill` 查看或导出，不要求安装 MaigoKite SkillRegistry；宿主可登记为 Skill。当前内容是输入整理、文件记录与恢复操作指引；已强制执行输入校验、MCP 项目累计求解准入、单次时间限制、结果完整性与宿主权限。确定性阶段准入、LLM/token 预算和工程兼容判据仍待实现，不能用 Skill 文本替代。MCP 不选择用户偏好或替 Agent 接受结果；不建插件市场、通用 DI、Capability Framework 或另一套 Agent Loop。

### 3.2 最小外部契约

本节描述完整领域流程的目标契约，不表示所有边界已经编码。当前已接通工程 MCP、项目求解准入和宿主文件导入；独立的模型决策、Brief/阶段存储与问题映射仍待实现。

仅为实际 I/O 定义小型 typed callable 或必要的 `Protocol`，使用 dataclass/Pydantic 与标准 JSON Schema；不为每个领域对象创建接口或通用 ServiceContainer。契约属于领域包，宿主实现适配：

| 边界 | 领域包输入/输出 | MaigoKite adapter 职责 |
| --- | --- | --- |
| 模型决策 | 当前阶段、紧凑输入、结果 Schema、能力/窗口与预算；结构化决策、实际/估计 usage、明确错误 | 复用现有模型服务并报告每次实际请求；不传入 Core、会话对象或私有 Provider 包装。独立宿主可直接组合官方模型 SDK |
| 工程计算 | 有版本的模型/约束、操作 ID、计算预算、job 状态、compact result 与 artifact 引用 | 连接已授权 MCP；领域代码不依赖 ToolRegistry 或 ToolMessage，SDK 协议处理仍由官方实现承担 |
| 项目存储 | 加载设计项目、按预期 revision 提交变更，以及按 operation ID 原子预留/对账预算 | 显式提供授权项目位置与存储适配；Repository 仅记录宿主关联及投影，不暴露连接、表名或产品 record |
| 用户交互与产物 | 领域问题/选项、确认请求、阶段进度、错误码、授权结果与 `ArtifactRef` | 转成 QuestionCard、审批、稳定 UI 事件与文件查看；独立宿主可以使用终端交互和本地文件 |

普通领域追问与宿主安全审批是不同请求。审批可由宿主拒绝，拒绝结果返回可移植的明确状态，领域流程不能绕过它；host approval ID 与 LangGraph interrupt 类型只存在于适配器。没有交互或授权能力时返回待确认/不支持，不默认为批准。取消由执行适配器的原生 async 机制负责，工程任务仍须取得 MCP 停止回执；不复制 cancellation tree。

公开身份使用 `project_id`、revision 和领域 operation/job ID；conversation/task/workspace ID 仅由宿主维护映射，不作为领域流程的必填字段。`ArtifactRef` 使用项目内相对定位、MIME、大小和 hash，宿主 URL、访问令牌和绝对安装路径不写进可移植结果。存储及调用权限不因模型提供一个 ID 而获得。

### 3.3 状态与事务所有权

下表约定各类数据的所有者。当前项目库保存身份、求解准入与限额修改，不保存完整 Brief、设计阶段或模型调用预算；这些目标记录不能写成已有数据库能力。

| 数据 | 权威所有者 | 其他层持有什么 |
| --- | --- | --- |
| Design Brief、选择、阶段、确认记录与跨回合预算 | 一个权威项目存储保存领域契约定义的记录，具体实现可替换 | MaigoKite Repository 持有项目关联及展示投影；执行快照不能成为第二份可编辑记录 |
| 当前调用位置与等待用户的执行状态 | 当前执行适配器；若用 LangGraph，由官方 checkpointer 管理 | 核心只定义待回答的领域问题，不读取 checkpoint 或通过聊天文字猜测状态 |
| 权限审批与用户授权审计 | 当前宿主的安全策略与审计实现 | 领域流程接收授权结果，不镜像宿主权限状态机 |
| 几何、离散模型、solver 输入、结果和日志 | 显式配置的项目 artifact 存储内不可变 revision | project store 存引用/hash；宿主负责授权呈现，LLM 读有界摘要 |
| solver 进程与计算任务 | MCP Server 的本次计算资源 | Agent 只持有受作用域约束的 job ID |
| oracle、兼容 profile 与差异报告 | 开发验证资源及版本化报告 | 生产只使用明确覆盖范围的 profile，不调用 GUI |

当前 `project_store` 直接使用标准 SQLite 的事务保存求解准入，不借用主应用 Repository。预算提交先于目录创建和 worker 启动；调用期间不持有事务，提交后中断保守占用额度，缺少证据不自动重放。它不保存 Brief/阶段，不定义通用仓储框架或 event sourcing，也不承诺通用 exactly-once。未来领域 revision 与模型预算继续遵循同一权威所有权，而不是在宿主另存可编辑账本。

若宿主选择 LangGraph，直接使用官方 checkpointer，不自行序列化 continuation，也不把 checkpoint 变成核心恢复格式。恢复应核对领域操作是否已提交，避免执行快照滞后导致重复计量或重复优化；UI 投影失败不成为重跑 solver 的理由。实现这种桥接须等兼容性路线成立后再做。[LangGraph 持久化边界](https://docs.langchain.com/oss/python/langgraph/persistence)

领域 schema 随包版本管理，具体数据库迁移由存储适配器负责。可移植快照包含紧凑领域 state、预算已耗/预留记录、待处理问题、operation/job 身份和 artifact manifest，不以 MaigoKite 表结构、会话历史或 LangGraph 序列化格式作为核心恢复契约；导出不包含密钥。

MaigoKite adapter 将项目显式绑定到当前工作区文件夹与对话；独立宿主只需提供项目位置和授权范围。跨对话复用须显式选项目，不能通过长期记忆隐式带入其他工作流。修改需求或材料产生新 revision；旧验证和图表保留可查但标为过期。

### 3.4 MaigoKite reference host 接入状态与缺口

以下区分已有宿主结果适配与尚未实现的领域流程约束，不将现有聊天系统缺少领域能力判作聊天缺陷。

| 当前实现与源码依据 | 可复用部分 | 不能直接沿用的假设 |
| --- | --- | --- |
| [Core 组合模型、工具与 Runtime](../../python/agent/core.py)；[API Execution 持有 HTTP 协程](../../python/agent/api/execution.py) | 组合根、HTTP/WS、退出时取消并等待的宿主机制 | Core、扩展重载和 task resume 当前认的是聊天 Runtime；新增 runner 必须纳入同一准入/退出边界，不能只启动无人管理的后台 Task |
| [Runtime `_start_turn` / `recover`](../../python/agent/runtime/langgraph_runtime.py) | 官方 LangGraph/checkpointer 的使用经验 | 新回合只继承 messages；公开恢复只允许文件工具或已完成结构化结果的投影。不能通过增加 AgentState 字段得到跨回合项目，不能直接扩大成任意 MCP replay |
| [ChatModelRuntime `get_langchain_model`](../../python/agent/models/runtime.py) | 配置、凭证、Provider 与 Core 持有的客户端 | adapter 借用模型调用，不复制 Provider 包装、不关闭共享客户端；模型请求仍需纳入领域预算，不能借用一整个聊天回合 |
| [MCPClient](../../python/agent/mcp/client.py)、[MCPSettings](../../python/agent/extensions.py) | 官方 SDK、stdio 连接、环境与凭证管理；同连接有界资源读取，天线适配使用公开 meta 与 cancel 操作取得停止回执 | 当前仅 stdio，默认请求 30 秒；通用 MCP 的本地取消仍不证明远端停止。天线停止未获确认时也必须提示未知。资源 URI 不能被浏览器直接打开，读取上限不是传输层内存配额 |
| [MCPToolWrapper](../../python/agent/tools/mcp_wrapper.py)、[结果适配](../../python/agent/integrations/antenna.py) | 工具发现、命名与 metadata；显式启用后，成功求解转为有界回执、校验的本地文件/图片引用 | 通用 MCP 仍保留原始 data；专用投影不把原始数组/日志写入上下文。项目求解预算在服务端，不能当作整个工作流的 token 预算 |
| [ProductToolMiddleware](../../python/agent/runtime/product_tools.py)、[PermissionManager](../../python/agent/tools/permission_mgr.py) | 实时权限决策与现有审批审计 | middleware 依赖产品 RunContext；直接调用 MCPClient 会跳过它。adapter 必须桥接政策和审计，不导入到领域包；不能信任 server 的只读标注自行降权 |
| [ask_user_question](../../python/agent/tools/native/ask_user_question.py)、[Runtime `answer_question`](../../python/agent/runtime/langgraph_runtime.py) | 现有问题卡片、回答校验及版本语义 | 问题恢复绑定 tool_call_id 和聊天 checkpoint；领域问题须由宿主映射到对应执行器的等待/恢复入口，不能伪造聊天 ToolCall 来续接 |
| [Repository `reserve_model_call`](../../python/agent/repository.py)、[SkillTool](../../python/agent/tools/native/skill.py) | 任务用量展示、Skill 发现与资源展示 | 预算以 task 为单位，Skill 只是指令读取；都不能替代跨轮预算和确定性阶段准入 |
| [Workspace 文件 API](../../python/agent/api/workspace.py) | 当前目录的预览、图片、鉴权下载与路径检查；结果适配复用聊天图片组件 | 已核对调用身份、URI/MIME/大小/hash 和实时权限；服务已有项目 ID、输入版本及来源记录，但完整 Brief/阶段状态未实现。MCP 路径/URI 不是授权凭据，完整数组不得重新塞进聊天 |

首版采用本地 stdio 与明确授权的项目目录，因此可以复用工作区文件预览；不为此新增云端 artifact 服务。文件不在该目录时拒绝引用或显式导入并校验，不让前端自动下载任意 MCP URL。领域包不解释 MaigoKite 的 folder ID，宿主不将本机绝对路径写成可移植 artifact 身份。

固定资源先核验再导入 `antenna-results/<结果内容标识>/`，每次读/写重新核对会话目录及权限，最后写导入回执。部分失败保留已核验文件，不把成功计算改成失败、不自动重算。修改冲突给出稳定错误码和相对文件位置；用户保留并移走冲突文件后，用 get_result 或 get_delivery 补齐对应结果。未新增宿主 Runtime、业务表或 HTTP 资源代理；独立服务有自己的项目数据库。Linux 已复跑导入和生产显式工具链，完整 GUI/Windows 未验收。

剩余领域步骤、问题映射和预算准入不应转成核心对宿主或 LangGraph 的依赖。不能在外层聊天 Loop 再执行一遍同一工程调用或另行扣除一份 workflow 预算；结果适配也不代表完整领域工作流已完成。

### 3.5 宿主入口与恢复路由

本节是后续独立领域执行入口的设计。当前仍为“聊天 → Skill → 既有工具权限/审批 → MCP”，`design` 只在一次有限计算中顺序调用现有工程模块，不是独立聊天执行器或持久阶段流程。下面的项目路由和问题映射尚未注册成产品 API。

用户可从聊天提出天线需求，但正式进入工作流后，由宿主绑定项目并将后续输入交给执行适配器；普通聊天不同时再生成一次工程决策。未来以一个明确的领域入口集成，不把完整工作流包装成一个会无限等待提问的普通 MCP tool，也不建设通用 workflow dispatcher。

核心提供领域输入、决策请求、状态变更规则和结果，不承诺一个绑定框架的 runner API。执行适配器提供开始、回答、查询与取消入口；若使用 LangGraph，其 thread/checkpoint 身份由该适配器持有。宿主保留任务到项目/执行/问题的映射，答案和审批回到原执行器；不能复用每轮 MaigoKite task ID 作为项目身份，也不靠事件文字猜测执行路由。

执行前由宿主检查当前权限；需要审批时沿用 PermissionManager/Repository 审计规则，由执行适配器等待决定。现有 middleware 私有方法不能作为跨包 API：实现时只抽取确有重复的产品桥接函数，不复制权限状态机。授权限定具体 server、项目目录、操作与资源预算；拒绝或权限降级不能被恢复流程忽略。MCP 服务不因审批通过就获得沙箱隔离。

Core 创建 adapter 并提供借用的模型/MCP 能力；API Execution 持有入口协程。退出和扩展重载先等待已接纳执行停止或进入明确可恢复边界，再关闭共享连接。计算进程由 MCP Server 负责。独立 CLI 使用同一领域规则与工程模块，不必安装 LangGraph，也不复制 MaigoKite 的资源管理。

### 3.6 独立提取准入

- 核心必须能在不安装 MaigoKite、LangGraph、LangChain、不设置仓库 PYTHONPATH 的环境中导入与使用；类型、schema、阶段资源均包含在自身发布物中。
- 薄 CLI/reference host 能直接使用同一领域规则、模型与工程契约，不启动 MaigoKite Core/HTTP/UI，也不依赖 LangGraph。可选执行适配器单独安装，不成为另一套 Agent 产品。
- 在同一版本及已支持的平台环境中，项目快照/artifact 可从宿主映射导出并由独立入口读取；已有预算不会重置。活跃模型连接不承诺跨进程接管；尚在运行的 MCP job 必须查询确认，未知状态不能自动重放。
- 提取独立仓库时只调整发布位置、安装与宿主适配依赖，不修改领域 schema、阶段规则、MMANA 映射、预算语义或公共调用接口。可选适配依赖须明确声明，不能从包顶层导入，不能依靠主应用间接安装。

独立工程包、CLI 与 MCP Server 已创建，不导入 `agent`、LangGraph、实验脚本，也不调整 `sys.path`。领域阶段、模型预算与执行适配仍待实现，宿主结果适配不代替它们。单次 solve 使用一个新目录、一次求解；optimize/design 可组合多次，但每个新频点都经过持久准入。完整数值保存在 artifact，CLI stdout / MCP 只返回有界摘要和引用；同编号同输入的完成结果复用不新增求解。

## 4. Design Brief 与阶段规则

以下是目标规则，尚未实现完整阶段机。当前 Skill 用文件记录需求和假设，工程包检查输入、证据和求解额度；`design` 完成有限计算链，不会自动把项目推进为 `VALIDATED` 或 `BUILD_READY`。

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

兼容层属于可独立复用的工程模块，由天线 MCP/CLI 调用，包含文件编解码、MMANA 语义前处理、后端映射和结果规范化。不依赖 MCP transport 或 MaigoKite，不包含用户访谈或新运行时。

### 5.1 三个独立兼容门槛

1. **文件兼容**：`.maa` 能由目标版本读取；导入/导出不丢已支持的模型信息。
2. **语义兼容**：几何、连接、源/负载的物理位置和方向、激励、地面及材料含义一致；两端可使用不同网格，但必须保存实际网格与收敛证据。
3. **工程结果兼容**：相同物理条件下，阻抗/谐振/带宽、电流分布、主后瓣和增益达到预先约定的工程误差范围。不以 pulse 数相等、逐点舍入相同或 bit-for-bit 作为通过条件。

报告使用 `not_evaluated / pass / fail / unsupported`，携带 reference profile、覆盖特性和报告引用。只有声明范围内的三个门槛都满足，才能称“该范围兼容”；不作覆盖任意模型的“完全兼容”承诺。未覆盖特性明确拒绝或降为研究结果，不能静默改变物理模型。完整原始方向图和差异仍需保留，便于核查工程判断。

### 5.2 保留语义，不做有损共同子集

内部区分工程模型、原始 MAA 文档与离散计算模型，分别记录 hash。工程模型可含导线/管材/负载/材料引用；MAA 文档保留 wire 顺序、SEG、DM 参数、源表达式、附加高度、地面和材料枚举、未知区块及编码信息。

- 文件编码按 profile 显式声明；不靠不可见的编码猜测改写标题或注释。UI 单位与文件单位分开验证。
- 未识别字段保留原文仅供 round-trip；若可能影响计算，报告 unsupported，不能继续求解后声称兼容。
- 不把负半径引用、零半径特殊结构或组合管径无条件当成普通正半径导线；实现前逐项核对目标版本语义。
- 分段产生可审计 segment/pulse map：wire ID、顺序、端点、长度、方向、连接、局部/全局 pulse 索引和 ground 标记。
- 源/负载先解析成明确物理位置及方向，再转后端表示；不把一个后端的 pulse 编号直接搬给另一后端。重网格后重新核对位置；不能静默移动馈点或负载，无法表达时报告限制。
- L/C/Q、Laplace、有限电导和匹配网络必须保留类型、单位、频率依赖与来源；不支持的项不能无声丢弃。
- 扫频记录几何是否固定、分段参考频率及是否逐频重网格；应以 oracle 结果核实，不自行假定。

默认复用后端原生网格和公开接口，不为“兼容”再维护一套 GAL 网格生成器。固定同网格仅用于定位差异，参考辅助网格必须标识，不能混称独立建模。不同真实地面或损耗假设生成独立的模型条件/结果；不能从一个模型拿阻抗、另一个拿增益拼成同一报告，也不能为匹配 oracle 隐藏现实材料损耗。

## 6. 方向图是一等数值结果

数据流固定为 `solver → raw far-field → comparison / metrics / renderer / export`。任何后续分析均读取数值 artifact，不从 PNG/SVG 反推数据。首版优先适配 plot-antenna 的 matplotlib 非交互绘图路径；只有现有接口确实不足时才补薄绘图代码。不引入图像生成模型或生产 GUI。交互式 Plotly/3D 延后。

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
- 同一 raw artifact 可离线重画。PNG 用于预览、SVG 用于报告；领域包返回 `ArtifactRef`，MaigoKite adapter 将其映射为工作区授权文件入口，其他宿主可直接保存/查看文件。不将 base64 图像塞入默认模型输入；SVG 按不可信文件处理，不直接插入可执行 DOM。

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

当前 MCP 工具涵盖项目查询、输入校验、评价、修订准备、参数搜索、匹配、取回/补画和候选包，实际注册名遵从既有 MCP 名称规范化，完整清单见[独立包说明](../../extensions/antenna_design/README.md#高层工具)。`model_create / sweep / pattern` 是后续候选，不宣称已注册；`.maa` 导入仍走明确的 CLI 子集。

`plan_design` 只读规划最低/推荐次数并留两轮网格复核额度；`design` 在同一 MCP 工具调用及取消作用域内顺序组合已有 optimize、solve、render、export。不新建通用工作流状态机，不移动天线业务到 AgentCore。工作流总时限内搜索占 65%，余下时间用于复核与交付；这不是完成时间保证。搜索与两层网格证据绑定并进入 ZIP；容差缺失只记录敏感性，容差通过也不认证制作。宿主复用结果导入和 tool.started 活动更新，模型只接收紧凑摘要。

2026-10-28 的边界收敛：`WorkflowState` 明确记录 `DESIGN_READY / SEARCH_RECORDED / VALIDATION_RECORDED / DELIVERED`，带输入、目标、物理方案与验证证据身份，由现有项目 SQLite 按 revision 原子写入。发布包与 SQLite 不假装跨存储原子提交：`get_design_state / get_delivery / export` 对“已发布包、未提交交付状态”核验原始输入、目标、所选结果、验证摘要及包完整性后幂等补记 DELIVERED；半成品不推进，冲突拒绝，相同并发提交可重复确认。它只修复已有交付元数据，不重新求解或重写历史包；无状态旧任务不推断阶段，重启不恢复 SciPy 种群。DELIVERED 仍是候选交付，不等于未来的 VALIDATED / BUILD_READY；完整 DISCOVERY/CONCEPT/Brief 流程仍未实现。本次仅静态检查，不把历史运行记录计作新版本行为验收。

交付评价同时汇总基础与细网格的辐射、直连 SWR 判据；任何已提供网格超标都进入紧凑回执、报告与下一步建议。网格容差失败/证据不足单独提示，不与目标达标或制作验收混为一谈。旧包未记录匹配汇总结论时返回未知，不按新版规则改写历史结果。

物理身份包含有序几何、馈点和材料；仿真身份包含频率、地面、网格、激励、角采样与阻抗参考。扫频对照除频率外保持相同口径，网格对照只放开分段数，不能因为物理 hash 相同就混用计算条件。v2 评价和交付新增身份字段，旧 design_sha256 的算法不改；历史文件、哈希和 ZIP 原样保留。源码、依赖版本与数值 provenance 继续存于各 run 的 environment/manifest，不由新输入身份代替。

优化语义固定为 `objective_mode=sampled_feasibility`：最小化显式门槛的归一化平方缺口，达到全部已请求采样约束后停止，不宣称最大增益或 Pareto 最优。增益/F/B 可省略，表示不参与目标；SWR 仅在显式直连且给出门槛时参与，至少要有一项约束。增益取指定方向的 total gain（非 peak/realized gain），F/B 为同仰角相反方位之差；scale 只影响搜索权重，不放宽工程门槛。目标身份排除证据路径和预算，参考指标与设计约束不能混用。

- `model_validate` 返回结构/语义/适用性检查，不等同于用户需求验收。
- `optimize` 复用 SciPy differential_evolution，接收 seed model、dimensions/bounds、targets、random_seed，以及可选 population_size 和 max_solver_runs；每个候选保持同一几何，复核全部目标频点。只搜索孤立导线的长度/中心坐标，不搜索拓扑或匹配网络；内部零 LLM 调用，不接受可执行表达式或 shell 目标函数。算法、停止条件和限额见[独立包说明](../../extensions/antenna_design/README.md#有界尺寸优化)。
- 长计算沿用 run_id、输入证据、进度、结果引用与 cancel；参数搜索以 get_optimization 只读取回完整最佳候选。原生 AnyIO/子进程机制承担生命周期，不恢复 SciPy 种群，不再为此新增 job API 或执行框架。单个频点仍用原 manifest/hash 校验与项目账本。
- 提交身份由可信宿主生成并绑定项目/版本，不以模型编出的 ID 授权。重复提交先核对身份与参数；超时/重启查询结果，不盲目重复优化。
- Server 持有并回收 solver 进程；请求取消只表示本地已停止等待，直到服务器确认计算已结束才标记 job cancelled。不承诺 SDK 当前没有的远端取消保证。
- CPU、内存、时间、输出文件量与 solver_runs 需要分别限制，并说明 Windows/Linux 的实际能力。当前已有 worker 时限、求解准入和有界资源读取，但没有系统级 CPU/内存/磁盘配额；独立 MCP 进程不等于 OS 沙箱。

当前只返回有界 summary、重要 warnings/diagnostics 和授权 artifact 引用，完整矩阵通过文件读取，不进入 ToolMessage。未来若增加 `pattern` 的 cut/region 查询，应限制角域、极化、频率、分页和抽样数量；不能将这个目标写成现有接口。日志按失败诊断范围限量读取并脱敏。

MCP 2025-11-25 Tasks 仍标为 experimental，且其规范允许任务状态已 cancelled 时底层执行继续。因此优先调查官方 SDK 的任务支持，但不能把 `tasks/cancel` 的回执等同于 solver 进程已退出：工程回执还需明确 `execution_stopped` 及已产生的结果/消耗，未确认则保持未知，不自动重提交。MaigoKite 当前 client 尚未暴露这些操作；不能只改 server 就宣称宿主已支持。[MCP Tasks 与取消语义](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)

## 9. Token 与计算预算

**已实现的边界**：MCP 项目的求解准入存入专用根目录 `.antenna-project.sqlite3`；新目录默认 3 次，操作员通过 `--max-solver-runs` 调整总限额并留记录，省略则保留已存限额。换 run_id、重启和删除产物不退额度；旧目录首次纳入时按每目录 1 次保守计入。`project_status` 返回预算与分页恢复记录，读取/补画不计次数。3 次只是单方案试算的初始保护，不足以代表参数搜索需求；优化按候选的每个频点扣准入，先 plan_design 再安排额度。独立 CLI 累计次数和下表的工作流级模型预算尚未实现。

`optimize` 在硬边界内动态分批，种群/资源批次默认 max(8, 2×自由参数数)，留最多两轮全频复核余量。资源批次不再决定停滞：SciPy 的公开 callback 在每代完成后比较改善，初始化不算一代；默认至少 5 代、连续 5 代无有效改善才因停滞停止，有效改善需超过 0.1% 和 1e-8。次数/时限不足可以更早停止，但不能声称探索充分。实测完整候选耗时乘 1.25 的高水位限制后续准入，worker 时限预留 max(5 秒, 10%) 收尾。分配回执不是第二份项目账本，也不锁定其他客户端额度。plan_design 按参数数、全频成本、暖机代数与复核计划给出资源依据；调整项目总限额仍经现有操作员配置留痕，不由模型工具自行扩权。

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

领域规则在执行前通过 project store 契约原子预留请求额度及输入估算/输出上限，返回后用模型服务提供的 usage 对账。没有精确 tokenizer 或 Provider usage 时标明 estimate，保守保留预留额度，不能记为零或宣称计费级精确硬上限；预算身份与已耗额度跨重启、重试和新聊天回合保留。不可控制的 SDK 自动重试须关闭或纳入最坏尝试数。输出/推理上限的实际可控性按 Provider 能力报告。预算实现不能依赖 MaigoKite task 计数器才能工作。

达到限额由确定性代码拒绝新请求，保留结果并说明冲突，不能再追加一轮“最后总结”偷偷超支；收尾额度不足时由模板呈现已有结论。用户显式增加预算形成有审计的变更，不靠开始新 task 重置额度。

### 9.2 Prompt 与数据流

模型主要读取当前结构化 state、当前阶段 Skill、最近未被吸收的用户信息与最新 compact result。确认过的原话在产品历史中保留审计，但不与 Brief、摘要三份重复注入；必须保留待完成的工具调用/结果配对和未处理用户纠正，不能靠字符串截断丢掉协议语义。

阶段资源只加载访谈、方案选择、建模、验证或制作中的必要部分。MMANA 格式、求解数学和材料数据按主题检索，首版使用领域包内资源/明确章节；宿主可将其展示为 Skill，但读取不依赖宿主注册表，不为此建设向量平台。固定前缀/工具 Schema 稳定排列，变化 state 放后部；不为缓存命中保留已失效材料或无关历史。

优化器执行数百次计算只返回有限的最佳/Pareto 候选摘要与完整结果引用；solver log、迭代日志、矩阵、完整方向图默认永不进入 prompt。matplotlib 直接读取 artifact，不需要 LLM 给每个采样点写绘图指令；PNG 展示给用户也不意味着自动送进视觉模型。

`compact result` 至少包括 revision、状态、频率范围、最差/标称 SWR、馈点阻抗摘要、绝对增益、主瓣/切面波束宽度、F/B/副瓣及其定义、硬约束违反项、重要假设、材料风险、数值与兼容验证状态、artifact 引用和消耗。必须保留关键失败条件，不能因限长把 warnings 截掉伪装成功；超限改为分类摘要与细节引用。

### 9.3 Telemetry

领域包定义普通结构化 usage/progress 记录，通过显式回调供宿主观察，关联 workflow_id、stage、brief/model revision、request/job ID。MaigoKite adapter 再映射到现有 model.call/model.usage 与 UI/Repository 投影；独立宿主可输出本地 JSON/日志。领域代码不发送产品事件 DTO，也不依赖订阅者成功落库才执行预算限制；权威预算在 project store 中对账，telemetry 不成为第二份账本。不新增事件总线。

分别记录实际/估计 input_tokens、output_tokens、已提供的 cached/reasoning token、model_requests、llm_rounds、MCP calls、solver_runs、solver 时间/峰值资源、每阶段 token、失败/重试与剩余额度。Provider 已含在 output 中的 reasoning token 不再次相加。一次进程包含多个频点时按频点计 solver_runs，MCP status 查询只增加 MCP calls，不增加 LLM calls。

算力预算与 token 预算相互独立：禁止用更多 LLM 往返替代便宜的数值迭代。profile benchmark 同时记录设计正确性、MMANA 差异、材料/制造约束、求解时间和阶段 token；不以最低 token 单项排名牺牲结果质量。

### 9.4 优化器预算不能只限制迭代次数

以 SciPy 为例，`maxiter` 计算的是种群代数；一次评价可能包含多个频点。其文档说明 callback 请求停止后仍可能执行默认 polishing，不能用一个 callback 冒充完整硬限额。[SciPy 官方说明](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.differential_evolution.html)

首个可控验证配置使用单 worker、固定 rng、显式种群和 `polish=False`；如后续开启并行或局部精修，必须将其纳入同一个剩余预算。实际每个频点求解前检查/预留额度，目标函数与约束函数共享同一组结果，不因各自读取指标重复计算。相同输入的复用键至少包含模型、材料、solver/profile、频率、网格与计算选项，不能只按几何或文件名缓存。

例如剩余 300 次频点求解，预留 60 次用于最终扫频与敏感性检查，每个候选计算 5 个频点，则优化阶段最多容纳 48 组全新的完整候选评价，而不是 300 个候选。实际启动的失败/重试计入消耗，缓存命中另计；无法确认远端计算是否启动时不自动退还预留额度。用量未知的任务先核对状态，不立即重复提交。几何预检查可提前拒绝无效候选，但不得把无效候选包装成可行结果。

内置优化器若只能限制迭代数，先核实能否给出可靠的频点求解上界；无法满足硬限额时保留 solver，改用可在公开评价函数边界计量的现有优化器，不改写搜索算法。开发阶段 GAL reference 采集的计算量单独统计，不混入用户的生产设计预算。

## 10. 可复现 artifact 与参考验证

### 10.1 制作包

下列是完整制作包的目标布局，不是当前宿主导入目录的固定文件表。现有 export 交付候选包，按 run_id 保存各频点证据，可能明确缺少可导出的 MAA；固定十项资源与 ZIP 内容见[包说明](../../extensions/antenna_design/README.md#接入-maigokite-并交付文件)。候选包不会因含有施工说明就变成制作验收通过。

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

先比较物理语义，再比较工程结果。源/负载位置或连接不同必须先解决；网格数量不同本身不是语义失败。坐标和单位转换必须有明确来源，不能通过拟合旋转、电流重缩放或修改频率让两组结果看起来一致。

| 对象 | 差异报告 |
| --- | --- |
| 阻抗 | 每频点 R/X、绝对和相对误差；近零分量不用相对误差单独裁决 |
| SWR | 相同参考阻抗与参考面，保留原始值、无穷/无定义状态和带宽/谐振偏移 |
| 电流 | 同激励下的馈点电流、沿导体的幅相分布及节点；相同网格可逐 pulse 比较，不同网格按物理位置分析，插值结果另存并标明，不冒充原始值 |
| 方向图 | 相同频率/角域/极化/地面下的原始场与切面；同角网格报告逐点绝对 dBi 差、RMS、最大误差，结合主后瓣/零陷位置判断工程影响 |
| 派生指标 | 最大 gain、主瓣方向、切面波束宽度、F/B、主要副瓣差异；并列峰值不能强行认定唯一方向 |
| normalized pattern | 归一化基准与 peak 的差异单列；不能分别归一化后掩盖绝对增益偏差 |

规则网格的 RMS 定义为有效成对样本差值的均方根；如果另报球面积加权误差，单独标识权重，不能与普通 RMS 混用。深零点的 dB 差可能失真，应同时报告线性功率误差与预先声明的比较下限/掩码和有效点数；不删除失败角度或后调阈值。角采样不同时记录两端设置，优先补算共同角度；必要的插值/重采样另存并标识，不能宣称原始点相同。原始数据可比较不等于要求每个点相等。

两端使用各自适用网格，分别加密检查稳定性，再比较会影响制作或选择的结果：谐振偏移、目标频段 SWR/带宽、增益、主瓣方向、后瓣及材料损耗。误差阈值由用途、制造/测量精度和模型敏感性预先确定，不能根据刚算出的差异倒定。CSV 舍入区间只解释观测分辨率，不是兼容容差。约 0.003 dB 的导出误差、pulse 数不同或未复刻 DM 算法，均不能单独阻塞下一项工程验证；反之，图形看起来相似也不能覆盖错误的馈电、材料或地面假设。

### 10.3 参考样本覆盖

先建立以下小型样本族；这是参考采集规格，不是已执行用例，也不是供用户制作的天线建议。各变体一次只改变一个因素，输入和 reference 必须一起冻结。

| 样本族 | 固定或单项变更 | 首先要回答的问题 |
| --- | --- | --- |
| A01 自由空间基线 | 145 MHz；沿 X 轴从 -0.5 m 到 +0.5 m、半径 0.001 m 的理想导线；均匀 20 段、中心 1 V/0° 源、50 Ω 参考阻抗；另存 40/80 段变体 | 核对单位、方向、源幅值和电流；同网格数值诊断与加密收敛分别记录，不预填阻抗或增益 |
| A02 分段与激励 | 从 A01 分别改为 SEG=0/-1/-2/-3，记录实际 DM1/DM2/SC/EC；再分别变更源偏移、wire 顺序及端点连接 | taper 是否按要求生效，源是否仍落在对应 pulse；禁止求解成功却偷偷改网格 |
| A03 地面 | 将 A01 平移至 z=5 m；分别冻结自由空间、理想地面和一种参数明确的实际地面 | 区分阻抗与远场采用的地面假设；NEC 方法差异不能记成普通映射误差 |
| A04 材料与负载 | 在相同几何下分别引入有来源的有限电导、RLC/Q、Laplace；记录频率与单位，不同时改变源位置 | 损耗、系数次序及频率依赖是否一致；未知材料不能用理想导体替代后通过 |
| A05 连接与方向图 | 冻结一个 V 形、闭环及三元 Yagi 的实际 `.maa`；补旋转/镜像和多源相位变体 | 接点、方向、H/V、前后方向与切面指标是否一致；对称/并列波瓣必须保留歧义 |

A01 先请求 θ=0…180°、φ=0…355°、步长 5° 的完整自由空间网格；若 GAL 导出无法配置该网格，先记录其真实角网格并调整采集规格，让候选计算同一网格，不通过插值宣称通过。有地面时单独定义有效角域。端点、极点和 null/floor 样本处理随 reference 一起保存；重画闭合曲线所补的 360° 点不重复计入误差。主瓣附近需要更细网格时另存版本。

这些样本按风险选取，不要求逐个复刻所有 SEG 模式后才能检查材料或实际天线。继续补 HF/VHF/UHF 代表性尺寸、异径、陷波、多频与制造容差组合，以及不支持介质/面结构、非法几何、预算耗尽和原始数据缺失的拒绝案例。

先用极简样本隔离分段、pulse、负载与单位差异，再用代表性天线验证组合行为。每类同时记录兼容覆盖和工程适用范围；不能用 HF dipole 的通过替代 UHF Yagi、加载天线或任意自定义结构。

### 10.4 Reference 采集交付与准入

每个样本包必须同时包含原始 `.maa`、GAL 原始数值导出、采集配置/版本/hash、明确的网格与源/负载位置，以及材料依据。候选 solver 的输入、原始输出、规范化结果和差异报告另存，不能覆盖 reference。如果无法导出内部 pulse 信息，只能用目标版本可取得的电流坐标等建立对应证据；仍无法确认时语义项保持 `not_evaluated`，不能把原始 MMANA 源码推断当作 GAL 的实际数据。

采集前声明参考阻抗、增益/极化/角度定义、需要覆盖的模型特性与误差判据；阈值尚未批准时允许报告数值差异，不允许给 PASS。参考文件未到位时可以继续接口调查和格式分析，但不构造模拟 reference 来宣布兼容完成。A01 原始模型、成功日志、截图与电流/远场 CSV，以及 140/150 MHz、40/80 段四例的原始 CSV 均已比较。该批参考版本由用户报告为 v1.2.0.20；四例未提供 GAL 另存模型和直接 R/X 表，CSV 工况依赖文件名及关联输入。后续六例 HF 工程参考和 23 项多导线/扫频参考已包含实际版本/EXE hash、另存模型、日志、CSV 和设置证据，但不能据此追认旧 A01 未记录的环境身份。五点扫频、倒 V 接点、方环及 Yagi 已有对照；方环 CSV 缺一个 pulse、多线相位朝向和其他 topology 尚有边界，无完整 profile 准入通过记录。

## 11. 下一步选型门槛

2026-10-19 后续进展：`-NextBatch` 的 **23/23 项参考已经回收并完成对照**，不需要重新采集。倒 V、闭合方环、三元 Yagi 的九次原计算后，追加六次固定网格诊断；原五工况 75 次计算继续复用。已有原生渐变分段将 Yagi 固定轴 F/B 差异从约 2.54 dB 缩小到 0.23 dB，不改尺寸或内核；均匀网格继续加密并非单调改善，不能据此宣称收敛或选择通用默认网格。完整 dB 差异、共同峰值线性功率诊断及原始数据一并保存，不通过删除深零陷获得 PASS。五工况粗谐振夹区一致，材料表示与多线复电流仍有明确限制。细节与产物见[工程评估](../../experiments/mmana_compatibility/ENGINEERING.md)。这些诊断仍保留在实验目录；后续独立工程入口位于 `extensions/antenna_design/`，两边均不引入 MaigoKite / LangGraph 依赖。

上述实验目录只评估求解器，不实现领域工作流、LangGraph 适配、项目数据库、MCP server、优化器或 UI。A01/A02 证据保留，固定波长改频与参考辅助网格只作诊断。HF 工程 75 次、原材料 2 次、多导线 9 次、网格诊断 6 次及材料五频点 5 次，累计 97 次候选工程计算，已与六例主频及新增 23 项 GAL 参考对照，没有扩展第二个 solver。后续 OB11 搜索与交付属于独立包的另一组运行记录，不能将两组次数或通过结论混用。

求解器选型采用 pymininec：冻结接线及共同物理位置上的复电流对照支持现成渐变分段，五点材料增量支持明确的历史集中损耗适配；没有证据要求自研内核。独立包随后已有项目准入、搜索/交付、匹配和宿主接入的 Linux 定向记录，不再将它们整体列为待运行。下一步仍需补 Brief、完整阶段约束、模型预算和工程验证，不重开通用 Runtime 重构。复用边界见[工程评估](../../experiments/mmana_compatibility/ENGINEERING.md#选型结论与首个薄适配的边界)，实际运行范围见路线图；旧样本不追认未预定义阈值的 PASS。

本批确认材料表示必须显式区分：GAL 铜预设与候选分布式复阻抗模型的总场差异约 0.15 dB；由历史 MMANA 材料线索出发，复用公共集肤接口计算整线电阻、再使用公共中心集中负载接口，原主频总场差异降至约 0.003 dB。新增五点的电阻增量也在 GAL 显示相减的舍入范围内；电抗仍有约 1.3 Ω 的基础差异。这支持窄适配，不支持把集中损耗默认当作更真实的材料模型，也不证明当前 GAL 的全部实现。领域结果必须记录所用材料表示及其依据，物理模式与 MMANA 表示不能静默互换。

工程评估暴露出需要保留的适用性边界：5 m 高的 7.1 MHz 水平导线低于 0.2λ，MININEC 的地面阻抗近似可能不能支撑现场制作判断。与 GAL 数值一致不消除共同的模型局限。有限电导候选的增益变化已可观察，但材料常数仍是示例，不替代用户材料来源、绝缘层和接头等实际条件。

spike 回答能否正确表达工程条件、结果是否稳定、与 GAL 的差异是否改变设计决策。新工况没有 GAL 原始导出时保持 `AWAITING_REFERENCE`，仍可先完成候选侧计算和复算材料。DM1/DM2/SC/EC 的内部生成规则不再是必过门槛，也不因此新增网格框架。只有物理映射或工程结果的关键缺陷无法由现成 API 解决时才考虑补缺。

优先核对已准备的语义案例，不反复扩大同一舍入误差的诊断。以下是后续选型门槛，不是本轮全部实施范围：

1. 冻结一个 GAL reference 版本与合法采集环境，建立原始数据/配置清单；先确认 `.maa`、CSV 与所需设置可完整导出。
2. 对同一极简模型先比较文件、分段、pulse、激励和负载，不先调结果误差。pymininec 与原始 MMANA 都检查；NEC 路线用于区分适配错误与方法差异。
3. 比较数值、材料、方向图与执行成本；确定 Windows/Linux、Python 3.14、超时/取消、重算和预算能否满足产品要求。
4. **直接采用**：所需语义和误差均满足，无需维护内部算法补丁。
5. **组合与兼容适配**：先组合已存在的 solver、MCP、优化器与格式处理能力；剩余差异限定在文件、分段、源/负载映射与明确的结果转换。优先公开 API/上游贡献，不长期覆写私有数值内核。每项补缺提交第 2.3 节的差异记录。
6. **提取兼容核心**：只有重现实验证明关键差异发生在现有后端不能适配的内核，并且原始源码确能改善目标兼容、许可清楚、维护成本可承受时采用。仅补或替换已定位的不兼容计算部分，保留其余已验证组件；必要时才提取更大的核心。先保留计算行为移除 GUI 耦合，不同步重写公式或优化算法；仍须 GAL oracle 对照。

若没有候选满足硬要求，结论是尚不可交付 MMANA-compatible 模式，而不是扩大容差、降低材料要求或以图片相似冒充成功。兼容实现不合适时应收缩明确支持的模型特性，而非静默降级。

首版最终交付必须能从制作包重算，直接展示 2D 图并打开原始文件，报告材料与模型限制，并在预算内完成或明确说明不可行。没有生产后端、真实兼容回执和工作流闭环之前，不在产品中展示已完成的天线设计模式。
