# antenna-design：独立工程入口

这是天线工作流的第一条工程链路：**明确输入 → pymininec → 原始数值、方向图、复算文件与有界摘要**。可将此目录整体移到独立仓库；不导入 MaigoKite、LangGraph、聊天记录或实验脚本，不启动 MMANA-GAL。

可通过 MCP + Skill 联合导入接入主应用，从已有数值导出候选设计包；不默认注册工具，不包含自动优化或制作验收。计算结果标记 `COMPUTED_NOT_VALIDATED`，交付包标记 `CANDIDATE_NOT_VALIDATED`：算出数值不等于已收敛、MMANA 兼容验收通过或可直接制作。

## 接入 MaigoKite 并交付文件

在仓库根目录执行，`--root` 填操作员选择的专用结果目录绝对路径：

```sh
uv sync --project extensions/antenna_design --locked --extra engineering --extra mcp
uv run --project extensions/antenna_design --no-sync antenna-design connect --root /absolute/antenna-project --output antenna-workflow.json
```

Windows PowerShell 同样使用上述 uv 命令，把路径改为 `D:/antenna-project`。`connect` 使用本机独立 Python 环境生成联合配置，包含 MCP 工具白名单、结果适配和完整 Skill；不安装、不启动、不改宿主设置、不覆盖输出文件。省略求解总限额保留已用项目的限额；若明确调整可加 `--max-solver-runs N`。配置绑定本机路径，换机器须重新生成。

在 **扩展 → 导入工作流** 预览并保存，然后 **应用并检查连接**。同名覆盖、白名单变化需要明确确认；扫描 Skill 同名会阻止导入，不静默替换。主应用沿用现有配置版本、审批、任务执行和文件预览，没有第二个 Workflow Runtime。

交付调用链：

```text
聊天选择 antenna-design Skill → 项目查询 → 输入/求解 → evaluate
→ antenna.export → 宿主校验资源并导入当前工作区 → 报告预览 / ZIP 下载
```

`antenna.export` 输入 delivery_id、title、purpose、assumptions、sources 和 evaluation；目标点必须都有同一设计的已完成 run_id。它只读取已有结果，不进行求解、模型调用或预留。输出固定十个资源：报告、几何、尺寸、模型、材料、评价、验证、施工调试、复算和 ZIP。ZIP 内含 design_brief 与逐 run_id 数值/图表/可用 MAA，以及已校验的环境证据；不复制任意私有文件或日志。

交付包保存在服务根目录 `.deliveries/<delivery_id>`，不被历史求解目录扫描计数。末尾原子发布 completed 标记；未完成目录保留、不覆盖。同编号同输入同源证据复用；更改输入需新编号。丢失回执用 `antenna.get_delivery`，不重新计算。只公开固定 `antenna://deliveries/{delivery_id}/{artifact_id}` 资源，单文件 ≤16 MiB、合计 ≤48 MiB；压缩包展开后同样有界，宿主不自动解压。

这是**候选方案文件交付**，不是“可以直接制作”的认证。export 不生成未计算的匹配网络，不把 PEC 当真实合金，不虚构公差、功率和机械验收。未达参考指标可交付带差额的当前候选；正式完成仍需扫频、收敛、匹配及实物验证。导出/下载新增路径尚不代表自然语言自主设计或 Windows GUI 已验收。

## 安装与运行

Python 3.14；在本目录执行，Linux / Windows PowerShell 的 `uv` 命令相同：

```sh
uv sync --locked --extra engineering
uv run --locked --extra engineering antenna-design solve examples/dipole.json --output output-dipole
uv run --locked --extra engineering antenna-design solve examples/yagi.json --output output-yagi
uv run --locked --extra engineering antenna-design solve examples/physical-copper.json --output output-copper
```

输出目录必须不存在，其父目录须已存在；再次运行使用新名字。不会覆盖历史数值，也不会自动删除失败证据。Linux 已复跑 MCP 调用同一计算 worker 的路径；这不等于此处每条 CLI 命令均已验收，Windows 入口尚未实机验证。

导入明确子集的 `.maa`：

```sh
uv run --locked --extra engineering antenna-design solve model.maa --mesh taper32 --output output-maa
```

`--mesh uniform80` 为每导线 80 段均匀网格；`taper32` 为 32 段、公开原生两端渐变，最小段长 `max(4r, L/512)`，最大 `L/16`。这两种是显式数值实验选择，不是对所有天线的推荐默认值。GAL 的 DM1/DM2/SC/EC 不自动翻译成候选网格；几何、馈点、材料条件必须保持，实际离散网格另存。

中文 Windows `.maa` 默认按 CP936 解码；其他文件可指定 `--maa-encoding utf-8-sig` / `cp1252` / `ascii`，不使用替换字符掩盖解码错误。当前名称字段限 ASCII；原始文件完整保留。

`--timeout 120` 为整个数值及绘图子进程的秒数限额，可设置 1～600 秒。超时或 Ctrl+C 杀掉并等待该子进程，不重试；完整数值已保存时发布可读取结果，否则保留失败状态。Ctrl+C 仍以取消退出，不当作成功。它不是系统级沙箱、CPU/内存/磁盘配额，也没有承诺断电恢复和目录持久化事务。

数值已经算出但导出/绘图失败时，摘要返回 `render_status=incomplete`，不发布本阶段的图和 MAA 文件，无需再求解。用保存的远场数据补画到**新目录**：

```sh
uv run --locked --extra engineering antenna-design render output-dipole --output output-dipole-plots
```

补画不运行求解器，也不改旧目录；原始计算仍在摘要中记录 `solver_runs=1`，本次 `invocation.json.solver_run_limit=0`。旧版没有 `numerical.json` 的结果仍可读取，但不能通过此入口补画。

`environment.json` 保留原数值计算环境；每次绘图另外保存 `render-environment.json` 和 `render-source/antenna_workflow/`，记录本次 Python、matplotlib、numpy 版本及绘图入口源码/hash。升级后补画不再被误认为使用原计算时的绘图实现。这些仍是本地证据，不扩大 MCP 公开资源清单。补画准备阶段在新目录创建后失败，也会尽力保存只含异常类型的 `failure.json`；满盘等情况下仍以缺少完成标记为准，不重放、不清空预留记录。

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
-m antenna_workflow.mcp_server --root <专用结果目录> --timeout 20 --max-solver-runs 3
```

也可以在已激活的独立环境运行 `antenna-design-mcp --root <专用结果目录>`。这是 MCP stdio 进程，不是网页或交互命令行；stdout 只传 JSON-RPC，诊断进入 stderr / 结果目录。不要把启动命令交给模型执行，也不要同时通过多个客户端共享同一结果目录。

### 高层工具

| 工具 | 参数 | 返回与边界 |
| --- | --- | --- |
| `antenna.project_status` | 可选 `limit`（1～20，默认 10）和 `before_sequence` | 持久项目 ID、剩余求解额度、最近运行编号和恢复建议；按返回的 `next_before_sequence` 查询更早记录，不启动 worker |
| `antenna.model_validate` | `model`：现有 `EngineeringRequest` JSON 对象 | `INPUT_ACCEPTED_NOT_SIMULATED`；校验单位、输入范围、支持字段与基本连接，不启动 solver，不称为物理验证 |
| `antenna.evaluate` | 最多 12 个频点目标/已有 run_id、地面和前向切面、系统阻抗；可选下一批次数 | 校验已发布证据，比较同一设计的采样点指标与预算缺口；只读、零求解，不预留次数，不宣称全带/工程验证 |
| `antenna.solve` | 同一 `model`，加 `run_id` | 一次 solver，绘图状态单列；`COMPUTED_NOT_VALIDATED` 摘要、资源引用与标准 MCP ResourceLink，不内联原始数组/日志 |
| `antenna.get_result` | 已有 `run_id` | 不需要原始输入，取回已发布结果；不启动 worker，不重新求解 |
| `antenna.render` | 源 `run_id`、新 `output_run_id` | 核验并复制数值证据到新目录，只补画；本次求解次数为零 |
| `antenna.cancel` | 运行中的 `run_id`；宿主自动清理另带 `operation_id` | 等待 worker 退出及证据保存后返回 `worker_stopped`；手动取消须有用户意图 |

例如 `model` 使用 `examples/dipole.json` 的对象内容，`run_id` 使用 `dipole-001`。**不是传本机 JSON 路径**，MCP 不提供任意文件导入；`.maa` 仍走前述显式 CLI 入口。

`run_id` 限 1～64 个小写 ASCII 字母、数字、下划线和连字符，首位为字母或数字。新编号创建专属目录；已完成的同编号、同输入可重复读取，`reused=true`、`solver_runs_this_call=0`。同编号不同输入、未完成目录、损坏证据均拒绝，不自动重新求解；原件保留，用户决定是否以新编号再算。这不是通用 exactly-once 或断电恢复系统。

`model_validate` 不做原生网格/交点/馈点 pulse 映射；这些由 `solve` 在真实 solver 对象上检查。两者都不代表网格收敛、MMANA 兼容认证或制作验收。工具 schema 由 Pydantic 生成，SDK 按输出 schema 校验成功回复；输入由 Pydantic 校验，以避免协议层报错回显完整输入。

### 计算后的目标评价

`evaluate` 用 `points[].frequency_hz / min_forward_gain_dbi / min_front_to_back_db / run_id` 对比已保存结果。明确提供 `ground`、`front_phi_deg`、`elevation_deg` 和 `system_impedance_ohm`；方向必须落在原始采样网格上。它读取经过 manifest/hash 校验的 request、summary 和 far-field，不从 PNG 估值，也不用峰值代替指定方向的增益。F/B 为同仰角的前向与反向单点增益之差；floor 标记不充当数值。

- `comparison_role=reference`（默认）只做参考差额比较；选定为设计目标后才使用 `design_objective`。返回 `at_or_above / below / unavailable`，仅设计目标模式产生 `sampled_targets_met / needs_optimization`。无论结果如何，顶层始终是 `SAMPLED_COMPARISON_NOT_VALIDATED`。
- `matching=unspecified`（默认）允许馈电方案待定，仍可评价辐射。`separate_network` 明确另行设计匹配；只有 `direct_feed` 且提供 `max_direct_swr` 时才检查直连门槛。`model_input_impedance_ohm` 是包含模型显式馈点负载的原生 R+jX；参考系统阻抗既不是目标原生电阻，也不会模拟匹配网络。
- 不同频点必须有相同几何、导线顺序、馈点、材料、地面和数值设置，名字与频率不参与设计 hash。新 revision、网格比较分开评价，不能挑不同设计的最佳频点拼成一副达标天线。请求/结果频率、参考阻抗/SWR、角网格、峰值和原请求 F/B 矛盾时拒绝评价。
- 缺失点用 `run_id=null`；`planned_new_solves` 是含缺失点的下一批新求解数，省略时仅估算缺失点。回执给出当前项目额度、增加总限额所需次数及最低总限额，不预留、不修改设置；实际执行仍由 `solve` 原子准入。额度耗尽仍可评价。
- 结果不超过 16 KiB；超过时提示减少每批频点，不截断证据或警告。读取的原始数组不进入回复。预算快照只用于计划，不保证并发调用后仍有相同剩余额度。

匹配后性能、连续带宽、网格收敛、材料安装与实测始终单列未验证。此工具是已有证据的确定性比较，不是优化器、工作流状态机或完整制作包生成器。

三个结果工具共用 `schema_version=1` 的回执，包含 `run_id / reused / solver_runs_this_call / summary / resources`。`get_result` 和 `render` 的 `solver_runs_this_call` 均为零；`render` 回执中的 `run_id` 是新的输出编号。宿主按这一传输契约适配，不需要安装或导入本包的 Python 类型。`summary.render_status` 为 `complete` 或 `incomplete`；旧版没有该字段的已完成包按 complete 读取。

开始或继续前先 `project_status` 查询当前项目。失去回执或本地导入失败后，先 `get_result` 查询原编号；忘记编号时从项目记录找回，补画失去回执时查询 **output_run_id**。返回不存在或未完成不意味着操作从未执行，不应自动改编号重算。补画要求源数值完整、输出目录不存在，不覆盖已有结果，也不自动修复损坏数据。

错误回执以 `structuredContent.code` 区分 `RUN_ACTIVE`（仍运行）、`RUN_NOT_FOUND`（无目录）、`RUN_INCOMPLETE`（无完成标记）、`RESULT_CORRUPT`（已发布证据无效）、`RUN_INPUT_CONFLICT`（不同输入占用编号）和 `RUN_ID_IN_USE`（补画输出已占用）。`BUSY` 表示另一项工作占用计算槽。不从文件存在推测任务正在运行；服务重启后无完成标记的目录只报未完成，不自动重放。

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

- 一个服务实例最多一个活跃 solve 或 render；并发调用返回 `BUSY`，不排队、不重试。新计算最多一次 solver，内部 LLM 调用为零；取回、补画或复用时本次 solver 次数为零。
- 一个专用 `--root` 对应一个项目；`.antenna-project.sqlite3` 保存稳定项目 ID、求解尝试预留、输入 hash、补画来源和额度调整记录。新目录默认最多 **3 次求解尝试**，是单方案路径的保守防循环限制，不是工程最优次数。`--max-solver-runs N` 可由操作员显式调整总限额；省略时保留数据库中的值，重启、换 run_id 或聊天回合不会清零。降低到已用次数以下会禁止新求解，不删除历史；模型工具不能修改额度。
- `solve` 在创建目录/启动 worker **之前**用 SQLite 事务预留 1 次；失败、超时、取消及崩溃都不自动退回。因此 `reserved_attempts` 是保守准入次数，不是假称精确 solver 调用次数。已登记但缺少文件的编号返回 `RUN_RESERVED`，不自动重放；额度用完返回 `SOLVE_BUDGET_EXHAUSTED`，仍可查询、取回和补画。补画登记来源，但不扣求解额度。
- 初次接管旧结果根目录时，每个符合 run_id 的已有目录保守计 1 次，包括旧补画和未完成目录；不猜测过去是否实际执行。重启发现新增旧目录也会纳入，已登记目录不重复计数。已用额度不会因删除结果目录而释放。专用目录只供这一个服务管理；不要并行使用 CLI 或其他进程写入。
- 数据库只保存准入事实，不保存 worker 状态机：`project_status` 中的 active 来自当前 AnyIO owner，完成/未完成/损坏/缺失来自现有证据校验。旧进程强杀后不推断 worker 已退出，不自动恢复执行。备份需保留整个结果目录和 SQLite 数据库；手工删除/篡改数据库、改用新根目录不在此本地可信操作员边界内。
- **整体工作流 LLM/token 预算、自动阶段推进及独立 CLI 的累计预算尚未实现**；CLI 单次 solve 仍受原有超时/次数约束。这里没有将 MCP 的准入限额宣称为整个 Agent 的用量管理。
- 默认 worker 时限 20 秒，可由操作员设置 1～600 秒；覆盖数值与绘图，不是完整 MCP 往返时限。MaigoKite 的“工具请求超时（秒）”默认 30，可按服务设置 1～660；例如 worker 120、宿主 150，为清理和回执留出余量。保存后须应用扩展配置。资源导入仍限 30 秒，不跟随计算时限扩大。
- MCP 请求取消和 stdio EOF 通过 SDK 取消 handler；AnyIO `run_process` 在退出作用域时清理并等待 worker。清理后核验 `numerical.json`：数值完整则发布结果，未完成的图不进入资源清单；数值不完整则保留失败状态。取消仍传播，不把保存数值当作取消请求成功执行。不会把阻塞 CLI 放到线程里后只取消等待。
- MCP 的协议取消回包可能先于清理完成，不能当作进程退出回执；Python MCP SDK 1.30 的本地 `call_tool` 取消也不会自动发送协议取消。显式启用结果适配的 MaigoKite 调用通过公开 `_meta` 传随机调用身份，取消或超时后调用 `antenna.cancel(run_id, operation_id)`，最多额外等待 10 秒。服务用原生 AnyIO CancelScope 停止对应 worker，等待清理后才确认。身份不符不取消别人的调用，共享 MCP 连接保持可用。
- 停止回执丢失、旧服务不支持 cancel 或断线时，宿主保留“远端停止未确认”提示，不谎报停止、不重试计算。已完成结果不被撤销；取消后仍可 get_result 取回保留数值。此桥接只适用于该扩展，不宣称通用 MCP 均可取消。没有采用实验性 MCP Tasks 协议或新建任务存储、通用取消框架。
- Linux 另外处理 SIGTERM；Windows 使用 stdio 关闭或 Ctrl+C 的正常退出路径。SIGKILL、Windows 强制终止、断电不保证 worker 清理；partial 目录不会自动恢复/重放。这里没有实现进程树监督或系统级资源配额。

### MaigoKite reference host

主应用已编码可选结果适配，接入步骤见[配置指南](../../docs/user-guide/Configuration.md#81-天线计算与方向图)。MCP 设置中显式选择“天线结果与方向图”，保存后应用；默认为标准工具结果。不根据服务名、端口或任意 `ResourceLink` 自动开启。

```text
现有工具权限与审批 → antenna.solve / get_result / render → 有界结构化回执
→ 同一个 MCP 连接 resources/read → 校验 URI / MIME / 大小 / SHA256
→ 当前对话文件夹 antenna-results/<结果内容标识>/ → 聊天 PNG 预览 / 点击打开
```

桥接位于主应用 `python/agent/integrations/antenna.py`，本包不导入它。MCP 仍按现有高风险策略审批，批准范围包括该次结果资源读取和本地副本；资源读取不绕过工具审批。每次读资源及写文件前重新查询当前对话目录和权限，降为只读后不再开始后续写入。正在执行的单次文件写入不能强杀，取消会等待它结束，不把线程留在后台继续写。

服务器证据包保留原布局；宿主副本采用平面文件名（例如 `azimuth.png / elevation.png / far_field.json / simulation.json`），原始资源路径和 URI 保存在最后写入的 `import-receipt.json`。目录标识来自服务命名空间、run_id 与资源内容 hash；同一结果跨工具调用补齐同一目录，不覆盖已编辑文件。源目录与当前对话目录可不同，宿主不把 MCP URI 当成本机路径或 HTTP 地址。数值完整但图不完整时，仍导入数值并提示补画，不展示残缺图片。

宿主每次导入最多 10 个文件、单文件 16 MiB、合计 48 MiB，整个读取/写入阶段限时 30 秒（不可中断的本地写入清理另计）。模型只收到有界摘要、资源与本地文件引用，不收到这些数组或图片字节。部分导入失败时保留已核验文件和警告，`import_complete=false`，不把成功计算改记为失败，也不自动重算；取消同样不自动重放远端副作用。

这是现有聊天链路的结果适配；配合下述 Skill 推进输入整理、计算和取回/补画，不是确定性阶段引擎、自动优化或制作验收。已在 Linux 通过真实 MCP/solver 与生产 Core 的显式工具路径复跑，未调用模型；不代表自然语言自主规划、GUI 或 Windows 实机验收通过。

### 安装工作流 Skill

包内包含 `skills/antenna-design/SKILL.md`，只依赖已连接的 MCP 与宿主原有文件工具。查看或导出（不安装、不启动 solver）：

```sh
uv run --no-sync antenna-design skill
uv run --no-sync antenna-design skill --output /absolute/skills/antenna-design/SKILL.md
```

Windows 将输出路径改成自己的绝对路径。导出不覆盖已有文件；可在“扩展 → Skill”导入这份 Markdown，或将其父技能目录纳入已有扫描配置，随后应用。更新扩展代码后也需重新加载 MCP 工具目录。

Skill 区分按性能目标原创设计与按已有尺寸复算：前者不要求原厂图纸，未知条件可保留为明确初始假设。它用现有文件工具保存 brief、模型版本、run_id 和评价；对比指标后再准备下一批参数/频点/预算，未知回执先取回、图失败只补画。仅取图不扩大为重新设计。它不绕过审批，不增加 Agent 框架，也不将自然语言中的次数约定伪装成已实现的跨回合硬预算。已导入的 Skill 是副本，需主动更新并应用；新工具也需在现有 MCP 设置中重载/启用，不会自动修改用户配置。

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
solution.json / numerical.json     原始数值摘要与固定数值文件 hash，先于绘图保存
simulation.json                    有界摘要 + 相对路径/大小/MIME/SHA256
compatibility.json                 适用边界、MAA 导出状态
environment.json                   版本、源码 hash、平台、用时
solver.args / solver.log           求解器自身导出的重放参数及日志
source/antenna_workflow/*.py        此次适配代码快照
pattern/far_field.json              原始 V/H/Total、绝对/归一化增益
pattern/azimuth.png / .svg
pattern/elevation.png / .svg
rendered.json                      四张图完整后的 hash 清单；失败时不发布图
diagnostics.log                    子进程输出，不进模型摘要
manifest.json / completed.json     完整文件 hash 清单与最后完成标记
```

只有存在完成标记且 manifest/hash 核对正确的目录才能经 MCP 读取；`completed.json` 表示已发布结果，**不等于图已完整或工程已验证**，需同时查看 `render_status`。数值已完成的绘图失败/超时可以发布数值结果，补画用新目录；数值未形成完整证据时保留 partial 和 `failure.json`，不自动重放。CLI 的 stdout 只输出 `simulation.json`，不内联原始数组或日志；失败输出在 stderr。

`theta` 从 +Z 向下，`phi` 从 +X 朝 +Y；角度单位为度，`elevation=90−theta`。V 对应 θ 极化，H 对应 φ 极化。增益单位 dBi；normalized 值为同一次**全角网格峰值**归零，不按各切面独立归零。`-999` 保留为求解器下限标记，对应 normalized 为 null，不解释为真实 -999 dBi 或物理零场。绘图裁剪到峰值−40 dB，不改原始 JSON。

主瓣只报告采样峰值方向，最多八个示例；极点重复方位去重只作用于摘要。指定 `front_phi_deg` 时 F/B 是指定水平切面前后两方向的差，不是前/后半球极值比。没有指定前方时返回 null，不给全向天线捏造 F/B。尚未实现精确波束宽度、副瓣判定、自动带宽或一般斜切面分析。

一次新 `solve` 最多一次 solver 调用、零 LLM 调用；`render` 和结果读取不调用 solver，无优化内循环或自动调参。MCP 另对同一结果根目录的累计求解准入限额记账，不代表完整工作流的 token 预算。Skill 提供文件记录和继续操作的指引；确定性阶段准入、optimizer、自动验收尚未完成，运行验收另行记录。

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

2026-10-22 已用真实 MCP/pymininec/matplotlib 复跑结果读取、导入保护、补画、主动取消、请求超时及进程重启后的取回；生产 Core 的显式工具/审批路径也已复跑，模型调用为零。停止回执不可得的安全提示另用 stub 检查，不算真实断线验收。静态检查与上述定向复跑均不等于数值收敛、GUI、Windows、强杀/断电或完整工作流验收；原 compatibility spike 的参考结果不能替代这些边界。
