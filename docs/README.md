# 项目文档

MaigoKite 是面向个人工作流的本地 Agent。本目录按读者和职责划分：用户指南说明如何使用，设计文档说明行为与约束，路线图记录交付和验证状态。

## 快速阅读

- 了解产品：[产品愿景](project/Vision.md) → [需求与验收](requirements/RequirementList.md)
- 了解架构：[系统设计](design/SystemDesign.md) → [智能体运行时设计](design/AgentRuntime.md)
- 了解界面：[前端与桌面交互设计](design/FrontendDesign.md) → [墨线 UI 设计](design/agent-ui-design.md)
- 开发运行：[开发指南](development/DevelopmentGuide.md)；测试与 CI 范围见 [测试与验收](testing/TestPlan.md)
- 当前进度：[交付路线与状态](project/Roadmap.md)
- 用户配置：[安装与首次使用](user-guide/Installation.md) → [配置指南](user-guide/Configuration.md)

## 按主题查阅

| 主题 | 主文档 |
| --- | --- |
| 产品方向 / 非目标 | [产品愿景](project/Vision.md) |
| 需求、场景与 US/UC 追踪 | [需求与验收](requirements/RequirementList.md) |
| 交付计划、完成证据与阻塞 | [交付路线与状态](project/Roadmap.md) |
| 版本变更记录 | [更新日志](project/Changelog.md) |
| 组件、信任与运行形态 | [系统设计](design/SystemDesign.md) |
| Python API/Core 职责与资源所有权 | [后端设计](design/BackendDesign.md) |
| Vue/Electron 与交互 | [前端与桌面交互设计](design/FrontendDesign.md)；视觉细节见 [墨线 UI 设计](design/agent-ui-design.md) |
| 聊天执行、审批桥接、上下文和资源 | [智能体运行时设计](design/AgentRuntime.md) |
| REST/WS 契约 | [API 设计](design/API.md)；FastAPI 请求结构与事件 DTO 优先 |
| 当前业务表与事务 | [数据库设计](design/Database.md)；Repository 是实现事实 |
| 手机远程首版（未交付） | [手机远程访问](design/RemoteAccess.md) |
| 模型接入与选择 | [模型接入与选择](agent-capabilities/models/model-routing.md) |
| 长期记忆与检索边界 | [记忆与检索边界](agent-capabilities/memory/memory-system.md) |
| MCP 传输方式 / 生命周期 | [MCP 集成](agent-capabilities/mcp/mcp-integration.md) |
| SKILL.md / 扩展边界 | [本地技能与扩展边界](agent-capabilities/skills/skill-lifecycle.md) |
| 天线工作流 / MMANA 兼容与求解器评估 | [天线设计](design/AntennaWorkflow.md)：独立计算包和可选 MCP 已接入，新增入口待运行验收；完整工作流仍在设计中 |
| 工具风险、模式、审批规则 | [工具权限](agent-capabilities/security/tool-permission.md) |
| 威胁、身份、凭证与网络 | [安全边界](ops-security/Security.md) |
| 日志、审计与排障 | [日志与诊断](ops-security/Observability.md) |
| 数据备份与恢复 | [备份与恢复](ops-security/Backup.md) |
| 开发环境、命令与贡献约束 | [开发指南](development/DevelopmentGuide.md) |
| 场景与验证口径 | [测试与验收](testing/TestPlan.md) |
| Agent 任务集、评分与重复试验 | [个人 Agent 行为评测](testing/AgentEvals.md) |
| 构建、打包和发布门禁 | [构建与发布指南](deployment/ReleaseGuide.md) |
| 用户首次使用 / 设置 | [安装与首次使用](user-guide/Installation.md)、[配置指南](user-guide/Configuration.md) |
| 取舍历史 / 术语 | [架构决策](reference/Decisions.md)、[术语表](reference/Glossary.md) |

天线扩展的安装和命令见 [独立包说明](../extensions/antenna_design/README.md)，数值对照过程见 [MMANA 兼容性实验](../experiments/mmana_compatibility/README.md)。实验结果不等于主应用接入或完整设计工作流已经验收。

历史需求快照、临时迁移记录和内部验收报告只保留在本地，不纳入版本控制。当前运行机制以智能体运行时设计为准。

## 状态与维护

- “当前有效”表示文档仍适用，不表示所有测试都已通过。计划、已有代码、模拟服务测试和真实服务测试要分开说明。
- 规划中/未交付不得写成可执行用户操作。提前注册页可以显示真实只读状态，不能有假保存控件。
- 历史记录/已弃用只保留决策与证据；旧 ADR 如被新方向取代，不自动进入路线。
- 图表放在所属设计旁，字段和权限矩阵不复制。保留前端、后端、API、数据库独立成册，读者与契约不同。
- 整理文档不能产生新的测试结论。进度与验证记录放在 Roadmap，接口定义放在 API，存储规则放在 Database；其他文档链接到对应说明，不重复维护完整清单。
- 被归并文档不留长期归档副本，使用版本历史追踪；个人化不代表删除安全边界或已需兼容的数据。
- 新建文档前先确定独立读者/职责，能在主文档解释的内容不另起册。

历史 US/UC 编号保留在需求文档，前期运行时研究（含 pi）保留在 AgentRuntime 中。它们用于追溯需求和取舍，不代表当前功能已验收。
