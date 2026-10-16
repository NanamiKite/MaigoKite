# MaigoKite 前端

基于现有 Vue 3、Pinia、Vue Router、Vite 5、Electron 44 和 Python FastAPI 后端，采用「墨线」视觉语言。
桌面应用名为 MaigoKite，使用同名桌面配置目录与 `maigokite_` 缓存键，不保留旧标识兼容层；助手称呼仍可单独设置。
Chat、Memory、Settings 等页面通过既有 HTTP API 读写产品数据，不在前端维护另一套执行状态机。
首页进入空白对话；原 Overview 保留为 `/overview`，扩展、审计、数据和知识库路由继续保留，均可从万能面板打开。

## 开发与演示

```sh
cd web
npm ci
npm run electron:install
npm run dev
```

上面的 `npm run dev` 只启动前端，供 UI 单独开发。完整应用从根目录启动：Linux `bash scripts/start.sh web|desktop`，Windows `scripts/start.ps1 web|desktop`；均管理本次启动的整套服务。Web 开发模式通过 Vite 代理后端 HTTP/WebSocket 请求。

不启动后端的本地演示：

```sh
VITE_MOCK=1 npm run dev
```

发送任意消息，即可回放：过程说明 → 连续读取三个文件 → 执行命令 → 等待批准 → 写文件 → 记忆节点 → 最终回复与小结。
演示里的批准、拒绝、停止、消息排队和页面数据都保存在本次浏览器运行内，不操作真实工作区。

Electron 演示同时关闭自动启动的后端进程：

```sh
npm run electron:compile
# 另开终端用 VITE_MOCK=1 npm run dev 启动演示页面后：
VITE_MOCK=1 MAIGOKITE_EXTERNAL_RUNTIME=1 npx electron .
```

Web 编译：`npm run build:web`；Web + Electron 编译：`npm run build`。Web 构建版由 Python 后端提供静态页面。
构建使用 `tsconfig.build.json` 检查生产代码，本地测试仍由原 `tsconfig.json` 和测试入口负责，不随应用编译。npm 与根目录入口共用构建脚本，成功后记录源码摘要；`built` 启动拒绝缺失、失败或过期的构建，不会回退到旧 Electron 产物。
完整构建使用根目录 `build.sh` / `build.ps1` 的 `web|desktop|all`，再用 `start` 的 `built` 模式启动。
`npm run electron:dev` 也转调完整桌面入口；`npm run electron:preview` 启动已有构建产物，不隐式重建。
`npm run electron:build` 仍是 electron-builder 实验入口；未完成或验证 Python/Chromium 的跨平台资源打包闭环，不等同于上述源码启动。成套入口拒绝 mock/external-runtime 环境，以免普通用户误连独立旧服务；单独联调可显式使用 `npx electron .`。

## 实现边界

- `styles/tokens.scss` 是宣纸/松烟、六种强调色、字体、圆角和动画的唯一定义位置。现有组件的样式复用这些变量。
- 主题、纸纹、用户称呼、头像字和速记快捷键存于本机。助手名称继续调用现有 preferences API。
- `api/client.ts` 保留原 HTTP API；`http.ts` 提供原生 fetch 的超时、鉴权头与错误归一。
- `api/socket.ts` 复用现有 WebSocket 入口，1–30 秒指数退避，恢复时先读取持久化历史，再释放缓存的实时事件。后端没有应用层 ping/pong，因此使用 25 秒一次的 HTTP 历史请求检查活性并补齐遗漏。
- `api/event-adapter.ts` 在前端边界转换现有 message/reasoning/tool/approval/task 事件，不修改后端公开协议。
- `stores/session.ts` 保留原会话 API；`run.ts` 只维护 UI 投影，不成为另一套 Agent Runtime。流式更新按动画帧归并，Markdown 流式解析限制为每 100ms 一次，完成时立即刷新。
- 聊天记录按段加载并使用 content-visibility；Markdown 结果有大小与数量受限的缓存。
- Rail 合并连续只读步骤，失败节点自动展开，参数可折叠，输出默认 20 行，“全部”最多呈现尾部 200 行。支持缩略图和 `[` / `]` 跳转。
- 收件箱汇总跨会话待批准请求，决定仍提交现有 approval/version API。界面等待持久化 decision 事件，不把 HTTP 接受请求当作执行完成。
- 运行中 Enter 提交当前任务插话，Alt+Enter 加入下一回合待发队列；等待审批或提问时，只能排队或处理对应卡片。排队消息在成功完成后核对任务状态再发送，停止、失败或断线后暂停，需用户确认继续；插话是否消费以服务端回执为准。
- 工作区浏览、只读预览和 Git Diff 使用原来的组件/API。任务抽屉显示真实会话任务并使用原 cancel API。
- 记忆增删改使用原 memories API。人设通过已有事实类别记录，置顶为本机展示排序，不宣称存在专用人设或服务端置顶字段。
- Electron 使用 contextIsolation、sandbox、禁用 nodeIntegration、严格导航限制和 IPC sender/参数校验。preload 独立打包为单个文件。openPath 只允许真实工作区内的路径，包括符号链接边界检查。
- 托盘反映活动/待批准状态；关闭主窗口隐藏到托盘。Alt+Space 速记可配置，Enter 发到最近会话，Ctrl/⌘+Enter 新建会话。系统占用的快捷键需在设置中改用可用组合。

## 保留但不伪装成已接通的能力

能力边界统一维护在 [前端与桌面交互设计](../docs/design/FrontendDesign.md) 和 [API 设计](../docs/design/API.md)，这里不再复制一份接口缺口表。审批理由、窄范围授权、结构化问答、逐步插话、计划和进程管理已有接线，但不代表相关运行验收全部完成，见 [交付路线与状态](../docs/project/Roadmap.md)。

- 没有回合回滚接口，不提供假撤销按钮；聊天分岔不等于文件或执行状态回滚。
- 上下文窗口未知时不显示占用百分比，不用输出长度估算容量；缺少权威文件变更记录时，不伪造回合新增/删除行数。
- 计划只消费任务所属的结构化事件，不用工作区 TODO 代替。自动记忆事件仍未交付，Mock 节点不代表生产能力。
- 进程操作只针对 Core 持有的真实命令句柄，不提供任意后台进程管理或终端恢复。
- macOS 通知可提供“允许”动作，但 Electron 42+ 要求应用签名；未签名或系统拒绝通知时仍从应用内处理。其他平台通过点击通知打开会话，不假定已有同等系统能力。

## 首批 UI 检查记录

首批 UI 只做过 TypeScript 类型检查、静态 lint 和 Web/Electron 生产编译，未新增或执行 Vitest/E2E。
这是历史记录，不约束后续开发，也不代表真实 Python API、桌面通知、快捷键或最终安装包已通过运行验收。

## GUI 补缺（2026-10-10）

- 文件树的展开状态由 `useFileTree` 统一提供；列表与预览分别反馈加载/错误，旧请求不能覆盖新选择。Diff 单文件读取失败保留文件列表，可直接重试。抽屉不再继承旧文件栏的收起状态，也不嵌套第二组文件/变更标签。
- 聊天和任务抽屉共用停止入口，显示提交状态、暂停待发队列；停止失败给出提示，HTTP 响应不伪装成任务终态。待发消息可取回编辑，不覆盖输入框已有草稿。
- 命令面板与收件箱复用浏览器原生 `dialog` 的焦点约束与恢复；Escape 先关闭浮层/输入候选菜单，不穿透停止后台任务。审批快捷键只作用于当前可交互区域。
- 工具错误保留错误码、用户提示和受限的内部设置链接；历史任务按创建时间排列，结束的审批节点停止等待动画。回到最新消息按钮在回合结束后仍可使用。
- 补充会话列表加载/重连入口、窄屏导航收起、设置页查询参数回退、记忆搜索结果定位与键盘选择候选项。
- 演示模式持续标识；外观、称呼缓存、布局和记忆置顶使用独立存储键。演示渲染器不调用真实文件打开、通知、托盘更新、外观保存或速记转发，窗口控制与平台查询仍保留。开发时 `VITE_MOCK=1` 也禁止自动启动 Python 后端。

2026-10-10 的这次补缺仅做源码核对、修改文件格式化与 diff 空白检查，**未运行测试、类型检查、lint、构建或桌面联调**，未新增依赖或后端接口。后续接入与验证以路线图为准，不沿用当时的接口缺口或测试结论。
