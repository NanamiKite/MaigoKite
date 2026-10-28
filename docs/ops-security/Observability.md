# 日志、审计与运行诊断

> 权威范围：当前可观测信号、脱敏要求和最小诊断流程。
> 任务事件是产品投影，运行检查点是执行状态，两者都不是普通运维日志。

## 1. 当前信号

| 来源 | 当前提供 | 限制 |
| --- | --- | --- |
| Python ConsoleObserver / logging | 组件控制台日志、Python 标准 logging 事件数据 | 没有统一外层 JSON Schema、应用日志轮转或云同步 |
| SQLite `audit_events` | 工具/审批等安全与业务审计 | 独立于普通运行日志 |
| `task_events` / 活动区 | 工具、审批、公开过程与终态，稳定 ID/sequence | 补拉有单页上限及订阅窗口，不是无限重放 |
| health / runtime-status | Python 后端基本状态、路径和版本等实际值 | FastAPI `/health` 在初始化完成后提供状态 |
| MCP/技能清单 | 实际加载/连接能力 | 清单不是安装、授权或运行成功证明 |
| 天线 MCP 结果（可选） | 求解摘要、资源 hash 和导入完成/警告；完整原件位于服务结果目录 | 计算完成、导入完成和工程验证是不同状态；部分导入不代表求解失败，不自动重算 |
| `model.usage` | 模型提供方返回的主模型、摘要与子 Agent 用量，按 `purpose: response / summary / subagent` 区分 | 缺失表示未提供；取消后的计费、内部重试与丢失回执未必可见，不是完整账单，也不按硬编码价格推算 |

当前没有接线 Prometheus/Grafana、Langfuse、OTel exporter、完整 trace propagation、长期指标库或告警服务。依赖包或 Observer 接口存在不等于已集成。

## 2. 脱敏与关联

- 禁止记录 API 密钥、Bearer Token、Cookie、完整认证头、MCP 完整环境和隐藏思维链。
- AuditLog 递归处理字典和列表，遮盖键名包含 `secret / token / password / authorization` 的值；这不是完整内容脱敏或 DLP，调用方仍须避免在其他键或正文放入凭证。
- 工具审计优先工具名、风险、结果与限长摘要。网页全文、提示词、ToolResult 不自动 dump 到普通日志。
- 用户可见事件只保存必要 payload，原始工具详情受授权约束，不用调试 dump 代替产品 DTO。
- 当前任务/tool_call/conversation/sequence 用于关联；request/trace/deadline 尚未完整传播。字段缺失时省略，不伪造 ID。
- 高基数运行 ID 可用于日志/trace，不作为长期 metric label。UTC 时间、数据保留和删除边界要明确。

日志导出前必须脱敏；不要把整个工作区、聊天或环境文件默认上传到诊断服务。

## 3. 排障流程

1. 先看工作区与运行状态，确认 Python 后端状态和真实路径；区分页面连接断开与后端进程退出。
2. 以任务/tool_call ID 检查持久活动和审批记录，再查对应组件控制台输出。
3. 模型问题检查所选配置项、实际模型 ID、基础地址、连接验证；搜索/MCP 问题查对应工具配置与服务错误，不能混同模型 API 密钥。
4. 审批恢复查运行时检查点和审批摘要值/版本，UI 显示 waiting 不证明恢复成功。
5. 停止后仍有活动、事件缺口或进程残留，按运行时/发布门禁记录故障，不伪造成功终态。
6. 数据异常先保留一致性副本再操作，见 [备份与恢复](Backup.md)。

天线结果缺失时先查 project_status 和停止原因，区分计算、绘图、交付及本地导入失败。单次结果用 get_result，优化用 get_optimization，候选包用 get_delivery；数值完整而图不全才用 render 补画，不重复求解来修复展示。`import_warning` 表示副本不完整，检查服务原件、当前目录权限和 `import-receipt.json`，冲突时先保留用户修改。

进度不是完成证明，停滞/次数或时限用尽也不等于达标。停止未确认时先核实原编号，不重新提交；配额以独立项目库为准，不从文件数或聊天文字估算。`COMPUTED_NOT_VALIDATED / CANDIDATE_NOT_VALIDATED` 都不是工程验证通过，具体状态与资源见 [MCP 结果适配](../agent-capabilities/mcp/mcp-integration.md#31-天线结果资源适配)。

用户侧连接提示见 [安装与首次使用](../user-guide/Installation.md) 和 [配置指南](../user-guide/Configuration.md)。事件 DTO 以 [API 设计](../design/API.md) 为准。

## 4. 后续最小演进（尚未实现）

按实际诊断需求逐步复用成熟 logger/OTel 和桌面日志收集，不自研 tracing 或上传服务：

- 统一 API/Core 日志字段和 request/trace 传播，补递归脱敏回归。
- 桌面滚动文件限制大小/份数，提供用户主动导出；不要求 S3、Kubernetes 或集中式日志平台。
- 增加必要的模型耗时、任务失败/取消、浏览器/MCP 资源、事件缺口和 SQLite 状态观测。
- 费用与缓存收益仅来自真实模型提供方/代表性任务测量；阈值按容量与体验确定，不预写虚假 SLO。
- 发布需能发现数据损坏、敏感凭证泄露和持续后台执行；个人产品不预建复杂值班或告警平台。
