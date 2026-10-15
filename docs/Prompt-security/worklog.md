# 工作日志：提示词安全审查

> 计划见 [plan.md](plan.md)。本文件按日期记录实际改动、验证结果和待办，不复制 [API 设计](../design/API.md) 的契约正文。

## 2026-10-16

- 按文档盘点未通过项：LAYA JA 漏拦、AR 误拦、HI/部分中文 guidance 的 XPU 崩溃，以及 KEV 部分隐蔽注入漏拦均保留为未关闭。
- 仅检查历史服务端口，8000/8009/9931/1234 均无监听；未安装/启动模型，未发真实推理请求。本轮未取得新的分类质量结果，不改写下方历史实验分数。
- 历史安全专项测试文件当前不在测试树中，完整仓库回归不能代替这些断言或真实审查整链路验收。当前边界见[专项计划](plan.md#2026-10-16-复核未通过项)，仓库回归回执见[交付路线](../project/Roadmap.md#当前验收状态)。

## 2026-10-13

### KEV 请求布局 A/B 实验

- 源码证据（`Documents\PROGRAM\kev`）：KEV 模型输入为 `<state> + question instr +
  options`（`kev/model.py` `encode`），其自带 prompt-injection 评测（`scripts/
  build_devtools_v1.py`）就是“待审文本放 `state` + 短问题”；旧 `jev_like` 把增量
  内嵌 question instructions，对 KEV 属偏离评测分布的布局。
- 本机 8009 三布局对照（12 良性 + 12 注入，中英，每条 3 次重复，结果完全一致）：
  旧布局良性均分 40.9 / 上界 63.9、存在良性反超注入的重叠；`state.message` 布局
  良性均分 17.2 / 上界 41.9、经典越狱注入 97+，首轮 16 条完全分离（AUC 1.00、
  零误差阈值 24–38）。隐蔽指令套取类注入两布局均低于默认阈值 75（共同短板）；
  指令扩展实验（+覆盖隐藏指令套取）注入均分 +4.0、良性 +2.1，收益边际，未采用。
  完整数据与结论见 [plan](plan.md)验证结果。

### 改动

- `python/agent/safety/formats/jev_like.py` — 布局改为 laya 式：增量放
  `state.message`，短指令放 `questions.prompt_safety`，`state` 设置映射为
  `state.guidance`（留空省略）；移除 `DEFAULT_STATE` 与 Model/Task/Step 元数据行。
- `python/tests/test_safety_review.py` — `jev_like` 断言改到新布局并补 guidance
  用例；修复过时的 laya 单数 `question` 断言，改为默认检查点与白名单拒绝用例。
- `python/tests/test_safety_settings.py` — 修复 `SAFETY_FORMATS` 断言补 `openai`
  （此前该用例因 openai 枚举加入而失败）。
- 文档同步：[plan](plan.md)、[configuration](configuration.md)、
  [API 设计](../design/API.md)、[模型接入与选择](../agent-capabilities/models/model-routing.md)。

### 验证

- `python/tests`：14 passed；`compileall` 通过。
- 新构造器经 `SafetyReviewer` 直连本机 KEV 端到端联调：良性 14/14 放行，
  注入 38/71/93（阈值 75 拦截经典越狱），guidance 路径服务端接受。

### KEV-4B benchmark

- 实例：`http://127.0.0.1:9931`（llama.cpp + `Kev-4B-Q4_K_M.gguf`，`decisions`
  模态实现 TypeSafe `/v1/systemone`，`model` 字段接受 `kev-latest`）。共 456 请求，
  重复间结果一致，主套件均值 ~250ms/请求。
- 布局对比（同一套 24 条汇总，指标表见 [configuration](configuration.md) §2）：
  4B 上 `inline` 反超 `message`（主套隙差 +87.4 vs +49.9、阈值 75 漏拦 0 vs 4），
  与 0.8B 的布局偏好相反；两布局 AUC 均 1.000，24 条汇总均存在零误差阈值。
- 指令扩展补测：4B 上 `message` 漏拦 7→3、`inline` 4→1（唯一漏拦为“翻译时附
  初始设定”27.3），良性上界仅 +0.5～+2.2。因需按决策模型取舍，未并入默认指令。

### jev_layout 布局可选化

- 后端：`config.py` 新增 `SAFETY_JEV_LAYOUTS = ("inline", "message")` 与默认
  `message`；DTO 新字段 `jev_layout`；PATCH 非法值返回 `SAFETY_LAYOUT_UNSUPPORTED`；
  `build_review_request` 增加 `layout` 参数，`formats/jev_like.py` 恢复双布局
  （`inline` 为迁移前逻辑原样），未知布局构造失败转保守拦截；`reviewer` 与
  runtime 透传 `settings["jev_layout"]`。
- 前端：设置页「审查请求格式」下方新增「JEV 布局」下拉（仅 `jev_like` 显示，
  标注 0.8B / 4B 推荐），`client.ts` 类型与保存投影补字段，字段搜索注册
  `safety-layout`，`en.ts` 补翻译。
- 测试与文档：`python/tests` 增至 **17 passed**（新增 inline 载荷、未知布局拒绝、
  reviewer 布局透传与配置/DTO 默认值断言）；同步 [configuration](configuration.md)
  （含 0.8B / 4B 布局实测表）、[API 设计](../design/API.md)、[plan](plan.md) 与
  [模型接入与选择](../agent-capabilities/models/model-routing.md)。

## 2026-10-12

### 方案阶段

- 评估可行性：仓库已有设置页与设置 API、LangGraph 审批/中断链路、前端任务继续/恢复入口，三块能力可复用。审查切点定在主模型每次真正发送前，不在 UI 或工具审批后补救。
- 确认边界：决策模型按工作区独立配置；阈值用 0-100；白名单按当前实际调用模型 ID 判断；审查失败保守拦截；复用现有暂停/继续链路，不建第二套审批系统。
- 计划用 engineering-docs 技能重写，收敛为“契约先行、请求构造库独立、runtime 只消费接口”的结构。
- 范围收敛：本批不动任何 UI，只输出前端可对接的接口、事件和类型，由前端组适配。

### 契约阶段

- `docs/design/API.md` 新增 `GET/PATCH /workspaces/{id}/settings/safety` 端点表单、字段与校验说明、`safety.reviewed` 事件约定、`capabilities.safety_review` 标志说明。
- 修正格式枚举：kev 是 jev 类的开源实现，枚举收敛为 `jev_like` 和 `laya`，不单独枚举 kev。
- 请求结构拆为 `state` 与 `question`：`state` 进入设置供自定义，留空用库内置默认值；`question` 由库根据审查输入生成。
- `docs/agent-capabilities/models/model-routing.md` 补决策模型独立配置与格式边界。

### Phase A（数据契约与持久化）✅

创建：
- `python/agent/safety/__init__.py` — 包入口。
- `python/agent/safety/config.py` — `safety.policy` 设置键、`safety.decision_model` 凭证引用、默认值（关闭 / `jev_like` / 阈值 75）、`normalize_safety_settings` 修复逻辑。
- `python/tests/test_safety_settings.py` — 8 个回归测试（仓库首个测试模块）。

修改：
- `python/agent/api/contracts.py` — `SafetySettings` DTO；`request_format` 用普通字符串，让端点能返回专用错误码。
- `python/agent/core.py` — `get_safety_settings` / `update_safety_settings`。
- `python/agent/api/settings.py` — 两个端点（owner_only）；格式错误抛 `APIError(400, "SAFETY_FORMAT_UNSUPPORTED")`；`api_key` 写入凭证存储不回显；写入记审计。
- `python/agent/api/app.py` — `capabilities.safety_review = true`。

### Phase B（前端契约，不含 UI）✅

- `web/src/api/client.ts` — `SafetySettings` / `SafetyRequestFormat` 类型与读写方法。
- `web/src/api/events.ts` — `SafetyReviewPayload` 类型（`safety.reviewed` 事件负载契约）。

### 验证记录

- 后端单测：`8 passed`（`python/tests/test_safety_settings.py`）。
- 导入烟测：`agent.api.contracts / agent.api.settings / agent.safety / agent.core` 导入正常，默认值与格式枚举正确。
- 前端类型检查：`web` 下 `npx vue-tsc --noEmit`，退出码 0。
- 未做端点 HTTP 联调，未运行真实决策模型验证（属于 Phase C/E）。

### 环境记录

- 本机无 `uv`；`.venv` 原本只有运行时依赖，已按开发指南 pip 分支补装 `pytest>=8`、`pytest-asyncio`。
- 测试命令：`$env:PYTHONPATH='python'; .\.venv\Scripts\python.exe -m pytest python/tests -q`。
- 系统 Python 是 miniconda 且缺 langchain；`agent/__init__.py` 急切导入 AgentCore，必须用 `.venv` 运行。

### 待办

- [x] Phase C：新增 `safety/request_builders.py`、`formats/jev_like.py`、`formats/laya.py` 和 `reviewer.py`；runtime 已接入前置切点、增量计算、白名单跳过、阈值判定及失败保守拦截。
- [x] Phase D：高风险审查复用现有结构化提问/恢复链路；结果落为 `safety.reviewed` 事件，继续或终止通过现有问题回答入口处理。
- [ ] Phase E：补审查场景测试、端点 HTTP 联调、文档收尾。
- [x] KEV 联调：`POST /v1/systemone` 已在本机 `127.0.0.1:8009` 验证，安全样例返回风险分值 0。
- [x] KEV score 校准：不再把 KEV 的 `score` 当作风险值。`jev_like` 现发送 `safe` /
  `dangerous` 两个 criterion，并读取 `legend` 对应的 `probabilities[dangerous]`，
  乘以 100 得到 0–100 `risk_score`；`risk_threshold` 保持 0–100。
- [x] LAYA 服务联调：`POST /predict` 已在本机 `127.0.0.1:8000` 验证；原单数 `question`
  载荷被 422 拒绝，已改为 `questions` 映射契约。检查点白名单、语言覆盖矩阵与服务端
  XPU 崩溃限制见 [plan](plan.md) 验证结果，`python/tests` 38 passed。
