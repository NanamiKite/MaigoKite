# 模型接入与选择

> 权威范围：模型配置所有权、显式模型选择、模型提供方兼容和未来路由边界。
> 当前交付状态见 [交付路线与状态](../../project/Roadmap.md)，API 字段见 [API 设计](../../design/API.md)。

## 1. 当前能力

一个工作区最多保存 32 个聊天配置项，指定一个默认项；会话可显式覆盖。支持 OpenAI、Anthropic、Ollama、OpenAI-compatible，LM Studio/vLLM 等是协议和地址预设，不是内置模型表。

配置项保存显示名称、协议、服务端模型 ID 和基础地址。普通设置不要求用户维护采样温度、上下文容量、价格或质量等级；容量可按需显式覆盖。请求预算和超时是运行策略，不是模型目录事实。

会话 `thinking` 使用模型支持的思考选项，与输出预算分开。旧 `response_mode` 的 `fast / balanced / thorough` 仅保留兼容，不再调整 `max_tokens`，也不映射为推理强度。能力查询与不支持时的行为见 [API](../../design/API.md#36-设置模型和凭证引用)。

- 用户主动用当前草稿“从服务获取”目录，成功后选入草稿；不自动保存，也不是后台能力探测。
- 目录不可用可手输真实模型 ID，不能把服务显示名称当 ID。
- 默认用于新会话，现有会话选择持久化；单次任务不会因普通延迟静默换模型。
- 子 Agent 默认跟随本回合父模型；会话可选择允许显式使用的其他配置项。主 Agent 按需用 `list_subagent_models` 发现 ID，再调用 `delegate(model_id=...)`，不新建 Registry、不接受模型自行提交端点/密钥。缺失/未授权/连接失败不静默替换，名称歧义须询问。指定允许模型的实际子图工具回合及主/子回复分离已通过真实 Core＋本机协议服务验收；自然语言选模和真实 Provider 不在其通过范围，见 [当前验收状态](../../project/Roadmap.md#当前验收状态) 与 [运行时设计](../../design/AgentRuntime.md#7-技能子任务与工作流边界)。
- API 密钥按配置项 ID 保存在凭证存储，只回显存在状态，受控同步到核心服务内存。
- 模型提供方/基础地址变化后不得把旧凭证发往新端点；本地模型提供方不借用云端环境密钥。
- 安全审查的决策模型独立于聊天模型目录，按工作区保存在 `settings/safety`：端点与模型 ID 进配置，密钥走 `safety.decision_model` 凭证引用，只回显存在状态。请求格式为 `jev_like`（jev 类及开源实现 kev）、`laya` 或 `openai`（OpenAI 兼容 chat/completions 结构化输出），请求由 `state`（可自定义；`jev_like`/`laya` 留空省略附加字段，`openai` 留空用内置默认指令）和 `question`（由库生成）组成；`jev_like` 按 `jev_layout` 选择布局（默认 `message`，增量放 `state.message`；`inline` 内嵌问题指令，0.8B 与 4B 实测对比见[配置指南](../../Prompt-security/configuration.md#2-jev_like含开源实现-kev)）、`laya` 把待审查增量放 `state.message`；`openai` 把两段映射为 system/user 消息，并用 `response_format` 约束结构化返回。`laya` 发往 `{decision_base_url}/predict`（缺失后缀自动补），`decision_model` 只接受 `english`/`typed-decisions`（留空默认 `typed-decisions`），`multilingual` 因实测反向误判禁用，且必须显式指定检查点、不用服务端自动路由。主模型调用前审查新增提示词，可信模型跳过，失败保守拦截。字段、错误码与 `safety.reviewed` 事件契约见 [API 设计](../../design/API.md#36-设置模型和凭证引用)。

## 2. 地址、目录与兼容

OpenAI/Anthropic/compatible 目录使用对应服务的 models 接口，Ollama 使用 tags 接口。基础地址只去除首尾空白和尾斜杠，不按端口识别产品、不自动补 `/v1`，也不剥离 completion/messages 后缀；请填写服务实际提供的 API 根地址。产品预设只提供默认值，不覆盖用户修改。旧配置若缺少 `/v1`，须按实际服务修正，不能仅凭端口自动迁移。

基础地址表示接口根，不代表浏览器页面地址；本地 OpenAI-compatible 常用 `http://127.0.0.1:1234/v1`，模型必须是目录返回 ID。HTTP 200 必须同时具有有效响应结构才算成功。

正式请求复用 LangChain 模型提供方集成和官方 SDK。OpenAI/compatible 与 Ollama 生成请求只有 loopback 地址自动直连；私网和公网遵循 HTTPX 环境代理与 `NO_PROXY`，保留环境 CA 配置。网络位置不代表产品身份。客户端所有权、取消与凭证轮换见 [智能体运行时设计](../../design/AgentRuntime.md)。支持协议不等于每个真实服务都已通过工具循环验收。

LM Studio 专属加载窗口和思考能力查询仅对 `models.routes` 中明确配置 `metadata_api: lmstudio` 的协议/地址组合启用；通用 compatible 不请求其专属接口。本地地址不匹配公开目录中的 localhost 样例。配置方法见 [配置说明](../../user-guide/Configuration.md)。

返回字段适配及流关闭补丁依赖 integration 的私有接口，因此 `langchain-openai / langchain-anthropic / langchain-ollama` 分别固定为 1.4.0 / 1.4.8 / 1.1.0，并同步锁文件。升级须审阅这些接口与客户端所有权；不再宣称从 0.2.0 起均兼容。

### 2.1 当前接入路由的窗口容量

容量绑定接入协议和基础地址，再按服务实际接受的模型 ID 查找。OpenAI/Anthropic 是协议选择，不证明自定义地址就是该厂商的直连接口；界面地址留空时，以已有 SDK 客户端的实际地址识别路由，包括环境变量覆盖。目录继续复用 models.dev，不维护自有模型容量表；只读取当前端点对应的目录，不跨厂商匹配同名 ID、前缀或 canonical origin。厂商 SDK 的模型资料只用于对应的官方直连，不拿来补中转站容量。

优先级为：实际加载窗口 → 当前配置项的 `context_window` → 当前服务返回的模型容量 → 当前路由目录/适用的 SDK 资料 → 路由默认值。服务显式超限回执仍可校正当前运行的容量。本地加载窗口可以覆盖声明值；声明不会更改服务端加载设置。

远端路由未提供容量、也无适用资料时，采用 [DSH 的可配置回退](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/llm/llm-pi-ai/src/config.ts#L59) **262,144 tokens**。这是接入假定值，不是 Claude 官方窗口、实测中转站容量或固定的 258K；事件保留 `capacity_source=route_default / capacity_status=assumed`。本地地址与 Ollama 不自动套用这个云端回退，未获取容量时仍未知；可显式配置本地路由的默认值。

`models.routes[].default_context_window` 在 YAML 中按协议和地址设置，只用于上述回退，不覆盖已获取的容量。某个模型的例外沿用设置页已有 `context_window` 字段，不新增 Provider Registry 或配置框架。配置示例和重启要求见 [配置指南](../../user-guide/Configuration.md#31-接入路由的默认窗口)。

窗口查询、聊天执行、压缩阈值共用该解析结果；可用输入预算仍扣除输出预留和安全余量。容量缓存采用新的路由策略版本，旧跨厂商匹配记录不再读取；历史 Token 用量保留，不通过重写历史事件改变过去的执行记录。本增量未运行测试或真实服务验收。

## 3. 配置所有权

| 数据 | 来源 | 原则 |
| --- | --- | --- |
| 模型提供方 / ID / 基础地址 / 默认项 | 用户配置、服务目录 | 不固化具体模型 |
| 敏感凭证 | 凭证存储 | 不入普通配置/数据库/事件 |
| 容量、输入类型、工具调用能力 | 当前路由服务端/目录或显式声明 | 不跨路由按名字猜；远端容量回退单独标记 assumed，不推断其他能力 |
| 输出预算、超时、重试 | 运行时策略与模型提供方约束 | 不伪装成探测到的服务参数 |
| 价格、缓存、延迟、成功率 | 可更新账单/真实观测 | 不硬编码，也不从模拟服务推算 |

配置优先级与操作步骤只在 [配置指南](../../user-guide/Configuration.md) 定义，设置交互只在 [前端与桌面交互设计](../../design/FrontendDesign.md) 定义。

## 4. 未交付的候选

统一 ModelRegistry、后台能力探测、自动能力/成本路由、产品降级、向量嵌入/索引尚未完成；已有的手动模型目录获取不代表这些能力。图片输入及 Images/Responses 生图首版已有本机协议和真实 Web 定向验收，SVG 本地符号预览缺陷已于 2026-10-16 复验关闭；不计真实模型 LIVE PASS。入口与限制见 [配置指南](../../user-guide/Configuration.md#12-看图生图与文件预览)。历史“阶段 2”表示后续候选，不要求同时建设。

2026-10-16 完整后端回归发现公共目录离线回退缺陷：`models.dev` 下载发生原生 `ssl.SSLError` 时未进入已有回退，可能阻断等待刷新的模型创建。已新增受控 HTTP 响应流的失败回归，未修改产品源码；HTTPX 读取错误的回退仍通过。它不是模型服务故障或账户额度不足，不能用配置/凭证用例通过掩盖；当前状态见 [验收回执](../../project/Roadmap.md#当前验收状态)。

不维护一份假想 YAML Registry。只有出现真实多模型策略需求，才在现有配置项引用上做最小扩展，记录能力的来源/时间，优先复用模型提供方元数据。禁止新建大型模型平台。

若路由落地，显式用户选择优先，候选必须满足能力、权限、数据位置和预算约束；变更模型提供方/费用须可见。认证失败、权限/驻留冲突和非法请求不能靠跨模型提供方重试掩盖。

旧聊天适配器及其未接线的降级代码已删除。生产直接使用 LangChain 官方模型；产品自动路由/降级尚未交付，不在 Registry 自建重试引擎。已输出片段后的失败不得拼接另一模型流或自动重放副作用。

## 5. 向量嵌入 / Image 准入

向量嵌入接入须同时完成真实适配层/连接验证、维度与输入限制、版本化索引/重建、删除闭环、凭证隔离、API 和测试。Image 接入须完成真实生成、输出格式/尺寸、产物保存与清理、审计、API 和测试。

当前允许只读未配置页，不允许可保存的空表单、静态模型清单或仅改前端内存的控件。候选只有完整后端闭环和验收记录后才转当前有效。

图片增量复用官方 OpenAI SDK，并接入既有工具审批、凭证库和工作区文件，不启用实验 `ImageAdapter` 的模型别名猜测。`image_generate` 配置独立于聊天配置；多模态看图走当前聊天模型的 LangChain 内容块。Images/Responses 由用户显式选择，不自动协议降级。生成文件为用户工作区文件，保留与清理由用户决定。官方 SDK 对接本机协议服务的生成/取消/崩溃后不自动重发已验收；真实服务、计费和远端取消未验收，不标记 LIVE PASS。

模型提供方验收的 PROTOCOL/LIVE/UNVERIFIED 口径唯一来源为 [测试与验收](../../testing/TestPlan.md)；本页不复制会过期的真实服务验收矩阵。
