<div align="center">
  <img src="web/public/brand/app-icon-cat-ribbon-v1.png" alt="MaigoKite 应用图标" width="128" height="128">
  <h1>MaigoKite</h1>

  <p>
    MaigoKite 是一个运行在你电脑上的个人 Agent。<br>
    用对话处理文件、查资料、操作网页，或通过本地 Skill 和 MCP 扩展工具；模型由你选择，文件访问范围和操作审批由你控制。
  </p>

  <p>
    <a href="https://github.com/NanamiKite/MaigoKite/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/NanamiKite/MaigoKite/ci.yml?branch=master&amp;style=flat&amp;label=CI" alt="CI"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MPL--2.0-blue?style=flat" alt="License"></a>
  </p>

  <p>
    <a href="#快速开始">快速开始</a> ·
    <a href="#功能">功能</a> ·
    <a href="#文档">文档</a> ·
    <a href="https://github.com/NanamiKite/MaigoKite/issues">提交 Issue</a>
  </p>
</div>

![MaigoKite 首页截图](docs/images/dashborad.jpg)

你可以让它：

- 阅读工作区里的 Markdown，把资料整理成笔记
- 搜索公开网页，把结论和来源链接写回文件
- 操作网页表单、截图，或修改文件后查看 Git diff

## 快速开始

目前需要从源码运行，提供 Web 和 Electron 桌面两个入口，二选一，不要同时启动；独立安装包尚未就绪。

### 准备环境

- Python 3.14.x
- Node.js 22.22.2+（22.x）、24.15.0+（24.x）或 26+ / npm
- ripgrep（`rg`，安装在工作区之外的绝对 PATH 目录）

推荐安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)，让 setup 按锁文件安装 Python 依赖。依赖须在运行应用的机器上安装，不要复制其他机器的 `.venv` 或 `node_modules`。详细要求见 [开发指南](docs/development/DevelopmentGuide.md#2-初始化)。

以下命令在仓库根目录执行，以桌面入口为例。setup 安装 Python 运行依赖及 Web/Electron 工具，首次使用或依赖变化时执行；更新前先退出应用。

### Linux

```bash
bash scripts/setup.sh &&
  bash scripts/build.sh desktop &&
  bash scripts/start.sh desktop built
```

### Windows

使用 PowerShell 5.1/7，无需 WSL、Bash 或 Make：

```powershell
.\scripts\setup.ps1
if ($LASTEXITCODE -eq 0) {
  .\scripts\build.ps1 desktop
  if ($LASTEXITCODE -eq 0) { .\scripts\start.ps1 desktop built }
}
```

<details>
<summary><b>只使用 Web</b></summary>

执行对应平台的 setup，成功后不启动桌面，改用以下命令。

Linux：

```bash
bash scripts/build.sh web && bash scripts/start.sh web built
```

Windows PowerShell：

```powershell
.\scripts\build.ps1 web
if ($LASTEXITCODE -eq 0) { .\scripts\start.ps1 web built }
```

就绪后打开 <http://127.0.0.1:8080>，页面和 API 均由 Python 后端提供，不需要另起 Web 服务。两个入口都只用于本机，不要把端口暴露到公网。

</details>

### 日常启动与退出

- 日常只需执行对应的 `start ... built`，不用每次安装或构建。源码或锁文件变化导致构建过期时，启动器会提示重新构建；构建失败后不要继续启动。
- `Ctrl+C` 停止本次启动的整套服务。关闭浏览器标签页不会停止服务；桌面关闭窗口会隐藏到托盘，用托盘“退出”关闭整套服务。
- 遇到端口占用，先退出旧实例；脚本不会结束其他进程。Electron 安装不完整等问题见 [常见启动问题](docs/user-guide/Installation.md#3-常见启动问题)。

<details>
<summary><b>沙箱、浏览器工具与升级</b></summary>

- **Linux 受限命令**需要支持隔离参数的 bubblewrap；不可用时会报错，不会退回宿主执行。普通文件搜索不依赖命令沙箱，也不要求完全访问，见 [工作区工具](docs/user-guide/Configuration.md#工作区工具)。SDK 目录和构建缓存配置见 [命令工具链与缓存](docs/user-guide/Configuration.md#linux-命令工具链与缓存)。
- **Windows 原生命令沙箱**仍为 alpha，默认关闭，尚未完成实机验收。未启用时拒绝受限命令，不自动切换为完全访问。安装需明确确认系统账户和防火墙更改，普通 setup/start 不做这些更改，见 [Windows 沙箱配置](docs/user-guide/Configuration.md#windows-原生命令沙箱)。
- **浏览器工具**需要单独安装 Playwright 的 Chromium，或配置已有 Chrome / Chromium；不会继承日常浏览器的登录状态。安装见 [开发指南](docs/development/DevelopmentGuide.md#路径与解释器)，路径设置见 [浏览器运行时](docs/user-guide/Configuration.md#71-浏览器运行时)。
- **升级 Python 环境**前先退出应用，再按 [初始化说明](docs/development/DevelopmentGuide.md#2-初始化) 更新依赖。旧版后端的数据与凭证处理见 [从旧后端升级](docs/development/DevelopmentGuide.md#从旧后端升级)。

</details>

## 第一次使用

1. 到 **设置 → 模型** 添加服务，填写 API 基础地址、实际模型 ID 和服务要求的 API Key；测试连接成功后保存，再选择默认模型。支持 OpenAI、Anthropic、Ollama 和 OpenAI-compatible API。
2. 确认工作区和权限。默认目录是项目根目录下的 `.workspace`，也可从侧栏加号添加已有文件夹；初次尝试可选择“只读”。
3. 发起一个不改文件的任务，先确认模型能调用工具：

   ```text
   列出当前工作区的文件和子目录，先不要修改任何内容。
   ```

更换地址或接口类型后需要重新填写 API Key，旧凭证不会发到新端点。配置步骤见 [模型设置](docs/user-guide/Configuration.md#3-模型)。

## 功能

### 文件与权限

读写和搜索工作区文件、预览内容、查看 Git diff，工具执行过程在活动区查看。

权限分为只读、工作区访问和完全访问，需要审批的操作会先请求确认。“只读”只限制自动执行范围，批准后仍可写入；完全访问会取消工作区沙箱与逐项审批，命令以当前系统用户身份执行。详见 [权限模式](docs/user-guide/Configuration.md#6-权限模式)。

### 附件、看图与生图

聊天可添加文本、PDF、DOCX、XLSX、PPTX，或引用工作区文件快照；文档可按页或结构读取、查找和预览。图片需要支持看图的模型；本机 Tesseract 可提取文字，但不能代替图表理解。细节见 [看图、生图与文件预览](docs/user-guide/Configuration.md#12-看图生图与文件预览)。

联网搜索需确认搜索服务可用；生图需单独配置 Images 或 Responses 服务，两者都不会自动借用聊天密钥。

### Skill 与 MCP

侧栏“扩展”管理本地 `SKILL.md` 和 stdio MCP Server。Skill 提供任务指令，本身不执行代码；MCP 接入外部工具，服务程序以当前用户身份运行，请只使用可信来源。保存后点击“应用并检查连接”；有运行中或等待交互的任务时，先完成或取消。详见 [技能与 MCP](docs/user-guide/Configuration.md#8-技能与-mcp)。

仓库另有独立安装的 [天线设计扩展](extensions/antenna_design/README.md)，通过 MCP + Skill 搜索导线尺寸、计算方向图并导出报告和模型文件，不随主应用安装。结果是候选方案，制作前仍须核对材料、带宽、匹配和机械条件。

## 数据与隐私

会话、任务、审批和运行事件保存在本地 SQLite，默认数据目录为 `~/.maigokite`，工作区文件保留在所选目录。API Key 使用操作系统凭证存储，普通配置接口不返回明文密钥。

**本地优先，但不是离线运行。** 使用本机 Ollama 时，模型请求可以留在本机；使用云模型、联网搜索或访问网络的 MCP 时，相关内容仍会发送给对应服务。

配置与路径覆盖见 [配置指南](docs/user-guide/Configuration.md#1-配置层级)。备份前请阅读 [备份与恢复](docs/ops-security/Backup.md)，不要直接复制活动中的 SQLite 文件。

## 限制与当前状态

- 模型「测试连接」只检查服务可达；工具调用、看图等能力以实际任务为准。
- 中断任务只有满足恢复条件时才能继续；浏览器页面不能跨后端重启保留。取消远端 MCP 操作后，重试前先核对实际结果，见 [断线、停止与重启](docs/user-guide/Installation.md#4-断线停止与重启)。
- 个人记忆需要手工维护，同时开启文件夹和对话开关后才使用；没有自动提取和语义检索。
- 子 Agent 只支持单层只读委派；持久工作流、手机远程访问和 Docker/服务器部署尚未提供。
- 文档、页图和 OCR 读取较新，还在实际使用中验证；完整 Office 版式、live 引用、图生图及音视频暂不支持。
- Windows 整套启停、真实模型与跨平台回归不完整；桌面安装包尚未就绪。

功能进度和已知问题见 [交付路线与状态](docs/project/Roadmap.md)，近期变化见 [更新日志](docs/project/Changelog.md)。

## 项目结构

| 路径 | 内容 |
| --- | --- |
| `python/agent/api/` | FastAPI HTTP/WebSocket、系统凭证、工作区文件与 Git 查询 |
| `python/agent/` | Python Core、Agent Runtime、Tools、权限和持久化 |
| `web/` | Vue 前端、Electron |
| `extensions/antenna_design/` | 独立安装的天线计算包与可选 MCP 服务 |
| `experiments/` | 开发实验与兼容性评估，不参与应用默认启动 |
| `scripts/` | Linux / PowerShell 成套安装、构建和启停入口 |

## 开发与贡献

开发环境、热更新与质量检查见 [开发指南](docs/development/DevelopmentGuide.md)。测试入口为 `scripts/run_tests.sh`；[GitHub Actions CI](.github/workflows/ci.yml) 检查 Python、前端及 Linux/Windows 源码构建，范围见 [测试与验收](docs/testing/TestPlan.md)。

提交 Issue 或 PR 时，请附复现步骤、实际与预期行为、运行环境和必要的脱敏日志。不要提交 API Key、个人数据、运行数据库或构建产物。

## 文档

| 想做什么 | 从这里开始 |
| --- | --- |
| 设置模型、权限、工具与扩展 | [配置指南](docs/user-guide/Configuration.md) |
| 排查启动或运行问题 | [安装与首次使用](docs/user-guide/Installation.md)、[日志与诊断](docs/ops-security/Observability.md) |
| 备份或迁移数据 | [备份与恢复](docs/ops-security/Backup.md) |
| 修改源码、运行检查 | [开发指南](docs/development/DevelopmentGuide.md) |
| 理解组件与任务执行 | [系统设计](docs/design/SystemDesign.md)、[运行时设计](docs/design/AgentRuntime.md) |
| 查看已交付能力和已知问题 | [交付路线与状态](docs/project/Roadmap.md) |

其他主题见 [完整文档索引](docs/README.md)。

## License

Copyright (c) 2026 NanamiKite，采用 [Mozilla Public License 2.0](LICENSE)（`MPL-2.0`）。第三方依赖保留各自许可证。
