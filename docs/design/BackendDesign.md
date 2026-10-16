# 后端设计

> 权威范围：Python 后端的入口、职责和资源所有权。Go Gateway 与内部 gRPC 已移除。
> 执行机制见 [智能体运行时设计](AgentRuntime.md)，对外契约见 [API 设计](API.md)，存储见 [数据库设计](Database.md)。

## 1. 单进程调用链

```text
Vue Web / Electron Renderer
→ HTTP / WebSocket（本机 8080）
→ FastAPI：输入校验、本机来源、工作区归属、凭证准备、产品 DTO
→ AgentCore：产品命令、工具与权限、模型生命周期
→ LangChain create_agent / LangGraph：执行、HITL、checkpoint
→ Repository：会话、任务、审批审计与事件投影
```

只启动一个 Uvicorn worker，不使用 reload 或多 worker。Electron 或源码启动脚本持有这个 Python 进程；MCP、工具进程和 Playwright 仍由现有 Core 持有。移除语言间 RPC 不改变 Runtime、权限规则或数据库格式。

## 2. 接口模块

| 文件 | 职责 |
| --- | --- |
| `python/agent/main.py` | CLI、独占端口、本机绑定、启动器 EOF 和 Uvicorn 退出 |
| `api/app.py` | FastAPI lifespan、HTTP/WS 本机边界、安全错误映射、能力与健康接口、可选 Web 静态资源 |
| `api/contracts.py` | 请求字段约束、产品事实到既有 JSON DTO 的投影 |
| `api/sessions.py` | 会话、任务、审批、问答、插话、进程和审计入口 |
| `api/execution.py` | 持有 HTTP 发起的异步调用、等待持久接纳回执、有界 WebSocket 订阅 |
| `api/settings.py` | 模型、偏好、权限、工具、Memory、MCP/Skill 配置接口 |
| `api/credentials.py` | OS keyring 引用与 Core 内存凭证准备；不接管审批策略 |
| `api/workspace.py` / `workspace_fs.py` | 已登记目录的浏览、预览、空文件创建、系统文件夹打开和只读 Git 查询 |
| `models/discovery.py` | 有时间、分页与大小上限的服务端模型目录查询 |

协议层不能运行第二套 Agent Loop、审批状态机、续接数据或事件仓库。`AgentCore` 与 Repository 的现有命令被直接复用，不为去掉 RPC 再造 Service/Manager 框架。

Core 的扩展数据归入 `extensions.ExtensionCatalog`：已应用配置、ToolRegistry、Skill 目录、MCP 连接及临时凭证由同一个快照持有。目录自身负责凭证筛选、健康连接复用、工具重新发现及候选连接状态；Core 仍负责运行准入、准备候选快照、同时切换目录与 graph，以及按顺序关闭旧资源。失败时只清理候选新建的连接，不关闭复用的现有连接；不引入通用插件生命周期。

模型配置、凭证同步与统计接口直接调用现有 `core.models`；记忆管理接口调用 `core.memories`（`memory.notes.FolderMemories`）。后者集中保存内容校验、敏感信息拒绝、文件夹作用域和开关管理，路径解析仍复用 Core 的工作区检查，写入与审计仍由 Repository 提交。仓库中无调用方的六个 Core 模型转发方法已移除，模型配置读写/列表/删除/默认值/连接验证统一使用 `core.models`；仍有调用方的入口保留显式委托。记忆对象不缓存记忆、开关或模型请求，不接入预留的 MemorySystem。

聊天、审批恢复、中断恢复和回答提问共用 `_wait_for_run`，统一消费事件、等待根任务及传播调用方取消。Core 的运行准入、扩展原子切换和资源关闭顺序不变；涉及活跃任务/进程的工作区删除仍保留在协调入口中。

## 3. 凭证与访问边界

HTTP 与 WebSocket 均检查直接连接的 loopback 地址、Host 和浏览器 Origin，不信任代理头。客户端不能覆盖 `local-user` 主体；工作区归属来自 Repository。精确的 `null` / `file://` 来源只在本机连接时接受。这不是设备认证，远程配对仍未实现。

凭证通过 `keyring` 的系统后端保存，不允许明文 fallback。沿用原 `MaigoKite/{kind}/{workspace}` 与 account 命名：Linux 读取原 login collection；macOS 读取原服务/账号；Windows 兼容读取旧 Go 目标与 UTF-8 blob，写入新后端并读回确认后才删除旧项。平台迁移代码未完成实机验收。

执行前先恢复当前主模型凭证，再按任务允许的模型清单准备可选子模型。清单中已删除的可选子模型不阻断主对话；实际委派选中缺失模型时仍报错，不自动替换。

MCP 按实际需要恢复。新聊天允许部分可选 MCP 恢复失败；文件任务恢复、回答提问和原生工具批准不会启动或等待无关 MCP。批准 MCP 工具时，必须确认目标服务和确切工具可用，失败不消费审批决定。具体规则见 [MCP 集成](../agent-capabilities/mcp/mcp-integration.md#配置与凭证管理)。

拒绝审批只尽力恢复模型，不启动被拒绝的 MCP。秘密不进入 DTO、日志、业务表或 checkpoint。

修改模型端点或删除配置时，先在现有 `workspace_settings` 中持久撤销凭证引用，并清除 Core 的内存凭证，再尝试删除系统密钥链条目。密钥链不可用不阻塞无密钥模型管理：撤销记录阻止重启或重新创建同 ID 后复用旧密钥，物理清理失败写入 `credential.revoked / cleanup_pending` 审计。

物理清理失败不表示秘密已从操作系统删除；可在解锁密钥链后显式删除凭证引用。只有显式保存新密钥才能解除对应撤销记录。独立的删除凭证 API 仍在物理删除失败时返回错误。

模型配置编辑、凭证写入和执行前恢复共用一个普通 asyncio 锁，避免恢复中的旧密钥覆盖新端点。

模型目录查询使用 aiohttp 的公开请求接口，将校验后的 IP 固定为连接目标，原域名继续用于 Host 和 TLS 证书校验，包括 HTTPS CONNECT 隧道。不覆盖 SDK 私有连接方法。系统 HTTP(S) 代理和 `NO_PROXY` 按原地址选择，显式 loopback 直连；SOCKS 代理暂不支持目录查询，会返回配置提示。IPv4-mapped IPv6 同样检查受保护网段，禁止重定向，保留总超时、分页和响应大小上限。

## 4. 执行与事件

HTTP 接纳与执行分离：收到持久化 `task.started / task.resumed / approval.decided / question.answered` 后返回，调用方断线不取消已接纳任务。取消仍进入 Core，等待根任务清理完成后发布终态。

WebSocket 先排入无业务序号的 `stream.ready`，再开放实时订阅。只转发 Repository 已分配的 event ID、sequence 和时间；不生成备用身份。每个连接最多缓冲 64 个事件，发送超时或队列溢出关闭连接，让既有客户端通过持久历史补拉，不阻塞 Runtime。

关闭先停止接纳、取消并等待接口持有的调用，再由 Core 关闭根任务、MCP、浏览器、模型客户端和数据库。端口在 Core 初始化和恢复旧任务之前独占绑定，避免端口冲突的第二实例先修改数据。

执行恢复只读 LangGraph checkpoint；UI 读产品投影；审批安全审计读 Repository。不增加双向同步或通用 Event Sourcing。历史任务的副作用与恢复限制不因迁移而消失。

## 5. 文件、依赖与交付边界

文件 API 只操作 Core 解析的已登记目录，不接受任意根目录。POSIX 使用目录句柄与 no-follow，Windows 锁住祖先目录并拒绝 reparse point，创建只允许单个空文件/目录且不覆盖。Git 使用固定参数、禁用外部 diff/textconv/fsmonitor，限制时间和输出，不继承模型凭证环境。

API/ASGI 复用 FastAPI、Uvicorn 和 AnyIO，系统凭证复用 keyring；MCP、浏览器、模型与 LangGraph 依赖继续由现有模块管理。同步系统 I/O 在工作线程中执行，线程不能被强杀；不实现自研线程取消框架。

Web 与 Electron 继续共用相对路径构建产物。HTTP 返回的入口 HTML 补充 `<base href="/">`，让直接打开 `/chat/<id>` 时资源仍从站点根目录加载；磁盘文件不改写，Electron `file://` 保持相对路径。不存在的资源与 API（含 HEAD 请求）不回退为 SPA 页面。Windows 构建参数与共享脚本一致，不再接受 `go` 目标。

2026-10-12 迁移落码时未执行运行验收；2026-10-15 已补当前 Python HTTP/WS、真实 Core 恢复与 Web 定向验收，后续图片增量也有独立通过/失败记录，见 [当前验收状态](../project/Roadmap.md#当前验收状态)。这不是全部路由或发布验收；独立桌面 Python runtime bundling、手机远程认证和平台凭证迁移仍未完成。不能把旧 Go 链路记录当成新接口证据。

## 6. 保留但未接入的基础设施

未接入不等于应该删除。下面的模块保留用于后续开发，但不注册到 Core、ToolRegistry、API、能力声明或默认启动流程；不能通过默认配置或自动发现把未完成实现带入生产调用链。

表中的 `ImageAdapter` 是未接入的实验适配器，不代表当前产品没有生图能力。生产生图使用 `models/image_generation.py` 与 `tools/native/image_generate.py`，看图使用当前聊天模型；入口、产物与限制见 [配置指南](../user-guide/Configuration.md#12-看图生图与文件预览)。

| 模块 | 当前源码边界 | 未完成部分 |
| --- | --- | --- |
| `models/embedding_provider.py` | 抽象契约和 BGE/OpenAI/GTE 预留类保留；具体空方法明确抛出 `NotImplementedError` | 专用 Provider 的实现与产品接线 |
| `models/embedding_adapter.py` | 使用 SDK 的异步 Embeddings；OpenAI 适配和显式传入的 SDK backend；维度来自配置或实际向量，不猜默认值 | BGE/GTE 本地加载、模型管理与生命周期的产品接线；不会自行下载模型 |
| `utils/storage.py` / `inspector/timeline.py` | Storage 抽象契约、具体后端和 TimelineBuilder 保留并明确报未实现；TimelineEntry 正常保存构造参数 | 通用键值存储、时间线构建；当前业务持久化与 UI 事件仍使用 Repository |
| `models/image_provider.py` / `image_adapter.py` | ImageResult 保存实际字段；实验性 SDK 返回远端 URL/内嵌数据；由调用方在完成后关闭 adapter | 该实验适配器不接入生图工具/API；请求落盘时在发模型请求前拒绝，不返回虚假本地路径 |
| `knowledge_base/store.py` | 内存文档存储使用 SDK Document，保留稳定 ID；批量重复 ID 在写入前拒绝 | 原始材料持久化、归属与版本生命周期 |
| `knowledge_base/vector_store.py` | 预计算向量传给 FAISS/Chroma/Qdrant 的公开操作；批次与维度校验；返回原生分数 | FAISS 替换/恢复明确未实现；Qdrant 集合须预先配置，使用 UUID ID，调用方关闭 client；未接入记忆或索引任务 |
| `knowledge_base/retriever.py` | 使用 SDK VectorStore/BaseRetriever 与异步检索；限制返回量，支持的过滤条件在检索前传入，不支持时拒绝 | 关键词语料/索引须由调用方显式提供，不用空白查询的样本冒充完整语料；不支持的按 ID 读取不伪装成“未找到” |

向量适配使用预计算向量契约，文本 Retriever 使用已配置 embedding 的 SDK VectorStore；两者不能直接互换，本轮不增加桥接框架。FAISS/Chroma 的距离与 Qdrant 的相似度也不是统一分数；SDK Retriever 不提供分数时返回 `None`，不造一个 `0.0`。

向量/检索依赖仅放在 `vectorstores` 可选组，未进入默认依赖。本轮同步了依赖锁文件，没有安装这些后端、启动服务或调用模型。预留类明确失败是防止误用，不是能力交付；接入之前仍须完成实际工作流、数据归属、权限、资源关闭与取消边界，以及用户批准的验收。

适配依据是现有 SDK 的公开接口：[LangChain FAISS](https://github.com/langchain-ai/langchain-community/blob/main/libs/community/langchain_community/vectorstores/faiss.py)、[Chroma Collection](https://github.com/chroma-core/chroma/blob/main/chromadb/api/models/Collection.py)、[Qdrant async API](https://qdrant.tech/documentation/tutorials-develop/async-api/)。不实现自有向量检索或排序引擎。
