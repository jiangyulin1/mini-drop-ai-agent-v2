# Mini-Drop 资产地图

> 状态：当前代码库的能力盘点，不是路线图或愿望清单。
> 基线：`main` @ `6589f90`（`feat: add explainable evidence confidence ledger`）。
> 盘点日期：2026-08-23。
> 事实优先级：当前代码和测试 > 当前实施架构文档 > 历史设计文档。

## 1. 阅读规则

本文件把资产分成三种状态：

- **已闭环**：代码、API/任务链和自动化测试能够证明该能力存在。
- **部分闭环**：主要路径存在，但依赖外部连接器、Linux 能力、人工步骤或仍有旧链路并存。
- **设计/未接入**：文档描述了方向，但当前运行时不能据此宣称已经具备能力。

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

### 3.1 当前推荐主线：Evidence-native Supervised Diagnostic Agent

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

`server/app/diagnosis/orchestrator.py` 加 `server/app/routes/diagnoses.py` 仍提供较早的 `DiagnosisSession`/`ProbePlan` 编排路径。它有自己的假设、探针、领域分析器和状态机，测试覆盖很广；但它不是新的 v6 Case/Agent Runtime 语义的完全替代品。

当前仓库同时注册旧路由和 `v6_routes.py`。因此不能把“旧 DiagnosisOrchestrator 的测试通过”解释为“v6 主线所有能力都已完成”，也不能在没有读入口调用链的情况下删除旧代码。

## 4. 资产总览

| 平面 | 主要资产 | 当前判断 |
|---|---|---|
| 控制面 | FastAPI、gRPC、认证、租户、REST/SSE、MCP | 已闭环；生产认证和外部连接器仍需部署配置 |
| Case 协作 | IncidentCase、TargetSession、CaseEvent、Command、Scope/Control Revision | 已闭环 |
| 采集执行 | Agent、Task、Attempt、Collector Catalog、取消/重试、Result Spool | 已闭环；真实 Collector 能力依赖 Linux/权限 |
| 产物分析 | Artifact、AnalysisJob、Analyzer Worker、Projection | 已闭环；不同 Artifact 的解析深度不同 |
| Evidence 治理 | CaseEvidence、Projection、Review、Lifecycle、Trust、Impact Preview、EvidenceReuseDecision | 已闭环基础；Artifact/Projection 引用不可变，依赖传播按当前支持/反证关系重算；显式复用已记录，但多支持集仍未统一 |
| 诊断计划 | Plan/PlanStep/Revision、CollectionProposal、Request、Fanout | 已闭环；旧 Plan 与 v6 Plan 仍需持续核对 |
| 服务关系 | 请求上下文依赖、Network Discovery、MembershipSnapshot、有界 BFS、Dependency Graph | MVP 已闭环；L7、长期拓扑流和完整远端身份解析未闭环 |
| Agent Runtime | AgentRuntimePort、Deterministic、Pi Adapter、Pi Sidecar、Shadow Plan、可选 LangGraph adapter | 已闭环骨架；LangGraph 只负责 bounded graph/checkpoint/interrupt，Pi 业务分支树不是持久化真值 |
| 推理状态 | Hypothesis、Evidence Gap、Evidence Analysis、Causal Graph、Conclusion | 已闭环基础；多支持集/完整局部重算尚未成为统一语义 |
| 恢复执行 | RecoveryPlan、Action Registry、Dry-run、Approval、Execute、Rollback、Verify | 已闭环窄白名单动作；不是通用自动修复 |
| 知识与外部源 | KnowledgeDocument/Memory、MCP Source Gateway、Grant、Skill/Query Registry | 已闭环受控读取；知识不能直接充当事故 Evidence |
| Web 工作台 | Case Workspace、Evidence Drawer、Plan/Proposal/Topology/Causal/Recovery 视图、SSE | 已闭环主要视图；长期目标配置和完整拓扑交互仍有限 |
| 评测与运维 | Golden/Holdout、评分器、VM gate、迁移检查、审计、OpenTelemetry | 已闭环测试基础；真实 Linux/外部 Holdout 不是本机测试可替代的 |

## 5. 服务端控制面

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

`app_factory.py` 启动以下后台循环：任务唤醒、Runtime Wakeup、Outbox Relay、离线/自治推进、Plan Driver。数据库状态是恢复依据；Sidecar 内存不是恢复依据。

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
- `server/app/diagnosis/collection_supervisor.py`：CollectorSpec、scope、capability、risk、budget、idempotency、discovery authority 和 revision 的确定性校验；它是 Agent 采集的唯一编译入口。
- `server/app/diagnosis/collection_reuse.py`：`normalize -> hard-gate -> score -> select` 的 fail-closed 复用判定。`collection-reuse.v2` 会补 CollectorSpec 默认值、统一 `pid/target_pid`、集合参数顺序和带时区时间表示；`probe_key` 表示物理探针，完整 `probe_fingerprint` 仍包含观测窗口和 scope revision。复用必须由当前链路显式提交 `reuse_existing_request_id`，多 Evidence/Projection 还要提交 `reuse_existing_evidence_id` 或 `reuse_existing_projection_id`；不会把 Case 内的旧 Evidence 自动放进上下文。`collector_id`、PID 或相同信息目标本身都不足以证明等价。评分只用于已通过硬约束的候选排序，近似并列默认返回 `REUSE_AMBIGUOUS`，不能靠模糊分数越过身份、版本、生命周期或 Projection 校验。
- `EvidenceReuseDecisionModel`（`evidence_reuse_decisions`）：按 Case/run/cycle/contract 记录一次明确的 `REUSED` 或 `RECOLLECT_REQUIRED` 决定，保存 probe/result/projection、目标身份、时间窗以及 control/scope/runtime/review 修订快照。它是解释和失效台账，不是 Evidence 真值，也不授予跨分支的隐式可见性；Evidence Review 排除/低信任和 Case scope 修订会把既有复用标为 `RECOLLECT_REQUIRED`，历史行保留。
- `PlanDriver` 的旧计划去重仍是兼容状态机路径，只能表示“计划步骤跳过”，不等同于新 Evidence 已向当前分支授权；新链路的 Evidence 复用必须经过 Supervisor 的指纹、生命周期、Projection 和显式 Evidence 选择校验。
- `server/app/diagnosis/probe_registry.py`：注册 Probe 定义、风险级别、能力要求、证据域和候选事实。
- `server/app/diagnosis/evidence_contracts.py`：假设/机制所需事实和候选采集器的契约。
- `server/app/diagnosis/adaptive_planner.py`：根据缺失事实、信息增益、风险和预算选择下一项注册 Probe。
- `server/app/diagnosis/fanout.py`：把逻辑步骤展开为多 Agent Task，并用幂等键避免重复。

### 7.2 Worker 侧

入口：`agent/mini_drop_agent/main.py`。它负责注册、心跳、拉取 Task、身份校验、Collector 执行、Artifact 上传、结果 spool 和取消响应。

当前 Collector 目录包括：

`sys_metrics`、`perf_cpu`、`continuous_perf`、`ebpf_io`、`process_scan`、`memory_smaps`、`runtime_snapshot`、`connection_probe`、`network_discovery`、`log_scan`、`pyspy`、`java_async`、`go_pprof`、`swarm_actuation`。

可用性受 Linux 内核、工具、权限、容器/namespace 和目标进程 incarnation 约束。Collector 出现在代码目录不代表目标 Worker 一定具备能力。

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

### 8.4 当前未闭环的拓扑能力

- 常驻 eBPF/SOCK_DIAG 事件流和长期拓扑历史。
- Kubernetes EndpointSlice、Docker/Swarm Provider、DNS 历史和 L7/OTel Span 关联。
- 未注册远端主机的安全接入和跨租户成员发现。
- 把拓扑关系自动提升为因果传播的能力（当前明确禁止）。

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

当前 Confidence Ledger 使用 trust、freshness、scope、directness、independence 等因子计算解释性分数；它不是事实证明器，也不应被当作完整的多支持集 Truth Maintenance。

### 9.3 Hypothesis、Gap、Causal、Conclusion

- `CaseHypothesisNodeModel` / `CaseHypothesisEdgeModel`：候选假设、支持/反证引用和关系。
- `EvidenceGapModel`：缺失事实、阻塞 Claim、尝试结果、可重试性和下一步。
- `EvidenceAnalysisRunModel`：固定 Evidence/Projection/Review revision 的分析运行、fingerprint、引用和 stale 状态。
- `CausalGraphRevisionModel`、`CausalNodeModel`、`CausalEdgeModel`：受验证的因果图版本。
- `ConclusionRevisionModel`：当前结论、证据审查、拒答/不足证据和报告版本。
- `ClaimEvidenceBindingModel`：Claim 与 Evidence 的显式绑定。

当前已经可以做到“证据排除后旧分析/结论不能继续作为 current”，但“一个 Claim 的多个替代支持集、冲突集和跨分支局部重算”还没有被统一成完整的 ATMS/ECRD 语义。这个缺口不能通过再增加 `InvestigationBranch` 表自动解决。

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

当前 Web 已能展示 Case、Evidence、Dependency Graph、Plan、Proposal、Hypothesis、Gap、Causal Graph、Conclusion、Recovery 和事件流；长期 Target Session 的初始化配置和全量拓扑控制仍不如 Case 工作台完整。

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

本地 `.venv` 当前可收集 **1211 个 pytest 测试**；收集命令：

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

### 部分闭环或依赖外部条件

1. 旧 DiagnosisOrchestrator 与 v6 Evidence-native 主线仍并存，写入和状态语义需要继续收敛。
2. Evidence 排除会使旧分析、结论和恢复计划失效，但多支持集/冲突集的通用局部重算还不是统一实现。
3. Target Session、Target Signal 和 Profile Window 有 API，自动 Agent/event bus 订阅和长期聚合尚未完整接入。
4. 拓扑发现是有界 L4 MVP；不是完整服务地图或 L7 因果系统。
5. Pi Session tree 可 fork，但当前 Sidecar 是一 Case 一内存 Session，不能代替并行业务分支账本。
6. Knowledge/MCP 已有受控读取，不能视为实时事故 Evidence RAG。
7. 恢复执行是窄白名单，不能外推成任意生产自动修复。

### 目前只是设计素材或未来方向

- 常驻拓扑事件流、7/90 天长期聚合、Kubernetes/Docker Provider、L7/OTel 关系。
- 完整 ATMS/ECRD 支持环境、多分支 Claim 合并和自动反事实调查。
- 通用多 Agent Supervisor 和长期独立 Pi 子会话。
- 未接入的外部告警、监控和服务变更连接器。

## 17. 架构结论

基于当前资产，下一步不应再增加一套大的 `BranchCheckpoint` 或“每假设一个 Agent”。`InvestigationTree` 已作为轻量审计/索引投影落地；真正的图执行优先复用可选 LangGraph。

更准确的判断是：

> Mini-Drop 已经有“Case + 受控采集 + 拓扑发现 + Evidence 治理 + Agent Runtime + 计划/恢复”的骨架；现在缺的是把两套诊断状态统一成一条主线，以及把 Evidence 生命周期变化稳定地投影到 Hypothesis、Gap、Plan、Task 和 Conclusion。

因此后续设计优先级应是：

1. 确认 v6 Evidence-native Case 主线为唯一在线写入权威，旧 DiagnosisOrchestrator 只保留兼容读/评测路径。
2. 先完善现有 Evidence Dependency/Confidence/Review 传播；树表只记录分支节点、依赖和状态事件，不复制 Evidence 真值。
3. 把 topology graph 作为共享 Scope/Observation 资产，继续保持“依赖边不等于因果边”。
4. 只有当真实场景证明单一 Runtime Context 不够时，才增加临时 Frame/fork；不要把 Pi Session tree 升格为业务账本。
5. 以“Evidence 排除 -> 影响预览 -> 旧分析/计划 fence -> Wakeup -> 当前理解重建”作为下一条验收闭环。

## 18. 候选演进：可回溯证据调查网络

以下是对用户目标的设计映射，不是当前已接入能力。它的重点不是把每个假设变成长期 Pi Session，而是让调查树成为一份可重放账本的投影：Evidence 全局共享，推理分支局部隔离，证据审查会反向重算支持关系和下一步探针。

候选存储不应是一棵单一真相树，而是三层共享结构：

1. 不可变 Observation/Artifact provenance DAG：按时间窗、目标身份和 hash 共享原始观察及其来源。
2. 按 assumption set 分叉的 Claim/Proof lattice：支持集是 AND/OR 关系，可共享子证据，但允许分支有不同的假设和反证。
3. 可取消的 Execution tree：`Proposal -> Task -> Attempt -> Artifact`，通过 generation fence 取消或重绑定迟到任务。

因此“证据链被摧毁”表示证明闭包被撤销、后代 Claim 变成 stale/refuted、待执行任务被 fence，原始 Artifact 仍保留并可由兄弟分支显式复用。实时会话 RAG 只应加载当前 obligation 的 Projection 和自上次 watermark 以来的 delta，不应把全部原始采集结果灌入每个分支上下文。

| 候选对象 | 可复用的现有资产 | 仍需补齐的语义 |
|---|---|---|
| Immutable Evidence Blackboard | `CaseEvidence`、`Artifact`、`Projection`、Review/Lifecycle/Trust | 统一 `projection_hash`、review revision 和不可变引用快照 |
| Claim / Counterclaim | `CaseHypothesisNode/Edge`、`ClaimEvidenceBinding`、Causal Graph | 多个替代 support set、opposing set、assumption 和 branch-local 状态 |
| Justification / Proof Obligation | `EvidenceAnalysisRun`、`EvidenceGap`、Current Understanding | 可重放的支持超边、反证集、失效闭包和最小证明包 |
| Probe Frontier / falsifiable experiment | `ProbeRegistry`、`AdaptivePlanner`、`CollectionProposal`、Supervisor | 为每个缺口记录预测结果、信息增益、成本/风险、expiry 和 disproof budget |
| Branch Cycle | `AgentCycle`、`ContextSnapshot`、Runtime generation、Pi bounded turn | 只保存分支假设/Claim/frontier；不把 Pi 会话树当持久化真值 |
| Revocation / backtrack | Evidence Review impact、Outbox/Wakeup、revision/generation fence | 排除/过期 Evidence 后增量 tombstone 后代 Claim、重开 Gap、激活 sibling frontier，并保留原始 Artifact |

目标状态应能区分 `SUPPORTED`、`REFUTED`、`UNKNOWN`、`STALE/CONTESTED`，而不是只用一个 confidence 排名。结论应携带最小支持集、未解决替代解释和覆盖范围；调查树只是该账本在某个 Case、branch 和 revision 下的视图。

## 19. 维护规则

每次重大代码变更后更新本文件：

1. 更新基线 commit 和盘点日期。
2. 检查新增模型是否有唯一写入权威。
3. 检查新增 API 是否通过 scope、tenant、risk、budget 和 revision fence。
4. 检查新增 Evidence 是否有 Artifact/Projection/hash/lineage 和引用验证。
5. 检查异步流程是否有 Outbox/Wakeup、幂等和迟到写入处理。
6. 同时更新“已闭环/部分闭环/设计素材”三类清单和对应测试。
7. 如果能力依赖 Linux、Pi、外部 Provider 或真实多主机环境，必须在状态中写明验证条件。

相关当前文档：

- `docs/evidence_native_agent_unified_architecture.md`：产品与实施架构合同。
- `docs/ai_design_traceability.md`：需求到代码/测试追踪。
- `docs/unknown_topology_discovery_rca_design.md`：未知拓扑 MVP 设计与限制。
- `docs/drop_execution_pipeline.md`：Task/Attempt/Artifact 执行底座。
- `docs/evidence-confidence.md`：Confidence Ledger 语义。
- `docs/README.md`：文档状态入口。
