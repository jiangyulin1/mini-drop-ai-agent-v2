# Mini-Drop 资产地图

> 状态：当前代码库的能力盘点，不是路线图或愿望清单。
> 基线：以当前工作树代码和迁移为准；最近已核验提交为 `24d6ed6`，分支推理状态改造尚未提交。
> 盘点日期：2026-08-24。
> 事实优先级：当前代码和测试 > 当前实施架构文档 > 历史设计文档。

## 1. 阅读规则

本文件只登记当前代码库中有实现和证据的资产；未接入方向不列为资产。

资产按两种状态标记：

- **已闭环**：代码、API/任务链和自动化测试能够证明该能力存在。
- **不完整**：主要路径存在，但依赖外部连接器、Linux 能力、人工步骤或仍有兼容链路并存。

“有模型、有表或有 API”不等于能力闭环。闭环必须同时检查：写入权威、权限边界、异步恢复、证据引用、失效处理和测试。

## 2. 系统边界

```text
Web / REST / SSE / MCP clients
              |
              v
FastAPI Control Plane -----------------------------+
  Case / Plan / Evidence / Policy / Recovery       |
  Agent Runtime Port / Tool Gateway                 |
              |                                     |
              +--> PostgreSQL or SQLite             |
              +--> MinIO or local artifact store   |
              +--> gRPC control channel             |
              |                                     |
              v                                     v
      Linux Worker Agents                    Pi Sidecar / Deterministic Runtime
      Collector -> Artifact                  Proposal / Analysis / Wakeup
              |
              v
      Analyzer Worker -> structured result -> Case Evidence / Projection
```

Mini-Drop 当前是“受控采集与证据调查工作台”，不是完整的可观测性平台。它可以使用服务关系、指标、日志、Profile、运行时快照和外部数据源，但不会自动拥有 Kubernetes、L7 tracing、长期指标库或任意主机访问权。

## 3. 在线主线与兼容主线

### 3.1 当前推荐主线：Evidence-native Investigation Runtime

产品定位不是一次性 AI 根因回答，而是以 Evidence 生命周期为真值、支持分支盲隔离的动态调查运行时：每个分支默认只能看到公共初始 Evidence 和自己的采集结果；服务端保留全局 lineage 用于审计和失效传播，但不把其他分支上下文注入 Agent。Evidence 可审核、排除和显式授权共享；依赖它的推理分支会失效，系统从有效祖先创建新的 cycle/generation 并继续探索。完整定位、状态语义和答辩叙事见 [`evidence-native-investigation-positioning.md`](evidence-native-investigation-positioning.md)。

```text
IncidentCase / target scope / user goal
        |
        v
CaseContextSnapshot @ case/control/scope/plan/evidence revisions
        |
        v
Pi Runtime 或 Deterministic Runtime
        |
        v
Internal Tool Gateway
        |
        +--> read Evidence / Projection / topology / knowledge
        +--> propose hypothesis / gap / dependency / causal graph
        +--> propose collection / update plan / finish
        |
        v
CollectionSupervisor
        |
        v
CollectionProposal -> CollectionRequest -> native Task
        |
        v
Worker Collector -> Artifact -> Analyzer -> CaseEvidence -> Projection
        |
        v
Evidence Analysis -> Hypothesis / Gap / CausalGraph / ConclusionRevision
        |
        v
Outbox / Wakeup -> rebuild current snapshot -> next Agent cycle
        |
        v
Optional RecoveryPlan -> approval -> execute -> verify -> observe
```

确定性代码拥有身份、范围、权限、预算、任务、Evidence 生命周期、引用验证和最终提交权。模型只能提出结构化候选。

### 3.2 仍然存在的兼容主线

`server/app/diagnosis/orchestrator.py` 加 `server/app/routes/diagnoses.py` 仍提供较早的 `DiagnosisSession`/`ProbePlan` 编排路径。它有自己的假设、探针、领域分析器和状态机，测试覆盖很广；但它不是新的 v6 Case/Agent Runtime 语义的完全替代品。Canonical Case Workspace 不再自动加载旧 DiagnosisSession；新 Case 的分支调查通过 `/api/v1/cases/{case_id}/workspace?branch_id=...` 和 `/api/v1/cases/{case_id}/branches` 工作。当前旧写入口默认返回 `410 LEGACY_DIAGNOSIS_DISABLED`，仅显式兼容开关可启用；采集器、Artifact/Evidence 转换、验证合同和审计/fence 元素继续复用。

当前仓库同时注册旧路由和 `v6_routes.py`。因此不能把“旧 DiagnosisOrchestrator 的测试通过”解释为“v6 主线所有能力都已完成”，也不能在没有读入口调用链的情况下删除旧代码。

## 4. 资产总览

| 平面 | 主要资产 | 当前判断 |
|---|---|---|
| 控制面 | FastAPI、gRPC、认证、租户、REST/SSE、MCP | 已闭环；生产认证和外部连接器仍需部署配置 |
| Case 协作 | IncidentCase、TargetSession、CaseEvent、Command、Scope/Control Revision | 已闭环 |
| 采集执行 | Agent、Task、Attempt、Collector Catalog、取消/重试、Result Spool | 已闭环；真实 Collector 能力依赖 Linux/权限 |
| 产物分析 | Artifact、AnalysisJob、Analyzer Worker、Projection | 已闭环；不同 Artifact 的解析深度不同 |
| Evidence 治理 | CaseEvidence、Projection、Review、Lifecycle、Trust、Impact Preview、EvidenceReuseDecision、branch lineage | 已闭环基础；分支 Evidence 默认盲隔离，公共种子/本分支/显式 PROMOTED 可见；Artifact/Projection 引用不可变，依赖传播按当前支持/反证关系重算；统一预览、下载、单条分析入口已存在；多支持集仍未统一 |
| 诊断计划 | Plan/PlanStep/Revision、CollectionProposal、Request、Fanout | 已闭环；旧 Plan 与 v6 Plan 仍需持续核对 |
| 服务关系 | 请求上下文依赖、Network Discovery、MembershipSnapshot、有界 BFS、Dependency Graph | MVP 已闭环；L7、长期拓扑流和完整远端身份解析未闭环 |
| Agent Runtime | AgentRuntimePort、Deterministic、Pi Adapter、Pi Sidecar、Shadow Plan、可选 LangGraph adapter、branch_id context | 已闭环 MVP；branch_id 已贯穿 Runtime/Tool Gateway/采集 lineage，LangGraph 只在安装 extra 后可用，Pi 业务分支树不是持久化真值 |
| 推理状态 | Hypothesis、Evidence Gap、Evidence Analysis、Causal Graph、Conclusion、Conclusion history API | 已闭环答辩 MVP；Hypothesis/Gap/Causal Graph/Conclusion/Dependency 已支持 branch scope，旧 Case 数据以 `NULL` 作为兼容范围；多支持集/完整局部重算尚未成为统一语义 |
| 恢复执行 | RecoveryPlan、Action Registry、Dry-run、Approval、Execute、Rollback、Verify | 已闭环窄白名单动作；不是通用自动修复 |
| 知识与外部源 | KnowledgeDocument/Memory、MCP Source Gateway、Grant、Skill/Query Registry | 已闭环受控读取；知识不能直接充当事故 Evidence |
| Web 工作台 | Case Workspace、Evidence Drawer、Branch selector、Plan/Proposal/Topology/Causal/Recovery 视图、SSE | 已闭环主要视图；用户可创建/切换隔离分支；长期目标配置和完整拓扑交互仍有限 |
| 评测与运维 | Golden/Holdout、评分器、VM gate、迁移检查、审计、OpenTelemetry | 已闭环测试基础；真实 Linux/外部 Holdout 不是本机测试可替代的 |

## 5. 服务端控制面

本地默认启动入口是 `python dev.py start`：它会在同一进程组拉起 Server、Pi sidecar、Analyzer、Agent 和 Web，默认使用 `MINI_DROP_AGENT_RUNTIME=pi`。需要离线兼容模式时必须显式设置 `MINI_DROP_AGENT_RUNTIME=deterministic`；单独运行 `dev.py server` 仍只启动控制面。

### 5.1 应用入口和运行进程

| 入口 | 文件 | 作用 |
|---|---|---|
| FastAPI + gRPC | `server/app/app_factory.py`, `server/app/main.py`, `server/app/grpc_server.py` | 组装 HTTP 路由、后台 sweeper、Outbox/Wakeup、gRPC 服务 |
| 开发命令 | `dev.py` | `server`、`agent`、`analyzer-worker`、`mcp`、`test`、`lint`、`demo` |
| Python CLI | `server/app/cli.py` | 环境检查、维护和辅助运行 |
| Web | `web/` | Vite/React/Ant Design/D3/ECharts 工作台 |
| MCP Server | `server/app/mcp_integration/` | 外部 MCP 工具/源的受控接入，不是 Case 真值源 |

### 5.2 HTTP 路由分组

- 基础任务与 Artifact：`server/app/routes/tasks.py`、`server/app/routes/agents_process.py`。
- Incident Case、Target Session、Signal、Profile Window、Evidence、Workspace：`server/app/routes/cases.py`。
- Plan、Evidence Review、Hypothesis、Iteration、Agent Turn、Confidence Impact：`server/app/routes/plans_control.py`。
- v6 Agent 工具、内部 Runtime 事件、Collection、Evidence Analysis、Causal/Dependency Graph：`server/app/v6_routes.py`。
- 拓扑发现：`server/app/routes/topology_discovery.py`。
- Fanout、Case Command、Pause/Resume/Stop/Resolve、Source Grant：`server/app/routes/fanout.py`。
- 恢复和注册动作：`server/app/routes/actuation.py`。
- 知识和记忆：`server/app/routes/knowledge_memory.py`。
- 旧 DiagnosisSession：`server/app/routes/diagnoses.py`。

### 5.3 后台可靠性

`app_factory.py` 启动以下后台循环：任务唤醒、Runtime Wakeup、Outbox Relay、离线/自治推进、Plan Driver。数据库状态是恢复依据；Sidecar 内存不是恢复依据。旧 `DiagnosisSession` 的 `diagnosis_advance` 默认冻结，只有设置 `MINI_DROP_ENABLE_LEGACY_DIAGNOSIS=true` 才会运行。

本机轻量模式按单租户进程运行；当前 Runtime Wakeup sweeper 使用进程级 tenant 配置扫描 Outbox。多租户同实例部署还需要按租户分片的队列/worker，不能把本机 sweeper 直接视为多租户生产方案。

## 6. Case、计划和并发控制

### 6.1 Case 层级

- `IncidentCaseModel`：问题目标、环境、范围、时间窗、状态、用户命令和审计入口。
- `DiagnosticTargetSessionModel`：服务/环境级长期档案，关联信号、Profile Window 和 Incident Case。
- `CaseEventModel`：Case 事件时间线和 SSE 游标。
- `CaseCommandModel`：暂停、停止、修正、恢复、用户干预等命令。
- `SystemControlModel`：系统级运行控制。

### 6.2 Revision 和 fencing

系统使用多个正交 revision：

- `case_command_revision`：用户命令序列。
- `control_revision`：控制/权限变化。
- `scope_revision`：目标范围、成员或身份变化。
- `plan_revision`：调查计划变化。
- `campaign_revision`：采集活动变化。
- `evidence_watermark`：Case 看到的 Evidence 投影位置。
- `runtime_generation`：Sidecar/Agent Runtime 代次。

采集任务还会固定 `input_evidence_review_revisions`。审批延迟、专家排除证据或运行代次变化后，迟到 Task 的真实 Artifact 仍保留，但只能标记为 stale，不能唤醒当前调查链。

迟到的模型、工具、采集或分析写入必须重新校验这些 revision；不能因为任务早先被批准就继续修改当前结论。

### 6.3 Plan、Fanout 和 Action

- `InvestigationPlanModel` / `InvestigationPlanStepModel`：可暂停、取消、移除、重排、重定向的调查计划。
- `PlanDriver`：把计划步骤编译成 CollectionProposal 或 Fanout。
- `MembershipSnapshotModel` / `FanoutCollectionRunModel`：对注册 Agent 做有界并行展开、覆盖率聚合、取消和恢复。
- `ActionAttemptModel`：恢复动作的执行阶段和幂等记录。
- `ContextPacketModel`、`InvestigationIterationModel`：Runtime 上下文和每轮决策审计。

## 7. 采集和产物执行面

### 7.1 Server 侧

- `server/app/models/agent_task.py`：Agent、Task、TaskAttempt、StatusEvent、AuditLog、AuthorizationGrant。
- `server/app/diagnosis/collection_supervisor.py`：CollectorSpec、scope、capability、risk、budget、idempotency、discovery authority 和 revision 的确定性校验；它是 v6 Agent Proposal 的编译入口。旧 Diagnosis、人工任务和部分兼容编排仍保留直接创建 Task 的路径，不能宣称全仓库已经统一为单一入口。
- `server/app/diagnosis/collection_reuse.py`：`normalize -> hard-gate -> score -> select` 的 fail-closed 复用判定。`collection-reuse.v2` 会补 CollectorSpec 默认值、统一 `pid/target_pid`、集合参数顺序和带时区时间表示；`probe_key` 表示物理探针，完整 `probe_fingerprint` 仍包含观测窗口和 scope revision。复用必须由当前链路显式提交 `reuse_existing_request_id`，多 Evidence/Projection 还要提交 `reuse_existing_evidence_id` 或 `reuse_existing_projection_id`；不会把 Case 内的旧 Evidence 自动放进上下文。`collector_id`、PID 或相同信息目标本身都不足以证明等价。评分只用于已通过硬约束的候选排序，近似并列默认返回 `REUSE_AMBIGUOUS`，不能靠模糊分数越过身份、版本、生命周期或 Projection 校验。
- `EvidenceReuseDecisionModel`（`evidence_reuse_decisions`）：按 Case/run/cycle/contract 记录一次明确的 `REUSED` 或 `RECOLLECT_REQUIRED` 决定，保存 probe/result/projection、目标身份、时间窗以及 control/scope/runtime/review 修订快照。它是解释和失效台账，不是 Evidence 真值，也不授予跨分支的隐式可见性；Evidence Review 排除/低信任和 Case scope 修订会把既有复用标为 `RECOLLECT_REQUIRED`，历史行保留。
- `PlanDriver` 的旧计划去重仍是兼容状态机路径，只能表示“计划步骤跳过”，不等同于新 Evidence 已向当前分支授权；新链路的 Evidence 复用必须经过 Supervisor 的指纹、生命周期、Projection 和显式 Evidence 选择校验。
- `server/app/diagnosis/probe_registry.py`：注册 Probe 定义、风险级别、能力要求、证据域和候选事实。
- `server/app/diagnosis/evidence_contracts.py`：假设/机制所需事实和候选采集器的契约。
- `server/app/diagnosis/adaptive_planner.py`：根据缺失事实、信息增益、风险和预算选择下一项注册 Probe。
- `server/app/diagnosis/fanout.py`：把逻辑步骤展开为多 Agent Task，并用幂等键避免重复。

### 7.2 Worker 侧

入口：`agent/mini_drop_agent/main.py`。它负责注册、心跳、拉取 Task、身份校验、Collector 执行、Artifact 上传、结果 spool 和取消响应。

Worker 代码实现包括：

`sys_metrics`、`perf_cpu`、`continuous_perf`、`ebpf_io`、`process_scan`、`memory_smaps`、`runtime_snapshot`、`connection_probe`、`network_discovery`、`log_scan`、`pyspy`、`java_async`、`go_pprof`，以及仅用于受控动作的 `swarm_actuation`。

AI 可见的正式目录是 `mini_drop_contracts/catalog/collectors.v1.json` 中的 13 项；`swarm_actuation` 不在该目录，也不在 Pi 的采集工具白名单中。

可用性受 Linux 内核、工具、权限、容器/namespace 和目标进程 incarnation 约束。Collector 出现在代码目录不代表目标 Worker 一定具备能力。

### 7.2.1 Collector 风险和执行边界

正式风险等级以 `mini_drop_contracts/catalog/collectors.v1.json` 为准，不以历史注释或前端标签为准：

- 当前 catalog **没有正式 R0 Collector**。`process_scan.py` 的历史注释曾称其为 R0，但实际 catalog 等级是 `R1`，执行策略按 R1 处理。
- 当前 R1 是 `memory_smaps`、`sys_metrics`、`process_scan`、`network_discovery`、`log_scan`、`runtime_snapshot`、`connection_probe`。
- 当前 R2 是 `perf_cpu`、`pyspy`、`continuous_perf`、`java_async`、`go_pprof`、`ebpf_io`。

Catalog 只定义有限 `duration_sec`/`sample_rate` 的 Task；`continuous_perf` 仍受最长 600 秒约束。多样本结果属于一次 Task 的时间序列 Artifact，不是目标主机常驻 telemetry。`log_scan` 是日志尾部读取，`network_discovery` 是时间点快照，均没有持续订阅语义。

### 7.3 Analyzer 和存储

- `analyzer/mini_drop_analyzer/worker.py`：从 AnalysisJob 读取 Artifact 并生成结构化结果。
- `server/app/models/artifact_diagnosis.py`：Artifact、AnalysisJob、AnalyzerWorker、DiagnosisRun/Report 和旧 RCA 兼容表。
- `server/app/storage.py`、`server/app/artifact_service.py`：MinIO/本地 Artifact 读取、流式下载、hash/availability/presign 边界。
- `server/app/diagnosis/evidence_projection.py`：把 Artifact 变成有界、确定性的 Projection；模型应读取 Projection，而不是无界原始文件。

## 8. 服务关系与上下游资产

这部分已经是现有资产，不需要重新发明一套“服务树”。

### 8.1 已有的静态依赖范围

`server/app/diagnosis/schemas.py` 的 `DiagnosisContext` 已支持 `ServiceInstance` 和 `DependencyEdge`，关系包括 `CALLS`、`READS_FROM`、`WRITES_TO`、`PUBLISHES_TO`、`CONSUMES_FROM`、`SHARES_DEPENDENCY`，并带有效时间、置信度和来源。

`DiagnosisOrchestrator._build_target_scope()` 已经执行：

1. 目标 Service 实例解析和身份冲突排除；
2. 同宿主实例纳入范围；
3. 按 `max_topology_hops` 沿下游边展开；
4. 按主机和实例预算截断；
5. 生成 `downstream_service_ids`、`dependencies` 和 scope completeness。

### 8.2 已有的动态拓扑发现

- `agent/mini_drop_agent/collectors/network_discovery.py`：Linux `/proc`/socket 发现，macOS `lsof` 降级路径。
- `server/app/diagnosis/dependency_graph.py`：Endpoint、ProcessIncarnation、SocketObservation、IdentityAssertion、DependencyNode/Edge/Graph 契约。
- `server/app/diagnosis/discovery_frontier.py`：有界 BFS、跳数/主机/进程/边/并发预算、身份解析、覆盖率和 `insufficient_coverage`。
- `server/app/diagnosis/network_discovery.py`：确定性 Projection、跨 Agent 聚合、边去重、digest、时间窗和 Evidence 引用。
- `server/app/routes/topology_discovery.py`：Case-scoped discovery run、种子 Task、扩展 Proposal、MembershipSnapshot、scope/control revision fence、Evidence wakeup。
- `server/app/v6_routes.py`：Pi 的 `discover_topology`、`get_dependency_graph` 和内部 Collection Gateway。
- `server/app/routes/cases.py`：Case API、Workspace 和 `dependency-graph` 查询。

### 8.3 重要语义边界

当前 Dependency Graph 明确是 `dependency_only_not_causal`。它能证明“在时间窗内观察到通信/依赖”，不能直接证明“下游是根因”。`InvestigationStateService` 会拒绝只引用 Dependency Projection 的因果图。

远端新 PID 的采集权不是由模型一句话获得，而必须同时满足 discovery run、active Evidence、MembershipSnapshot、target entity、boot ID、process start time 和当前 scope/control revision。`external_unmanaged_endpoint`、`virtual_endpoint` 和未解析实体不可直接作为可采集目标。

### 8.4 拓扑资产边界

当前资产只覆盖有界、Case-scoped 的 L4 发现和身份 fencing；Dependency Graph 的语义固定为 `dependency_only_not_causal`。未注册成员、虚拟端点和仅凭通信关系的因果结论不在资产范围内。

## 9. Evidence、推理和结论

### 9.1 Evidence 三层

```text
Artifact       原始采集产物，可下载/校验
CaseEvidence   Case 内稳定 Evidence 身份和生命周期
Projection     有界、确定性、供 AI/UI 读取的内容视图
```

关键实现：`case_evidence.py`、`evidence_projection.py`、`evidence_governance.py`、`evidence_analysis.py`。

### 9.2 治理和失效

Evidence lifecycle 包括 `ACTIVE`、`EXCLUDED`、`INVALID`、`SUPERSEDED`；trust 包括 `UNREVIEWED`、`TRUSTED`、`LOW_TRUST`。人工 Review 是追加 Revision，不覆盖 Artifact、Projection、Hash 或采集时间。`upsert_case_evidence` 对 provenance/hash/lineage 做不可变校验；`(evidence_id, projection_kind, projection_version)` 是不可变快照键，同版本内容变化必须拒绝并使用新版本。

Case 级存储和调查链上下文是两个不同层次：Artifact、CaseEvidence、Projection 可以留在 Case 内供后续显式查询；当前 Runtime Wakeup 只加载本批 Task 或 Review 明确授权的 Evidence。低信任、过期、排除或 stale 结果不能静默成为新的推理输入。

`Evidence Review` 已提供影响预览、短时 impact token、关联分析 stale、结论重验证、恢复方案冻结和 Outbox/Wakeup。页面隐藏或归档不改变推理准入。

当新 Evidence 到达时，系统不会因为已有结论而跳过 Wakeup：原始 Artifact、CaseEvidence、Projection、工具轨迹和旧 ConclusionRevision 都保留；Runtime 收到当前 Evidence watermark 与结论历史，必须展示旧结论、解释其 superseded 原因，并提交新的 revision。`GET /api/v1/cases/{case_id}/conclusions` 返回当前结论及全部历史，Case Workspace 也内嵌 `conclusion_history`，因此新采集内容与旧结论可以在同一首屏对照。

服务端结构化门禁位于 `v6_routes.py` 的 `finish_investigation`：`INSUFFICIENT_EVIDENCE` 必须使用空/unknown 根因、拒答理由和 Evidence Gap；请求 `CONFIRMED` 不能使用 unknown 根因，机制 confidence 必须在 `[0,1]` 且不能低于确认阈值。门禁不依赖 Prompt 或评分器，拒绝发生在持久化之前。

当前 Confidence Ledger 使用 trust、freshness、scope、directness、independence 等因子计算解释性分数；它不是事实证明器，也不应被当作完整的多支持集 Truth Maintenance。

### 9.3 Hypothesis、Gap、Causal、Conclusion

- `CaseHypothesisNodeModel` / `CaseHypothesisEdgeModel`：候选假设、支持/反证引用和关系。
- `EvidenceGapModel`：缺失事实、阻塞 Claim、尝试结果、可重试性和下一步。
- `EvidenceAnalysisRunModel`：固定 Evidence/Projection/Review revision 的分析运行、fingerprint、引用和 stale 状态。
- `CausalGraphRevisionModel`、`CausalNodeModel`、`CausalEdgeModel`：受验证的因果图版本。
- `ConclusionRevisionModel`：当前结论、证据审查、拒答/不足证据和报告版本。
- `ClaimEvidenceBindingModel`：Claim 与 Evidence 的显式绑定。

当前已经可以做到“证据排除后旧分析/结论不能继续作为 current”，并保留 superseded revision 供用户回看；“一个 Claim 的多个替代支持集、冲突集和跨分支局部重算”还没有被统一成完整的 ATMS/ECRD 语义。这个缺口不能通过再增加 `InvestigationBranch` 表自动解决。

## 10. Agent Runtime、Pi 和工具

### 10.1 Runtime 抽象

`server/app/agent_runtime/port.py` 的 `AgentRuntimePort` 定义 `start_or_resume`、turn、steer、follow-up、abort、state。Case、Evidence、Plan、Task 和权限归 Mini-Drop；Runtime 只推进一轮并输出结构化决策。

- `deterministic.py`：无模型控制组和永远可用的回退路径。
- `pi_adapter.py`：调用 Sidecar 内部 HTTP 协议，不接触模型密钥。
- `shadow.py`：生成 Shadow Plan、比较确定性计划和模型计划，不创建真实 Task。
- `policy.py`：`READ_ONLY`、`PROPOSE_ONLY`、`AUTO_READ_LOW`，风险和工具集合只能收缩，不能由请求扩大。
- `catalog.py`、`v6_policy.py`：工具目录和读/提案/写权限分区。

### 10.2 Pi Sidecar

`agent_runtime/pi-sidecar/src/runtime.mjs` 使用 Pi 0.84.2、禁用内置 shell/file，仅允许 Mini-Drop custom tools；当前默认一 Case 一个内存 Session。Pi 的 JSONL session tree/fork 适合会话上下文和临时 Frame，不是 Case/Evidence/Task 的持久化权威。

`agent_runtime/pi-sidecar/src/tools.mjs` 暴露：Case snapshot、Evidence/Projection、Dependency Graph、Topology Discovery、Collection Proposal、Evidence Analysis、Hypothesis/Gap/Dependency/Causal Graph、Plan、Knowledge、Query、Finish 等工具。

Pi 没有内置 Mini-Drop 级的子 Agent Supervisor、Evidence invalidation、长期任务树或业务 Truth Maintenance。Sidecar 重启后的恢复依靠服务器的 Runtime Binding、Context Snapshot、Outbox/Wakeup 和 generation fence。

### 10.3 当前模型循环

```text
用户消息 / Evidence wakeup / Approval / Review
    -> CaseContextSnapshot
    -> Pi 或 deterministic turn
    -> 工具调用（只提交结构化动作）
    -> server verifier / supervisor
    -> Case event + model attempt + assistant message
    -> Task/Evidence/analysis wakeup
```

“Agent 自主采集”必须准确描述为：模型在允许工具和预算内提出 Proposal，服务端决定是否创建/执行 Task；不是模型直接执行任意命令。

采集策略的实际边界如下：

- `READ_ONLY`：工具目录不暴露采集提案工具，只能读取当前 Case、Projection、拓扑和知识。
- `PROPOSE_ONLY`：可以持久化可审阅的 `CollectionProposal`，但不拥有执行权；审批时会重新校验 scope、discovery authority 和 revision，失败则 fail-closed。
- `AUTO_READ_LOW`：默认策略，只能在代码允许的工具、预算和 `R0/R1` 风险范围内自动创建 `CollectionRequest/Task`；`R2/R3` 不能由该策略直接越过审批。超出当前 Case scope 的进程/Agent 还必须有拓扑发现 Evidence、Membership 和身份 fencing。

`MINI_DROP_AGENT_AUTO_READ_LOW` 默认是关闭的；它控制后台自动推进是否创建低风险采集，不会改变 Tool Gateway 的代码权限边界。启动时 Runtime mode/flags 由环境变量读取，当前没有前端动态修改启动配置的能力。

### 10.4 AI 能力的实际边界

当前真正接入 Case 调查模型的是 Pi Sidecar 链路：`MINI_DROP_AGENT_RUNTIME=pi`（或配置 Sidecar URL 时的默认模式）调用 Pi 0.84.2，默认 Provider/Model 为 DeepSeek `deepseek-v4-flash`。Pi Provider 凭证只在 Sidecar 进程中使用；Sidecar 存活或健康检查通过不代表 completion 已成功，必须有真实受控 Turn 才能证明可用。

当前 Tool Catalog 共 25 个工具，其中 13 个 `READ_ONLY`、12 个 `PROPOSE_ONLY`，没有 `WRITE` 工具。模型能读取有界 Case/Evidence Projection、知识和依赖图；也能提出注册 Collector 的采集提案、拓扑发现、计划、假设、Evidence Gap、Evidence Analysis、Evidence Dependency、因果图和结束调查请求。上述请求都经过 Tool Catalog、RuntimePolicy、tenant/scope、risk、budget、revision/generation 和引用校验。模型不能执行 Shell、任意文件读写、未注册 Collector 或直接写入 Task/Artifact/结论真值。

`finish_investigation` 只提交候选结论；服务端 verifier 才决定 `CONFIRMED`、`PARTIALLY_CONFIRMED` 或 `INSUFFICIENT_EVIDENCE`，并检查引用、因果闭合、置信度和 Evidence Gap。Dependency Graph 只能证明观察到的通信/依赖，不能单独升级为因果根因。恢复动作也不在 Pi Tool Catalog 中，必须走独立 RecoveryPlan 的预检、审批、执行、回滚和验证 API。

`deterministic` 是不调用模型的控制组/离线路径；它保留 Case、Evidence 和人工工作台，不会替 AI 选择探针或自动创建新的 AI 采集任务。`pi_shadow` 只生成 Shadow Plan，不创建真实 Task。旧 `/diagnoses` 兼容路径仍可使用规则/领域分析器，但不能作为 Pi 主线的模型能力证据。

RuntimeOptions 当前真正影响 Pi 请求的是 `model`、`reasoning_effort`、`prompt_variant` 和会话选项；`temperature`、`max_tokens`、`seed` 仅作为实验元数据记录，不能宣称已改变 SDK 调用。Server 侧 NLP/旧 RCA 仍有独立的 OpenAI-compatible Provider 和无 Key 的关键词 fallback，它们与 Pi Case 调查不是同一条能力链。

历史报告 `reports/evaluation/verified-20260821.md` 记录了 8 个真实 GitHub PR 的单轮 DeepSeek 运行（人工 75/80）以及一次未知拓扑真实 Pi 运行。更新的 9×3 结果见 `docs/evidence-native-live-eval-2026-08-25.md`：9 个 PR、每个 3 轮共 27 轮，第二轮结构门禁和完整 Evidence ID/hash 绑定均为 27/27；按非双盲 oracle 粗评约 9.2–9.6/10。两者都只证明指定 PR Projection 下的链路、引用和边界能力，不构成通用准确率或生产自治结论。

### 10.5 线上展示环境实测边界（2026-08-24）

本次对 `https://47.112.10.137:80/` 的实测只能证明“受控演示环境可访问”，不能替代发布验收：

- `/api/livez`、`/api/readyz?core_only=true` 和完整 `/api/readyz` 均返回健康；数据库、对象存储和 Analyzer 正常，检查时有 1 个 Analyzer 在线、2 个 Linux Worker 在线（各暴露 13 个 Collector 能力），任务队列无 pending/running/failed。
- 认证后的 `/api/v1/agent-runtime/config` 返回 `pi-0.84.2`、`mode=pi`、`ai_ready=true`。线上 Tool Catalog 实测为 25 项：13 个 `READ_ONLY`、12 个 `PROPOSE_ONLY`、0 个 `WRITE`；`agent_auto_read_low=false`，因此默认是模型提案加服务端门禁，不是模型直接执行。
- 线上开启了 `MINI_DROP_WEB_AUTO_SESSION_ENABLED`：未携带 API Key 的访问者可以通过当前站点的 bootstrap 获得共享 HttpOnly Web session，身份为配置的 `jyl-operator`，角色包含 `operator` 和 `authorization_admin`。这适合受控评审，不适合公开不可信用户或生产数据。
- 数据库中存在 127 个 Case，主要是 `Benchmark case-*` 和 `SYNTHETIC_EVAL/REPLAY` 数据。部分 Case 的 scope 使用 `redacted-service`、虚拟 PID `12345`，线上真实 Worker 无法观测该 PID；这类 Case 可以展示 Evidence 约束下的 `PARTIALLY_CONFIRMED`/`INSUFFICIENT_EVIDENCE` 和拒答，但不能宣传成真实生产事故 RCA 或准确率证明。
- 一个可展示的 Case 已出现 5 条 Evidence、2 次分析、4 个假设、4 个采集提案和 `PARTIALLY_CONFIRMED` 结论；其结论仍明确写出目标 PID 不可观测、机制只能部分确认。这是合格的 Evidence-native 交互样例，但不是完整真实拓扑闭环。
- 线上 Web bundle 仍是旧版本：远端 `AIDiagnosis` 资源没有当前源码中的“当前调查路径”“失效传播”总览，而本地最新构建已包含。不能把本地最新前端能力写成线上已部署能力，必须重新构建并发布 Web 后再演示该界面。
- 该地址实际是“HTTPS over port 80”，证书由 `Mini-Drop JYL Private CA` 签发。未安装该私有 CA 的普通浏览器会报 `ERR_CERT_AUTHORITY_INVALID`；普通 HTTP 请求也会收到“plain HTTP request was sent to HTTPS port”。面向外部评审应改为可信证书的 443，并让 80 只做重定向。

因此，线上环境当前可以描述为：**受认证边界保护、Pi Runtime 已就绪、以合成/回放 Evidence 和有限真实采集为主的受控 Evidence-native Demo**。不能描述为：公网可直接访问的生产级服务、真实多主机拓扑 RCA、完整调查树产品、通用自动修复 Agent 或已验证的模型准确率。

## 11. 知识、外部源和记忆

- `knowledge_memory.py`：租户知识文档、Case Memory、文本分块和可选向量/词法检索。
- `source_gateway.py`、`authorization.py`：注册数据源、Grant、单次读取、scope/tenant/query budget 和审计。
- `skill_registry.py`、`query_registry.py`：代码拥有的候选 Skill/Query 注册表。
- `mcp_integration/`：外部 MCP Server/Source 适配。

知识、历史经验和外部源只能作为上下文或候选事实，不能绕过 Case Evidence 生命周期直接变成当前事故结论。

## 12. 恢复与动作执行

`server/app/diagnosis/action_registry.py`、`actuation.py`、`recovery_verifier.py`、`verification_contract.py` 和 `routes/actuation.py` 共同提供：

```text
Recommendation -> preflight/dry-run -> approval -> execute -> postcondition
              -> repeated verification -> rollback or stable observation
```

动作有 `policy_only` 与 `executable` 区分。执行前必须经过 scope、operation class、impact、租约、版本/标签、幂等和回滚检查。当前不是通用的“让 Agent 自动修复生产系统”。

## 13. Web 资产

- `web/src/pages/ai-workspace/CanonicalCaseWorkspace.jsx`：Case 统一工作台。
- `web/src/components/EvidenceDrawer.jsx`：Evidence 详情、引用、Review 和影响信息。
- `web/src/components/DiagnosisWorkbench.jsx`：诊断计划、探针、假设和结果工作区。
- `web/src/pages/ai-workspace/CaseConversation.jsx`：Case 对话、Agent Turn、实时消息。
- `web/src/pages/ai-workspace/DiagnosisTechnicalDrawer.jsx`：诊断技术细节。
- `web/src/pages/ai-workspace/KnowledgeMemoryDrawer.jsx`：知识/记忆查看。
- `web/src/components/MultiAgentCollectionModal.jsx`：Fanout/多 Agent 采集。
- `web/src/api/client.js`：REST 客户端和 SSE/Workspace 数据访问。

当前 Web 已能展示 Case、Evidence、Dependency Graph、Plan、Proposal、Hypothesis、Gap、Causal Graph、Conclusion、Recovery 和事件流；长期 Target Session 的初始化配置和全量拓扑控制仍不如 Case 工作台完整。AI 调查页现有 Evidence 路径总览，会把范围、采集、Evidence、验证、结论和受控行动串成当前状态投影，并提示 Evidence review/分析输入失效后的回溯。专家介入已具备后端确定性控制面：对话/`/commands` 的暂停、停止、恢复，以及服务/进程/依赖边 focus 和 `investigation-summary`；前端仍需把这些 API 接入统一的专家模式交互，且必须继续遵守 revision CAS 与 Discovery Evidence 门禁。

## 14. 持久化模型分组

| 模型文件 | 权威范围 |
|---|---|
| `models/agent_task.py` | Agent/Task/Attempt/Status/Audit/Grant |
| `models/artifact_diagnosis.py` | Artifact/Analyzer/旧 Diagnosis/Report/旧 RCA |
| `models/case_plan.py` | IncidentCase/TargetSession/Plan/Fanout/Review/Signal/Profile/Recovery/Message/Hypothesis/Iteration/Action |
| `models/runtime_core.py` | Runtime Binding/Turn/Event/CaseEvidence |
| `models/v6_core.py` | InvestigationRun/InvestigationTree/ContextSnapshot/AgentCycle/ModelRequest/Proposal/Request/Projection/Review/Analysis/Outbox/Wakeup/Dependency/Confidence/Causal/Gap/Conclusion/Memory |

`server/app/sql_repository.py`、`server/app/sql_repository_v6.py` 和 `server/app/application/repository_facade.py` 分别承载旧、v6 和应用层访问。后续改造首先要确认写入权威，避免同一 Case 同时由两套 Repository 产生互相不认识的状态。

## 15. 测试和评测资产

本轮后端验收执行结果为 **1235 passed, 6 skipped**；前端为 **104 passed**，生产构建成功。收集命令：

```bash
./.venv/bin/python -m pytest --collect-only -q
```

主要测试族：

- 基础 Task/Agent/Artifact/Storage/SQL/状态机。
- Collector 和 Linux/macOS 降级解析。
- DiagnosisOrchestrator、Adaptive Planner、Evidence Contract、Domain Analyzer。
- Case/v6 Agent Runtime、Tool Gateway、Policy、Collection Proposal/Request、Outbox/Wakeup、Runtime generation。
- Evidence Projection、Evidence Governance、Review Invalidation、Confidence Ledger、引用路径。
- Unknown Topology Discovery、跨 Agent 身份、Membership、BFS/coverage/digest、拓扑 API/Workspace/Pi。
- Plan/Fanout/Cancel/Resume/Revision Fence、Recovery/Actuation/Verification。
- Web Unit、Evidence Drawer、Canonical Workspace、E2E 脚本。
- Golden/Holdout/VM gate、Oracle 隔离、审计和评分器。

测试通过只能证明相应契约，不等于 Linux Collector、外部 Provider、Pi Sidecar、真实跨主机拓扑或生产动作已经验证。正式能力声明还要看 `reports/evaluation/` 和 Linux/VM runbook。

## 16. 当前完成度重新判断

### 已经具备的核心资产

1. 受边界控制的 Linux 采集和可恢复 Task/Attempt/Artifact 执行。
2. Case 级 Evidence、Projection、引用、Review、Trust、Lifecycle 和影响预览。
3. 上下游/未知拓扑的 L4 发现、身份解析、有界扩展、Evidence 化和采集授权。
4. Plan、Proposal、Approval、Fanout、取消/恢复和 revision/generation fencing。
5. Pi/Deterministic 可替换 Runtime 与内部 Tool Gateway。
6. 受审批、可回滚、可验证的少量恢复动作。
7. Web 工作台、SSE、审计、测试和评测基础。

### 不完整或依赖外部条件

1. 旧 DiagnosisOrchestrator 与 v6 Evidence-native 主线仍并存，但旧链已冻结为默认关闭的兼容入口，后台默认不推进（`MINI_DROP_ENABLE_LEGACY_DIAGNOSIS=true` 才显式启用）；新功能不得再写入旧根因排名链。旧链中的可复用 parser、Evidence 字段映射、benchmark、授权、审计和 fencing 不随入口删除。
2. Evidence 排除会使旧分析、结论和恢复计划失效；分支级新 cycle/generation 会从有效 Evidence 继续，跨分支共享需要 operator 显式 promote；多支持集/冲突集的通用局部重算还不是统一实现。
3. Target Session、Target Signal 和 Profile Window 有 API，自动 Agent/event bus 订阅和长期聚合尚未完整接入。
4. 拓扑发现是有界 L4 MVP；不是完整服务地图或 L7 因果系统。
5. 当前分支隔离已覆盖 Evidence、Hypothesis、Gap、Causal Graph、Dependency、Conclusion 和 InvestigationTree；Pi Session tree 可 fork，但 Sidecar 是一 Case 一内存 Session，不能代替完整并行业务分支账本。
6. Knowledge/MCP 已有受控读取，不能视为实时事故 Evidence RAG。
7. 恢复执行是窄白名单，不能外推成任意生产自动修复。

## 17. 架构结论

基于当前资产，下一步不应再增加一套大的 `BranchCheckpoint` 或“每假设一个 Agent”。`InvestigationTree` 已作为轻量审计/索引投影落地；真正的图执行优先复用可选 LangGraph。

更准确的判断是：

> Mini-Drop 已经有“Case + 受控采集 + 拓扑发现 + Evidence 治理 + Agent Runtime + 计划/恢复”的可验收主线；旧诊断链已冻结为兼容路径。当前主要缺口是多支持集真值维护、复杂冲突的自动局部回溯、统一 Source/MCP ingestion，以及跨分支 Claim/Hypothesis/Conclusion 的完整共享撤销传播。

## 18. 维护规则

每次重大代码变更后更新本文件：

1. 更新基线 commit 和盘点日期。
2. 检查新增模型是否有唯一写入权威。
3. 检查新增 API 是否通过 scope、tenant、risk、budget 和 revision fence。
4. 检查新增 Evidence 是否有 Artifact/Projection/hash/lineage 和引用验证。
5. 检查异步流程是否有 Outbox/Wakeup、幂等和迟到写入处理。
6. 同时更新“已闭环/不完整”两类清单和对应测试。
7. 如果能力依赖 Linux、Pi、外部 Provider 或真实多主机环境，必须在状态中写明验证条件。
8. 新增 Collector 时必须同时记录风险等级、目标类型、时长/采样边界、输出 Artifact、权限限制和测试；不能只把 Collector 名称加入列表。
9. 新增前端按钮时必须记录它对应的实际 API、状态迁移、失败/过期行为；没有后端动作的设计不登记为资产。

相关当前文档：

- `docs/evidence_native_agent_unified_architecture.md`：产品与实施架构合同。
- `docs/ai_design_traceability.md`：需求到代码/测试追踪。
- `docs/unknown_topology_discovery_rca_design.md`：未知拓扑 MVP 设计与限制。
- `docs/drop_execution_pipeline.md`：Task/Attempt/Artifact 执行底座。
- `docs/evidence-confidence.md`：Confidence Ledger 语义。
- `docs/README.md`：文档状态入口。
