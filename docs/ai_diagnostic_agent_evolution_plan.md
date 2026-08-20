# Mini-Drop AI Collector Agent 架构审计、竞品对比与改造计划（历史稿）

> 状态：历史审计稿；当前基线已迁移至 `ai_collector_architecture_and_migration_plan.md`
> 版本：2026-08-19
> 范围：当前仓库、v6.0 旧方案、`演进.pages`、公开竞品资料与现有评测实现
> 保留目的：追溯本轮审计细节与早期取舍；不得作为第二份执行真源，也不直接授权删除生产代码。

## 0. 结论先行

当前 Mini-Drop 不是缺少功能，而是同时叠加了五套概念：原生采集、旧 Diagnosis、Case 协作、Pi Runtime、Recovery。它们共享部分数据，却没有形成唯一主链。默认运行模式仍是 `deterministic`，Case 的 AI 入口在兼容路径中还会进入旧规则 Diagnosis。因此当前“根因排名”主要由规则、固定权重和领域分析器完成，不是模型自主判断。

后续应只保留一条产品主链：

```text
用户问题或已有 Evidence
-> AI 识别缺失事实并提出采集/分析请求
-> 确定性 Gateway 校验范围、权限、预算和风险
-> CollectionSupervisor 下发 Collector/Query/Source
-> Artifact/SourceResult 统一物化为 canonical Evidence
-> 每条 Evidence 可预览、下载、单独 AI 分析和人工治理
-> durable Wakeup 驱动 AI 继续、停止或提交 Evidence-bound Report
```

本计划做出以下取舍：

1. 删除“生产运行时规则根因候选/排名”这个产品概念；旧规则只保留为迁移期离线基线，最终移出 Server 主链。
2. 保留并增强 Task、Collector、Artifact、Case、Evidence、Projection、Review、Pi、Tool Gateway、Fencing、Outbox/Wakeup 等可信底座。
3. 不建立多个长期并列的 `rule_tree / causal_graph / hypothesis_first` 诊断引擎；它们降为实验 Prompt/CollectionPolicy 变量，不拥有执行权。
4. `CausalGraph` 不再是每次分析的必经状态。最终输出改为 Evidence-bound `InvestigationReport`；确有传播证据时才附可选因果边。
5. 第一阶段采用单 Agent + 确定性 Verifier。Critic 只作为可消融的实验 Arm，证明有净收益后才进入检查点，避免再次增加一套“第二判断者”。
6. Recovery 不与旧 Autonomous Agent 绑定。若保留通用运维演进方向，只保留注册动作、dry-run、审批绑定、验证和回滚内核，并默认关闭自动执行。
7. Mini-Drop 不与大型商业平台比连接器数量；它的差异化是深度 Linux/进程证据、主动补采、Evidence 生命周期和可公平复现实验。

## 1. 我们此前讨论的共识

### 1.1 你的目标

- 主要职责是探索 Mini-Drop 的 AI 功能、可落地方案和 Demo，而不是维护一个越来越大的智能规则归因系统。
- AI 应围绕采集器工作：理解问题、选择证据、按需下发采集、分析真实结果、解释缺口，并允许人工随时干预。
- 所有 Evidence 应可预览、可下载、可单独交给 AI 分析；人工可核验、降低信任、排除或恢复。
- 不需要的旧系统应真实移除，不接受长期用 feature flag 掩盖两套主链；但旧设计中成熟的审计、安全、审批和恢复能力不能因删除而丢失。
- 项目要逐步具备通用运维 Agent 的可用性，同时在评测中形成自己的优势，而不是成为另一个通用聊天壳。

### 1.2 已澄清的概念

- AI 功能是：意图理解、Evidence 分析、缺口识别、下一采集选择、多证据综合、正确拒答、报告和修复建议。
- 确定性基础设施不是“智能归因”：Collector Schema、权限、scope、预算、审批、去重、完整性、引用验证、状态机和回滚都应由程序保证。
- k6、OTel Collector、Tool Gateway、Source Gateway 本身不是 AI，但它们决定 AI 是否有真实、受控、可评测的工具环境。
- MCP 可有两个方向：北向把 Mini-Drop 能力提供给 Web/CI/其他 Agent；南向把受控外部 Source 接入 Evidence。两者必须使用不同授权边界。
- 根因不是系统按固定权重算出来的“神秘分数”。目标架构中，由模型提交结构化 Claim，Verifier 只验证其 Evidence 绑定；证据不足时必须 `PARTIAL/INSUFFICIENT`。

## 2. 审计材料和事实优先级

本计划按以下顺序判断事实：

1. 当前代码和当前测试；
2. 本次用户目标与架构决策；
3. v6.0 中已证明有价值的合同；
4. `演进.pages` 和其他历史设计；
5. README、旧进度勾选和历史报告。

v6.0 是需求素材，不是当前完成度证明。`演进.pages` 是方向建议，不是执行指令。商业产品只能依据公开架构做抽象比较，不能假设其未公开内部实现。

## 3. 当前真实架构

### 3.1 服务和入口

FastAPI 与 gRPC 在同一服务生命周期启动并共享 Repository（`server/app/app_factory.py:319-331`）。`create_app()` 同时挂载旧 Diagnosis、Case、Plan、Actuation、Task 和 v6 Tool 路由（`server/app/app_factory.py:1199-1222`）。

前端 `/cases` 看起来是统一 AI 工作台，但同一页面仍加载 Case、旧 Diagnosis 和 Recovery 数据。用户看到的是一个页面，后端实际上仍是多套所有权叠加。

### 3.2 已成熟的采集主链

```text
POST /api/tasks
-> Repository.create_task
-> Agent heartbeat 拉取任务
-> Collector 子进程执行
-> gRPC NotifyResult
-> Artifact + AnalysisJob
-> Analyzer 完成 Task
-> PlanDriver.on_task_done
-> CaseEvidenceService.materialize_task_artifacts
-> EvidenceProjection
-> EVIDENCE_COMMITTED Outbox
-> Runtime Wakeup / 下一 Agent Cycle
```

这条链是项目最成熟、最难被竞品通用 Tool Wrapper 替代的部分。关键实现包括：

- Task 创建和能力校验：`server/app/routes/tasks.py:140`、`server/app/sql_repository.py:3673`；
- Agent Collector 注册和执行：`agent/mini_drop_agent/main.py:62`、`:107`、`:515`；
- gRPC 拉取与上报：`server/app/grpc_services/healthcheck_service.py:16`、`hotmethod_service.py:27`；
- Task Artifact 物化为 canonical Evidence：`server/app/app_factory.py:752-803`、`server/app/diagnosis/case_evidence.py:40`；
- 有界、版本化 Projection：`server/app/diagnosis/evidence_projection.py:138`、`:232`。

### 3.3 Case/Pi 主链的现状

默认 Runtime 是 `deterministic`（`server/app/agent_runtime/config.py:31-36`），不是 Pi。Case Turn 只有在 Pi/Shadow 模式才进入 Sidecar；兼容路径仍会调用 `start_case_diagnosis()`，而该函数明确启动旧确定性 Diagnosis 并创建 Hypothesis（`server/app/routes/plans_control.py:354-472`）。

Pi 边界本身设计正确，值得保留：

- `AgentRuntimePort` 隔离运行时，Mini-Drop 持有业务权威；
- Sidecar 不暴露原始 Pi RPC；
- Shell/文件内置工具被禁用；
- generation、事件 spool 和 ACK 支持恢复；
- 私有 thinking 不持久化。

但 Tool Catalog 仍包含 `get_causal_graph`、`evaluate_hypotheses`、`rca_candidate_analysis` 和 `finish_investigation`，因此 AI Runtime 仍被旧归因模型塑形（`server/app/agent_runtime/catalog.py:95-166`）。

### 3.4 当前根因排名到底由谁判断

当前旧 Diagnosis 的判断链是：

```text
Task Artifact
-> collect_evidence
-> generate_candidates(rules.json)
-> calibrate(固定权重)
-> domain_analyzers
-> RulesOnlyReasoner.assess_cluster
-> 根因候选/分类/排名
```

`DiagnosisOrchestrator` 直接导入 `domain_analyzers`、`RulesOnlyReasoner`、RCA candidate/calibrator 和 causal graph。`RulesOnlyReasoner` 是主要判断者；模型可能参与意图解析或独立验证，但不是这条主链的根因决策者。也就是说，当前“智能规则归因”本质是规则程序，AI 多数时候在外围解释。

这正是需要移除的耦合：它既让 AI 准确率无法归因，也让用户误以为模型在判断根因。

### 3.5 Source 和 Recovery 的平行路径

`SourceGateway.query()` 会返回临时 `EvidenceEnvelope`，但不会自动写入 canonical CaseEvidence（`server/app/diagnosis/source_gateway.py:210-216`、`:332`）。其默认连接器还直接引用旧 Diagnosis/Topology Orchestrator（`:171-196`）。因此 Prometheus、Trace、MCP 与 Task Artifact 还不是同一事实层。

`CaseSupervisor` 当前是 `AutonomousIncidentAgent` 的租约包装（`server/app/diagnosis/case_supervisor.py:16-38`）；Actuation 路由又同时组装 Autonomous Agent、旧 Orchestrator 和 PlanDriver（`server/app/routes/actuation.py:224-269`）。后台自治虽默认关闭，依赖和 API 仍全部加载。

### 3.6 目标 Evidence 功能的真实完成度

| 目标 | 当前状态 | 事实判断 | 目标状态 |
|---|---|---|---|
| 所有证据可预览 | 部分落地 | Task/Collection Artifact 有图表和原始预览；canonical Evidence 没有统一 preview API/renderer | 任何 Evidence 都通过统一 descriptor 选择 JSON、表格、文本、火焰图、时间序列或 raw fallback |
| 所有证据可下载 | 部分落地 | 下载依赖 Task Artifact，Evidence Drawer 反查 Task；Source/MCP Evidence 无统一下载合同 | `GET evidence/{id}/content` 和 `/download` 只依赖 Evidence lineage/raw locator |
| 单条 Evidence 可独立 AI 分析 | 未落地 | 只有旧 Orchestrator 的批量 `analyze_evidence`；没有独立、持久、零采集副作用的分析对象 | 新增 `EvidenceAnalysisRun`，可重复、可比较、可引用、不会自动下发采集 |
| Agent 自主下发采集 | 部分落地 | Pi 有 `request_operation`，PlanDriver 能调度 READ_LOW；但默认 deterministic，旧规则仍决定路径，Source 也未统一 | AI 只提交 `CollectionProposal`，Supervisor 校验后编译 CollectionRun/Task/SourceCall |
| 人工降信任/排除/恢复 | 部分落地 | API/UI 已有 `LOW_TRUST/EXCLUDED/RESTORED`；EXCLUDED 会过滤 Prompt 并使结论失效 | Review Revision 触发 Wakeup；LOW_TRUST 必须真实影响上下文、Claim 充分性和报告状态 |
| 人工“删除” Evidence | 不应物理删除 | 当前使用逻辑排除，审计仍保留，这是正确方向 | 普通用户使用 EXCLUDED；物理删除只走独立保留期/合规流程，不能破坏 lineage |
| Collector/Query/MCP 统一 Evidence | 未完成 | canonical Evidence 主要物化 Task Artifact；Source 返回临时 Envelope | 全部进入同一 Ingestion、Projection、Review、Outbox 合同 |

## 4. 当前问题不是代码多，而是所有权不唯一

当前至少有五个“谁决定下一步”的候选：

1. 旧 `DiagnosisOrchestrator`；
2. `PlanDriver`；
3. `AutonomousIncidentAgent/CaseSupervisor`；
4. Pi Tool Loop；
5. Query/Source/MCP API 调用者。

如果只继续增加策略、工具和模型参数，系统会变成多脑调度。真正需要收敛的是四个唯一所有权：

| 对象 | 唯一所有者 |
|---|---|
| AI 决策 | `AgentRuntimePort` 后的单一 Agent Runtime |
| Case 派生执行 | `CollectionSupervisor` |
| 当前事实 | canonical Evidence Store |
| 权限和副作用 | Tool Gateway + Policy/Approval/Verifier |

普通第一页的 standalone Task 可以继续独立创建；只要 Task 属于 Case，就必须由 CollectionSupervisor 创建并携带完整 lineage/fence。

## 5. 产品定位与能力边界

### 5.1 建议的一句话定位

> Mini-Drop 是一个 Evidence-native Deep Runtime Collector Agent：让 AI 在受控范围内自主获取 Linux 进程和运行时证据，并让每条证据可追溯、可预览、可下载、可单独分析和可人工治理。

“通用运维”应理解为目标和接入面可以扩展，而不是允许模型执行任意 Shell。

### 5.2 近期能做好的能力

- Linux 主机、进程、容器和已注册服务的性能/运行时调查；
- perf/eBPF/smaps/process/runtime 与受控 OTel metrics/traces/logs 联合补证；
- 问题驱动和数据驱动两种入口；
- READ_ONLY 解释、低风险自主采集、停止/拒答；
- Evidence 预览、下载、单证据分析、多证据报告和人工 Review；
- 人批、已注册、可验证的有限恢复动作；
- Web、CI 和北向 MCP 复用同一 Case/Collection/Evidence API。

### 5.3 明确不承诺的能力

- 未注册环境和未知命令的任意处置；
- 仅凭自然语言对任意云、Kubernetes 或数据库写操作；
- 没有可观测数据时仍给出确定根因；
- 生产级多租户、跨区域高可用、全量云资源治理；
- 替代 Datadog/Dynatrace/Grafana 的完整遥测平台；
- 在未完成 holdout 前宣称通用 RCA 准确率优于商业产品。

### 5.4 自治等级

| 等级 | 能力 | 本计划目标 |
|---|---|---|
| L0 | 预览、下载、单证据/多证据只读分析 | 必须完成 |
| L1 | 在已注册 READ_LOW Operation 中自主补采，可暂停和纠正 | 必须完成 |
| L2 | 提出恢复建议，dry-run 后由人工审批执行 | 可选首例 |
| L3 | 自动执行并自动回滚 | 不在近期范围 |

完成本计划后停在 L1/L2 是一个可用的受控 Beta，不是生产级全自治运维平台。

## 6. 市面产品与开源方案的架构比较

### 6.1 商业产品

商业产品内部实现并不完全公开，下表只比较其公开产品架构，不把营销描述当作可验证事实。

| 产品 | 公开架构抽象 | 强项 | 与 Mini-Drop 的差异 | 借鉴点 |
|---|---|---|---|---|
| Datadog Bits AI SRE | Datadog Agent/Integrations -> 统一 telemetry 与 Service Catalog -> Watchdog/平台分析 -> Bits AI investigation -> Incident/Case | SaaS 接入广度、跨 telemetry 关联、服务上下文 | 依赖 Datadog 数据面；公开合同不以用户可治理的 canonical Evidence 为核心 | 调查时间线、已有 telemetry 优先、工具结果裁剪和成本控制 |
| Dynatrace Davis AI | OneAgent -> Smartscape/拓扑 -> Grail 数据层 -> Davis predictive/causal/generative -> Workflows/Automation | 自动拓扑、统一数据、因果分析和自动化成熟 | 是闭源全栈观测平台；Mini-Drop 不可能短期复制其数据广度 | 资源身份、拓扑时序、因果结论与自动化分层，不让 LLM 直接替代事实层 |
| Azure SRE Agent | Azure 资源与 Azure Monitor 上下文 -> 受身份权限约束的 Agent 工具 -> 调查/建议/受控操作 | Azure 原生上下文和资源操作面 | 平台绑定强；Mini-Drop 更适合异构 Linux/进程深度证据 | 工具权限继承、操作前审核、调查与执行分级 |
| PagerDuty AIOps/Advance | Events/Integrations -> Event Orchestration/降噪 -> Incident/Service Context -> AI 辅助与 Runbook/Automation | 事件响应、协作、升级和流程自动化 | 重心是 incident workflow，不是深度证据采集 | Case 时间线、人工接管、审批和 runbook 状态，不复制其事件平台 |

结论：商业产品的护城河是遥测规模、生态和组织流程。Mini-Drop 不应正面复制，而应把它们通常视为 Tool Result 的内容升级为可审计 Evidence 产品，并在深度运行时场景做得更强。

### 6.2 开源 Agent 与评测方案

| 方案 | 核心架构 | 值得借鉴 | 不应复制 |
|---|---|---|---|
| HolmesGPT | 单 Agent 循环 + 大量 K8s/VM/云/数据库/observability Toolsets + MCP | 只读默认、RBAC、大结果落盘/裁剪、多 Provider、Toolset 包装 | 追求接入数量、把 Tool Result 直接当最终事实、常驻 Operator 复杂度 |
| kagent | CRD 声明 Agent/Model/MCP -> Controller 编译 -> Runtime Actor/ADK -> A2A/HITL | 声明式类型、持久 continuation、原 Tool Call 精确审批/恢复、OTel | Kubernetes Controller、通用多 Agent、Memory/BYO Runtime 的平台复杂度 |
| K8sGPT | 静态 Analyzer 先发现错误 -> LLM `--explain` | 适合作为“规则检测 + AI 解释”的清晰基线 | 这正是 Mini-Drop 当前应退出的双系统结构 |
| Microsoft AIOpsLab | Application + Task + Fault + Workload + Evaluator 的实验环境 | 可复现问题注册、完整 Session Trace、定位/分析/缓解分项评分 | 任意 Shell、临时实验权限和重型环境不能进入生产 Gateway |
| OpsPilot | 单 Agent/显式状态 + Tool Gateway | 简单状态图、服务端校验、先评测后扩工具 | 不再引入另一套 Agent 框架 |

### 6.3 Mini-Drop 可形成的真实优势

1. **Deep Runtime Evidence**：perf、off-CPU、eBPF、smaps、FD、runtime blocking 等不是通用日志问答可轻易补齐的证据。
2. **主动证据获取**：AI 不只消费现有监控，而是按缺失事实选择低扰动 Collector。
3. **Evidence 产品合同**：raw、projection、hash、lineage、preview、download、AI analysis、review revision 一体化。
4. **人机共同治理**：人工排除/降信任会真实改变后续上下文和报告有效性，不只是聊天反馈。
5. **公平可移植评测**：相同 Collector 通过 MCP 提供给不同 Agent，在同模型、同预算下测 Agent 本身。
6. **正确拒答**：健康、过期、冲突、采集失败和证据不足是一级场景，不用“总能给答案”换命中率。

优势范围也必须诚实：Mini-Drop 可在 Linux 性能深诊和 Evidence 治理上领先，不会在云资源覆盖、SaaS 集成数量或企业事件协作上领先。

## 7. 唯一目标架构

```text
Web Console / CI Bot / Northbound MCP
                 |
                 v
Case & Conversation Service
  - goal / scope / time window / commands / audit
                 |
                 v
Agent Runtime Port -> Pi Collector Agent
  - analyze evidence
  - identify missing fact
  - propose collection
  - continue / ask / stop / report
                 |
                 v
Tool Gateway & Policy
  - schema / scope / budget / risk / revision / generation
                 |
                 v
CollectionSupervisor (唯一 Case 派生执行者)
  - Proposal -> CollectionRun -> Assignment -> ExecutionUnit
        |                              |
        v                              v
Native Task/Agent Collectors      SourceCall/Southbound MCP
perf/eBPF/smaps/runtime           OTel/Prom/Trace/Log/CMDB
        |                              |
        +---------------+--------------+
                        v
Artifact / SourceResult -> Evidence Ingestion
                        v
Canonical Evidence + Projection + Raw Locator
        |               |                 |
        v               v                 v
Preview/Download   EvidenceAnalysisRun   Human Review Revision
        +---------------+-----------------+
                        v
Domain Outbox / Runtime Wakeup -> 下一 Agent Cycle
                        v
Evidence-bound InvestigationReport / Abstain
                        v
Optional Remediation: dry-run -> human approval -> verify/rollback

k6/Fault Harness 只负责 workload、fault、SLO、cleanup 和 evaluator，首期不对模型开放。
```

### 7.1 AI 与确定性内核的边界

AI 负责：

- 将问题转为调查目标和 Missing Fact；
- 解释单条 Evidence；
- 综合多条 Evidence、冲突和限制；
- 选择下一项受控 Operation；
- 判断继续、询问、停止或拒答；
- 提交 Claim、可选传播边、缓解建议和验证计划。

确定性内核负责：

- 身份、scope、tenant、时间窗、resource incarnation；
- Collector/Operation 参数 Schema、能力和风险；
- 预算、去重、审批、CAS、generation、revision 和 idempotency；
- Task/SourceCall 的创建、取消、重试和 coverage；
- Artifact hash、Evidence lineage、Projection parser 和 raw access；
- 引用存在性、目标/时间匹配、review 状态和输出 Schema；
- 副作用执行、SLO 验证和回滚。

确定性内核不得再负责：

- 用规则库产生唯一根因候选；
- 用固定权重给根因排序；
- 用硬编码 Collector 顺序冒充 AI 规划；
- 因模型不可用而输出一份伪“智能诊断”。

模型不可用时，系统应降级为可预览、可下载、可人工下发采集的 Evidence Workbench，而不是回到规则归因产品。

### 7.2 用 InvestigationReport 取代神秘根因排名

建议输出：

```text
InvestigationReport
- state: SUPPORTED | PARTIALLY_SUPPORTED | INSUFFICIENT_EVIDENCE
- summary
- primary_claim?                 # 只有证据满足时存在
- contributing_claims[]
- claims[]
  - statement
  - mechanism
  - entity_ref
  - evidence_bindings[]          # evidence_id + projection_hash + field/extractor
  - counter_evidence_bindings[]
  - limitations[]
- propagation_edges[]?           # 可选，不是必经图系统
- open_gaps[]
- recommendations[]
```

Verifier 只回答“引用是否真实、适用、足够支撑这个 Claim”，不替模型选择根因。不要给用户展示没有统计意义的 0.83/0.76 根因概率；使用可解释的支持状态和证据缺口。

## 8. 核心领域合同

### 8.1 CollectorSpec/OperationSpec 唯一注册表

当前 `task_kinds.py`、Agent `COLLECTORS`、`probe_registry.py`、`query_registry.py`、Tool Catalog 和数据库 `OperationSpec` 存在重复定义。目标注册表至少包含：

```text
operation_id / version
executor_kind = COLLECTOR | QUERY | SOURCE
collector_id / artifact_types / projection_kinds
target_schema / parameter_schema / result_schema
required_capabilities
risk / expected_cost / max_duration / max_result_bytes
preview_renderer / parser_version
default_enabled / deprecation
```

Server 是规范真源；Agent 启动时上报实现能力和版本，CI 做双向一致性检查。Sidecar 只消费版本化目录，不能用内置旧列表静默掩盖 catalog 漂移。

### 8.2 canonical Evidence

每条 Evidence 至少包含：

```text
evidence_id / case_id / tenant_id
operation_id + version / collector_id + version
target_ref + incarnation
requested_window / observed_window
actual_parameters / execution lineage
raw_locator / media_type / size / content_hash
projection_kind / parser_version / projection_hash
freshness / quality / completeness
review_state / review_revision
created_at
```

Artifact、legacy DiagnosisEvidence、Attachment、SourceGateway Envelope 和 MCP Result 都只能通过同一个 Ingestion Service 进入该模型。

### 8.3 Preview 与 Download

- Preview 是 Evidence 的能力，不是 Task 页面特例；
- raw 小对象可直接流式读取，大对象使用受控下载/短期 URL；
- Preview renderer 按 `projection_kind/media_type` 注册；
- 任何预览都显示 target、time window、collector/version、hash、是否截断和 review 状态；
- 下载必须记录审计，不能暴露 MinIO 凭据或任意对象路径；
- Source/MCP 如果没有 raw blob，也要保存规范化 source snapshot 作为可下载内容。

### 8.4 EvidenceAnalysisRun

单证据分析必须是独立对象，不复用 Case 最终结论：

```text
analysis_id / evidence_id / evidence_revision / projection_hash
mode = SINGLE_EVIDENCE | COMPARE | CASE_SYNTHESIS
model / provider / prompt_version / tool_catalog_version
facts[] / anomalies[] / limitations[] / suggested_missing_facts[]
claim_bindings[] / output_hash / token_usage / latency / cost
status / created_by / created_at
```

`SINGLE_EVIDENCE` 强制 `READ_ONLY`：可以读该 Evidence 的分页 Projection 和必要元数据，不得创建 Task、SourceCall 或 CollectionRun。用户明确选择“继续补证”后，才产生新的 Investigation Turn。

### 8.5 人工 Review

- `ACTIVE`：正常进入上下文；
- `LOW_TRUST`：仍可见但明确标注，不能单独支撑关键 Claim，context selector 降低优先级；
- `EXCLUDED`：不进入新 Prompt/Report，相关 Claim 失效并触发 Wakeup；
- `RESTORED`：恢复参与并触发重新分析；
- `STALE/INVALID/SUPERSEDED`：由系统事实决定，用户不能伪造为 ACTIVE。

“删除”默认实现为可恢复的 `EXCLUDED`。物理删除应是对象保留期或合规流程，并留下 tombstone；否则引用、审计和评测都不可复现。

## 9. 保留、合并和移除

### 9.1 必须保留

- `agent/mini_drop_agent` 的 Collector、能力探测、取消和结果 spool；
- Task、Attempt、Artifact、AnalysisJob、Analyzer 与 gRPC 分发；
- Case、ResourceRef、Attachment；
- CaseEvidence、EvidenceProjection、EvidenceReviewRevision；
- `AgentRuntimePort`、Pi Adapter/Sidecar 和 provider 审计；
- Tool Gateway、RuntimePolicy、scope/revision/generation fencing；
- Plan/Step 的人工取消、改目标、排序和低风险调度语义；
- Fanout、MembershipSnapshot、coverage、幂等；
- Outbox、Wakeup、Cycle、ModelRequest、AssistantMessage；
- 引用验证、明确拒答、成本和 OTel 审计；
- Action Registry、dry-run、approval binding、verification/rollback 的独立能力。

### 9.2 合并改造

| 当前内容 | 合并到 | 说明 |
|---|---|---|
| TaskKind/Collector/Probe/Query/Tool/DB Operation 多注册表 | `CollectorSpecRegistry` | 一处声明，Server 权威，Agent 能力握手 |
| Artifact/DiagnosisEvidence/CaseEvidence/Source Envelope | `EvidenceIngestionService` | 所有来源相同 lineage、projection、review、outbox |
| `PlanDriver` + Fanout + Case lease/command | `CollectionSupervisor` | 保留确定性调度能力，去掉旧 Autonomous Agent 依赖 |
| Python `ai_provider.py` + Pi Provider 配置 | `ModelConfig/ProviderRegistry` | 一套连接测试、审计、成本和模型选择 |
| `evidence_guard.py` 的质量算法 | `EvidenceQualityPolicy` | 保留去重、时效、冲突、独立来源，不再输出根因 |
| Hypothesis/Conclusion/Causal 的可用字段 | `InvestigationReport + EvidenceGap` | Claim 引用和可选边，不保留第二套推理引擎 |
| Recovery 安全组件 | 独立 `remediation` 模块 | 默认禁用执行，不依赖 DiagnosisOrchestrator |

### 9.3 迁移完成后的候选删除

- `server/app/rca/` 生产目录；
- `server/app/diagnosis/domain_analyzers.py`；
- `server/app/diagnosis/reasoner.py`；
- `server/app/diagnosis/strategies/` 中承担根因/固定探针判断的实现；
- `server/app/diagnosis/orchestrator.py`；
- `server/app/diagnosis/store.py` 与 `pipeline.py`；
- 强制型 `causal_graph.py`、`root_entity_resolver.py`；
- `server/app/diagnosis/autonomous_agent.py`；
- Legacy Diagnosis routes/models/tables/API；
- `rca-analysis`、`evaluate-hypotheses`、`get-causal-graph` Pi Tools；
- 前端 Diagnosis History、LegacyConversation、规则 Hypothesis/固定 Causal/Autonomous Recovery 主流程。

Recovery 的物理删除取决于产品决策。如果目标仍是通用运维 Agent，应迁移并保留 `remediation` 安全内核；如果最终只做 Collector Agent，可在 L1 稳定后作为单独包移出部署。

## 10. 删除依赖和迁移门禁

不能先删目录再修断链。必须按以下门禁执行：

1. deterministic 和 Pi 的新 Case Turn 都不再调用 `start_case_diagnosis()`。
2. 新的无模型降级模式只提供人工采集和 Evidence Workbench，不输出规则根因。
3. `SourceGateway` 删除 `DiagnosisEvidenceConnector/TopologyContextConnector` 对旧 Orchestrator 的依赖，改读 Case/Evidence/ResourceGraph。
4. Task、Query、Source、MCP 结果全部可物化为 canonical Evidence + Projection。
5. 每条 Evidence 有统一 detail、preview、download/raw locator；Source 失败形成 EvidenceGap。
6. `EvidenceAnalysisRun` 已实现单条分析、持久化、重放和零采集副作用。
7. `LOW_TRUST/EXCLUDED/RESTORED` 真正改变模型上下文和 Claim/Report 有效性，并产生 durable Wakeup。
8. Tool Catalog 已删除 RCA/因果判断工具，新增 `analyze_evidence`、`propose_collection`、`submit_investigation_report`。
9. 前端和 MCP 不再请求旧 Diagnosis、Proposal、Hypothesis、Recovery 耦合 API。
10. AI Validation 已替换 RCA 必过项，覆盖 Provider、Tool Call、Evidence Analysis、引用、拒答和越权。
11. 新主链至少完成一轮双读对账：停止写旧表 -> 新旧读结果观察 -> 删除旧读 -> 最后删表。
12. 全量 import/API/schema/前端依赖扫描无生产引用；迁移和回滚脚本均通过后才物理删除。

## 11. 分阶段改造计划

### M0：冻结新产品合同，停止扩旧系统

目标：先防止复杂度继续增加。

- 冻结旧 RCA/Diagnosis/Autonomous Agent 的新功能；
- 将本计划评审结果写为 ADR，明确唯一所有权；
- 为当前五条路径增加 characterization tests 和调用遥测；
- 建立旧 API/表/Tool 的依赖清单与流量统计；
- 修复评测 Oracle 泄漏和策略矩阵假变量，不先优化分数。

退出：可以机器化回答“哪个入口仍调用旧 Orchestrator、谁创建了每个 Case Task、哪种 Evidence 未进入 canonical store”。

### M1：Evidence 产品纵向切片

目标：先实现用户最关心、也最能形成差异化的能力。

- 统一 Evidence detail/preview/download API；
- 实现 renderer registry 和 Source snapshot raw content；
- 实现 `EvidenceAnalysisRun` 及前端“单独 AI 分析”；
- Review Revision 支持 ACTIVE/LOW_TRUST/EXCLUDED/RESTORED；
- Review 级联 Report 失效并产生 Wakeup；
- ANSWER_ONLY/SINGLE_EVIDENCE 六类副作用为 0。

退出：任意 Task/Source Evidence 可预览、下载、单独分析；刷新后分析和 Review 状态不丢失。

### M2：统一 Collector/Operation 与执行所有权

目标：让人工和 AI 使用同一个采集内核。

- 建立 `CollectorSpecRegistry` 和一致性 CI；
- 将 PlanDriver/Fanout/lease/command 合并为 `CollectionSupervisor`；
- 落地 CollectionProposal -> CollectionRun -> Assignment -> ExecutionUnit；
- Task/Query/Source/MCP 全部进入相同 Evidence Ingestion；
- 加入 target incarnation、coverage、部分失败和取消门禁。

退出：所有 Case 派生 Task/SourceCall 都能反查唯一 ExecutionUnit 和 Supervisor epoch；不存在第二写入口。

### M3：Pi Collector Agent 主链

目标：让 AI 真正决定“缺什么证据、下一步采什么”。

- Tool Catalog 收敛为读 Evidence、分析 Evidence、列 Operation、提出 Collection、提交 Report；
- 每个新 Evidence watermark 产生新的 Cycle/ModelRequest；
- 同一首轮 Snapshot 的 Evidence fork 必须改变后续 CollectionProposal；
- 用户可暂停、纠正 scope、排除 Evidence、调整采集和停止；
- 单 Agent + Verifier 先跑通，Critic 仅作为实验开关。

退出：问题驱动与已有 Evidence 驱动各一条真实三轮 E2E；新 Evidence 确实进入下一模型请求并改变行为。

### M4：退出旧规则归因

目标：物理收敛，而不是继续双轨。

- 停止生产写旧 Diagnosis/RCA 表；
- 从前端、OpenAPI、MCP 和 Runtime Catalog 移除旧入口；
- 旧规则评测冻结为 benchmark fixture 或归档包；
- 依门禁删除旧 Orchestrator/Reasoner/RCA/Autonomous 依赖；
- 保留必要兼容读取的明确截止版本，不设永久兼容层。

退出：Server 生产 import 图中不存在旧归因模块；默认启动不加载其表、路由和后台任务。

### M5：评测与竞品对照

目标：证明 AI Collector Agent 的真实增益。

- 完成 C0-C3 四轨评测；
- HolmesGPT 首先接入相同 MCP Collector Catalog；
- kagent 作为第二个可选 Harness，不阻塞首个结果；
- 运行冻结 Evidence Replay 和真实 Linux/Online Boutique E2E；
- 输出 Tool-parity 与 Native-product 两张榜，不混为一个总分。

退出：至少 30 个独立 holdout 达到 Beta 门槛；准备对外比较时扩展到 40-60 个配对 holdout。

### M6：可选人批恢复和通用化

只有 M1-M5 稳定后再做：

- 将 Action Registry/dry-run/approval/verify/rollback 迁入独立 remediation 模块；
- 首个动作仅限人批、无状态服务、可验证重启；
- 增加更多 Source/Collector，而不是开放任意命令；
- 再评估多租户、HA、Kubernetes/cloud 写工具和长期会话。

## 12. 评测架构

### 12.1 先修复现有评测失真

当前评测不能直接证明 AI 增益：

- 离线策略矩阵只解析 `strategy_id`，调用 `run_evaluation()` 时没有把 strategy/reasoner 传入，各 Arm 实际仍跑默认 Reasoner（`scripts/run_agent_strategy_matrix.py:75-99`）；
- Pi live 评分用故障关键词命中最终文本，“证据不支持 CPU hotspot”也可能被当成命中（`scripts/run_pi_agent_eval.py:201-226`）；
- 故障名称可能出现在被测进程命令行，`process_scan` 可直接读到 Oracle；
- 旧 `eval_harness` 直接运行 `domain_analyzers + DEFAULT_REASONER`，服务的是旧规则链（`server/app/diagnosis/eval_harness.py:32-47`）。

这些问题不修，继续跑矩阵只会得到更漂亮但无效的数字。

### 12.2 四条评测轨道

| 轨道 | 评什么 | 是否调用真实模型/采集器 |
|---|---|---|
| C0 Contract | Evidence ingest、preview、download、hash、review、fence、越权、恢复 | 模型可替身，真实服务/DB/对象存储 |
| C1 Collector Planning Replay | 在分支 Tool Result 下选什么 Collector、何时停止、成本 regret | 真实模型，冻结 Replay Source |
| C2 Evidence Analysis | 单 Evidence 事实抽取、多 Evidence Claim、冲突、拒答和引用 | 真实模型，冻结 Evidence |
| C3 Live E2E | workload/fault -> Agent -> Task/Source -> Evidence -> Report -> cleanup | 真实模型、Agent、Collector 和目标环境 |

### 12.3 对照 Arm

| Arm | 说明 |
|---|---|
| B0 Fixed Recipe | 固定低成本采集顺序，无 AI，测底座上限和开销 |
| B1 Legacy Rules | 冻结旧规则归因，仅离线/历史基线，不留在生产 Runtime |
| A1 Mini-Drop Agent | 单 Agent + deterministic policy/verifier |
| A2 Agent + Critic | 只在高成本采集和 finish 检查点运行，测净收益 |
| X1 HolmesGPT parity | 同模型、同 MCP Collector Catalog、同预算 |
| X2 kagent parity | 可选第二 Harness，同工具和预算 |

另做 Native-product 表，允许竞品使用自身完整工具栈；不得拿 parity 结果宣称击败商业产品全平台。

### 12.4 数据集方向

1. Deep Runtime：CPU hotspot、off-CPU/锁、I/O syscall、smaps/内存增长、FD、runtime stall；
2. Resource Attribution：目标进程、noisy neighbor、宿主竞争、容器限制、下游依赖、跨节点网络；
3. Ambiguous/Negative：健康波动、证据冲突、过期、采集失败、错误 scope、决定性事实不可用；
4. Compound：Primary + Amplifier + distractor，但不要求每例都构建完整 CausalGraph。

AIOpsLab 的 Problem 五元组可改为：

```text
Target Application
+ Investigation Goal
+ Workload/Fault
+ Allowed Collector Set
+ Evaluator
```

### 12.5 主指标和硬门禁

主指标：

- 预算内 Evidence 充分率；
- 加权信息目标召回率；
- Claim-level Evidence 支持精度；
- 正确停止/拒答率；
- 无效采集率与相对最优采集成本 regret；
- First Useful Evidence / Time to Sufficient Evidence；
- 引用有效率、Evidence 生命周期正确率；
- Collector 对目标的性能扰动；
- token、模型请求、P50/P95 延迟和单 Case 成本。

安全项不进入可抵消的加权总分：越权、跨 scope、绕审批、Oracle 泄漏、无效引用和 cleanup 失败都必须为 0。

建议 Beta 门槛：

- 至少 30 个独立 holdout；
- Evidence 充分成功率 >= 80%；
- 信息目标召回 >= 85%；
- Claim 支持精度 >= 98%；
- 正确拒答 >= 90%；
- 错误自信 <= 5%；
- 采集成功率 >= 95%；
- 所有安全硬门禁为 0。

对外宣称优于其他 Agent 前，建议使用 40-60 个独立配对 holdout，报告置信区间和配对显著性，不只给平均分。

## 13. `演进.pages` 的参考价值

该文档提出六项方向：统一 Tool Catalog、可插拔 DiagnosticStrategy、RuntimePolicy、RuntimeOptions、Experiment Matrix、注册一致性。判断如下：

| 内容 | 处理 | 原因 |
|---|---|---|
| 统一 Tool Catalog | 强保留并扩大 | 应进一步统一到 CollectorSpec/OperationSpec，而不只同步 Python/JS Tool 名称 |
| RuntimePolicy | 保留 | 权限、风险、side effect、审批必须可配置但只能缩权 |
| RuntimeOptions | 保留并收窄 | 模型/effort/prompt variant 是实验变量；不支持的 temperature/seed 不得伪装生效 |
| Experiment Matrix | 强保留但重写 Harness | 思路正确，当前实现没有真正把策略传入离线评测 |
| 注册一致性 | 强保留 | 是后续扩 Collector 的最低维护门槛 |
| DiagnosticStrategy 六分支 | 改造 | 改为实验 `CollectionPolicy/PromptVariant`，不再同时维护 deterministic 与 Pi 两条根因路径 |
| Sidecar catalog 失败回退内置列表 | 修改 | 应回退到最后一个已验证、版本匹配的 catalog 或 fail closed，不能静默回到漂移列表 |
| `allow_arbitrary_command` 字段 | 删除 | 即使固定 false 也会制造未来可开启的错误暗示；系统根本不提供该能力 |
| `capture_reasoning_trace` | 删除生产语义 | 只记录决策摘要、工具轨迹和公开报告，不采集私有思维链 |

总体上，`演进.pages` 对“实验基础设施”很有价值，对“多诊断策略长期产品化”不适合当前目标。最优吸收方式是保留 Catalog/Policy/Options/Matrix/CI，把 Strategy 从生产判断者降为实验参数。

## 14. v6.0 中不能丢失的优秀能力

- 问题驱动和 `@Task/@Collection/@Artifact/@Evidence` 数据驱动双入口；
- ANSWER_ONLY/单证据分析零副作用；
- canonical Evidence + Projection + 精确 Claim 绑定；
- 用户 pause/stop/correct/retarget/reorder/review；
- generation/revision/idempotency fence；
- durable Outbox/Wakeup 和 Runtime 恢复；
- Case 派生执行只有一个 Supervisor；
- logical resource、incarnation、membership snapshot、coverage；
- 大 Evidence 分页、裁剪和 Prompt Injection 隔离；
- Skill/Knowledge 只提供机制，不作为本次事故事实；
- 修复建议与执行分离，dry-run、审批绑定、验证和回滚；
- Provider/模型调用、成本、事件和 Evidence 的完整审计；
- Oracle 隔离、FaultContract、cleanup 和反假绿思想。

不保留的是 v6 将 Hypothesis/CausalGraph/Conclusion/Recovery 全部设为首轮必经产品对象的复杂度，以及它对 Formal Authority、超大 UX、长会话等一次性交付的过度耦合。这些能力可在核心增益成立后分层恢复。

## 15. 如果完成本计划后停止扩展，服务是否可用

完成 M0-M5 后，服务可以作为以下产品使用：

> 面向受控 Linux/容器环境的 AI 深度证据采集与分析 Beta，可由人监督完成调查、补采、证据治理和可追溯报告。

它可以稳定服务于 Demo、研究评测、内部性能排障和少量已注册环境；也可以通过 MCP 被其他运维 Agent 调用。

它还不能被称为通用生产 SRE 平台，边界包括：

- 只覆盖已注册 Collector/Source 和明确 scope；
- 模型不可执行任意命令；
- 写操作默认不开放，恢复最多到人批首例；
- 未证明大规模多 Case、HA、多租户和长期保留；
- 不能覆盖商业观测平台的全部云集成与事件协作；
- Provider 不可用时退化为 Evidence Workbench，而不是自动根因判断。

这是有用而诚实的终点。继续向通用运维 Agent 演进时，应优先增加高价值 Collector/Source 和资源身份解析，不应重新引入规则根因平台。

## 16. 下一轮需要共同确认的决策

1. 是否正式接受定位名称 `Evidence-native Deep Runtime Collector Agent`；
2. Recovery 安全内核是保留为独立可选模块，还是在 L1 后整体移出；
3. 第一批统一 Operation 的精确清单和风险等级；
4. `EvidenceAnalysisRun` 的输出 Schema、模型和成本上限；
5. LOW_TRUST 对 Claim 充分性的机器规则；
6. 首个 parity 对手是否锁定 HolmesGPT，kagent 是否进入第二阶段；
7. 30 个 Beta holdout 的场景配比和真实环境；
8. M4 物理删除前需要保留多长的只读兼容窗口。

在这些决策确认前，可以实现 M0/M1 的无争议纵向切片，但不应开始批量删除数据库表或 Recovery 安全组件。

## 17. 公开参考

- Datadog Bits AI SRE: <https://docs.datadoghq.com/bits_ai/bits_ai_sre/>
- Dynatrace Davis AI: <https://docs.dynatrace.com/docs/discover-dynatrace/platform/davis-ai>
- Azure SRE Agent: <https://learn.microsoft.com/en-us/azure/sre-agent/overview>
- PagerDuty AIOps: <https://support.pagerduty.com/main/docs/pagerduty-aiops>
- HolmesGPT: <https://github.com/HolmesGPT/holmesgpt>
- kagent architecture: <https://github.com/kagent-dev/kagent/blob/main/docs/architecture/README.md>
- kagent human-in-the-loop: <https://github.com/kagent-dev/kagent/blob/main/docs/architecture/human-in-the-loop.md>
- K8sGPT: <https://github.com/k8sgpt-ai/k8sgpt>
- Microsoft AIOpsLab: <https://github.com/microsoft/AIOpsLab>
- OpsPilot: <https://github.com/Plz12111/opspilot>
- MCP Tools specification: <https://modelcontextprotocol.io/specification/2025-11-25/server/tools>
- Anthropic, Building effective agents: <https://www.anthropic.com/research/building-effective-agents>

## 18. 成功标准

本计划的成功不是功能数量，而是系统能用机器证据回答：

> 在相同模型、相同 Collector Catalog、相同权限和相同预算下，Mini-Drop 是否比固定顺序、旧规则和通用 Tool Agent 更快获得充分证据，是否以更少无效采集形成更准确的 Evidence-bound Claim，是否在健康、冲突和缺失场景正确拒答；同时每条 Evidence 都可预览、下载、单独分析和人工治理，所有副作用都经过唯一 Supervisor 与确定性安全边界。
