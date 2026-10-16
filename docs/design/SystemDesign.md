# 系统设计

> 权威范围：个人智能体的系统边界、组件所有权与运行形态。
> 详细实现分别由后端、前端、API、数据库和智能体运行时文档负责；本页不重复其字段、状态机或配置。

## 1. 产品范围与原则

MaigoKite 是本地优先、用户可控的个人通用智能体，为用户选定的工作流提供定制能力，不建设通用智能体平台、团队 SaaS 或编码专用产品。产品范围见 [产品愿景](../project/Vision.md)，验收见 [需求与验收](../requirements/RequirementList.md)。

- 副作用由用户权限模式与审批控制，模型不能自授权。
- 每个进程、运行状态和共享资源只有一个明确所有者。
- 优先复用成熟的运行时、模型提供方、MCP、浏览器、取消与持久执行组件；没有明确收益时不自研替代。
- 默认本地数据；云模型、联网与未来远程访问显式启用。
- 机器契约和实际代码优先，Markdown 不维护另一套结构定义。

## 2. 组件与依赖方向

```mermaid
flowchart LR
    U[个人用户] --> UI[Vue Renderer]
    E[Electron / 源码启动器] -. 本地进程生命周期 .-> A[Python FastAPI]
    UI -->|REST / WebSocket| A
    A --> C[AgentCore]
    A --> K[OS CredentialStore]
    C --> R[选定 Runtime / Provider]
    C --> P[PermissionPolicy / ToolRegistry]
    P --> N[Native / 文件 / Skill]
    P --> B[Playwright Browser]
    P --> M[MCP stdio 子进程]
    C --> D[(SQLite / checkpoint / 文件)]
```

| 组件 | 负责 | 不负责 |
| --- | --- | --- |
| Vue | 对话、设置、文件、活动检查与审批交互 | 最终授权、Node/Shell/文件系统权限 |
| Electron | 安全窗口、白名单 IPC、桌面子进程启动和关闭 | 工具、智能体编排或业务状态 |
| Python API | REST/WS、本机来源与工作区归属、输入限额、凭证边界 | 第二套工具执行、审批或任务状态机 |
| Python 核心服务 | 产品命令、策略、工具/模型提供方资源、业务持久化与事件投影 | 公网服务、第二套通用智能体框架 |
| 成熟运行时 | 模型循环、工具编排、HITL 与检查点 | MaigoKite 用户审批 UX、安全审计和业务查询 |

执行统一为 LangChain create_agent + LangGraph，旧主循环和切换开关已删除，现有工具通过公开转换接入。执行范围与状态所有权见 [智能体运行时设计](AgentRuntime.md)，验证范围见 [交付路线与状态](../project/Roadmap.md)。源码切换不等于发布验收。

## 3. 工作区与数据

工作区是个人项目、文件和资料边界。现有 `personal/team` 数据值与成员关系表保留兼容，不代表继续建设团队管理；当前没有独立 User/Team 管理 API。手机与桌面访问同一用户数据，未来配对设备身份与业务成员关系分开。

SQLite 是当前唯一业务库，领域写入由 Python Repository 管理。敏感凭证值在 OS 凭证存储，受控同步到核心服务内存，不进入普通配置。模型执行检查点与产品业务库用途不同，不能双向同步成两套执行事实来源。

消息和原始材料是事实来源；摘要、搜索结果与未来索引由它们派生。手工记忆可在文件夹和对话开关同时开启时附带到模型请求，但不会自动从聊天中提取信息，也没有语义检索。向量嵌入和索引仍未接入，数据约束见 [数据库设计](Database.md)。

## 4. 通信与信任边界

| 边界 | 当前协议 / 契约 |
| --- | --- |
| UI ↔ Python API | HTTP/WebSocket；[API 设计](API.md)、FastAPI OpenAPI 与产品事件 DTO |
| API → 核心服务 | 同一进程内调用 AgentCore / Repository，无跨语言 RPC |
| 核心服务 ↔ MCP | 当前 stdio；[MCP](../agent-capabilities/mcp/mcp-integration.md) |
| 核心服务 → 模型提供方 | 复用模型提供方库；[模型接入](../agent-capabilities/models/model-routing.md) |
| 核心服务 → 存储 | Repository / LangGraph checkpointer，分别管理业务与执行状态 |

请求必须依次经过本机来源/工作区归属检查、Python ToolPolicy、实际资源边界与 hard-deny。API 允许不能跳过工具策略，技能也不能扩大授权。风险与模式的唯一矩阵见 [工具权限](../agent-capabilities/security/tool-permission.md)。

## 5. 本地运行与关闭

桌面模式为 Electron + 单 Python 后端，默认只监听 127.0.0.1:8080；Web 构建版由同一后端提供静态页面，开发版另外启动 Vite。MCP 子进程由核心服务持有。源码启动器和 Electron 协调退出，核心服务等待活动运行实例清理后关闭依赖；不能先标记 cancelled 再遗留后台执行。CLI 可指定 loopback 端口，成套入口与前端代理约定使用 8080。

当前尚未形成干净机器安装包的完整 runtime bundling 验收。本地 loopback/Origin 边界也不能替代设备认证。构建、资源打包和发布阻塞仅在 [构建与发布指南](../deployment/ReleaseGuide.md) 维护。

历史多人服务器、PostgreSQL、Kubernetes 和分布式 Worker 不进入个人产品路线，也不是手机访问个人电脑的前置条件。Docker/服务器部署的交付边界见 [构建与发布指南](../deployment/ReleaseGuide.md)。

## 6. 恢复与未来远程访问

WebSocket 只传递事件，不是事实来源。UI 读取持久事件并去重，连接中断不自动取消主机任务。执行恢复以 LangGraph 检查点为依据；缺少检查点的历史任务不能续跑。浏览器 session 是易失资源，不能承诺重启续跑。恢复资格与限制见 [智能体运行时设计](AgentRuntime.md) 和 [API 设计](API.md)。

手机 Web/PWA 为规划中，通过受保护 HTTPS 入口访问同一 Python API；不引入另一套运行时，也不能利用反向代理落到 loopback 就免认证。当前没有设备配对认证，不得直接公开本地端口。连接方案、权限和 R0–R3 验收仅由 [手机远程访问](RemoteAccess.md) 定义。

## 7. 专项入口

- [后端设计](BackendDesign.md)、[前端与桌面交互设计](FrontendDesign.md)
- [API 设计](API.md)、[数据库设计](Database.md)、[智能体运行时设计](AgentRuntime.md)
- [安全边界](../ops-security/Security.md)、[工具权限](../agent-capabilities/security/tool-permission.md)
- [交付路线与状态](../project/Roadmap.md)、[架构决策](../reference/Decisions.md)

现有单层只读子 Agent 与未来的后台、写入或多层委派分别看待。工作流、子 Agent 扩展和远程 MCP 应根据具体需求开发，不能因为有预留类、目录或页面就算作已实现。
