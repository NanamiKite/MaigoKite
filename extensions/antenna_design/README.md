# antenna-design：独立工程入口

这是天线工作流的第一条工程链路：**明确输入 → pymininec → 原始数值、方向图、复算文件与有界摘要**。可将此目录整体移到独立仓库；不导入 MaigoKite、LangGraph、聊天记录或实验脚本，不启动 MMANA-GAL。

当前不是完整需求访谈/设计/优化工作流；可通过显式 MCP 配置接入主应用，不默认注册工具。所有成功结果仍标记 `COMPUTED_NOT_VALIDATED`：算出数值不等于已收敛、MMANA 兼容验收通过或可直接制作。

## 安装与运行

Python 3.14；在本目录执行，Linux / Windows PowerShell 的 `uv` 命令相同：

```sh
uv sync --locked --extra engineering
uv run --locked --extra engineering antenna-design solve examples/dipole.json --output output-dipole
uv run --locked --extra engineering antenna-design solve examples/yagi.json --output output-yagi
uv run --locked --extra engineering antenna-design solve examples/physical-copper.json --output output-copper
```

输出目录必须不存在，其父目录须已存在；再次运行使用新名字。不会覆盖历史数值，也不会自动删除失败证据。以上是使用命令，本轮实现仅完成静态检查，未把这些运行计为验收通过；Windows 新入口也尚未实机验证。

导入明确子集的 `.maa`：

```sh
uv run --locked --extra engineering antenna-design solve model.maa --mesh taper32 --output output-maa
```

`--mesh uniform80` 为每导线 80 段均匀网格；`taper32` 为 32 段、公开原生两端渐变，最小段长 `max(4r, L/512)`，最大 `L/16`。这两种是显式数值实验选择，不是对所有天线的推荐默认值。GAL 的 DM1/DM2/SC/EC 不自动翻译成候选网格；几何、馈点、材料条件必须保持，实际离散网格另存。

中文 Windows `.maa` 默认按 CP936 解码；其他文件可指定 `--maa-encoding utf-8-sig` / `cp1252` / `ascii`，不使用替换字符掩盖解码错误。当前名称字段限 ASCII；原始文件完整保留。

`--timeout 120` 为整个数值及绘图子进程的秒数限额，可设置 1～600 秒。超时或 Ctrl+C 杀掉并等待该子进程，不重试；父进程发布失败状态。它不是系统级沙箱、CPU/内存/磁盘配额，也没有承诺断电恢复和目录持久化事务。

基础安装只含 Pydantic。需要查看输入契约而不安装数值依赖时：

```sh
uv run --locked antenna-design schema
```

## 独立 MCP Server

本包已有可选的 **stdio MCP 入口**，复用官方 Python SDK 1.30.0 与 AnyIO；协议、请求取消和传输关闭交给 SDK。数值核心不导入 MCP，也不依赖 MaigoKite / LangGraph。安装在本包自己的虚拟环境中，不修改主应用依赖：

```sh
uv sync --locked --extra engineering --extra mcp
```

由 MCP 客户端启动以下进程，参数中的目录由使用者选择，不能由模型指定：

```text
# Linux command
/absolute/antenna_design/.venv/bin/python
# Windows command
C:\absolute\antenna_design\.venv\Scripts\python.exe

# args：每项作为独立参数；--root 后填写绝对路径
-m antenna_workflow.mcp_server --root <专用结果目录> --timeout 20
```

也可以在已激活的独立环境运行 `antenna-design-mcp --root <专用结果目录>`。这是 MCP stdio 进程，不是网页或交互命令行；stdout 只传 JSON-RPC，诊断进入 stderr / 结果目录。不要把启动命令交给模型执行，也不要同时通过多个客户端共享同一结果目录。

### 两个高层工具

| 工具 | 参数 | 返回与边界 |
| --- | --- | --- |
| `antenna.model_validate` | `model`：现有 `EngineeringRequest` JSON 对象 | `INPUT_ACCEPTED_NOT_SIMULATED`；校验单位、输入范围、支持字段与基本连接，不启动 solver，不称为物理验证 |
| `antenna.solve` | 同一 `model`，加 `run_id` | 一次 solver + 2D 图；`COMPUTED_NOT_VALIDATED` 摘要、资源引用与标准 MCP ResourceLink，不内联原始数组/日志 |

例如 `model` 使用 `examples/dipole.json` 的对象内容，`run_id` 使用 `dipole-001`。**不是传本机 JSON 路径**，MCP 不提供任意文件导入；`.maa` 仍走前述显式 CLI 入口。

`run_id` 限 1～64 个小写 ASCII 字母、数字、下划线和连字符，首位为字母或数字。新编号创建专属目录；已完成的同编号、同输入可重复读取，`reused=true`、`solver_runs_this_call=0`。同编号不同输入、未完成目录、损坏证据均拒绝，不自动重新求解；原件保留，用户决定是否以新编号再算。这不是通用 exactly-once 或断电恢复系统。

`model_validate` 不做原生网格/交点/馈点 pulse 映射；这些由 `solve` 在真实 solver 对象上检查。两者都不代表网格收敛、MMANA 兼容认证或制作验收。工具 schema 由 Pydantic 生成，SDK 按输出 schema 校验成功回复；输入由 Pydantic 校验，以避免协议层报错回显完整输入。

`solve` 成功回执的 `schema_version` 为 `1`，包含 `run_id / reused / solver_runs_this_call / summary / resources`。宿主按这一传输契约适配，不需要安装或导入本包的 Python 类型。

### 结果与读取边界

资源 URI 形如 `antenna://runs/dipole-001/azimuth-png`，客户端通过 MCP `resources/read` 读取，不是 HTTP URL 或浏览器可直接打开的路径。服务提供对应的 `resources/templates/list`：

| 资源 ID | 文件 |
| --- | --- |
| `request` / `summary` / `compatibility` | `request.json` / `simulation.json` / `compatibility.json` |
| `far-field` / `currents` | `pattern/far_field.json` / `currents.json` |
| `azimuth-png` / `azimuth-svg` | 水平切面 PNG / SVG |
| `elevation-png` / `elevation-svg` | 垂直切面 PNG / SVG |
| `maa` | 仅在该模型可导出时提供 `design.maa` |

只读取有完成标记的目录；每次验证 manifest hash 和所读文件的大小/SHA256。URI 只允许固定资源 ID，拒绝符号链接、junction、越界目录和非普通文件。日志、环境清单和源码快照仍保存在本地证据包，但不通过这些资源接口暴露。复用结果时核对请求和摘要；其余文件在实际读取时再核验，不能把摘要可读当作整个包已逐文件重新验收。

工程输入限 64 KiB；结构化成功摘要限 16 KiB、最多 10 个资源引用；SDK 同时提供文本副本和结构化内容，因此该限额**不是整个 JSON-RPC 包或 token 数**。单个资源最多读取 16 MiB，元数据资源为 64 KiB。MCP 默认不主动读回数组；宿主也不能自动把完整 `resources/read` 结果塞回模型历史。日志不进工具回复；失败只提供有界错误，不直接透出第三方异常。

这些是本地服务文件接口的边界，不是操作系统沙箱、鉴权服务或抵抗同用户恶意篡改的隔离机制。专用目录由本地可信用户拥有，不应与其他用户共享写权限。资源上限也不是磁盘写入配额。

### 预算、取消与关闭

- 一个服务实例最多一个活跃 solve；并发调用返回 `BUSY`，不排队、不重试。新计算最多一次 solver，内部 LLM 调用为零；复用时本次 solver 次数为零。**整体工作流的 LLM/token/累计计算预算尚未实现**，不能靠换编号无限计算后仍声称总预算受控。
- 默认 worker 时限 20 秒，可由操作员设置 1～600 秒；覆盖数值与绘图，不是完整 MCP 往返时限。MaigoKite 目前 MCP 请求默认 30 秒；若提高 worker 时限，宿主超时也必须同步考虑。客户端超时但未发送取消通知不保证立即停止，服务器自身 deadline 仍生效。
- MCP 请求取消和 stdio EOF 通过 SDK 取消 handler；AnyIO `run_process` 在退出作用域时清理并等待 worker。失败记录在清理之后写，只有 worker 正常退出且最终 manifest 已写完才发布完成标记。不会把阻塞 CLI 放到线程里后只取消等待。
- MCP 的协议取消回包可能先于清理完成，**不能当作进程退出回执**。本轮没有另造任务协议；取消与完成竞争时，以最后完成标记为准，不抹掉已经完成的结果。
- Linux 另外处理 SIGTERM；Windows 使用 stdio 关闭或 Ctrl+C 的正常退出路径。SIGKILL、Windows 强制终止、断电不保证 worker 清理；partial 目录不会自动恢复/重放。这里没有实现进程树监督或系统级资源配额。

### MaigoKite reference host

主应用已编码可选结果适配，接入步骤见[配置指南](../../docs/user-guide/Configuration.md#81-天线计算与方向图)。MCP 设置中显式选择“天线结果与方向图”，保存后应用；默认为标准工具结果。不根据服务名、端口或任意 `ResourceLink` 自动开启。

```text
现有工具权限与审批 → antenna.solve → 有界结构化回执
→ 同一个 MCP 连接 resources/read → 校验 URI / MIME / 大小 / SHA256
→ 当前对话文件夹 antenna-results/<调用标识>/ → 聊天 PNG 预览 / 点击打开
```

桥接位于主应用 `python/agent/integrations/antenna.py`，本包不导入它。MCP 仍按现有高风险策略审批，批准范围包括该次结果资源读取和本地副本；资源读取不绕过工具审批。每次读资源及写文件前重新查询当前对话目录和权限，降为只读后不再开始后续写入。正在执行的单次文件写入不能强杀，取消会等待它结束，不把线程留在后台继续写。

服务器证据包保留原布局；宿主副本采用平面文件名（例如 `azimuth.png / elevation.png / far_field.json / simulation.json`），原始资源路径和 URI 保存在最后写入的 `import-receipt.json`。同内容可复用，不覆盖已编辑文件。源目录与当前对话目录可不同，宿主不把 MCP URI 当成本机路径或 HTTP 地址。

宿主每次导入最多 10 个文件、单文件 16 MiB、合计 48 MiB，整个读取/写入阶段限时 30 秒（不可中断的本地写入清理另计）。模型只收到有界摘要、资源与本地文件引用，不收到这些数组或图片字节。部分导入失败时保留已核验文件和警告，`import_complete=false`，不把成功计算改记为失败，也不自动重算；取消同样不自动重放远端副作用。

这只是现有聊天链路的结果适配，不是完整访谈、Design Brief、优化、工程验收或制作报告工作流。新宿主链路仅完成静态检查，未运行 MCP/聊天端到端或 Windows 实机验收。

## 输入与真实边界

| 项目 | 已实现入口 | 明确限制 |
| --- | --- | --- |
| 导线 | 米制端点、半径；最多 16 根等径直导线 | 裸、均匀圆导体；非 PCB/surface/绝缘/复合材料模型 |
| 连接 | 显式首尾连接、非连接寄生单元 | 拒绝分叉、反向接点、重叠及内部相交；不自动拆线或猜接点 |
| 激励 | 一个电压源；导线中心或已连接末端 | 物理位置必须唯一匹配 pulse；无最近点替代，无多源 |
| 负载 | 馈点纯串联电阻 | 无任意位置复阻抗、RLC/Q/trap |
| 材料 | 显式理想导体，或有依据说明的实际电导率 | 复用分布式复集肤阻抗；不把材料名当作电导率，不映射 GAL 铜预设 |
| 地面 | 自由空间、理想地面、均匀土壤 εr/σ | 地面模型要求导线离地；无接地/埋线、多层土壤、地网；低架地面仅近似 |
| 网格 | 显式均匀/两端渐变；实际 pulse 最多 512 | 渐变回退、意外连接/接地、馈点错位均拒绝；不承诺自动收敛 |
| 角采样 | 2/5/10/15/30°；全空间或地面以上 | 最大 16,380 点；不插值伪造细分辨率 |
| 切面 | 自选仰角的水平切面、自选 φ 的垂直切面 | 角度须落在采样网格；一般斜切面、交互 3D 尚未实现 |

JSON 输入未知字段、NaN/Infinity、无效单位范围拒绝；输入文件上限 64 KiB。工程层接受 1 MHz～1 GHz 作为计算参数范围，**不是这个频段内所有几何/材料均已验证**。薄线条件、独立网格收敛、材料测量与实际安装仍须另行评估。

MAA 导入只接受已调查格式：无损、自由空间/理想地面、附加高度零、单个 `wNc` 或连接点 `wNe`、无负载或源位置纯 R、已见 800/80/2/1 或 1600/160/2/1 设置。未知源表达式、负载模式、材料枚举、外部土壤设置、额外有效段落均拒绝，不静默吞字段。

### 材料与 MAA 导出

`examples/physical-copper.json` 明确写入假设电导率及依据，不称它为实测材料。它保留 `request.json`、`compatibility.json` 和原生求解器参数，但**不生成伪兼容的 `design.maa`**。物理分布式损耗与 GAL 历史铜损耗表示不同，尚不能无损翻译。

仅理想导体 + 自由空间/理想地面的已支持输入导出 `design.maa`；该文件使用 GAL 独立自动分段，注释明确写明它是参考几何，不是候选网格或兼容认证。未支持条件的 `compatibility.json.maa_export` 明确为 `UNSUPPORTED_MATERIAL_OR_SOIL_REPRESENTATION`。

## 数值与文件契约

```text
request.json / original.json 或 original.maa
invocation.json                    输入、调用预算与原件
design.maa                         仅能够诚实表达的子集
currents.json                      复电流、物理坐标、邻接/基方向、实际分段
simulation.json                    有界摘要 + 相对路径/大小/MIME/SHA256
compatibility.json                 适用边界、MAA 导出状态
environment.json                   版本、源码 hash、平台、用时
solver.args / solver.log           求解器自身导出的重放参数及日志
source/antenna_workflow/*.py        此次适配代码快照
pattern/far_field.json              原始 V/H/Total、绝对/归一化增益
pattern/azimuth.png / .svg
pattern/elevation.png / .svg
diagnostics.log                    子进程输出，不进模型摘要
manifest.json / completed.json     完整文件 hash 清单与最后完成标记
```

失败或中断保留 partial 文件和 `failure.json`；只有存在完成标记且 manifest/hash 核对正确的目录才是完整结果包。恢复是用户重跑到新目录，不自动重放副作用。CLI 的 stdout 只输出 `simulation.json`，不内联原始数组或日志；失败输出在 stderr。

`theta` 从 +Z 向下，`phi` 从 +X 朝 +Y；角度单位为度，`elevation=90−theta`。V 对应 θ 极化，H 对应 φ 极化。增益单位 dBi；normalized 值为同一次**全角网格峰值**归零，不按各切面独立归零。`-999` 保留为求解器下限标记，对应 normalized 为 null，不解释为真实 -999 dBi 或物理零场。绘图裁剪到峰值−40 dB，不改原始 JSON。

主瓣只报告采样峰值方向，最多八个示例；极点重复方位去重只作用于摘要。指定 `front_phi_deg` 时 F/B 是指定水平切面前后两方向的差，不是前/后半球极值比。没有指定前方时返回 null，不给全向天线捏造 F/B。尚未实现精确波束宽度、副瓣判定、自动带宽或一般斜切面分析。

一次 CLI 命令严格一次 solver 调用、零 LLM 调用，无优化内循环或自动调参；这个次数不包括将来整个工作流的预算。MCP 复用同一 worker，已完成结果的重复读取不新增计算。访谈阶段推进、optimizer、自动验收尚未完成；宿主结果适配已编码，运行验收另行记录。

## 复用选择与维护责任

- [pymininec](https://github.com/schlatterbeck/pymininec)：直接复用 1.2.0 的 Wire、MININEC、Excitation、Medium、Skin_Effect_Load、Impedance_Load、Angle；无内核拷贝或私有方法覆盖。数值依赖独立锁定，结果再记录实际源码 hash。其许可为 MIT，保持上游归属。
- [YAAS](https://github.com/ftomasczik/yaas-antenna-simulator)：已有 MAA 导入，但转换到 NEC、居中源和均匀网格范围不覆盖此次 MININEC 连接馈点映射。因此只补约束严格的小型格式映射，不拷贝其 GUI/工程管理或数值代码；以后出现满足语义的独立转换器可替换本模块。
- [plot-antenna](https://github.com/schlatterbeck/plot-antenna)：已调查其数据与任意方位/仰角切面入口。首版沿用 spike 的 matplotlib 公共 polar API 做少量数值→图表映射，明确双尺度与 floor 标签，不实现绘图引擎、插值或 3D。未来确需交互 3D/多后端时优先采用该工具，不扩张当前 renderer。
- CLI 使用标准库 `subprocess`，MCP 使用 [AnyIO `run_process`](https://anyio.readthedocs.io/en/stable/subprocesses.html) 的单 worker 作用域；两者共用输入准备、worker 命令与结果发布。绘图缓存用 `TemporaryDirectory`，没有另起调度、取消或任务状态机。
- MCP 使用[官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)，当前锁定 1.30.0；本包只做工具映射和结果读取，不实现 JSON-RPC、请求队列或会话 Runtime。后续升级单独检查协议与取消行为。

包采用 MPL-2.0；第三方依赖保留各自许可证，不随本包重新许可。原始 MMANA 源码和 GAL 程序均未复制到包中。

## 静态检查

```sh
uv sync --locked --extra engineering --extra mcp --extra dev
uv run --locked --extra engineering --extra mcp --extra dev ruff check src
uv run --locked --extra engineering --extra mcp --extra dev ruff format --check src
uv run --locked --extra engineering --extra mcp --extra dev mypy src
```

本轮没有运行软件测试、MCP 端到端调用或新适配器的数值验收。类型与 lint 检查不替代取消/EOF/Windows 实机验证。之前 compatibility spike 的已知结果用于选择语义，不自动等于这份新接线已经通过行为回归。后续获授权验收时应复用已有参考/冻结输入，核对数值和进程行为；无需重新要求用户跑整批 GAL。
