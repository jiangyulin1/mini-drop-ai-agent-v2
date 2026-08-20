# Mini-Drop AI 功能介绍、能力边界与设计方案

> 状态：兼容说明；当前产品基线以 `ai_collector_architecture_and_migration_plan.md` 为准
> 本文保留 Runtime/实验接口背景，不得据此恢复 rules-first RCA 或默认自动恢复。
> 更新时间：2026-08-20

---

## 1. 当前 AI 功能介绍

### 1.1 运行时模式

由 `MINI_DROP_AGENT_RUNTIME` 控制，支持三种模式：

| 模式 | 值 | 说明 |
|---|---|---|
| 确定性 | `deterministic` | 不调用模型；保留人工 Evidence 工作台并明确报告 AI 未配置，不生成规则 AI 结论 |
| Pi Shadow | `pi_shadow` | 连接 Pi Sidecar，但只生成 Shadow Plan，不创建真实 Task |
| Pi 实时 | `pi` | 连接 Pi Sidecar，模型参与 Turn，仍受服务端权限/审批约束 |

当前云服务器运行在 `deterministic` 模式，`MINI_DROP_AI_ENABLED=off`，尚未启用 Pi Sidecar。

### 1.2 实验策略（仅离线 Harness）

实验 Harness 仍注册 6 种旧对照策略，用于消融与回归。生产 Pi Prompt 不再接收 Hypothesis、规则 Skill、Knowledge 选择或诊断策略提示；在线决策依据是 Case、Evidence、受引用分析、Collector Catalog、采集历史和真实预算。

| strategy_id | 思路 |
|---|---|
| `rule_tree` | 严格按症状决策树选择探针，固定、可复现 |
| `hypothesis_first` | 先生成有界候选假设，再选区分性探针 |
| `evidence_first` | 先做低成本广度证据收集，再归纳根因 |
| `causal_graph` | 先构建候选因果边，再逐边验证 |
| `exploratory` | 允许更广的已注册探针组合，追求覆盖率 |
| `hybrid` | 首轮广度扫描，后续按信息增益自适应；默认策略，兼容旧行为 |

### 1.3 RuntimeOptions

`RuntimeOptions` 将“策略思路”与“模型/思考成本”分离：

- `strategy_id` / `strategy_params`
- `reasoning_effort`：`none | low | medium | high`
- `model` / `prompt_variant` / `temperature` / `max_tokens` / `seed`
- `capture_reasoning_trace`：仅实验模式，生产不保存原始思维链

Pi 0.84.2 当前真正应用 `model`、`reasoning_effort`、`prompt_variant`；`temperature`、`max_tokens`、`seed` 作为可复现实验元数据记录，但 SDK 尚未暴露对应参数，文档中明确不伪装为已生效。

### 1.4 RuntimePolicy

`RuntimePolicy` 是运行级权限配置：

- `side_effect_policy`：`READ_ONLY` / `PROPOSE_ONLY` / `AUTO_READ_LOW`
- `enabled_tools` / `disabled_tools`
- `enabled_operations` / `allowed_risk_levels`
- `execution_mode`：`normal` / `dry_run` / `sandbox` / `deny_write`
- `auto_approve` / `require_approval_for`
- `allow_arbitrary_command`：永远为 `False`，代码强制拒绝
- `max_collection_requests`：每 Case 上限 8，只能收紧
- `max_collection_duration_sec`：每 Case 累计预留时长上限 240 秒，只能收紧

核心原则：**实验只能缩小权限，不能扩大权限**。

### 1.5 Tool Catalog 与 Tool Gateway

- `server/app/agent_runtime/catalog.py` 是唯一 Tool 规范源，当前 12 个工具。
- Sidecar 通过 `GET /internal/agent/tools/catalog` 动态拉取；失败时回退到内置列表。
- 服务端 `_tool_fence` 每次调用都重新解析本地 `ToolSpec` 和 `RuntimePolicy`。
- 工具目录只是发现元数据，不是授权凭证。

当前生产目录固定为 12 个 Evidence/Collector 工具：Case Snapshot、Evidence 列表/投影/比较、Knowledge 检索、可复用 Evidence、Collector Catalog、采集提案/状态、Evidence Analysis 提交/读取和调查结束。

旧 `evaluate_hypotheses` 和 `rca_candidate_analysis` 已从生产目录和内部路由移除。规则候选代码只保留给旧 `/diagnoses` 兼容路径与离线基线，不进入 Pi 模型工具集。

### 1.6 意图解析

`server/app/diagnosis/intent.py` 负责把自然语言解析为结构化诊断意图：

- 支持 AI 解析，失败时自动 fallback 到确定性关键词规则。
- 模型只能输出 `NormalizedIntent`，不能修改策略、权限、时间策略。
- 请求上下文中的目标、环境、时间范围优先于模型推断。
- 关键词信号（例如 `latency`、`oom`、`packet loss`）用于校正模型误分类。

### 1.7 确定性非 AI 路径

`DeterministicAgentRuntime` 不调用模型，也不会替模型选择探针或启动旧规则诊断。它只保留控制、审计和人工 Evidence 工作台；需要 AI Turn 时明确返回未配置状态。

### 1.8 Pi Sidecar

`agent_runtime/pi-sidecar` 是 Pi 运行时的内部协议服务：

- 只暴露 Mini-Drop 内部 HTTP 协议，不暴露原始 Pi RPC；
- 支持 `resume` / `turn` / `steer` / `follow-up` / `abort` / `state`；
- 工具只包含 Mini-Drop 白名单工具，禁用内置 shell/file 工具；
- 模型凭证只保存在 Sidecar 进程环境变量中；
- 支持按 `RuntimeOptions` / `RuntimePolicy` 创建或刷新 Session。

### 1.9 实验矩阵

正式方向是 `benchmarks/collector_agent_v1`：锁定模型、Prompt、Catalog、Policy、场景、Evidence hash、seed 和 provider usage，衡量 Evidence 充分率、信息目标召回、Claim Support Precision、正确停止/拒答、错误自信、下一动作和浪费采集。当前仅 3 个开发场景，不能声明正式 AI 正确率；正式模式至少要求 30 个独立 holdout 场景。

### 1.10 注册一致性

`scripts/check_registry_consistency.py`（兼容别名 `scripts/check_capability_registry.py`）检查：

- TaskKind ↔ Worker Collector；
- Probe ↔ TaskKind / Collector；
- QueryOperation ↔ Collector；
- EvidenceContract / PROBE_FACTS ↔ Probe；
- Server Tool Catalog ↔ Sidecar 兼容白名单。

CI 已纳入该检查。

---

## 2. 能力边界

### 2.1 权限边界

| 边界 | 说明 |
|---|---|
| 不能新增未注册工具 | 请求中的 `enabled_tools` / `disabled_tools` 必须来自 `TOOL_CATALOG` |
| 不能扩大风险级别 | `allowed_risk_levels` 只能是 `R0` / `R1` 子集 |
| 不能开启任意命令 | `allow_arbitrary_command=True` 直接拒绝 |
| 不能绕过 R3 审批 | 即使实验 `auto_approve=True`，R3 仍保留审批 |
| 不能绕过服务端最终判定 | Sidecar 工具目录、模型输出、API 参数都不能提升权限 |

### 2.2 模型自由行为边界

- 模型不能自由声明 `strategy_id`，只能使用服务端注册表。
- 模型不能修改 `RuntimePolicy` / `RuntimeOptions` 中的安全字段。
- 模型不能直接创建 Task / 执行 Shell / 读写文件。
- 模型只能通过白名单工具提出 `PROPOSE_ONLY` 计划或请求已注册低风险操作。
- 模型输出必须经 Schema 校验；未知字段会被拒绝。

### 2.3 思维链边界

- `capture_reasoning_trace=True` 仅实验模式。
- 生产消息只保存最终可见回答、决策摘要、工具调用序列、Evidence 引用。
- 不把模型私有思维链写入 `AssistantMessage` 或实验报告。

### 2.4 当前部署边界（云服务器）

- Control 当前运行 `deterministic` 模式，`MINI_DROP_AI_ENABLED=off`。
- 未配置 Pi Sidecar URL / Internal Token / 模型 API Key。
- 因此当前云环境只能跑离线确定性实验矩阵，不能跑 live Pi 矩阵。
- Worker 节点 Agent 容器已运行，可作为探针/采集执行端。

---

## 3. 设计方案

### 3.1 分层架构

```text
Web / API
   │
   ▼
Agent Turn API (POST /api/v1/cases/{case_id}/agent/turn)
   │
   ▼
AgentRuntimePort（可替换运行时）
   ├── DeterministicAgentRuntime（默认/控制组）
   └── PiAgentRuntimeAdapter ── HTTP ── Pi Sidecar
                                          │
                                          ▼
                                   Pi AgentSession
                                   （仅白名单工具）
```

### 3.2 数据流

1. 用户发起 Turn。
2. 服务端构建 `CaseContextSnapshot`，包含 Case、Evidence、策略、RuntimeOptions、RuntimePolicy。
3. 运行时选择 `deterministic` 或 `pi`。
4. Pi 模式由 Sidecar 创建/恢复 Session，注入策略上下文和白名单工具。
5. 模型只调用内部工具，服务端 Tool Gateway 做最终权限判定。
6. 结果落库为可审计事件：决策摘要、工具序列、Evidence 引用，不落原始思维链。

### 3.3 关键接口

| 接口 | 作用 |
|---|---|
| `POST /api/v1/cases/{case_id}/agent/turn` | 外部 Agent Turn 入口 |
| `POST /api/v1/diagnoses` | 诊断创建入口，可携带 `strategy_id` / `runtime_policy` / `runtime_options` |
| `GET /api/v1/agent-runtime/config` | 返回可用策略、工具目录、RuntimePolicy/Options Schema |
| `GET /internal/agent/tools/catalog` | Sidecar 工具发现 |
| `POST /internal/runtime/v1/cases/{id}/resume` | Sidecar 恢复/创建 Session |
| `POST /internal/runtime/v1/cases/{id}/turn` | Sidecar 提交 Turn |
| `POST /internal/agent/tools/*` | Tool Gateway 工具调用 |

### 3.4 安全模型

- 服务端是唯一授权主体。
- 工具元数据不可信，不能用于提升权限。
- 审批绑定仍保留：恢复计划执行走 `approval_binding`。
- 实验只能缩小权限，不能扩大权限。
- 策略必须来自注册表，模型不能自封策略。

---

## 4. 当前落地状态

### 4.1 已实现

- ✅ Tool Catalog 统一规范与 Sidecar 动态拉取
- ✅ 6 种 DiagnosticStrategy 注册与 deterministic / Pi 两条路径
- ✅ RuntimePolicy 参数化与权限边界校验
- ✅ RuntimeOptions 分离策略与思考成本
- ✅ Experiment Matrix 脚本与示例矩阵
- ✅ 注册一致性检查脚本 + CI
- ✅ 文档：`agent-tool-catalog.md`、`agent-runtime-experiments.md`、`agent-strategy-matrix.md`、`runtime-policy.md`、`extension-guide.md`、`registry-consistency.md`
- ✅ 本地 79 个相关测试通过

### 4.2 待推进

- ⏳ 云服务器启用 Pi 实时模式（需要配置 API Key、Pi Sidecar URL、Internal Token）
- ⏳ 云服务器跑 live Pi 策略矩阵
- ⚠️ Pi SDK `createAgentSession` 不暴露 `temperature` / `max_tokens` / `seed`，当前仅作为实验元数据记录，不伪造“已生效”
- ⏳ 更多真实 Case / Problem Registry 数据
- ⏳ 成本与延迟指标纳入矩阵报告
- ✅ 可观测性：Sidecar 事件、token 消耗、模型调用审计（`message_end` 上报 usage/cost）

---

## 5. 推进路线

### 第 1 步：云服务器启用 Pi

1. 在 Control `.env` 配置：
   - `MINI_DROP_AI_ENABLED=on`
   - `MINI_DROP_AI_API_KEY=...`
   - `MINI_DROP_AGENT_RUNTIME=pi` 或先 `pi_shadow`
   - `MINI_DROP_PI_RUNTIME_URL=http://127.0.0.1:8899`
   - `MINI_DROP_PI_INTERNAL_TOKEN=...`
2. 启动 Pi Sidecar（容器或 systemd）。
3. 验证 `/internal/runtime/v1/health` 与 `/api/v1/agent-runtime/config`。

### 第 2 步：跑 live 矩阵

- 在 `benchmarks/agent_experiments/matrix.json` 增加 live 条件。
- 使用 Sidecar 对同一批 Case 跑不同策略 / effort / policy。
- 输出新增指标：延迟、token、成本、重复一致性。

### 第 3 步：补强可观测性

- 记录每次模型调用的 provider、model、reasoning_effort、token。
- 将矩阵报告与审计事件打通。
- 确保不落原始思维链。

### 第 4 步：扩展策略与工具

- 新增策略时按 `docs/extension-guide.md` 完成注册和测试。
- 新增工具时同步 Tool Catalog、Sidecar 白名单、Gateway、测试。
- 运行 `scripts/check_registry_consistency.py`。

---

## 6. 相关文档

- [Agent Tool Catalog](agent-tool-catalog.md)
- [Agent Runtime Experiments](agent-runtime-experiments.md)
- [Agent Strategy Matrix](agent-strategy-matrix.md)
- [RuntimePolicy](runtime-policy.md)
- [Extension Guide](extension-guide.md)
- [Registry Consistency](registry-consistency.md)
