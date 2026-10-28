# antenna-design：独立工程入口

这个包接受明确的薄线模型和目标，使用 pymininec 计算，也可用 SciPy 搜索导线尺寸，随后复核网格、补画并导出候选文件。可单独使用或通过 MCP 接入 MaigoKite；不导入主应用、LangGraph、聊天记录或实验脚本，不启动 MMANA-GAL。

可通过 MCP + Skill 联合导入接入主应用，执行有界尺寸优化、从已有数值导出候选设计包；不默认注册工具，不包含任意拓扑自动设计或制作验收。计算结果标记 `COMPUTED_NOT_VALIDATED`，交付包标记 `CANDIDATE_NOT_VALIDATED`：算出数值不等于已收敛、MMANA 兼容验收通过或可直接制作。

## 接入 MaigoKite 并交付文件

### 领域状态与证据身份

`design` 现在将 `WorkflowState` 存入同一项目 SQLite，依次记录 `DESIGN_READY → SEARCH_RECORDED → VALIDATION_RECORDED → DELIVERED`。它表示最后已提交的证据里程碑，不表示 worker 仍活跃、可以自动续跑或已经允许制作。每次写入校验 revision、输入/目标身份和已选方案；中断后用 `antenna.get_design_state(run_id)` 查询，再按原结果编号检查证据。若包已完整发布而交付里程碑尚未提交，`get_design_state / get_delivery / export` 会核验完整包、原设计输入、目标、所选结果和验证摘要后，幂等补记 DELIVERED；缺失/半成品不推进，证据冲突报错，不重算、不重写原文件。查询因此可能修复项目库元数据，MCP 注解明确为非破坏性、幂等操作。历史任务没有状态记录时不猜测、不回填。完整需求访谈、Brief 和制作验收仍不在这个有限状态记录的覆盖范围。

- `physical_design_sha256`：有序导线几何、半径、馈点位置/电阻、实际材料参数。频率、网格、地面、激励幅相、采样与阻抗参考不改变这个身份。
- `simulation_configurations[run_id]`：该次计算的频率、地面、网格、激励、方向图配置和参考阻抗；多频比较还核对排除频率后的计算口径，网格复核仅允许改变分段数。
- `criteria_sha256`：采样频点、工程门槛、方向与馈电口径，不含结果编号或计划计算次数。
- 旧 `design_sha256` 保留原来的算法和含义，不当作物理身份使用。新评价/交付回执为 v2，读取兼容 v1；同一新物理身份对应多个旧 hash 时该旧字段为 null。原始文件和旧 ZIP 不重写。

这些是输入身份，不是“结果缓存键”或数值等价证明。求解器/依赖版本、源代码快照和文件 hash 仍由每次运行的 environment/manifest 记录；缺失的旧环境不补造。

职责边界：`assessment.py` 组合已有搜索、网格与匹配判据；`delivery_render.py` 只绘制文档；`delivery.py` 校验证据、限制读写大小并发布不可变包。使用现有 SciPy、scikit-rf、SQLite 和 MCP SDK，没有新增执行框架或宿主依赖。

本轮改动只进行静态检查，没有重跑历史求解或行为验收。

在仓库根目录执行，`--root` 填操作员选择的专用结果目录绝对路径：

```sh
uv sync --project extensions/antenna_design --locked --extra engineering --extra mcp
uv run --project extensions/antenna_design --no-sync antenna-design connect --root /absolute/antenna-project --output antenna-workflow.json
```

Windows PowerShell 同样使用上述 uv 命令，把路径改为 `D:/antenna-project`。`connect` 使用本机独立 Python 环境生成联合配置，包含 MCP 工具白名单、结果适配和完整 Skill；不安装、不启动、不改宿主设置、不覆盖输出文件。省略求解总限额保留已用项目的限额；若明确调整可加 `--max-solver-runs N`。配置绑定本机路径，换机器须重新生成。

在 **扩展 → 导入工作流** 预览并保存，然后 **应用并检查连接**。同名覆盖、白名单变化需要明确确认；扫描 Skill 同名会阻止导入，不静默替换。主应用沿用现有配置版本、审批、任务执行和文件预览，没有第二个 Workflow Runtime。

交付调用链：

```text
聊天选择 antenna-design Skill → project_status → plan_design → design
→ MCP 内部搜索 / 网格复核 / 补画 / export → 宿主导入工作区 → 报告预览 / ZIP 下载
```

`antenna.export` 输入 delivery_id、title、purpose、assumptions、sources 和 evaluation；目标点必须都有同一设计的已完成 run_id。它只读取已有结果，不进行求解、模型调用或预留。输出固定十个资源：报告、几何、尺寸、模型、材料、评价、验证、施工调试、复算和 ZIP。ZIP 内含 design_brief 与逐 run_id 数值/图表/可用 MAA，以及已校验的环境证据；不复制任意私有文件或日志。

交付包保存在服务根目录 `.deliveries/<delivery_id>`，不被历史求解目录扫描计数。末尾原子发布 completed 标记；未完成目录保留、不覆盖。同编号同输入同源证据复用；更改输入需新编号。丢失回执用 `antenna.get_delivery`，不重新计算。只公开固定 `antenna://deliveries/{delivery_id}/{artifact_id}` 资源，单文件 ≤16 MiB、合计 ≤48 MiB；压缩包展开后同样有界，宿主不自动解压。

这是**候选方案文件交付**，不是“可以直接制作”的认证。export 不生成未计算的匹配网络，不把 PEC 当真实合金，不虚构公差、功率和机械验收。未达参考指标可交付带差额的当前候选；正式完成仍需扫频、收敛、匹配及实物验证。导出/下载新增路径尚不代表自然语言自主设计或 Windows GUI 已验收。

## 修改频点、架设与匹配参数

用户通过对话提出调整，由 Skill 整理明确输入，不需要改求解器或 Agent Runtime：

- `antenna.prepare_revision` 从已有 `source_run_id` 读取核验过的模型，接收新的 `targets`、可选 `installation` 与 `material`。新 targets 的 run_id 必须为空，不能把旧频率/旧安装结果用于新方案。它只准备输入，后续沿用 solve 或 plan_design/design。
- `installation.feed_height_m` 是馈点离地高度（米）；`rotation_xyz_deg` 是绕原馈点的外禀 XYZ 角（度）。地面使用 targets.ground；前向方向和仰角由 targets 指定。支持刚体旋转/抬高及当前地面模型，不模拟屋顶、建筑、桅杆、支架；notes 保留这些未模拟条件，不把文字当作计算输入。
- `antenna.matching` 接收已完成的 `evaluation` 和 `options={"mode":"per_frequency_l_network","reference_plane":"antenna_feed"}`；评价必须显式选择 separate_network。复用 scikit-rf 2.2.0 的 Circuit 与 SciPy least_squares，计算每频点理想 L 型网络，不消耗电磁求解次数。不同点的元件需要切换/调谐，不是固定多波段匹配、巴伦或功率设计。
- design/export 可带 `matching_options`，报告输出拓扑及 L/C，ZIP 保存 `matching/l-networks.json` 的全精度参数、原始阻抗和限制；不修改裸天线增益/F/B，也不把高直连 SWR 隐藏成已达标。元件损耗、Q、公差、馈线与实际环境仍未验收。
- `antenna.evaluate_matching` 接收同一物理模型的已保存 `evaluation`、固定 `network={topology,components,reference_plane}` 和可选 `max_swr`。components 按 series、shunt 排列，使用 H/F 的正数或零值 omit；工具逐点复用同一组元件，不重新调谐、不插值缺失频点。未指定 max_swr 只报告 SWR，不默认添加验收指标。
- export 可带最多六组 `matching_checks`（各为 evaluate_matching 的完整输入），保存每组元件、原始负载、匹配后 SWR 与邻频辐射比较。ZIP 的 `matching/check-N/` 保留输入、结果及对应数值证据；不同材料、几何或网格不能混入同一个匹配复核。多个独立网络并不构成已实现的多波段切换器，理想匹配也不补偿裸天线的增益/F/B 缺口。

接入升级后重新生成联合配置，预览新增工具再应用；不自动修改已有用户设置。旧包没有 matching_options 仍可取回，原文件/hash 不改变。

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
| `antenna.plan_design` | OptimizeInput + title/purpose；可选 assumptions/sources、verification_segments、mesh_tolerances | 只读计划：初始化加完整一代的最低次数、达到暖机代数的推荐次数、验证预留与额度缺口；不改变项目限额 |
| `antenna.design` | 与 plan_design 相同；run_id 同时作为交付编号 | 一次调用完成搜索、两个更细网格、敏感性比较、补画和交付；原始证据保留，失败不自动重试，不等于制作验收 |
| `antenna.get_design_state` | `run_id` | 查询领域里程碑，并核验已发布交付的状态对账；不启动 worker、不恢复搜索、不为历史任务推断状态 |
| `antenna.project_status` | 可选 `limit`（1～20，默认 10）和 `before_sequence` | 持久项目 ID、剩余求解额度、最近运行编号和恢复建议；按返回的 `next_before_sequence` 查询更早记录，不启动 worker |
| `antenna.model_validate` | `model`：现有 `EngineeringRequest` JSON 对象 | `INPUT_ACCEPTED_NOT_SIMULATED`；校验单位、输入范围、支持字段与基本连接，不启动 solver，不称为物理验证 |
| `antenna.evaluate` | 最多 12 个频点目标/已有 run_id、地面和前向切面、系统阻抗；可选下一批次数 | 校验已发布证据，比较同一设计的采样点指标与预算缺口；只读、零求解，不预留次数，不宣称全带/工程验证 |
| `antenna.optimize` | 新 run_id、model、dimensions、targets；可选 max_solver_runs、random_seed、population_size、评分尺度 | SciPy 参数搜索，按改善/停滞/耗时动态领取额度，pymininec 复核每个候选的全部频点；不调用模型，不自动改变项目总限额 |
| `antenna.get_optimization` | 优化的 run_id | 取回最佳候选与停止原因、重新核验数值证据；不恢复种群，不重新求解 |
| `antenna.prepare_revision` | 已完成 source_run_id、新 targets；可选 installation/material | 从核验模型准备新输入；旧 run_id 不充当新结果，不启动 solver，不预留额度 |
| `antenna.matching` | 已完成 evaluation、逐频 L 型网络 options | scikit-rf/SciPy 理想匹配；按频点调谐或切换，不等于固定多波段网络 |
| `antenna.evaluate_matching` | 同设计 evaluation、固定 network、可选 max_swr | 同一组理想 H/F 元件逐点复算，不重新调谐、不插值；无 max_swr 时只报告数值 |
| `antenna.export` | 新 delivery_id、title/purpose、同设计 evaluation；可选搜索/网格/匹配证据 | 读取已有数值，生成固定十项资源及 ZIP；不求解，交付仍为候选 |
| `antenna.get_delivery` | 已有 delivery_id | 核验并取回候选包，补记匹配的交付里程碑；不重新导出、不新增求解 |
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

### 有界尺寸优化

完整 MCP 参数示例见 [examples/yagi-optimize.json](examples/yagi-optimize.json)，工具接收该 JSON 对象而非文件路径。示例目标只是演示搜索输入，不是已达标参数或 OB11 用户目标；省略次数与种群时使用自动分批策略，不会自动提升项目总限额。

端到端输入见 [examples/yagi-design.json](examples/yagi-design.json)：同一对象先传给 plan_design，再传给 design。示例未声明工程容差，不会输出收敛通过；它也不是 OB11 的尺寸或验收目标。

`antenna.optimize` 复用已安装的 [SciPy differential_evolution](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.differential_evolution.html) 与 [LatinHypercube](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.qmc.LatinHypercube.html) 生成初始种群，不维护自研遗传算法。已比较 [antenna-optimizer](https://github.com/schlatterbeck/antenna-optimizer)：其 PGApack/PGApy + PyNEC 路线需要另引优化器原生依赖和 NEC2++ 求解器；本批保留已有 pymininec 证据链、直接使用已有 SciPy，避免同时迁移求解器与文件契约。

- 完整 model 作为种子，dimensions 指定 wire（1-based）、parameter（length_m / center_x_m / center_y_m / center_z_m）及米制 lower/upper。修改长度保留中点与方向，修改中心使用绝对坐标；方向、半径、材料、网格、馈电和未列尺寸不变。最多 24 个变量，只支持孤立单元与中心馈电，不自动改拓扑或匹配网络。
- targets 复用 EvaluationInput，要求 design_objective、最多 6 个频点，不接受 planned_new_solves。全套旧 run_id 必须与种子物理模型完全一致，否则拒绝；可复用旧基线而不消耗次数。没有旧结果则全部省略，基线计算也计入本批预算。
- objective_mode 固定为 sampled_feasibility：寻找达到所有已请求采样门槛的方案，不最大化达标后的额外收益。min_forward_gain_dbi / min_front_to_back_db 可以省略，不强迫全向天线填写 F/B；省略项返回 not_requested，全部辐射门槛省略时 radiation_status 为 not_evaluated。至少需要一项辐射门槛或显式直连 SWR 门槛。gain_scale_db/front_back_scale_db/swr_scale 只归一化缺口，不改变达标标准；指定方向 total gain、同仰角反向 F/B、馈点直连 SWR 不混作 peak/realized gain 或匹配后指标。
- 目标值为各频点未达增益、F/B 及显式直连 SWR 门槛的归一化平方和。gain_scale_db / front_back_scale_db / swr_scale 默认 1，均可显式选择；已超额的某项不抵扣其他项缺口。方向取原始角度采样，不用峰值代替前向增益。缺失或无效数值不能成为最佳候选。
- random_seed 默认 0，population_size 为**总种群数**，省略时取 max(8, 2×自由参数数)，也可显式设为 5～256。这是低成本探索的启发式，不是最优种群或收敛保证；通过 SciPy 公开 init 接口传入。单 worker、polish=False；相同参数在本次搜索内复用，不跨请求仅凭几何套用缓存。
- max_solver_runs 可省略。默认从剩余次数中留出最多两个全频点扫算的验证余量，搜索上限取其余次数与 4096 的较小值；余量不是独占预留，后续仍须查项目账本。显式上限必须在 1～4096 且不超过剩余次数，验证余量此时由调用者另留。候选机会最多 floor(搜索上限/频点数)，无效几何也占机会，实际频点准入才扣项目次数，旧基线复用不扣。
- 资源批次与 SciPy 代数分开：公开 callback 每完成一整代比较最佳缺口评分，初始化不算一代。默认至少完成 minimum_generations=5 代，且连续 stagnation_generations=5 代没有超过 0.1% 和 1e-8 的改善才因停滞停止。每个完整候选实测耗时乘 1.25 的高水位限制后续准入；预算/时限仍可提前停止，不表示充分探索。
- 参数搜索、所有频点与保存共享 worker 时限（最多 600 秒），预留 max(5 秒, 10%) 收尾余量；不够一个候选时主动结束。冷启动尚无估计的第一个候选及异常变慢的求解仍受父进程硬超时限制。预算回执公开硬上限、已领取次数、批次、停滞和实际耗时；一次 MCP 调用完成迭代，内部模型调用为零，不自行扩大项目限额/时限。
- `.optimizations/<run_id>/request.json` 保存输入，progress.json 原子更新最佳完整候选与分配回执，batch-NNNN.json 记录续批时的改善依据；每个频点落在普通 `<run_id>-cNNNN-fN` 数值目录并有原证据清单。查询重验最佳输入、指标与 hash，用量从 SQLite 读取，避免中断漏计。不保存/恢复 SciPy 种群。
- 搜索不为每个候选绘图；最佳 run_ids 可以 get_result/evaluate，随后用 render 在新编号补画，再 export。未完成候选留作诊断，不把不同候选的频点混合。best 与 baseline_score 表示是否改进，stop_reason 区分采样目标满足、停滞、时间/次数不足、优化器结束、失败和中断。
- 使用同一 AnyIO 根作用域管理优化子进程；宿主取消会停止并等待它退出，不新增 cancellation tree。断线/重启先 get_optimization，绝不重放同一优化编号；继续只能在明确的新预算下建立新搜索，原始输入和结果不覆盖。强杀服务仍可能留存 worker，interrupted 不等于已确认停止。

完整交付使用同一输入加 title/purpose 调用 plan_design/design。服务自动留出两层全频网格复核次数；默认按原网格增加约 10%/20%，在 pulse 上限内选择两个更细偶数网格，也可显式指定。声明 mesh_tolerances 时必须给 impedance_ohm；gain_db/front_back_db 可按需求给出。只判断已声明的容差，所需方向数据低于数值下限时记录 incomplete_evidence；整个容差对象省略则只记录敏感性，不宣称收敛。工作流共享配置时限，搜索分配 65%，其余用于复核/绘图/交付；同步导出清理需等待线程退出，不杀线程。

export 可附 optimization_run_id 和 verification；ZIP 增加 optimization/、verification/ 与对应原始证据，旧交付保持不变。新计算的相同源码快照在项目内通过硬链接复用，不支持硬链接时独立复制；既有证据不删除。MCP 官方 progress 通知更新同一工具活动，不插入聊天正文；优化/评价只给模型有界摘要，完整回执仍供 UI 查看。

更新代码后，重新生成接入配置并在扩展页面确认 plan_design/design 等白名单和 Skill 副本，保存并应用；不会静默修改现有配置。当前新项目默认 3 次只适合单次试算；plan_design 会报告完整工作流的额度缺口，而不是先耗尽次数。

2026-10-23 的 OB11 r04 实际验收已通过真实 MCP 的规划、搜索、两档网格复核、补图、交付及宿主导入：399 次新增求解、131 个完整候选、5 代搜索，零模型调用。另经生产 LangGraph `/tool` 入口验证审批、最终回复、进度事件和真实绘图 worker 取消；完成结果可在 MCP 重启后零求解取回。验收不覆盖自然语言模型决策、HTTP/WebSocket 与 GUI 渲染、Windows 或运行中断电恢复；指标未全达标，交付仍是未验证候选。范围和记录见 [更新日志](../../docs/project/Changelog.md#2026-10-23-ob11-工作流实际验收)。

`solve / get_result / render` 共用 `schema_version=1` 的计算回执，包含 `run_id / reused / solver_runs_this_call / summary / resources`。`get_result` 和 `render` 的 `solver_runs_this_call` 均为零；`render` 的 run_id 是新输出编号。`summary.render_status` 为 `complete` 或 `incomplete`；旧版无此字段的已完成包按 complete 读取。

`design / export / get_delivery` 返回候选包回执，使用 `delivery_id`、`kind=antenna_design_delivery`、`status=CANDIDATE_NOT_VALIDATED`、物理身份、逐 run 仿真身份及固定十项资源，另有辐射状态、直连匹配状态与下一步。新包的 matching_status 汇总已提供的全部网格：任一层 SWR 超标不能被基础网格达标掩盖；报告和 validation 使用同一结论，完整各层数据仍保留。历史包缺少该字段时返回 null，表示未记录，不假定通过、不改写旧 ZIP。旧 design_sha256 仅保留历史语义。`design` 的 run_id 同时作为 delivery_id；不能拿它的回执当作单次 solve 结果。宿主兼容 v1/v2 传输契约，不安装本包 Python 类型。

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

- 一个服务实例只有一个计算槽，solve、optimize、design 或 render 占用期间，并发计算返回 `BUSY`，不排队、不重试。solve 一次求解；optimize/design 按预算执行多个候选或网格，内部 LLM 调用为零；取回、补画或复用不增加求解。
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

以上图示只画单次计算结果；`design / export / get_delivery` 同样导入候选包，优化/评价/规划另返回紧凑指标和预算。宿主不执行数值优化算法，也不提供新的阶段引擎。Linux 已有真实 MCP 搜索交付、生产 Core 显式工具审批/取消、重启取回与文件导入记录；范围见 [路线图](../../docs/project/Roadmap.md#2026-10-23-天线恢复与生产调用验收)。完整 GUI、自然语言自主规划、Windows 和运行中强杀恢复仍未验收，不能用单次求解的旧记录代替其他路径测试。

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

一次新 `solve` 最多一次 solver 调用、零 LLM 调用，本身不做参数搜索；搜索由 `optimize / design` 提供，每个新频点仍扣项目准入。`render` 和结果读取不调用 solver。Skill 指导文件记录和后续操作，不替代确定性阶段准入、工作流级 token 预算或制作验收，这些能力仍未完成。

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
