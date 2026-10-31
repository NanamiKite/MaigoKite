# MaigoKite API 设计

> 本文说明当前 REST 资源、WebSocket 事件和兼容规则，API 主版本为 v1。
> 功能进度与实际测试范围见 [交付路线与状态](../project/Roadmap.md)。

请求模型由 FastAPI 生成 OpenAPI，事件继续沿用稳定产品 DTO；本文档说明稳定资源与行为，不复制生成代码。

## 1. 协议边界

| 边界 | 协议 | 服务端 |
|---|---|---|
| UI → Python API | 本机 HTTP REST | FastAPI / Uvicorn |
| UI ↔ Python API | 本机 WebSocket | FastAPI / Uvicorn |
| API → AgentCore | 同进程调用 | Python |
| 智能体核心 ↔ MCP Server | MCP stdio | MCP Server |

### 1.1 类型与数据契约

固定业务记录、公开响应和第三方 SDK 数据分层处理，不共用一个万能字典：

| 边界 | 当前实现 |
|---|---|
| SQLite → Repository/Core | `agent/data_contracts.py` 中的 TypedDict；任务、工具调用、会话、消息、审批、授权、提问、事件记录在离开持久化边界时由 Pydantic TypeAdapter 校验 |
| Core/Runtime → 工具 | `RunMetadata` / `ExecutionMetadata` 描述已知字段；进程句柄、凭证保留在内部执行上下文，不作为 JSON 返回 |
| API → UI | `api/contracts.py` 中的 Pydantic 响应模型；已覆盖会话 CRUD/分岔、消息查询、任务查询/取消/结果、审批查询、授权查询、提问查询和会话快照 |
| Runtime → 事件存储/传输 | `event_contracts.py` 以事件名关联关键 payload；实时流和历史事件使用同一信封与校验规则 |
| HTTP/WebSocket → 前端 | 从实际 FastAPI OpenAPI 生成 TypeScript 传输类型和 Ajv 独立校验函数；先校验 `unknown`，再交给视图层 |

- JSON Schema、工具参数、用户结构化结果等动态字段使用递归 `JSONValue` / `JSONObject`，不是任意 Python 对象；事件不再使用 `default=str` 静默序列化未知对象。SDK 的消息与工具调用优先复用 SDK 自带类型。
- 已声明的事件不能因 payload 校验失败退回「未知事件」分支。扩展事件保留合法 JSON payload；未纳入关键事件表的类型尚不具备业务字段级校验。
- 请求格式错误为 400；已覆盖边界的内部记录或响应校验失败为 500 `CONTRACT_INVALID`，不回显验证异常中的原始数据。前端 HTTP 校验失败使用同名错误码，WebSocket 无效事件断开并走既有历史恢复流程，不冒充处理成功。
- 快照继续保留既有 `arguments_json / reuse_scope_json / options_json` 协议，前端解码后再次按审批/提问模型校验。
- 这是主执行链的收敛范围，不表示整个 API 已全部类型化。设置、扩展、进程、任务 inspection/input 等尚有动态响应；前端这些未迁移调用仍保留原泛型边界。视图状态允许尚未收到回执的局部记录，不等同于完整传输 DTO。

更新响应模型或事件契约后，在已安装项目 Python/前端依赖的源码目录运行（Linux / PowerShell 相同）：

```sh
npm --prefix web run generate:contracts
```

默认使用仓库 `.venv`，也可用 `MAIGOKITE_PYTHON` 指定解释器。脚本仅构造路由导出 OpenAPI，不进入应用 lifespan、不打开用户数据库、不读取凭证或调用模型。生成文件位于 `web/src/api/generated/`，与契约修改一起提交，不手工编辑。普通启动/构建使用已生成的文件，不隐式启动后端生成契约。

生成依赖复用 [openapi-typescript](https://openapi-ts.dev/node) 和 [Ajv standalone](https://ajv.js.org/standalone.html)，运行端不引入 schema 编译器。Pydantic 下限为 2.12，匹配项目 Python 3.14 的注解支持（[官方说明](https://pydantic.dev/articles/pydantic-v2-12-release)）。

## 2. 通用调用上下文

外部请求应经过 API 层身份/资源校验形成可信上下文。以下是目标关联字段；当前请求仅携带部分主体/工作区字段，request/trace/deadline 尚未统一传播：

```text
request_id
trace_id
principal_id
workspace_id
client_version
deadline
```

- 外部客户端不得在请求体中覆盖 `principal_id`。
- `workspace_id` 可来自路径或请求头，但必须经过成员关系校验。
- 资源不存在和无权访问时，应按安全策略返回形式一致的响应，避免资源枚举。

## 3. REST 资源

统一前缀：`/api/v1`。

### 3.1 身份与工作区

当前版本没有独立的 `/me`、工作区 CRUD 或成员管理 REST。所有已注册资源都位于 `/workspaces/{workspace_id}` 下，受本地访问中间件与智能体核心成员关系检查共同约束。API 层当前固定映射到 `local-user`，这不构成远程设备认证。团队身份、工作区 CRUD 与成员管理已移出当前路线，不在本文保留未注册接口草案。

`local-user` / owner 检查只决定产品数据归属，不证明调用者是桌面客户端。桌面模式先在 LocalBoundary 中校验本次启动的 `X-MaigoKite-Desktop-Token`，覆盖 HTTP、OPTIONS 和 WebSocket；认证发生在 CORS 与业务路由之前。凭据由 Electron 主进程附加，渲染页及公开 DTO 不持有它。`Origin: null` 必须同时通过凭据验证；Electron 本地页面的 WebSocket 使用精确的 `Origin: file://`，也仅在凭据有效时接受。HTTP/OPTIONS 不接受 `file://`，CORS 不新增此来源。未验证的来源返回 403，来源合法但缺少桌面凭据返回 401 `LOCAL_AUTH_REQUIRED`，WebSocket 拒绝码为 1008（接受前拒绝时，握手表现为 HTTP 403）。直接 peer 与 Host 必须仍为 loopback，不使用转发头认证。

浏览器模式保留现有本机 HTTP / 无 Origin 原生调用，拒绝不透明页面来源；它尚不是完整的本机进程或设备认证。详见 [安全边界](../ops-security/Security.md#5-网络与-electron)。

文件夹工作区位于既有个人数据范围内，不增加租户或身份系统：

```text
GET  /workspaces/{workspace_id}/folders
GET  /workspaces/{workspace_id}/folders/browse
POST /workspaces/{workspace_id}/folders
DELETE /workspaces/{workspace_id}/folders/{folder_id}
```

POST 仅所有者可用，接受 `root_path` 与可选 `name`，由 Core 校验并登记规范化的实际目录，同目录重复添加返回已有记录。GET 返回 `id / name / root_path`，空 ID 表示启动配置的默认目录。模型、凭证、个人记忆仍以 `workspace_id` 为范围，不以 `folder_id` 隔离。

`DELETE folders/{folder_id}` 仅 owner 可用，API/Core 均检查身份；成功返回 204。只删除文件夹登记，不读取或删除磁盘文件、消息、任务、checkpoint、模型或记忆。关联对话（含归档）在同一 SQLite 事务中移至默认目录，撤销原目录下的复用审批授权，并写入 `workspace_folder.deleted` 审计；之后的文件操作使用默认目录。默认空 ID 不可删除（400），不存在或不属于当前个人范围的登记为 404。存在未结束任务、仍在清理的 Runtime run、运行进程或最新可恢复 interrupted 任务时返回 409；整项操作失败，不部分迁移。磁盘目录已消失不影响移除登记。界面提供二次确认并同步已载入对话的目录归属，迟到的旧列表不重新显示被移除的工作区。

`folders/browse` 为本机 Web 文件夹选择器提供目录列表，要求 owner 且请求直接连接地址为 loopback（不信任转发头来判定本机）。可选 `path` 为已有绝对目录，省略时从 API 层进程用户的主目录开始；`show_hidden=true` 包含点开头目录。响应为 `{path, parent, home, root, entries: [{name, path}], truncated}`，根目录的 parent 为空。

只枚举目录，不返回文件或读取内容；每次最多扫描 5000 项、返回 500 个文件夹，不缓存响应。注册路径仍调用上述 POST，由 `core.conversations` 做最终校验。它不是工作区文件预览接口，也不是远程设备文件选择协议；API/Core 使用同一后端文件系统，反向代理不应将此本机专用入口公开到远程。

手机访问为规划中，范围以 [手机远程访问](RemoteAccess.md) 为准。配对设备关联同一个个人主体；首版复用会话、任务、审批和 UI Event DTO，仅开放明确列出的远程资源。配对、会话和撤销的具体契约在实施时补充，本文不将它们列成已存在的端点。

### 3.2 会话与消息

```text
GET    /workspaces/{workspace_id}/conversations
POST   /workspaces/{workspace_id}/conversations
POST   /workspaces/{workspace_id}/conversations/{conversation_id}/fork
GET    /workspaces/{workspace_id}/conversations/{conversation_id}
PATCH  /workspaces/{workspace_id}/conversations/{conversation_id}
DELETE /workspaces/{workspace_id}/conversations/{conversation_id}
GET    /workspaces/{workspace_id}/conversations/{conversation_id}/messages
POST   /workspaces/{workspace_id}/conversations/{conversation_id}/messages
GET    /workspaces/{workspace_id}/conversations/{conversation_id}/snapshot
```

发送消息返回已创建的 `task_id`；增量结果通过事件流提供。

请求可选携带 `output_schema`，只约束本次任务的最终结果；省略或为 null 时仍为普通聊天。它不是对话或模型全局设置，详见下方“结构化任务结果”。结构化请求的幂等指纹同时覆盖 Schema；相同键更换 Schema 返回 409，普通聊天原有指纹保持兼容。

`POST .../fork` 需 `Idempotency-Key`（1–128 个无空格可打印 ASCII 字符），body 为 `{}` 或
`{"message_id":"msg_..."}`。省略消息 ID 时选择最新持久消息；分岔点须为助手回复，源会话有活动/待审批任务时返回 409。
201 返回新 `Conversation`，含 `forked_from_conversation_id / forked_from_message_id`。同源会话同键同参数返回原分岔；换参数使用旧键返回 409。
分岔复制该点之前的 user/assistant 消息，最多 500 条/2 MiB，超限返回 400 而非静默裁剪；创建与消息复制同事务提交。
模型、思考设置、目录和子 Agent 限额沿用源会话；完全访问及旧自动模式降为工作区权限。审批授权、任务、checkpoint、工具事件、队列和文件均不复制，也不调用模型。
分岔的首次执行沿用现有产品历史导入入口，不恢复源任务；文件仍共享，源会话删除不删除分岔。GUI 从已保存助手回复下方的「⋯ → 从此处分叉新对话」传入该回复的 `message_id`，不再由会话管理默认从末尾分岔；不是逐节点执行 Fork 或文件回滚。
请求键只在本次页面内保留；刷新后先查看会话列表，不保证找回原键。分岔删除后不保留去重墓碑。

发送消息可带 `Idempotency-Key`（1–128 个无空格可打印 ASCII 字符）。键按工作区、会话、主体隔离，内容指纹覆盖 content 与 attachments；新请求返回 202，相同键/相同内容返回原任务的 200 `{task_id, conversation_id, status, replayed: true}` 和 `Idempotency-Replayed: true`，不同内容返回 409。返回旧任务不会重新发模型请求、写消息或广播历史事件；中断/失败任务也不会因重发键自动恢复。相同文本的新操作必须用新键。省略键保持旧行为；GUI 已为每次逻辑提交生成键，网络/5xx 错误时保留原键，用户点击确认才重试。4xx 明确拒绝会释放键；成功回执后刷新失败不等于提交失败。键仅保留在当前页面会话内，刷新页面后应先查询任务记录，不保证找回原键。服务端的键随任务保留，删除会话/任务后不再保留去重记录。正常 GUI 发送/完成与刷新已有定向验收，图片 API 也验证了相同键返回原任务；提交结果不明后的人工重试和幂等并发故障不在该通过范围。

`snapshot` 返回 `messages / tasks / approvals / questions / last_sequence / truncated`，其中 approvals/questions 只含待处理项，嵌套审批和问题沿用已发布的 JSON 字符串字段（`arguments_json / options_json`），客户端 API 边界将审批参数和问题选项转换为界面 DTO。在单 Core 的同一个 Repository 锁内读取，每类最多 200 条最新记录并按时间返回，条目合用约 900 KB 序列化预算；省略时明确 `truncated: true`。这解决多个列表分开读取的观察窗口，不提供跨进程一致性或完整事件快照。GUI 已先订阅并缓冲，再取快照、补拉历史并按 event_id 合并；快照水位内的活动卡片与未完成回复仍回放，但不得覆盖较新的任务状态或重新触发排队发送。待审批/待回答卡片直接使用真实快照水位，不制造伪事件。`truncated` 会提示较早记录可能不完整，客户端不能因缺少某个待处理 ID 就关闭对应卡片。接线不等同于断线恢复验收。

会话包含 `folder_id / archived`；创建可指定 `folder_id`，PATCH 可更新 `title / folder_id / archived`。既有会话迁移后仍使用默认目录且未归档。前端按文件夹与归档状态显示列表；直接进入会话时切换到其归属目录。归档会话拒绝发送消息；未结束的任务禁止移动、归档、删除（409）。DELETE 清理产品任务、消息及 LangGraph checkpoint，不删除工作区文件，保留安全审计。

`title_source` 为只读状态：`pending / automatic / manual`。创建时省略标题或传空值进入 `pending`；首条用户消息在同一事务中生成标题并改为 `automatic`。显式命名、重命名及分岔标题为 `manual`，不因文字恰好是“新对话”而覆盖。旧记录没有来源证据，升级时统一保留标题并设为 `manual`；不根据语言或标题内容猜测来源。GUI 新建入口发送空标题，不发送翻译后的默认名称。

归档仅禁止新执行，不禁止相同幂等键/内容查询已存在的提交回执。幂等请求中的会话若已删除，返回 404，不悄悄新建会话。`snapshot` 的空列表返回 `[]`，不返回 `null`。

`files / files/content / changes / changes/diff / runtime-status` 支持可选 `folder_id` 查询参数，API 通过 `core.conversations` 解析已登记目录，客户端不能直接传任意根目录。Runtime 权限校验与工具上下文使用会话所属目录，不变更共享全局根目录；MCP 进程启动目录和技能加载目录不因切换而变化。

#### 附件输入、图片生成与文件预览

- `POST /workspaces/{workspace_id}/files/images?folder_id=...` 接受 `{data: "纯 Base64"}`，仅工作区所有者可上传。支持 PNG/JPEG/WebP/GIF，单张不超过 16 MiB；创建 `chat-images/` 下的独立文件，返回 `ImageReference`（type/path/folder_id/sha256/mime_type/size）。文件名由应用生成，不接受任意写入路径。该路由与 `/files/attachments` 上传路由的请求体上限为 23 MiB，其余路由仍为 2 MiB。
- `POST /files/attachments?folder_id=...` 接受 `{name, data: "纯 Base64"}`；`POST /files/attachments/reference?folder_id=...` 接受 `{path}`，将当前工作区文件复制为快照。两个入口均要求 owner，拒绝越界、敏感路径、链接和非普通文件。文本支持 UTF-8（允许 BOM）；PDF、DOCX、XLSX、PPTX 经本地解析校验后保存原始快照。按内容识别，拒绝加密、损坏或超过处理限制的文档；不解析旧二进制 Office 格式或音视频。
- 图片继续返回 `ImageReference`；文本返回 `TextReference`，包含 `type: "text" / name / path / folder_id / sha256 / mime_type: "text/plain" / size`。图片保存在 `chat-images/`，文本保存在 `chat-files/`，应用生成文件名，保留安全的扩展名以支持已有 Markdown/代码预览。上传文件保留在工作区，删除消息不会删除文件，不提供自动垃圾回收。
- 文档返回 `DocumentReference`，包含 `type: "document" / name / path / folder_id / sha256 / size / format: "pdf" | "docx" | "xlsx" | "pptx"`，也保存在 `chat-files/`。原文不整段内联，而是通过已有 `file_read(view="document")` 按需读取。
- 消息 `attachments` 接受图片、文本、文档三种引用，最多 8 个文件，其中图片最多 4 张，合计不超过 16 MiB。准入检查工作区归属、大小与哈希；消息查询、幂等指纹和 `task.started` 仍沿用 `attachments_json`，不改变纯文本及旧图片请求。
- `GET /settings/model-inputs?model_id=...` 返回 `image_input: true/false/null`、文本支持、`document_inputs: ["pdf", "docx", "xlsx", "pptx"]` 及附件限制；缺省 model_id 使用默认模型。未知看图能力保持 null，不按名称猜测。GUI 允许先添加附件再换模型，已明确不支持图片且无本地 OCR 时阻止发送并保留草稿。`image_fallback=ocr` 表示可用本机文字识别，`ocr_languages` 返回已安装语言；原 `image_input` 仍只表示模型原生能力，不把 OCR 伪装成视觉。服务端准入检查后，在请求前转换当前图片并再次校验。
- 文本在现有上下文中按累计预算内联；其余保留带哈希的 `file_read` 引用，读取工具不可用时明确拒绝，不返回无法使用的句柄。`file_read` 增加 `sha256` 校验及 `view: "text" | "image" | "document" | "ocr"`；文本复用 offset/next_offset 字节分页，也可用 start_line（从 1 起）和 line_count（默认 100，上限 1000）读取 UTF-8 选定行；按行与哈希读取上限为 16 MiB，每次内容仍以 64000 字节封顶，超长单行通过 next_offset 继续。图片以 SDK 标准图像内容回给模型，UI 事件仍只投影文件元数据。所有工具读取沿用现有权限和审计。
- `GET /files/document?folder_id=...&path=...&sha256=...&unit=1&text_offset=0` 返回 `DocumentPage`。PDF 的 unit 为原文件物理页号；DOCX 为正文顶层段落/表格顺序编号，不冒充 Word 页码；XLSX 按工作表每 100 行分段，保留单元格坐标及公式文本，PPTX 按幻灯片提取文字和表格。响应含 format/sha256/parser_version/mode/unit/end_unit/total_units/kind/text/text_offset/next_offset/next_unit。侧栏按此接口查看提取文字；后续翻页携带首响应哈希，源文件变化返回 409，不混用版本。文本以普通文本渲染，不执行文档脚本或外部资源。
- 模型通过 `file_read(view="document", unit=1, unit_count=5, text_offset=0, sha256=...)` 读取；unit_count 最大 10，返回字节上限仍为 64000。长内容先在相同 unit/unit_count 下续用 next_offset；完成后再推进 next_unit。没有可提取文字的页面保留空文本，不将其解释为没有图片/图纸；不会自动调用视觉/OCR。
- `GET /files/document/navigation?folder_id=...&path=...&sha256=...&action=outline|search&query=...&offset=0&ignore_case=true` 返回 `DocumentNavigation`，复用文件预览的鉴权、路径及哈希检查。outline 读取真实 PDF 书签、DOCX 内置 Heading 1–9、工作表或幻灯片标题，不根据正文猜目录；search 为 1–200 字符的字面匹配，默认忽略大小写，不接受正则。只覆盖已提取文字，不搜索图片或省略的 Word 结构。
- 文档导航返回 `items[{unit,kind,text,text_offset,level}]`，单次最多 20 项，`next_offset` 为下一组目录项/匹配项游标，不是文件字节偏移；响应保留 format/sha256/parser_version/total_units/empty_text_units。空文本单元不等于空白页面；没有匹配不证明图片中没有目标内容。PDF 目录最多保留 1000 个有效目标，超限时 `outline_truncated=true`。
- `file_read` 的 document 模式新增 `document_action=outline|search`，搜索携带 query，可用 lookup_offset/ignore_case 控制分页和大小写；默认 read 保持原接口。模型先取目录或定位关键词，再用 `document_action=read, unit_count=1, unit=结果.unit, text_offset=结果.text_offset` 回读，并沿用响应 sha256。文档附件的初始读取提示指向 outline，不额外调用模型生成摘要。
- `GET /files/document/page?folder_id=...&path=...&sha256=...&unit=1` 返回该 PDF 页的 PNG；复用路径、敏感项、版本及登录检查。PDFium 独立子进程按页栅格化，不嵌入 PDF 脚本；目标像素至多约 400 万、最长边 4096，输出不超过 16 MiB，20 秒超时。
- `GET /files/document/ocr-languages` 返回本机 Tesseract 可用语言列表；空列表不表示已安装。`GET /files/document?...&mode=ocr` 对选定 PDF 页识别文字，返回 `mode=ocr` 的 DocumentPage，仍按 text_offset 续读。整条页 OCR 请求 25 秒上限；不自动 OCR 整本文件，也不将识别结果混进原文关键词检索。
- 工具使用 `file_read(view=document, document_action=render|ocr, unit=...)` 查看同一页，或 `file_read(view=ocr, path=..., sha256=..., text_offset=...)` 识别栅格图片文字。页图经标准图片块回给视觉模型；已知纯文本模型不能接收页图，可选择 OCR。OCR 不具备图表理解能力。
- 文字提取、目录和查找共用 pypdf/python-docx/openpyxl/python-pptx 和 AnyIO 可取消工作进程，最多并发 2 项，20 秒超时；页图/OCR 另有 2 项并发上限。单文件 16 MiB；PDF 至多 200 页、单页展开内容流上限 4 MiB；Office 至多 3000 ZIP 成员、展开总量 64 MiB、单 XML 8 MiB；提取文字总量 100 万字符；XLSX 至多 100 个工作表、每表 10000 行/200 列，PPTX 至多 200 张幻灯片。内容流大小检查发生在第三方解码后，因此不是硬内存配额或 OS 沙箱。进程内缓存最多 4 份，键含原字节、格式和 parser_version；没有持久解析缓存。
- 不按固定回合数衰减附件；同一内容只物化一次。旧图片仅在总量超过 16 MiB 或切换到已知不支持看图的模型时移除视觉块并保留引用；当前回合原生不支持时仅在本机 OCR 可用后降级文字，否则拒绝；原生图片总量仍超限时明确拒绝。历史文件缺失/变化时说明不可用，不伪造内容；跨目录不重新读取旧目录附件。GUI 附件消息仍需等当前任务结束后发送。
- `GET /workspaces/{workspace_id}/settings/image-generation` 和 `PUT` 使用已声明的响应契约。PUT 为 `{settings, api_key?, clear_api_key?}`；settings 包含 enabled/protocol/base_url/model/size/response_format/timeout_seconds。凭证引用按端点绑定，`credential_required` 由服务端管理，换地址不沿用旧密钥。
- protocol 为 `images` 时复用 OpenAI SDK `images.generate`，一次请求一张图片；`responses` 时调用官方 Responses `image_generation` 工具，限制一次工具调用并设置 `store=false`。不按端口或模型名猜协议、不自动换接口、不自动重试；兼容服务必须实际实现所选接口。
- `image_generate` 走现有权限审批与审计，L5 单次授权；显式完全访问沿用既有放行语义。审批哈希绑定当前配置摘要。请求前使用工具调用 ID 原子写入审计意图，同一 ID 的崩溃重放不再请求服务；结果不确定时提示先检查产物，再显式发起新的调用。成功结果 `images` 只含工作区文件信息，保存到 `generated-images/`，并沿现有 `file.changed` 通知文件变化。取消不能保证远端停止或未计费；不提供跨崩溃 exactly-once 保证。
- `GET /files/content` 返回 mime_type/data_url，可带 `sha256` 核对附件快照。哈希不符返回 `409 ATTACHMENT_CHANGED`；聊天缩略图与附件预览均携带哈希，不展示被替换后的内容作为原附件。文本上限 512 KiB，受支持栅格图片上限 16 MiB，超限不返回截断 Base64。Markdown 由前端净化后渲染；HTML 为无脚本、无网络、无同源权限的静态 iframe。SVG 沿用文本接口，由前端 XML 解析与 DOMPurify 净化后作为 `<img>` 图片预览，不作为内联 DOM 或可执行文档；外部引用、脚本与嵌入网页不可用，保留本地片段引用和内嵌栅格图片。截断、畸形或含自定义实体的 SVG 不渲染，保留源码入口。
- `GET /files/download?folder_id=...&path=...` 使用相同工作区前缀与认证，额外要求 owner；只读注册目录内的非敏感普通文件，安全句柄读取拒绝链接/越界，最多 48 MiB。返回 `application/octet-stream`、安全 `Content-Disposition: attachment`、`no-store/nosniff`；超限为 `413 FILE_DOWNLOAD_TOO_LARGE`。前端使用携带认证的 Blob 下载，不把带凭证 URL 交给浏览器。它不解压 ZIP，也不要求文件可预览。

2026-10-28 的文本附件、预算物化和回读增量尚未进行行为验收；不沿用下面旧图片路径的测试结论。

上面的路径仍受既有本机访问和工作区授权边界约束。不新增多模态 Runtime，不表示所有模型均支持图片输入，也不包含任意厂商原生图片输出、图生图/蒙版编辑、视频或音频。

2026-10-15 已运行真实 Core/SDK 与 Web/Chromium 定向验收；图片上传/引用/重启、两种生图协议、审批/取消/失败与基础预览通过。当时发现 SVG 本地 `<use href="#…">` 被净化器移除。**2026-10-16 局部放行 `use` 后，原失败单测与真实 GUI 复验均通过，并新增 `xlink:href`、非法/外部引用过滤和实际渲染像素检查，SVG 缺陷关闭。** 历史失败数字保留。真实服务、密钥链、旧库迁移和 Windows 不在通过范围，准确回执见 [当前验收状态](../project/Roadmap.md#当前验收状态)。

协议参考：[OpenAI 图片输入说明](https://developers.openai.com/api/docs/guides/images-vision)、[Responses 生图工具](https://developers.openai.com/api/docs/guides/tools-image-generation)。

### 3.3 任务、工具调用与审批

```text
GET  /workspaces/{workspace_id}/tasks?conversation_id={conversation_id}
GET  /workspaces/{workspace_id}/tasks/{task_id}
GET  /workspaces/{workspace_id}/tasks/{task_id}/result
GET  /workspaces/{workspace_id}/tasks/{task_id}/inspection
POST /workspaces/{workspace_id}/tasks/{task_id}/cancel
GET  /workspaces/{workspace_id}/conversations/{conversation_id}/events/history?after={sequence}
GET  /workspaces/{workspace_id}/approvals
GET  /workspaces/{workspace_id}/approvals/{approval_id}
POST /workspaces/{workspace_id}/approvals/{approval_id}/decision
GET  /workspaces/{workspace_id}/approval-authorizations?conversation_id={conversation_id}
DELETE /workspaces/{workspace_id}/approvals/{approval_id}/authorization
```

任务重试和单条工具调用独立查询尚未注册；任务级工具详情由 `inspection` 提供（见 3.8）。取消操作请求停止任务，并关闭仍待处理的审批；实际停止确认和清理顺序取决于当前执行路径，见 [智能体运行时设计](AgentRuntime.md)。第三方 MCP 调用能否立即被传输方式中止，仍有限制。

审批由智能体核心创建，外部 API 不提供任意创建审批请求的入口。审批决定必须包含目标审批 ID、decision 和审批版本。服务端从已保存的规范化工具调用读取参数，同时验证版本与过期时间；客户端不能在决定时替换参数。

决定请求支持 `{"decision":"deny","version":1,"note":"请只读取文件，不要修改"}`。
`note` 可选，最多 2000 字符；与决定、版本推进、审计在同一事务中保存。GET 单条审批可查询已结束的决定，返回 `note / decided_at`，不再只能查询 pending 列表。

允许请求可携带 `scope`：`{"decision":"allow","version":1,"scope":"session"}`。缺省为 `once`；另支持 `task / session / session_public_web`，仅可选择审批响应的 `allowed_scopes`。`deny` 只接受 `once`。响应增加服务端生成的 `reuse_scope`（只读）、`authorization_scope / authorization_expires_at / authorization_revoked_at`；客户端不得提交自定义 scope 规则。L5/MCP/沙箱外命令不支持复用。

`session_public_web` 需要用户显式选择，仅出现在 L3 内置搜索/网页读取的可用范围中，覆盖同会话的 `web_search / web_fetch`，不覆盖 Browser、MCP 或命令。`reuse_scope` 继续记录原操作的引擎/origin，授权范围必须结合 `authorization_scope`（授权列表中为 `scope`）解释；旧 `session` 仍只批准原引擎/站点。授权最长 8 小时，沿用同一撤销、权限变更及审计入口。不支持该范围的操作返回 `APPROVAL_SCOPE_UNAVAILABLE`，不能用改写请求给其他工具授予公共网页权限。

授权列表返回 `{"authorizations":[...]}`，项包括 `approval_id / conversation_id / task_id / scope / expires_at / resource`；撤销返回 204。两接口要求 owner 且校验主体，不删除原审批审计；任务/会话授权最长 8 小时。每次执行重新核对权限和回执，详见 [工具权限](../agent-capabilities/security/tool-permission.md#窄范围复用)。

GUI 审批卡已接 scope 选择，只展示该审批版本允许的选项；会话管理接授权列表与撤销。快照额外解析 `reuse_scope_json` 为同一 DTO。完全访问显式确认后发送 `danger_full_access`，旧自动模式仅保留显示，不冒充完全访问。本次没有运行交互验收。

#### 结构化任务结果

向现有消息入口 `POST /api/v1/workspaces/personal/conversations/{conversation_id}/messages` 发送：

```json
{
  "content": "读取工作区 README，返回摘要与待办建议。",
  "output_schema": {
    "type": "object",
    "properties": {
      "summary": {"type": "string"},
      "action_items": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["summary", "action_items"],
    "additionalProperties": false
  }
}
```

仍返回 202 与 `task_id`；工具、审批、提问及取消沿用原入口，不新增工作流执行框架。首版复用 LangChain `ToolStrategy`，需要所选服务/模型支持工具调用；不根据厂商名称承诺支持，也不静默降为未经校验的文本。`/tool` 直接执行命令不支持同时指定输出 Schema。

Schema 须为 JSON Schema 2020-12，根类型为 object，最多 16 KiB / 32 层。支持嵌套类型、required、枚举等标准约束及本地片段引用；不支持 `$id`、外部引用或顶层 `oneOf`（LangChain 将其解释为多个结果工具，首版只接受单一结果）。引用校验不访问网络或文件。结果最多 256 KiB / 64 层，NaN/Infinity 不接受；`format` 按标准注解处理，不承诺业务字段的真实性。无效 Schema 在准入前拒绝，不创建任务或调用模型。

`GET /api/v1/workspaces/{workspace_id}/tasks/{task_id}/result` 返回：

```json
{
  "task_id": "task_...",
  "status": "succeeded",
  "ready": true,
  "output_mode": "json_schema",
  "schema": {"type": "object"},
  "value": {"summary": "项目摘要", "action_items": ["补充安装说明"]},
  "error": ""
}
```

示例中的 schema 为节选；实际响应返回该任务完整原始 Schema。接口受既有工作区身份和任务归属检查保护。运行、待审批、失败、取消或尚未提交结果时返回 `ready=false / value=null`；普通聊天为 `output_mode=text / schema=null / ready=false`。下游必须同时检查 `status=succeeded` 和 `ready=true`，不解析消息、思考或工具进度作为结果。失败时 `error` 为安全产品诊断；未知/越权任务返回 404。

任务列表与单任务 DTO 增加 `output_mode / result_ready`；`task.started` 带输出模式，结构化提交后的 `task.succeeded` 带 `result_ready=true`。JSON 结果仍同步为助手消息以兼容既有聊天展示，只有独立结果接口是工作流消费入口。流式文字仍可表示执行进度，不是已校验结果。内部结果工具不进入产品工具目录、审批或 `tool.*` 记录；校验不通过、多个结果、结果与尚待执行工具混交时任务失败，不自动修补、重试整个任务或提供半成品。

Schema 随任务准入固定保存，审批/提问续接和重启恢复读取同一份约束；同 Schema 的提交也不会共享下一任务的执行状态。结果、对应助手消息和成功状态同事务提交，停止先提交则不保存结果，成功先提交则停止不撤销结果。事件通知晚于事务；通知失败后以查询结果与快照为准。

若最终 checkpoint 已完成而结果事务尚未提交，最新 interrupted 任务可经既有 owner/version 恢复入口只补存结果，不重放工具；仍未完成的执行保留现有文件读写恢复限制，不新增浏览器/MCP 副作用重放。`structured_output / task_result` 能力位表示后端入口已接通，不表示所有 Provider 已实测。前端配置入口、自动串联步骤及运行验收本批不包含。

### 3.4 用户记忆与数据摘要

当前已注册的用户记忆契约：

```text
GET    /workspaces/{workspace_id}/memories
GET    /workspaces/{workspace_id}/memory-settings?folder_id=
PATCH  /workspaces/{workspace_id}/memory-settings
POST   /workspaces/{workspace_id}/memories
PATCH  /workspaces/{workspace_id}/memories/{memory_id}
DELETE /workspaces/{workspace_id}/memories/{memory_id}
GET    /workspaces/{workspace_id}/data-summary
```

`GET memories?q=` 当前执行大小写不敏感的关键词查询。所有记忆操作都使用认证主体作为 `subject_user_id`，客户端不能指定其他用户。写入、修改或删除操作与对应审计事件在同一数据库事务中提交。`data-summary` 仅工作区所有者可读，返回权威数据计数，不返回内容。

记忆列表的 `folder_id` 参数区分三种情况：不传只返回未归属旧记录，空字符串选择默认目录，非空值选择注册文件夹。创建/修改的 `folder_id` 由服务端解析成文件夹 ID + 根路径的归属；修改时不传则保留原归属，不自动迁移旧记录。返回 `folder_scope` 为不透明归属值，不作为查询权限凭据。

`memory-settings` PATCH 使用 `{folder_id, enabled}`，GET 返回同形对象；这是文件夹级开关，默认 false。会话创建/PATCH 与返回 DTO 增加 `memory_enabled`，同样默认 false，允许运行中关闭供后续请求读取。移动会话重置为 false，分岔不继承开启状态。只有两层同时开启才允许注入；UI 另以 `scoped_memory` 能力位判断当前服务是否接通模型路径，不能仅凭开关已保存宣称模型使用了记忆。

主模型调用前重新读取当前主体/文件夹的手工记录与两层开关，仅改本次 `ModelRequest` 副本；不把记忆参考区写入消息或 checkpoint。`scoped_memory` 随 Runtime 就绪开启，`automatic_memory_events` 仍为 false。关闭/删除不撤回已组装在途请求，也不清除历史回复中已经引用的事实。

文档导入、检索和索引重建接口尚未注册；只有能力闭环时再定义契约，范围见 [记忆与检索](../agent-capabilities/memory/memory-system.md)。

### 3.5 技能与 MCP

运行清单与个人配置分开：

```text
GET /workspaces/{workspace_id}/skills
GET /workspaces/{workspace_id}/mcp-servers
GET /workspaces/{workspace_id}/extension-settings
PUT /workspaces/{workspace_id}/extension-settings
POST /workspaces/{workspace_id}/extension-settings/apply
```

`skills` 返回实际启用的声明式技能元数据，`mcp-servers` 返回实际加载的 stdio Server 状态和工具名，不把期望启用当作已经连接。

三个 `extension-settings` 接口只允许个人工作区 owner（Core 同时要求 `workspace_id=personal`）。GET/PUT/apply 返回同一个快照：`config / active_config / applied_revision / reload_required / reloading / runtime_ready / servers / skills / skill_errors / extension_error`。其中 skills 包括停用的已扫描元数据；config 是期望配置，active_config 仅含上次生效配置的 `revision / mcp_servers`，不重复返回 Skill 正文；extension_error 提示应用或资源清理失败。应用新版本要求 Skill 扫描成功且所有启用 MCP 已连接；已应用的 revision 不保证连接持续健康，仍须检查逐项状态。`runtime_ready=false` 时不宣称聊天链可运行。

PUT 完整替换 `config`（2 MiB 上限）：`revision / mcp_servers / skill_directories / skills / disabled_skills`，校验通过后递增 revision，写现有 `workspace_settings` 并记审计，不操作运行中的进程。MCP 项包括 `namespace / transport=stdio / command / args / cwd / enabled / env_allowlist / secret_env / enabled_tools / disabled_tools / result_adapter / request_timeout_seconds`；托管 Skill 项包括 `name / content / enabled`，每个 Markdown 最大 256 KiB。只接受声明字段，不接受原始 env/secret 值。

`request_timeout_seconds` 默认 30，允许 1～660 秒，只控制工具调用；资源导入仍限 30 秒。修改后须应用扩展配置，不会热改活动调用。

`result_adapter` 为 `none | antenna_design`，缺省为 `none`。显式选择天线适配后，`antenna.solve / get_result / render` 使用 v1 计算回执，`antenna.design / export / get_delivery` 使用候选包回执（当前 v2，兼容读取 v1）。现有审批通过后，宿主按回执类型校验 schema_version 和调用身份，通过同一个 SDK 连接读取固定资源，再写入当前对话目录；不接受任意 URL，不改变 MCP 风险等级。事件信封的 schema_version 独立于天线回执版本，不能一并升级。

投影沿用 `tool.completed.data`：计算结果使用 `antenna_result`，候选包使用 `antenna_delivery`；共享 `output_directory / images / files / import_complete / import_warning / import_warning_code / import_warning_params`。图片复用 `ImageReference`，文件下载沿用既有鉴权接口，ZIP 不在宿主解压。导入失败保留计算或交付原件，不自动重算；无效回执返回 `ANTENNA_RESULT_INVALID`，不读取文件。

`optimize / get_optimization / evaluate / plan_design` 另有紧凑的模型侧结果，保留决策所需的指标、停止原因和预算，不把重复 JSON、原始数组或图片字节塞进 ToolMessage；完整回执仍可供界面查看。配置仍走 save/apply 与上次生效配置机制，不新增 HTTP 资源代理。资源、进度与取消边界见 [MCP 集成](../agent-capabilities/mcp/mcp-integration.md#31-天线结果资源适配)。

apply 默认接受 `{"revision": <保存后的版本>}`，应用新配置。API 按 `secret_env` 从现有密钥链读取引用 `mcp.<namespace>.<ENV_NAME>`，在进程内传递凭证给 Core。Core 拒绝旧版本、重复应用或仍有运行/待审批/待回答的任务（409）；无效配置 400，非 owner 403。

同一接口接受 `{"revision": <active_config.revision>, "restore_only": true}`，只恢复已生效配置中缺失的 MCP 连接、复用健康连接及已加载 Skill；允许等待审批/回答，不允许活动运行，不应用尚未生效的编辑。缺凭证或连接失败按服务器报告，已恢复服务保留可用；返回 200 不代表全部恢复。新聊天可尝试恢复可选扩展，部分失败不阻断聊天；文件任务恢复、回答提问及原生工具批准不启动无关 MCP，也不以所有启用连接恢复为前置条件。MCP 批准仅要求目标服务与确切工具可用，失败不消费决定。checkpoint、审批 hash/version 和每次工具权限仍由 Runtime 校验，后续调用不可用工具不会因此被放行。拒绝审批无需恢复 MCP 连接。

配置或进程异常有逐项状态，不能用 HTTP 200 推断全部连接成功；基础 Runtime 或新配置准备失败返回错误并保留原配置、聊天入口及健康连接，只关闭本次新建资源。准备成功再释放被替换的旧资源。Core 持有应用任务到清理完成，调用方断线不提前开放新运行；请求超时后先 GET 查看 reloading/结果，不盲目重发 apply。

凭证沿用 3.6 的 POST/PUT/DELETE 接口；值不进入扩展配置/GET/审计。更新/删除凭证不改活动进程环境，下次显式 apply 或重建缺失连接时生效；restore_only 不替换健康连接的凭证。复用连接的内存凭证快照取自该 client 实际启动环境，不把并发请求读到的新密钥误记为已生效；显式 apply 同样按实际环境比较，凭证变化时准备替换连接。卸载配置不删除程序、目录或凭证。当前只支持本地 stdio 与 Markdown/目录技能，不是技能包安装、版本授权或 Grant 平台，扩展边界见 [技能](../agent-capabilities/skills/skill-lifecycle.md)。

配置保存/应用、失败保留健康版本、Skill 读取及 Core 重启加载生效配置已通过真实进程验收；凭证重启/轮换、并发恢复/应用中断、扫描故障及设置 GUI 全流程不在该通过范围，见 [当前验收状态](../project/Roadmap.md#当前验收状态)。

### 3.6 设置、模型和凭证引用

```text
GET   /workspaces/{workspace_id}/settings
PATCH /workspaces/{workspace_id}/settings
GET   /workspaces/{workspace_id}/tool-settings
PATCH /workspaces/{workspace_id}/tool-settings
GET   /workspaces/{workspace_id}/settings/safety
PATCH /workspaces/{workspace_id}/settings/safety
GET   /workspaces/{workspace_id}/models
GET   /workspaces/{workspace_id}/models/{model_id}/thinking
GET   /workspaces/{workspace_id}/models/{model_id}/context
POST  /workspaces/{workspace_id}/models
PUT   /workspaces/{workspace_id}/models/{model_id}
DELETE /workspaces/{workspace_id}/models/{model_id}
POST  /workspaces/{workspace_id}/models/{model_id}/default
POST  /workspaces/{workspace_id}/models/actions/validate
POST  /workspaces/{workspace_id}/models/actions/discover
POST  /workspaces/{workspace_id}/models/actions/batch
GET   /workspaces/{workspace_id}/credential-references
POST  /workspaces/{workspace_id}/credential-references
PUT   /workspaces/{workspace_id}/credential-references/{credential_id}
DELETE /workspaces/{workspace_id}/credential-references/{credential_id}
GET   /workspaces/{workspace_id}/preferences
PATCH /workspaces/{workspace_id}/preferences
GET   /workspaces/{workspace_id}/runtime-status
```

`tool-settings` 仅工作区所有者可读写。GET 返回实际注册工具的 `name / description / source /
enabled / configurable / default_enabled`；PATCH 请求为 `{"enabled":{"web_search":true,"sleep":false}}`，
按工具名合并选择并返回完整目录。未知工具、非布尔值和尝试开启启动配置已停用的工具均被拒绝。
选择保存到既有 Workspace 设置，不修改 YAML、不授予权限、不启动新 MCP 服务；可见性与审批是独立边界。
新增启用项在新任务/恢复生效，停用限制后续调用，不撤销已开始的副作用。

`GET/PATCH settings/safety` 管理提示词安全审查配置，仅工作区所有者可读写。请求与响应使用同一结构：

```json
{
  "enabled": false,
  "request_format": "jev_like",
  "jev_layout": "message",
  "state": "",
  "decision_base_url": "",
  "decision_model": "",
  "credential_configured": false,
  "trusted_model_ids": [],
  "risk_threshold": 75
}
```

`request_format` 只接受 `jev_like`、`laya`、`openai`。`jev_like` 覆盖 jev 类格式及其开源实现 kev，不单独枚举 kev；`openai` 为 OpenAI 兼容 API 的结构化输出模式。`jev_layout` 只接受 `inline` 与 `message`（仅对 `jev_like` 生效，其余格式忽略该字段），非法值返回 `SAFETY_LAYOUT_UNSUPPORTED`，与 `SAFETY_FORMAT_UNSUPPORTED` 同为 400 专用错误码。审查请求由 `state` 和 `question` 两部分组成：`state` 使用设置中的自定义文本，留空时采用请求构造库的默认值（`jev_like` / `laya` 留空省略附加字段，见下；`jev_like` 的 `inline` 布局则用内置分类指令）；`question` 由该库根据审查输入生成，不在此处配置。

`risk_threshold` 为 0–100 整数；`trusted_model_ids` 最多 32 个，匹配当前实际调用的模型 ID，不匹配默认模型设置。`enabled: true` 时必须提供 `decision_base_url` 与 `decision_model`。当前 PATCH 实现会补齐 DTO 默认值，前端须先读取、再提交完整配置，以保留其他字段；未知字段被拒绝。格式不在枚举内时返回 `SAFETY_FORMAT_UNSUPPORTED`，阈值或白名单越界返回 400 Validation 错误。

对于 `jev_like`/KEV，请求布局由 `jev_layout` 决定：`message`（默认）把待审查增量
放入 `state.message`，`state` 设置非空时作为 `state.guidance` 附加，instructions
引用两者（与 `laya` 同布局，见下）；`inline` 把增量连同 Model/Task/Step 元数据内嵌
`questions.prompt_safety.instructions`，`state` 作为静态分类指令（留空用库内置指令，
自定义整体替换）。两种布局的审查请求都使用 `safe` 和 `dangerous` 两个分类。KEV 返回的
`probabilities` 中与 `legend` 值 `dangerous` 对应的概率会乘以 100，作为事件和
运行时使用的 `risk_score`；返回体中的 `score` 不作为风险值读取。因而
`risk_threshold` 仍保持 0–100 配置语义，`risk_score > risk_threshold` 时拦截。
布局选择依据（0.8B 与 4B 的实测对比）见
[提示词安全审查配置指南](../Prompt-security/configuration.md#2-jev_like含开源实现-kev)。

对于 `laya`，审查请求发送到 `{decision_base_url}/predict`（不以 `/predict` 结尾时
自动补后缀）。待审查增量放入 `state.message`；`state` 设置非空时作为 `state.guidance`
附加，instructions 引用两者。`questions.prompt_safety` 固定为 `score` 类型、criteria
为 `safe`/`dangerous`，响应按 `legend` 对应的 `probabilities[dangerous]` 乘以 100 形成
`risk_score`，阈值语义与 `jev_like` 相同。`decision_model` 只接受 `english` 与
`typed-decisions`（留空默认 `typed-decisions`），其余值在请求构造时失败并按审查失败
保守拦截；`multilingual` 因实测反向误判被排除，审查请求必须显式指定检查点，不使用
服务端自动路由。语言覆盖矩阵与已知限制（JA 漏拦、AR 误拦、HI 触发服务端崩溃）见
[提示词安全审查计划](../Prompt-security/plan.md)的验证结果。

对于 `openai`，审查请求发送到 `{decision_base_url}/chat/completions`：`state` 作为
system 消息、`question` 作为 user 消息，`response_format` 优先使用 `json_schema` 约束
`{"risk_probability": <0–1 数值>, "reason": <字符串>}`。服务以 400/404/415/422 拒绝该模式时，
自动降级为 `json_object` 并重试一次，schema 说明已写入 system 消息。响应读取
`choices[0].message.content` 中的 JSON：`risk_probability` 缺失或不在 0–1、`reason`
缺失、或内容不是可解析 JSON 时，按审查失败保守拦截；解析后的概率乘以 100 形成
`risk_score`，再按 `risk_score > risk_threshold` 拦截。
请求固定提供 `max_tokens: 1024`，为支持隐藏推理字段的模型预留推理与最终 JSON
的共同输出空间；不能以 `reasoning_content` 替代最终 `content`，最终内容为空时仍按
审查失败保守拦截。

已用 LM Studio `http://127.0.0.1:1234/v1` 的
`google/gemma-4-12b-qat` 完成真实联调：良性提示返回
`risk_probability: 0.0` 并转换为 `risk_score: 0`、放行；请求读取并外发
API 密钥的注入提示返回 `risk_probability: 1.0` 并转换为 `risk_score: 100`、
拦截。两次请求均由 `json_schema` 直接返回，未触发 `json_object` 降级。

`api_key` 只出现在 PATCH 请求中，按 `safety.decision_model` 凭证引用保存；GET 只返回 `credential_configured`，不回传密钥。密钥留空保留既有凭证，保存或离开面板后清除输入中的密钥。更换服务地址时须重新提供密钥或移除旧凭证，避免发送到其他服务。

配置按工作区持久化，不随会话变化。设置页「安全」位于「权限」之后，支持配置读取、保存、审查凭证移除，以及 `?tab=safety` 和字段搜索定位。

安全确认的提问选项含独立的 `value` 与 `label`：新记录提交 `safety.continue / safety.stop`，运行时只用标识决定继续或停止，GUI 按标识翻译标签。回答仍走原提问 API、版本检查和 LangGraph 恢复；未知选项拒绝提交。普通模型提问继续允许自由文本。升级前没有 `value` 的安全确认保留原记录，仅在读取此类旧回执时兼容原确切回答；不改写 checkpoint。历史消息保存可读标签，提问回执保存决策标识。

`capabilities.safety_review` 表示配置与运行时审查能力可用。启用后，主模型每次调用前审查新增的人类提示词；可信模型跳过审查，审查失败保守拦截，高于阈值时复用结构化提问/恢复链路等待人工决定。接口和 GUI 测试已完成，KEV 与 LAYA HTTP 联调已完成；完整端到端验收仍待执行。验收记录见 [交付路线与状态](../project/Roadmap.md)。

读取凭证只返回引用和元数据，不返回敏感凭证值。

**模型窗口查询。** `models/{model_id}/context` 由 API 恢复当前配置的凭证，再调用
`AgentCore.get_chat_model_context`。与执行路径复用同一个容量解析函数和 Core 所有的模型客户端；
不生成消息、不创建任务、不自动加载模型、不修改配置。返回 `model_id / model_key / model_name`、
`model_context_window / capacity_source / capacity_status / capacity_checked_at`，以及
`input_budget_tokens / output_reserved_tokens / safety_margin_tokens / compaction_trigger_tokens`。
窗口未知时，窗口、输入预算和容量压缩阈值均为 null；不把旧的 32000 产品默认值冒充容量。
容量已知时，预算为窗口减输出预留和安全余量，不再对所有模型套用 32000 上限。
此接口不返回对话用量，客户端必须从当前会话的执行回执取得用量，不能借用其他会话。
本次接口和恢复链路为源码接入状态，未运行测试或真实 Provider 验收。

**会话思考控制。** `models/{model_id}/thinking` 返回 `kind: effort | toggle | unavailable`、
`options: string[]`、`default`、`source` 和 `reason`；列表由 Core 确认，前端不按模型名称猜测。
`default` 为空表示默认值未知，不等于关闭思考。查询只读取能力资料，不发生成请求、不自动加载模型。
API 在读取能力、保存显式思考设置之前，沿用 OS 凭证存储恢复所选模型的 Core 内存凭证，
不启动 MCP/Skill，不返回 Secret。

创建/PATCH 会话接受 `thinking: string`；PATCH 省略保持原值，`""` 恢复服务默认（不发送推理参数）。
模型变更且未指定 thinking 时自动清空；非法档位由 Core 拒绝，活跃或等待批准的任务不允许更改。
GET/List 返回相同字段；客户端仍将历史数据的缺失值按 `""` 解读。
旧 `response_mode` 暂留作兼容字段，但不再改变输出 token 预算，也不被转换为推理强度。

当前控制范围：OpenAI 使用原生 SDK `profile.reasoning_effort_levels`（原始 GPT-5 使用官方档位修正），
Claude 使用已确认的自适应思考型号及官方 effort 档位，Ollama 查询 `/api/show.thinking`，
LM Studio 仅在对应 `models.routes` 显式配置 `metadata_api: lmstudio` 后查询 `/api/v1/models.capabilities.reasoning`，不通过端口或本地地址推断。OpenAI/LM Studio 的强度参数复用 SDK Responses，
Claude 使用 adaptive + effort，Ollama 使用原生 reasoning/think；不自建 Provider 协议。
LM Studio 仅原生 API 暴露的 on/off 尚不映射到兼容接口，返回 `native_control_only`；
未知兼容服务、旧版缺少元数据或未接入的预算型控制返回不可调节，保持服务默认。

模型目录支持多选及手动追加 ID；每个选中模型仍为独立配置。设置页一次提交 `POST models/actions/batch`，不再逐项复制凭证、保存和删除。请求包含 `operation_id`（一次编辑的 UUID）、`expected_revision`（打开编辑页时的目录版本）、`profiles`、`removed_ids` 和可选服务 `api_key`。新配置的 `id` 留空，由后端根据编辑标识及模型 ID 分配稳定标识；已有配置保留原 ID、默认项及独立生成设置。

后端复用现有模型变更锁，先校验整批输入和容量，再保存、最后删除；目录版本不符返回 `409 MODEL_CATALOG_CHANGED`，不写入。GET models、设置默认项及批量响应都携带 `revision`。新增项的密钥只从本批涉及的同工作区、同协议、同规范化地址配置复用，已有独立凭证不被空输入覆盖；明确填写新密钥则应用到本批模型。地址/协议改变仍先撤销旧凭证。

`reuse_credentials` 表示本次编辑是否要求沿用已配置服务密钥，默认 true；无密钥服务由设置页明确提交 false。要求复用但无法取得来源密钥时，在任何写入/删除之前返回错误，不能静默创建无密钥配置再移除旧来源。页面只提交此意图，不选择或复制各模型的凭证；协议/地址改变后不能沿用原服务密钥。

SQLite 与 OS 密钥链不具备共同事务。可预期的部分失败返回实际目录、`saved_ids`、`removed_ids` 和结构化 `error`；成功时 error 为 null。未知程序错误交给 API 安全诊断边界，前端重新读取实际状态但不自动重试写入。只有明确批量回执才能推进草稿版本；丢失响应或其他窗口写入后发生版本冲突，须重新打开编辑页。仍保留最多 32 项及先保存后删除语义：已满时需先删除再新增，不能假装可原子替换满额目录。

旧单模型保存 API 保留 `credential_source_id` 兼容入口；批量入口不接受逐项凭证来源，统一由后端选择。密钥仍按目标模型 ID 存储，不回传前端，也不进入目录版本、普通配置或审计正文。

模型 API 管理工作区的聊天模型配置集合，并保证最多一个默认模型。`models/actions/validate` 使用提交的草稿配置做一次最小模型调用，不保存草稿。`models/actions/discover` 根据模型提供方与基础地址查询服务端模型目录：OpenAI、Anthropic 和兼容端点使用 `/models`，Ollama 使用 `/api/tags`。

连接验证及目录发现仅转换明确的配置、连接、认证及服务响应异常；未知程序错误不再冒充 `ok=false` 或目录不可用，交给统一 API 边界，日志仅记录异常类型与代码位置，不记录第三方异常正文。天线 MCP 资源读取也区分无效/损坏资源与内部错误，并保留脱敏终端边界，避免 MCP SDK 将未知异常原文回传。

目录发现优先使用本次草稿中的 Key。仅当模型提供方与基础地址同已保存配置完全匹配时，才读取该配置的凭证存储引用。结果和敏感凭证都不持久化。目录请求禁用重定向并检查链路本地地址；只有本地端点绕过系统代理，公网兼容服务遵循用户的代理环境。不按端口猜厂商。

设置写入、目录发现、连接验证和凭证管理只允许工作区所有者。模型凭证按模型配置 ID 隔离（`model-profile.{id}`）。保存后，API 层将凭证同步到智能体核心内存；发送消息前，再次从 OS 凭证存储同步当前会话选择的配置。修改模型提供方/基础地址会使旧凭证失效。敏感凭证不进入领域数据库或事件。

联网搜索凭证使用 `tavily`、`google` 引用名。API 层保存或删除引用时直接同步智能体核心，并在每次消息任务开始前重新同步，以覆盖核心服务重启场景。搜索失败事件只携带稳定错误码、用户提示和设置路由，不携带敏感凭证。

`GET/PATCH /workspaces/{workspace_id}/permissions` 管理默认执行模式：`read_only / workspace / danger_full_access`；不向普通客户端暴露逐工具风险矩阵。`PATCH conversations/{conversation_id}` 可修改当前会话的 `model_id`、`thinking` 和 `permission_mode`（旧 `response_mode` 仅保留兼容）。服务端验证模型属于当前工作区；L1–L5 风险、参数升级和 hard-deny 由 ToolPolicy 内部维护。

2026-10-11 兼容约定：旧 `full_access` 请求仍被接受，但规范化为 `workspace_auto`，保留原工作区限制而非自动提权；历史会话、YAML/环境变量与默认设置同样处理。前端要把 `workspace_auto` 显示为兼容的“旧版受限自动”，不作为新的完全访问档位。真正完全访问须显式提交 `{"permission_mode":"danger_full_access"}` 或 `{"default_mode":"danger_full_access"}`，界面接入时应明确告知将取消工作区沙箱与逐项审批。设置默认仅影响后续新会话。

创建/PATCH 会话还接受 `max_subagent_calls`（0–32，默认 0 关闭）和 `max_concurrent_subagents`（1–8，默认 1；启用时不能超过前者）。省略字段保留旧值，`0` 不当作省略。GET/List 返回会话设置及真实 0 值；客户端仍容忍旧数据字段缺失。Core 在持久化事务内校验组合；新任务原子快照限额，运行中修改仅影响后续回合。`capabilities.subagents` 表示支持此接口/执行入口，不绕过工具停用设置和共享模型调用预算。执行限制见 [运行时设计第 7 节](AgentRuntime.md#7-技能子任务与工作流边界)。

`capabilities.subagent_model_selection` 表示支持多模型子 Agent。创建/PATCH 会话接受 `subagent_model_ids: string[]`，最多 32 个、不重复、必须是同工作区已配置模型 ID。PATCH 省略保留旧值，`[]` 清空额外选择（仍可跟随父模型）；GET/List 返回相同字段，空值返回 `[]`。PATCH 使用 Pydantic 的字段提供状态区分省略与清空。Task 返回准入时保存的 `subagent_model_ids`，供重启后的凭证准备使用；不是另一份执行状态。

权限设置请求与响应仅包含 `default_mode`。REST 写入拒绝未知字段（包括旧的 `tool_levels / grants`），不再静默接受无效设置。旧产品库无需删表，旧风险配置不生效，下一次保存只写默认档位。

审批 `deny` 只拒绝当前操作，不取消整轮或关闭其他待审批操作。API 尽力准备模型凭证供后续续接，但凭证不可用不能阻止拒绝保存，也不为被拒绝的操作启动 MCP。决定仍验证主体、任务状态、参数 hash、版本及过期时间并保存审计；后续续接失败通过 `task.failed` 报告。`allow` 继续使用凭证准备和原生 HITL 恢复路径。授权有期限且可撤销，不存在永久会话授权或独立审核 Agent；批次续接语义见 3.8。

`preferences` 是当前主体在工作区中的用户级偏好。`assistant_name`（1–32 个字符）用于界面称呼，只修改当前个人主体的显示偏好，不改变工作区名称。

`runtime-status` 是工作区所有者可见的只读投影，返回规范化工作区根目录及来源、单一 `backend` 服务状态和版本、即时生效项与需重启项。它不修改部署配置，也不声称能够检测运行期间手工编辑的外部 YAML。

响应包含 `sandbox: {platform, backend, status, reason, network_isolation, cpu_memory_limits, hint}`，另含 `scope / other_boundaries / per_process_rlimits_available / captured_log_limit_bytes / disk_quota`。
这是宿主能力而非当前对话的授权结果：Linux 通过带断网、禁用嵌套 user namespace 和去除 capabilities 的最小 bubblewrap 启动探测才返回 `available`；探测在线程中执行、单次最多 2 秒，结果在后端进程内缓存。修改系统配置后需重启后端重新探测。它不保证特定工作区挂载成功，也不代表完全访问任务启用了隔离。
Linux 区分 `bubblewrap_not_installed / bubblewrap_incompatible / bubblewrap_permission_denied / bubblewrap_launch_failed / bubblewrap_probe_timeout / bubblewrap_probe_failed`；提示为固定安全文本，不回显原始进程 stderr。其他平台未接入返回 `native_backend_not_integrated`。Windows 为 `backend: srt-windows, experimental: true`：未启用/缺依赖返回 `unavailable` 与 `windows_sandbox_disabled / dependency_unavailable`；配置启用且本地依赖存在返回 `unverified / verification_on_execute`，不冒充 `available`。Windows 设置查询不启动 sandbox 进程或修改 ACL；账户、WFP 与路径授权由实际命令启动时的官方 SDK 校验。增加 `dns_isolation=false / interactive_stdin=false / read_boundary=dedicated_account_acl_and_explicit_denies`。Windows 的 `network_isolation=false` 表示状态查询未验证，不是命令可绕过网络策略。

`scope=command_processes`，不是整个 Agent 的安全状态。`other_boundaries.file_search` 说明跨平台受控搜索，其他项说明进程内文件策略、MCP 宿主进程与 Chromium 原生沙箱；这些说明不代表对应依赖已安装或启动成功。Windows 沙箱进程日志/停止沿用原 API，进程快照的 `stdin_available=false`；不得将内部停止管道投影为用户 stdin。

响应另含 `file_search: {status, backend: "ripgrep", requires_process_sandbox: false, verified: false, reason, hint}`。发现工作区之外的可信可执行文件时 `status=available` 并附 `executable`，否则 `status=unavailable, reason=trusted_ripgrep_not_found`。此项仅做 PATH/文件检查，不启动搜索进程；`available` 表示依赖已发现，不是搜索执行验收通过。与 `sandbox.status` 独立，受限命令不可用不能解释为普通文件搜索不可用。API 启动日志也报告这两项。

命令工具失败结果可附 `data.sandbox_reason`，例如 `executable_outside_sandbox` 表示程序已安装但不在命令可读范围，区别于 `COMMAND_UNAVAILABLE`。已执行命令的结果附 `network_authorized`，表示本次策略是否授权联网，不表示网络连通性或请求已成功。Linux 工具链只读目录与缓存策略来自启动 YAML，不是模型参数、客户端授权或新的设置写入接口；宿主能力探测不验证每个工作区的这些目录。

`per_process_rlimits_available` 仅表示 Linux 系统位置存在 prlimit；是否配置见启动 YAML，实际应用失败时命令不会执行。它不是 cgroup/Job Object 总资源配额，因此原 `cpu_memory_limits` 继续为 `false`。`captured_log_limit_bytes=8388608` 表示单次命令捕获日志总量上限，`disk_quota=false` 明确没有工作区磁盘配额。工具结果新增 `output_limit_exceeded / output_bytes_limit / process_limits`；`process_limits` 的零值表示未额外施加该限制，超量输出返回 `COMMAND_OUTPUT_LIMIT`。旧客户端可忽略新增字段；本批未修改设置界面。

#### 本地用量与可查询额度（2026-10-13）

```text
GET /workspaces/{workspace_id}/usage?days=7
GET /workspaces/{workspace_id}/models/{model_id}/balance
```

两个入口均要求工作区 owner，并沿用本机访问边界。`usage` 默认取最近 7 个本机日历日，
`days=1/7/30` 分别为今天、最近 7 天、最近 30 天，`days=0` 为全部已保存记录；允许范围 0–365。
时间边界以后端所在机器的时区计算，响应附带 UTC `since / until` 和本机日期 `start_date / end_date`。

响应包含 `profiles / models / totals / daily / unattributed_calls`。公开 profiles 只含
`id / name / model / provider / base_url / quota_service`；地址移除 URL 凭证、查询参数和 fragment。
models 按历史调用的 `model_id / provider / base_url / model` 聚合，daily 按调用记录日期聚合。
每组 metrics 为：

```text
calls / failed_calls / reported_calls / cache_reported_calls
tokens: {input_tokens?, output_tokens?, total_tokens?, cache_read_tokens?,
         cache_creation_tokens?, cache_miss_tokens?, reasoning_tokens?}
token_reports: {<counter>: <实际报告该字段的调用数>}
cache_pct: number | null
```

数据来自现有 `task_events` 中的模型开始、失败、真实用量回执及同调用 ID 的上下文身份信息，
按 `(task_id, call_id)` 合并重复回执；没有调用 ID 的旧记录按 event ID 独立保存，不猜测去重。
只有服务明确报告的非负整数计数才累计，字段缺失保留缺失，不写零、不从正文估算。
缓存和推理 Token 不额外计入总 Token。只有全部调用都报告输入和合法缓存读取计数且分母非零，
才给出缓存命中率；不完整时返回 null。请求进行中或未返回用量的调用仍计入 calls。

主模型、摘要及子 Agent 的后续回执记录实际模型配置身份；旧记录可通过相同 call ID 的
context 事件补全，无法确定的单独列为未归属记录，不按当前模型设置回填历史。
配置已改模型/地址或已删除时，旧路由数据作为历史配置保留。查询使用独立只读 SQLite 连接
和工作线程，不新增统计数据库、价格表或运行时调度。
统计仅覆盖**仍保存在本机的调用记录**；删除对话会级联删除其事件，不能恢复已删除记录的用量，
也不表示供应商账户的全量账单。

`quota_service` 目前仅支持 `deepseek / openrouter / null`，按已核实的官方 HTTPS 地址识别，
不对未知中转站猜测接口。前端只在非 null 时提供手动查询入口，访问统计页不自动向供应商发请求。
`balance` 使用所选配置的现有 OS 密钥链凭证，无凭证返回 409、不支持返回 404；上游鉴权失败或
异常返回脱敏错误，不回传 Key 或上游原始响应。官方查询禁用重定向，限制超时和响应体积。

- DeepSeek：调用 [GET `/user/balance`](https://api-docs.deepseek.com/api/get-user-balance/)，
  只显示服务明确返回的 `total_balance`，`kind=balance`。
- OpenRouter：调用 [GET `/api/v1/key`](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key)，
  只显示 `limit_remaining`，`kind=key_limit`，不是账户余额。不调用要求 management key 的 credits 接口。

额度响应为 `{kind, values: [{currency, amount}], checked_at}`；金额用十进制字符串，未提供金额时
values 为空，不算成零或无限。多配置不相加余额，前端可选择本次查询所使用的模型配置。
当前为源码接入状态，未运行测试、构建或真实供应商查询。

### 3.7 工作区文件与变更

```text
GET /workspaces/{workspace_id}/files?path={relative_path}
GET /workspaces/{workspace_id}/files/content?path={relative_file}
POST /workspaces/{workspace_id}/files?path={relative_parent}&folder_id={registered_folder_id}
POST /workspaces/{workspace_id}/files/open-folder?path={relative_folder}&folder_id={registered_folder_id}
GET /workspaces/{workspace_id}/changes
GET /workspaces/{workspace_id}/changes/diff?path={relative_file}
```

GET 接口是受工作区根目录约束的只读投影，拒绝绝对路径、`..` 穿越和逃逸根目录的符号链接。预览限制为 512 KiB，变更列表最多 200 项。差异在用户选中文件后懒加载，限制为 256 KiB；响应同时返回截断状态。`changes` 在非 Git 工作区返回 `git: false`，不执行写入或暂存操作。文件查询与操作均按 `folder_id` 解析已登记的目录，缺省为默认工作区，不接受调用方指定任意根目录。

POST 接口为用户显式文件操作，限工作区所有者；不作为 Agent 工具、审批续接或自动执行入口：

- `POST /files` 正文为 `{"name":"notes.md","type":"file"}` 或 `{"name":"notes","type":"directory"}`。只在已存在的相对父目录创建一个空文件或文件夹，不递归创建路径、不覆盖、不写正文。成功返回 201 与 `name / path / type / size / modified_at` 文件树条目；同名返回 409 `ALREADY_EXISTS`，非法名称 / 路径返回 400，权限失败返回 403。拒绝路径分隔符、控制字符、保留名称以及 `.git` 路径。复用系统相对目录句柄创建能力，防止校验后替换符号链接或 junction 令写入逃逸，由 POSIX 目录句柄或 Windows 系统 API 实现。
- `POST /files/open-folder` 另外要求直接连接地址为本机 loopback。只可打开工作区内已存在目录；API 以固定文件管理器程序加路径参数启动，不经 shell。快速成功返回 204；启动器仍在运行时返回 202 `{"launched":true}`，仅表示启动已接受，不保证窗口已打开；启动或快速退出失败返回 503 `OPEN_FAILED`。Electron 优先使用已有 `openPath` IPC，不调用此接口。

这两项源码已接线，尚未执行运行验收；使用新接口需重启 API。

### 3.8 GUI 交互与执行检查接口（2026-10-10）

本节说明任务恢复、执行检查和运行中交互的现有接口。界面行为见 [前端设计](FrontendDesign.md)，测试范围见 [交付路线与状态](../project/Roadmap.md)。

以下路径均以 `/api/v1/workspaces/{workspace_id}` 为前缀，沿用现有工作区访问校验：

| 方法与路径 | 用途与响应 |
|---|---|
| `GET /capabilities` | `{capabilities: {...}}`：当前 Core Runtime 支持的交互能力；缺失键按不支持处理 |
| `GET /tasks/{task_id}/inspection` | `task / tool_calls / approvals / projection / last_sequence`，供任务与工具详情抽屉使用 |
| `GET /questions?conversation_id=...` | `{questions: [...]}`，仍等待回答的问题 |
| `POST /questions/{question_id}/answer` | `{"answer":"用户选择或自由输入","version":1}`；接受后返回 202 |
| `GET /tasks/{task_id}/inputs` | `{inputs: [...]}`，该任务的插话及消费状态 |
| `POST /tasks/{task_id}/inputs` | `{"content":"先不要修改，改为说明方案"}`；必须带 `Idempotency-Key`，返回 202 与 input 记录 |
| `GET /jobs?conversation_id=...` | `{jobs: [...]}`，真实命令进程与已保存的结束结果，最多 100 条命令调用范围内的记录 |
| `POST /jobs/{job_id}/stop` | 终止本 Core 持有的进程句柄并等待退出，返回 204；不是取消整个 Agent 任务 |
| `POST /jobs/{job_id}/input` | `{"text":"一行输入\n","eof":false}`；向获批的交互式命令发送最多 4096 字节，或以空 text/eof=true 关闭输入；确认后返回 204，不自动重试 |
| `POST /tasks/{task_id}/resume` | 工作区所有者显式恢复被进程中断的文件任务，正文 `{"version":当前任务版本}`；确认接受后返回 202，不代表完成 |

**能力边界。** `approval_note / approval_detail / task_inspection / file_change_events / process_list / process_stop` 为本批支持项。
2026-10-11 增加 `process_input`，每个 job 返回 `stdin_available`。GUI 必须同时检查进程 live、stdin_available 和能力标志，不能把历史记录当成可操作进程。输入正文不保存到审计或对话，响应丢失不代表未送达；进程自行回显的输出仍可能包含输入。当前是有超时的标准管道，不提供 PTY 或断线后恢复进程。
`structured_questions / task_input / structured_plan / context_estimate` 在 Core 的唯一 LangGraph Runtime 就绪时为 true；关闭或未就绪时不对外宣称可执行。这些标志表示后端能力，工具仍可能被工作区配置停用。

`scoped_memory` 同样跟随 Runtime 就绪，表示提供两层显式开关控制的请求记忆投影，不代表某个文件夹/对话已开启，也不代表已实现自动提取。
`session_approval / task_approval / approval_authorization_revocation / permission_boundary_v2 / conversation_fork` 在 Runtime 就绪时为 true，表示支持上述新接口/语义，不代表前端已完成联调或主机沙箱可用。`auto_review / model_context_percentage / turn_rollback / automatic_memory_events` 为 false。回合回滚和审核 Agent 不在本批范围。

**显式任务恢复。** `task_resume` 在 Runtime 就绪时为 true，表示提供受限恢复入口，不表示所有 interrupted 任务均可重放。API 先按现有凭证入口补齐 Core 的进程内模型密钥，再直接调用 `AgentCore.resume_task(workspace_id, task_id, principal_id, version)`。Core 只接受所有者、最新版本、最新会话回合、无活跃运行、未归档且存在未完成 checkpoint 的 `interrupted` 任务；等待审批的任务继续使用 Approval API，不走这个接口。旧任务无 checkpoint 也按不可恢复处理；状态/版本/checkpoint 不满足返回 409，越权返回 403，任务不在工作区内返回 404。

首版恢复只接受保留的 checkpoint 中工具调用均为 `file_read / file_write` 的任务，以及这两种直接 `/tool` 命令。保留历史若包含旧回合的其他工具也会保守拒绝；命令、Browser、MCP 等不自动重放。恢复不等于重新授权：未批准的调用仍暂停等待，已批准的调用仍核对当前策略及审批 hash/version。文件内容匹配时可零写入核对；不同内容可能被再次覆盖，接口不提供外部编辑冲突保护、回滚或 exactly-once 保证。调用者应先检查目标文件，确认重放意图。

接受事件 `task.resumed` 使用既有 AgentEvent DTO 和持久 event_id/sequence/occurred_at，payload 包含 `execution_resumed: true` 与本次 `trace_id`。API 收到该请求的确认才返回 202，后续事件透传原 WS 通道；并发拒绝请求不会冒领其他恢复请求的事件。执行与订阅分离：断线不取消；用户停止与 Core shutdown 都取消并等待同一根任务。UI 不接触 checkpoint/continuation，不新增前端状态机。验收范围见 [交付路线与状态](../project/Roadmap.md)。

**任务详情。** `tool_calls` 返回工具名、参数、结果、状态和时间；不暴露 checkpoint、运行时对象或 `continuation_json`。
`projection` 包含 `plan / context / changes / summary / questions / usage / truncated`；问题包含 `options` 数组和已保存回答。快照在同一 Repository 锁内读取。最多 50 条工具调用、50 条审批和最近 100 条相关事件；工具结果、审批、文件差异与问题另外共用约 900 KB 的输出预算，过大条目省略并标记 `truncated`。`usage` 独立汇总该任务全部已保存的用量事件，不受最近 100 条活动限制；按 purpose 返回 `calls / failed_calls / reported_calls / tokens`，另有 `compactions`。它仅汇总已有回执，不将缺失字段填零，不计算价格；cached/reasoning 不额外加到总 token。`last_sequence` 是该任务的事件水位，不是整个会话所有任务的完整快照水位。截断时应补读既有事件历史，不能将省略的内容判为不存在。

**结构化提问。** LangGraph 路径注册 `ask_user_question(title, options)`。普通模型提问的选项为 `{label, recommended}`，也允许自由输入。产品返回的选项还可包含独立 `value`（缺省为 null）；安全确认用它保存 `safety.continue / safety.stop`，不能用翻译后的 label 代替决策标识，也不接受未知选择，详见上方安全设置。

提问使用原生 `interrupt / Command(resume=...)`，Repository 保存问题、回答和审计。`question.requested` 携带 `question_id / tool_call_id / title / options / version`；`question.answered` 携带 `question_id / answer / version`。等待期间沿用任务状态 `waiting_approval`，UI 按问题事件/记录区分提问与权限审批，不能将提问渲染成“允许/拒绝”。回答验证工作区、主体、版本及 checkpoint；重复或过期回答返回 409。回答、审计与可见用户历史同事务保存，执行恢复仍看 checkpoint。新增安全决策标识的跨进程恢复尚未验收。

GUI 的收件箱与活动卡片已接入问题选项、自由文本及“回答并继续”。提交当前展示的 version；
成功、冲突或网络失败后重读快照和持久事件，不用 HTTP 202 冒充执行完成，也不自动重复回答。
普通聊天输入不隐式充当问题回答：运行中 Enter 通过 `/tasks/{task_id}/inputs` 引导，
Alt+Enter 显式加入下一回合待发列表；等待审批或提问时只能稍后发送，或就地处理卡片。
驳回理由通过 Approval API 的 note 保存并交给模型。`deny` 只拒绝该操作，`scope` 必须为 `once`；任务继续或等待同批下一项审批，不发布 `task.cancelled`。批次审阅完成后，被拒绝调用通过既有 `tool.failed`（`error_code=APPROVAL_REJECTED`）投影，产品工具状态为 `denied`。续接失败另发 `task.failed`，拒绝决定仍有效；显式 cancel 才停止整轮。
权限策略拒绝或工具未启用时，同样不执行工具，但使用 `tool.failed` 的 `error_code=TOOL_DENIED`、`denied=true`，工具记录为 `denied`，审计为 `tool.denied`；不得与用户驳回审批或工具执行后失败混淆。
阶段 3a 的补充输入、计划恢复与运行抽屉已进行真实 API/Core 验收，见
[UI 设计的阶段 3a 记录](agent-ui-design.md#10-阶段-3a--数据链路)。
该验收不代表审批/回答的跨进程恢复通过；Mock 未实现问题续接，返回空问题列表。

**插话。** 仅接受当前活跃的普通模型任务，不接受 `/tool` 或正在等待交互的任务。content 最多 10000 字符，每任务最多 20 条未消费输入；幂等键最多 128 字节，同键不同内容返回 409。`task.input.queued` 是接收回执；下一模型步骤在原生 middleware 中追加 `HumanMessage`，包含该输入的模型调用成功返回后才保存可见用户消息并发出 `task.input.consumed`。预算拒绝、失败和取消不冒充已消费；Provider 可能已收到但响应丢失的情况仍不能确认，`not_consumed` 不是“服务端一定没收到”的证明。`consumed` 也不保证后续工具或整个任务成功。尚未开始的旧工具提议会收到未执行结果，让模型读取新输入后重新判断；不抢停已经开始的工具或模型流。若当前回合结束前未确认消费，查询状态为 `not_consumed`，不自动新建回合或重发。客户端按 input ID 合并状态，不能因迟到的 queued 回执把 consumed 状态改回 queued。

**计划与上下文。** 计划直接复用 LangChain `TodoListMiddleware / write_todos`；`plan.updated.items` 是 `{content, status}` 数组，status 为 `pending / in_progress / completed`，只作用于当前任务，不是工作区 TODO。`context.updated` 提供 `call_id / model_name / estimated_input_tokens / input_budget_tokens / measurement`；本地估算为 `estimate`，显式超限回执提供的输入计数为 `provider`，沿用 `estimated_input_tokens` 字段兼容现有消费者，不表示计费成功。`model_context_window / context_percent` 在容量已知时有值，否则为 null，绝不把产品预算冒充模型窗口。

正式调用的 `context.updated.memory` 为可选对象：`included_count / candidate_count / ids / estimated_tokens / token_limit`，不含记忆原文。`candidate_count` 是通过两层开关和归属筛选后有界查询的条目数（最多 100），不是总库存；未启用时为 0。`estimated_input_tokens` 已包含参考区，不能再加一次 `memory.estimated_tokens`。压缩前的预检和辅助摘要不提供此字段，不据此覆盖本次正式调用的收据。

容量附加字段：`capacity_source` 包括 `lmstudio.loaded_instance / ollama.loaded_instance / ollama.model_info / model_profile / provider_metadata / provider_error / models.dev / sdk_profile / route_config / route_default / null`，分别表示加载实例、Ollama 模型资料、显式模型覆盖、当前服务资料、拒绝回执、当前路由目录/SDK 资料、YAML 路由默认值、内置路由假定值和未知。目录是资料上限，不冒充实际加载窗口；未知远端路由默认容量为 262,144，`route_default` 对应 `capacity_status=assumed`，不是实测。`capacity_status` 还可为 `known / cached / unknown / model_catalog_unavailable / metadata_unavailable / unsupported_metadata / unauthorized / not_loaded / model_not_found / ambiguous_instance`；`capacity_checked_at` 为 UTC 解析/观测时间。`output_reserved_tokens / safety_margin_tokens / compaction_trigger_tokens` 分别是输出预留、估算余量和自动压缩阈值，均不是已消耗 token。`purpose` 区分 `response / summary`。压缩前和正式调用前均可发布，预检的 `context_*` call ID 不是模型调用次数，只有 `model.call.started` 才代表请求开始；不能对多个 `context.updated` 求和。模型真实计费计数继续通过既有 `model.usage` 提供。

容量未知时不按产品默认预算触发模型压力压缩，也不发布伪造阈值。明确的服务端超限错误可以
触发一次强制原生摘要，再重试一次；只在尚无可见 Token、历史实际缩减且当前输入保留时进行。
摘要使用 LangGraph `TAG_NOSTREAM`，不进入回复正文；公开的 `ExtendedModelResponse / Command`
将替换历史与新回复一起提交 checkpoint，UI 只投影新回复，不重复历史工具操作。
恢复失败、没有可压缩历史、已输出内容或取消时不重复调用、不提交失败摘要。
固定指令和工具定义本身超预算的失败带 `reason=fixed_overhead_exceeded`，显示输入预算不足，
不称为历史摘要失败。现有 `context.max_tokens` 只作未知容量下恢复工作的预算，不能声明服务端窗口。

模型配置使用可选 `context_window`（正的安全整数；省略/0 表示没有模型覆盖），从设置页获取的服务目录按精确模型 ID 保存，经既有 REST / Repository 传递，无数据库增列。换服务或换模型不能沿用另一模型的容量。

Runtime 使用 Core 已拥有的官方 SDK 查询当前服务 `/models`，读取精确模型项的 `context_window / context_length / contextWindow / max_model_len`。LM Studio 加载实例接口只对 `models.routes` 中明确设置 `metadata_api: lmstudio` 的 compatible 路由启用，不凭端口或本地地址探测。models.dev/SDK 资料只适用于已识别的当前路由，不跨厂商匹配模型 ID，也不把目录内 localhost 样例当作本机服务身份；自定义 Anthropic/OpenAI 协议地址不等于官方路由。

无资料的远端接入使用 `models.routes[].default_context_window`，未配置时采用 262,144 的 `assumed` 回退，本地/Ollama 无默认云端回退。输出上限与旧产品预算仍不冒充窗口。持久容量记录使用内部 `capacity_policy / catalog_route`，本次策略升级使旧推断缓存失效，不删除聊天和用量。

`context.updated` 保持 `model_id / model_key`：后者为 `[provider, normalized_base_url, model, context_window_or_null]` 的紧凑 JSON，仅用于配置关联，不含密钥。前端同时匹配配置 ID 与该关联值；旧事件缺少关联值时不把历史容量套到当前配置，新请求发布新的观测。路由配置由现有 YAML 读取，不新增 REST 字段或设置系统。

压缩状态通过 `context.compaction.started / failed / skipped` 与既有 `context.compacted` 发布。started 带 `before_tokens / input_budget_tokens / model_context_window / measurement`；failed 带 `message / error_type / model_context_window` 且保留原历史；skipped 的 `reason=no_compressible_history` 不代表完成压缩。前端按事件投影独立压缩状态，不从普通“思考中”推断；窗口仪表保留 measurement、容量来源、获取状态与观测时间，未知窗口只显示已有 Token 计数/估算、不绘制占用比例。聊天过程与底部显示压缩失败原因，关闭窗口用量显示不隐藏失败反馈。本次前端接入未运行测试或真实服务验收，后端没有手动压缩接口，界面不提供该按钮。

用量汇总中，旧记录若缺少 `call_id` 或调用开始回执，`calls / failed_calls` 返回 null（未知），不能显示为“0 次调用”。已收到的 token 小计和 `reported_calls` 仍可查询，但不是完整账单或调用次数的估计。

压缩触发的 `reason` 区分 `pressure`（模型步骤前）、`input_pressure`（补充输入后的最终请求）和 `provider_overflow`（服务端明确拒绝）。根任务取消时用既有 `context.compaction.skipped`、`reason=cancelled` 结束压缩展示，再发布任务终态；不把取消记作压缩成功。摘要调用也发布 `model.call.completed`、`purpose=summary`，即使服务未返回用量计数。

`context.compacted` 只在 LangGraph 持久上下文确认包含该摘要后发布，摘要模型返回不等于压缩提交。取消恰好发生在提交后时，允许先报告已确认的压缩、再报告任务取消；无法确认提交时发布 skipped/failed（失败原因 `checkpoint_unconfirmed`），保留原始聊天记录。checkpoint 的完整内容不对外推送。

MCP 失败沿用工具事件：`MCP_UNAVAILABLE` 表示本次请求未发送；`MCP_OUTCOME_UNKNOWN` 表示连接/超时后没有可靠回执，不能断言远端未执行，也不得自动重试。两者通过现有 `user_hint / action` 指向 `/integrations`，模型可继续其他获准操作，不将整个任务直接标为失败。

浏览器取消后的关闭未确认时，沿用 `tool.failed` 的 `TOOL_CLEANUP_PENDING / user_hint` 提醒用户检查实际结果，随后任务仍按取消结束，不把取消转换成可继续执行的普通失败。该提示不表示 Chromium 已停止：对应页面已禁止继续调用，context/profile 所有权保留到公开 close 事件或 Core 关闭处理；禁止自动重试原动作。关闭等待上限 10 秒，后台回收可继续处理未确认资源。

浏览器页面在重启、主动关闭或空闲回收后失效，工具返回 `BROWSER_SESSION_EXPIRED`，沿用 `tool.failed.error_code / user_hint` 提醒重新打开 URL、必要时复用原命名 profile；不自动重试旧句柄或重放上传、下载和点击。进程输入超时/断管仍通过原有错误响应告知“无法确认送达，不要自动重发”；已退出、未启用 stdin 与输入结果不确定分别使用经过筛选的产品提示，不返回第三方异常原文。

**文件变更。** `file_write / file_edit` 成功后发出任务归属的 `file.changed`：`path / tool_call_id / diff / diff_truncated / added / removed / before_hash / after_hash`。Diff 是单次工具操作前后的证据，不是整个回合净变化，不应将同文件多次修改的行数相加冒充净变化。前后内容各最多读取 256 KB，差异文本最多 64000 字符；二进制、无法读取或行数过多时 `diff_truncated: true`，无法计算的行数/hash 为 null。命令、MCP 或外部程序修改文件不在此事件覆盖内，仍使用现有工作区 Git Diff。发生写入后、事件提交前的崩溃可能缺少该投影；重放已满足内容的写入不会伪造原始 before image。同字节核对成功的 `file_write` 仍通过标准工具投影返回结果：`tool.completed.data` 含 `reconciled: true / bytes_written: 0 / size_bytes`，`file.changed` 含可选 `reconciled: true`，并以 `before_hash: null / diff_truncated: true` 表示原始差异不可恢复，不表示产生了新的磁盘写入。本批不提供文件回滚快照。

**进程。** job ID 使用真实命令的 tool_call ID，只允许停止当前 Core 持有且属于该工作区的句柄，客户端不能提交任意 PID。日志通过 GET 轮询，不是新增日志 WebSocket 协议；`log_tail` 为 stdout/stderr 各约 4000 的尾部片段。POSIX 使用不移动子进程文件偏移的 `pread` 读取；无 `pread` 的平台 `live_logs_available: false`，只提供已有完成输出。仍沿用现有执行超时、进程组终止及平台限制；这不是 detached background scheduler，不保证跨 Core 重启接管进程。

权限拒绝、版本冲突、任务已结束等情况不会以空列表/假成功代替真实写入结果。前端已接入相关端点和事件映射，但接线和 Mock 演示不等于真实 Python API 已联调通过；验收范围见 [交付路线与状态](../project/Roadmap.md)。

## 4. 命令语义

- 当前审批决定使用版本号防止重复决定和丢失更新。
- `Idempotency-Key` 用于任务插话和可选的聊天提交；不扩展为通用幂等写入框架。ETag、任务重试动作仍是规划中。
- 取消、审批和重试使用显式动作，不伪装成普通字段 PATCH。

## 5. Python HTTP 服务

`python/agent/api/app.py` 将所有原工作区 REST/WS 路由挂到同一个 FastAPI 应用，默认本机 8080；`/health` 在 Core 初始化完成后提供健康状态，`/openapi.json` 由请求模型生成。内部 gRPC、Proto 及 Go/Python 绑定已删除。

生命周期只允许一个 worker。HTTP 入口完成输入与工作区归属校验，Core 决定执行、审批、取消及恢复；客户端不能自选可信 principal。请求体上限 2 MiB，系统文件/凭证操作在工作线程中完成，HTTP 断线不取消已接纳任务。

原有会话、消息、任务、审批与事件 JSON 保持兼容；`runtime-status` 的 `gateway / agent_core` 合并为 `backend`，前端按单服务状态展示。Web 构建版由同一后端提供页面，未知 API 地址不返回 SPA 页面。迁移后已补真实 Core HTTP/WS、审批重启与文件恢复、Web 构建及指定 GUI 场景验收；不代表全部 API、平台或安装包通过，见 [当前验收状态](../project/Roadmap.md#当前验收状态)。

## 6. WebSocket 事件

连接：

```text
GET /api/v1/workspaces/{workspace_id}/events?conversation_id={conversation_id}
```

事件信封：

```json
{
  "schema_version": 1,
  "event_id": "evt_...",
  "sequence": 42,
  "type": "task.started",
  "occurred_at": "2026-09-14T00:00:00Z",
  "conversation_id": "conv_...",
  "task_id": "task_...",
  "payload": {}
}
```

稳定事件类型：

```text
message.delta
message.completed
model.usage
model.call.started
model.call.failed
task.started
task.waiting_approval
task.succeeded
task.failed
task.cancelled
task.interrupted
reasoning.started
reasoning.delta
reasoning.completed
tool.proposed
tool.started
tool.completed
tool.failed
subagent.queued
subagent.started
subagent.progress
subagent.message
subagent.tool.completed
subagent.completed
subagent.failed
subagent.cancelled
approval.requested
approval.decided
question.requested
question.answered
task.input.queued
task.input.consumed
plan.updated
context.updated
context.compaction.started
context.compaction.failed
context.compaction.skipped
context.compacted
context.history
safety.reviewed
file.changed
```

`message.*` 只承载用户可见的最终回答。模型主动输出的简短过程说明使用 `reasoning.*`，工具与子智能体生命周期分别使用 `tool.*` 和 `subagent.*`；三者均通过 `task_id` 关联任务，不写入消息资源。系统不传输模型隐藏思维链。

`subagent.*` 的 `subagent_id` 对应父任务的 `delegate` 工具调用 ID，不是独立产品 task。queued 带 `used/limit/task/label/requested_model_id`；started 带 `task` 和实际 `model_id/name/provider/model/label`，progress 带相同模型身份及只读工具名/参数，completed 带摘要和模型身份，failed 带安全错误信息与请求 ID。前端将进度/结果映射到同一 delegate 活动；原 `tool.completed/failed` 投影继续保留，父任务终态保持不变。子 Agent 模型事件的 `purpose=subagent`、`subagent_id` 及实际模型身份用于用量归属，不复用主回复的 `message.delta`。

子 Agent 对话页签继续读取父任务的事件历史，不新增 conversation、task 或独立执行状态。增量事件沿用现有可扩展 EventEnvelope：

- `subagent.progress` 增加 `tool_call_id`，精确关联子 Agent 内部一次读取；旧事件没有该字段时仅保留委派级进度，不虚构内部工具调用。
- `subagent.message`：`subagent_id/message_id/content/reasoning/final/truncated`。SDK 一次模型调用返回后记录公开文本与公开思考，不包含隐藏推理、签名、凭证或原始 provider metadata；`final` 表示此条回复未请求工具，不替代子 Agent 生命周期终态。
- `subagent.tool.completed`：`subagent_id/tool_call_id/tool_name/arguments/output/ok/duration_ms/truncated`。记录工具真实返回（包括拒绝或失败），不通过该事件重新执行工具；取消时未返回的操作不伪造结果。
- 单项正文、公开思考及工具输出各最多保存 64,000 个字符，超出时 `truncated=true`，不改变返回模型的原始工具结果或 checkpoint。页签按 journal sequence 排序、按消息/工具 ID 合并；历史没有这些增量记录时只展示已保存任务、摘要或错误。

这些是产品展示投影，仍由官方 `create_agent`/LangGraph 驱动子 Agent 执行。关闭 UI 页签不取消执行，父任务停止与只读权限检查沿用原链路。

`model.usage` 是可选的观测事件，不写入消息，也不表示任务完成。`payload.usage` 保存 Provider/SDK 提供的数值，例如 `input_tokens / output_tokens / total_tokens / cache_read_tokens / cache_creation_tokens / cache_miss_tokens / reasoning_tokens`；缺失缓存字段表示未提供。旧路径可能保留 `prompt_tokens / completion_tokens`。新路径同时覆盖主模型与摘要，通过 `purpose: response | summary`、`call_id` 区分；`model.call.started/failed` 记录调用次数和无完整结果的调用。SDK 内部重试与未返回的服务端用量无法据此补齐，因此不是完整账单。`context.compacted` 只给近似压缩前后/摘要 token 数和目标上限；`context.history` 标记 checkpoint 或可见历史导入，`execution_resumed: false` 表示开启新回合而非重放旧执行。未知事件类型不得阻断客户端的 sequence 推进。

`safety.reviewed` 记录一次提示词安全审查，payload 含 `review_id / model_id / request_format /
risk_score / threshold / skipped / skip_reason / blocked / reason`。`skipped: true` 表示当前模型命中
可信白名单，`risk_score` 为 null；`blocked: true` 表示分值超过阈值，或请求构造、决策模型调用失败。
`reason` 使用稳定值，例如 `risk_threshold_exceeded / review_error / format_unsupported`。
事件先写入任务事件日志再转发，客户端按 `event_id` 去重。拦截后的暂停复用既有任务状态事件
（`task.waiting_approval` / `task.interrupted`），不新增审批资源；人工决定通过既有任务恢复或取消接口
提交，结果由 `task.*` 状态事件回执，`review_id` 用于把决定关联回该次审查。本事件由运行时审查接入后
开始发送，本批仅定义契约。

重复聊天提交由 Core 返回 `replayed` 回执，API 转为 HTTP 200，不持久化、不广播为 UI 事件，也不向前端泄漏 LangGraph 类型。

客户端按 `event_id` 去重，按 `sequence` 检测缺口。断线不取消任务。API 在同一事件循环不让出执行的区间内，先排入 `{"type":"stream.ready","payload":{}}`，再允许实时事件入队。连接队列最多 64 项，发送超时或队列满时断开，客户端通过持久历史补拉。该帧是传输控制消息，不属于产品事件，不分配 `event_id / sequence`，不写活动区。

客户端收到 `stream.ready` 后才通过 REST `events/history?after=` 连续分页重放（每页最多 1000 条），期间缓冲实时事件；回放成功再释放缓冲。历史游标只由已确认的 HTTP 历史推进，不能用较新的实时序号或会话快照跳过活动历史。补拉失败则重连，不先释放越过缺口的实时事件；注册确认超过 10 秒同样重连。慢订阅者被断开后沿用这一恢复路径。

这提供“注册屏障 → 历史补拉 → 实时缓冲”的衔接，不是跨数据库/WebSocket 原子事务或新的 Event Sourcing。Core 持久日志是事件身份的唯一来源；API 不再为缺身份的错误或业务事件发明 ID/序列，转发异常只触发重连。API 与 Web 需一起更新，旧 API 不会发送注册确认。后端注册顺序、身份保持和超过 1000 条历史的定向验收范围见 [交付路线与状态](../project/Roadmap.md)；本次不包含浏览器 GUI E2E。

## 7. 错误模型

```json
{
  "error": {
    "code": "CONVERSATION_TITLE_LENGTH",
    "message": "对话名称须为 1–200 个字符。",
    "params": {"min": 1, "max": 200}
  }
}
```

继续使用 `{error: {code, message}}` 信封，`params` 为可选的安全标量参数映射（字符串、数字、布尔值），缺省为空。`message` 是可直接展示的安全兜底，不是原始第三方异常；不再把尚未实现的 `request_id / retryable / details` 写成现有响应。

会话/工作区校验及安全确认逐步细化错误码，如 `CONVERSATION_TITLE_LENGTH / CONVERSATION_BUSY / WORKSPACE_FOLDER_CHANGED / WORKSPACE_TASK_ACTIVE / QUESTION_CHOICE_REQUIRED`，不改变原有 HTTP 状态语义。前端仅对已登记的具体错误码使用现有 i18n 和已校验参数；未知码、缺失参数或旧服务响应继续显示安全兜底。未迁移接口仍使用 `VALIDATION / CONFLICT / ACCESS_DENIED / NOT_FOUND / INTERNAL` 等通用码，不按中文提示文本反推错误类型。

错误消息不得包含敏感凭证、原始系统提示、内部路径或未脱敏工具输出。

## 8. 版本策略

- REST 主版本位于路径。
- Event 使用 `schema_version`。
- 请求模型与 JSON DTO 的兼容性独立于内部实现语言；不再维护 Proto。
- Breaking change 必须新增版本并记录 ADR。
- Markdown 示例应接受机器契约校验；当前没有完整自动文档校验流水线，不能把要求写成已通过。
