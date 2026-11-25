# antenna-design 开发交接

更新：2026-11-25。用途：交接 **antenna-design 垂直工作流的软件开发**。多个开发对话分别负责不同范围，换模型只接续对应对话的任务，不把各对话的进度合成一个“当前任务”。本文件不是应用运行时状态、设计案例记录或全仓开发进度表。

## 先读什么

1. 在当前宿主仓库开发时，先遵守该仓库的工作约束与开发指南。
2. 阅读本文件的公共边界，再定位用户指定的开发对话条目，按其文件范围阅读 [包说明](README.md) 和相关源码。
3. OB11 是设计案例，不是这里交接的软件项目；不把它的 revision、求解次数或交付结果作为工作流的开发进度，也不因接手开发而操作 `.workspace` 中的设计数据。
4. 不从 IDE 当前打开的文件推断本会话职责，不代填其他对话的进展。没有对应交接条目时，只核对当前用户任务所需信息，不接管其他开发任务。

## 多对话交接规则

- 每个开发对话单独维护一个稳定任务标识的条目。标识是人工可读的工作项名称，不冒充平台 conversation ID；换模型沿用同一条目，不另开一份重复进度。
- 各条目记录负责范围、涉及文件、已完成、进行中、待办/阻塞、验证证据和下一步。阶段性代码存在、静态检查通过、行为验收通过须分开记录，不能互相代替。
- 只更新自己的条目；其他对话的职责和状态必须有其明确交接依据才登记。未知就保留未知，不根据文件存在、修改时间或聊天片段推断已完成。
- 写入前重读，只修改相关段落，不整份覆盖。文件范围有重叠时先协调，不把文档中的“负责范围”当作文件锁或独占授权。
- 公共架构约束只记录已经确认的决定；单个对话的提议留在其条目，不能悄悄改成全局规则。跨对话依赖写清目标条目和需要的接口/产物，不替对方标记完成。
- 结束当前开发任务、发生重要决定或准备换模型时更新对应条目。只保留简短当前状态和证据入口，不追加完整聊天、日志、凭证或私有推理。

新条目使用以下结构；没有实际信息的字段标注未知，不为其他会话创建猜测条目：

```text
任务标识 / 负责对话标识（未知则注明） / 更新日期
负责范围与明确不处理的范围
涉及文件
已完成 / 进行中 / 待办与阻塞
已确认决定 / 尚待确认提议
实际验证命令、范围和结果 / 未验证项
下一步 / 跨对话依赖
```

## 不变的产品边界

- 用户描述用途、设备、材料、场地和限制；Agent 补齐影响设计的未知项，而不是要求用户先知道天线参数。
- LLM 负责需求和工程取舍；求解与数值搜索由现成组件承担。不能通过大模型逐轮猜尺寸代替优化器。
- 领域核心不依赖 MaigoKite、LangGraph 或其会话/checkpoint API。MCP 是可选入口，MaigoKite 是适配宿主；独立包将来迁出不应重做核心架构。
- 当前复用 pymininec、SciPy、scikit-rf、matplotlib、官方 MCP SDK 和标准 SQLite。确切版本以本包清单与锁文件为准，不重写数值内核、协议或优化框架。
- MMANA-GAL 只作开发验证参考，不是生产 GUI 依赖。兼容结论必须限定输入、语义及证据范围；不能由“同属 MININEC”或单个案例相近推导全面兼容。
- 原始电流、远场数组和环境记录是证据；图表由数值生成，模型只读紧凑摘要和必要局部数据。实际材料、安装、匹配与制作可行性不能以理想模型替代。

## 公共源码入口（不代表各对话完成度）

| 职责 | 当前入口与边界 |
| --- | --- |
| 自然对话工作约定 | [antenna-design Skill](src/antenna_workflow/skills/antenna-design/SKILL.md)；使用现有宿主模型和文件工具，不是独立的自动领域规划器 |
| 工程输入、身份、评价 | `contracts.py`、`identity.py`、`evaluation.py`；物理方案、计算条件、目标判据各自识别 |
| 有界搜索与复核 | `optimization.py`、`verification.py`、`matching.py`；有限参数搜索、网格证据、理想匹配，不是任意拓扑或实物验收 |
| 设计作业与持久状态 | `design.py`、`workflow.py`、`project_store.py`；SQLite 保存准入账本与最后已提交里程碑 |
| 结果与交付 | `assessment.py` 组合判据，`delivery_render.py` 生成报告，`delivery.py` 核验和发布不可变包 |
| 调用与宿主接入 | `cli.py`、`mcp_server.py`；`connection.py` 输出 MaigoKite 接入配置，不修改宿主设置 |

表中 Python 文件均在 `src/antenna_workflow/`。完整用法和能力限制以 README 对应章节为准。

`WorkflowState` 当前只有 `DESIGN_READY → SEARCH_RECORDED → VALIDATION_RECORDED → DELIVERED`，表示已提交证据，不表示 worker 活跃，也不表示 `VALIDATED / BUILD_READY`。完整 DISCOVERY/CONCEPT、结构化 Brief 的领域持久模型和制作验收还没有由这套状态实现。

## 状态与恢复的硬边界

- 执行恢复看服务实际状态与原始证据，不从本文件或聊天文字推断任务已经完成。没有状态记录的历史任务不能视为没执行过。
- `get_design_state / get_delivery / export` 可在核验原输入和完整交付后补记交付里程碑；它不是恢复 SciPy 种群，也不会重新求解。
- `physical_design_sha256` 与 `simulation_configurations`、`criteria_sha256` 分开。旧 `design_sha256` 不能改名冒充新的物理身份。
- 求解额度以项目 SQLite 与 `project_status` 为准；开发交接不维护设计案例的额度账本，也不授予新计算权限。
- `COMPUTED_NOT_VALIDATED`、`CANDIDATE_NOT_VALIDATED` 不得写成制作通过。未声明网格容差只能报告敏感性；中心频点达标不表示连续带宽达标。
- 不覆盖旧原始数值、manifest 或已发布 ZIP；证据缺失、哈希冲突、未知执行结果须先查原编号，不能用重算掩盖。

## 开发对话记录

### antenna-dev-handoff：开发交接范围纠正

- 负责对话：提出维护 `agent.md` 并澄清多开发对话边界的本对话；平台会话 ID 未提供。更新：2026-11-18。
- 本轮范围：维护天线工作流开发交接约定。不处理其他开发对话的任务，不继续 OB11 设计，不调整 Runtime 或产品功能。
- 涉及文件：本文件、包 README、`docs/design/AntennaWorkflow.md`；撤销上一轮误加到产品 Skill 和 OB11 目录的交接约定。
- 已完成：明确软件开发与设计案例的区别；按开发对话记录范围和进度；撤销“每个设计案例维护 agent.md”的未经要求的产品行为。
- 进行中：无代码开发任务。待办：其他对话的进度由各自负责者提供，不在这里汇总猜测。
- 已确认决定：`agent.md` 服务于不同模型之间的开发接续，不进入产品 Skill、模型上下文自动注入或 MCP 执行链。
- 验证范围：本轮仅文档内容与本地引用核对；未运行软件测试、构建、模型、求解器或 GUI，不代表任何工程或产品能力新通过验收。
- 下一步：换模型接手时沿用本条目的范围；如用户转向其他开发任务，定位或建立对应条目后更新。跨对话依赖：暂无已确认依赖；其他对话进度未知。

### antenna-opt-evidence：优化读写路径（内存评分、延迟发布）

- 负责对话：实施“天线优化先在内存评分，只把需要保留的结果发布成完整证据”的开发对话；平台会话 ID 未提供。更新：2026-11-25。
- 负责范围：`optimize / design` 的候选评分与证据发布路径、`project_status` 的候选状态分类及对应文档。不处理求解器、采样精度、计费规则、旧产物清理、产品默认值；补画/ZIP 去重与通用扫频留到本路径验收之后。
- 涉及文件：`src/antenna_workflow/evaluation.py`、`artifacts.py`、`optimization.py`、`mcp_server.py`、`skills/antenna-design/SKILL.md`、包 README、`docs/design/AntennaWorkflow.md`、`docs/project/Changelog.md`、`docs/project/Roadmap.md`。
- 已完成：`evaluation.py` 把“从文件加载并校验”（`_load_point`）与“用数值计算指标”（`computed_point` / `evaluate_computed`）分开，二者共用 `_measure_point` 与 `_evaluate`；`artifacts.py` 拆出 `write_numerical` 并新增 `publish_computed`，可发布已算出的 `Solution` 而不重新求解。`optimization.py` 的候选在内存求解评分，只写 `candidate-NNNN.json` 摘要；完整证据在首次可评价、整代结束且有改善、干净收尾或达标时经 `_publish_best` 写入并核验，写下 `published-NNNN.json` 发布记录后才切换 `progress.json`，发布自带的回执保存即最终内容。收尾预留按首次发布实测耗时的 1.5 倍扩展。`project_status` 新增 `summary_only` 状态和 `get_optimization` 下一步，回执或发布记录已引用但目录缺失的 run 标 `corrupt`。
- 已确认决定：`best` 在发布点之间可能是仅存在于内存的待发布候选，`result.best` 始终是磁盘证据完整的上一份最佳。只有干净停止才在收尾发布；异常路径不重试（半成品目录无法原地重试），保留上一份已提交最佳，发布失败时 stop_reason 记为 `failed`。发布记录先于回执切换写入，使曾被发布后又被替换的最佳在目录丢失时仍按证据缺损处理。数值目录的 `elapsed_seconds` 只计求解时间。
- 验证证据：独立包 `ruff check src`、`ruff format --check src`（23 文件）、strict `mypy src`（22 模块）通过，环境按锁文件新建（Ruff 0.17.1、mypy 2.4.0）。未读取或修改测试源码。
- 未验证项：薄线多频数值基线（逐频增益、前后比、R+jX、SWR、评分、最佳参数、准入次数与旧路径对比）、峰值内存与读写量/耗时实测、四类中断取回、取消等待、求解、GUI、Windows 与远程 CI 均未运行，需另行授权。
- 下一步：获授权后先跑数值基准与中断验收；通过后再做补画/ZIP 去重和通用扫频。跨对话依赖：暂无。
