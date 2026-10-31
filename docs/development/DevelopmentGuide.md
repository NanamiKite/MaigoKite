# 开发指南

> 本文说明源码初始化、启动、质量检查和贡献约束。
> 组件职责见 [系统设计](../design/SystemDesign.md)，发布要求见 [构建与发布指南](../deployment/ReleaseGuide.md)。

## 1. 仓库入口

| 路径 | 用途 |
| --- | --- |
| `python/agent/api/` | FastAPI HTTP/WebSocket、系统凭证和工作区文件接口 |
| `python/agent/` | 核心服务、运行时桥接、工具和业务持久化 |
| `web/src/` | Vue；`electron/` 下为主进程/预加载层 |
| `extensions/antenna_design/` | 可单独安装的天线计算包，自有依赖清单、锁文件与可选 MCP 入口 |
| `experiments/mmana_compatibility/` | MMANA 开发采集、求解器对照与差异分析；不参与主应用启动 |
| `config/`、`scripts/` | 运行配置、开发/构建/测试脚本 |
| `docs/` | 按 [文档索引](../README.md) 分配权威范围 |

后端只运行一个 Python 进程，API 与 AgentCore 直接调用。现有运行时已经支持人工审批、受限文件任务恢复和单层只读子 Agent；持久工作流及更复杂的委派仍未实现，范围见 [运行时设计](../design/AgentRuntime.md#7-技能子任务与工作流边界)。

早期无调用方的独立工作流/HITL、子 Agent 和 Recovery 骨架已移除，不代表上述现有能力被删除。判断功能是否可用，要看实际调用，不能只看目录或类名。不要在不同模块重复定义同一套类型、策略或协议。

## 2. 初始化

不再需要 Go SDK、protoc 或 gRPC 绑定生成。Python 固定使用 3.14.x：`.python-version` 选择 3.14，`pyproject.toml` 限定 `>=3.14,<3.15`，不自动接受旧版或下一小版本。Node/npm 版本与依赖以 `web/package.json` 和锁文件为准。

主应用 Python 依赖在根目录的 `pyproject.toml` 中声明，解析结果保存在根目录的 `uv.lock`。`python/` 只存放源码，不单独安装，也不维护另一份 requirements 清单。

推荐从仓库根目录执行，按锁文件安装运行依赖到根 `.venv`：

```bash
uv sync --locked --no-default-groups --python 3.14
```

需要 Python 开发工具时追加 `--extra dev`。`dev` 定义在 `project.optional-dependencies`，包含 pytest、Ruff、mypy、Black、pre-commit 与 Python 构建工具，不是自动安装的 dependency group。

`--no-dev` 仅针对 dependency group，不能替代去掉 `--extra dev`。默认不添加 `--all-extras`。

`vectorstores` 只用于尚未接入聊天的实验适配器，默认不安装，也不表示知识库功能已经交付。维护者修改依赖后须运行 `uv lock` 更新锁文件；依赖锁定成功不代表运行或发布验收通过。

天线扩展是独立 distribution，不在根项目的 extras 中。需要它时，进入 `extensions/antenna_design/`，按[包说明](../../extensions/antenna_design/README.md)安装；其 `engineering` / `mcp` 依赖使用该目录自己的 `uv.lock` 和虚拟环境。普通 setup 不安装求解器，根项目检查也不等于已检查或运行该扩展。实验脚本与可安装包分开维护，不能把旧数值对照当作新入口的运行结果。

也可以让 `setup.sh` / `setup.ps1` 安装应用所需依赖。默认 Python 只安装运行依赖，显式 `setup dev` 才包含 Python 开发工具。脚本优先使用 uv；没有 uv 时，用可用的 Python 3.14.x 创建项目环境，再通过 pip 安装同一清单。pip 安装不保证使用 uv 锁定的版本，也不会清除先前安装的开发工具，脚本会明确提示；不另写依赖卸载器。解释器命令名也不必是 `python3`：

```bash
bash scripts/setup.sh
```

如果只运行了上面的 uv 命令，还需要从仓库根目录安装 Web 依赖：

```bash
cd web
npm ci --include=dev --foreground-scripts --ignore-scripts=false --engine-strict
npm run electron:install
```

默认 `setup`（与 `setup all` 相同）和 `setup dev` 会执行以上 Web 安装步骤。`setup python` 只同步 Python 运行依赖，不修改 `node_modules`，也不检查 Electron 是否安装完整。

npm 的 devDependencies 包含源码构建/启动所需的 Vite、TypeScript、Electron 等，与 Python 可选开发工具不同。uv 和 pip 都使用根项目，默认环境目录为根 `.venv`。可用 `UV_PROJECT_ENVIRONMENT` 指定其他目录，相对路径以项目根为基准。

脚本不使用 Poetry 或子项目清单，也不默认安装向量库或 Docker SDK。不要把项目依赖强行安装到全局解释器中。设置 `MAIGOKITE_PYTHON` 后，uv 安装和构建使用指定解释器；pip 用它创建项目环境，依赖仍安装在项目环境内。

升级旧 Python 环境前，先退出整套应用。setup 修改依赖前会检查成套服务的 8080/5173 端口，有占用则退出，不结束进程；其他端口上的手动启动实例仍须自行关闭。

安装时允许 uv 获取 Python 3.14，并重建不兼容的项目虚拟环境。默认精确同步会移除不属于本次依赖选择的包；从 `setup dev` 切回默认 setup 或 `setup python` 时，仅开发使用的 Python 包也会被移除。不要在虚拟环境内存放业务数据。脚本不删除 uv 共享解释器、下载缓存、数据库或工作区。

没有 uv 时，先安装 Python 3.14，将旧 `.venv`（或 `UV_PROJECT_ENVIRONMENT` 指定的目录）重命名保留，再运行 setup。pip 分支不自动删除或重建不兼容环境；同版本环境中的多余包也不会由 pip 自动移除，需要精确清理时改用 uv。启动时发现旧环境会明确报错，不会偷偷切换到另一个缺少依赖的全局 Python，也不自动删除环境。

## 3. 成套启动与构建（Linux / Windows）

两平台共用 `scripts/local.cjs`，Shell / PowerShell 入口只转发参数，不依赖 Makefile、WSL 或 Git Bash。

需要 Node.js 22.22.2+（22.x）、24.15.0+（24.x）或 26+，Python 3.14.x。Node 范围来自锁定的 vue-i18n / nopt / abbrev 等依赖，不支持 Node 18。Windows 使用 PowerShell 5.1/7。请在目标机器上初始化，不要复用从 Linux 复制的 `.venv` 或 `node_modules`。

| 操作 | Linux（仓库根） | Windows PowerShell（仓库根） |
| --- | --- | --- |
| 初始化应用依赖 | `bash scripts/setup.sh` | `.\scripts\setup.ps1` |
| 仅同步 Python 运行依赖 | `bash scripts/setup.sh python` | `.\scripts\setup.ps1 python` |
| 初始化并加装 Python 开发工具 | `bash scripts/setup.sh dev` | `.\scripts\setup.ps1 dev` |
| 修复 Electron | `bash scripts/setup.sh electron` | `.\scripts\setup.ps1 electron` |
| Web 开发 | `bash scripts/start.sh web` | `.\scripts\start.ps1 web` |
| 桌面开发 | `bash scripts/start.sh desktop` | `.\scripts\start.ps1 desktop` |
| 构建 Web | `bash scripts/build.sh web` | `.\scripts\build.ps1 web` |
| 构建桌面 | `bash scripts/build.sh desktop` | `.\scripts\build.ps1 desktop` |
| 构建两种入口 | `bash scripts/build.sh all` | `.\scripts\build.ps1 all` |
| 已构建 Web | `bash scripts/start.sh web built` | `.\scripts\start.ps1 web built` |
| 已构建桌面 | `bash scripts/start.sh desktop built` | `.\scripts\start.ps1 desktop built` |

`start` 的默认目标是 web，默认模式是 dev。dev 启动 Python 后端和 Vue 热更新页面；桌面入口先编译 Electron，由 Electron 启动后端。桌面等待后端 `/health` 就绪以及开发模式下的 Web HTTP 响应，再创建窗口；退出时会取消等待。开发窗口与 Vite 均使用 `127.0.0.1:5173`，不依赖 localhost 的 IPv4/IPv6 解析顺序。桌面 built 直接加载 `web/dist/index.html`，不需要开发服务器。

`built` 不自动编译。npm 和根目录构建命令共用 `scripts/lib/web-build.cjs`：开始前撤销所构建组件的成功记录，所有步骤成功后才写入对应输出目录的 `.maigokite-build.json`。启动前核对界面源码、资源、构建配置及锁文件的摘要，并确认入口产物完整；缺失、失败或过期时拒绝启动任何服务。旧版本没有记录，也需要重新构建。该记录只用于源码目录的启动保护，不是安装包验收或签名机制。

Linux 可用 `bash scripts/build.sh desktop && bash scripts/start.sh desktop built`，保证仅在构建成功后启动。PowerShell 5.1 不支持 `&&`，改用 `if ($LASTEXITCODE -eq 0) { .\scripts\start.ps1 desktop built }` 接在 build 命令后；Web 入口同理。

`build` 默认构建 all，也可选择 python/vue 单个组件。其中 python 生成 wheel/sdist；有 uv 时使用其隔离构建，不要求项目安装 Python dev extra；无 uv 时须先显式安装开发工具（`setup dev`），否则明确提示缺少构建工具。Web/桌面构建不依赖 Python 开发工具。

Web dev 使用 `http://127.0.0.1:5173`，Vite 代理 HTTP/WebSocket 到 Python 后端 8080；built 使用 `http://127.0.0.1:8080`，由同一个 Python 进程提供页面和 API，不启动 Vite preview。只支持本机 loopback，不提供远程认证。

Python CLI 为 `maigokite-agent run --config config/config.dev.yaml --port 8080`，源码也可用项目 Python 执行 `python/main.py run`。可选 `--web-dir web/dist` 提供已构建页面；只允许一个 worker，不使用 uvicorn reload/multi-worker 启动同一 Core。

默认配置为 `config/config.dev.yaml`。built 也使用这份配置和开发数据库，不会自动切换数据目录。可通过 `MAIGOKITE_CONFIG` 指定其他配置，但成套脚本与前端代理仍约定使用 8080。

### 路径与解释器

Web 与桌面默认使用安装根目录下的 `.workspace`，由 Core 在启动时创建。源码模式的安装根目录就是项目根目录。可通过非空 YAML `workspace.root` 或 `MAIGOKITE_WORKSPACE_ROOT` 指定其他目录；启动不会搬动旧文件，也不会把源码根目录本身当作工作区。

脚本中的相对安装、工作区、配置和解释器路径，都以项目根目录为基准解析，不随子进程的工作目录变化。

Core 解释器顺序：`MAIGOKITE_PYTHON` → `UV_PROJECT_ENVIRONMENT` 或根 .venv → 已激活环境 → `uv python find 3.14 --no-python-downloads` → 系统 `python3.14 / python / python3` → Windows `py -3.14`；所有候选都校验为 3.14.x。
显式配置无效立即报错；启动不下载解释器或安装依赖。
工作区命令和 MCP 的 command/args 不必使用 Core 的解释器。Linux 沙箱仍按原有权限和挂载边界处理路径。

浏览器工具需单独执行 `<Core Python> -m playwright install chromium`，或在配置中指定已有 Chromium 的路径。
桌面需本机图形会话；Linux 的系统图形库依赖不由脚本提权安装。

Linux 桌面启动会在 `$XDG_DATA_HOME/applications`（未设置或不是绝对路径时为 `~/.local/share/applications`）维护 `com.electron.maigokite-web.source.desktop`，使 Wayland/X11 的窗口标识能关联到项目图标。入口使用当前源码的绝对图标路径，`Exec` 调回同一套启动脚本；不新增应用菜单项或自启动，不修改系统级入口。源码版标识通过启动器内部的 `MAIGOKITE_DESKTOP_NAME` 在 Electron ready 前传入，不改变用户数据目录；安装版使用 `package.json` 的 `desktopName`，由 Builder 的 `linux.syncDesktopName` 同步生成桌面入口。

仅带脚本管理标记的入口会被更新；自定义同名入口不会覆盖。注册失败会输出警告但不阻止启动，此时任务栏可能仍显示默认图标。修复图标后需从托盘退出、重新构建并启动；不需要重装 Python/Electron、注销桌面会话或强制切到 X11。删除源码目录时可以一并删除上述源码版桌面入口，不涉及聊天数据。

### 从旧后端升级

先退出旧版整套进程，再执行 setup、build 和 start。Go 网关和 8081 gRPC 服务已移除，无需修复 GOROOT。保留原配置和 SQLite 路径，会话、任务、审批和 LangGraph checkpoint 原地沿用；不在新旧版本之间同时共享打开数据库。Windows 凭证通过受限兼容读取迁移，Linux/macOS 沿用原服务/账号；系统密钥链需可访问，不退回明文。此迁移未做实机验收。

### Electron installation：编译成功但运行时缺失

`electron:compile` 只编译主进程和 preload 的 JavaScript，不负责安装 Electron 可执行文件。
若启动报告 `Electron failed to install correctly`，不要反复构建，也不必先删除整个 node_modules。

当前依赖为 Electron 44、electron-builder 26，确切版本由 `web/package-lock.json` 锁定。Electron 42 起不再通过 npm postinstall 下载运行时；`npm ci` 和 `npm rebuild electron` 均不能代替显式安装。
`npm run electron:install` 通过 Node 的原生环境代理选项调用 Electron 官方 `install-electron` 入口，沿用 HTTP(S)_PROXY / NO_PROXY、TLS 与校验和验证。当前安装器使用 `@electron-internal/extract-zip`；旧 `extract-zip → yauzl` 覆盖已删除。
参见 [Electron 安装行为变化](https://www.electronjs.org/docs/latest/breaking-changes#behavior-changed-electron-no-longer-downloads-itself-via-postinstall-script)。

首次获取源码时，按 setup → build desktop → start desktop built 的顺序执行即可。锁文件随仓库分发，不依赖开发机的缓存，也不需要手工修改 node_modules。
已安装旧依赖的环境，先更新仓库，再在项目根目录执行：

```bash
bash scripts/setup.sh electron
```

```powershell
.\scripts\setup.ps1 electron
```

修复入口用 `npm ci --ignore-scripts` 重建锁定的 Web 依赖树，再显式执行官方安装器，不重装 Python。安装器复用有效缓存，缺失时下载并校验文件；不伪造 `path.txt`，不关闭校验。该入口也可用于从 Electron 28 升级；随后仍须重新构建桌面代码。新机器应执行完整 setup。

完整 setup 在 npm ci 后也执行显式 Electron 安装。构建矩阵使用相同步骤；只做前端静态检查的 CI 不下载二进制。

setup 检查 `ELECTRON_INSTALL_PLATFORM` / `ELECTRON_INSTALL_ARCH` 及对应 npm 覆盖值，拒绝与本机冲突的目标；`setup python` 不做这些检查。Electron 44 已不使用 `ELECTRON_SKIP_BINARY_DOWNLOAD`，前端 CI 也不再依赖它。

启动检查只读安装器生成的 `path.txt`、实际可执行文件及 `dist/version`，同时核对依赖锁中的版本，不调用可能自动下载的 `require('electron')`。因此依赖缺失或版本过期时，成套启动会要求先 setup，不会在启动中安装软件。

下载使用已有的网络和代理设置。不要通过禁用 TLS 或 checksum 校验来解决网络错误。

涉及 Electron 的 setup 会显示安装脚本输出，结束前会检查 Electron 可执行文件是否非空、是否可执行，以及 dist/version 是否与 npm 包版本一致；显式使用 ELECTRON_OVERRIDE_DIST_PATH 时，由调用者管理版本。桌面 build/start 也会在编译或创建服务前检查，不只依据 npm 的退出码判断安装是否完成。`setup python`、Web build/start 不要求 Electron。
仅修复 Electron 下载、且已有成功构建记录与当前源码/锁文件一致时，可直接使用 `start.sh desktop built` / `start.ps1 desktop built`。若修复过程更新了锁文件或源码已变化，启动器会要求先重新构建。

### 生命周期

- Web 入口管理 Python 后端与开发模式的 Vite；桌面入口管理 Electron 和可选 Vite，由 Electron 管理 Python 后端。默认不连接旧实例。
- 端口冲突在服务创建前报错，Core 禁止 reuseport 并在恢复业务库前绑定端口；不会按名称杀掉其他程序。
- 子进程创建与联动退出复用 concurrently，强制清理复用 tree-kill / 操作系统进程组，不建立另一个进程管理框架。
- Ctrl+C 或任一组件退出会停止本次拥有的其他组件。Web 标签页关闭不代表退出。桌面关窗口隐藏到托盘，托盘“退出”才关闭整套。
- 启动器保留 stdin 管道；Python 后端收到 EOF 后执行现有关闭逻辑。Windows 也先通过 EOF 通知进程清理，而不是立即 taskkill；超过等待上限才强制结束本次启动的进程。强制结束不能保证任务完成清理。
- Uvicorn 处理 SIGINT/SIGTERM 并退出 HTTP/WS，lifespan 等待调用清理后关闭 Core。重复桌面退出不会跳过已经开始的清理。
- `scripts/dev.sh` 是 `start web dev` 的兼容快捷方式。在 web 目录执行 `npm run electron:dev`，也会调用成套桌面入口。
- 成套入口拒绝 `VITE_MOCK=1` / `MAIGOKITE_EXTERNAL_RUNTIME=1`。UI 单独联调可在 web 目录运行 Vite 和显式 `npx electron .`；这些外部服务仍由各自启动者负责。

Electron 成套启动会为其后端生成一次性随机凭据，不需要用户配置。独立调试 built `file://` 页面与外部后端时，必须在启动两者前提供相同的 `MAIGOKITE_DESKTOP_TOKEN`（32 字节随机值的 64 位小写十六进制编码）；不持久保存或打印该值。后端会消费并移除环境项，不向工具继承。没有凭据时不允许通过 `Origin: null` 连入，不能通过恢复 CORS 的无条件 null/file 放行来兼容旧客户端。`VITE_MOCK=1` 的纯 UI 调试不调用真实 API，不需要这个凭据。

PowerShell 禁止执行脚本时，确认源码可信后可使用进程级 `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1 web`；脚本不更改全局执行策略。
这些脚本不自动运行软件测试。脚本与调用链已做静态核对，但 Windows 实机运行和整套退出仍待验收；静态检查不能代替启动验证或安装包验收。

配置、生效范围和凭证管理见 [配置指南](../user-guide/Configuration.md)。

## 4. 质量与构建入口

测试源码、Vitest 配置和 `scripts/run_tests.sh` 随 Git 分发；生成的报告与缓存不提交。新克隆需先安装对应开发依赖，再执行下表中的测试入口。未执行或收集失败的测试不能记为通过，覆盖范围与证据口径见 [测试与验收](../testing/TestPlan.md)。

生产构建使用 `vue-tsc --noEmit -p tsconfig.build.json`，保留应用、Electron 与 Vite 配置的严格类型检查，不纳入测试文件或 Vitest 配置。生产构建成功不能替代测试通过。

| 入口 | 范围 |
| --- | --- |
| `.venv/bin/python -m pytest python/tests scripts/acceptance --ignore=python/tests/spike/test_runtime_spike_live.py`（根目录） | 无模型凭据的 Python 单元/集成测试与运行时验收门禁 |
| `cd web` 后 `npm run test:run` | Vue/Vitest |
| `scripts/run_tests.sh` | 聚合运行 Python 测试、运行时验收和 Vue/Vitest；随 Git 分发 |
| [GitHub Actions CI](../../.github/workflows/ci.yml) | PR 与主分支检查、Linux/Windows 源码构建；具体门禁见 [测试与验收](../testing/TestPlan.md#github-actions) |
| `build.sh all` / `build.ps1 all` | Vue/Electron；Python 分发包另用 python 目标，不是完整桌面发布 |

全源码静态检查还覆盖预留的向量/检索适配器，因此检查环境须安装 `dev` 和 `vectorstores` 两组锁定依赖，与 CI 一致：

```bash
uv sync --locked --extra dev --extra vectorstores --no-default-groups
uv run --no-sync ruff check python/
uv run --no-sync mypy python/agent
```

这不会启动向量服务或把预留模块接入 Core；普通应用安装仍不需要这些可选依赖。检查环境可以通过 `UV_PROJECT_ENVIRONMENT` 放在独立目录，避免改变日常运行环境。不能用全局忽略缺失导入替代完整检查环境。

报告须写明检查范围与实际结果；检查失败应保留非零退出码，不能记为“工具缺失”或“跳过”。平台限制和安装包验收要求见 [构建与发布指南](../deployment/ReleaseGuide.md)。

HTTP 请求模型在 `python/agent/api/contracts.py`，FastAPI 提供 `/openapi.json`；不再维护 Proto 与跨语言生成代码。事件 DTO 继续见 [API 设计](../design/API.md)。新增持久字段仍须覆盖空库与旧库升级。

## 5. 修改规则

- Python：公开边界写类型，异步资源由明确生命周期持有，取消后等待资源清理；不创建通用 DI、插件或取消框架替代已有组件。
- Vue：使用 TypeScript 与清晰的组件/store 边界；卸载清理订阅，渲染不可信 Markdown 前消毒，渲染进程不获 Node/Shell 权限。
- 工具、副作用、权限、恢复修改必须覆盖对应失败路径；日志不记录敏感凭证、完整环境和隐藏思维链。
- 成熟实现优先。新增自研基础设施须说明替代调查、关键需求和长期维护收益，不能因已有代码就默认沿用。

### Python 编码约束

- 以根目录 `pyproject.toml` 为准：Python 3.14、Ruff 行长 120、现有 lint 规则及 strict mypy。新增代码同样遵守，不通过增大行长、放宽类型检查或排除文件消除诊断。
- 导入按现有 Ruff `I` 规则分组排序；括号内多行导入每个名称独占一行。长条件、生成器表达式和调用参数用括号分行，不把多个处理步骤挤在一行。
- 同一函数内的变量保持明确的业务含义和类型。`for` 不创建独立作用域；字符串路径与 `Path`、不同业务记录不得共用一个变量名。路径运算使用 `Path`，在命令参数或序列化边界再转为字符串。
- 固定业务记录复用已有 TypedDict、dataclass 或 Pydantic 契约，动态 JSON 使用已有 JSON 类型。不得用 `Any`、无依据的 `cast`、`type: ignore` 或 `noqa` 掩盖本可修复的类型/格式错误。确有上游类型缺陷的例外，须说明证据、范围及移除条件。
- 注释说明代码做什么、为何需要该边界或限制；不以“薄封装”“不是另一套 Runtime”等声明代替实际设计约束。

### 前端编码约束

- 以 `web/.eslintrc.cjs`、`web/.prettierrc` 为准。属性换行、缩进和引号交给现有格式化工具，不能靠降低规则级别消除诊断。
- 在 `web` 目录运行 `npm run lint` 只读检查全部前端，要求零错误、零警告；对指定手写源码也可运行 `npm run lint:check -- <文件路径...>`，格式另用 `npm run format:check -- <文件路径...>`。需要修改时显式运行 `npm run lint:fix -- <文件路径...>` 或 `npm exec -- prettier --write <文件路径...>`，不要用全目录写入覆盖无关改动。
- CI 全量 lint 使用 `--max-warnings=0`，不能用“文件未改动”豁免存量警告；另外对 PR/push 改动的 `src`、`scripts` 下 JS/TS/Vue 手写文件执行零警告与格式检查，手动触发时检查最近一次提交。生成的 `src/api/generated` 由契约生成器维护，不手动格式化。这是静态门禁，不会在本地自动执行测试。
- ESLint 与 Prettier 同时通过才算格式收尾；复杂模板表达式产生规则冲突时，提取有业务含义的计算值，不关闭缩进规则。`v-html` 仅允许在已核对的 DOMPurify 净化边界处声明局部例外，说明净化函数并立即恢复规则，不全局关闭安全提醒。
- 固定 API 响应复用生成的传输类型与校验器，开发 Mock 同样遵守；用 `satisfies` 检查固定响应，不能通过类型断言或放宽校验绕过缺失字段。

### 改动收尾

- 逐项核对本次 diff 的导入、行长、变量类型与调用方契约，并检查取消、异常和资源所有权是否因修改而改变。格式修正不夹带权限策略或执行逻辑变更。
- 静态检查与行为测试分别报告。使用第 4 节既有入口和 CI，不新增自动测试钩子；在用户限制执行检查或测试时遵守限制，完成源码审阅并明确记录未运行项，不宣称门禁通过。
- 已知诊断必须修复或明确列为未解决项，不能沿用旧提交的通过记录为新改动背书。保留其他会话的未提交改动，不顺手格式化无关文件或降低测试断言。

## 6. 提交与文档

使用短生命周期目标分支，保持改动可审阅；Conventional Commits 可用 `feat/fix/docs/refactor/test/chore/perf`，scope 按模块。不得把示例 Git reset/force push 当作自动清理步骤。

提交或 PR 须说明改动、验证范围、未验证项，以及对兼容性和回滚的影响。协议变化更新 API，表变化更新 Database，运行机制变化更新 AgentRuntime，验收状态更新 Roadmap；不要另建内容重复的文档。只有实测证据才能标记“通过”，编译和测试不能代替发布包验收。
