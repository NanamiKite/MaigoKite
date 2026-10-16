# 项目术语

术语只解释边界，不声明交付状态。状态与验收见 [交付路线与状态](../project/Roadmap.md)，实现细节见对应设计。

| 术语 | 项目含义 / 权威说明 |
| --- | --- |
| 个人智能体 | 为单一用户和其选定流程服务的本地优先通用智能体，不是团队平台或编码专用产品 |
| 工作区 | 个人项目/资料与资源范围；成员关系/team 值仅保留兼容 |
| API 层 | Python FastAPI 外部协议与本机/资源访问边界，不自行编排工具 |
| AgentCore | Python 组合根，组织产品命令、资源和业务写入 |
| 智能体运行时 | 执行模型/工具循环及暂停恢复；复用 LangChain/LangGraph，见 [智能体运行时设计](../design/AgentRuntime.md) |
| 运行时适配层 | 执行入口、审批桥接、UI 投影与业务持久化的桥接代码，不是第二个运行时 |
| legacy | 已移除的旧自研执行链；仅用于指代历史记录，不是可选运行后端 |
| LangGraphRuntime | create_agent/LangGraph 与产品权限、事件、持久投影之间的薄适配层 |
| ToolRegistry | 工具发现、结构定义、权限/产品元数据和公开转换入口 |
| 工具调用 / ToolMessage | 模型工具请求与结果，必须保持调用配对，不混入最终回复 |
| Checkpoint | LangGraph 执行状态，运行时恢复的事实来源；不同于业务库 |
| 产品投影 | messages/task_events 等用户查询视图，不驱动另一个执行状态机 |
| 数据契约 | 明确业务记录、API 响应和事件字段；传输类型由 OpenAPI 生成，校验范围见 [API 设计](../design/API.md#11-类型与数据契约) |
| 结构化任务结果 | 按本次任务的 JSON Schema 校验后保存的结果；后续步骤从结果接口读取，不解析进度或聊天文本 |
| 任务事件 / sequence | 持久活动事件与会话内顺序；WebSocket 只是增量通道 |
| 审批 / HITL | MaigoKite 保存审批安全事实；LangGraph interrupt/恢复控制执行暂停与续接 |
| AccessPolicy / ToolPolicy | API 层资源访问检查 / Core 工具最终决策；二者都需通过 |
| Risk level / permission mode | 内部风险 L1–L5 / 用户三个模式，不能混为用户角色；矩阵见 [工具权限](../agent-capabilities/security/tool-permission.md) |
| hard-deny | 当前策略下不能被单次审批覆盖的禁止规则；显式部署禁用项在完全访问下仍生效，模式边界见工具权限 |
| 沙箱 | 实际进程/文件/网络隔离；应用参数检查不是完整 OS 沙箱 |
| MCP | 标准工具服务协议；当前 stdio，由核心服务持有客户端/子进程 |
| BrowserRuntime | 核心服务持有的 Playwright 浏览器能力，session 易失，不是独立爬虫/协议栈 |
| SKILL.md / 技能 | 按需读取的本地声明指令；当前 workflow kind 也不执行代码 |
| 工作流 | 多步骤流程；技能说明不等于已实现持久 DAG/调度引擎 |
| 子智能体 | 隔离上下文的任务委派；当前实现范围见 [智能体运行时设计](../design/AgentRuntime.md#7-技能子任务与工作流边界) |
| 运行上下文 | 输入/运行投影，不另建长期消息事实库 |
| User Preference / 用户记忆 | 显式产品设置 / 可管理长期信息；手工记忆按文件夹、对话双开关投影到请求，与自动提取或语义检索不同 |
| 摘要 / 上下文压缩 | 对运行时保留历史的有损压缩，不等于长期记忆；生命周期见 [智能体运行时设计](../design/AgentRuntime.md#输入预算与摘要) |
| 向量嵌入 / FTS / Vector Index | 未来资料检索的派生表示/索引，原文才是权威 |
| 提示词 cache | 模型提供方前缀缓存，不是本地回答缓存；命中只取真实返回值 |
| Inspector / activity | 用户可见的执行活动，不展示隐藏思维链；子 Agent 的能力范围见运行时设计 |
| 运行时监督器 | 管理桌面启动的子进程及关闭顺序；源码中的进程管理与安装包测试是两件事 |
| PROTOCOL / LIVE / UNVERIFIED | 本地协议验收 / 真实服务验收 / 未运行；不能互相替代 |
| 阶段 | 历史工程阶段编号，不是产品界面或实时完成度标签 |

删除泛用模式、未接线接口和“Claude Code 生态派生产品”等旧术语；MaigoKite 不因参考外部实现就成为其分支或插件。
