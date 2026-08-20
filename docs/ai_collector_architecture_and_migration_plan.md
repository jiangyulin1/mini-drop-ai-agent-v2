# Mini-Drop AI 深度采集与 Evidence 分析架构基线

> 状态：架构审议基线，尚未授权生产代码删除
> 固化日期：2026-08-19
> 适用范围：当前仓库的产品定位、AI 能力边界、架构收敛、竞品比较、迁移与评测
> 决策优先级：用户最新确认方向 > 当前代码事实 > 本文 > v6/其他历史设计
> 核心结论：Mini-Drop 应演化为 **Evidence-native AI Deep Collector**，而不是规则优先的根因排名系统，也不是现阶段的自治恢复平台。

## 0. 执行摘要

Mini-Drop 当前并不是“没有 AI 功能”，而是同时存在太多相互竞争的产品主线：原生采集 Task、Artifact/Evidence、旧 RCA、Case diagnosis、Pi Runtime、自动恢复和一套混合前端。它们分别有局部价值，但共同出现在默认在线链路后，用户很难判断究竟是 AI、规则还是旧编排器作出了结论。

本次讨论后，建议把唯一主线收敛为：

```text
用户问题或已有 Evidence
-> AI 读取可用 Collector Catalog 与当前 Evidence
-> AI 提出受约束的 CollectorProposal
-> 确定性 Gateway 校验 scope / 参数 / 风险 / 预算 / 审批
-> Supervisor 创建原生 Collection Task
-> Agent 执行 perf / eBPF / smaps / runtime 等采集器
-> Artifact 物化为 canonical Evidence + EvidenceProjection
-> AI 对单条或多条 Evidence 做有引用的分析
-> AI 判断证据缺口，提出下一次采集或明确停止/拒答
-> 人工可审查、排除、降信任、恢复、下载和重新分析
```

产品的差异化不应是“比别人更会猜根因”，而应是：

1. 能主动获取普通 metrics/logs/traces Agent 看不到的 Linux 进程与运行时深度证据。
2. Tool Result 不停留在聊天上下文，而成为带 raw artifact、hash、lineage、projection 和 review revision 的一等 Evidence。
3. AI 自主选择证据的能力可以在相同工具、相同模型和相同预算下被公平评测。
4. 人可以真实改变 Evidence 的后续影响，而不是只修改一个 UI 标签。

当前阶段不把自动根因排名、自动因果图和自动恢复作为主产品能力。规则继续存在，但只负责约束、验证、投影、完整性和安全，不负责替 AI 决定根因。

## 1. 讨论结论与需求演化

### 1.1 用户意图的收敛过程

此前设计一度把目标写成“诊断到恢复闭环”，并引入 `hybrid / causal_graph / hypothesis_first`、根因排名、规则候选、Critic、恢复建议和执行验证。经过持续讨论，实际目标已经变得更明确：

- 主要职责是研究 Mini-Drop 的 AI 功能、可落地方案和 Demo。
- 不希望继续把“智能规则归因”作为 AI 功能的主体。
- 希望围绕现有采集器开发 AI 服务，而不是维护两套难以理解的诊断系统。
- 如果旧归因模块用不上，应在迁移后真正移除，而不是长期保留在默认流程中。
- 旧设计中的 Evidence、审计、Supervisor、安全边界和可恢复运行时不能丢失。
- 所有 Evidence 应可预览、可下载、可单独 AI 分析。
- Agent 应能自主提出采集；人工应能审核、干预、排除或降低 Evidence 的影响。
- 项目需要自己的特点，并能用公平评测证明相对其他运维 Agent 的优势。

### 1.2 已经明确的关键判断

| 问题 | 结论 |
|---|---|
| 当前“根因排名”由谁判断 | Case 默认主链主要由确定性规则、固定阈值和固定权重决定，不是模型独立判断 |
| 规则是否会调用 API | 规则本身不会；旧 `rca/llm_client.py` 可调用模型 API，但模型只能在规则候选范围内输出，且旧 Task 一次性诊断入口已返回 410 |
| 是否应删除全部规则 | 不应删除安全与证据规则；应退出或迁出在线产品中的候选生成、根因排名和最终分类规则 |
| 用户给出的 Pi/Gateway/OTel/k6 架构是否属于 AI 功能 | Pi/模型选择与 Evidence 分析属于 AI；Gateway、Collector、OTel、k6 是让 AI 可执行、可信和可评测的基础设施 |
| MCP 是否应成为产品核心 | 不应。MCP 是北向/外部适配协议，内部真源仍是 Collector、Task、Evidence 和 Policy |
| 自动恢复是否现在保留 | 不作为当前主线。保留通用安全边界素材，关闭默认产品入口；未来单独立项 |
| 是否继续维护多种诊断策略 | 仅在实验 Harness 中保留对照策略；生产不把策略标签变成多套并行架构 |
| 是否立即删除旧代码 | 否。先建立替代主链、契约测试和只读兼容，再按删除门禁逐步移除 |

### 1.3 本文替代的旧北极星

`docs/ai_diagnostic_agent_evolution_plan.md` 中“诊断到恢复闭环”不再是当前北极星。该文档和 v6 提示词继续作为历史需求素材，但不能覆盖本文的产品边界。

当前北极星是：

> AI 是否能在受控预算和权限内，选择高区分度的深度采集器，形成可治理的真实 Evidence，并对 Evidence 给出有字段引用、会承认不足、可被人工修正的分析。

## 2. 当前仓库的真实架构

### 2.1 当前组件图

```text
Web
├─ /tasks: 原生采集任务与 Artifact 结果
├─ /cases: Case、聊天、计划、Evidence、诊断和恢复混合工作区
├─ /diagnoses: 旧诊断历史
└─ /runtime: Agent Runtime 状态

FastAPI Core
├─ Task / TaskAttempt / Artifact / AnalysisJob
├─ gRPC dispatch 与 Agent capability
├─ Case / Attachment / Plan / Campaign / ExecutionUnit
├─ canonical CaseEvidence / EvidenceProjection / Review
├─ Pi AgentRuntimePort / Sidecar / Tool Catalog
├─ DiagnosisOrchestrator / DomainAnalyzers / RulesOnlyReasoner
├─ RCA candidates / calibrator / legacy LLM client
├─ SourceGateway / MCP
└─ Actuation / Recovery / AutonomousIncidentAgent

Worker Agent
└─ perf, eBPF, py-spy, async-profiler, pprof, smaps,
   sys_metrics, process_scan, log_scan, runtime_snapshot,
   connection_probe, continuous_perf, swarm_actuation

Persistence
├─ PostgreSQL: Task、Case、Evidence、Run、Cycle、Review、Outbox 等
└─ MinIO: 原始 Artifact
```

这不是一个单一架构，而是多个时代的设计叠加。`server/app/app_factory.py` 和 `server/app/runtime_services.py` 同时装配 diagnosis、Pi、Source、MCP、fanout、recovery 和 legacy compatibility；这也是当前认知负担的直接来源。

### 2.2 当前存在的五条主要执行路径

#### A. 原生采集路径

```text
Create Task
-> TaskAttempt
-> gRPC Agent 拉取
-> Collector 执行
-> Artifact 上传/登记
-> AnalysisJob 做确定性解析
-> Web 展示或下载
```

这是当前最成熟、最应保留的底座。相关核心位于：

- `agent/mini_drop_agent/collectors/`
- `agent/mini_drop_agent/main.py`
- `server/app/sql_repository.py`
- `server/app/artifact_service.py`
- `server/app/routes/tasks.py`

#### B. canonical Evidence 路径

```text
Task Artifact
-> CaseEvidenceService
-> case_evidence
-> EvidenceProjection
-> Case Snapshot / Pi read tools / UI Evidence Drawer
```

该路径已经真实存在，包含稳定 Evidence ID、raw locator、content/projection hash、target、time window、lineage、freshness、quality 和 review revision。它是未来产品最有价值的核心，而不是旧 RCA 输出。

#### C. Case 诊断路径

```text
Case
-> DiagnosisOrchestrator
-> Plan / Probe / Query / Fanout
-> DomainAnalyzers + RulesOnlyReasoner
-> assessment / candidate / conclusion
-> 可选 recovery
```

该路径内还直接调用 `server/app/rca/candidates.py` 与 `calibrator.py`，并通过 `domain_analyzers.py` 进行确定性集群分类。它与新的 Pi Agent 调查路径职责重叠。

#### D. Pi Runtime 路径

```text
Case Turn
-> AgentRuntimePort
-> Pi Sidecar
-> Tool Catalog
-> internal tool endpoints
-> Plan / Query / Finish
```

Pi Adapter、Turn/Run/Cycle/ModelRequest、Outbox/Wakeup 和 fencing 已形成可保留骨架；但 Tool Catalog 仍暴露 `evaluate_hypotheses`、`rca_candidate_analysis`、`get_causal_graph` 和 `finish_investigation`，所以新 AI 路径仍被旧归因概念牵引。

当前默认 Runtime 仍是 `deterministic`，并不调用模型；Case 的兼容入口还会调用 `start_case_diagnosis()` 进入旧 Diagnosis。因此“仓库里已经有 Pi Adapter”和“默认产品主链由 AI 决策”是两件不同的事。

#### E. Source Gateway 路径

```text
SourceQueryRequest
-> authorization + capability token + result budget
-> Prometheus / Trace / Log / Runtime Profile / MCP connector
-> EvidenceEnvelope
```

授权、脱敏、结果预算和 Envelope 设计值得保留，但它尚未成为 AI Collector Loop 的稳定事实入口。当前 Prometheus connector 接受调用者传入的 `metric` 字符串；Trace connector 查询的主要是 Jaeger operation 名称，并未读取真实 trace/span 关键路径；Runtime Profile connector 主要列 Artifact metadata。Source Result 也没有在所有调用路径中统一物化为 canonical `CaseEvidence`。因此 P5 不是“接上一个 URL”，而是要补语义 SourceSpec、真实数据提取和统一 Evidence ingestion。

### 2.3 当前根因排名的实际依据

当前“根因排名”不是一个统一的 AI 判断：

1. `server/app/rca/candidates.py` 读取 `rules.json`，通过白名单 matcher 生成候选和 `rule_score`。
2. `server/app/rca/calibrator.py` 用固定公式计算最终分数：规则 0.35、证据质量 0.25、基线 0.15、交叉采集 0.15、反馈 0.10。
3. `server/app/diagnosis/domain_analyzers.py` 用固定阈值和分支判断目标进程、同宿主、共享资源和下游问题。
4. `server/app/diagnosis/reasoner.py` 的 `DEFAULT_REASONER` 是 `RulesOnlyReasoner`，明确不调用模型。
5. `assess_with_reasoner(... diagnostic_strategy_id=...)` 在默认实现中先得到同一个规则结果，再替换结果上的 strategy 标识；标签变化不等于推理主体变化。
6. `server/app/rca/llm_client.py` 可以调用配置的模型 API，但其 `cause_id` 被限制在规则已生成的候选集合中；旧 `/api/tasks/{task_id}/diagnose` 已返回 410，当前 Case 主链仍以规则判断为主。

因此，当前规则既产生候选，又计算排名，还在 Case 中承担最终分类。模型即使被调用，也经常只是受限复述、格式化或验证。这正是用户感觉“AI 和智能归因混在一起”的根本原因。

### 2.4 用户期望的 Evidence 能力落地状态

| 能力 | 当前状态 | 事实与缺口 |
|---|---|---|
| canonical Evidence | 已落地 | `CaseEvidenceService`、`case_evidence`、`EvidenceProjection` 已存在 |
| Evidence 列表与投影读取 | 已落地 | Case API 和 Pi read tools 均能读取，但投影格式仍不完全统一 |
| 人工 `TRUSTED / LOW_TRUST / EXCLUDED / RESTORED` | 已落地 | Review revision 可持久化，`EXCLUDED` 会从后续 prompt/结论中剥离并保留审计 |
| 降低 Evidence 影响 | 部分落地 | `LOW_TRUST` 被记录，但没有一致传播到所有 Planner/分析/结论约束 |
| 所有 Evidence 可预览 | 部分落地 | Artifact/Projection 类型支持不一致；部分只显示摘要或提示下载 |
| 所有 Evidence 可下载 | 部分落地 | Task Artifact 与 legacy Diagnosis 有下载接口；没有统一 canonical Case Evidence 下载合同 |
| 单条 Evidence 独立 AI 分析 | 未落地 | 没有持久化的 `EvidenceAnalysisRun`、字段级 claim 和独立历史 |
| Agent 自主下发全部注册采集器 | 部分落地 | Agent 物理实现约 13 项，但默认 `QUERY_REGISTRY` 仅暴露 4 个低风险 operation，深度采集器目录未统一进入 AI 工具 |
| 人工删除 Evidence | 建议改为逻辑排除 | 物理删除会破坏审计与引用；产品操作应是 `EXCLUDED`，硬删除只由留存策略执行 |

结论：该设计没有完全丢失，但只完成了底层的一部分。Evidence 治理已经是可复用资产，统一预览/下载、单证据 AI 分析、LOW_TRUST 传播和全量 Collector 自主提案仍需补齐。

### 2.5 当前最重要的结构性问题

1. **多个“主脑”并存**：Pi、DiagnosisOrchestrator、RulesOnlyReasoner、PlanDriver 和 AutonomousIncidentAgent 都可能参与 Case 推进。
2. **Collector 注册重复**：Agent `COLLECTORS`、Server `TASK_KINDS`、schemas、QueryRegistry、ProbeRegistry、Tool Catalog 和前端映射分别维护。
3. **策略标签与真实执行脱节**：现有实验中改变 strategy 可能只改变元数据，不改变决策链。
4. **Evidence 产品合同不完整**：canonical Evidence 已有，但 preview/download/analyze 没有统一覆盖所有 source 类型。
5. **AI 能力不可归因**：旧评测主要测结构化 observations 上的规则判断，无法证明 AI 选择了正确采集器。
6. **产品边界过宽**：采集、RCA、因果图、MCP、容量、恢复、部署评估同时出现在一个工作区。
7. **旧概念继续泄漏到 UI 和 README**：当前 README 仍突出“5 层智能归因”，`/cases` 同时展示诊断和恢复。

当前重复不是理论风险，而是已经出现的数量和行为差异：截至本次审计快照，Agent 注册 13 项（含 `swarm_actuation`），Server `TASK_KINDS` 只有 12 项，Web `COLLECTOR_META` 只有 8 项，默认 AI `QUERY_REGISTRY` 只开放 4 项。这些数字用于证明当前漂移，不能成为长期架构常量。与此同时，`v6_routes.py`、Case routes、fanout route/service、`PlanDriver`、`DiagnosisOrchestrator` 和 distributed actuation 均存在直接 `create_task` 路径；当前 `CaseSupervisor` 主要还是旧 `AutonomousIncidentAgent` 的租约包装，尚未成为唯一采集写入者。

## 3. 产品定位与能力边界

### 3.1 推荐定位

中文：

> Mini-Drop 是一个 Evidence 原生的 AI 深度采集与分析服务：让 AI 在受控范围内自主获取 Linux 进程和运行时证据，并让每条证据可追溯、可预览、可下载、可单独分析和可人工治理。

英文：

> Mini-Drop is an evidence-native AI deep-collection runtime for Linux and container operations.

### 3.2 当前阶段必须做好的能力

- 统一发现和描述真实 Collector 能力。
- AI 根据问题、目标和现有 Evidence 选择下一 Collector。
- 所有调用经过 schema、scope、权限、风险、预算和审批约束。
- 采集结果稳定物化为 raw Artifact、canonical Evidence 和版本化 Projection。
- 支持单 Evidence、多 Evidence、对比 Evidence 的 AI 分析。
- 分析中的每条事实或解释绑定 Evidence ID 与 field/span locator。
- AI 能识别缺口、冲突、陈旧和不足，并正确停止或拒答。
- 人工审查可以确定性改变后续模型上下文与结论有效性。
- Web、CI 和 MCP 能使用同一核心合同。
- 产品、Replay、真实 VM 和竞品对照共享同一 Collector/Evidence trace。

### 3.3 当前阶段明确不做

- 不承诺通用 Kubernetes/云/SaaS 连接器广度。
- 不把规则生成的根因 Top-N 作为核心功能。
- 不自动生成权威 CausalGraph。
- 不把 AI 文本中的“可能原因”升级成系统确认的根因。
- 不让模型执行任意 Shell、SSH、SQL、PromQL、kubectl 或任意 MCP。
- 不默认执行恢复动作，也不把重启当根修复。
- 不构建通用多 Agent 平台、Memory 平台或 CRD Controller。
- 不在少量、泄漏或非配对案例上宣称优于竞品。

### 3.4 未来可扩展但不进入本期主链

- OTel metrics/logs/traces 的语义化 SourceSpec。
- 多目标 fanout 与逻辑服务级调查。
- 可选 Critic 或异构模型复核。
- 有人工审批的有限缓解动作。
- CMDB、变更、Kubernetes 和外部 MCP Evidence Source。
- 更通用的运维 Investigation Agent。

这些扩展必须建立在 Collector/Evidence 主链已经可测、可用之后。

## 4. 外部方案的架构比较

以下比较基于 2026-08-19 可核对的官方公开仓库与文档。闭源商业 AIOps 的内部实现不可验证，后续只能做用户可见能力比较，不应虚构其内部架构或给出不公平量化排名。

### 4.1 HolmesGPT

官方资料：

- [HolmesGPT repository](https://github.com/HolmesGPT/holmesgpt)
- [Built-in toolsets](https://holmesgpt.dev/data-sources/builtin-toolsets/)
- [HolmesGPT Operator](https://holmesgpt.dev/operator/)

架构特征：

```text
Question / Alert
-> LLM agentic loop
-> Toolset selection
-> Prometheus / Grafana / K8s / VM / DB / SaaS / MCP
-> filtered tool result
-> repeat
-> investigation output
```

优势：

- 数据源和 Toolset 广度强。
- 默认只读并遵循 RBAC。
- 有服务端过滤、JSON 遍历、输出变换和上下文预算。
- 大结果可以流式落盘，避免全部塞进模型上下文。
- 多 Provider 和生产集成成熟度较高。

边界：

- 公开架构的核心对象仍偏向 tool call/result 和 investigation output。
- 不应在没有证据时声称它“没有持久化”，但 canonical Evidence、raw/projection/hash、单证据 review 并不是其公开产品合同的中心。

Mini-Drop 应借鉴：大结果治理、只读默认、Toolset 封装、多 Provider。
Mini-Drop 不应复制：早期追求几十类连接器、常驻 Operator 和自动修复。

### 4.2 kagent

官方资料：

- [Architecture](https://github.com/kagent-dev/kagent/blob/main/docs/architecture/README.md)
- [Data flow](https://github.com/kagent-dev/kagent/blob/main/docs/architecture/data-flow.md)
- [CRDs and types](https://github.com/kagent-dev/kagent/blob/main/docs/architecture/crds-and-types.md)
- [Human in the loop](https://github.com/kagent-dev/kagent/blob/main/docs/architecture/human-in-the-loop.md)

架构特征：

```text
SandboxAgent / ModelConfig / MCPServer CRD
-> Controller reconcile
-> ActorTemplate / runtime
-> ADK agent loop
-> MCP tools / A2A agents
-> session/run persistence + OTel
```

优势：

- 声明式、类型化的 Agent、ModelConfig 和 ToolServer。
- 工具发现与 `toolNames` allowlist 清晰。
- `requireApproval` 和 A2A HITL 能暂停并恢复原来的精确 Tool Call。
- 配置真源、运行查询数据库和 OTel 可观测性职责清楚。

边界：

- 它是 Kubernetes-native 通用 Agent 平台，复杂度远高于 Mini-Drop 当前需要。
- tool artifact 不是以 canonical Evidence 生命周期为中心。

Mini-Drop 应借鉴：声明式 `ModelConfig/CollectorSpec`、精确 pause/resume、结构化审批、OTel Run/Tool trace。
Mini-Drop 不应复制：CRD Controller、Actor substrate、通用多 Agent、BYO Runtime 和 Memory 平台。

### 4.3 K8sGPT

官方资料：

- [K8sGPT repository](https://github.com/k8sgpt-ai/k8sgpt)
- [K8sGPT docs](https://docs.k8sgpt.ai/)

架构特征：

```text
Kubernetes objects
-> deterministic analyzers
-> Result.Error
-> optional --explain
-> LLM enriches/explains analyzer result
```

优势：

- 把 SRE 知识编码为确定性 Analyzer，行为容易理解。
- 支持多 Provider、缓存、custom analyzer、匿名化和 MCP。
- 适合稳定检查已知 Kubernetes 问题。

它也最准确地代表了 Mini-Drop 当前不希望继续的产品形态：**规则先发现并分类，AI 再解释**。这类架构可以作为离线 baseline，但不应再被包装成 Mini-Drop 的 AI 主能力。

### 4.4 Microsoft AIOpsLab

官方资料：

- [AIOpsLab repository](https://github.com/microsoft/AIOpsLab)
- [AIOpsLab paper](https://arxiv.org/abs/2501.06706)

它主要是研究/评测环境，不是生产运维 Agent。核心抽象为：

```text
Problem = Application + Task + Fault + Workload + Evaluator
```

Orchestrator 部署应用、注入故障、启动 workload，与 Agent 循环交互，保存 Session trace，最后评分并清理环境。

Mini-Drop 应直接借鉴：

- Scenario、Workload/Fault、Agent、Environment、Evaluator 分离。
- Detection、Localization、Analysis、Mitigation 分项评测。
- 完整交互 trace、step、token、time 和 cost 评分。

不能复制到生产链：任意 shell、弱 blocklist、把 observation 字符串当 Evidence、评测控制面与生产权限混用。

### 4.5 商业运维 AI 的公开能力链

这里的“架构”只指官方资料能够验证的产品功能链，不推断闭源产品的内部微服务、存储模型或未公开审批协议。

#### Datadog Bits AI Investigations

官方资料：

- [Bits AI Investigations](https://docs.datadoghq.com/bits_ai/bits_investigation/)
- [Investigate issues](https://docs.datadoghq.com/bits_ai/bits_investigation/investigate_issues/)
- [Bits AI evaluation platform](https://www.datadoghq.com/blog/engineering/bits-ai-eval-platform/)

公开可验证的功能链是：Monitor/Synthetic/人工 Prompt/Slack 触发调查，系统生成并更新假设，查询 Metrics、APM、Logs、Events、Profiler、DBM、RUM、Network 和代码等信号，逐步评价 Evidence，最后形成结论或明确 `inconclusive`。调查可展示 Steps 和 Hypothesis Tree，并支持 RBAC 和自动调查限额。

最值得 Mini-Drop 借鉴的是其评测方法：一个 label 由 `ground truth + world snapshot` 组成，snapshot 保存事故当时可用的查询和信号位置，再按技术、故障类型和难度分层，以 pass@k 和周期性回归评估。该思路可以直接转化为 Mini-Drop 的 Branching Replay。

公开资料没有证明每条 Evidence 都具备 canonical raw Artifact 下载、独立 AI AnalysisRun 和人工降信任传播，也不能把它写成自动生产修复系统。

#### Grafana Assistant Investigations

官方资料：

- [Grafana Assistant Investigations](https://grafana.com/docs/grafana-cloud/platform/grafana-assistant/platform/investigation/)

公开功能链是：人工或告警启动，形成 Plan 与 Hypotheses，查询 metrics/logs/traces/profiles，收集 Sources/Evidence，允许用户 hints、corrections 和 steer，最后生成带 Sources 的 Report。Source Citation 可以定位到 panel query、查询、时间窗和 datasource；用户可 Disprove/Reopen 假设，`/ask` 可以旁路提问而不改变当前计划和报告。

Mini-Drop 应吸收 Citation、steer、Disprove/Reopen 和只读旁路问答。Grafana 的 Source 主要是查询级引用，不等同于 Mini-Drop 目标中的统一 raw Artifact、下载、ReviewRevision 和单证据 AnalysisRun；Mini-Drop 也不必强制每次调查收敛到根因。

#### Azure SRE Agent

官方资料：

- [Azure SRE Agent overview](https://learn.microsoft.com/en-us/azure/sre-agent/overview)

公开功能链包括 Azure/外部事件、Observability/Incident/SCM 上下文、Skills/Subagents/Python/MCP/Hooks、工具权限判断、人工批准、执行后 Verification，以及写入用户 Application Insights 的审计。官方明确 mitigation 只提出，未经人工批准不执行，并使用 Managed Identity 与 Azure RBAC 控制资源访问。

Mini-Drop 应吸收 `proposal != execution`、每次工具调用前的 Permission Gate、专门 Verification 角色和用户可持有的审计。当前阶段不复制整个 Azure 控制面、通用 Bash 或广泛 Action Plane。

#### PagerDuty AIOps / SRE Agent

官方资料：

- [PagerDuty Advance](https://support.pagerduty.com/main/docs/pagerduty-advance)
- [SRE Agent](https://support.pagerduty.com/main/docs/sre-agent)
- [Connectors, tools and skills](https://support.pagerduty.com/main/docs/connectors-tools-and-skills)
- [PagerDuty AIOps](https://support.pagerduty.com/main/docs/aiops)

公开产品应拆成两层理解：第一层做 Event ingestion、grouping/suppression/correlation/probable origin；第二层由 Incident 驱动 SRE Agent 使用 connectors、runbooks 和 service-scoped memory，生成 likely cause、next steps 和 remediation recommendation。

Mini-Drop 可借鉴逐 Connector 的 Tool allowlist、结构化 Skill 和按 Service 隔离且可 redact 的 Memory。不要把 PagerDuty 的告警归并或 Probable Origin 引入 Mini-Drop 主线，否则会重新回到用户希望弱化的规则归因。

#### Dynatrace Intelligence / Davis

官方资料：

- [Davis AI](https://docs.dynatrace.com/docs/discover-dynatrace/platform/davis-ai)
- [Root cause analysis](https://docs.dynatrace.com/docs/dynatrace-intelligence/root-cause-analysis)

公开功能链是 OneAgent/OTel/integrations 数据进入 Grail 和 Smartscape 实时拓扑，结合 baseline/anomaly 与 causation-based analysis 形成 Problems/RCA，并可连接 AutomationEngine 或 agentic workflow。其结构性优势是资源拓扑与依赖上的确定性因果分析，而不是纯生成式模型猜测。

Mini-Drop 应借鉴稳定资源身份、依赖关系和时间上下文。它不应在当前阶段复制“大一统拓扑 + 自动根因排名”，该成本与 Mini-Drop 的进程级深度采集优势不匹配。

#### 商业方案提炼

| 产品 | 官方公开的核心优势 | Mini-Drop 应吸收 | 不应误判或照搬 |
|---|---|---|---|
| Datadog | 多信号假设调查、可 `inconclusive`、world-snapshot 评测 | Snapshot replay、分层回归 | 未公开的 canonical Artifact/逐工具审批不能假定 |
| Grafana | 查询级 Source Citation、Hypothesis UI、实时 steer | Citation、旁路问答、人工纠偏 | Source 不等于完整 Evidence 生命周期 |
| Azure SRE Agent | Permission Gate、审批执行、Verification、审计 | Proposal/Execution 分离、验证 | 全云控制面和通用执行面 |
| PagerDuty | Incident 协作、Connector/Skill、服务级 Memory | Tool allowlist、可治理 Memory | 告警归因不作为 Mini-Drop 主线 |
| Dynatrace | 拓扑与确定性 causation-based RCA | 资源身份、依赖和时间上下文 | 大一统拓扑和自动排名 |

### 4.6 开源与评测方案差异矩阵

| 维度 | HolmesGPT | kagent | K8sGPT | AIOpsLab | Mini-Drop 目标 |
|---|---|---|---|---|---|
| 产品类型 | 通用调查 Agent | K8s Agent 平台 | 规则分析 + AI 解释 | Agent 评测环境 | 深度采集与 Evidence 分析服务 |
| 主循环 | LLM 多轮 Toolset | ADK + MCP/A2A | Analyzer 先行 | Agent-Environment 循环 | AI 选 Collector -> Evidence -> 再选 |
| 工具优势 | 广度 | 声明式平台与互操作 | 已知 K8s 检查 | 故障/评测控制 | perf/eBPF/smaps/runtime 深度 |
| Evidence 一等对象 | 不是公开中心合同 | 不是公开中心合同 | 否 | Session observation | 是，raw + projection + hash + review |
| 人工干预 | RBAC/操作控制 | 精确 HITL continuation | 主要是运行选项 | 实验控制 | Evidence review + proposal approval |
| 评测优势 | 在线集成 | 平台可观测 | 确定性 | 强 | 产品与评测共享 Evidence trace |
| Mini-Drop 是否复制 | 否，学结果预算 | 否，学类型/HITL | 仅作 baseline | 学评测方法 | 自身主线 |

### 4.7 Mini-Drop 的可辩护优势

Mini-Drop 不会短期超过 HolmesGPT 的连接器广度，也不应试图超过 kagent 的通用平台能力。它可以形成优势的窄而深区域是：

1. **Deep Runtime Acquisition**：进程级 CPU hotspot、off-CPU/锁等待、I/O latency、smaps、FD/connection、语言运行时状态。
2. **Evidence Lifecycle**：每次采集都有 raw、Projection、hash、target identity、time window、lineage 和 review。
3. **Human-governed AI**：排除或降信任后，模型上下文和后续分析真实改变。
4. **Measurable Acquisition Intelligence**：可以测 AI 是否用最少成本拿到足够证据，而不是只测最终文案是否包含根因词。
5. **Interoperability without identity loss**：通过 MCP 提供能力，但产品身份仍是 Collector/Evidence runtime。

## 5. 《演进.pages》和 v6 旧设计的参考价值

### 5.1 《演进.pages》中值得采用的部分

| 建议 | 采用方式 | 优先级 | 调整原因 |
|---|---|---|---|
| 统一 Tool Catalog | 改为唯一 `CollectorSpec`/`SourceSpec`，再生成模型 Tool Catalog | P0 | 直接解决 Python/JS/Server/Agent 多处重复 |
| 可插拔 DiagnosticStrategy | 改为评测侧 `CollectorSelectionStrategy` | P1 | 不再让策略名对应多套在线诊断系统 |
| RuntimePolicy | 完整保留并收敛 | P0 | scope、风险、预算、审批和副作用是必须的确定性边界 |
| RuntimeOptions | 限定为 model/config、cycle/tool/token budget | P1 | 不能允许任意 Prompt 或任意执行参数破坏可复现性 |
| Experiment Matrix Harness | 作为核心产品资产重建 | P0 | 这是证明 AI 增益与竞品差异的关键 |
| Registry 一致性检查 | 扩展为 schema/catalog/Agent/Web/评测一致性门禁 | P0 | 防止“注册了但不可调用”或前端/Agent 漂移 |

该文档最有价值的不是增加更多 Agent 框架，而是把配置、策略、权限、运行选项和实验变量分开。需要避免的是继续保留 `hybrid / causal_graph / hypothesis_first` 作为面向用户的产品模式；它们最多作为离线实验 Arm。

### 5.2 v6 中必须保留的优秀能力

- canonical `CaseEvidence` 与版本化 `EvidenceProjection`。
- Turn、Run、Cycle、ModelRequest、AssistantMessage 的可追踪结构。
- `AgentRuntimePort`，使 Pi 可替换而不污染领域内核。
- `CaseSupervisor` 作为 AI 派生执行的唯一所有者。
- Durable Outbox/Wakeup，保证新 Evidence 最终触发下一 Cycle。
- generation/revision/idempotency fencing。
- `ANSWER_ONLY` 零副作用语义。
- 用户 pause/stop/correct/retarget/review 的确定性控制通道。
- target identity、resource incarnation、time window 和 projection hash。
- Artifact 与 Evidence 的双向 lineage。
- 大结果裁剪、分页、落盘和上下文预算的设计方向。

### 5.3 v6 中应简化或移出本期的部分

- `HypothesisRevision -> CausalGraphRevision -> ConclusionRevision -> Recovery` 不再是每个 Case 的强制主链。
- `Operation -> Campaign -> Assignment -> ExecutionUnit` 不暴露给模型或普通 UI；单目标默认编译为 `CollectionRequest -> Task`，多目标时才使用内部 fanout 结构。
- Critic 不作为 P0 必选组件；先证明单 Agent 自适应采集有增益，再通过 ablation 决定是否加入。
- Recovery、Capacity、Skill/Knowledge、外部 MCP 不同时抢占主线。
- Formal Authority、签名 Provider Ledger、完整 P01-P10 可保留方法，但不阻塞第一个 AI Collector Beta。
- 不再要求根因 Top-1、复合因果图和自动修复作为统一 Definition of Done。

## 6. 目标架构

### 6.1 总体架构图

```text
┌─────────────────────────────────────────────────────────────┐
│ Web 控制台 / CLI & CI / Mini-Drop MCP Server               │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Mini-Drop Core Server                                       │
│                                                             │
│  Interaction & AI Plane                                     │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Case / Turn / AgentRun / ModelGateway                │  │
│  │ AI: analyze evidence, find gap, select collector     │  │
│  └────────────────────────┬──────────────────────────────┘  │
│                           │ CollectorProposal               │
│  Deterministic Execution Plane                              │
│  ┌────────────────────────▼──────────────────────────────┐  │
│  │ Tool Gateway + RuntimePolicy + Approval + Fencing    │  │
│  └────────────────────────┬──────────────────────────────┘  │
│  ┌────────────────────────▼──────────────────────────────┐  │
│  │ CollectionSupervisor -> CollectionRequest -> Task    │  │
│  └───────────────┬───────────────────────────┬───────────┘  │
│                  │                           │              │
│  Evidence Plane  │                           │              │
│  ┌───────────────▼───────────────────────────▼───────────┐  │
│  │ Artifact -> Evidence -> Projection -> AnalysisRun    │  │
│  │ ReviewRevision / Citation / Preview / Download       │  │
│  └────────────────────────┬──────────────────────────────┘  │
│                           │ Outbox / Wakeup                 │
└───────────────────────────┼─────────────────────────────────┘
                            │
          ┌─────────────────┴──────────────────┐
          ▼                                    ▼
┌────────────────────────┐          ┌────────────────────────┐
│ Mini-Drop Node Agents  │          │ Source Gateway         │
│ perf/eBPF/smaps/runtime│          │ OTel/Prom/Trace/MCP    │
└────────────┬───────────┘          └────────────┬───────────┘
             └──────────────────┬────────────────┘
                                ▼
                 被测 Linux / Container / Service

Evaluation Plane（与生产权限隔离）
k6 + Fault Harness -> Scenario -> Agent Trace -> Evidence -> Evaluator
```

### 6.2 四个平面的职责

#### Interaction & AI Plane

负责：

- 理解用户调查目标和目标资源。
- 读取 Collector Catalog、Evidence inventory 和受限 Projection。
- 对单条/多条 Evidence 生成结构化分析。
- 识别当前缺少的 information goal。
- 提出下一 `CollectorProposal` 或 `NextCollectionProposal`。
- 决定继续、询问、停止或 `INSUFFICIENT_EVIDENCE`。

不负责：权限判断、Task 创建、任意命令执行、Evidence 状态修改和最终安全验证。

#### Deterministic Execution Plane

负责：

- Collector/Source allowlist 和严格参数 schema。
- 目标解析、scope、capability、risk、budget、approval。
- revision/generation/idempotency fence。
- 接受/拒绝 Proposal，并创建唯一 CollectionRequest/Task。
- timeout、取消、重试、结果大小和资源开销上限。

它不生成根因候选，也不对异常做业务意义排名。

#### Evidence Plane

负责：

- raw Artifact 的不可变保存与 hash。
- 所有来源统一物化为 canonical Evidence。
- 确定性、版本化、可裁剪的 Projection。
- preview、download、bundle、lineage 和 citation。
- 单 Evidence 与多 Evidence AnalysisRun 的持久化。
- ReviewRevision 及其确定性影响。

#### Evaluation Plane

负责：

- workload、fault、cleanup 和 activation probe。
- 隔离 private oracle，防止答案泄漏。
- 提供可分支的 Collector replay。
- 保存模型、工具、Evidence、token、时间、开销 trace。
- 计算语义支持、证据充分性、效率、安全和统计结果。

k6 和 fault injector 永远不进入生产模型可见 Tool Catalog。

### 6.3 AI 与规则的最终边界

| 决策 | AI | 确定性系统 |
|---|---|---|
| 理解用户问题 | 主责 | 校验输入长度/类型 |
| 选择可能有信息增益的 Collector | 主责 | 限制可用集合、风险和预算 |
| 提出参数/目标/时间窗 | 主责 | clamp、schema、scope、capability 校验 |
| 创建 Task | 无权 | Supervisor 唯一负责 |
| 解析原始 Artifact | 不负责 | 版本化 parser/projection |
| 单 Evidence 解释 | 主责 | 引用、schema、状态与 side-effect 校验 |
| 识别冲突和不足 | 主责 | freshness、identity、review state 提供事实 |
| 根因候选和排名 | 不作为默认产品输出 | 规则也不再在线生成 |
| 权威因果图 | 不在本期 | 不自动生成 |
| Evidence 排除/恢复 | 提示影响 | 人工命令 + 确定性状态传播 |
| 恢复执行 | 不在本期 | 默认关闭 |
| 停止/拒答 | AI 提议 | budget/no-progress 可强制停止 |

### 6.4 推荐的核心领域对象

#### `ModelConfig`

```text
model_config_id
provider / model
credential_ref               # 只存 Secret 引用
temperature / max_tokens / seed
prompt_version
tool_call_capabilities
enabled / created_at
```

#### `CollectorSpec`

```text
collector_id / spec_version / implementation_version
display_name / description
information_goals[] / output_signals[]
target_types[]
parameter_schema
risk_level / required_capabilities[]
default_duration / max_duration
estimated_overhead / max_result_bytes
artifact_types[]
projection_kind / projection_version
preview_modes[] / download_supported
enabled
```

`information_goals` 描述采集器能够回答什么，例如“目标进程用户态/内核态 CPU 分解”或“热点调用栈”，而不是写死“CPU 故障必须调用 perf”。这能给 AI 足够语义，又不把规则映射伪装成 AI 决策。

#### `CollectorProposal`

```text
proposal_id / case_id / agent_run_id / cycle_id
collector_id / target_selector / parameters / time_window
information_goal / reason_summary
expected_cost / expected_risk
input_evidence_refs[]
status = PROPOSED | ACCEPTED | REJECTED | EXPIRED
validation_result
```

#### `CollectionRequest`

这是 Supervisor 接受 Proposal 后的权威执行对象：

```text
collection_request_id / proposal_id / case_id
collector_spec_version / resolved_target_identity
effective_parameters
runtime_generation / control_revision / scope_revision
idempotency_key / budget_reservation
status
task_id / attempt_ids[]
```

#### `Evidence`

```text
evidence_id / case_id
collector_id / collector_spec_version / producer_version
resolved_target_identity / resource_incarnation
effective_parameters / observed_time_window
raw_locator / media_type / size_bytes / sha256
projection_kind / projection_hash
collection_request_id / task_id / attempt_id / source_call_id
freshness / completeness / quality
review_state / review_revision
created_at
```

#### `EvidenceProjection`

```text
projection_id / evidence_id
schema_id / schema_version / parser_version
content / field_index
source_hash / projection_hash
truncated / source_bytes / projected_bytes
created_at
```

#### `EvidenceAnalysisRun`

```text
analysis_run_id / case_id / mode = SINGLE | MULTI | COMPARE
evidence_inputs[]             # evidence_id + review_revision + projection_hash
model_config_id / prompt_version
side_effect_policy = READ_ONLY
facts[]                       # claim + field/span citations
anomalies[] / interpretations[]
conflicts[] / limitations[]
next_collection_proposals[]  # 仅提案，不自动执行
status / token_usage / latency / created_at
```

#### `InvestigationReport`

持续调查需要一个替代旧“根因排名/强制因果图”的终态对象：

```text
report_id / case_id / agent_run_id / revision
state = COMPLETED | PARTIAL | INSUFFICIENT_EVIDENCE | NO_ISSUE_FOUND
supported_findings[]         # claim + citations + applicability
contradicted_or_ruled_out[]
unresolved_alternatives[]
open_information_goals[]
limitations[]
recommended_next_steps[]    # 默认仍是 Proposal，不是执行动作
input_evidence_revisions[] / model_config_id / prompt_version
created_at
```

它允许 AI 提交有证据支持的发现，但不要求制造连续 Top-N、伪精确 confidence 或每次都生成 CausalGraph。只有存在时间、身份和传播证据时，报告才附可选 causal edge；Verifier 只验证状态、引用和能力边界，不替模型生成根因。

#### `ReviewRevision`

```text
review_id / evidence_id / revision
decision = TRUSTED | LOW_TRUST | EXCLUDED | RESTORED
reason_code / reason / actor_id / created_at
```

### 6.5 Collector Catalog 的唯一真源

推荐新增独立、可被 Server 与 Agent 共同导入的轻量合同包，例如：

```text
mini_drop_contracts/
  collector_spec.py
  catalog/
    collectors.v1.json
```

职责分配：

- Catalog JSON 是 metadata、schema、风险、输出和展示的唯一真源。
- Agent 的 `COLLECTORS` 只保存 `collector_id -> implementation` 绑定和本机 capability 探测。
- Server 从 Catalog 生成 `/api/v1/collectors`、Task 参数校验和模型 Tool schema。
- Web 只消费 Server API，不再维护 `COLLECTOR_META` 副本。
- Evaluation manifest 通过 catalog hash 固定同一工具集合。
- 一致性测试校验 Catalog、Agent implementation、Projection parser 和 preview renderer 的闭包。

`swarm_actuation` 不是 Collector，应从 Collector Catalog 分离为默认禁用的 `ActionSpec`；本期不暴露给模型。

### 6.6 单 Evidence AI 分析合同

用户从任意 Evidence 卡触发“AI 分析”时：

```text
POST /api/v1/cases/{case_id}/evidence/{evidence_id}/analyses
-> 固定 READ_ONLY
-> 加载指定 Evidence revision + Projection hash
-> 运行模型
-> 校验每条 claim 的 field/span citation
-> 持久化 EvidenceAnalysisRun
-> 返回 facts / anomalies / limitations / optional next proposals
```

必须满足：

- 分析不会自动创建 Task。
- 输入中明确展示 review state、freshness、target 和 time window。
- `EXCLUDED` 不进入自动或综合分析；用户仍可显式发起隔离的单证据分析，结果必须标记 `EXCLUDED_INPUT`，且在 Evidence 被 `RESTORED` 前不能回流当前 Case。
- 结果不能只保存自由文本；必须有结构化 claim 和精确引用。
- 同一 Evidence 新增 review revision 或 Projection version 后，旧分析标记为 `STALE_INPUT`，但保留审计。

### 6.7 Evidence 预览与下载合同

建议统一接口：

```text
GET /api/v1/cases/{case_id}/evidence/{evidence_id}
GET /api/v1/cases/{case_id}/evidence/{evidence_id}/preview
GET /api/v1/cases/{case_id}/evidence/{evidence_id}/download?format=raw|bundle
GET /api/v1/cases/{case_id}/evidence/{evidence_id}/analyses
POST /api/v1/cases/{case_id}/evidence/{evidence_id}/reviews
```

规则：

- 每种 `CollectorSpec.artifact_type` 必须注册 preview renderer 或明确的安全文本/二进制占位。
- `download?format=raw` 返回原始 Artifact；没有天然文件的 Source Evidence 生成规范 JSON raw。
- `bundle` 至少包含 manifest、raw、Projection、hash、lineage、review 和 AnalysisRun 元数据。
- HTML/SVG 火焰图在 sandbox frame 中预览；日志和 JSON 限长、脱敏、可分页。
- 缺少 raw object、hash 不一致或 parser 失败时返回明确状态，不伪装成功。

### 6.8 人工 Review 的确定性语义

不要用不透明的“根因权重 × 0.6”继续制造伪精确排名。建议采用可解释的使用规则：

| 状态 | 模型可见 | 可引用 | 可单独支撑高置信分析 | 后续行为 |
|---|---:|---:|---:|---|
| `TRUSTED` | 是 | 是 | 是 | 正常使用 |
| `LOW_TRUST` | 是，带原因 | 是 | 否 | 必须请求交叉证据或降低确定性 |
| `EXCLUDED` | 否 | 否 | 否 | 使依赖它的当前分析失效并触发重算 |
| `RESTORED` | 是 | 是 | 按恢复后的有效状态 | 触发新 revision 与必要重算 |

这已经实现了用户所说的“降低权重”，但语义是可审计的影响限制，而不是藏在校准公式里的数值。

表中的“模型可见”指自动调查与综合分析；用户显式发起的隔离单证据分析可以读取 `EXCLUDED` 输入，但结果带 `EXCLUDED_INPUT`，不能回流当前 Case。

### 6.9 Source Gateway、OTel 和 MCP

#### OTel/Prometheus/Trace

它们是 Evidence Source，不应让模型直接写查询语言。模型调用语义工具，例如：

```text
get_service_red(service_ref, window)
get_trace_critical_path(service_ref, window)
get_trace_error_edges(service_ref, window)
get_log_error_clusters(resource_ref, window)
```

Gateway 使用版本化模板注入 selector、scope、时间窗和结果预算，再物化为 Evidence/Projection。

#### MCP

分两种方向：

1. 北向 Mini-Drop MCP Server：允许 CI 或其他 Agent 创建调查、列 Collector、读取/分析 Evidence。
2. 外部 MCP Source：未来通过 SourceRegistry 接入 CMDB、变更或其他只读事实。

两者都不能绕过内部领域合同。模型不能持有 MCP URL、Token，不能动态连接任意 Server，MCP Result 必须清洗后进入 Evidence。

#### k6

k6 是 Evaluation Plane 的 workload/SLO 工具，不是生产 Agent Tool。首期模型看不到 k6，也不能修改 fault/workload。

### 6.10 Runtime 与可靠性

需要保留 v6 的以下不变量：

1. AI 派生 CollectionRequest 只能由持有效 lease 的 Supervisor 创建。
2. 每个 Cycle 使用新的 Case/Evidence Snapshot，不能依赖 Sidecar 内存真源。
3. Evidence commit 与 Outbox 在同一数据库事务；Wakeup 可重试、可去重。
4. 每次模型请求记录实际 Projection hash、Catalog hash、Policy hash 和 ModelConfig。
5. 写 Proposal 带 runtime generation、control/scope revision 和 idempotency key。
6. pause/stop 后迟到 Tool Call 不能创建新 Task。
7. `ANSWER_ONLY` 和单 Evidence 分析的副作用增量必须为零。

## 7. 保留、合并、迁出和删除规划

### 7.1 必须保留并强化

| 资产 | 位置 | 处理 |
|---|---|---|
| Linux/语言 Collector | `agent/mini_drop_agent/collectors/` | 保留，补齐统一 Spec、输出和开销合同 |
| Task/Attempt/Artifact/AnalysisJob | Server repository/worker | 保留；区分确定性 Artifact processing 与 AI AnalysisRun |
| PostgreSQL/MinIO | persistence/storage | 保留，强化 raw/hash/bundle 对账 |
| Case/Turn/Run | Case + runtime | 保留为持续调查容器，不再等同于 RCA Case |
| CaseEvidence/Projection | diagnosis evidence modules + v6 repo | 升级为产品核心 |
| AgentRuntimePort/Pi Adapter | `server/app/agent_runtime/` | 保留，收窄工具和输出合同 |
| Tool Gateway/Policy/Fencing | runtime/v6 routes/policy | 保留并统一入口 |
| Supervisor/Lease | case supervisor/application repo | 保留，成为 AI CollectionRequest 唯一调度者 |
| Outbox/Wakeup | jobs/sql repository v6 | 保留 |
| Human Review/Audit | plan control/evidence review | 保留并补齐传播 |
| capability discovery/clamping | Agent main | 保留，从 Catalog 生成约束 |
| VM fault lifecycle/Oracle/统计 | benchmark/scripts | 保留方法，换评分对象 |

### 7.2 应合并或重命名

| 当前资产 | 目标处理 |
|---|---|
| `TASK_KINDS`、Agent `COLLECTORS`、Web meta、Query/Probe registry | 合并为 CollectorSpec 真源；各层只保留 implementation/renderer 绑定 |
| `DiagnosisOrchestrator` 中的采集调度 | 抽到 CollectionSupervisor；Orchestrator 不再是主脑 |
| Query Operation 与深度 Collector | 统一为 AI 可见 acquisition catalog 的不同 Spec 类型 |
| `AnalysisJob` | 明确为确定性 Artifact processing；新建 `EvidenceAnalysisRun`，避免名称混淆 |
| Case UI | 从“AI 诊断/恢复”改为“AI 调查与 Evidence 工作区” |
| Plan/Campaign/ExecutionUnit | 收到内部调度层；单目标主路径简化为 Proposal -> CollectionRequest -> Task |
| Strategy Registry | 移到实验 Harness；生产只保存激活的 ModelConfig/Prompt/Policy 版本 |

### 7.3 从默认在线产品迁出

| 模块/能力 | 处理 |
|---|---|
| `server/app/rca/candidates.py` / `calibrator.py` / `rules.json` | 冻结为 benchmark baseline，停止在线调用 |
| `server/app/diagnosis/domain_analyzers.py` | 仅保留数据质量/collector health 检查；归因分支迁到 baseline |
| `RulesOnlyReasoner` | 不再作为产品 fallback；保留为离线对照直到新评测稳定 |
| `evaluate_hypotheses` / `rca_candidate_analysis` Tools | 从生产 Tool Catalog 移除 |
| 自动 `Hypothesis/CausalGraph/Conclusion` | 从默认 Case 状态机和 UI 移出 |
| root-cause ranking API/UI | 变为 legacy read-only 后删除 |
| Recovery/Actuation/Autonomous Agent | 后台自治虽默认关闭，但路由和依赖仍被默认装配；应从主导航和默认 service graph 移出，未来单独立项 |
| Deployment Capacity | 保持独立工具，不混入 Investigation |

### 7.4 不能整目录直接删除的内容

- `server/app/rca/evidence.py` 中可能仍有 Artifact 到结构化 Evidence 的转换逻辑，应先迁入 Projection parser。
- `domain_analyzers.py` 中数据清洗、阈值提取与 collector health 逻辑可能可复用，但归因判断不应继续在线。
- `DiagnosisOrchestrator` 同时持有调度、证据物化、审计和结论逻辑，必须先分解依赖。
- Recovery 相关 fencing/approval 模式可以抽为通用安全组件，不能因关闭恢复而删除安全基础。
- 旧数据库表在兼容读取和迁移验证完成前不得直接 drop。

## 8. 分阶段改造计划

### P0：冻结产品边界与建立架构门禁

目标：阻止继续向旧 RCA/Recovery 主线增加功能。

改动：

- 本文成为当前 AI 产品架构基线。
- README 明确 Collector/Evidence 定位，旧“5 层智能归因”标为 legacy。
- 建立 dependency map，列出所有在线 RCA、CausalGraph、Recovery 调用入口。
- 增加 architecture tests：Case AI 派生 Task 只能经过 Supervisor；单 Evidence 分析零副作用。

退出标准：新增 AI 功能不再依赖 `RulesOnlyReasoner`、RCA candidate 或 recovery route。

### P1：统一 CollectorSpec 与工具发现

目标：让“Agent 能做什么”只有一个真源。

改动：

- 新增 `mini_drop_contracts` 与 `collectors.v1`。
- 迁移 12 个采集型 Collector；`swarm_actuation` 分离为 Action。
- Server 生成 Collector API、参数校验和 AI Tool schema。
- Agent 上报实现版本与当前机器的可执行 subset。
- Web 从 API 渲染 Collector 名称、参数、风险和结果类型。
- 扩展 registry consistency gate。

退出标准：Catalog、Agent implementation、Server validation、Projection parser、preview renderer 一致；不存在 UI-only 或 Agent-only Collector。

### P2：完成 Evidence 产品合同

目标：先让 Evidence 服务本身真正可用，再让 AI 自主采集。

改动：

- 统一 canonical Evidence detail/preview/download/bundle API。
- 为每种 Artifact 注册 Projection 与 Preview adapter。
- 新建持久化 `EvidenceAnalysisRun` 和单 Evidence AI 分析接口。
- 实现 claim-level field/span citation verifier。
- 使 `LOW_TRUST`、`EXCLUDED`、`RESTORED` 对 Snapshot、AnalysisRun 和引用确定性传播。
- 前端 Evidence Drawer 增加“AI 分析”、历史分析、限制和下一采集提案。

退出标准：所有启用 Collector 的成功产物 preview/download 成功率 100%；EXCLUDED 再引用为 0；单 Evidence 分析实际副作用为 0。

### P3：建立唯一 AI Collector Loop

目标：模型真正决定采什么，而不是给规则结果换标签。

改动：

- Pi Tool Catalog 只暴露 Evidence read、Collector discovery、Collector proposal、Analysis finish/abstain。
- 新增结构化 `CollectorProposal` 与 `CollectionRequest`。
- Supervisor 唯一接受 Proposal 并创建 Task。
- Evidence commit 通过 Outbox/Wakeup 触发下一 Cycle。
- 实现 max cycle、budget、no-progress 与重复采集停止规则。
- 保留固定 sequence/rules baseline，但不进入生产 runtime。

退出标准：至少三个真实场景证明模型根据不同 Evidence 选择不同下一 Collector；Provider trace 证明 Tool Call 来自模型；无规则 candidate 泄漏。

### P4：收敛旧诊断与恢复产品面

目标：消除用户当前感受到的“两套系统”。

改动：

- 停止 Case 主链调用 candidates/calibrator/domain root classification。
- `/diagnoses` 与旧 RCA 只读一段兼容期。
- 从 Tool Catalog、Case Snapshot 和 UI 移除 root ranking/automatic causal graph。
- 将 recovery/actuation 从默认 app graph、导航和 Case workspace 移除或 feature-disable。
- 分解 `DiagnosisOrchestrator`，留下证据/调度所需服务后删除归因编排。
- 更新 README、API 文档和 Demo。

退出标准：关闭 legacy flag 后，采集、Evidence、AI 分析、下一采集和人工 Review 全链仍通过；运行时不导入在线 RCA 模块。

### P5：接入 OTel Source 与北向 MCP

目标：扩展常规遥测，但不稀释深度采集定位。

改动：

- 为 RED、关键路径、错误边和日志簇建立版本化语义 SourceSpec。
- Source Result 统一进入 Evidence/Projection。
- Mini-Drop MCP 暴露 Collector/Evidence 高层 API。
- 外部 MCP Source 仅允许注册、审批和清洗后的只读结果。

退出标准：OTel 与 Node Collector Evidence 可在同一分析中被精确引用；模型无法写任意查询或连接任意 MCP。

### P6：重建评测并开展竞品对照

目标：证明 AI 采集和深度 Evidence 的独立增益。

改动：

- 新建 `benchmarks/collector_agent_v1/`。
- 迁移 ai_ops_v2 的 private oracle、clean environment、hash、audit 和统计骨架。
- 修复 live eval 的故障名泄漏和否定句关键词误判。
- 建立 tool-parity MCP adapter，先对接 HolmesGPT，再评估 kagent。
- 输出 Common Telemetry、Deep Runtime、Evidence Governance 三张独立榜。

退出标准：满足第 9 节 Beta 门槛；对外优势结论必须有足够独立 holdout 与配对置信区间。

## 9. 评测架构与验收标准

### 9.1 当前评测为何不能证明 AI 优势

- `golden_scenarios/` 和 `lightweight_ai_eval` 主要调用 domain analyzers + default reasoner，模型没有自主选采集器。
- `ai_ops_v2` 的 30-case lifecycle、Oracle 和统计设计值得保留，但当前 VM runner 仍调用旧 `/diagnoses` + `CONSTRAINED_HYBRID`。
- 现有历史报告明确存在“LLM 只做意图解析，规则负责 RCA/探针选择”的运行。
- `run_agent_strategy_matrix.py` 当前没有把所选 strategy/reasoner 真正传入评测主链，不同 Arm 实际可得到同一默认规则结果。
- 当前 Pi live fixture 的命令行包含故障标签，`process_scan` 会把 private answer 暴露给模型。
- 关键词评分会把“证据不支持 cpu-hotspot”错误判为命中。
- 当前 citation validity 多为 ID 存在性，而不是引用字段是否语义支持 claim。

所以旧分数只能作为历史规则链回归，不能作为 AI Collector Agent 的准确率证明。

### 9.2 新评测的四条轨道

| 轨道 | 目的 | 运行频率 |
|---|---|---|
| C0 Contract/Conformance | Spec、权限、幂等、preview/download、review、cleanup | 每次提交 |
| C1 Branching Replay | AI 在完整可选 Collector 分支中规划下一步 | 高频、可重复 |
| C2 Evidence Analysis | 单/多 Evidence、冲突、陈旧、LOW_TRUST/EXCLUDED 分析 | 高频、模型回归 |
| C3 Live E2E | 真实 Linux/Online Boutique、采集开销、Evidence 和 cleanup | 发布候选 |

Replay 不能只预先提供“正确证据”。每个状态都要包含可调用 Collector 及其录制返回，包括 distractor、失败和低价值结果，才能测出真实选择能力。

### 9.3 Oracle 应描述信息目标，不写死采集器序列

私有 Oracle 至少包含：

```text
information_goals[]
acceptable_next_actions[state]
sufficiency_condition
must_abstain
claim_assertions[]           # expected fact + evidence field/span
forbidden_or_wasteful_actions[]
budget / approval expectations
```

这样 `perf_cpu`、`pyspy` 或其他能够达到同一信息目标的工具可以被视为等价，不会把评测变成让模型背固定规则。

### 9.4 内部基线与消融

| Arm | 说明 | 要证明的变量 |
|---|---|---|
| B0 | random / cheapest-first | sanity floor |
| B1 | 固定 `sys_metrics -> process_scan` | 无 AI 固定流程 |
| B2 | 旧规则 Collector policy | K8sGPT-like baseline |
| B3 | 同模型 direct answer，无工具 | 模型先验 |
| B4 | 同模型 + 一次性全量 Evidence | 自适应采集是否有价值 |
| B5 | 同模型 + common telemetry tools | 常规观测能力 |
| M1 | Mini-Drop + deep runtime Collector | 核心产品增益 |
| M2 | M1 + Critic | Critic 的净收益，非默认假设 |

### 9.5 竞品比较必须分两张榜

#### Tool-parity

Mini-Drop、HolmesGPT、可行时的 kagent 使用：

- 同一个模型 snapshot。
- 同一个 Mini-Drop MCP Collector simulator/catalog。
- 同一个时间、调用次数、token 和风险预算。
- 同一个 Evidence 返回和 private Oracle。

该榜只比较 Agent 规划和分析能力。

#### Native-product

各产品使用自己的推荐工具栈跑同一公开场景。该榜比较端到端产品能力。Mini-Drop 的 deep runtime 优势可以在这里体现，但不能据此声称模型推理更强。

AIOpsLab、RCAEval 和 Cloud-OpsBench 是框架或数据来源，不列入同类产品排名。

### 9.6 主指标

不可被综合分掩盖的 Primary：

- `Evidence Sufficiency Success @ Budget`。
- Weighted Information Goal Recall。
- Claim Support Precision。
- Correct Stop/Abstain Rate。
- False Certainty Rate。

效率与 Collector：

- acceptable next-action Top-1/Top-k。
- First Useful Evidence latency。
- Time to Sufficient Evidence。
- wasteful collector ratio / premature stop / endless collection。
- 相对 Oracle Pareto 最低成本的 regret。
- Evidence Utility AUC。
- tool count、wall time、bytes、Worker CPU/RSS、token/cost/latency。

Evidence 产品：

- raw/projection hash 一致性。
- universal preview/download success。
- extraction precision/recall。
- field/span citation precision/recall。
- unsupported claims per 100 claims。
- conflict/stale detection。
- EXCLUDED 再引用、LOW_TRUST 单独高置信、RESTORED 行为恢复。

安全是硬门禁，不能被其他分数抵消：

- 未授权实际执行 = 0。
- approval bypass = 0。
- scope violation = 0。
- Oracle leakage = 0。
- cleanup failure = 0。

### 9.7 统计设计和建议门槛

- 统计单位是独立 Scenario，重复运行不能伪装扩大样本量。
- 同场景、同模型、同预算采用 paired randomized crossover。
- 比例使用 Wilson CI；成败配对使用 McNemar exact；连续差值使用 scenario-cluster bootstrap 或 paired permutation/Wilcoxon。
- 多个 primary endpoint 使用 Holm 校正。
- 锁定 model、prompt、catalog、policy、scenario、Evidence hash、seed 和 provider usage。
- 加入 leakage audit，扫描 prompt、cmdline、env、fixture/path/log 和 Evidence 中的 fault label/oracle token。

建议 Beta 门槛，在 pilot 后冻结：

| 指标 | 建议门槛 |
|---|---:|
| 独立 holdout Scenario | 至少 30 |
| Evidence Sufficiency Success | ≥ 80% |
| Weighted Information Goal Recall | ≥ 85% |
| Claim Support Precision | ≥ 98% |
| Correct Abstain | ≥ 90% |
| False Certainty | ≤ 5% |
| Collector execution success | ≥ 95% |
| Median cost regret | ≤ 30% |
| 每个 domain goal recall | ≥ 75% |

对外声称优于竞品前建议有 40–60 个独立 paired holdout Scenario。Common Telemetry 做 -5pp 非劣检验；Deep Runtime 要么成功率至少提升 10pp，要么充分证据的 cost/time 至少降低 25%，且 paired 95% CI 不跨 0。

## 10. 每个阶段完成后的实际可用性

| 停止点 | 服务是否可用 | 能力边界 |
|---|---|---|
| 当前版本 | 局部可用 | 原生采集可用；Evidence 部分可用；AI/规则归因混杂，不适合宣称通用运维 Agent |
| P1 完成 | 可作为规范化 Collector 平台 | 能发现、校验和人工运行 Collector；还不是完整 AI 服务 |
| P2 完成 | 可作为 Evidence 管理与 AI 分析 Beta | 所有证据可看、可下、可单独分析、可人工治理；AI 尚不自主闭环补采 |
| P3 完成 | 可作为 AI Collector Agent Beta | AI 可在受控范围内自主补采和停止；适合 Demo、内部试用和评测 |
| P4 完成 | 产品主线清晰可用 | 旧 RCA/恢复不再干扰；仍不是通用运维 Agent |
| P5-P6 完成 | 可开始对外验证差异化 | 有常规遥测、MCP 和公平竞品评测；仍不承诺自治恢复 |

如果推进到 P3/P4 后停止后续扩展，服务可以是一个真实可用的 **Linux/容器 AI 深度采集与 Evidence 分析 Beta**。它不能被称为成熟的通用运维 Agent，因为它仍缺少广泛资产拓扑、变更系统、Kubernetes/云连接器、持续告警接管和经验证的恢复能力。

这不是失败的边界，而是一个清楚、可测试、具有差异化的产品边界。

## 11. 删除门禁

任何旧模块只有同时满足以下条件后才能删除：

1. 所有调用者和数据依赖已列出。
2. 所需的 Evidence transform、policy、audit 能力已迁移并有纵向测试。
3. 新 Turn 已不再调用 `start_case_diagnosis()` 或其他旧 Diagnosis 入口。
4. Task、Query、Source 和 MCP Result 已全部进入 canonical Evidence。
5. 统一 preview/download、`EvidenceAnalysisRun` 和 Review 状态传播已经完成。
6. 生产 Tool Catalog 已移除 RCA/Hypothesis/CausalGraph 工具。
7. 新主链在 legacy flag 关闭时通过 C0、C2 和至少一条 C3。
8. 历史数据先停止新写，再完成必要双读、只读迁移或明确放弃策略。
9. UI、API、脚本、Demo、README 不再引用旧能力。
10. 旧 baseline 已冻结到 `benchmarks/`，生产包不再导入。
11. 数据库迁移与回滚经过验证，至少一个发布候选证明移除后无 Task/Evidence 回归。

建议删除顺序：

```text
从 Tool Catalog/UI 移除
-> 停止创建新 legacy 数据
-> legacy API 只读
-> 迁移可复用解析/验证能力
-> 关闭 feature flag 跑完整门禁
-> 删除在线 imports 和 service wiring
-> 删除代码
-> 最后再迁移/drop 旧表
```

## 12. 第一批需要审核的架构决策

为避免再次扩张成多套系统，建议直接采用以下默认决策：

1. 产品名义从“AI 根因诊断”改为“AI 调查与 Evidence 分析”。
2. Case 保留，但只作为会话、目标、Evidence 和 AgentRun 容器。
3. 不保留默认 root-cause ranking；AI 可输出“有证据支持的发现”和“未验证的可能解释”，二者必须分开。
4. `LOW_TRUST` 使用显式可见、不可单独支撑高置信的语义，不引入隐藏数值权重。
5. 用户“删除 Evidence”实现为可审计的 `EXCLUDED`；物理删除走独立留存策略。
6. Critic 只作为 M2 实验，不作为 P0 架构前提。
7. Recovery/Actuation 从当前主产品移出，未来单独评审。
8. MCP 是适配器，不是内部领域模型。
9. k6 只属于评测平面。
10. 旧规则归因保留到 benchmark baseline，不继续进入生产主链。

## 13. 主要风险与应对

| 风险 | 表现 | 应对 |
|---|---|---|
| 删除旧 RCA 导致解析能力一起丢失 | Artifact 无法投影 | 先把 transform/parser 迁入 Evidence Plane |
| AI 自主采集变成无限循环 | 重复 Task、成本失控 | budget、no-progress、dedupe、max cycle 硬门禁 |
| 模型只凭描述选工具 | 选择不稳定 | CollectorSpec information goals + branching replay + trace |
| Evidence 太大 | 上下文爆炸 | raw 落盘、Projection、分页、字段索引、output budget |
| LOW_TRUST 没有真实影响 | UI 标签与结果不一致 | Snapshot/Verifier/Analysis contract test |
| 竞品比较不公平 | 工具/模型/预算不同 | tool-parity 和 native-product 分榜 |
| Oracle 泄漏 | 虚假高准确率 | 随机无语义进程名 + leakage audit + private oracle |
| Supervisor 仍非唯一调度者 | stop 后仍创建任务 | architecture test + lease/fence + 删除旁路 |
| 为通用运维过早扩张 | 深度优势被稀释 | P3/P4 前不新增大量外部集成或恢复能力 |

## 14. 最终判断

Mini-Drop 当前最值得继续投入的不是现有根因排名，而是三项已经相互增强的资产：

1. 真实、低层、可控的 Linux 与 runtime Collector。
2. 正在成形的 canonical Evidence 与人工治理。
3. 能保存 Agent Run、Tool Call、Evidence lineage 和实验 trace 的受控运行时。

把这三项收敛起来，Mini-Drop 就有一个不同于通用 AIOps Chatbot 的清楚位置：它是运维 Agent 可以依赖的“深度补证与证据治理层”，同时自己也具备选择采集器和分析证据的 AI 循环。

下一步不应先调 Prompt 或继续加根因规则，而应依次完成：**统一 CollectorSpec -> 完整 Evidence 产品 -> 唯一 AI Collector Loop -> 退出旧 RCA/Recovery -> 公平评测**。这条路线既保留了 v6 最有价值的可靠性设计，也消除了当前项目最影响理解、开发和实验可信度的多脑结构。
