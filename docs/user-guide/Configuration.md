# MaigoKite 配置指南

> **状态**：当前有效
> 本文说明当前配置方式。功能进度、已知问题和测试范围见 [交付路线与状态](../project/Roadmap.md)。

配置字段最终以 ConfigManager、实际默认配置和当前 API 为准；并非所有历史字段都已接线。本文解释用户可见的配置，不复制内部结构体。

## 1. 配置层级

优先级从低到高：

```text
内置默认值 → 配置文件 → 部署环境覆盖 → 工作区设置 → 当前会话设置
```

敏感凭证不写入普通配置；配置只保存凭证引用。

配置文件的位置取决于启动方式：

| 启动方式 | 读取哪个文件 |
| --- | --- |
| 源码脚本或源码桌面入口 | 优先读取 `MAIGOKITE_CONFIG` 指定的文件，否则读取项目内的 `config/config.dev.yaml` |
| 直接启动 Python CLI | `--config` 优先于 `MAIGOKITE_CONFIG`；两者都不指定时使用代码默认值，不自动加载开发 YAML |
| Electron 安装版分支 | 优先读取 `MAIGOKITE_CONFIG`；否则尝试读取 `resources/config/config.prod.yaml`，不存在时使用代码默认值 |

每次只读取选中的一份 YAML，不会把开发或生产配置自动叠加到 `config/config.yaml`。编辑前先确认实际启动使用的文件。界面保存的模型、偏好和扩展设置主要进入 SQLite，不回写 YAML；也不会自动在 AppData 生成一份用户 YAML。安装版路径是代码约定，完整安装包仍待验证，见 [发布指南](../deployment/ReleaseGuide.md)。

### 默认工作区

默认使用 `<安装目录>/.workspace`，源码启动时对应 `<项目根目录>/.workspace`。目录在 Core 启动时创建，不依赖终端当前所在位置，不把应用源码或资源包作为默认文件操作范围。

- 路径优先级：`MAIGOKITE_WORKSPACE_ROOT` → 非空 `workspace.root` → 安装目录下的 `.workspace`。随附 YAML 的 `workspace.root: ""` 表示自动默认；显式 `"."` 仍是 Core 启动工作目录，不会偷偷改写。
- 桌面与开发启动器通过 `MAIGOKITE_INSTALL_DIR` 传递同一个安装根目录；无需普通用户设置。自行部署时可指定绝对安装根目录，也可直接指定绝对工作区路径。文件与运行状态接口继续以 Core 返回的已登记路径为准。
- 桌面安装版：Windows/Linux 可执行文件所在目录；AppImage 原始文件所在目录（不是只读挂载点）；macOS 为 `.app` 所在目录（不是签名包内部）。如果该位置不可写，启动会报错，请使用可写安装位置或显式工作区，不自动提权或改用主目录。安装版路径逻辑尚未经过跨平台安装验收。
- 先结束或停止任务再重启使默认路径变更生效。旧文件不迁移，未指定文件夹的对话会使用新默认目录；需要继续使用旧目录时，将其添加为文件夹工作区并移动对应对话。数据库、登录凭证与已登记的其他工作区不变。

### 项目标识

数据和技能目录位于 `~/.maigokite`，项目环境变量使用 `MAIGOKITE_*`，
密钥链命名空间为 `MaigoKite/`，前端偏好缓存键使用 `maigokite_` 前缀。
Electron 使用 `MaigoKite` 用户目录与 `com.electron.maigokite-web` 应用 ID。
Python 命令为 `maigokite-agent`。开发阶段不保留旧名称别名或自动迁移；模型与联网凭证需要重新配置。

## 2. 界面称呼

打开“设置 → 通用”，可修改对话标题栏、欢迎页和输入框中显示的助手称呼。称呼长度为 1–32 个字符，按工作区中的当前用户保存；不会改变项目名、工作区名或模型身份。

## 3. 模型

一个工作区可以保存多个聊天模型配置，并选择一个默认模型。“设置 → 模型”显示已配置的服务、凭证状态和默认标记。点击服务行编辑，或点击“添加模型”配置新服务；同一时间只打开一个编辑器。

- `OpenAI`：官方 API，或带鉴权的 OpenAI-compatible 云服务；自定义服务可填写 API 基础地址。
- `Anthropic`：Claude 官方 API，可选自定义 API 基础地址。
- `OpenAI Compatible`：本地 LM Studio、vLLM 或兼容服务；若服务端启用 Bearer 鉴权，可选填写独立 API 密钥。
- `Ollama`：Ollama 原生接口。

常用本地配置：

| 服务 | API 基础地址 | 模型名称示例 |
|---|---|---|
| Ollama | `http://127.0.0.1:11434` | `qwen2.5` |
| LM Studio | `http://127.0.0.1:1234/v1` | 以 LM Studio 已加载模型 ID 为准 |
| vLLM | `http://127.0.0.1:8000/v1` | 以 vLLM 的 `--served-model-name` 为准 |

选择服务后按以下顺序配置：

1. 填写服务要求的 API 密钥。接口类型、服务预设与基础地址在“自定义设置”中。
2. 点击“从服务获取”，用当前草稿读取模型目录，按名称或 ID 搜索并选择模型。确认选择只更新草稿，不自动保存；目录不可用时可手工输入准确的模型 ID。
3. 点击“测试连接”，验证成功后保存配置。测试本身不保存草稿。

采样温度、输出 Token 和超时采用运行时默认值，常规设置页不要求用户调节。

非敏感设置写入工作区数据库；API 密钥按模型配置 ID 隔离保存在系统密钥链，不写入 YAML、数据库或 API 响应。模型提供方或基础地址变化后必须提供新 Key，旧凭证不会发送到新端点。本地模型提供方不会继承云端 API 密钥，也不会通过系统 HTTP 代理访问回环端口。

默认模型用于新对话。聊天输入框可为当前会话选择其他已保存模型，并按该模型支持的能力调整思考设置；选择保存到会话，不改变工作区默认模型。选择服务默认时不发送思考覆盖参数；不支持调节的模型保持服务默认。思考设置不是输出长度或速率档位，旧 `快速`、`均衡`、`深度` 字段不再改变输出预算。

无桌面的服务器环境可以使用 `MAIGOKITE_MODEL_PROVIDER`、`MAIGOKITE_MODEL_NAME`、`MAIGOKITE_MODEL_BASE_URL` 以及模型提供方对应的 `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`。工作区中已保存的设置优先于 YAML 和这些模型设置环境变量。

模型目录由用户主动获取，不在后台自动发现。多模型自动路由、能力/成本判断和自动降级尚未交付。

模型列表和路由规则见 [模型接入与选择](../agent-capabilities/models/model-routing.md)。

### 3.1 接入路由的默认窗口

设置页中的“窗口容量（可选）”只覆盖当前服务上的对应模型。实际加载窗口优先，其次是该覆盖值、当前服务的容量资料和当前端点对应的模型目录。中转站不根据同名 Claude/OpenAI 模型继承官方目录的窗口。

远端服务没有容量资料时，默认按 **262,144 tokens** 计算；这是运行时假定值，不保证中转站支持该容量。可在当前启动使用的 YAML 中设置这个接入的回退值：

```yaml
models:
  # 原有 chat 配置保持不变
  routes:
    - provider: openai_compatible
      base_url: "https://gateway.example/v1"
      default_context_window: 200000  # 示例：填写此服务实际约定的容量
    - provider: anthropic
      base_url: "https://gateway.example"
      default_context_window: 200000
    - provider: openai_compatible
      base_url: "http://127.0.0.1:1234/v1"
      metadata_api: lmstudio  # 显式声明后才查询 LM Studio 加载窗口与思考能力
```

`provider` 和 `base_url` 必须对应实际接入的协议和基础地址，只去除首尾空白及尾斜杠，不按端口补 `/v1` 或识别产品。`base_url` 必须显式填写；界面留空、地址由 SDK 环境变量提供时，填写 SDK 实际使用的地址。同地址的不同协议是不同路由；同一协议/地址不允许重复配置。容量若填写，必须是正的安全整数。`metadata_api` 可省略；当前仅支持 compatible 路由显式声明 `lmstudio`，不要求同时填写容量默认值。省略 `routes` 或使用 `[]` 时不访问 LM Studio 专属接口。

OpenAI/compatible、Ollama 生成请求默认遵循 `HTTP_PROXY / HTTPS_PROXY / ALL_PROXY / NO_PROXY`；只有 `localhost` 和 loopback IP 自动直连，私网不强制绕过代理。直连仍保留 `SSL_CERT_FILE / SSL_CERT_DIR` 的证书配置。旧 LM Studio 地址若只写到端口，请自行补上服务要求的 `/v1`；没有可靠的旧产品标识时，程序不替用户修改路径。

此字段只在未获得模型容量时回退，不强制限制已知窗口。要覆盖单个模型，在设置页填写它的窗口容量。Ollama 和本地地址没有自动的 262,144 回退：以服务加载数据为准，查不到时保持未知；也可在上面的列表中显式声明一个默认值。

修改 YAML 后需要自行重启 Core，浏览器刷新后获取新容量；不会自动重启正在运行的服务。旧容量缓存不再沿用，但聊天记录和用量不删除。此配置不包含密钥，也不改变服务端的加载窗口。

## 4. 工具选择与联网配置

### Windows PowerShell 命令工具

Agent 通过同一个 `exec_command` 执行命令，不需要另外安装 MCP 工具。默认 `shell=auto` 在 Windows 上优先使用 PATH 中的 PowerShell 7（`pwsh.exe`），找不到时使用系统 Windows PowerShell 5.1（`powershell.exe`）；其他平台仍使用不展开变量或管道的 direct argv。可要求 Agent 显式指定 `shell=pwsh` 或 `shell=powershell`，指定版本不可用时报告错误，不悄悄换解释器。工具结果返回实际选择的 shell 和可执行路径，启动时检查主版本是否符合要求。

例如可对 Agent 说“使用 PowerShell 列出当前工作区文件”。底层调用示例（不是终端命令）：

```json
{
  "command": "Get-ChildItem -LiteralPath . | Select-Object Name,Length",
  "shell": "auto",
  "sandbox_permissions": "use_default",
  "network_access": false
}
```

上例要求先安装并启用下节的原生沙箱；不可用时明确失败，不自动升级。PowerShell 脚本仍按现有不透明操作策略审批，沙箱接入不绕过 PermissionPolicy。确有宿主执行需要时，可以单次请求 `require_escalated` 和 `network_access=true`，**请求不等于授权**；非完全访问模式仍需用户批准。不会因为检测到 Windows 而自动开启完全访问。PowerShell 脚本不能复用“以后都允许某个工具”的授权，现有命令白名单也不会只检查脚本开头。

中文与编码处理：

- 原始脚本通过两版都支持的 UTF-16LE `-EncodedCommand` 传入，避免中文、反斜杠与多层引号被宿主 Shell 再解析；审批展示的仍是原始脚本。
- 仅在新子进程内把 Console 输入/输出和 `$OutputEncoding` 设为 UTF-8，使用 `-NoProfile`，不修改系统代码页、用户 Profile 或执行策略。普通调用使用 `-NonInteractive`；宿主路径的 `interactive=true` 仍需审批，只提供标准输入，不是交互终端。当前 Windows 沙箱不支持交互 stdin，明确拒绝此参数。
- **文件编码独立于命令输出编码**：5.1 的 `>` / `Out-File` 默认 UTF-16LE，`-Encoding UTF8` 带 BOM，且不能使用 `utf8NoBOM`；7 的常规文本输出默认 UTF-8 无 BOM。优先用文件工具，Shell 读写应显式指定所需编码。5.1 执行包含中文的 `.ps1` 文件时，该文件应使用可识别的编码，例如 UTF-8 BOM；命令传输编码不会修复已有脚本文件。
- 外部程序可能忽略这些设置并输出 GBK 等字节。发现无法按 UTF-8 解码时，结果标记 `output_decoding_errors`、提示检查该程序编码并保留原始日志，不自动猜编码或改写文件。PowerShell 在内部已错误解码的文本无法据此可靠检测。

超时与取消使用现有进程管理：原生沙箱通过 SRT 的 Job 生命周期停止并等待 ACL 清理；宿主路径仍通过 `taskkill /T /F` 请求停止所属进程树并等待返回，父进程先退出、后台脱离等情形没有 Job Object 级保证。日志面板支持 Windows 的独立文件句柄读取，不改变子进程的写入偏移。

Python 后端和命令子进程的管道输出显式使用 UTF-8；这不改变 PowerShell 5.1 的文件写入编码，也不自动转换其他程序的 GBK 输出。命令子进程使用隐藏控制台启动，Git 状态查询不再弹出控制台窗口。内部搜索/Python 调用直接传 argv，不再先拼命令字符串再拆分。`shell=direct` 在 Windows 不直接运行 `.cmd/.bat`，此类命令请显式使用 PowerShell。

文件工具和工作区 API 不接受 `C:foo`、`\foo`、`file.txt:stream`、设备名称或尾随点/空格等含糊路径；请使用工作区相对路径，或文件工具支持的完整绝对路径（例如 `C:\项目\说明.txt`）。这不会把 PowerShell 脚本当成可静态分析的文件参数。

尚未完成 Windows 实机验收。待验证：5.1/7 的中文输出与中文/空格路径、管道和错误退出码、UTF-8/BOM 文件差异、审批拒绝与恢复、中文 stdin、超时及 GUI 停止子进程树。Linux direct 的真实进程中文 stdin、EOF、停止及退出后拒绝输入已通过定向验收，不能外推为 PowerShell 或 Windows 子进程树通过。

编码约定依据：[微软编码说明](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_character_encoding?view=powershell-7.5)、[EncodedCommand 参数](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_powershell_exe?view=powershell-5.1#-encodedcommand)、[taskkill](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/taskkill)。

### Windows 原生命令沙箱

源码接入 [Anthropic Sandbox Runtime 0.0.79](https://github.com/anthropics/sandbox-runtime/tree/v0.0.79)，锁定公开 SDK 与配套 helper，不自研 Windows Token/ACL/WFP/Job 实现。上游 Windows 后端仍为 **alpha**；本次没有运行 Windows 安装或实机验收，默认关闭。仅覆盖受限 `exec_command`，不接管 MCP Server、Playwright 或进程内文件工具。

关闭 MaigoKite 和其他使用 SRT 的程序后，在仓库根目录执行：

```powershell
# 只安装锁定依赖；普通 Windows setup 已包含此步，不修改系统权限。
.\scripts\setup.ps1 sandbox
# 先阅读下方边界，再确认系统安装；会弹出 UAC。
.\scripts\sandbox.ps1 install -ConfirmSystemChanges
.\scripts\sandbox.ps1 status
```

在**实际加载的配置文件**中合并以下内容，再重启后端。源码启动默认读取 `config/config.dev.yaml`，指定了 `MAIGOKITE_CONFIG` 时以该文件为准，不要只改未加载的模板。

```yaml
tools:
  windows_sandbox:
    enabled: true
    allow_read: []
```

安装委托官方 helper 创建机器级 `srt-sandbox` 账户、相关组、注册表凭证、ACL 和 WFP 规则。普通 setup 只安装 npm 依赖；启用配置不会触发 UAC 或自动安装。拒绝 UAC 不报告成功。卸载入口为 `scripts/sandbox.ps1 uninstall -ConfirmSystemChanges`，会删除共享沙箱账户及其 profile，可能影响其他 SRT 程序，必须先停止它们；官方保留的 ProgramData/LocalAppData 状态目录不由脚本递归删除。不安装 TLS 拦截证书，也不修改证书吊销检查。

执行边界与限制：

- 使用专用账户、受限 Token、NTFS ACL、WFP 与 Job；`workspace` 临时授予工作区修改，`read_only` 明确禁止工作区写入，已知敏感路径与 `.git` 另加拒绝规则。目录扫描失败、超额、重解析点/junction 或 UNC 工作区均拒绝执行。它不是 Linux 的 mount namespace，公开可读的其他目录不保证不可读；也不是完整 DLP。
- **网络不等于完全断网**：未批准联网时代理目的地为空，并校验 WFP 防直连；系统 DNS 解析不在隔离内。已批准联网只开放上游代理支持的访问，不承诺任意 UDP/网络协议。不能用此能力处理“绝无网络外泄”的任务。
- SDK 使用机器级共享沙箱账户。同一用户的 MaigoKite 实例通过文件锁串行运行沙箱命令，避免工作区授权重叠；不能隔离其他用户/其他 SRT 产品的并发授权和代理。不要在同机并行使用这些客户端。
- Node.js 宿主与安装目录中的 `scripts/windows-sandbox` 必须位于当前工作区之外，避免工具改写后续要以宿主身份运行的 SDK/helper；默认 `.workspace` 布局满足要求，不能把整个 MaigoKite 安装目录选作受限命令工作区。`MAIGOKITE_NODE` 可指定可信 `node.exe` 的绝对路径。优先使用机器级 PowerShell/工具安装；确需用户目录内的工具，才在 `allow_read` 添加其确切目录，**不要加入整个用户主目录或凭证目录**。规则属于启动配置，不由模型扩大。
- 日志、超时和停止保留；停止等待 helper 退出及临时 ACL 回收，回执缺失/失败不会报告成功。Core 控制管道关闭会请求停止；强杀/系统崩溃后依赖 SRT 后续初始化的恢复，不能保证断电前完成清理。不要在清理异常后立即启动另一个 SRT 客户端。
- 当前锁定版 SDK 不透传交互 stdin，沙箱内 `interactive=true` 明确失败，GUI 不会把桥接控制管道作为可输入终端。CPU/内存总配额、磁盘配额未交付，见下节资源限制。

`sandbox.ps1 status` 仅检查官方安装记录，`provisioned=true` 也不是可执行验收。API 状态刷新只查依赖：启用且依赖存在时为 `unverified / verification_on_execute`，不在读取设置时启动命令或修改 ACL。每次实际执行仍由 SDK 校验账户、防火墙和目录授权，成功进入后才具备该次沙箱边界；失败不回退宿主运行。

### 对话中的子 Agent

聊天输入框的“子 Agent”展开项可以设置每次任务派发总数和最大并行数，0 为关闭。在“允许指定的其他模型”中勾选已配置模型并应用后，可以直接发送：“用我配置的‘文档审阅’模型开一个子 Agent 检查这些文件，最后由你汇总。”

未指定模型时跟随主模型；主模型不会因此切换。名称重复时需明确是哪一个配置。没有配置或没有允许的模型不会被自动替换，需先更新配置再发起新任务。勾选云端模型意味着允许把委派说明和读取的材料发给该服务。

设置只影响之后发起的任务；派发失败也占用次数，更换模型不会重置额度。当前子 Agent 只读文件和搜索，不能再次委派，主 Agent 等待结果后继续。活动区显示实际模型及过程；不支持后台聊天、追问子会话或 Fork。指定模型调用和界面展示已有定向测试，取消、预算等其余场景见路线图。升级后需重启 Python 后端并刷新界面。

### 工作区工具

打开“设置 → 工具”可启停助手可见的内置工具与已连接 MCP 工具，选择持久化到当前
工作区。启用工具不等于免审批；权限模式仍在独立的“权限”设置中控制。
默认启用文件读写编辑、查找与搜索、命令、联网、浏览器和技能；待办与等待需手动启用。
新增启用项用于下一次任务/恢复；停用会阻止后续调用，但不会停止已经开始的副作用。

启动配置可以提供默认工具集和不可被界面放宽的停用列表：

```yaml
tools:
  default_tools: [file_read, file_write, file_edit, glob, grep, exec_command, web_search, web_fetch, browser, skill]
  disabled_tools: []
```

`default_tools: []` 关闭默认内置工具，不关闭已显式连接的 MCP 工具。MCP 加载范围由
服务级工具列表控制，再由设置页选择。`glob/grep` 需要安装 ripgrep（`rg`），缺失会提示；
grep 使用 Rust 正则语法而非 Python 正则语法。非 Windows 的 `exec_command` 默认使用 direct argv，不解释管道、重定向或 Shell 展开；Windows 行为见本节 PowerShell 说明。
Linux、Windows 的普通搜索和只读子 Agent 使用同一条受控搜索路径，无需完全访问、bubblewrap 或 WSL。`rg` / `rg.exe` 必须安装在工作区、搜索目录之外的绝对 PATH 目录。文件名发现复用 ripgrep 的 glob/ignore 规则，禁用外部配置及父目录/全局 ignore；内容搜索通过已校验的文件句柄制作临时快照，再由 ripgrep 匹配。不允许模型指定程序、shell 或额外命令参数。这是应用级只读能力，不是通用进程沙箱。
受限搜索不跟随发现的符号链接，文件句柄校验拒绝符号链接和目录 junction/reparse point；拒绝 UNC 路径，过滤各层已知敏感路径。`glob` 最多检查请求的 `max_results` 个候选项（上限 10000），只读取文件元数据；`grep` 每次最多检查 2000 个候选文件、每文件 2 MiB、内容快照共 32 MiB。超过限额或跳过不可读取项时返回 `truncated=true`，不要将零匹配理解为完整扫描。请缩小搜索路径继续。整次搜索有 30 秒超时，复用进程取消与输出限制；临时快照在完成、失败或协作取消后删除，后端被强制终止时可能留下系统临时目录残留。
API 启动日志和工作区 `runtime-status` 分开报告搜索依赖与命令沙箱。找不到可信 ripgrep 时提示安装；Linux 命令沙箱不兼容时提示更新 bubblewrap 后重启，不删除隔离参数重试，也不影响文件搜索。命令仍遵循原审批和沙箱要求，不能将搜索路径当作执行任意程序的入口。
历史 `bash` 名称只兼容旧显式调用，不再交给模型。

### Linux 命令工具链与缓存

普通文件读写、`glob/grep` 不依赖命令沙箱。编译、运行解释器和安装依赖仍通过命令沙箱执行，审批不能替代实际隔离。Linux 复用 [bubblewrap 的公开挂载和环境选项](https://github.com/containers/bubblewrap/blob/main/bwrap.xml)，不新增运行时或沙箱后端。

对于安装在用户目录、`/opt` 等位置的可信工具链，可在**实际启动使用的 YAML** 中明确配置只读目录，重启后端生效：

```yaml
tools:
  linux_sandbox:
    allow_read: []  # 填写本机存在的精确 SDK 目录，例如 /opt/company-sdk/1.0
    workspace_cache: true
```

- `allow_read` 默认空，最多 32 项，仅允许受限命令读取或执行指定目录中的内容，不扩大原生文件工具或 MCP 的授权。目录必须已存在，不能是整个用户目录、当前工作区的父目录、默认 `~/.maigokite` 私有目录、设备/系统运行目录或临时目录，也不能包含 socket、设备等特殊文件。存放秘密的其他自定义目录同样不得用于工具链授权。
- 附加目录通过 bubblewrap `--ro-bind` 只读挂载，沿用敏感路径扫描和挂载遮蔽。扫描仍受合计 10 万项、5 秒和挂载数上限约束，不会为扫描成功而跳过保护。不要挂入整个磁盘或大型开发目录。
- 可执行文件须位于进程 PATH 中，或通过绝对路径调用。配置只读目录不会自动选择 SDK 版本或修改宿主 PATH；PATH 目录别名会规范化为实际挂载目录。虚拟环境解释器若为符号链接，其目标运行库也必须可见。程序已安装但被隔离时返回 `executable_outside_sandbox`，与“未安装”区分。
- `workspace_cache: true` 时，Linux 可写沙箱将 XDG、uv、pip、npm、Go 缓存分别放入当前工作区 `.maigokite/cache` 的子目录，按实际使用创建。不同文件夹不共享缓存，也不复制宿主凭证或登录配置。缓存不是可信代码来源，且没有总磁盘配额；只读沙箱或设为 `false` 时使用本次临时缓存。
- HOME 指向本次沙箱的临时目录，不映射真实用户目录，也不继承宿主 Git 全局配置、密钥或包管理器登录态；项目配置仍由各工具处理。本仓库已忽略 `.maigokite/cache/`，其他工作区需自行添加所需忽略规则。应用不会修改其 Git 元数据，也不自动删除历史缓存。
- 安装依赖需要联网时，保持 `sandbox_permissions=use_default`，仅申请 `network_access=true`，批准后仍保留文件隔离。附加只读路径作为命令参数时，仍须经过现有越界审批；配置不自动授权任意参数。只有需要宿主执行时才请求 `require_escalated`，不能因依赖缺失或缓存不可用而自动升级权限。

Windows 继续使用 `tools.windows_sandbox.allow_read` 和上游 SRT 环境，本节缓存策略仅适用于 Linux。Git 元数据仍只读，设备、系统服务和宿主凭证不会自动开放。

### 命令资源限制

`max_output_bytes` 仅控制回复中的每路预览。后端从管道保存日志，stdout/stderr 每次调用合计最多 **8 MiB**，超出后请求停止所属进程、等待清理并返回 `COMMAND_OUTPUT_LIMIT`；日志只保留上限内的字节。这不限制命令直接写文件、历史日志累计或整个工作区磁盘占用。

可选的部署限制只对 `exec_command` 生效，不是模型可调整的参数：

```yaml
tools:
  command_limits:
    cpu_seconds: 0
    address_space_mb: 0
```

零保留宿主原有上限。Linux 非零值通过 util-linux 的 `prlimit` 在执行前设置软/硬上限；CPU 是单进程累计秒数（向上取整），地址空间是虚拟内存 MiB，不是 RSS 或所有后代合计内存。后代继承 rlimit，但每个进程分别计量。地址空间设得过低会使 Node/浏览器等无法启动。缺少 prlimit 或 Windows 配置非零值时明确失败，不忽略配置；Windows 原生 CPU/内存限制与全工作区磁盘配额尚未接入。修改启动 YAML 后需重启后端。

### 联网服务

工具设置页下方可以为 Web 搜索配置可选服务。未配置凭证时使用无需密钥的 DuckDuckGo；保存
Tavily 后优先使用 Tavily。Google Programmable Search 需要 API 密钥和搜索引擎 ID（CX）。

敏感凭证保存在操作系统密钥链，界面只接收引用状态。Python API 在任务开始前，将当前工作区的凭证同步到智能体核心内存，不写入会话、ToolResult 或普通配置。

搜索服务请求失败时，活动流将工具标记为失败并显示检查网络或打开联网工具设置的提示；详细参数和原始结果仍收在二级详情中，模型可以根据失败结果继续给出说明。

## 5. 工作区与运行状态

侧栏顶部可切换文件夹工作区；点击 `+` 添加，桌面版打开系统文件夹选择器，Web 版填写服务所在设备的绝对路径。所选路径必须是存在的文件夹，且不能是文件系统根目录。新对话归属所选工作区，已有对话保留在默认工作区，可在会话 `···` 菜单手动移动。文件预览、Git Diff、内置文件/查找/命令工具及浏览器截图使用当前对话的文件夹，不修改全局进程工作目录。

会话 `···` 菜单支持重命名、移动、归档/恢复和删除。归档保留消息与执行记录，恢复后才可发消息；侧栏“归档对话”查看所选工作区的归档列表。删除需要再次确认，清理消息、任务记录及对应 LangGraph checkpoint，但不删除文件夹或工具写入的文件；安全审计保留。未结束的任务禁止移动、归档或删除。

“设置 → 工作区”只展示所选目录和Python 后端状态，不修改进程启动参数。选择文件夹即时生效，不需要重启；默认根目录和服务端口仍来自启动配置。MCP 启动目录、技能加载目录由“扩展”配置并显式应用，切换文件夹不会重启这些服务。模型、联网凭证、权限模式和界面称呼即时生效，仍按同一 `personal` 范围共享；文件夹不是新的账号或团队权限范围。页面不声称能检测运行期间手工修改的外部 YAML。

### 5.1 存储边界

- 当前为个人数据范围，其下可包含多个文件夹工作区；既有 team 值只保留数据兼容。
- 当前使用 SQLite 与工作区文件；独立文档库、向量嵌入和向量服务未接线。
- 未来索引若启用，必须支持版本与重建；现在没有可操作的索引配置。

存储语义见 [数据库设计](../design/Database.md)。

## 6. 权限模式

权限运行时提供三个面向用户的档位：

- `只读（read_only）`：读取与搜索自动执行；编辑、命令和联网操作询问。
- `工作区（workspace，推荐）`：当前工作区内读取、编辑和真实沙箱内的普通命令自动执行；越界、联网与敏感操作询问。
- `完全访问（danger_full_access）`：显式取消工作区沙箱与逐项审批，命令以当前操作系统用户权限执行；不启用原本禁用的工具。

旧 `full_access / auto` 保留为 `workspace_auto` 的受限行为，不会因升级而获得完全访问。部署显式禁用项仍有效。界面已接入新模式、风险确认和授权范围选择；拒绝后继续、文件审批重启和显式完全访问下的测试进程/生图已有定向回执，窄授权撤销/过期、模式变更与旧库升级仍需专项验收。旧页面的“完全访问”不能视为新模式已启用。完整矩阵见 [工具权限](../agent-capabilities/security/tool-permission.md)，验证范围见 [交付路线与状态](../project/Roadmap.md#当前验收状态)。

设置页只修改新对话默认档位，聊天输入框可覆盖当前对话档位。需要确认时，审批记录包含规范化参数、资源范围、风险等级和有效期；默认只批准一次。支持复用的操作可选择当前任务/会话范围，最长 8 小时且可撤销；L5 不提供复用。L1–L5 是服务端内部风险模型，不要求用户逐工具配置。没有独立审核 Agent。

权限设置只支持 `default_mode`；不要配置 `tool_levels` 或独立 `grants`。“驳回”只拒绝当前操作，可附理由，Agent 会继续处理允许的部分；同批其他待审批操作仍须分别确认。“停止任务”才结束整轮。密钥链锁定或模型不可用时，拒绝仍会保存，但模型无法继续会明确报告失败。这里的“只读”指默认自动执行范围，不是系统只读沙箱；单次批准的文件写入仍可执行。

部署者还可在 YAML 中配置额外白名单。空数组表示该维度不额外收紧；非空时，工具名、Bash 命令前缀和资源路径必须同时满足白名单。相对路径以工作区根目录为基准，白名单只能缩小 ToolPolicy 的权限，不能绕过工作区边界、审批或 hard-deny：

```yaml
permissions:
  allowlist:
    tools: [file_read, glob, grep, exec_command]
    command_prefixes: ["git status", "git diff"]
    paths: ["docs", "src"]
```

## 7. 工作区文件与差异

右侧栏的“文件”页按需读取侧栏所选文件夹，可选择文本文件进行只读预览；“变更”页展示 Git staged、unstaged 与 untracked 文件，并在选中文件后加载逐文件差异。配置 `MAIGOKITE_WORKSPACE_ROOT` 可显式指定默认根目录。文件查询只接收已登记的文件夹 ID 和相对路径，拒绝绝对路径、目录穿越与逃逸根目录的符号链接。

### 7.1 浏览器运行时

浏览器工具默认按需启动，不需要为每次任务预先打开窗口。部署配置示例：

```yaml
browser:
  enabled: true
  headless: true
  executable_path: ""  # 默认使用 Playwright 管理的 Chromium；可填本机已有浏览器的真实路径
```

浏览器工具在应用层只接受公开 HTTP(S) 地址，并解析主机地址，拒绝私网、本机、链路本地和保留地址。截图路径必须位于工作区；`fill/click/press` 等页面副作用仍受当前会话权限与审批控制。该检查不能替代生产网络边界。部署时还应通过容器或主机出口策略阻止 Chromium 访问内网，以覆盖 DNS 重绑定等应用层无法彻底消除的风险。

## 8. 技能与 MCP

从侧栏“扩展”或“设置 → 管理 MCP 与 Skill 扩展”进入：

1. MCP：添加名称、可执行程序、启动参数（每行一个）、工作目录与环境变量名。程序以当前用户身份运行，只使用可信来源；启动参数不放密钥。
2. 凭证：先在 MCP 配置声明凭证环境变量名并保存，再点击该服务的“配置凭证”。值保存到现有系统密钥链，不在页面回显。密钥链不可用时需先解决系统配置，不退回明文存储。
3. Skills：导入或编辑单个 `SKILL.md`，或配置现有技能目录；可以启停、移除托管指令。目录来源不在页面修改原文件。格式只在 [本地技能](../agent-capabilities/skills/skill-lifecycle.md#2-当前文件格式) 维护。
4. 保存后点击“应用并检查连接”，查看实际加载状态。存在运行中、等待审批或回答的任务时，先完成/取消任务；应用不打断它们。网络请求超时后先刷新状态，不重复启动进程。
5. 只想恢复已配置的 MCP 时，点击“恢复已有连接”。它不应用新编辑，有待审批/待回答任务也可使用；正在执行的任务仍须先结束。

配置保存到现有产品数据库，不修改 YAML。首次没有数据库配置时，使用 `skills.scan_directories` 和 `mcp.servers` 作为初始值；成功初始化后记录生效配置。此后重启使用上次生效配置，不自动应用已保存但尚未生效的编辑；手改 YAML 的这两项不会覆盖数据库配置。升级前没有生效记录时，以当时的数据库配置/YAML 建立初始值。

MCP 目前只支持本地 stdio，不提供 HTTP 地址连接。Skill 只提供按需可读的指令，不自动执行目录代码；单个 Markdown 导入不等于 Git/ZIP 技能包安装，也不含包内资产、依赖、版本授权或 Grant。

初次启动仍可在 YAML 配置受信服务：

```yaml
mcp:
  servers:
    local_tools:
      enabled: true
      command: "python3"
      args: ["-m", "my_mcp_server", "--workspace", "{workspace}"]
      cwd: "{workspace}"
      env_allowlist: []
      secret_env: ["MY_MCP_TOKEN"]  # 仅保存变量名；值通过界面写系统密钥链
      # 可选；省略 enabled_tools 加载全部，[] 不加载；disabled_tools 排除优先。
      # 使用服务端原始工具名，不带 namespace。
      enabled_tools: ["search", "read"]
      disabled_tools: []
      result_adapter: "none"  # 默认；仅显式接入天线服务时选 antenna_design
      request_timeout_seconds: 30  # 1～660；仅工具调用，资源导入仍限 30 秒
```

`command` 以 argv 方式直接执行，不经过 shell；`cwd` 必须位于默认工作区内。`{workspace}` 在 cwd/args 中替换为该路径，而不是当前会话选择的目录。`env_allowlist` 只声明从 Core 启动环境继承的变量名，不得与 `secret_env` 重名，凭证值不得写入 YAML。工具命名为 `<server>__<tool>`，调用沿用 ToolPolicy、审批和审计；此审批不限制 MCP 程序自行启动的行为，不等于系统沙箱。

后端重启后，MCP 内存凭证需要从密钥链重新加载，通常无需重填。可点击“恢复已有连接”；新聊天也会按上次生效配置尝试恢复，部分可选服务失败不会阻止普通聊天。文件任务恢复、回答提问和批准原生工具不会启动或等待无关 MCP。批准 MCP 工具时，目标服务和确切工具必须可用，否则保留审批，不消费决定。拒绝操作不依赖 MCP 凭证或连接，也不会启动被拒绝的服务。

应用失败时查看页面错误和逐项状态。Skill 扫描出错或任一启用 MCP 未连接时，不提交新版本。原普通聊天入口保留，但已断开的外部工具仍需恢复；保存配置不等于生效，连接成功也不保证持续健康。

停用或移除在应用后生效，不删除程序、Skill 源目录或密钥链值。清除凭证需单独操作，再次应用配置以替换健康进程中的旧环境；“恢复已有连接”只重建缺失连接，不轮换健康连接的凭证。当前没有自动健康重启、空闲回收或远端副作用取消保证。基本保存、应用和失败回退已有测试，密钥链轮换、故障处理及设置页完整操作仍需验收。

### 8.1 天线计算与方向图

这是独立安装的本地工程扩展，可以按明确目标搜索导线尺寸、复核网格、计算理想匹配，并导出候选设计包。不随普通聊天安装求解器，也不提供任意拓扑自动设计或“可直接制作”的保证。

推荐在仓库根目录生成 **MCP + Skill 联合配置**，Linux / Windows PowerShell 使用相同命令（将结果目录改成自己的绝对路径）：

```sh
uv sync --project extensions/antenna_design --locked --extra engineering --extra mcp
uv run --project extensions/antenna_design --no-sync antenna-design connect --root /absolute/antenna-project --output antenna-workflow.json
```

Windows 的 `--root` 可用 `D:/antenna-project`。生成器保存本机独立环境的 Python 路径、工具白名单、结果适配、计算/请求时限和随包 Skill；不启动服务、不改现有设置。配置不能直接拷给另一台电脑使用，应在目标机器重新生成。

打开 **扩展 → 导入工作流**，选择 JSON，核对命令、目录和 Skill，确认需要覆盖的同名条目；旧工具白名单变化另行确认。保存后点击 **应用并检查连接**。导入不安装依赖、不自动执行命令，也不覆盖其他服务或凭证。扫描目录中已有同名 Skill 时需先自行移走旧副本或调整扫描目录，不能覆盖托管条目来假装更新成功；原来明确停用的 Skill 也不会被暗中启用。

已有项目应继续指向原结果目录，省略 `--max-solver-runs` 保留总额度和已用次数，不要为了继续计算更换根目录。升级后重新生成配置，并确认新增工具及 Skill；当前配置包含规划、搜索、交付和匹配工具，旧白名单不会自动更新。

手动配置方式仍可用：

1. 在 `extensions/antenna_design` 目录运行 `uv sync --locked --extra engineering --extra mcp`，建立独立服务环境。
2. 在“扩展 → MCP”添加服务，命名空间可填 `antenna`。命令填写独立环境的 Python **绝对路径**：Linux 为 `<包目录>/.venv/bin/python`，Windows 为 `<包目录>\.venv\Scripts\python.exe`，不是主应用的 Python。
3. 参数框逐行填写以下六项；工作目录留空。`{workspace}` 指默认工作区，不随对话切换。服务在该专用子目录保存完整证据包；不要多个服务共用同一结果目录。

```text
-m
antenna_workflow.mcp_server
--root
{workspace}/antenna-service-results
--timeout
20
```

4. “结果展示”选择“天线结果与方向图”，保存并点击“应用并检查连接”。此选择允许在获准计算后，把校验过的结果复制到当前对话文件夹；只用于可信的天线服务。其他 MCP 保持“标准工具结果”。
5. “工具请求超时（秒）”默认 30。较慢计算可将参数中的 `--timeout` 设为 120，同时把此项设为 150，留出回执与清理余量；保存后应用才能生效。仅修改服务器参数不会自动延长宿主等待。
6. 服务按当前白名单加载工具；若命名空间为 `antenna`，模型看到 `antenna__antenna_solve` 等名称。先用 `project_status` 查项目，单次计算用 `solve`；完整参数搜索先用 `plan_design`，再用 `design`。工具清单、JSON 示例和匹配参数见[独立包说明](../../extensions/antenna_design/README.md#高层工具)，不是把任意文件路径交给服务。
7. 在独立包目录用 `uv run --no-sync antenna-design skill --output <新目录>/SKILL.md` 导出随包工作流说明，在“扩展 → Skill”导入并应用。Skill 指导保存需求、输入版本、run_id 和下一步；不增加执行器，也不负责强制累计预算。

服务以专用结果目录为一个项目，SQLite 持久保存预算与运行编号。新目录默认允许 3 次求解尝试，只适合少量试算；参数搜索按候选频点计次，不能把一个候选算作一次。先用 `plan_design` 查看最低/推荐次数、网格复核余量和缺口，再按实际需求调整总限额。在参数框增加 `--max-solver-runs` 和总次数两行，保存并应用；模型工具不能自行提高限额。

省略参数则保留已存限额，重启不重置已用次数。失败/取消保守占用次数，取回、复用、补画、评价、匹配和导出不增加电磁求解次数；旧目录首次接管按目录数保守计入。`project_status` 可查询剩余额度和编号。额度只控制该 MCP 项目，不是模型服务余额或整个聊天的 token 限额。

调用仍经过现有 MCP 审批。成功结果导入当前对话文件夹的 `antenna-results/<结果内容标识>/`，聊天展示已完成的水平/垂直方向图，点击可打开文件；SVG、数值 JSON 和可导出的 `.maa` 也保留在该目录。服务原件不会被搬走或覆盖。

`design` 在一次调用内顺序完成参数搜索、两档网格复核、补画和候选包。已有完整数值只需 `evaluate` 后 `export`，不必重新搜索。文件包括报告、几何 SVG、尺寸 CSV、模型、材料说明、验证缺口、施工调试说明、复算入口和 `design.zip`。ZIP 按原 run_id 分目录保留各频点数值和方向图，不混覆盖同名文件。聊天回执可打开已导入文件，预览面板可下载；二进制 ZIP 不要求能文本预览。下载沿用身份和工作区检查，上限 48 MiB。

交付包标记 `CANDIDATE_NOT_VALIDATED`：尺寸是数值模型，不是已经验证的下料清单，材料/匹配/机械未知项保持未知。导出不调用求解器、模型或增加额度；重复同编号同输入复用，导出回执丢失用 `get_delivery(delivery_id)`，不要重新计算。包读取/导入沿用逐文件完整性和权限检查。

导入前逐文件验证 URI、MIME、大小和 SHA256；每次写入重新检查权限。只读或权限中途降级时停止后续导入。部分文件导入失败不自动重算，结果保留 `import_warning`；已核验文件不会被删除。只有最后写入 `import-receipt.json` 的副本才标记导入完成。

丢失回执或导入中断时，用 `get_result(run_id)` 取回原结果并补齐同一目录，不新增计算、不覆盖用户修改。数值已保存而图片不完整时，回执为 `render_status=incomplete`；用 `render(run_id, output_run_id)` 在新编号下补画。补画回执丢失应查询新的输出编号。旧版缺少数值阶段清单的包可读取，但不能直接补画；未知或损坏的结果不自动重算。

启用此结果适配后，停止任务或计算请求超时会自动向服务取消**本次调用**并等待停止回执；不停止同服务的其他调用。连接中断或旧服务不支持时，仍会提示远端停止未确认，不能据此直接重新求解。单独停止已知计算可调用 `cancel(run_id)`，补画使用输出编号；未返回 `worker_stopped=true` 不视作已确认停止。升级代码后重启 Python 后端，再“应用并检查连接”重载扩展工具。

运行中会明确提示等待查询；缺失、未完成、损坏和编号冲突分别提示下一步。本地结果已有修改时，提示中给出相对文件位置；先自行保留并移走冲突副本，再取回原结果，不用重新计算。

需要修改频点、材料或架设条件时，用 `prepare_revision` 准备新输入，旧频点的结果不能直接冒充新方案。`matching` 计算逐频理想 L 型网络，各点需要调谐或切换；`evaluate_matching` 使用同一组固定元件检查已有采样点，不逐点重调，也不补造点间阻抗。理想匹配不证明连续带宽、器件损耗、功率或实物性能。

计算结果仍为 `COMPUTED_NOT_VALIDATED`，交付包仍为 `CANDIDATE_NOT_VALIDATED`，不表示已通过收敛、全面 MMANA 兼容或制作验证。Linux 已有计算、搜索交付、生产审批/取消和重启取回的定向记录；完整 GUI、Windows、自然语言自主设计和运行中强杀恢复尚未验收。

## 9. 凭证

- 当前使用操作系统密钥存储；UI/API 只显示引用状态与元数据，不回显敏感凭证。
- 不宣称 Vault/KMS 或加密文件降级已接线；环境变量仅用于受支持的启动/模型路径，安全边界见 [安全边界](../ops-security/Security.md)。

## 10. 记忆（阶段 2）

“用户记忆”页面支持按工作区文件夹手动添加、查询、修改和遗忘长期信息，写操作进入审计日志。要让当前对话使用这些信息，需同时开启该页面的文件夹开关和聊天栏的对话开关，二者均默认关闭；旧的未归属记忆不会自动使用。双开关、遗忘后下次请求省略、请求/checkpoint 分离及分岔默认关闭已通过真实 Core 定向验收；多目录、修改/续接和 GUI 等剩余范围见 [当前验收状态](../project/Roadmap.md#当前验收状态)。

每次主模型调用前重新读取开关和有效条目，仅在本次请求中附带给已配置的当前模型，不把参考区写死进聊天历史或 checkpoint。预算不足时部分或全部省略，自动提取与语义检索尚未实现；此功能不扫描其他聊天或文件，也不增加服务。

“向量嵌入与索引”入口已提前注册，但目前是只读状态页，没有可保存的模型提供方控件。保留期、自动抽取、向量嵌入模型提供方和索引重建需要等对应 API、版本化索引、删除闭环和测试完成后再开放。

记忆与后续索引配置保持以下约束：

- 敏感凭证不进入记忆。
- 同一 `personal` 范围内也按文件夹隔离记忆；未归属旧记录不会自动进入任一目录。文件夹不是宿主进程或凭证的安全隔离边界。
- 文件夹与单个对话分别控制记忆使用，两层开关默认关闭。移动对话/创建分岔不自动继承开启状态；同目录不同工作流也可以独立关闭。
- 关闭、修改或删除影响后续重新组装的请求，不会撤回已发出的请求或 SDK 对该请求的内部重试，也不会擦除既有回复中引用过的事实。需要干净上下文时新建关闭记忆的对话。
- 当前可查看、修改和遗忘用户记忆；导出尚未完整交付。
- 原始消息/文档删除后派生索引必须清理。

详见 [记忆](../agent-capabilities/memory/memory-system.md)。

## 11. 生效范围

- 新会话设置通常不改变已经运行的任务。
- 权限缩减和凭证撤销应立即阻止新的工具调用。
- 需要重启进程或重建索引的配置必须在 UI/CLI 中明确提示。
- 无法识别的配置字段应报错或告警，不静默忽略。

## 12. 看图、生图与文件预览

旧图片上传、生图和静态预览有本机协议及 Web/Chromium 定向记录，SVG 本地 `<use href="#…">` 缺图已于 2026-10-16 复验关闭，外部引用仍被移除。后续附件菜单、文本/文档读取、页图和 OCR 尚未行为验收，不能沿用旧图片结果；真实模型服务也不据此标为 LIVE PASS。准确范围见 [当前验收状态](../project/Roadmap.md#当前验收状态)。

- **看图**：在聊天输入区打开 `+`，选择「添加附件」，添加 PNG/JPEG/WebP/GIF 后发送给当前模型，也可粘贴或拖入。每条最多 4 张图片，所有附件合计不超过 16 MiB；模型需支持图片输入，或本机具备文字识别能力。后者只提供图片文字，不理解图形细节，不能仅凭“OpenAI-compatible”认定模型支持看图。图片存入当前工作区的 `chat-images/`，移除只取消发送，不删除已上传文件。附件消息暂不支持在当前任务执行中引导或排队。
- **生图**：在「设置 → 工具 → 图片生成」填写实际 API 根地址、模型 ID 和密钥并启用。Images 模式对应 OpenAI Images 兼容接口；Responses 模式需填写支持 `image_generation` 工具的对话模型 ID。可使用与聊天相同的服务，但不会自动借用聊天密钥，协议须显式选择，不按模型名称猜测。尺寸和返回格式可由服务决定，不强制使用内置模型参数表。
- **批准与费用**：普通权限下每次生成需确认，完全访问模式可直接执行。服务可能计费，失败或取消不会自动重试；取消请求也不代表服务端未生成或未计费。同一工具调用在请求前记录审计意图，崩溃恢复不重复发送该请求，但不提供服务端 exactly-once 保证。结果不确定时先检查 `generated-images/`，需要重试时明确发起新的生图请求。URL 图片下载不携带 API 密钥、环境代理或 netrc 凭证，跨源下载仅允许公开网络目标；无法下载时，可让支持的服务返回 Base64。
- **查看产物**：生成图片存入 `generated-images/`，点击聊天中的图片预览可在右侧查看；桌面版还可选择「在本机打开」。图片不随对话删除，仍保留在工作区。输入图片和生成图片的大小上限均为 16 MiB。
- **文件预览**：右侧支持栅格图片，以及 SVG、Markdown、HTML 的源码/预览切换。切换区沿用抽屉的纸面、细线与选中标记，支持左右方向键和 Home/End。SVG 净化后作为图片显示，保留本地渐变/符号引用和内嵌栅格图片，不执行脚本、不加载外部资源或嵌入网页。SVG 大小上限为 512 KiB，与文本一致；截断或解析失败时提示查看源码。HTML 不执行脚本、不加载外部或相对资源；完整网页交互应使用浏览器，不降低预览沙箱权限。

看图与生图是两种能力，不等于所有多模态模型都能原生输出图片。本次不包含图生图/蒙版编辑或其他厂商专有多模态协议。

### 文档和本地文字识别

聊天可添加 UTF-8 文本、PDF、DOCX、XLSX、PPTX，或从当前工作区引用快照。右侧文档预览支持目录、查找、按页/段落/表格段/幻灯片跳转；PDF 可切换“提取文字 / 页面图像”，安装文字识别引擎后增加“识别文字”。查找覆盖文档提取的文字，不覆盖图片中的未识别内容。

每条消息最多 8 个附件，其中图片最多 4 张，合计不超过 16 MiB。工作区引用会复制当前文件为快照，不随源文件变化；文本和文档保存在 `chat-files/`，移除附件或删除对话不自动删除文件。待上传或失败的文件留在当前窗口的对话草稿中，切换对话取消旧上传；刷新页面不恢复未上传的本地 File 对象。

同步本轮依赖后重启应用（Linux：`bash scripts/setup.sh`；Windows：`powershell -File scripts/setup.ps1`）。PDF 页面显示无需另装桌面阅读器。OCR 是可选的本地能力：安装 Tesseract 5 并将其程序目录加入系统 PATH，同时安装所需语言包；用 `tesseract --list-langs` 检查实际可用语言。需要中文时安装 `chi_sim`，英文为 `eng`。Tesseract 的安装说明见[官方文档](https://tesseract-ocr.github.io/tessdoc/Installation.html)；应用不会下载语言模型或启动外部服务。

没有 Tesseract 不影响普通聊天、PDF/Office 文字提取和 PDF 页图显示；识别入口只在检测到可用语言后显示。纯文本聊天模型可以通过本机 OCR 读取图片文字，界面明确提示能力差异。识别结果可能错漏，请对照原图，尤其是表格、尺寸和数字。

附件原文只作为资料，不是执行许可。非完全访问模式下，由含附件的上下文发起写入、命令或外发会请求确认；授权仍限具体目录、命令或站点。添加新附件后要重新确认，避免旧的“本会话允许”覆盖未知资料。
