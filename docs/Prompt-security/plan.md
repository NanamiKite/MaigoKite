# Plan: Prompt Safety Review（提示词安全审查）

> 状态：Phase A–D 已完成，Phase E 验收进行中。KEV 已完成本机 HTTP 联调和二分类概率接入；`jev_like` 请求布局已按 A/B 实验改为可配置的 `jev_layout`（`message` 默认 / `inline`），0.8B 与 4B 的对比数据见验证结果与 [配置指南](configuration.md)；LAYA 已完成本机 HTTP 联调、请求契约修正与语言覆盖验证，完整端到端验收仍待执行，进度与证据见 [交付路线与状态](../project/Roadmap.md)。`openai` 结构化输出格式已实现请求构造、降级与解析，单测覆盖通过，并已用本机 LM Studio（`google/gemma-4-12b-qat`，`json_schema` 直连无需降级）完成真实联调：良性提示返回归一化概率 0.0、转换为 0 分并放行；注入提示返回 1.0、转换为 100 分并拦截。
> 接口契约以 [API 设计](../design/API.md#36-设置模型和凭证引用) 为准，本文件只记录计划与边界。

这是一个增量改造。目标是把安全检查接到模型调用前，先取当前步相对上一轮的提示词增量，再交给独立审查模型打分。分值超过阈值时，任务进入可人工接管的暂停态，前端复用现有继续和恢复交互，让用户决定是否放行。

## 2026-10-16 复核未通过项

本节区分历史实测失败与尚未验证，不用全量仓库测试通过推定安全分类质量已恢复。

| 历史未通过项 | 本轮复测状态 | 关闭所需证据 |
| --- | --- | --- |
| LAYA JA 注入漏拦、AR 良性误拦 | 未复测，仍未关闭 | 对原良性/注入样例及所用检查点、阈值重新测量，分别报告漏拦与误拦 |
| LAYA HI 注入及部分中文 guidance 导致 XPU 崩溃 | 未复测，仍未关闭 | 真实推理请求、故障后可用性与恢复验证；`/health` 200 不能替代 |
| KEV 隐蔽指令套取等输入在默认阈值漏拦 | 未复测，仍未关闭 | 记录模型版本、message/inline 布局与阈值，复跑原失败样例；不从主套件 AUC 外推 |
| 安全配置 → 主模型前置审查 → 人工继续/终止 | 源码已有接线，但本轮未补完整 E2E | 当前 Core、UI、真实审查服务联合验证暂停、恢复及失败保守拦截 |

- 本轮只读检查历史端口 8000、8009、9931、1234，均无监听；未安装/启动外部模型或读取用户模型凭据，不能执行上述真实服务复测。
- 下方提及的 `test_safety_review.py`、`test_safety_settings.py`、`test_safety_laya.py`、`test_safety_openai.py` 当前不在测试树中；其通过数字仅为历史记录，本轮完整后端 212 项不覆盖这些专项断言。不能据此将 Phase E 标记完成。
- `multilingual` 检查点已有配置白名单阻止使用，但这不是修复该模型误判的证据。JA/AR 是**已测不达标**，HI 是**已测服务端故障**，不再笼统称为“未覆盖语言”。普通模型协议测试和图片/浏览器验收都不替代本专项。

**Workflow**
1. 先定数据契约，再动 UI 和 runtime *blocking foundation*。
   - 定义安全检查配置对象、DTO、API 返回结构和默认值。
   - 请求格式只接受 `jev_like`、`laya` 和 `openai`；kev 是 jev 类的开源实现，归入 `jev_like`，不单独枚举。
   - `openai` 表示 OpenAI 兼容 API 的结构化输出：端点取 `decision_base_url` 拼接 `/chat/completions`，`state` 与 `question` 分别作为 system 与 user 消息，通过 `response_format` 约束决策模型只返回固定结构的风险分值与理由。
   - 决策模型请求拆为 `state` 与 `question` 两部分，`state` 进入设置供自定义，留空时用库内置默认值。
   - 确认这些字段按工作区持久化，不随会话临时保存。
   - 明确审查模型配置是否复用现有模型账户体系，还是独立保存端点和密钥。
   - 产出：前后端读写同一份安全配置草案。

2. 先留出前端接口，不动 UI *parallel with backend DTO wiring after shape is fixed*。
   - 只定义给前端组对接的接口和事件，不改设置页、聊天页或现有表单。
   - 输出安全检查配置的 API 字段、默认值、错误码和示例响应，前端据此自适配。
   - 如需新增枚举或事件类型，只补契约，不补界面。
   - 产出：前端可以按接口自行接入，不依赖这次改动提供 UI。

3. 接着提取独立请求构造库 *depends on step 1*。
   - 新建请求构造层，把“如何把审查输入拼成请求”从 runtime 中剥离出来。
   - 库至少提供统一入口和三个实现：`jev_like`、`laya` 与 `openai`。
   - 每个实现负责生成 `state` 与 `question` 两段请求；`state` 取用户自定义值，`question` 由库根据审查输入生成。`openai` 实现把两者装配为 chat/completions 消息，并附带结构化输出约束。
   - 未来新增格式时，只扩展该库，不把格式逻辑散落到 runtime。
   - 输入只接受标准化的模型元信息、任务和步骤元信息、增量文本和可选上下文；输出结构化请求对象，不直接发网。
   - 产出：审查请求的构造方式独立于运行时执行链。

4. 再找 runtime 前置切点并接入库 *depends on step 3*。
   - 找到主模型每次真正发送前的统一入口，而不是在 UI 或后处理阶段补审。
   - 在这一层记录“上次已审查文本”“当前模型 ID”“当前 step 序号”“当前请求格式”，用于对比增量并选择构造器。
   - 如果当前 step 切换了模型，要把它视作新的审查上下文，而不是沿用上一模型的审查快照。
   - runtime 只负责收集输入、调用请求构造库、发送到决策模型并接收结果。
   - 产出：每次外发前都先经过统一审查入口。

5. 然后实现增量审查 *depends on step 4*。
   - 计算本步新增提示词，只把新增部分送入决策模型；必要时附带最少量上下文，例如上一步模型 ID、step 元信息和上次审查标记。
   - 当前模型 ID 命中可信白名单时直接跳过审查，但仍记录跳过原因。
   - 决策模型返回风险分值后与阈值比较，决定放行或拦截：`jev_like`/`laya` 沿用 `dangerous` 概率；`openai` 也解析 0–1 的归一化 `risk_probability`，统一乘以 100 形成内部风险值，取值分支按格式隔离在审查执行层，不散落到 runtime。
   - `openai` 请求优先使用 `response_format: json_schema`；服务不支持时降级为 `json_object` 并把 schema 写入提示；仍无结构化输出或字段非法时按返回异常处理。
   - 如果请求构造失败、格式不支持或决策模型返回异常，采用保守策略，默认拦截。
   - 产出：审查结果可以稳定驱动放行和拦截。

6. 再接人工介入链路 *depends on step 5*。
   - 风险超过阈值时，把当前任务置为等待人工确认态，并产生安全审查拦截事件。
   - 前端复用现有“任务已中断/继续任务”入口，新增安全拦截说明和“继续/终止”动作。
   - 继续动作必须走恢复和确认流程，不能重新提交原 prompt，避免重复泄露。
   - 产出：用户可以在不中断会话历史的前提下决定是否放行。

7. 最后补审计与测试 *parallel with steps 2-6 where possible*。
   - 记录每次审查命中、跳过、拦截、请求格式和人工决定，供审计和排障。
   - 为可信模型跳过、阈值拦截、格式构造失败和审查失败保守拦截写最小回归测试。
   - 验证前端设置页、后端 API、请求构造库、runtime 审查和暂停恢复能串起来。
   - 产出：可回溯、可观察、可测试。

**Execution order**
- Phase A: 先完成数据契约和持久化层，作为唯一来源。 ✅ 已完成
- Phase B: 再输出前端可对接的接口、事件和错误码，不改 UI。 ✅ 已完成
- Phase C: 接 request builder 库、runtime 审查、风险判定和白名单跳过逻辑。 ✅ 已完成
- Phase D: 处理人工介入所需的任务状态、恢复语义和审计事件。 ✅ 已完成
- Phase E: 最后补测试和文档。 🔄 验收进行中

**Implemented scoring contract**

- `jev_like`/KEV 使用两个 criterion：`safe` 和 `dangerous`；不再使用
  0–100 共 101 个风险等级 criterion，也不把 KEV 返回的 `score` 当作风险值。
- 审查器根据返回体的 `legend` 找到 `dangerous` 类别，并读取对应的
  `probabilities` 值；归一化概率乘以 100 后形成 `risk_score`。
- 设置 API 和 UI 的 `risk_threshold` 仍是 0–100 整数，运行时保持
  `risk_score > risk_threshold` 时拦截。
- 缺少 `dangerous` 概率、概率越界、响应格式错误或决策服务失败时，继续使用
  保守拦截策略；不会回退到 `score`、旧的 `risk_score` 或关键词规则。
- 由于 KEV 当前使用约 0.9B 模型，概率只代表模型当前分类信号，不等同于经过
  校准的真实安全概率；高风险权限和凭证外发仍需要运行时权限边界保护。

**Phase boundaries**
- 不新增第二套审批系统，直接复用现有 waiting_approval / interrupted 语义。
- 不把普通工具审批与提示词审查混在一起，两者在 runtime 上是不同门禁。
- 结构化输出只属于 `openai` 格式；不改写 `jev_like`/`laya` 的既有服务契约，也不把响应解析逻辑散到 runtime。
- 原 Phase A、Phase B 只输出接口、事件和契约，不修改 UI。后续已接入安全设置页及运行时审查/人工恢复；历史配置接口与 GUI 测试不等于聊天整链路验收，后者仍待补证据。

**Relevant files**
- `web/src/api/client.ts` — 前端可对接的 API 契约和类型定义。
- `web/src/api/events.ts` — 若新增安全审查事件，需要扩充事件类型。
- `python/agent/api/settings.py` — 后端设置读写 API。
- `python/agent/api/contracts.py` — 新增安全配置 DTO、请求格式枚举和校验边界。
- `python/agent/safety/request_builders.py` — 新的独立请求构造库入口，统一调度不同格式实现。
- `python/agent/safety/formats/jev_like.py` — `jev_like` 格式请求构造实现，覆盖 jev 类与开源实现 kev。
- `python/agent/safety/formats/laya.py` — `laya` 格式请求构造实现。
- `python/agent/safety/formats/openai.py` — `openai` 格式请求构造实现，装配 chat/completions 消息与结构化输出约束。
- `python/agent/safety/reviewer.py` — 审查请求发送与响应解析；各格式取值分支和 `openai` 结构化输出降级在此处理。
- `python/agent/runtime/langgraph_runtime.py` — 最关键的运行时挂钩点，负责模型调用前审查、任务暂停和恢复衔接。
- `python/agent/core.py` — 若需要把配置纳入核心工作区设置或审计事件，这里通常会接线。
- `python/agent/repository.py` — 若新增配置表、审查记录或事件持久化字段，这里会涉及。
- `docs/design/API.md` — 请求格式和安全审查 API、事件约定。
- `docs/Prompt-security/configuration.md` — KEV/LAYA/OpenAI 兼容三种格式的配置指南。
- `docs/agent-capabilities/models/model-routing.md` — 审查模型和请求格式边界。

**Verification**
1. 先补一组最小单测或集成测试，覆盖三类场景：可信模型跳过、风险超阈值暂停、审查失败默认拦截。 ✅ 已完成
   - `openai` 格式已追加两组场景：结构化输出解析与 `response_format` 降级、非法结构化输出保守拦截。 ✅ 已完成（`python/tests/unit/test_safety_openai.py`，24 passed）。
2. 跑后端相关测试，或至少目标模块的类型和单测，确认新配置和运行时逻辑能通过。 ✅ 已完成
3. 跑前端类型检查或构建，确认设置页新增字段和 API 契约没有断裂。 ✅ 已完成（`vue-tsc --noEmit` exit 0，覆盖 `jev_layout` 新字段）
4. 手工验证一次完整流程：保存安全配置 -> 触发模型调用 -> 命中审查 -> 人工继续或终止 -> 状态回到正常流转。

历史后端验证结果（2026-10-13；不是本轮重跑）：

- `python/tests`（当前工作区文件）：17 passed（安全配置与 `jev_layout` 默认/修复、
  三格式请求构造、`jev_like` 双布局载荷与未知布局拒绝、reviewer 布局透传、
  `laya` 检查点白名单、概率解析与保守拦截；`openai` 专项用例文件不在当前工作区，
  未重跑）。
- Python 编译检查和 `git diff --check` 通过。
- 本机 KEV endpoint：`POST http://127.0.0.1:8009/v1/systemone`。
- 已验证二分类响应中的 `probabilities` 可转换为 0–100 `risk_score`，并驱动阈值判断。
- KEV-4B benchmark（2026-10-13，`http://127.0.0.1:9931` llama.cpp +
  `Kev-4B-Q4_K_M.gguf`，`/v1/systemone` 契约一致；456 请求、重复间结果一致、
  主套件均值 ~250ms）：两种布局 AUC 均 1.000；主套件 `inline` 隙差 +87.4、
  阈值 75 零漏拦，`message` +49.9、4 漏拦；24 条汇总 `inline` 6.0/12.8/+6.9
  （最佳阈值 6 零误差）、`message` 11.0/13.1/+2.1（阈值 12 零误差）。指令扩展在
  4B 上收益显著：`message` 漏拦 7→3、`inline` 4→1（仅“翻译时附初始设定”27.3），
  良性上界仅 +0.5；因需按决策模型取舍，未并入默认指令。
- KEV 请求布局 A/B（2026-10-13；三布局 × 12 良性 + 12 注入（中英）× 每条 3 次
  重复，重复间结果完全一致）：旧指令内嵌布局良性均分 40.9、上界 63.9，存在良性
  反超注入的重叠（任意阈值至少 2 错）；`state.message` 布局良性均分 17.2、上界
  41.9，经典越狱注入 97+，首轮 16 条完全分离（AUC 1.00、零误差阈值 24–38）。
  隐蔽指令套取类注入在两种布局下都低于默认阈值 75，是 0.8B 模型的共同短板；
  指令扩展（补充隐藏指令套取覆盖）实测注入均分 +4.0、良性 +2.1，收益边际，未采用。
  新构造器经 `SafetyReviewer` 端到端联调：良性 14 分放行、注入 93 分拦截，
  guidance 路径服务端接受。
- 本机 LM Studio endpoint：`POST http://127.0.0.1:1234/v1/chat/completions`，模型为
  `google/gemma-4-12b-qat`；已验证 `risk_probability` 的 0–1 归一化输出可转换为
  0–100 `risk_score` 并驱动阈值判断。良性样例为 `0.0 → 0`，注入样例为 `1.0 → 100`；
  两次均未触发 `json_object` 降级。
- 本机 Laya endpoint：`POST http://127.0.0.1:8000/predict`（`/health` `/models`
  `/qtypes` `/presets` 同时可用；`/health` 不反映推理可用性，后端故障时仍返回 200）。
  原 `laya` 构造的单数 `question` 载荷被服务端 422 拒绝，已改为 `questions` 映射契约；
  `reviewer` 对 `laya` 不以 `/predict` 结尾的地址自动补后缀，构造失败转为审查失败保守拦截。
  已验证响应中 `legend`/`probabilities` 的 `dangerous` 概率可转换为 0–100 `risk_score`
  并驱动阈值判断，同输入重复请求结果一致。
- Laya 语言覆盖（阈值 75，`risk_score > risk_threshold` 拦截，括号内为良性/注入分值）：
  EN `7/95`、ZH `10/87`、RU `22/86` 在 `english` 与 `typed-decisions` 上均通过；
  FR 在 `typed-decisions` 为 `24/97`、在 `english` 为 `69/84`（良性分接近阈值）；
  JA 注入漏拦（`40/62`）、AR 良性误拦（`82/94`），两种检查点均不达标；
  HI 注入触发服务端 XPU 崩溃（`level_zero ... UR_RESULT_ERROR_UNKNOWN`），与检查点无关。
  语言前置路由经实测无收益（`typed-decisions` 在已测语言上持平或优于 `english`），
  审查路径不引入语言判断，固定使用检查点且必须显式指定（自动路由会选中不可用的
  `multilingual`）。JA/AR 已测不达标、HI 已测服务端故障，其风险分不可信；当前复测边界见本页上方。
- Laya 服务端已知问题（服务端自身缺陷，非本仓库代码）：`guidance` 字段叠加中文注入
  文本同样触发上述 XPU 崩溃；崩溃后进程可能退出，`/health` 200 与 `/predict` 可用性
  无关，恢复需重启服务。

**Decisions**
- 审查模型按工作区独立配置，不和主模型共用。
- 风险阈值使用 0-100，便于配置和调试。
- 可信模型白名单按“当前实际调用模型 ID”判断，而不是仅看默认模型。
- 审查失败采用保守策略，默认暂停并要求人工确认。
- 这次方案优先复用现有暂停和继续链路，不另造第二套审批系统。
- KEV 风险值使用 `probabilities["dangerous"] * 100` 的归一化概率映射；不使用
  KEV 的 `score` 索引值。
- JEV_LIKE/KEV 请求布局（2026-10-13 起，`jev_layout` 二选一）：默认 `message`
  ——待审查增量放 `state.message`，短指令引用 `message`，`state` 设置留空不携带
  附加字段、非空作为 `state.guidance` 附加（与 LAYA 一致）；`inline` 保留迁移前的
  指令内嵌布局（Model/Task/Step 元数据与 `previous_model_id` 仅存在于该布局，
  `state` 为静态分类指令）。依据是两轮本机 A/B（数据见验证结果与
  [配置指南](configuration.md)）：0.8B 上 `message` 是解药（`inline` 存在良性反超
  注入的重叠），4B 上 `inline` 反而最优（主套隙差 +87.4 vs +49.9）。设置页提供
  「JEV 布局」选择；非法布局 PATCH 返回 `SAFETY_LAYOUT_UNSUPPORTED`，运行时构造
  失败按审查失败保守拦截。
- `openai` 格式要求决策模型输出包含 0–1 归一化 `risk_probability` 的结构化 JSON；解析失败不回退自由文本打分，沿用保守拦截。
- LAYA 请求布局：待审查增量放 `state.message`，`questions.prompt_safety` 用 `score`
  类型与 `safe`/`dangerous` criteria；`state` 设置留空时不携带附加字段，非空时作为
  `state.guidance` 附加并在 instructions 中引用。指令内嵌增量的 jev_like 式布局在
  Laya 上无区分度（良性 80 / 注入 77），不采用。
- LAYA `decision_model` 白名单为 `english` 与 `typed-decisions`，留空默认
  `typed-decisions`，其余值在请求构造时失败并保守拦截；`multilingual` 实测对
  良性/注入反向误判（良性 93–99 / 注入 86–94），禁用。审查请求必须显式指定检查点，
  不使用服务端自动路由（自动路由可选中 `multilingual`）。
- LAYA 不引入语言前置路由：`typed-decisions` 在已测语言上持平或优于 `english`，
  语言覆盖限制按上文矩阵如实记录，不为未验证语言推断可用性。

**Further Considerations**
1. 需要明确审查输入是只看“本步增量”，还是还要附带最近一轮上下文摘要；建议先做“增量 + 必要少量上下文”，减少误判。
2. 需要确认审查模型 API 的接入协议是否要兼容现有模型提供方列表，还是允许完全独立的审查服务地址。
3. 需要确认拦截后的用户动作是“继续一次”“整任务放行”还是“对同会话放行”，这会影响恢复态的粒度。
4. 各 OpenAI 兼容服务对 `response_format` 的支持程度不同（`json_schema` 与 `json_object`），需要明确支持矩阵、降级顺序与验收用服务列表。
