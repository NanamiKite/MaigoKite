# 测试与验收策略

> 本文说明测试场景、执行入口和通过条件，不表示下列场景均已实现或通过。
> 实际结果见 [交付路线与状态](../project/Roadmap.md)，执行环境见 [开发指南](../development/DevelopmentGuide.md)，安装包要求见 [构建与发布指南](../deployment/ReleaseGuide.md)。

## 1. 验证原则

单元/契约/集成用于日常修改，真实进程恢复和完整 E2E 用于运行时/发布变更，性能与干净机器安装包用于代表性环境。覆盖率、编译或模拟服务成功都不能替代故障恢复和产品语义验收。

- 报告代码版本、命令、范围、实际结果和跳过原因；测试文件存在不算通过。
- PROTOCOL PASS 仅指本地 HTTP/SSE 等协议验收；LIVE PASS 必须真实模型提供方完成相同路径；无凭证/未运行记 UNVERIFIED。
- 不发明凭证、不改敏感凭证管理来获得真实服务验收结果；缺失某个真实服务验收不妨碍其他检查，但不得宣布完整发布就绪。
- 迁移时的新旧路径对比保留为历史证据。当前只验证唯一 LangGraph 路径的产品语义，不恢复旧执行器，也不为模仿旧内部行为增加状态机；模型回复不要求词元完全相同。
- 不测试或要求尚未进入需求的 Team/PostgreSQL/工作流/包平台作为当前发布前提。

## 2. 现有入口

Agent 的任务结果与重复运行稳定性由独立 [个人 Agent 行为评测](AgentEvals.md) 覆盖。默认脚本模型只验证评测装配，不能计入真实模型能力；它不替代本文件中的功能验收。

下表列出当前测试入口及历史名称。测试源码、fixtures、Vitest 配置和运行脚本由 Git 管理；本地生成的 `test-results/` 与 `playwright-report/` 不提交。新克隆后需先安装对应依赖。只有实际执行并记录结果，才能报告通过；文件存在不等于测试通过。

| 位置 | 覆盖对象 |
| --- | --- |
| `gateway/` Go 测试（历史，后端已删除） | 曾覆盖路由、参数/错误映射、工作区、模型/凭证与文件查询；不能替代 Python API 测试 |
| `python/tests/unit / integration` 等 | 核心服务、Repository、策略、工具、浏览器和模型 |
| `web/src/tests` | 状态仓库、设置、活动/审批、文件与 Markdown、Electron 环境 |
| `python/tests/unit/test_desktop_access.py` / `web/src/tests/desktop-access.test.ts` | 进程内 ASGI 来源/凭据/预检/WS 边界与 Electron 请求头目标逻辑；不启动 Core，不替代真实窗口和 webRequest 验收 |
| `test_langgraph_production.py` | 生产工具/审批/取消与投影 |
| `test_langgraph_process_recovery.py` | 核心服务进程终止/恢复、文件崩溃点 |
| `test_context_optimization.py` | 结构定义预算、配对、摘要保护/取消、用量和客户端轮换 |
| `python/tests/spike` | 选型协议/真实服务验收验证，不代替真实生产入口 |
| `scripts/acceptance` | 关闭清理、MCP 取消复用、事件分页及运行时收尾门禁；由 `scripts/run_tests.sh` 默认纳入 |
| `scripts/acceptance/test_personal_agent.py` | 真实 Core 子进程与 TCP HTTP/WS：聊天工具循环、拒绝后继续、停止、搜索、子模型、扩展重启、记忆/分岔和进程输入 |
| `scripts/acceptance/gui_personal_agent.py` | 显式运行的真实 Web/Chromium 验收；不由默认后端 CI 收集，缺少构建或真实浏览器时不得记为通过 |
| `scripts/acceptance/test_media_features.py` | 图片输入与生成的真实 Core/API/SDK 协议验收，含审批、配置变化、取消、Core 崩溃和计划投影；不使用付费模型 |
| `python/tests/unit/test_media.py` | 图片地址/文件边界、派发意图持久化、磁盘写入中重复取消；测试替身与真实资源的边界在用例内说明 |
| `scripts/acceptance/gui_media_features.py` / `web/src/tests/media-preview.test.ts` | 真实图片 GUI 与预览安全/兼容性；保留 SVG 本地符号回归断言，并以真实像素验证符号渲染，结果见 Roadmap |
| `python/tests/unit/test_model_catalog.py` | 公共模型目录读取中断后的离线回退；真实目录对象配受控 HTTP 响应流，覆盖 HTTPX/TLS 异常、完整快照保留、关闭与退避，取消/程序错误不被吞掉，不依赖公网 |

### 功能验收入口

#### 背景 GUI 脚本与静态契约

背景滑块以当前设计的 0–100% 直接透明度为准；不再检查已移除的自动求限或 `--plate-a`。
截图对比度门禁检查不透明纸面基线，自由透明度另验 0/50/100 与刷新持久化，不宣称任意图片下都满足对比度阈值。
`gui_desk_background.py`、`gui_desk_runtime.py`、`gui_desk_electron.py` 会将必检项缺失/为假、空截图文字清单及页面异常计入失败，写出报告后返回非零退出码。滚动耗时是测量值，不是性能通过结论。
文字令牌按每个元素实际继承的作用域读取，包含设置预览的局部覆盖；未定义令牌不能借 CSS 继承冒充已登记颜色。真实 Chromium 反向用例验证嵌套覆盖和任意未登记色，仍保留 7:1 / 4.5:1 门禁。脚本在 `finally` 写报告时保留原始异常，不能只留下“缺少检查项”而丢失失败原因。

三个脚本均要求显式 `--output <独立目录>`。Web 脚本支持 `--url` 和 `--chromium`，未指定浏览器时使用已经安装的 Playwright Chromium，不自动下载；图片默认使用生成的 PNG，`--photo` 可指定本地素材。Electron 使用本项目 Electron 包公开导出的平台路径，也可指定 `--electron`，配置目录临时创建并清理。仍需预先提供隔离的 `VITE_MOCK=1` Vite 服务或对应桌面构建；不能指向正在使用的客户端。

验收脚本的反向控制检查不启动浏览器、后端或模型，已纳入默认 pytest 收集；也可独立运行：

```bash
uv run --no-sync python -m unittest discover -s scripts/acceptance -p test_desk_checks.py -v
```

CI 对上述脚本、共享检查与文字色报告运行 Ruff/strict mypy，并通过 `vue-tsc --noEmit -p tsconfig.json --incremental false` 检查前端测试契约。反向控制、静态检查和 jsdom 通过不代表真实 GUI、Electron 安装包或 Windows 已验收。

安装锁定的 `dev` 检查依赖后，可运行完整后端测试及产品验收；显式排除真实付费模型入口：

```bash
PYTHONPATH=python uv run --no-sync python -m pytest \
  python/tests scripts/acceptance \
  --ignore=python/tests/spike/test_runtime_spike_live.py -vv --tb=short
```

只运行后端产品验收及进程恢复：

```bash
PYTHONPATH=python uv run --no-sync python -m pytest \
  scripts/acceptance python/tests/integration/test_langgraph_process_recovery.py \
  python/tests/integration/test_http_api.py -vv --tb=short
```

Web/浏览器验收须先构建 `VITE_MOCK=0` 的真实 UI，再显式运行；`MAIGOKITE_ACCEPTANCE_CHROME` 可指定已有 Chrome/Chromium。用例不会自动安装浏览器、关闭沙箱或连接用户账号：

```bash
# 在项目根目录执行；构建脚本读取 web/ 中的依赖。
(cd web && VITE_MOCK=0 npm run build:web)
PYTHONPATH=python MAIGOKITE_ACCEPTANCE_WEB_DIR="$PWD/web/dist" \
  uv run --no-sync python -m pytest scripts/acceptance/gui_personal_agent.py \
    scripts/acceptance/gui_media_features.py -vv --tb=short
```

只验新增图片功能可运行 `PYTHONPATH=python uv run --no-sync python -m pytest scripts/acceptance/test_media_features.py python/tests/unit/test_media.py -vv --tb=short`；前端全量入口为 `cd web && npm run test:run -- --no-cache --maxWorkers=2 --minWorkers=1`。已知失败应原样报告并保留断言，不用跳过或修改净化预期清零。当前结果唯一来源为 [当前验收状态](../project/Roadmap.md#当前验收状态)。

PowerShell 可先设置 `$env:PYTHONPATH = "python"`，再运行同样的 `uv run --no-sync python -m pytest ...` 后端命令；这不代表 Windows 原生验收已通过。GUI 用例的当前运行证据为 Linux，不能外推为 Electron 安装包验收。

新增产品验收只替代模型 HTTP/SSE 输出，Core、LangGraph、PermissionPolicy、Repository、工具、MCP 与 GUI 保持真实；沿用现有进程恢复测试的启动入口。子 Core 使用独立工作区/SQLite/checkpoint，剥离继承的模型/Tracing 密钥。真实浏览器测试访问公开测试页面，Cookie 为自建 canary，不包含真实登录；命令 stdin/stop 使用测试自己启动的进程及显式完全访问，不视为 OS 沙箱隔离验收。
`gui_personal_agent.py` 还通过真实 API/页面验证会话标签切换、重命名、关闭不删除、重开草稿保留与不落 localStorage，以及正文鼠标/键盘调宽、释放后保存、刷新恢复、双击还原和窄屏溢出。文字测量辅助程序的独立反向用例不替换产品方法，也不计入 Agent 能力验收。

模型配置/凭证单测在本模块的元数据 HTTP 边界固定公共目录响应，避免假模型触发额外公网依赖；不替换目录解析或回退业务逻辑。独立 `test_model_catalog.py` 保留原生 TLS 错误的回归断言，先复现失败、再验证产品修复，不能用模块夹具屏蔽。2026-10-16 首轮曾实际访问公共 `models.dev` 并暴露该缺陷，不是付费模型调用；后续修复定向通过与全量历史分别记录，最终回归结果和仍开放的失败只在 Roadmap 记录。

完整批次 HITL 必须验证全部决定齐备前无写入，旧预期“首项批准立即写第一个文件”不再适用。多项文件批准与部分批准后重启已有定向记录，但不覆盖后续驳回语义调整；两者的验证范围须分开，不能删除失败测试后宣称通过。

### 最近改动需要补测的内容

以下是待验证清单，不是新增测试的通过记录：

- 核心模块调整后，复验同一生产入口的审批、提问、取消、扩展替换与关闭；不能只检查迁移前后的函数是否相似。
- 会话标题覆盖新建空标题、首条消息、重复提交、手工改名、分岔及旧库升级，确认用户命名不会被默认展示文字覆盖。
- 安全确认分别检查稳定选项标识、翻译后的标签、旧无标识记录、未知选项、版本冲突和 Core 重启恢复；通用提问仍可自由输入。
- 错误展示检查已知错误码参数、未知码及参数缺失时的安全提示，确认不按错误文案判断业务结果。
- Electron 44 单独检查安装下载、源码构建、实际启动/退出、图形加速偏好、通知及目标平台限制；旧版本的背景展示结果不能替代新版本运行验收。

### 附件、文档与桌面认证

现有定向测试与待验证产品路径分开记录，不能因同属多模态或桌面能力就合并计算通过范围：

| 范围 | 要检查的行为 | 当前证据边界 |
| --- | --- | --- |
| 文本/图片快照与草稿 | 服务端类型和总量、哈希变化、切换对话/迟到响应、失败重试、按行与字节续读、模型能力变化和跨目录历史 | 10 月 28–31 日有静态记录，新增行为未验收；旧图片用例不替代这些场景 |
| PDF/Office 提取与导航 | 损坏/加密/超限错误、真实目录、空文字页、单元编号、文字层与 OCR 偏移分离、快照版本与长文本续读 | 新入口未运行行为测试；必须用真实文件，不能靠空夹具宣布支持 |
| 页图与本机 OCR | 引擎/语言不可用、实际页图、像素/输出/超时限制、取消后进程与临时文件清理；文字降级预算和可回读引用 | OCR/PDF 渲染、语言准确率、真实 GUI 与 Windows 未验收；不以文字提取代替视觉验证 |
| 附件集合约束授权 | 当前会话/文件夹的 path+hash、添加新附件或版本变化后的授权失配、拒绝后继续、执行前重查、完全访问边界 | 源码已接入，行为未验收；不宣称完整污点追踪或提示注入防护 |
| 桌面 API 认证 | HTTP/预检/WS、错误或缺失凭据、伪造 null/file 来源、允许窗口/主 frame、自有 API 和重定向不泄漏 | ASGI 24 项与请求头逻辑 12 项已有定向通过记录；真实 Electron 事件、窗口识别和握手仍待验收 |

结果与命令见 [近期记录](../project/Roadmap.md#2026-10-28-与-10-31-的定向测试)。本清单不授权启动应用、调用模型或执行测试，运行前仍按项目工作约束确认范围。

### 天线扩展与数值实验

分三层验证，分别记录结果，不相互替代：

| 层次 | 需要验证的内容 |
| --- | --- |
| 数值实验 | `experiments/mmana_compatibility/` 的物理输入、参考来源、网格与误差分析；每项结论只适用于实际计算过的模型和材料 |
| 独立工程包 | 干净环境安装、JSON/受限 MAA 输入、solver 与 manifest、编号复用/冲突、项目准入和重启；搜索停止原因、网格结果、固定/逐频匹配、交付包来源与 ZIP 完整性 |
| MaigoKite 接入 | 官方 MCP 调用/进度、现有审批和取消清理、URI/MIME/大小/hash 校验、当前目录归属、只读/权限变化、冲突保护、部分导入、鉴权下载与聊天展示 |

导入失败时应保留已完成的计算，不自动再次求解；只有导入完成标记存在才显示完整导入。还要确认关闭或取消后的资源清理，以及正常聊天在未安装天线包时仍能使用。操作入口见 [独立包说明](../../extensions/antenna_design/README.md) 与 [接入步骤](../user-guide/Configuration.md#81-天线计算与方向图)。

10 月 23–25 日已有 Linux 的真实 MCP、生产 Core 审批/取消、重启取回、图片 HTTP 预览、搜索交付与固定匹配的定向记录，范围见 [路线图](../project/Roadmap.md#2026-10-23-天线恢复与生产调用验收)。这些记录不是本次重新运行的测试，也不能覆盖完整 GUI、自然语言模型决策、Windows、运行中强杀恢复或每条独立 CLI 命令。

10 月 28 日的 `WorkflowState` 和 v2 回执另需检查 revision/证据冲突、已发布包与数据库之间的崩溃窗口、重复查询补记、历史 v1 兼容、未知网格/匹配状态，以及单独 optimize 不复用 design 编号。这批只有静态结果，不能从旧版运行记录推出通过。

工程结论另看样本和物理条件。须覆盖有限电导率、邻频辐射、同一组固定元件跨频点复算，以及基础网格达标但细网格不达标的情况。没有容差不宣称收敛，离散点通过不代表连续带宽；理想匹配不替代元件 Q/损耗、功率、巴伦和实物验证。

主应用 CI 不会自动进入独立包安装依赖或执行实验；本地临时验收记录也不等于已提交可回归的测试源码。正式回归范围、独立安装和目标平台测试仍须补齐，不能将根项目检查通过写成所有层均通过。

### GitHub Actions

[CI 工作流](../../.github/workflows/ci.yml) 在所有 PR、`main`/`master` 推送及手动触发时运行。同一 PR/分支启动新运行时，会取消旧运行。

Python 使用 3.14。CI 通过 `uv sync --locked --extra dev --extra vectorstores --no-default-groups` 安装检查依赖，让全源码 mypy 能读取可选适配器的 SDK 类型；普通应用安装仍不包含 `vectorstores`，安装检查依赖不表示这些模块已接入 Core 或通过行为验收。Node 使用 24.x，通过 `npm ci` 安装依赖。不配置模型凭据或仓库 Secret。CI 和 `scripts/run_tests.sh` 显式排除 `test_runtime_spike_live.py`，其余模型测试使用测试替身或本机 HTTP/SSE 服务，不产生模型 API 费用。

| 检查 | 范围 |
| --- | --- |
| Python checks and acceptance | Ruff、mypy、`python/tests` 与 `scripts/acceptance`；包含 HTTP/WS、Core 跨进程审批恢复和文件崩溃点 |
| Frontend checks and tests | 不带 `--fix` 的 ESLint、全部 Vitest 用例 |
| Source build（Linux / Windows） | 调用对应 Bash/PowerShell 构建脚本，检查 Web 生产 TypeScript、Web/Electron 编译、Python wheel/sdist |
| CI gate | 仅当上述所有检查成功时通过；可在仓库分支保护或规则集中设为必需检查 |

lint/类型检查失败后，仍继续执行测试并保留失败状态，不使用 `continue-on-error`。pytest/Vitest 的 JUnit 报告作为 Actions artifact 保存 7 天，不提交到 Git。

测试步骤和作业均有超时限制。pytest 长时间无响应时输出线程栈，供排查阻塞位置。

此工作流不发布制品、不启动桌面窗口，也不验证独立安装包或真实模型服务。浏览器测试使用替身，不能计为真实 Chromium 验收；Bubblewrap 用例因宿主限制跳过时，须查看 pytest 的跳过原因。

Core 的命令契约用例在执行器边界使用替身，断言规范化目录、沙箱模式、网络权限与审批前不执行；另测沙箱不可用时返回 `SANDBOX_UNAVAILABLE` 且不回退到宿主进程。受限 `glob/grep` 单独验收可信 ripgrep、固定参数、临时工作目录、最小环境与有限只读快照，命令沙箱不可用不应阻塞搜索；缺少可信 ripgrep 时返回 `RIPGREP_UNAVAILABLE`，不改走 shell 或任意命令。真实搜索验证默认 ignore/隐藏文件过滤、显式正向 glob 的原生包含覆盖语义、始终生效的敏感路径排除、符号链接、快照读取和临时目录回收，不 mock 搜索结果。真实 Bubblewrap 用例保留 `pwd`、宿主读取隔离及工作区写入断言，只有实际探测可用时运行，跳过不计为隔离验收通过，不通过修改系统安全设置让测试通过。

前端用例使用真实 router、i18n 和可交互的选择/确认组件，按页面实际请求提供 API fixtures。未 mock 的 `fetch` 会被阻止并使测试失败，即使组件捕获了异常；不得连接开发者正在运行的后端作为隐式测试依赖。

工作流已配置不等于远程运行通过。通过、失败或跳过的结果，以对应提交的 Actions 记录为准。

## 3. 生产聊天纵向路径

```text
发送聊天 → 所选 Runtime → file_read / file_write
→ 现有 Policy / Approval → UI event → 最终回复与数据库投影
```

验证至少：

- 普通聊天、显式 `/tool`、审批/回答续接与受限恢复均进入同一 create_agent/LangGraph 路径；旧 Loop 和迁移开关已删除，不再要求 legacy 默认可用。
- 内置结构定义、规范化参数与权限策略一致，ToolResult 能进入第二模型回合；失败/拒绝不伪装成功。
- 多工具调用完整保留，不依赖模型重复提出；同名同参数正确配对。逐项审批，批次决定齐备后仅执行获准项；拒绝、过期/policy change 均有确定结果。
- 审批摘要值/版本、单次消费、审计与 UI 投影一致；运行恢复不读取 continuation_json。

## 4. 取消与恢复

取消验收覆盖模型流式输出、file_read 和可控慢速异步工具：根任务取消 → 资源清理完成 → `task.cancelled`。终态后不得再产生非终态事件。Core 关闭须等待所有运行实例，再关闭共享客户端；同步线程不能立即终止的限制须如实记录，不能用任务状态掩盖后台工作。

真正的跨进程 E2E：

```text
Core 启动 → file_write interrupt → 文件未写
→ 终止 Core process → 新 Core 启动
→ 同 thread/run 审批恢复 → 写入 → 模型续答
```

进程交互经当前 HTTP API 完成，实时事件经当前 WebSocket 完成；不再使用已移除的 gRPC 客户端或监听端口。

检查检查点落盘、无旧进程内存、参数摘要值/版本、一次合法审批、UI/产品终态一致。额外测试部分批次批准后重启，不只重新实例化适配层。

file_write 保留两类崩溃点：批准后执行前；写入后 ToolMessage/检查点提交前。记录实际重放次数与内容状态核对；不把相同内容当通用恰好一次（exactly-once），增加内容冲突/外部修改/并发边界。浏览器易失 session 的 interrupted 与 MCP 外部执行限制按当前路径单独验收。

## 5. 模型提供方与上下文

OpenAI、Anthropic、Ollama、目标兼容服务各记录 text 流式输出 → tool 调用 → tool result → 第二模型回合 → final → 取消。Anthropic 工具循环不能仅用首轮文本替代，真实服务验收矩阵记录在具体验收报告，不复制多份。

上下文覆盖固定前缀、结构定义开销、最近用户/完整工具配对、超预算错误、摘要失败/空/未缩减保护、摘要不泄漏 UI、取消摘要、调用上限不因审批恢复清零。真实缓存延迟/命中/费用和摘要质量须代表性服务测量，不能用模拟服务数值推断。

共享客户端：单运行实例取消不关闭其他运行实例；轮换后不复用旧模型实例；关闭核心服务无泄漏；仅 loopback 自动直连，私网/公网遵循 HTTPX 环境代理和 NO_PROXY，且不禁用环境 CA。HTTP 429 只提示请求受限，不据此推断账户额度；凭证仍须隔离。基础地址按用户输入保留，不按端口补 `/v1`。

## 6. 产品、安全与持久化

- API 层本机来源/工作区归属检查贯穿列表、详情、命令、WS 与文件；可信 principal 不来自客户端。设备认证尚未交付，不能把本机检查当成设备认证验收。
- 权限预期唯一来源为 [工具权限](../agent-capabilities/security/tool-permission.md)：动态风险、hard-deny、参数变更和撤销均回归。
- JSON Schema 联合/嵌套/额外属性/离线引用与错误 DTO；MCP isError/非法结果正确失败，超时/异常退出/最小环境与资源清理。
- 浏览器用真实 Playwright 验证公开 URL、动作审批、截图路径、失败/取消和 session 回收，不自研浏览器协议替代测试。
- 前端活动/回答分离、事件去重/缺口/超过 1000 条、断线不取消、Markdown/HTML 消毒、审批重复点击和错误提示。
- 当前 SQLite 空库/旧库升级、外键/排序、状态与事件一致性债务、一致性备份；不假定 Outbox/UoW 已存在。
- 用户记忆覆盖手工管理、遗忘、文件夹归属和双开关请求投影；核对请求副本与消息/checkpoint 分离、续接重新读取及超预算省略。自动提取与语义检索未接入，不计为通过；未来索引增加后再测删除传播。
- Electron 窗口/IPC/导航安全及实际安装制品启动、关闭无孤儿进程；开发环境通过不代替 package-smoke。

## 7. 性能与发布

记录硬件、模型、代码版本、数据规模和并发，测长聊天渲染/输入、首词元/任务耗时、事件补拉、SQLite 锁、浏览器/MCP 常驻资源。只报“提升倍数”或页面不卡不够。

发布前，适用的质量检查、契约、安全、恢复、备份和干净机器制品验收必须通过；已知缺陷及未验证项须明确列出。手机配对、锁屏重连等场景，仅在 [手机远程访问](../design/RemoteAccess.md) 的对应版本进入交付时增加，不作为本地首版的发布前提。
