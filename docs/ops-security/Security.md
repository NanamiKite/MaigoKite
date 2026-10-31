# 安全边界

> 权威范围：威胁模型、身份、凭证、网络和数据安全。
> 工具风险/模式/审批的唯一矩阵见 [工具权限](../agent-capabilities/security/tool-permission.md)。

## 1. 信任模型

用户请求、模型输出、网页/文档、工具/MCP 结果和技能内容均为不可信输入。被模型读取不改变其信任级别，不能成为授权或系统指令。

可信边界为 Python API 的本机来源/资源检查、ToolPolicy、实际沙箱/资源限制、凭证管理和审计。不能用提示词、前端按钮或“个人使用”代替服务端检查。

## 2. 当前身份与缺口

当前以本地个人主体和工作区访问检查为主；外部请求不得自行声明可信 principal。保留成员关系兼容结构，不建设团队邀请/角色平台。

loopback/Origin 限制不是完整设备认证。Electron 管理的后端现在要求每次启动的随机凭据；浏览器模式仍是本机来源限制，没有登录、设备配对或对恶意本机进程的隔离。OIDC/JWT、Vault/KMS 不应写成已接线能力。内部 gRPC 已移除，不得公开业务库或将 Python API 直接暴露到远程。

手机会话、配对、撤销与 HTTPS 信任边界按 [手机远程访问](../design/RemoteAccess.md) 单独设计，不把反向代理到 loopback 当作本地免认证依据。

## 3. 工具、进程与审批

AccessPolicy 和 ToolPolicy 都需通过。模型/技能/MCP 自报能力不能授权；具体参数、范围、摘要值/版本和单次审批规则只在工具权限文档维护。

`exec_command` 的受限模式要求可用的系统沙箱，默认不批准联网。Linux 使用网络 namespace，Windows SRT 不隔离系统 DNS，不能统称完全断网。沙箱不可用时不自动回退到宿主执行；进程内文件工具的路径校验也不等于完整 OS 隔离。

MCP 程序由部署者显式配置并信任，当前使用最小环境和受限 cwd；全面资源隔离与独立 Grant 尚未交付。详见 [MCP](../agent-capabilities/mcp/mcp-integration.md)。

当前没有覆盖整个 Agent 的统一 OS 沙箱，各能力的实际边界如下：

| 执行路径 | 当前边界 | 不提供的保证 |
|---|---|---|
| Linux 受限命令 | bubblewrap 工作区挂载、显式工具链只读挂载、网络/PID namespace；嵌套敏感路径遮蔽；工作区缓存与临时 HOME | 内容 DLP、硬链接别名检测、工作区/缓存总磁盘配额 |
| Linux / Windows 受限搜索 | 固定可信 ripgrep、句柄校验、有限只读内容快照；不依赖命令沙箱 | 任意程序/网络隔离；被替换的 ripgrep 隔离；硬链接与所有宿主竞态防护 |
| Windows 受限命令（可选、默认关闭） | SRT 0.0.79 alpha：专用账户、Token、ACL、WFP、Job；启动前检查，停止等待清理 | DNS 阻断、公开可读路径隐藏、其他 SRT 客户端隔离、交互 stdin、进程树资源配额；未实机验收 |
| 原生文件工具 | 进程内路径与授权检查 | 整个 Core 的文件系统隔离、所有 TOCTOU 防护 |
| MCP stdio | 部署者显式信任程序、最小环境、工具调用审批 | 服务进程的宿主文件/网络权限限制；cwd 不是 chroot |
| Browser | Playwright 启用 Chromium 原生沙箱，独立 profile 和产品 URL/文件策略 | Playwright driver/浏览器父进程的工作区隔离、完整网络出口隔离 |

嵌套遮蔽只针对启动扫描时已存在的已知敏感名称，不识别改名凭证、硬链接别名或秘密内容，也不防止并发的宿主进程在扫描和挂载之间更改目录。扫描不完整时拒绝启动，而不是忽略错误后执行。不能据此宣称完整防泄露。

Windows 原生后端只通过公开 SDK 接入，普通安装依赖不修改系统账户/ACL/防火墙；管理员安装与卸载必须显式确认。同一用户的 MaigoKite 沙箱命令串行运行，不与其他 SRT 客户端并用。详情与上游限制见 [工具权限](../agent-capabilities/security/tool-permission.md) 和 [配置指南](../user-guide/Configuration.md#windows-原生命令沙箱)。

本地技能是声明文件，不执行目录代码；包摘要值/签名/安装检查是未来门槛，不能伪装当前安装流程。显式禁用工具、部署限制及操作系统权限不因批准而消失；用户明确选择 danger_full_access 则取消工作区隔离与逐项审批。旧 full_access 保留受限语义。精确边界与当前限制见 [工具权限](../agent-capabilities/security/tool-permission.md)。

## 4. 凭证

- Python API 通过操作系统凭证存储按模型/工具配置隔离敏感凭证，普通 API 只回显存在状态，受控同步至核心服务内存。
- 模型提供方/基础地址变化不能把旧凭证发送到新地址；本地模型提供方不读取云端环境密钥。
- 敏感凭证不进入普通 YAML、业务表、记忆、向量、任务事件、日志或导出内容。
- MCP 只继承显式 allowlist；不能把核心服务全部环境或所有模型 Key 下发第三方进程。
- 轮换/撤销应更新后续调用，活动运行实例的共享客户端安全关闭由核心服务生命周期处理，见 [智能体运行时设计](../design/AgentRuntime.md)。

没有明确部署和验收前，不宣称加密文件降级、外部敏感凭证 Manager 或动态 SkillGrant 已实现。

## 5. 网络与 Electron

`Origin: null` 不表示 Electron 身份，本地 HTML、data URL 或 sandbox 页面也可能具有该来源。浏览器模式拒绝 `null`、`file://` 和其他非本机 HTTP 来源，保留本机 HTTP 页面和无 Origin 原生调用的现有边界；这仍不区分任意本机 HTTP 服务或原生进程。

Electron 主进程用 Node `crypto.randomBytes(32)` 为本次启动生成随机凭据，仅通过子进程环境 `MAIGOKITE_DESKTOP_TOKEN` 交给其后端。后端在初始化 Core 前从环境移除该值，使用内存中的凭据核对 `X-MaigoKite-Desktop-Token`，避免向工具/MCP 子进程继承。凭据不进入前端配置、preload IPC、URL、数据库或日志；重启产生新值。

桌面模式的 HTTP、预检和 WebSocket 请求都必须通过该验证，不能通过伪造 localhost Origin 或省略 Origin 降级绕过。LocalBoundary 位于 CORS 外侧，校验 ASGI 直接连接地址、Host、来源和凭据后才进入 CORS 或业务路由；比较使用 `secrets.compare_digest`。只有已验证的桌面请求才可获得 `Access-Control-Allow-Origin: null`，无凭据的 `null` 请求返回 403，WebSocket 在接受前以 1008 拒绝。

Electron 本地页面的 WebSocket 实际发送 `Origin: file://`，它仅在本次桌面凭据验证成功时可通过；来源字符串本身不建立信任。带路径的 file URL、无凭据/错误凭据的 file 来源继续拒绝；HTTP/OPTIONS 的 file 来源仍不放行，CORS 保持不变。10 月 31 日在隔离的 Linux Electron 44.8.0 中复现修复前的拒绝，并验证修复后增量和终态事件可达；这不等于完整客户端、预检、重定向或安装包验收。

主进程复用 Electron `webRequest.onBeforeSendHeaders`，仅对主窗口/胶囊窗口的已允许主 frame，以及自有后端或开发代理的 API/health 地址附加凭据；子 frame 和未知窗口不授权。每个目标重新计算请求头，跨地址重定向不保留凭据。主进程健康探测与工作区查询显式携带相同凭据。该机制不能防止已攻陷的应用主 frame、同 OS 用户的恶意程序或调试器读取进程内存，也不替代远程设备认证。

浏览器当前公开 HTTP(S)/地址检查是应用防护，不保证 Chromium 所有出站请求都隔离；DNS 重绑定、重定向、下载和资源访问需实测及主机/容器出口策略。不能把 URL 检查写成完整 SSRF 沙箱。

临时浏览器和持久化登录 profile 均显式设置 `chromium_sandbox=True`。启动失败不以 `--no-sandbox` 重试；Linux 需受支持的非 root 环境。这不改变 MCP 或 Python 文件工具的执行权限。

远程入口须 HTTPS/WSS、受限 Origin/CORS、设备会话和最小开放资源。Electron 当前安全窗口基线见 [前端与桌面交互设计](../design/FrontendDesign.md)，任意导航/下载、资源替换和 IPC 参数安全仍须发布验收。

## 6. 数据与诊断

工作区是默认业务范围。派生索引继承源授权/删除规则，不能存唯一原文或绕过遗忘。消息、截图、工具结果也可能有敏感数据，不只是 Key 才需要保护。

日志/审计当前按键过滤不是完整 DLP；递归脱敏、导出和保留策略见 [日志与诊断](Observability.md)。备份必须保护访问与密钥分离，实际流程见 [备份与恢复](Backup.md)，不把规划的自动加密当已实现。

出现泄露、越权、审批绕过或沙箱问题时：先限制新调用/访问，撤销相关设备或凭证，保留脱敏证据，通知用户并修复复验。未来 Grant/索引不存在时不虚构撤销步骤。

## 7. 验收边界

关键安全场景以 [测试与验收](../testing/TestPlan.md) 为唯一测试清单。当前代码防护、未实现能力与待验收项必须分别说明，不能用统一当前有效标签或勾选清单暗示全部安全承诺成立。
