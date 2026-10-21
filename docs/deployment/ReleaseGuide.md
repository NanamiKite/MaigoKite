# 构建与发布指南

> 本文说明源码构建、桌面制品、发布门禁和回滚。
> 桌面安装包尚未完成运行资源打包与验证（runtime bundling）。本页检查项是发布要求，不是已通过的验收结果。

## 1. 当前可生成什么

| 入口 | 当前产物 / 限制 |
| --- | --- |
| `bash scripts/build.sh all` / `scripts/build.ps1 all` | Web 资源与 Electron 编译；不构建 Python 安装运行时 |
| `build.sh web` / `build.ps1 web` | Web 静态资源，启动用对应的 `start web built` |
| `build.sh desktop` / `build.ps1 desktop` | Web + Electron，启动用对应的 `start desktop built`，直接加载本地 HTML |
| `build.sh python` / `build.ps1 python` | 从根项目生成 wheel/sdist，不等于带解释器和依赖的可执行运行时 |
| `web/ npm run build` | Vue 静态资源与 Electron 主进程/预加载层编译 |
| `web/ npm run electron:build` | electron-builder 入口；不证明已包含正确的 Python/Chromium 资源 |

Electron packaged 分支会查找 `resources/bin/agent-core`，Windows 使用 `.exe` 后缀。尚未验证源码构建产物是否满足这些路径和跨平台执行要求。打包时不能假定用户机器上已经安装开发机使用的 Python、依赖或浏览器。

当前桌面依赖锁定 Electron 44.8.0 / electron-builder 26.17.0。升级不改变 appId、用户数据目录、IPC 或后端启动参数，但有上游平台边界：Electron 44 不再提供 Windows ia32 / Linux armv7l 二进制，macOS 至少为 13；macOS 原生通知需要签名。Linux 图形栈、窗口定位/快捷键及 Windows 实机启停仍须重新验收，不能沿用 Electron 28 的通过记录。参见 [Electron breaking changes](https://www.electronjs.org/docs/latest/breaking-changes)。

Linux/Windows 的 `start desktop built` 从源码目录启动，使用本地 Python 环境运行后端，不进入 Electron `app.isPackaged` 分支。Web `built` 由同一 Python 后端在 8080 提供页面和 API，仅支持本机 loopback，不提供远程认证。

构建和启动脚本不会自动安装浏览器或运行软件测试。Linux 下的 Core HTTP/WS、进程恢复和 Chromium 已完成部分实际运行测试；这不代表桌面安装版已经验证。Windows 整套启停、桌面安装包及其运行资源仍需在目标系统上测试，已完成和未覆盖的范围见 [路线图](../project/Roadmap.md#当前验收状态)。

环境安装与构建命令见 [开发指南](../development/DevelopmentGuide.md)。[GitHub Actions CI](../../.github/workflows/ci.yml) 已配置 Python/前端检查与 Linux/Windows 源码构建，结果以对应提交的 Actions 记录为准。具体覆盖范围见 [测试与验收](../testing/TestPlan.md#github-actions)；独立安装包、桌面启动和真实模型仍须按下述要求验收。

主应用 Python 构建与安装读取根目录的 `pyproject.toml` 和 `uv.lock`，不从 `python/` 安装另一套分发包。独立的 `extensions/antenna_design/` 使用自己的清单和锁文件，不随普通 setup 或桌面构建安装；它的 CLI/MCP 分发也尚未完成跨平台运行验收。数值实验结果不能代替安装或宿主接入测试。

## 2. 个人桌面发布闭环

默认工作区位于安装根目录下的 `.workspace`，不得放入 `app.asar`、AppImage 挂载点或 macOS 签名包内部。启动器会把安装根目录传给 Python 后端。安装位置不可写时会报错，不会自动换目录或提权；可显式设置 `MAIGOKITE_WORKSPACE_ROOT` 指定可写位置。

源码已接入上述路径处理，但各平台的安装、升级和卸载仍须验收。升级或卸载不能自动删除工作区中的用户文件。详细路径和覆盖规则见 [配置指南](../user-guide/Configuration.md#默认工作区)。

```text
确定目标平台与版本
→ 契约/测试检查
→ 构建受锁定依赖的 Python runtime
→ 打包 Vue/Electron 与所需 Browser 资源
→ 校验、签名和安装
→ 干净机器完整 E2E
→ 备份兼容检查
→ 发布与观察 / 回滚
```

必须验证：

- [ ] 目标平台的可执行格式、架构、路径与权限正确，不从任意可写位置执行替换资源。
- [ ] 用户不装系统 Python，也能完成首次聊天、文件读取、写入审批和关闭。
- [ ] 浏览器资源可用，错误提示能定位缺失依赖；不得默默依赖开发机浏览器。
- [ ] Python API 默认 loopback；本地认证缺口明确，端口不可当作安全认证。
- [ ] 启动就绪、异常退出、重启策略和退出等待经过测试，无孤儿核心服务/MCP/浏览器进程。
- [ ] 取消确认后没有持续执行和新的非终态 UI 事件。
- [ ] 旧 SQLite 升级、检查点恢复和文件 crash/replay 通过；范围见 TestPlan。
- [ ] 敏感凭证不进入制品、普通配置、数据库或日志；导出内容已脱敏。
- [ ] 版本、Changelog、制品校验信息和验证记录一致。

代码存在或 electron-builder 执行成功，都不代表以上检查已通过。发布阻塞项统一在 [交付路线与状态](../project/Roadmap.md) 中跟踪。

## 3. 最小质量门

验收场景见 [测试与验收](../testing/TestPlan.md)，执行命令见 [开发指南](../development/DevelopmentGuide.md#4-质量与构建入口)。至少覆盖适用的静态检查与类型检查、Python/Vue 测试、协议兼容、授权安全、数据库升级、真实进程恢复和桌面 package-smoke。

缺少工具、凭证或平台时，须标记未运行/跳过，不能标记通过。模型提供方验收分别记录 PROTOCOL、LIVE 和 UNVERIFIED；不能根据模拟服务推断真实缓存收益和成本。优先复用现成 CI，不为个人项目另建发布平台或值班系统。

## 4. 配置与数据升级

沿用现有配置方式，不为发布单独创建配置 Schema。敏感凭证保存在 OS 凭证存储中；用户路径和业务数据不能写入不可变的应用资源目录。退出或替换运行资源前，须先关闭使用这些资源的活动实例。

Repository 在启动时执行兼容升级，不使用 Alembic 或独立 Migration Job。发布前须用受支持的旧库副本验证，并保留一致性恢复点；备份要求见 [备份与恢复](../ops-security/Backup.md)。一次复制业务库、检查点、文件和凭证，不保证得到全局原子快照。

## 5. 回滚与诊断

保留上一稳定制品及其数据兼容说明。如果升级后的数据库格式不兼容旧版，不能直接降级程序；须先恢复副本做演练，或明确修复向前兼容问题。

排查方法见 [日志与诊断](../ops-security/Observability.md)。发布说明只写实际功能，不能把规划中页面、空壳工作流或未验收运行时列为已交付。回滚不能恢复已撤销的权限或凭证，也不能撤销工具已经产生的副作用。

## 6. 历史容器与未来远程访问

失效的根目录 `Dockerfile`、`docker-compose.yml` 及 `scripts/deploy.sh` 已删除，不提供 Docker 部署入口。当前只使用仓库的 Web/桌面构建启动脚本；不要用历史提交中的容器配置部署当前版本。

未来如需容器部署，再处理构建上下文、非 root 运行、敏感凭证、受保护的 HTTP 入口、健康接口、数据一致性和进程关闭，并通过真实容器 E2E。个人桌面发布不要求 PostgreSQL、多副本 lease 或 Kubernetes。

手机远程访问的设计见 [手机远程访问](../design/RemoteAccess.md)。交付前必须完成设备配对和受保护的入口；部署旧 Compose 或公开 loopback 端口不能替代这些要求。
