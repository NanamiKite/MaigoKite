# 提示词安全审查服务配置指南

> 状态：当前有效
> 权威范围：三种请求格式（`jev_like` / `laya` / `openai`）的审查服务配置方法与选型边界
> 接口字段与错误码以 [API 设计](../design/API.md#36-设置模型和凭证引用) 为准；验证证据、
> 语言覆盖矩阵与已知限制见 [提示词安全审查计划](plan.md)；审查模型的配置归属见
> [模型接入与选择](../agent-capabilities/models/model-routing.md)。

## 1. 通用配置

设置入口：设置页「安全」标签（位于「权限」之后，支持 `?tab=safety` 深链与字段搜索），
或对应的安全配置 API（GET 读取 / PATCH 保存）。配置按工作区持久化，不随会话变化。

| 字段 | 说明 | 默认 |
| --- | --- | --- |
| `enabled` | 审查总开关；为 `true` 时必须提供 `decision_base_url` 与 `decision_model` | `false` |
| `request_format` | `jev_like` / `laya` / `openai`，只接受这三个值 | `jev_like` |
| `decision_base_url` | 审查服务地址；三种格式的后缀拼接规则见下文各节 | 空 |
| `decision_model` | 决策模型；含义随格式不同，见下文各节 | 空 |
| `state` | 自定义状态/指令文本；三种格式的映射方式不同 | 空 |
| `trusted_model_ids` | 命中即跳过审查的聊天模型 ID（按当前步实际调用模型匹配），最多 32 个 | 空 |
| `risk_threshold` | 0–100 整数；`risk_score > risk_threshold` 时拦截，等于阈值放行 | `75` |
| `api_key` | 仅出现在 PATCH 请求中，按 `safety.decision_model` 凭证引用保存；GET 只返回 `credential_configured` | 保留既有凭证 |

通用语义（三种格式一致）：

- 审查请求由 `state` 与库生成的 `question`/提问组成；`state` 留空时采用各格式的库内置行为。
- 风险分统一为 0–100 的 `risk_score`（服务返回的 0–1 概率 × 100），超阈值拦截。
- 请求构造失败、网络失败/超时、响应缺少合法概率或结构非法时，一律**保守拦截**
  （`reason=review_error`），复用「继续/终止」人工决定，不放行也不伪装成功。
- 凭证不回显：密钥留空保留既有凭证，更换服务地址必须重新提供密钥或移除旧凭证。
- PATCH 会补齐 DTO 默认值，须先读取再提交完整配置；未知字段与非法枚举被拒绝
  （`SAFETY_FORMAT_UNSUPPORTED`）。

## 2. `jev_like`（含开源实现 KEV）

- **端点**：`decision_base_url` 不以 `/v1/systemone` 结尾时自动补全——裸地址
  （如 `http://127.0.0.1:8009`）会拼成 `.../v1/systemone`，以 `/v1` 结尾则拼 `/systemone`。
- **`decision_model`**：按审查服务声明的模型名填写，作为请求中的 `model` 字段传给服务。
- **`jev_layout`（布局，二选一）**：设置页「JEV 布局」配置；非法值 PATCH 返回
  `SAFETY_LAYOUT_UNSUPPORTED`，运行时构造失败按审查失败保守拦截。
  - `message`（默认）：待审查增量放入 `state.message`，`questions.prompt_safety`
    为 `score` 类型、criteria 固定 `safe` / `dangerous`，instructions 为引用
    `message` 的固定短指令；`state` 设置非空时作为 `state.guidance` 附加并被
    instructions 引用（与 `laya` 一致；guidance 可能降低区分度，建议留空）。
  - `inline`：待审查增量连同 Model/Task/Step 元数据内嵌 `questions.prompt_safety`
    的 instructions，`state` 为静态分类指令（留空用库内置指令，自定义整体替换）。
- **布局实测**（同一套 12 良性 + 12 注入（中英）× 每条 3 次重复，重复间结果一致；
  指标为良性上界 / 注入下限 / 隙差 / 最佳可达阈值误差 / 默认阈值 75 漏拦数）：

  | 决策模型 | 布局 | 良性上界 | 注入下限 | 隙差 | 最佳阈值误差 | 漏拦@75 |
  | --- | --- | --- | --- | --- | --- | --- |
  | 0.8B `kev-latest`（`:8009`） | `inline` | 63.9 | 51.6 | −12.3（重叠） | 2 | 8/12 |
  | 0.8B `kev-latest`（`:8009`） | `message` | 41.9 | 26.6 | −15.4 | 1 | 7/12 |
  | 4B Q4_K_M llama.cpp（`:9931`） | `inline` | **6.0** | 12.8 | **+6.9** | **0**（阈值 6） | **4/12** |
  | 4B Q4_K_M llama.cpp（`:9931`） | `message` | 11.0 | 13.1 | +2.1 | 0（阈值 12） | 7/12 |

  结论：**0.8B 选 `message`**（`inline` 在 0.8B 上存在良性反超注入的重叠，
  `message` 把良性上界压低 22 分、最佳阈值误差减半），**4B 选 `inline`**
  （间距更大、默认阈值漏拦更少；4B 主套件上 `inline` 隙差 +87.4、75 阈值
  0 漏拦，且两种布局 AUC 均为 1.000）。分轮明细与指令扩展实验（4B 上
  `inline`+扩展指令可达 1/12 漏拦）见 [计划](plan.md)验证结果。
- **请求/响应**：读取响应 `legend` 对应的 `probabilities[dangerous]` × 100 作为
  `risk_score`；KEV 返回体中的 `score` 索引值不作为风险值。
- **实测证据**：本机 `POST http://127.0.0.1:8009/v1/systemone` `message` 布局端到端
  联调通过，良性 14 分放行、注入 93 分拦截，见 [计划](plan.md)验证结果。

## 3. `laya`

- **端点**：`decision_base_url` 不以 `/predict` 结尾时自动补（如
  `http://127.0.0.1:8000` → `http://127.0.0.1:8000/predict`）。
- **`decision_model`（白名单）**：只接受 `english` 与 `typed-decisions`，留空默认
  `typed-decisions`，其余值在请求构造时失败并保守拦截。`multilingual` 实测对良性/
  注入反向误判（良性 93–99 / 注入 86–94），已禁用。审查请求必须显式指定检查点，
  不使用服务端自动路由——自动路由会按文本检测选中 `multilingual`。
- **`state`**：留空时不携带附加字段；非空时作为 `state.guidance` 附加，
  instructions 同时引用 `guidance` 与 `message`。注意附加 `guidance` 已知会降低
  区分度并与服务端崩溃相关，建议留空。
- **布局**：待审查增量放入 `state.message`，`questions.prompt_safety` 为 `score`
  类型、criteria 固定 `safe` / `dangerous`。指令内嵌增量的布局在 Laya 上无区分度，
  不要改回。
- **语言覆盖（阈值 75，良性/注入分值）**：EN `7/95`、ZH `10/87`、RU `22/86` 通过；
  FR `24/97`（`typed-decisions`）；**JA 注入漏拦（`40/62`）、AR 良性误拦（`81/92`）、
  HI 触发服务端 XPU 崩溃**——前两者是已测不达标，HI 是已测服务端故障，不是从未测试；其风险分不可信。2026-10-16 外部服务未运行，未能复测，均未关闭，见[专项回执](plan.md#2026-10-16-复核未通过项)。
- **服务端注意**：`/health` 返回 200 不代表推理可用（后端故障时仍 200）；已知
  `guidance` 叠加中文注入文本、以及 HI 注入文本会触发服务端 `level_zero` 崩溃，
  崩溃后需重启服务。服务崩溃期间审查请求失败，运行时按保守拦截处理。

## 4. `openai`（OpenAI 兼容结构化输出）

- **端点**：`decision_base_url` 不以 `/chat/completions` 结尾时自动补
  （如 `http://127.0.0.1:1234/v1` → `.../v1/chat/completions`）。
- **`decision_model`**：目标服务上的聊天模型 ID（如 LM Studio 的
  `google/gemma-4-12b-qat`），作为 chat/completions 的 `model` 字段。
- **消息映射**：`state` 作为 system 消息（留空用库内置默认指令），并始终追加
  JSON 输出格式说明；`question` 作为 user 消息，含模型 ID、任务/步骤元信息与
  待审查增量。
- **结构化输出**：`response_format` 优先 `json_schema`（strict）约束
  `{"risk_probability": <0–1>, "reason": <字符串>}`；服务以 400/404/415/422 拒绝时
  自动降级 `json_object` 重试一次（schema 说明已在 system 消息中）。
- **解析**：读取 `choices[0].message.content` 中的 JSON（容忍 markdown 围栏）；
  `risk_probability` 缺失或越界、`reason` 缺失、JSON 不可解析时保守拦截，
  不回退自由文本打分。概率 × 100 形成 `risk_score`。
- **实测证据**：LM Studio `http://127.0.0.1:1234/v1` + `google/gemma-4-12b-qat`：
  良性 `0.0 → 0` 放行、注入 `1.0 → 100` 拦截，均未触发降级，见 [计划](plan.md)。

## 5. 选型与排查

**选型**：本机 KEV/jev 类服务选 `jev_like`；本机 Laya 服务选 `laya`；任何支持
chat/completions 与结构化输出（或可降级 json_object）的 OpenAI 兼容服务选 `openai`。

| 现象 | 排查方向 |
| --- | --- |
| 405 / 404 | `decision_base_url` 拼接错误，核对各格式后缀规则与实际路径 |
| 422 | `decision_model` 不合法：`laya` 检查白名单，`openai` 核对模型 ID 是否存在 |
| 持续保守拦截（`reason=review_error`） | 服务不可达、超时或响应结构缺 `dangerous`/`risk_probability` 概率字段 |
| `laya` 健康检查 200 但持续失败 | 服务端推理后端已崩溃，重启 Laya 服务 |
| 良性/注入分值贴在一起或反向 | 超出语言覆盖或检查点不适用，对照 [计划](plan.md)语言矩阵 |
| 应放行却拦截 | 确认 `risk_threshold`（等于阈值放行）与 `trusted_model_ids` 是否覆盖该模型 |
