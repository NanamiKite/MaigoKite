# MaigoKite

MaigoKite 是一个运行在你电脑上的个人 Agent，用来处理文件、查资料、操作网页，也可以通过本地 Skill 和 MCP 定制自己的工作流。你选择模型，并决定它能访问什么、哪些操作需要确认。

比如你可以让它：

- 阅读一个文件夹，整理里面的 Markdown 和资料
- 搜索公开网页，把结果和来源写回文件
- 修改本地文件，并在界面里查看 diff
- 点击网页、填写表单或截图

项目仍在开发中，提供 Web 和 Electron 桌面入口。暂时需要从源码运行，还没有独立安装包。

## 模型、工具与工作区

支持 OpenAI、Anthropic、Ollama 和 OpenAI-compatible API。可以添加多个模型，设置默认模型，也可以为不同会话选择模型。

源码启动时会创建项目根目录下的 `.workspace` 作为默认工作区，不会自动搬入旧文件。也可以用侧栏加号添加已有文件夹，读写、搜索文件并预览内容、查看 Git diff。

你可以选择只读、工作区访问或完全访问。需要审批的操作会先显示给你确认，工具调用过程可在活动区查看。完全访问会取消工作区沙箱与逐项审批，请按任务需要选择。

浏览器工具使用 Playwright，需要配置 Chromium，不会自动继承日常浏览器的登录状态。

侧栏“扩展”用于管理本地 `SKILL.md` 和 stdio MCP Server。Skill 提供任务指令，本身不执行代码；MCP 接入外部工具。保存后需显式应用配置，有运行或待处理交互时须先完成或取消任务。

修改默认目录、权限和扩展的操作步骤见 [配置指南](docs/user-guide/Configuration.md)。

## 运行

### 环境要求

- Python 3.14.x（项目固定使用 3.14 系列）
- Node.js 22.22.2+（22.x）、24.15.0+（24.x）或 26+ / npm
- ripgrep (`rg`，安装在工作区之外的绝对 PATH 目录)

推荐安装 [uv](https://github.com/astral-sh/uv)，以便按锁文件安装 Python 依赖。请在运行应用的机器上安装依赖，不要复制其他机器的 `.venv` 或 `node_modules`。详细环境要求见 [开发指南](docs/development/DevelopmentGuide.md)。

Linux 受限命令需要支持隔离参数的 bubblewrap。版本或系统不支持时会显示具体提示，不会自动改为宿主执行。普通 `glob/grep` 文件搜索不依赖命令沙箱，也不要求开启完全访问，见 [工作区工具](docs/user-guide/Configuration.md#工作区工具)。

SDK 位于用户目录时，可配置精确目录的只读访问，无需开放整个用户目录。Linux 构建缓存按工作区复用，配置见 [命令工具链与缓存](docs/user-guide/Configuration.md#linux-命令工具链与缓存)。

升级 Python 环境前先退出应用，按 [开发指南](docs/development/DevelopmentGuide.md#2-初始化) 更新依赖；启动脚本不会自动替换旧环境。

### Linux

在仓库根目录，首次使用时安装依赖，再构建并启动桌面：

```bash
bash scripts/setup.sh                # 首次使用，或更新依赖后执行
bash scripts/build.sh desktop && bash scripts/start.sh desktop built
```

如果只使用 Web，在执行 setup 后改用：

```bash
bash scripts/build.sh web && bash scripts/start.sh web built
```

### Windows

使用 PowerShell，在仓库根目录执行，无需 WSL、Bash 或 Make：

```powershell
.\scripts\setup.ps1                  # 首次使用，或更新依赖后执行
# 确认 setup 成功后：
.\scripts\build.ps1 desktop
if ($LASTEXITCODE -eq 0) { .\scripts\start.ps1 desktop built }
```

如果只使用 Web，在执行 setup 后改用：

```powershell
.\scripts\build.ps1 web
if ($LASTEXITCODE -eq 0) { .\scripts\start.ps1 web built }
```

Windows 命令工具支持 PowerShell 7 / 5.1。可选的 SRT 原生沙箱仍处于 alpha，默认关闭，尚未完成 Windows 实机验收；未启用时拒绝受限命令，不自动切换为完全访问。它不依赖 WSL，首次安装需确认系统账户和防火墙更改，普通 setup/start 不做这些更改。步骤见 [Windows 沙箱配置](docs/user-guide/Configuration.md#windows-原生命令沙箱)，编码差异见 [PowerShell 说明](docs/user-guide/Configuration.md#windows-powershell-命令工具)。

`setup` 安装 Python 运行依赖及 Web/Electron 工具。执行前先退出应用；仅更新后端或安装开发工具的选项见 [开发指南](docs/development/DevelopmentGuide.md#2-初始化)。

Web 和桌面入口二选一，不要同时启动。日常使用只需执行对应的 `start ... built`，不用每次重装或构建。修改前端或 Electron 源码后重新构建；需要热更新开发时，省略 `built`，使用 `start.sh web|desktop` 或 `start.ps1 web|desktop`。

构建失败后不要继续启动。`built` 会检查构建记录和当前源码，产物缺失或过期时须重新构建。

Web `built` 地址为 `http://127.0.0.1:8080`，页面和 API 都由 Python 后端提供；开发模式地址为 `http://127.0.0.1:5173`，Vite 代理同一个后端。桌面 `built` 直接打开本地界面。两种入口都只用于本机，不要直接把端口暴露到公网。

旧版后端的升级、数据沿用和凭证限制见 [从旧后端升级](docs/development/DevelopmentGuide.md#从旧后端升级)。

`Ctrl+C` 停止本次启动的整套服务。关闭浏览器标签页不会停止服务；桌面关闭窗口会隐藏到托盘，用托盘“退出”关闭整套服务。端口占用时先退出旧实例，脚本不会替你结束其他进程。

启动失败时先查看 [常见启动问题](docs/user-guide/Installation.md#3-常见启动问题)，包括 Electron 安装不完整、端口冲突和模型连接问题。

这些命令仍依赖本机开发环境，不会生成自带 Python 等运行资源的独立安装包。发布限制见 [构建与发布指南](docs/deployment/ReleaseGuide.md)。

### 浏览器工具

安装 Playwright Chromium，或通过 `browser.executable_path` 指定已有 Chrome / Chromium。留空时使用 Playwright 管理的 Chromium，不会自动寻找固定路径的 Chrome。步骤见 [开发指南](docs/development/DevelopmentGuide.md#路径与解释器) 和 [配置指南](docs/user-guide/Configuration.md#71-浏览器运行时)。

## 第一次使用

启动后，先到 **设置 → 模型** 添加模型服务。填写 API 地址、模型 ID，以及服务要求的 API Key。测试连接后保存，选择默认模型，再确认工作区路径和权限模式。

例如，将本项目目录添加为工作区后，可以输入：

```text
读取 README.md，告诉我这个项目现在主要在做什么。
```

需要查资料时，先在工具设置中确认搜索服务可用，再让 Agent 将结果和来源写回文件。不同模型对工具调用的支持不同，连接成功不代表工具调用可用。

## 数据

会话、任务、审批和运行事件保存在本地 SQLite 中。API Key 使用操作系统凭证存储，普通配置接口不会返回明文密钥。

“本地优先”不等于离线运行：

- Ollama 可以完全运行在本机
- OpenAI / Anthropic 等服务会向对应 API 发送模型请求
- Web 搜索会访问配置的搜索服务
- MCP 是否访问网络取决于对应 MCP Server

数据目录为 `~/.maigokite`。配置位置和项目标识见 [配置指南](docs/user-guide/Configuration.md#1-配置层级)，备份前请阅读 [备份与恢复](docs/ops-security/Backup.md)。

## 当前状态

项目还没有进入稳定发布阶段。使用前请留意这些限制：

- 执行内核和 UI 的部分新增功能尚未完成回归验证
- 任务停止或失败后，不是所有情况都能从原位置恢复
- 远程 MCP 操作不一定能被本地取消立即终止
- 浏览器会话不能跨 Core 重启恢复
- 手工记忆的请求注入已接入源码，需同时开启文件夹和对话开关，尚未验收；自动提取和语义检索未实现
- 子 Agent、持久工作流和手机远程访问还没有完整交付

历史数据的恢复限制见 [断线、停止与重启](docs/user-guide/Installation.md#4-断线停止与重启)。功能进度与验证记录见 [交付路线与状态](docs/project/Roadmap.md)，版本变化见 [更新日志](docs/project/Changelog.md)。

## 项目结构

| 路径 | 内容 |
| --- | --- |
| `python/agent/api/` | FastAPI HTTP/WebSocket、系统凭证、工作区文件与 Git 查询 |
| `python/agent/` | Python Core、Agent Runtime、Tools、权限和持久化 |
| `web/` | Vue 前端、Electron |
| `scripts/` | Linux / PowerShell 成套安装、构建和启停入口 |

## 开发

构建：

```bash
bash scripts/build.sh all
```

开发环境和质量检查命令见 [开发指南](docs/development/DevelopmentGuide.md)。测试源码、配置和运行脚本随仓库分发，安装开发依赖后可运行 `bash scripts/run_tests.sh`。默认使用模拟模型与本地 HTTP/SSE 服务，不需要模型凭据，也不调用付费模型。

[GitHub Actions CI](.github/workflows/ci.yml) 在 PR、主分支推送和手动触发时检查 Python、前端及 Linux/Windows 源码构建。测试覆盖范围和安装包验收要求见 [测试与验收](docs/testing/TestPlan.md)。

提交 Issue 或 PR 时，请附上复现步骤、实际行为、预期行为和必要的脱敏日志。不要提交 API Key、个人数据、运行数据库或构建产物。

## 文档

模型、权限、工作区和扩展设置见 [配置指南](docs/user-guide/Configuration.md)。遇到问题可查看 [安装与首次使用](docs/user-guide/Installation.md) 和 [日志与诊断](docs/ops-security/Observability.md)。架构与其他文档从 [文档索引](docs/README.md) 进入。

## License

Copyright (c) 2026 NanamiKite.

Licensed under **Mozilla Public License 2.0** (`MPL-2.0`).

See [LICENSE](LICENSE).

Third-party dependencies retain their respective licenses.
