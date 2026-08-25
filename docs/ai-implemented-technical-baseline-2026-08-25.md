# Mini-Drop 已落地 AI 技术基线

> 核验日期：2026-08-25  
> 代码基线：`ed5c62b`（`main`）  
> 文档性质：以当前代码、迁移、运行配置、测试和真实评测记录为依据的实现说明；不把历史规划或演示设想写成已交付功能。

## 1. 结论与产品边界

Mini-Drop 当前落地的是一个 **Evidence-native（证据原生）受监督运维调查系统**。它不是自由执行 Shell 的自治 Agent，也不是用规则直接给出根因的 RCA 产品。

在线主线的职责划分如下：

```text
操作员 / 告警 / 初始 Task
        |
        v
Case + Branch Workspace（状态与范围真源）
        |
        v
Pi Agent Runtime（模型推理、提出假设/缺口/下一步）
        |
        v
Tool Gateway（schema、权限、revision、generation、预算再校验）
        |
        v
CollectionProposal -> CollectionRequest -> Task -> Worker Collector
        |
        v
Artifact -> CaseEvidence -> EvidenceProjection
        |
        v
EvidenceAnalysis / Hypothesis / Gap / CausalGraph / ConclusionRevision
        |
        v
人工复核、排除、纠正、暂停、聚焦或受审批恢复
```

模型只能读取受限的 Case/Evidence Projection，或通过注册工具提交提案和结构化调查状态；确定性后端拥有 Task、Evidence 生命周期、权限、预算、审计、结论入库和实际执行权。知识库可以辅助解释，但不属于当前事故 Evidence。

当前可以对外描述为：**具备真实模型驱动、证据绑定、受控采集、分支调查、人工干预和可审计结论的运维调查闭环。**

当前不得描述为：通用生产根因定位准确率、自动修复、任意主机命令执行、完整拓扑 RCA、完整多支持集真值维护，或生产自治。

## 2. 架构组件与所有权

| 层级 | 已落地组件 | 所有权与职责 |
|---|---|---|
| 交互层 | React `AIDiagnosisWorkspace`、`CanonicalCaseWorkspace`、`InvestigationWorkbench`、Evidence/知识抽屉 | 展示 Workspace、分支、采集、Evidence、结论和人工操作；不推断后端状态 |
| 控制面 | FastAPI Case/V6 路由、Repository、SQLAlchemy/Alembic | Case、租户、scope/control revision、持久化和 API 权威 |
| 推理运行时 | `AgentRuntimePort`、Deterministic、Pi Adapter、Shadow Adapter | 运行一轮 Agent Turn；不拥有业务真相，不直接执行采集 |
| 模型边车 | Node.js Pi Sidecar，`@earendil-works/pi-coding-agent` 0.84.2 | 维护内存 Session、拼装 prompt、调用 Provider、仅暴露内部 HTTP 协议 |
| 工具网关 | `server/app/v6_routes.py` 和 Tool Catalog | 每次工具调用重新验证 Case、策略、scope、generation、Evidence 与参数 |
| 调查状态 | `InvestigationStateService`、V6 持久化模型 | 假设、缺口、因果图、树、Evidence 依赖、结论 revision 的唯一写入口 |
| 执行面 | `CollectionSupervisor`、Task/Attempt、Linux Agent、Analyzer | 将模型提案编译为原生受控 Task，采集并产出 Artifact |
| 证据面 | CaseEvidence、Projection、Review、AnalysisRun、Confidence Ledger | Evidence 版本、引用、治理、失效传播与输入指纹 |
| 异步可靠性 | Domain Outbox、Runtime Wakeup、consumer effect、Sidecar JSONL spool | 从任务/复核事件可靠恢复后续 Agent Cycle；Sidecar 内存不是权威状态 |

核心代码入口：

- 运行时契约：`server/app/agent_runtime/port.py`
- 运行时选择：`server/app/agent_runtime/dispatcher.py`
- Pi 适配器：`server/app/agent_runtime/pi_adapter.py`
- 统一工具目录：`server/app/agent_runtime/catalog.py`
- Tool Gateway 和调查 API：`server/app/v6_routes.py`
- 采集编译器：`server/app/diagnosis/collection_supervisor.py`
- 调查状态：`server/app/diagnosis/investigation_state.py`
- Evidence 分析：`server/app/diagnosis/evidence_analysis.py`
- Pi Sidecar：`agent_runtime/pi-sidecar/src/{server,runtime,tools,event-spool}.mjs`

## 3. 核心数据模型

### 3.1 Case、分支与运行时围栏

`IncidentCase` 是 Case 级状态真源。Agent 每次运行接收的是 `CaseContextSnapshot`，其中包含 Case goal、target scope、计划、当前可见 Evidence 摘要、假设、Gap、结论历史、预算、Focus、干预信息和版本号。

为阻止迟到写入覆盖新状态，系统持久化并校验：

- `control_revision`：控制命令、暂停/恢复/停止等变更；
- `scope_revision`：服务、进程或拓扑范围变更；
- `plan_revision`：调查计划变更；
- `evidence_watermark`：当前可见 Evidence 进度；
- `runtime_generation`：运行时 Session 的代际围栏；
- `branch_id`：分支局部调查可见性与状态范围。

`AgentRuntimeBindingModel` 保留 Case-wide 兼容绑定；新分支使用 `AgentRuntimeBranchBindingModel`，其唯一键为 `(case_id, tenant_id, branch_id)`。`AgentRuntimeTurnModel`、`AgentCycleModel`、`ModelRequestModel`、Assistant Message，以及假设、Gap、因果图、结论和 Evidence 依赖都支持 `branch_id`。迁移 `0037_branch_reasoning_scope` 让历史数据保持 `NULL` 的 Case-wide 兼容范围，迁移 `0038_branch_runtime_fencing` 增加分支级 binding 与 generation fencing。

### 3.2 Evidence 三层与治理

Evidence 不以“模型工具结果文本”存储，而分为三层：

```text
Artifact
  原始 Worker/Analyzer/Source 产物，可校验、下载、定位。

CaseEvidence
  Case 内稳定身份；关联 Task、Artifact、来源、目标、实例身份、hash、生命周期和 review。

EvidenceProjection
  对模型和 UI 可见的有界、确定性内容；带 projection kind/version/hash。
```

`CaseEvidenceModel` 记录 provenance、target、resource incarnation、content hash、projection hash、source channel 和 data origin。`EvidenceProjectionModel` 以 `(evidence_id, projection_kind, projection_version)` 表示不可变快照；同版本内容变更必须新建版本。模型读取的是 Projection，不读取无限制原始文件。

Evidence 治理由追加式 `EvidenceReviewRevisionModel` 表达，核心维度相互独立：

- 生命周期：`ACTIVE`、`EXCLUDED`、`INVALID`、`SUPERSEDED`；
- 人工信任：`UNREVIEWED`、`TRUSTED`、`LOW_TRUST`；
- UI 组织状态不等同于推理准入。

Review 不删除 Artifact 或历史结论。Evidence Review 的 `apply_evidence_review` 在同一数据库事务内写入 Review revision、更新 Evidence 准入状态、将关联 `CURRENT` Analysis 标记为 `STALE_INPUT`，并传播到受影响 Hypothesis/Claim/Investigation Tree/Conclusion，再按影响策略围栏 Runtime、触发自动重新校准或等待人工批准重启。Projection 变化不依赖一个单独的“变更监听”来提前改写所有 Analysis；旧 Analysis 在完成时重新读取最新 Projection hash，由 stale fence 拒绝过期提交。

### 3.3 调查推理状态

当前已持久化以下结构，而非把模型回答仅保存为聊天文本：

| 结构 | 作用 |
|---|---|
| `CaseHypothesisNode/Edge` | 候选假设、支持/反证 Evidence、替代解释和置信度 |
| `EvidenceGap` | 缺失事实、阻塞 Claim、冲突 Evidence、下一步与可重试性 |
| `InvestigationTreeNode/Dependency/Event` | 分支局部的假设、义务、主张、决策及其显式依赖和失效重放轨迹 |
| `EvidenceDependencyEdge` | Evidence 到 Hypothesis/Claim/Causal Node/Edge 的支持或反证绑定 |
| `CausalGraphRevision/Node/Edge` | 有 Evidence 支撑的因果图版本；Dependency Graph 不可替代因果图 |
| `EvidenceAnalysisRun` | 固定输入集的模型分析和字段/span 引用结果 |
| `ConclusionRevision` | 当前或历史结论、拒答、限制、Evidence 绑定和 revision |
| `ConfidenceChainSnapshot/Adjustment` | 可解释的 trust、freshness、scope、directness、independence 等评分链 |

`INSUFFICIENT_EVIDENCE` 是合法终态。它不是“模型失败”的占位符，而是缺少可验证根因时的受约束拒答：可在零 Evidence 下提交，但必须包含明确拒答理由与 Evidence Gap。非拒答的结论必须给出根因位置、机制、支持/反证、限制和精确 Evidence/Projection/字段引用。

## 4. Agent Runtime 与模型调用

### 4.1 可替换运行时

`AgentRuntimePort` 定义六个操作：`start_or_resume`、`submit_turn`、`steer`、`follow_up`、`abort`、`get_state`。业务模型不依赖某一家 Agent SDK。

| 模式 | 环境值 | 已实现行为 |
|---|---|---|
| Deterministic | `MINI_DROP_AGENT_RUNTIME=deterministic` | 不调用模型；保留控制、审计和人工 Evidence 工作台 |
| Pi Shadow | `pi_shadow` | 调用 Pi Sidecar 生成 Shadow Plan；不创建真实 Task |
| Pi | `pi` | Sidecar 调用真实 Provider；仍只能通过 Tool Gateway 提案/提交 |

若未设置显式模式但设置了 Pi URL，服务端选择 `pi`；无 URL 时回落到 `deterministic`。模式切换需重启，以避免一个 Case 在运行中被半切换。

### 4.2 Pi Sidecar

Pi Sidecar 是 Node.js 内部服务，不暴露原始 Pi RPC。它只接受以下内部路由：

```text
POST /internal/runtime/v1/cases/{id}/resume
POST /internal/runtime/v1/cases/{id}/turn
POST /internal/runtime/v1/cases/{id}/steer
POST /internal/runtime/v1/cases/{id}/follow-up
POST /internal/runtime/v1/cases/{id}/abort
GET  /internal/runtime/v1/cases/{id}/state
GET  /internal/runtime/v1/health
```

模型密钥只从 Sidecar 环境读取，例如 `DEEPSEEK_API_KEY` 或 `MINI_DROP_AI_API_KEY`；Python Server 适配器不会读取或传递模型密钥。Sidecar 按 Case 维护内存 Session，但服务端的 binding、Context Snapshot、Cycle、Turn 和 Event 才是恢复权威。Sidecar 重启后，Pi Adapter 检测到缺失的内存 Session 时旋转 generation，并用最新 Snapshot 重建会话。

Sidecar 禁用 Pi 的 shell、文件、编辑和目录工具，只向模型注册 Mini-Drop 内部工具。它对 prompt 做确定性字符预算裁剪，保留关键 revision、intervention、Evidence 引用和省略计数；不会截断 JSON 形成不可审计上下文。生产不保存原始私有思维链，只保存面向用户的回答摘要、工具调用、哈希、用量和事件。

`EventSpool` 在 Sidecar 本地以 JSONL 暂存未被 Server 确认的事件，按 idempotency key 去重；确认后才移除。它用于传输可靠性，不能取代服务端 Outbox/数据库状态。

### 4.3 Prompt 与行为约束

系统 Prompt 明确要求模型：先读取 Case Snapshot 和 Evidence；干预后先 `acknowledge_intervention`；知识只能作为背景；Evidence 不足时记录具体 Gap；新采集被接受后结束当前 Turn 并等待 durable wakeup；不能把 Dependency Graph 当因果；终态必须经 `finish_investigation` 提交；每个非拒答主张引用 Evidence ID、Projection hash 和 field/span。

Prompt 是行为引导，不是安全边界。任何模型输出仍必须通过 Tool Gateway 和状态服务验证。

## 5. Tool Catalog、策略与安全边界

### 5.1 当前工具目录

`server/app/agent_runtime/catalog.py` 是工具 Schema、内部路径和权限类型的唯一规范源。当前目录有 25 个工具，Sidecar 启动时通过 `/internal/agent/tools/catalog` 拉取并与本地兼容白名单交叉验证；拉取失败时才使用随版本发布的兼容回退。目录只是发现元数据，不能授予服务端权限。

| 类别 | 工具 |
|---|---|
| 干预与读取 | `acknowledge_intervention`、`get_case_snapshot`、`list_case_evidence`、`get_evidence_projection`、`compare_evidence`、`search_knowledge`、`get_causal_graph`、`get_dependency_graph`、`get_evidence_gaps`、`get_investigation_tree`、`find_reusable_evidence`、`list_collectors`、`get_collection_status`、`get_evidence_analyses` |
| 调查与采集提案 | `propose_collection`、`discover_topology`、`propose_plan_revision`、`propose_hypothesis_revision`、`propose_investigation_tree_node`、`propose_investigation_tree_dependency`、`record_evidence_gaps`、`propose_causal_graph`、`propose_evidence_dependency` |
| 受控提交 | `submit_evidence_analysis`、`finish_investigation` |

工具权限类型为 `READ_ONLY`、`PROPOSE_ONLY` 或 `WRITE`。即使工具名为 proposal，服务端仍会进行 schema、租户、Case 状态、可见 Evidence、revision、generation、risk、预算和审批检查；模型没有 Task、Shell、文件或任意网络命令能力。

### 5.2 RuntimePolicy

每个 Turn 可携带 `RuntimePolicy`。核心字段为：

- `side_effect_policy`：`READ_ONLY`、`PROPOSE_ONLY`、`AUTO_READ_LOW`；
- 工具 allow/deny 集；
- `allowed_risk_levels`，代码上限为 `R0/R1`；
- `execution_mode`：`normal`、`dry_run`、`sandbox`、`deny_write`；
- `max_collection_requests`，上限 8；
- `max_collection_duration_sec`，上限 240 秒；
- 审批条件。

请求只能缩小代码定义的权限，不能扩大。`allow_arbitrary_command=True` 直接拒绝；未注册工具拒绝；非实验场景的 `auto_approve` 拒绝，且 R3 永远不能被解除审批。`_tool_fence` 在每次调用时重新解析政策，不信任 Sidecar 已做的判断。

### 5.3 干预、暂停与聚焦

操作员可以通过聊天或 Case Command 暂停、停止、恢复、纠正或切换服务/进程 Focus。命令先写入 Case 控制面，再尝试通知 Runtime，不依赖模型理解自然语言。Focus 切换执行 scope/control revision CAS；未知 PID 或没有 Discovery Evidence 的进程会被拒绝。变更后旧 Scope 的 Turn 被围栏，Runtime 收到 `abort` 和 `steer(FOCUS_CHANGED)`；Sidecar 不可用时，Case 状态仍持久化并呈现 `pending`。

Evidence Review 形成干预屏障：在后续非确认工具之前，模型必须确认精确 intervention ID，并重新读取 Evidence 生命周期/revision。旧 generation 的迟到工具或 finish 只能保留审计，不能覆盖 current 状态。

## 6. 采集、分析与结论闭环

### 6.1 Collector Catalog 与 Linux 执行

Collector 目录来自 `mini_drop_contracts/catalog/collectors.v1.json`，由 `collector_spec.py` 加载和 hash。当前注册的 AI 可选采集器为：

- R1：`memory_smaps`、`sys_metrics`、`process_scan`、`network_discovery`、`log_scan`、`runtime_snapshot`、`connection_probe`；
- R2：`perf_cpu`、`pyspy`、`continuous_perf`、`java_async`、`go_pprof`、`ebpf_io`。

目录声明目标类型、参数 Schema、所需 Worker capability、风险、时长、样本率、预计开销、最大产物大小、Artifact 类型和 Projection 类型。Collector 是否可用还取决于目标 Linux 内核、权限、容器 namespace、工具安装和进程 incarnation；目录中存在不代表每台 Worker 都可以执行。

模型提交 `propose_collection` 后，`CollectionSupervisor` 按以下顺序确定性编译：

```text
Proposal
  -> 校验 Case/tenant/revision/generation/CollectorSpec/target identity
  -> 校验 capability/risk/approval/budget/input Evidence/idempotency
  -> CollectionRequest
  -> 原生 Task 和 TaskAttempt
  -> Worker Collector
  -> Artifact / Analyzer
  -> CaseEvidence + EvidenceProjection
  -> Outbox / RuntimeWakeup
```

模型不能先将提案标为已接受再异步尝试创建 Task。真正的接受点按当前实现是：`CollectionRequest` 创建成功、幂等 Task 创建成功后，再把 Request 标记为 `DISPATCHED` 并把 Proposal 标记为 `ACCEPTED`。这些步骤由 Supervisor 以顺序化的 Repository 调用完成；文档不把它扩大为所有部署路径上的单一数据库事务。审批恢复会复用原 Proposal、原参数和原工具调用身份，并重新校验当前 revision，不能让模型重新生成一个“类似”的调用。

### 6.2 Evidence Analysis

`EvidenceAnalysisRun` 的输入由 Evidence ID、每条 Evidence 的 Review Revision、Projection ID/hash、analysis mode、模型配置和 prompt version 固定，并计算 `input_fingerprint`。相同输入复用同一运行，避免重复消耗 Provider。Evidence Review 的事务路径会直接把关联 `CURRENT` 运行标记为 `STALE_INPUT`；Projection hash 的变化则在分析完成时被 `_stale_input_reasons` 检出，并将该运行置为 `STALE_INPUT` 后拒绝提交。

分析提交与完成都执行 fence。模型事实必须引用 pinned Evidence/Projection 及精确字段路径或文本 span，支持 `items.0.value`、`items[0].value`、`projection.items[0].value` 等路径格式，但最终都解析到 Projection 内容。`LOW_TRUST` 可用于探索但不能独自支撑高确定性结论；`EXCLUDED` 默认不能进入当前多 Evidence 分析或最终报告。

### 6.3 假设、Gap、因果与结论

一个 Agent Cycle 的预期步骤是：观察当前 Snapshot；修订竞争假设与反证；识别一个阻塞决策的事实缺口；按信息增益、风险、成本与预算比较可选 Collector；提交一项采集或对现有 Evidence 分析；等待任务/审批/Review 边界；唤醒后重建 Snapshot；最后提交带引用的结论或明确拒答。

因果图只允许在存在机制性 Evidence 时提交。`DependencyGraph` 只表达时间窗内观察到的通信/依赖，语义为 `dependency_only_not_causal`；它不能单独支持因果图或根因结论。模型还需用 `propose_evidence_dependency` 把 Evidence 与 Hypothesis/Claim/Causal Node/Edge 的支持或反证关系显式保存。

### 6.4 动态拓扑发现

`network_discovery` 采集 `/proc`/socket 信息（macOS 开发环境可走 `lsof` 降级），由 Discovery Frontier 做受限 BFS。预算包括 hop、host、process、edge、并发 Task；输出端点、进程 incarnation、监听/连接观察、覆盖率和 Evidence 引用。

对新发现 PID 的采集不由模型一句话授权。系统要求 discovery run、活跃 Discovery Evidence、Membership Snapshot、目标实体、boot ID、process start time 以及当前 scope/control revision 同时成立。未托管外部端点、虚拟端点和未解析实体不能直接成为采集目标。

## 7. 知识、记忆、MCP 与非 AI 模型调用

### 7.1 知识与 Case Memory

`KnowledgeDocument`、`KnowledgeChunk` 和 `CaseMemory` 已持久化，支持文本/文档导入、分块、检索、Case 记忆维护和提升。模型可调用 `search_knowledge` 检索历史 runbook、领域知识或记忆片段，但系统 Prompt、Tool Schema 和状态验证均要求它们仅作为背景：Knowledge chunk 必须单独引用，不能充当本 Case 的 Evidence 或直接证明根因。

本地 embedding Provider 可选；代码支持词法/网络无关回退，生产启动不依赖必须下载本地模型。

### 7.2 MCP 与外部 Source

MCP Server 入口为 `mini-drop-mcp`，并有 Source、Probe、Grant、Policy 和 Action 的授权合同。外部 Source 访问需要显式授权，访问审计与范围/策略检查保留在 Server。MCP 不是模型的任意网络出口；MCP 开关由 `MINI_DROP_AGENT_MCP_ENABLED` 控制。

当前 Source/MCP 和 Task Artifact 都可以物化为 canonical Evidence/Projection，但二者的完全统一 Ingestion contract 仍被列为后续工作，不能假定所有旧 Source 路径都拥有与原生 Task 相同的 lineage 与失效语义。

### 7.3 Server 侧 AI 辅助能力

Server 还保留 AI Provider、意图解析与输入校验模块。意图解析可以调用模型并在失败时回退到确定性关键词规则；其输出仅是 `NormalizedIntent`，不能修改权限、策略、时间范围或确定性 Scope。旧规则诊断、策略矩阵和候选排名只保留为兼容读取或离线评测基线，不属于 Pi 在线调查主脑。

## 8. 前端已落地能力

`web/src/pages/AIDiagnosisWorkspace.jsx` 是当前 AI 工作区入口，支持从 Task 创建 Case、发起 Turn、刷新 Workspace、切换分支和接收 Case SSE。`CanonicalCaseWorkspace` 将以下内容聚合到同一 Case 视图：

- Case 状态、当前 Focus、revision、Runtime 活动与当前结论；
- 分支列表、分支可见 Evidence、Evidence promote；
- 信息目标、Collection Proposal/Request/Task 时间线与审批状态；
- Evidence 列表、Projection、Review、失效/重检提示和引用跳转；
- Hypothesis、Evidence Gap、Dependency/Causal Graph、Conclusion History；
- Agent 消息、运行时事件和 Provider/模型调用审计；
- Knowledge/Memory Drawer、Worker 状态和人工控制。

`InvestigationWorkbench` 对计划和采集活动提供状态分组、取消/移除队列步骤、fan-out、Evidence Review 覆盖和离线状态展示。前端应只使用 Workspace 聚合 API 与事件流呈现事实；不从消息文本推断是否已经完成或是否允许执行。

## 9. 审计、可观测性与可靠性

系统已记录的关键审计实体包括：

- Runtime binding、Turn、Event、Cycle、Context Snapshot；
- Model Request/Response、provider/model、token/cost/latency、response hash；
- Tool start/end、参数 hash、结果 hash、重试次数、Evidence 引用和 HTTP 状态；
- Proposal、CollectionRequest、Task、TaskAttempt、Artifact、Evidence、Review、AnalysisRun；
- Case Command、Focus、Approval、Investigation Tree Event、Conclusion Revision。

Runtime 事件以 `(case, tenant, runtime_generation, event_seq)` 作为持久化身份维度，并用 idempotency key 处理 Sidecar 崩溃重放；当前代码证明的是 generation fence、唯一约束和重复事件去重，不是对 event_seq 到达顺序的全局排序保证。持久化 Outbox 与已接入 consumer 的 effect receipt 用于避免重复触发产生重复业务效果。Task/Artifact 到 Evidence 的事件可以唤醒 Runtime，模型被要求等待 wakeup 而不是轮询 Worker。

保留的可观测性并不表示已经证明所有并发场景。特别是旧兼容入口、Fanout 和部分派发路径仍需要持续收敛到同一 Outbox/围栏语义。

## 10. 部署、配置与密钥边界

支持 Native 轻量、Local Compose、Linux 全栈、Control/Worker、Pi Control Demo 和低带宽评测模式。生产/演示中的 Pi Control 把 Sidecar 放在 Control Compose 网络内，默认端口 8899 不应暴露公网；Server HTTP 默认在容器网络/loopback 的 8191，Web/Nginx 是用户入口。

关键配置类别：

- `MINI_DROP_AGENT_RUNTIME`、`MINI_DROP_PI_RUNTIME_URL`、`MINI_DROP_PI_INTERNAL_TOKEN`；
- `MINI_DROP_PI_MODEL_PROVIDER`、`MINI_DROP_PI_MODEL`、Provider API Key；
- `MINI_DROP_AGENT_AUTO_READ_LOW`、最大活跃 Case、Fanout 开关；
- API/gRPC Token、TLS、PostgreSQL、MinIO 和 Worker Artifact 上传配置；
- 低带宽模式的 context 字符预算、轮次、上传、MCP 与 tracing 开关。

Provider Key、内部 Token、数据库/对象存储密码和 TLS 私钥只应由受保护环境文件或部署密钥注入，不写入仓库、评测报告或前端。Sidecar 健康不等于真实模型可用；发布就绪应以 `/api/readyz` 和一次真实受控 Turn 共同确认。

## 11. 已有验证证据

截至本基线，仓库记录的代表性验证包括：

- JYL 三节点 Pi + DeepSeek `deepseek-v4-flash` 真实闭环：Task -> Artifact -> CaseEvidence -> Branch -> Tool -> Evidence-bound Conclusion；
- 公开 GitHub PR 9x3 真实 Provider 矩阵：27/27 完成，第二轮 27/27 包含完整 canonical Evidence ID/hash 引用，结构门禁通过；
- public-6 扩展：6/6 完成，使用 compact Projection，未上传 raw pack/仓库 clone；
- P07 隐藏事实动态补证测试：缺证拒答、补采 runtime snapshot、Evidence materialization、wakeup、Gap 解决与 stale-scope 拒绝；
- 专家介入/Focus 测试：暂停不触发 Runtime Turn、服务聚焦 revision、未知 PID 拒绝、CAS 冲突和 PID 聊天解析；
- CI 设计包含 Python 静态检查、迁移/registry/testset 校验、后端测试、前端 lint/test/build 和 Sidecar Node 测试。

评测结果是当前机制闭环与结构合规的证据，不等价于通用生产 RCA 准确率。9x3/public-6 都包含公开素材、人工 Oracle 判断或 synthetic wiring 信号，因此必须保留其非双盲、非真实生产 telemetry 的限制。

## 12. 代码审计证明索引

本节是本次“已落地”审计的证据索引。`实现`表示当前工作树存在可执行实现；`契约`表示代码只定义了 Schema/模型，不能单独证明端到端完成；`测试`表示存在回归测试；`历史报告`表示只能证明某次环境曾经运行，不足以证明当前代码在所有环境都可用。

| 功能主张 | 证据等级 | 当前代码证据 | 代码实际证明的范围 |
|---|---|---|---|
| Agent Runtime 可替换接口 | 实现 | `server/app/agent_runtime/port.py:14-144` | 定义 Snapshot、Turn、Binding、Steer、Follow-up、Abort、State 契约 |
| Deterministic/Pi/Pi Shadow 选择 | 实现 | `server/app/agent_runtime/config.py:18-42`、`dispatcher.py` | 根据环境选择运行时；不证明 Provider 一定可用 |
| Pi Server 只暴露内部协议 | 实现 | `agent_runtime/pi-sidecar/src/server.mjs:1-145` | 只路由 resume/turn/steer/follow-up/abort/state/health，并支持内部 Token |
| Pi 模型初始化与密钥边界 | 实现 | `agent_runtime/pi-sidecar/src/runtime.mjs:558-577` | Provider/Model 从环境读取，Key 只在 Sidecar 侧设置 |
| Pi Session 创建、generation 刷新 | 实现 | `agent_runtime/pi-sidecar/src/runtime.mjs:579-704` | 内存 Session 按 Case 管理，generation 变化会关闭旧 Session 并重建 |
| Prompt 字符预算与 Evidence 摘要 | 实现 | `agent_runtime/pi-sidecar/src/runtime.mjs:60-497` | 生成有界 JSON Context 并记录省略元数据 |
| 不持久化私有思维链 | 实现 | `agent_runtime/pi-sidecar/src/runtime.mjs:1119-1124`、`1340-1345` | `thinking*` 事件被过滤；不等于模型供应商侧永不保留日志 |
| Runtime Event JSONL spool | 实现 | `agent_runtime/pi-sidecar/src/event-spool.mjs:1-69` | 本地事件 append/ack/replay 去重；不等于服务端 durable queue |
| 25 个 Agent 工具的统一目录 | 实现/契约 | `server/app/agent_runtime/catalog.py:25-69` 及 `ToolSpec` 定义；运行时计数为 25 | 名称、Schema、内部路径、权限类别存在；每个路由仍需单独验证 |
| Sidecar 工具转发与审计 hash | 实现 | `agent_runtime/pi-sidecar/src/tools.mjs:28-153` | 计算参数/结果 hash、HTTP 状态、Evidence refs、重试计数 |
| Tool Gateway 每次重新做 policy fence | 实现 | `server/app/v6_routes.py:659-714` | 注册、RuntimePolicy、干预确认、generation、Case 状态检查；当前该函数还有未定义 `binding` 的静态错误 |
| Case Snapshot 真实读取 | 实现 | `server/app/v6_routes.py:1075-1235` | 读取 Case、Workspace、Evidence、假设/Gap/结论等，并按分支过滤 |
| 采集提案入口 | 实现 | `server/app/v6_routes.py:1356-1547` | 通过 CollectionSupervisor 处理参数、scope、Evidence、预算和 request/task 关系 |
| 拓扑发现工具 | 实现 | `server/app/v6_routes.py:1549-1678` | 创建/推进有界 discovery run；工具描述明确区分 dependency 与 causality |
| Evidence Analysis 创建/提交 | 实现 | `server/app/v6_routes.py:1680-1765`、`diagnosis/evidence_analysis.py:20-193` | 输入 fingerprint、Projection/Review fence、引用校验和 completion |
| 分析字段/span 引用校验 | 实现 | `server/app/diagnosis/evidence_analysis.py:196-240` | 校验 Evidence ID、Projection hash、field path 和 quote 是否来自 Projection |
| Hypothesis/Gap 分支状态 | 实现 | `server/app/diagnosis/investigation_state.py:23-110`、`:112-190` | 校验 scope revision、Evidence refs、数量上限并写入 branch 参数 |
| Causal Graph 提案 | 实现 | `server/app/v6_routes.py:2071-2230`、`investigation_state.py:191-280` | 结构、端点、角色、Evidence refs 和 watermark 验证；不证明因果判断本身正确 |
| `finish_investigation` 终态门禁 | 实现 | `server/app/v6_routes.py:2200-2425`、`:2717-2744` | 结论字段、引用、状态、intervention/generation 等在持久化前检查 |
| Runtime 事件入库 | 实现 | `server/app/v6_routes.py:2745-2785`、`:2943-...` | 接收 Sidecar 正规化事件并按 generation/sequence/idempotency 处理 |
| 分支 Runtime Binding | 实现/迁移 | `server/app/models/runtime_core.py:61-106`、`migrations/versions/0038_branch_runtime_fencing.py` | 分支 binding 表和唯一约束存在；并不证明所有旧入口都已无 Case-wide 路径 |
| Agent Turn/Cycle/Model Request 分支字段 | 实现/模型 | `server/app/models/runtime_core.py:109-205`、`server/app/models/v6_core.py:224-312` | 持久化字段和索引存在；兼容数据仍可为 `NULL` |
| CaseEvidence/Projection/Review 模型 | 实现/模型 | `server/app/models/runtime_core.py:210-330`、`server/app/models/v6_core.py:794-895` | canonical Evidence、Projection hash、Review revision 表存在 |
| Analysis input fingerprint 唯一性 | 实现/模型 | `server/app/models/v6_core.py:895-945`、`diagnosis/evidence_analysis.py:61-74`、`:177-193` | 相同输入可复用；是否所有调用方都使用该服务须继续审计 |
| Evidence Review 触发 stale | 实现/测试 | `server/app/sql_repository_v6.py:2649-2958`、`diagnosis/evidence_analysis.py:101-175`、`tests/test_evidence_governance.py` | Review 事务内更新准入状态、失效关联 Analysis、传播依赖并写入 Outbox；Analysis completion 另行比对 review/lifecycle/projection |
| Investigation Tree | 实现/模型 | `server/app/models/v6_core.py:67-185`、`v6_routes.py:1896-1960` | 节点、依赖、事件和分支字段存在；不是自动局部祖先回溯器 |
| Evidence Dependency/Confidence Ledger | 实现/模型 | `server/app/models/v6_core.py:1328-1455`、`v6_routes.py:2037-2070` | 支持/反证边和解释性 confidence 记录存在；不是完整 ATMS/ECRD |
| Conclusion/Claim Evidence Binding | 实现/模型 | `server/app/models/v6_core.py:1659-1770` | Conclusion revision 和 Projection hash/field path 绑定存在 |
| Knowledge/Memory 检索工具 | 实现 | `server/app/v6_routes.py` 对应 knowledge 路由、`diagnosis/knowledge_memory.py`、Sidecar `search_knowledge` | 检索/保存路径存在；代码语义要求 Knowledge 不计作当前 Evidence |
| 13 个 CollectorSpec | 实现/契约 | `mini_drop_contracts/catalog/collectors.v1.json`、`mini_drop_contracts/collector_spec.py:1-110`；运行时计数为 13 | 目录、Schema、风险、能力和产物合同存在；目标 Worker 能否执行取决于 capability/环境 |
| Worker Collector 注册 | 实现 | `agent/mini_drop_agent/main.py:32-47、85-143` | 本机构建注册并拒绝未知 collector；不等于每个部署环境工具齐全 |
| 采集 Task 编译/监督 | 实现 | `server/app/diagnosis/collection_supervisor.py:26-...` | Proposal/Request/Task 关系和校验逻辑存在；需以具体部署检查 Outbox/Worker 连接 |
| 前端 Case Agent Turn | 实现 | `web/src/api/client.js:427-432`、`web/src/pages/AIDiagnosisWorkspace.jsx:795-963` | Web 调用 Agent Turn 并携带 branch/policy/options |
| 前端 Workspace/Branch/Review | 实现 | `web/src/pages/ai-workspace/CanonicalCaseWorkspace.jsx:624-748`、`web/src/components/InvestigationWorkbench.jsx:77-380` | 展示和操作 Workspace、分支、Evidence Review、计划和 fanout |
| 真实 Pi/DeepSeek 评测 | 历史报告/测试 | `docs/evidence-native-live-eval-2026-08-25.md`、`reports/evaluation/...` | 证明指定 JYL 环境的历史运行，不替代当前代码静态/全环境证明 |

### 12.1 明确下调的原文主张

本次代码审计后，以下表述不再作为“已完整落地”写法：

- “Source/MCP 和 Task Artifact 已完全统一进入同一 Ingestion contract”：当前文档只保留为部分已接入、统一合同待完成。
- “Outbox/Wakeup 已完整保证所有路径 exactly-once”：模型、部分 Consumer 和 Sidecar spool 已存在，但旧兼容入口、Fanout 和部署级派发仍需逐路径验证。
- “分支干预已经完全隔离所有并发实体”：分支 Binding、Turn、Cycle、ModelRequest 和状态字段已存在，但必须继续验证 CollectionRequest、Task、Proposal、Fanout 子任务的同一事务围栏。
- “模型可以自主选择并证明根因”：代码证明的是模型可以提出结构化状态，服务端可以拒绝不合规引用；不证明模型判断正确。
- “真实评测结果就是产品准确率”：评测报告本身明确是公开素材、非双盲或 synthetic wiring 的有限证据。

## 13. 当前缺口、质量状态与验收口径

### 13.1 语义与产品缺口

以下能力尚未完成，不能提前承诺：

1. 一个 Claim 的多替代支持集、冲突集、时间窗/实例/指标语义可比性及局部真值重算；当前 Confidence Ledger 不是完整 ATMS/ECRD。
2. 基于冲突自动选择精确祖先节点的局部回溯；当前有 Investigation Tree、依赖和重放基础，但不是完整自动回溯器。
3. Evidence promote 后跨分支 Claim/Hypothesis/Conclusion 的完整共享、授权与可撤销传播。
4. Source/MCP 与 Task Artifact 的完全统一 Ingestion contract。
5. Pi Sidecar 的内存 Session 不是完整的并行业务分支账本；权威仍在服务端持久化状态。
6. 任意生产环境故障证明、自动修复、完整实时拓扑平台和通用模型准确率基准。

### 13.2 当前代码质量事实

本文件记录的是功能与设计基线，不覆盖已发现的验收风险。2026-08-25 当前工作树复跑显示：后端全量 `1254 passed, 6 skipped`；本次定向 AI 闭环 `122 passed, 1 skipped`；迁移 drift、registry consistency、前端既有 105 项测试和生产构建通过。此前出现的进程扫描 API `PENDING` 时序失败本次未复现，因此不再作为当前失败计数，但仍属于需要关注的幂等窗口风险。Ruff 当前仍报告 7 项错误，包含 `cases.py` 中未定义 `branch_id`、`v6_routes.py` 中未定义 `binding`，以及未使用变量/导入；其中两个未定义名称可能在对应成功路径触发运行时错误。

因此当前建议的验收用语是：**核心功能和演示链路可验收，但代码质量门禁未全绿，应按功能通过、质量风险保留处理。**

### 13.3 本次代码验收执行记录

以下结果来自 2026-08-25 当前工作树，而不是历史报告：

| 检查 | 结果 | 证明范围 |
|---|---:|---|
| 后端全量 `pytest -q` | `1254 passed, 6 skipped` | 当前后端回归测试通过；不替代真实 Worker/Provider 部署验收 |
| AI 定向闭环测试 | `122 passed, 1 skipped` | Tool Gateway、Runtime Turn/Local Loop、Evidence、Investigation、Governance、MCP 路径 |
| `scripts/check_registry_consistency.py` | PASS | TaskKind、Collector、Probe、EvidenceContract、QueryOperation、Agent Tool 注册表一致 |
| `scripts/check_migrations.py` | PASS | Alembic 无 schema drift，迁移可从 baseline 升级到当前 head |
| Tool Catalog 断言 | `25` | 当前 `TOOL_CATALOG` 实际条目数 |
| Collector Catalog 断言 | `13` | 当前 `collectors.v1.json` 实际条目数 |
| Ruff | 7 errors | 质量门禁未全绿；详见 13.2，未据此否定已通过的功能测试 |

前端 105 项测试和生产构建沿用同日既有记录；真实 Pi/Provider 运行沿用历史评测报告并单独标注为“历史报告/测试”，不能与本地代码回归混为一谈。

## 14. 维护与扩展约束

新增 AI 能力时必须维持单一主线：

1. 新工具同时修改 Tool Catalog、Gateway 路由、Sidecar 白名单、策略、测试和 `check_registry_consistency.py`；
2. 新采集器同时修改 Collector Catalog、Worker 注册、参数/风险合同、Projection、Evidence 解析和测试；
3. 新模型输出只能通过结构化 Tool/Service 写入，不可直写 Task、Evidence、结论或恢复动作；
4. 新 Evidence 类型必须定义 provenance、hash、Projection、生命周期、Review 与失效传播；
5. 新分支能力必须显式处理 branch visibility、revision、runtime generation、迟到事件和审计；
6. 评测必须隔离 Oracle，记录输入 hash、模型/提示/策略/预算、原始运行记录和评分依据；
7. 不恢复第二套 rules-first 在线 RCA 主脑。规则可用于安全、合同验证、确定性投影和离线基线，不可替代 Evidence-bound 调查结论。

## 15. 关联文档

- `docs/evidence_native_agent_unified_architecture.md`：统一架构合同与演进背景。
- `docs/evidence-native-investigation-positioning.md`：产品定位和 Evidence 语义。
- `docs/ai_current_design_interview_handbook.md`：答辩/核验手册与更细的资产说明。
- `docs/evidence-native-live-eval-2026-08-25.md`：真实 Pi/DeepSeek 评测、边界和运行记录。
- `docs/runtime-policy.md`、`docs/agent-tool-catalog.md`：策略与工具扩展合同。
- `docs/deployment-profiles.md`：部署模式、端口与发布限制。
