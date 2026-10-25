---
name: antenna-design
description: 用已连接的天线 MCP 从性能目标推进原创细线天线设计，整理输入、计算、评价并导出设计包，继续已有结果的取回或补画。也适用于明确尺寸的复算；不适用于 PCB、微带或面导体仿真。
---

# 天线计算工作流

## 开始与继续

使用已发现的 `antenna.project_status`、`antenna.model_validate`、`antenna.solve`、`antenna.evaluate`、`antenna.plan_design`、`antenna.design`、`antenna.optimize`、`antenna.get_optimization`、`antenna.get_result`、`antenna.render`、`antenna.cancel`、`antenna.export`、`antenna.get_delivery`；宿主可能将名字变成 `<namespace>__antenna_solve` 等形式，按实际工具目录调用。缺少工具时提示在“扩展”导入本机生成的联合配置、检查工具白名单并应用，不自行安装、启动进程或更改扩展设置。旧服务缺少工作流工具时明确缺口，不改为大模型逐轮猜尺寸并反复 solve。

先确认本次是新方案、修改参数、取回结果还是补画。继续已有项目时，读取用户指定工作区中的输入和已有 run_id；不要通过长期记忆混入其他项目。只取回或补画时不要重新访谈、生成几何或求解。修改频点/架设时还可调用 `antenna.prepare_revision`；要求匹配参数时使用 `antenna.matching`，不要由模型代算电路。

调用 `project_status` 核对服务的稳定 project_id、剩余额度和最近运行。项目由操作员配置的专用结果目录确定，不随聊天工作区自动切换；与工作区记录不一致时先澄清，不混用结果。丢失编号可按 `next_before_sequence` 翻页查找，不能直接生成新编号重算。旧服务没有此工具时明确缺少持久预算保障，不声称跨回合次数已经受控。

## 准备计算输入

先区分任务：**按目标原创设计**，还是**按已有几何复算/复刻**。用户给出参考型号、单元数、波段、增益与 F/B，通常是在提供目标，不代表要求抄原厂尺寸。原创设计可选择受支持拓扑并提出初始尺寸；原厂图纸不是前置条件。只有复算指定实物时，实际尺寸与连接才是必要输入。

根据本次目标收集频点、可用空间、架设高度/地面、材料与馈电方式。只询问影响下一步选择的缺失项；可先用明确标注的初始假设探索，但不把自行选择的频点、理想材料或照片推断写成用户已确认事实。记录目标的来源、单位、自由空间/地面条件、前向方向/切面，以及中心点、全带最小值或平均值的区别。无法对齐口径的参考指标保留为参考，不悄悄改成硬验收门槛。

馈点的 `R+jX` 是计算结果，不固定为 36 Ω 或 50 Ω；系统/馈线的参考阻抗单独记录。原生馈点阻抗包括模型中显式的馈点电阻，直连 SWR 只是相对给定系统阻抗的结果。若计划另做匹配网络，高直连 SWR 不等于辐射方案失败，匹配后 SWR/损耗仍未知；不能通过修改 `reference_impedance_ohm` 假装完成匹配，也不凭空增加 SWR≤2 等用户未给出的目标。

需要跨回合推进时，使用宿主已有文件工具，在用户选定工作区保存：

- `brief.json`：目标、已知约束、待补参数、参数来源、用户已确认的假设。
- `model-rNN.json`：待计算的完整 `EngineeringRequest`，单位为 Hz / m / Ω；修改模型时保存新版本，不覆盖已有计算输入。
- `runs.json`：按实际回执记录输入版本、服务命名空间、project_id、run_id、render_status、结果文件引用及下一步。预算以 project_status 为准；没有可靠回执的调用记录为结果未知，不记为未执行。
- `evaluation-rNN.json`：保存实际 evaluate 回执及目标来源，保留每点指标差额、证据引用、未验证项与下一步；不手工把状态改成已达标。

三个波段比较同一副天线时，几何、导线顺序、物理馈点、材料、地面和网格保持不变，只改变频率。不能每个波段换一套尺寸/馈点后拼成多波段达标。修改任一设计参数后新建 revision，并重新检查所有目标频点；旧结果仅作对照。

用户可以微调目标频点并提供架设方式，不把示例中的三个频率写成固定模板。调用 `prepare_revision(source_run_id, targets, installation?, material?)` 从核验过的旧模型准备新输入：targets 中 run_id 全部为 null；installation 使用 feed_height_m 和绕旧馈点的外禀 XYZ rotation_xyz_deg，地面条件来自 targets.ground。前向方向和仰角使用世界坐标，须与角网格一致。高度/旋转导致接地或触地时拒绝，不自动抬升改变用户条件。屋顶、桅杆、支架、邻近建筑等放在 notes，明确尚未模拟；不能把“提供了架设说明”说成完整环境已经建模。返回模型及假设写入新 revision，再走原 solve 或 plan_design/design，准备步骤不求解、不预留。

这些是工作区记录，不是新的执行器或权限凭据。没有文件写权限时明确说明尚未保存，仍可整理需求；不要虚称已经建立项目或完成计算。

当前电磁契约只支持最多 16 根同半径裸导线、无分支的首尾连接或不相接的寄生单元、一个中心/连接端馈点、馈点纯电阻加载，以及显式均匀/渐变网格；求解的输入阻抗仍为 R+jX。不支持不同线径、分支、接地导体、结构内线圈/RLC；复杂加载天线不能靠删掉这些结构后冒充原方案。端口外的理想 L 型匹配由独立电路计算处理，不冒充天线结构的电磁加载。

`model_validate` 只检查输入契约，不证明物理正确或数值收敛。实际交点、网格和馈点映射还会在求解时检查。遇到不支持项应说明缺少的能力，停在当前步骤，不反复改写输入来绕过限制。

## 计算与检查结果

复算明确输入时先调用 `model_validate`，通过后对同一输入调用一次 `solve`，使用新的 run_id。调用前将这个 run_id 与输入版本记录下来，丢失回执后仍有查询依据。默认只算用户已选定的一个方案；扫频、网格比较或参数搜索须先明确范围和资源边界，不用大模型循环更换尺寸/run_id 代替数值优化。用户已委托评估计算额度时，根据自由参数数、历史全频点耗时和后续验证需求提出有依据的硬上限，不再让用户选择任意次数档位。

新求解前列出本批输入版本、频点、网格、预计次数及停止条件，并核对 `remaining_attempts`。例如一个候选的三个频点需要 3 次；再比较一个候选就是另 3 次，不能把三次默认限额称为完成优化所需次数。扫频/收敛另计，预留后续验证而不虚报固定费用。可用 evaluate 的缺失点和 `planned_new_solves` 作只读预算提案；它既不预留次数，也不是批准。

服务在执行前原子记账；失败或取消也保守保留额度。额度不足时继续做不消耗求解的目标评价、取回、补画和下一批方案整理。用户已委托评估额度时，报告估算依据、时限与验证预留，并由有权限的操作员通过已有服务配置记录总限额变更；否则请求资源授权。优化器不会自行提升项目总限额。不得通过命令工具另起 solver、换服务根目录、删除数据库绕过。没有批准的新计算不执行，但不要因此停掉所有可继续的工作。

批准、拒绝、取消沿用宿主已有交互。审批不是工程参数确认；拒绝计算不妨碍继续整理资料。超时或断线后，先查询原 run_id，不能直接再次调用求解。

结果解释使用回执中的数值及单位；记录 `COMPUTED_NOT_VALIDATED`，不把单频结果称为完整带宽或制作验收。只引用摘要和文件，不把原始电流、全角度数组塞进聊天。文件预览由宿主提供；MCP URI 不是浏览器 URL。

用 `evaluate` 比较已保存结果：逐点提供 frequency_hz、min_forward_gain_dbi、min_front_to_back_db 和 run_id，指定 ground、front_phi_deg、elevation_deg、system_impedance_ohm。比较平均值/标称参考时保持 `comparison_role=reference`；只有已选定为设计目标时才用 design_objective，它仍不是正式工程验收。min_* 在 reference 下只是比较基准，不将高于参考值说成已复现厂家性能。

馈电方案未决定时保留 `matching=unspecified`，仍可比较辐射；选择另做匹配时用 separate_network，不填 max_direct_swr。只有用户确实要求直连及给出门槛时，才用 direct_feed + max_direct_swr。缺结果用 run_id=null，不编造编号；planned_new_solves 是含缺失点的下一批新求解数，省略时只估算缺失点。

需要匹配参数时，对同一设计已完成的评价输入明确选择 `matching=separate_network`，调用 `matching(evaluation, options={mode: per_frequency_l_network, reference_plane: antenna_feed})`。SciPy/scikit-rf 根据每点实际 R+jX 返回连接顺序、串/并联 L/C、H/F 数值和理想匹配后阻抗/SWR，不新增电磁求解。每个频点的网络是独立候选，需要调谐或切换，不能拼成一套固定三波段网络；无解回执表示数值搜索未找到，不断言物理上无解。元件损耗、Q、公差、功率、馈线及巴伦仍须验证。design/export 加同样的 matching_options，报告与 ZIP 才会保存计算参数；不要只在对话中口述数值。

据评价推进，不以“有图片”收尾：

- 辐射目标未满足：指出具体频点和增益/F/B 差额，按下一节把有边界的参数搜索交给 `optimize`。差额本身不证明某个尺寸就是根因，不靠大模型逐轮试数。
- 辐射达到当前采样点目标但匹配未解决：保留方案，按用户选择计算理想逐频匹配；固定多波段网络等超出当前能力的需求记录缺口，不伪造结果。
- 目标点满足：转入约定的带边/带内采样、网格收敛、材料安装和制作验证计划，不宣布全带或工程验收通过。
- 结果不一致/缺失：修正比较口径或取回证据，不用新计算掩盖证据问题。

## 数值优化，不让大模型承担迭代

完整设计交付优先使用 `antenna.plan_design` → `antenna.design`。输入沿用 OptimizeInput，另加 `title/purpose/assumptions/sources`；`run_id` 同时是交付编号。可提供两个严格递增的 `verification_segments`，省略时按原网格和 pulse 上限计算；不会改变网格模式、物理几何或馈点。

计划先评估初始化加完整一代的最低次数、达到 minimum_generations 的推荐次数，并留两轮全频网格复核额度；短预算明确 warmup_fits_budget=false，不冒充充分探索。项目硬上限和显式搜索上限不会自动增加。plan 不预留、不求解；不足时报告具体缺口和依据，不让用户随意挑次数档位。执行时重新检查账本。工作流总时限中 65% 用于搜索，其余留给复核、绘图和打包；保留时间不保证极慢模型一定完成。

`design` 在 MCP 内完成搜索、两层网格求解、敏感性比较、最佳候选补画和 ZIP 导出，内部不调用大模型。它会返回与 export 相同的交付回执，宿主按原结果适配器导入工作区。`mesh_tolerances` 可声明 gain_db/front_back_db/impedance_ohm 三个容差；没有明确容差时只记录敏感性，不能称为网格收敛。即使容差以内，也不是全带、匹配或制作验收。中断后先查 get_optimization/project_status；已有交付用 get_delivery，未完成复核/补画可以按原编号查证后显式继续，不能重放 design 偷偷增加求解。

用户同意参数搜索后，一次调用 `antenna.optimize`，传完整初始 `model`、`dimensions`、`targets`；通常省略 `max_solver_runs` 和 `population_size`，使用自动分批策略。SciPy 生成候选，pymininec 求解，MCP 内部评价、记录最佳候选并停止；内部 LLM 调用为零。不需要模型逐个候选确认、调尺寸或读取方向图数组。

- `dimensions` 每项包含 1-based `wire`、`parameter`、`lower/upper`，单位米。参数只接受 `length_m`（绕中点改长度，方向不变）和 `center_x_m / center_y_m / center_z_m`（绝对中心坐标）。初始值必须在范围内；未列字段不变。首版仅孤立单元、中心馈电，不能优化连接结构、材料、馈点电阻、匹配网络或拓扑。
- `targets` 沿用 evaluate 的口径，必须是显式选择的 `design_objective`，最多 6 个频点；原厂标称参考不自动升级成目标。不要填写 planned_new_solves。若已有完全相同初始模型的全频点结果，填所有 run_id 复用基线；否则全部为 null，由算法计入预算计算基线。
- 评分是各频点增益/F/B 缺口的归一化平方和；默认尺度分别为 1 dB，可显式设置 gain_scale_db / front_back_scale_db。只有已选定直连并给出 max_direct_swr 时纳入 SWR 缺口，swr_scale 默认 1。超出的增益不抵消别的频点缺口；不要用改变尺度、参考阻抗或目标冒充优化成功。
- 默认在项目剩余次数中留出最多两次完整频点扫算的验证余量，其余作为搜索硬上限（不超过 4096）。这只是余量，不是锁定的验证预留，也不保证足够完成带宽/收敛验收。显式 `max_solver_runs` 可进一步指定本次上限，不能超过项目剩余次数；使用它时由调用者另留验证余量。
- 默认总种群/资源批次为 `max(8, 2×自由参数数)`，不代表数值收敛保证。资源批次仅限制消耗，不用于判断停滞。SciPy 每完成一整代后才比较改善；初始化不计代数。默认至少完成 minimum_generations=5 代，并连续 stagnation_generations=5 代没有超过 0.1% 且 1e-8 的改善才因停滞停止；次数/时限依然可以提前终止。回执 generations_completed 是实际完整代数，不用候选数量推算。
- 完整候选实测耗时（含保存/评价）乘 1.25 后的高水位用于限制后续批次和候选准入；共享 worker 时限保留至少 5 秒或 10% 收尾时间。不够完成下一个全频候选就停止。没有耗时记录时最多先放行一个探测候选，极慢求解仍由父进程硬超时终止。`budget` 回执区分硬上限、已领取候选、实际用量、停滞和耗时；没有旧基线时基线也计费。
- 使用新的优化 run_id（最长 40 字符），调用前保存 `optimization-input-rNN.json`。回执保存为 `optimization-result-rNN.json`；best.run_ids 属于同一几何，可直接用于 evaluate/export，best.parameters 的顺序与 dimensions 一致。首版搜索只存数值，不逐个候选绘图。为最佳候选各频点调用 render，再以其输出编号交付带图设计包；不为出图重新 solve。
- `stop_reason=sampled_targets_met` 只表示当前采样点目标满足；stagnation / time_limit / budget_limit / optimizer_finished / interrupted / failed 都不等于达标。best=null 表示没有完整可评价候选，不能给出虚构尺寸或指标；best 仍是基线则明确没有找到更好的方案。
- 取消使用优化 run_id；超时、断线或重启后先 get_optimization。它只取回，不恢复种群、不重新求解。未完成候选的频点可由 project_status 找回，不能拼进最佳方案。继续搜索需要明确新的预算和新编号，可将已保存最佳模型及全频点结果作为新基线；不能偷偷重跑旧输入。

## 交付设计文件

匹配参数选定或取整后，使用 `antenna.evaluate_matching` 将同一组元件应用到同一设计的已保存频点：传 evaluation、network（topology、按 series/shunt 排列的 components、antenna_feed 参考平面），仅在用户明确给出指标时传 max_swr。它不重新调谐，不插值未知阻抗，也不新增电磁求解。不要把每个频点分别匹配成功说成固定网络覆盖整段；缺少邻频证据时先安排有界扫算。多个独立网络需要另行选择切换/调谐方式，元件损耗、Q、功率、巴伦仍未验收。将实际检查输入作为 export.matching_checks 保存，不能只在对话中口头总结，或把良好 SWR 当作辐射指标达标。

用户要求设计成果时，评价之后继续调用 `antenna.export`，不要只交付工具日志、评价 JSON 或一张示意图。提供新的 `delivery_id`、标题、用途、明确的假设与来源、完整 `evaluation` 输入；每个目标点必须有同一设计的已完成 run_id。它读取已有数值并生成候选包，不新增求解，也不消耗项目次数。未达到参考指标或未做制作验证时仍可交付当前候选，但说明差额和限制，不能称为成品。

包包含 `design-report.md`、`geometry.svg`、`dimensions.csv`、`model.json`、`materials.json`、`evaluation.json`、`validation.json`、`construction.md`、`reproduce.json` 和 `design.zip`。ZIP 按频点保留原始数值、PNG/SVG、可用 MAA 和环境证据；尺寸表是模型尺寸，不是已验证的采购/下料单。施工说明须保留匹配、结构、安装、带宽和收敛缺口，不能用未模拟的匹配网络补全“通过”。

单独 export 搜索结果时提供 `optimization_run_id`；有网格复核则提供 `verification.refinements`（两个完整 EvaluationInput）及可选 tolerances。服务核对目标、最佳几何与原始指标，ZIP 附搜索输入/停止回执、最佳候选原始结果及网格敏感性和各层数值证据。不要仅把复核结论写在 assumptions 中代替证据。

以宿主返回的 `files` 相对路径交付：报告用于阅读，SVG 查看几何，ZIP 下载完整证据。不要给用户 `antenna://...` 当下载链接，也不要假称 MCP 服务目录已出现在聊天工作区。`import_complete=false` 时用 `get_delivery(delivery_id)` 补齐；已修改的本地文件保留。同一编号同一输入重复 export 会复用；内容修改需新编号。把 delivery_id 和本地目录补进 `runs.json`，跨对话继续只需取回，不重新求解。

## 恢复分支

| 实际情况 | 下一步 |
| --- | --- |
| 回执丢失或本地文件导入中断 | `get_result(run_id)`；它不求解。宿主核验已有文件并补齐同一个结果目录，不覆盖用户修改 |
| 优化超时、断线或重启 | `get_optimization(run_id)` 取回完整最佳候选；不恢复执行，不自动再提交 optimize |
| 交付回执丢失或包的本地导入中断 | `get_delivery(delivery_id)`；不重复求解或重新导出 |
| `DELIVERY_INPUT_CONFLICT` / `DELIVERY_INCOMPLETE` / `DELIVERY_UNAVAILABLE` | 保留原包并核实输入或完整性；不覆盖、不删目录重试，不改变求解额度 |
| 数值已保存，`render_status=incomplete` | `render(run_id, output_run_id)`；使用新的输出编号，只读取数值补画 |
| 补画的回执丢失 | 查询 **output_run_id**，不再次补画同一编号 |
| `RUN_ACTIVE` / `BUSY` | 计算仍在进行；等待后查询，不重新求解，也不为抢占资源自动取消其他操作 |
| 用户明确要求停止计算 | 调用 `cancel(run_id)`；补画使用 output_run_id。只有 `worker_stopped=true` 才确认进程停止；未确认时说明未知，不继续求解 |
| `RUN_NOT_FOUND` / `RUN_INCOMPLETE` / `RESULT_CORRUPT` | 分别说明编号不存在、没有完整结果或证据损坏；保留编号和失败信息，由用户决定是否另开计算 |
| `RUN_INPUT_CONFLICT` / `RUN_ID_IN_USE` | 查询已占用的编号，不覆盖；只有新方案已确认时才使用新编号 |
| `RUN_RESERVED` | 该编号已记账，可能在写目录前中断；查 project_status 并保留记录，不自动重放或退额度 |
| `SOLVE_BUDGET_EXHAUSTED` | 停止新增求解；查询、取回和补画仍可使用，请用户决定是否增加总限额 |
| `EVALUATION_DESIGN_MISMATCH` / `EVALUATION_BASIS_MISMATCH` | 分开不同设计版本，核对地面/前向切面；不要混算或改目标迎合结果 |
| `EVALUATION_EVIDENCE_MISMATCH` | 保存证据存在内部矛盾；保留原件并检查来源，不生成通过结论 |
| `ANTENNA_IMPORT_CONFLICT` | 本地文件已被修改，保留它；用户移走冲突文件后再 get_result，不能用重算解决导入冲突 |
| 所需参数或结构不支持 | 保留需求和缺口，等待补充参数或能力；不输出虚构数值 |

`solver_runs_this_call=0` 表示本次读取/补画没有求解；原摘要中的 `solver_runs=1` 记录原始数值来源，不重复计数。`reserved_attempts` 是服务持久保存的准入次数，可能多于实际求解次数；工作区记录和 Skill 不自行维护第二份权威预算，也不能把它称为模型 token 预算。

结束时按本次请求范围给出可点开的实际文件；仅取图就交付已有图，不重新要求设计包。设计推进时交付当前尺寸/馈点模型、计算摘要、方向图、评价和下一批计划。制作需求还应列明材料、下料/连接/匹配、安装调试与复算条件；尚未形成的部分明确列缺口，不能拿工具回执、过程日志或示意图代替制作包。说明本次实际操作、输入版本/run_id 和下一步，不将有界参数搜索称为任意拓扑设计或工程验收。
