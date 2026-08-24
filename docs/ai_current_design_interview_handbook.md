# Mini-Drop AI 当前设计与面试答辩手册

> 文档定位：当前实现说明、架构设计稿、面试背诵主线与追问核实记录
>
> 核验日期：2026-08-22
>
> 代码基线：当前工作树（含 `0037_branch_reasoning_scope` 分支推理状态迁移）
>
> 面试运行假设：Pi Sidecar、模型 Provider 与 API Key 已正确配置，MINI_DROP_AGENT_RUNTIME=pi
>
> 事实优先级：当前代码与锁文件 > 本文核验结论 > 当前架构合同 > 历史设计文档

## 0. 使用方法

本文用于两个场景。

第一，作为项目的完整技术设计说明。它描述 AI 功能为什么这样设计、每个组件拥有什么权力、数据怎样结构化、执行怎样恢复、安全门禁怎样分层、最终结论怎样建立可信度。

第二，作为持续维护的面试答辩手册。后续提问遵守以下流程：

1. 如果问题已经被本文或当前代码明确回答，直接给出结论、设计理由和代码证据。
2. 如果问题无法可靠回答，先查询当前代码、配置、迁移、测试或运行报告。
3. 核实后，把新结论补入本文“问答核实记录”或对应设计章节。
4. 再回答问题，并明确区分当前实现、目标设计、兼容路径和改进建议。
5. 不根据历史文档中的数字、接口或完成声明猜测当前事实。

本文不应保存 API Key、密码、Token、Cookie、私钥或模型私有推理。

---

## 1. 一句话定义

Mini-Drop 是一个 Evidence-native 的受监督诊断 Agent：模型负责理解问题、维护假设和反证、识别证据缺口、选择下一项深度 Collector、分析 Evidence 并决定停止；Mini-Drop 的确定性控制面负责身份、租户、范围、权限、风险、预算、审批、任务执行、证据物化、字段引用验证、状态提交和失败恢复。

最短背诵版本：

> 模型提议，网关裁决，Supervisor 编译，Worker 执行，Evidence 固化，Wakeup 续跑，Verifier 定案。

该定义刻意避免三种误解：

- 它不是把监控数据一次性发给 LLM 的聊天机器人。
- 它不是让模型直接执行 Shell、perf、eBPF、SQL、SSH 或任意 MCP。
- 它不是用规则先选定根因，再让模型润色报告的 rules-first RCA。

---

## 2. 当前事实基线

### 2.1 当前已经核实的事实

| 项目 | 当前事实 |
|---|---|
| 默认 Runtime 配置 | 仓库示例默认 deterministic，面试和演示环境显式切换为 pi |
| Pi SDK | package-lock 锁定 @earendil-works/pi-coding-agent 0.84.2 |
| Worker Collector | 当前注册 13 个只读/采集型 Collector |
| Pi ToolSpec | 当前服务端目录实际有 20 个 ToolSpec，旧文档中的 12 个不是当前数字 |
| Provider | Pi Sidecar 可选择 DeepSeek 等模型；凭据留在 Sidecar 环境中 |
| 产品主线 | Evidence-native supervised diagnostic Agent |
| 业务真源 | Mini-Drop 数据库中的 Case、Task、Evidence、Revision、Event，而不是 Pi 内存 Session |
| 执行协议 | Web 到 Server 为 REST/SSE；Server 到 Worker 为 gRPC；Server 到 Pi Sidecar 为内部 HTTP |
| 原始产物 | 完整部署使用 MinIO；本机轻量模式可使用本地 Artifact |
| AI 权限 | 模型只能调用注册工具，不能直接创建 Task 或运行任意命令 |
| 最终状态 | CONFIRMED、PARTIALLY_CONFIRMED、INSUFFICIENT_EVIDENCE |

截至 2026-08-24，分支推理状态已经由 `0037_branch_reasoning_scope` 持久化：Hypothesis、Evidence Gap、Causal Graph、Evidence Dependency、Conclusion 和相关 Assistant Message 都可绑定 `branch_id`。旧 Case 数据以 `NULL` 作为兼容的 Case-wide 范围；Pi Session tree 仍只是运行时上下文，不能代替业务分支账本。当前后端验收为 `1235 passed, 6 skipped`，前端为 `104 passed`，生产构建成功。

### 2.2 面试环境假设与仓库默认值的区别

面试时要准确表达：

> 产品演示路径默认认为 Pi API Key 已配置并运行在 pi 模式；仓库示例保留 deterministic 默认，是为了没有 Provider 或 Sidecar 时 fail closed、支持离线测试并提供实验控制组。

推荐配置轮廓：

~~~env
MINI_DROP_AGENT_RUNTIME=pi
MINI_DROP_PI_RUNTIME_URL=http://127.0.0.1:8899
MINI_DROP_PI_INTERNAL_TOKEN=<random-internal-token>
MINI_DROP_PI_MODEL_PROVIDER=deepseek
MINI_DROP_PI_MODEL=deepseek-v4-flash
DEEPSEEK_API_KEY=<provider-key>
~~~

MINI_DROP_AI_API_KEY 还可作为兼容凭据来源，但需要区分两条模型调用路径：

- Pi Runtime 路径负责长期 Case 调查、工具循环和 Evidence-native Agent。
- server/app/ai_provider.py 的 OpenAI-compatible 客户端主要用于 NLP、摘要和旧兼容 RCA。

---

## 3. 设计目标与非目标

### 3.1 核心目标

1. AI 能根据问题、目标、现有 Evidence 和预算，自主选择下一项高信息增益 Collector。
2. 所有采集都复用原生 Task、TaskAttempt、Worker Agent 和 Analyzer，不建立第二套执行器。
3. Tool Result 不只留在聊天上下文，而要物化为可治理的 CaseEvidence。
4. 每条事实和结论能够追溯到 Evidence ID、Projection hash 和字段或文本 span。
5. 用户能够把 Evidence 标为低信任、排除或恢复，并确定性影响后续分析。
6. 模型可以合法拒答；证据不足不是系统故障，而是受支持的调查结果。
7. Server、Sidecar、Worker 或 Analyzer 短暂故障后，调查能够幂等恢复。
8. AI 能力可通过 Collector 选择、证据充分性、Claim 支持精度、正确停止和安全违规进行评测。

### 3.2 当前非目标

- 不允许模型直接获得通用主机命令执行能力。
- 不把任意 MCP 工具直接暴露给模型。
- 不把自动恢复作为当前 AI 主线的默认能力。
- 不把依赖图自动等同于因果图。
- 不把模型私有思维链展示或持久化。
- 不用一个未经校准的 confidence 数字冒充根因概率。
- 不因为存在 Pi Adapter 就声称所有默认路径都由 Pi 决策。

---

## 4. 总体架构

~~~text
User / React Web
        │ REST / SSE
        ▼
FastAPI Control Plane
├── Case / Turn / Plan / Hypothesis / Gap
├── RuntimePolicy / RuntimeOptions
├── AgentRuntimePort
│   ├── DeterministicAgentRuntime
│   └── PiAgentRuntimeAdapter ── internal HTTP ── Pi Sidecar
├── Tool Catalog + Tool Gateway                    │
├── CollectionSupervisor                           ▼
├── EvidenceAnalysis / InvestigationState      Pi AgentSession
├── Outbox / RuntimeWakeup                     Model Provider
└── PostgreSQL / SQLite
        │
        │ CollectionRequest → native Task
        ▼
gRPC Control Plane
        │ heartbeat / dispatch / result
        ▼
Linux Worker Agent
├── perf / py-spy / async-profiler / pprof
├── eBPF / smaps / sys_metrics / runtime snapshot
├── process / log / connection / topology discovery
└── Artifact upload
        │
        ├── MinIO / local artifact storage
        ▼
Analyzer Worker
        │ deterministic parsing / flamegraph / TopN
        ▼
CaseEvidence + EvidenceProjection
        │ transactional outbox / wakeup
        └───────────────────────────────→ Pi follow-up
~~~

### 4.1 权力边界

| 组件 | 可以做什么 | 明确不能做什么 |
|---|---|---|
| Pi / 模型 | 阅读受控上下文、维护假设、选择工具、提交提案和结论 | 直接写 DB、直接创建 Task、执行任意命令、扩大权限 |
| Pi Sidecar | 管理 AgentSession、工具循环、事件、follow-up、steer、abort | 成为 Case 真源、绕过 Tool Gateway |
| FastAPI Server | 管理 Case、Policy、Revision、Task、Evidence、结论和审计 | 把模型输出直接当事实 |
| Tool Gateway | 校验每个工具调用的实时权限和版本 | 信任 Sidecar 工具目录就是授权 |
| CollectionSupervisor | 把合法 Proposal 编译为 Request 和 Task | 根据自然语言自行猜测命令 |
| Worker Agent | 执行本机已注册 Collector | 执行未注册工具或无限参数 |
| Analyzer | 校验并确定性分析 Artifact | 代替模型做开放式根因判断 |
| Evidence Store | 保存规范证据、Projection 和 Review | 让聊天文本冒充 Evidence |

设计的核心是“双层智能”：

- 模型负责开放式的诊断决策和信息增益选择。
- 确定性代码负责安全、事实、状态和提交不变量。

---

## 5. 核心领域对象

### 5.1 Case 与 Investigation Run

Case 是用户问题、目标范围、状态、命令、计划、Evidence 和结论的业务聚合根。Pi Session 只是某一 Case 的运行时执行上下文。

Investigation Run 表示一次业务调查运行。用户纠正范围、补充关键输入或在证据不足后显式继续时，可以产生新的 Run 或 Revision；历史结论仍保留审计价值。

### 5.2 AgentTurn、ContextPacket 与 CaseContextSnapshot

AgentTurn 保存用户消息、请求模式、side effect policy、RuntimeOptions、RuntimePolicy 和 Turn 状态。

ContextPacket 是发给 Runtime 的持久化上下文包，带 canonical hash。CaseContextSnapshot 是模型每一轮观察到的结构化世界快照，包括：

- Case goal 和 target scope；
- control、scope、plan、runtime generation；
- Evidence watermark；
- CollectionProposal 和 CollectionRequest；
- EvidenceAnalysisRun；
- Hypothesis、EvidenceGap 和 CausalGraph；
- Evidence 摘要、Projection hash、质量和时间窗；
- 正在运行的 Task；
- 剩余请求数和采集时长预算；
- RuntimePolicy、RuntimeOptions 和 investigation directive。

### 5.3 Hypothesis、EvidenceGap 与 CausalGraph

Hypothesis 是模型提出的候选解释，包含支持 Evidence、反证、缺失事实和状态。

EvidenceGap 是不能闭合某个 Claim 的具体缺口，例如“缺少目标进程在事故窗口内的 CPU profile”。Gap 必须描述 required_fact，而不是含糊地写“证据不足”。

DependencyGraph 记录通信或部署关系；CausalGraph 记录经过 Evidence 支持的机制关系。系统明确阻止把仅有 TCP 依赖观察的数据升级成因果结论。

### 5.4 CollectionProposal、CollectionRequest、Task 与 TaskAttempt

CollectionProposal 是 AI 的意图：选哪个 Collector、哪个逻辑目标、什么参数、为了解决什么信息目标。

CollectionRequest 是 Supervisor 验证后的权威请求，固定：

- Collector 与 Spec 版本；
- 解析后的目标身份；
- 有效参数；
- runtime generation；
- control、scope、plan revision；
- 幂等键；
- 预算预留；
- 对应 Task。

Task 是原生执行单元，TaskAttempt 是一次具体的领取和执行尝试。一个任务可以因恢复或重试产生多个 Attempt，但只有当前有效 Attempt 可以提交权威结果。

### 5.5 Artifact、CaseEvidence 与 EvidenceProjection

Artifact 是物理产物；CaseEvidence 是 Case 中稳定的逻辑证据身份；EvidenceProjection 是提供给模型和 UI 的有界、结构化视图。

三者不可合并，因为它们解决不同问题：

| 对象 | 解决的问题 |
|---|---|
| Artifact | 原始文件在哪里、大小和 hash 是否正确 |
| CaseEvidence | 证据来自谁、针对谁、何时有效、血缘是什么 |
| EvidenceProjection | 模型能在预算内看到哪些确定性字段 |

### 5.6 EvidenceAnalysisRun 与 ClaimEvidenceBinding

EvidenceAnalysisRun 固定分析输入，包括 Evidence ID、Review revision/state、Projection ID/hash、分析模式、模型配置和 Prompt 版本。

ClaimEvidenceBinding 把最终自然语言 Claim 绑定到具体 Evidence 和字段：

~~~json
{
  "claim": "目标进程 CPU 使用率大于 90%",
  "evidence_id": "ev-...",
  "projection_hash": "sha256...",
  "field_path": "signals.cpu_percent",
  "predicate": {"operator": "gte", "value": 90}
}
~~~

### 5.7 ConclusionRevision

最终结论不是覆盖写的一段报告，而是带状态、Claim、Evidence binding、限制、因果图引用和建议的版本化记录。

支持的状态：

- CONFIRMED：证据和因果结构满足确认门槛。
- PARTIALLY_CONFIRMED：部分事实成立，但仍有阻断 Gap、未区分替代假设或未验证因果边。
- INSUFFICIENT_EVIDENCE：无法在当前范围和预算内形成受支持结论。

---

## 6. 结构化数据处理

### 6.1 总体转换链

~~~text
Collector raw output
→ Artifact metadata + raw object
→ deterministic parser
→ canonical CaseEvidence
→ bounded EvidenceProjection
→ AI Fact with citation
→ deterministic citation/predicate verifier
→ ClaimEvidenceBinding
→ ConclusionRevision
~~~

### 6.2 Collector 输出为什么不能直接塞进 Prompt

Collector 数据可能包含数 MB 的日志、数千个进程、火焰图或拓扑边。直接放入 Prompt 会造成：

- 成本和延迟不可控；
- 不同 Collector 的字段语义不一致；
- 截断可能破坏 JSON；
- 模型难以稳定引用具体事实；
- 原始日志中的不可信文本可能产生 Prompt injection；
- 无法证明结论引用的是哪一版数据。

因此系统先做确定性 Projection，再让模型按需展开。

### 6.3 Projection 的统一结构

通用 Projection 内容包括：

~~~json
{
  "artifact_type": "sys_metrics",
  "summary": "...",
  "signals": {},
  "top_items": [],
  "samples": [],
  "log_events": [],
  "errors": [],
  "coverage": {},
  "topology": {},
  "window": {},
  "target": {},
  "quality": "COMPLETE",
  "interpretation_hints": [],
  "raw_ref": {}
}
~~~

不同 Artifact 会选择不同 projection_kind，例如 TIMESERIES、TOP_ITEMS、LOG_EVENTS、FLAMEGRAPH_HOTSPOTS、DEPENDENCY_GRAPH、TOPOLOGY_GRAPH 或 RAW_PREVIEW。

### 6.4 大结果处理

Projection 使用确定性预算：

1. 优先保留身份、hash、目标、时间窗和关键 signals。
2. 对 top_items、samples、logs、errors 设置固定数量上限。
3. 超过单 Projection 最大字节数时，从最大的可选列表尾部裁剪。
4. 设置 truncated 和 source_bytes/projected_bytes。
5. 保留 raw_locator，允许 UI 下载原始 Artifact。

Pi Prompt 还有第二层字符预算。Sidecar 会压缩 Snapshot，并记录 omitted_fields、omitted_evidence_count 和少量 omitted Evidence refs，避免静默丢失上下文。

### 6.5 SourceGateway 与外部 MCP 数据

外部数据不直接变成 Tool Result 真相，而先经过：

~~~text
SourceDefinition
→ principal / tenant / grant authorization
→ short-lived Capability Token
→ connector call
→ redaction + result budget
→ content hash + projection hash
→ EvidenceEnvelope
→ CaseEvidence materialization
~~~

EvidenceEnvelope 包含 source、principal、tenant、resource scope、operation、query fingerprint、valid time、data class、hash、redaction 统计和 policy decision。

MCP 在这里是外部 Source 的适配协议，不是权限或业务状态的真源。

### 6.6 Citation 验证

EvidenceAnalysis 完成时逐条检查：

- Evidence 必须属于该 Analysis 的 pinned inputs；
- Projection hash 必须与创建 Analysis 时固定的 hash 相同；
- field_path 格式合法；
- 字段必须真实存在；
- 文本 quote 的 start/end span 必须与实际内容一致；
- HIGH certainty 不能完全由 LOW_TRUST Evidence 单独支持。

最终 Conclusion 还会验证：

- Evidence 是 ACTIVE；
- Evidence 不是当前 scope revision 的 stale 数据；
- Projection version/hash 匹配；
- target_ref、resource_incarnation、event_window 匹配；
- predicate 对持久化字段求值成功。

---

## 7. Pi Agent Runtime 设计

### 7.1 为什么采用 Pi

项目需要的是长周期工具型调查，而不是一次 completion。Pi 提供：

- Agent loop；
- Session 管理；
- 自定义 Tool Call；
- 流式 lifecycle/tool 事件；
- steer、follow-up、abort；
- reasoning effort 和 model 选择；
- 上下文和消息管理。

如果直接调用 Chat Completions，需要项目自行实现这些通用 Runtime 能力。采用 Pi 可以把工程重点留在 Mini-Drop 的领域优势：Collector、Evidence、Policy、Supervisor 和恢复语义。

### 7.2 为什么使用 Sidecar

Python Server 不直接嵌入 Pi，而通过 Node.js Sidecar，理由包括：

- Pi SDK 原生运行时在 Node.js；
- Provider 凭据可以隔离在 Sidecar 进程环境；
- Server 不暴露原始 Pi RPC，只暴露 Mini-Drop 内部协议；
- Sidecar 可以独立重启和升级；
- AgentRuntimePort 保留替换 Runtime 的能力。

### 7.3 AgentRuntimePort

统一端口包含：

- start_or_resume；
- submit_turn；
- steer；
- follow_up；
- abort；
- get_state。

因此替换 Pi 不需要修改 Case API、Evidence Store、CollectionSupervisor 或 Worker。

### 7.4 Sidecar 安全配置

创建 Pi Session 时：

- 禁用内置 shell/file 工具；
- 只注册 Mini-Drop custom tools；
- resourceLoader 关闭；
- 工具集合按本轮 RuntimePolicy 动态激活；
- system prompt 约束 Evidence-native 行为；
- thinking 事件不持久化；
- final/tool/lifecycle 事件才进入传输层。

### 7.5 Session 与业务真源

Sidecar 中一条活跃 Case 映射一个内存 AgentSession，但 Session 不是业务真源。恢复时从 Server 的 CaseContextSnapshot 重建。会话历史可为了低带宽评测按 Turn 重置，而 Case/Evidence 历史仍在数据库中。

---

## 8. 完整执行链条

### 8.1 用户 Turn 接入

1. Web 调用 Case Agent Turn API。
2. Server 校验 operator 角色、Tenant 和 Case。
3. 自然语言被路由为 ANSWER_ONLY、ATTACH_EVIDENCE 或 INVESTIGATE 等 disposition。
4. disposition 决定 side_effect_policy 上限。
5. 用户提供的 RuntimePolicy 只能进一步收紧权限。
6. Server 保存用户消息和 Runtime Turn。
7. Server 构建 ContextPacket 和 CaseContextSnapshot。
8. Pi Adapter start_or_resume，再 submit_turn。
9. HTTP Accepted 只表示 Runtime 接受任务，不表示模型已经完成回答。

### 8.2 模型观察和规划

Pi 首先阅读 Case Snapshot、Evidence inventory 和 Gap。随后可以：

- 展开某条 Evidence Projection；
- 比较多条 Evidence；
- 查询知识库；
- 更新 Hypothesis 和 Gap；
- 提议 Plan；
- 选择一个 Collector；
- 在证据充足时提交结构化结论。

模型选择下一步时应关注信息增益：这项采集能否区分当前主要替代假设，而不是按固定 Collector 顺序执行。

### 8.3 CollectionProposal 到 Task

~~~text
Pi propose_collection
→ internal Tool Gateway
→ Tool fence
→ CollectionSupervisor.propose_and_dispatch
→ Proposal persisted
→ schema/scope/capability/risk/budget validation
→ CollectionRequest persisted
→ native Task created
→ Proposal marked ACCEPTED
~~~

正确提交点是 Request 和 Task 已持久化之后。不能先把 Proposal 标为 ACCEPTED，再尝试创建 Task。

如果 policy 是 PROPOSE_ONLY，Proposal 以 awaiting_execution_authority 状态保留，不创建 Task。批准时重放原 Proposal 和固定参数，而不是让模型重新生成一个“相似”请求。

### 8.4 Worker 执行

Worker 启动后：

1. 注册 Agent 元数据和真实 capability。
2. 周期性 gRPC 心跳。
3. 空闲时领取唯一 TaskAttempt。
4. 根据 collector_type 选择本地注册实现。
5. 校验 task_id、PID、进程 incarnation、duration、sample rate。
6. 在隔离子进程中执行 Collector。
7. 上传 Artifact 或保存在受控本地目录。
8. 先写本地 ResultSpool，再调用 NotifyResult。
9. Server ACK 丢失时可重放，终态任务会幂等确认。

### 8.5 Analyzer 执行

采集成功后创建 AnalysisJob。Analyzer Worker 通过数据库租约领取任务：

- 校验 Artifact 可用性、登记大小和 SHA-256；
- 运行确定性分析；
- 定期续租；
- 提交前再次验证租约；
- 失去租约的旧 Worker 不得提交；
- 生成的分析产物再次登记为 Artifact。

### 8.6 Evidence 物化和 Wakeup

Task Artifact 被物化为 CaseEvidence 和 EvidenceProjection。提交同时产生 Domain Outbox 事件。

Outbox Relay 把事件归并为 RuntimeWakeup。Wakeup 不是简单字符串通知，而会：

- 固定 evidence watermark；
- 创建新的 ContextPacket/Snapshot；
- 创建 AgentCycle 和 ModelRequest；
- 继承上一 Turn 的 RuntimePolicy、RuntimeOptions 和策略；
- 调用 Runtime follow-up；
- 成功后消费 Wakeup，失败则重新排队。

如果采集失败或没有产生 Evidence，Wakeup 会明确告诉模型“没有新 Evidence”，要求记录 limitation 或选择替代 Collector，禁止假设采集成功。

### 8.7 Evidence Analysis

单条、多条或对比分析会先创建 EvidenceAnalysisRun。相同输入 fingerprint 可以复用，避免重复模型调用。

输入发生以下任何变化，旧 Analysis 都会变为 STALE_INPUT：

- Evidence Review 状态变化；
- Review revision 增加；
- Projection hash 变化；
- Evidence 消失。

### 8.8 最终提交

Pi 必须调用 finish_investigation，而不能用普通文本冒充结构化终态。Server 验证 Claim、Gap、CausalGraph 和替代假设，决定最终状态，并原子写入：

- ConclusionRevision；
- ClaimEvidenceBinding；
- AssistantMessage；
- Case Event；
- Turn 完成状态。

---

## 9. 状态机

### 9.1 Task

~~~text
PENDING → RUNNING → UPLOADING → ANALYZING → DONE
    └──────────────→ FAILED / CANCELLED
~~~

每次迁移必须有 reason，并记录 actor。终态不能继续迁移。

### 9.2 Collection

~~~text
Proposal PROPOSED
├── REJECTED
├── PROPOSED awaiting authority
└── ACCEPTED
      ↓
CollectionRequest ACCEPTED → DISPATCHED → RUNNING
      ├── EVIDENCE_READY
      └── FAILED / CANCELLED / FENCED
~~~

### 9.3 EvidenceAnalysisRun

~~~text
QUEUED → RUNNING → COMPLETED
   ├──────────────→ FAILED / FENCED
   └──────────────→ STALE_INPUT
~~~

COMPLETED 与 CURRENT/STALE_INPUT 是两个正交维度：结果可能曾经完成，但由于 Review 或 Projection 变化，不再适用于当前判断。

### 9.4 Runtime Turn 与 Cycle

Turn 的 accepted 和 completed 必须分开。一个 Pi Turn 可能经历工具调用、等待 Evidence、Wakeup 和新的 Cycle。HTTP 200 或 AcceptedTurn 不是调查完成信号。

---

## 10. 门禁设计

门禁采用纵深防御，不依赖 Prompt 自律。

### 10.1 API 与身份门禁

- API role；
- Tenant 隔离；
- Case ownership；
- Pydantic 严格 schema；
- 引用资源必须能附加到当前 Case；
- Answer-only 与有副作用 intent 冲突时拒绝。

### 10.2 RuntimePolicy 门禁

side_effect_policy：

| 模式 | 能力 |
|---|---|
| READ_ONLY | 只能读取和分析，不能提议采集或写调查状态 |
| PROPOSE_ONLY | 可以形成可审查 Proposal，不自动创建 Task |
| AUTO_READ_LOW | 可以自动执行代码允许的低风险采集 |

其他约束：

- allow_arbitrary_command 永远为 false；
- allowed_risk_levels 只能是 R0/R1 子集；
- enabled_tools 只能从注册表选择；
- auto_approve 仅实验模式；
- R3 审批不能被关闭；
- 请求数和时长预算不能超过代码上限。

### 10.3 Tool Gateway 门禁

每次 Tool Call 都重新检查：

1. Tool 是否注册；
2. Tool 是否属于本轮 effective_tools；
3. execution_mode 是否允许写入；
4. Runtime generation 是否当前；
5. Case 是否暂停或终止；
6. Side effect policy 是否满足。

工具目录只是发现元数据，不是授权凭证。即使 Sidecar 缓存了旧目录，Server 仍以当前本地 ToolSpec 为准。

### 10.4 CollectionSupervisor 门禁

Supervisor 校验：

- Case 存在；
- Collector 已注册、启用且 Spec 版本未漂移；
- information_goal 是 Collector 声明的目标；
- 参数通过 schema；
- target 能解析且在 scope 内；
- Agent 在线并拥有 capability；
- risk 在允许集合中；
- input Evidence 存在且未 EXCLUDED；
- control/scope revision 当前；
- 请求数量和累计预留时长未超限；
- 幂等键没有重复产生副作用。

### 10.5 Plan 门禁

PlanDriver 只调度当前 Plan Revision 中可调度、依赖满足的 READ_LOW Step。高风险步骤进入审批。重复 Collector + 目标可以标记 SKIPPED_REUSED。

集群步骤不能让模型任意枚举节点，而是通过 MembershipSnapshot、TargetResolver 和 fanout budget 展开。

### 10.6 Worker 本地门禁

- Collector 实现必须本地注册；
- Worker 上报真实 capability；
- 非法 task_id 拒绝；
- PID 必须大于 0；
- 禁止采集 Agent 自己；
- duration 和 rate 本地 clamp；
- process incarnation 不匹配则拒绝；
- mutation 型 action 不属于 AI Collector Catalog。

### 10.7 Artifact 和 Analyzer 门禁

- attempt 与 Task 必须匹配；
- 终态重放幂等；
- Artifact 数量和字段长度有限制；
- 输入大小、对象存在性和 hash 校验；
- Analyzer 租约提交前复验；
- 没有权威 Artifact 就不能伪造 Evidence。

### 10.8 Evidence 和 Conclusion 门禁

- Evidence 必须属于当前 Case；
- EXCLUDED Evidence 不能支持结论；
- LOW_TRUST 不能单独支持 HIGH certainty；
- Projection hash/version 必须匹配；
- field_path 和 predicate 必须真实成立；
- Evidence watermark 过期则拒绝；
- dependency-only 不能建立 causal graph；
- blocker Gap、未验证因果边或未区分替代假设会降级 CONFIRMED。

---

## 11. 并发、版本与 Fencing

### 11.1 为什么有多个 Revision

| 字段 | 含义 | 防止的竞态 |
|---|---|---|
| runtime_generation | 当前 Sidecar Session 代数 | 旧 Session 迟到事件或工具调用 |
| control_revision | 暂停、停止和控制命令版本 | 旧控制状态下继续写入 |
| scope_revision | 目标、拓扑和时间范围版本 | 改目标后旧采集污染新调查 |
| plan_revision | 调查计划版本 | 旧 Step 在新计划后继续调度 |
| evidence_watermark | 当前 Evidence 集合进度 | 基于旧证据提交因果图或结论 |
| review_revision | 人工证据治理版本 | 降信任/排除后旧分析仍有效 |
| projection_hash | 模型看到的数据内容版本 | 引用被替换或静默变化 |

### 11.2 Generation fencing

Sidecar Session 丢失或 RuntimeOptions 改变时会重建 Session。新 generation 生效后，旧 generation 的新事件被 Server 以 409 GENERATION_FENCED 拒绝。

Runtime Event 使用 generation、event_seq 和 idempotency_key 去重。Sidecar 把未确认事件写入 JSONL EventSpool，Server ACK 后删除；重启后可以 replay。

### 11.3 幂等层次

系统不是只依赖一个幂等键：

- Turn 有稳定 idempotency；
- Tool Event 有 generation + seq + key；
- CollectionRequest 有 Case/Tenant scoped idempotency；
- Plan Step 使用稳定 step_id；
- TaskAttempt 区分重试执行；
- Evidence ID 根据 Artifact lineage 确定性生成；
- EvidenceAnalysis 使用 input_fingerprint；
- AssistantMessage 可按 Turn + 内容产生稳定 ID；
- Outbox 和 Wakeup 各自有 dedupe key。

---

## 12. 置信与可信度设计

### 12.1 核心原则

系统不把模型自报的 confidence=0.9 当成根因概率。在线 Pi 主线把可信度拆成四个维度：

1. Evidence 质量与适用性；
2. Claim 的字段级支持；
3. 因果结构与替代假设是否闭合；
4. 最终结论状态。

这是一种门槛式、可验证的可信设计，而不是未经校准的加权总分。

### 12.2 Evidence 质量

关键属性：

- completeness；
- quality；
- freshness；
- trust_level；
- Review state；
- target 和 resource incarnation；
- event time window；
- source lineage；
- projection truncated 状态；
- 是否适用于当前 scope revision；
- 是否与独立来源冲突。

Evidence Review 支持 ACTIVE、LOW_TRUST、EXCLUDED/RESTORED 等治理语义。物理删除不是普通调查操作，因为会破坏审计和历史引用。

### 12.3 Fact certainty

模型可以声明 HIGH、MEDIUM、LOW，但这只是分析输出的一部分，不是系统授权。

硬规则：

- Fact 必须有 Citation；
- Citation 必须命中 pinned Projection；
- 字段或 span 必须存在；
- HIGH 不能仅由 LOW_TRUST Evidence 支持；
- 发现冲突要写入 conflicts/limitations，而不是平均掉。

### 12.4 因果确认

通信关系、同时发生和因果机制必须分开：

- DependencyGraph：谁连接谁、谁部署在哪、观测覆盖怎样。
- CausalGraph：哪个机制导致或放大哪个症状。

只有 Dependency Evidence 时不能产生权威根因。Causal node/edge 的 Evidence refs 必须是 ACTIVE，edge 才能被标为 SUPPORTED。

### 12.5 最终状态降级

模型请求 CONFIRMED 时，Verifier 会检查：

- 是否存在 blocker Gap；
- 是否缺少必需因果边；
- 是否有多个主要替代假设未区分；
- 是否存在 verifier_role 为 PRIMARY_CAUSE 或 PRIMARY_ROOT_CAUSE 的节点；
- 所需 edge 是否为 OBSERVED/SUPPORTED。

任一条件不满足，状态降为 PARTIALLY_CONFIRMED。

如果没有 Evidence，只有在状态为 INSUFFICIENT_EVIDENCE 且提供明确 abstention_reason 时才允许提交。

### 12.6 旧兼容链的数值权重

旧 evidence_guard 和 rules-first 兼容链存在数值 weight/confidence：

- DONE observation 基础权重较高；
- 过旧数据降权；
- 不可信的失败采集降权；
- 缺 Evidence refs 或没有事实内容降权；
- 重复 observation 去重；
- 高质量独立来源冲突降权；
- source family 用于计算独立性。

这些数值用于旧兼容分析、证据整理和离线评测，不能说成在线 Pi 主线的“根因概率公式”。

### 12.7 Evidence Contract

EvidenceContract 描述一种机制需要哪些 required facts、哪些 Collector 能补齐、最少独立来源族和事故窗口。例如 memory_leak 需要 RSS slope、趋势和内存使用率，并要求多窗口、多来源。

当前要诚实说明：Contract 在 Planner、兼容分析和评测中已有使用，但并非每种机制的最少来源/窗口规则都已经统一接入 Pi finish 的最终硬门禁。这是后续可增强点。

---

## 13. Context、Prompt 与模型行为

### 13.1 Context 组成

模型看到的是 Snapshot，而不是数据库全量数据。默认包含有限数量的：

- Evidence 摘要和 hash；
- Hypothesis 和 Gap；
- Collection 状态；
- EvidenceAnalysis 摘要；
- 因果图和依赖图；
- 预算和 Policy；
- 用户当前消息。

需要详情时通过 get_evidence_projection 等工具展开。

### 13.2 System Prompt 的职责

Prompt 指导模型：

- 先读 Case 和 Evidence；
- Knowledge 只作背景，不是当前 Evidence；
- 缺证据时记录 Gap；
- 选择一个高信息增益 Collector；
- Collector 接受后停止本轮并等待 Wakeup；
- 不轮询采集状态；
- 不把 dependency 当 causality；
- 最终必须通过 finish_investigation；
- 所有 Claim 要引用 Evidence ID、Projection hash 和字段/span。

Prompt 只是行为引导。真正安全由 Tool Gateway 和 Verifier 保证。

### 13.3 RuntimeOptions

RuntimeOptions 与权限分离，包含：

- model；
- reasoning_effort；
- prompt_variant；
- strategy metadata；
- fresh_session；
- temperature、max_tokens、seed 等实验字段。

当前 Pi 0.84.2 真正应用 model、reasoning effort、prompt variant 和 fresh_session；temperature、max_tokens、seed 在此集成中主要作为实验元数据，不应声称已经影响 SDK 请求。

### 13.4 Token 消耗与上下文分层

系统不把长期 Case 的原始数据和完整历史对话反复发送给 Provider。当前采用“原始数据留存、确定性投影、摘要索引、按需展开、Prompt 再限界”的分层：

1. Artifact/Object Store 保存原始 perf、日志、指标和拓扑产物，原始内容不进入每个 Prompt。
2. EvidenceProjection 确定性提取 signals、Top 项、样本、异常、时间窗和 raw_ref。默认最多保留 20 个样本、10 个 Top 项、12 条日志事件、10 个错误、40 个拓扑节点和 80 条边；大于 8 MiB 的原始 Artifact 不整体读入投影器，单 Projection 最大约 512 KiB。
3. Server 构建 Case Snapshot 时默认只放有限摘要：最多 20 条 Evidence 摘要、30 个假设、60 条假设边、30 个 Gap、最近 20 个 Proposal/Request、最近 10 个 Analysis 摘要。Knowledge 正文不常驻 Prompt，而由 search_knowledge 按需查询。
4. Python Context optimizer 默认 24,000 字符，按 section 分配预算；会脱敏、去重、限制深度和字符串长度。指标样本压缩为 count/min/max/avg/last/slope，加 first/last；日志按关注词、错误严重度和新近程度排序。
5. Pi Sidecar 再以 MINI_DROP_PI_CONTEXT_MAX_CHARS 做第二次确定性裁剪，默认同为 24,000 字符。Evidence ID、projection hash 和 omission ledger 优先保留；被省略的 Evidence 数量和少量引用会显式记录，而不是静默丢失。
6. 模型若需要细节，通过 list_case_evidence、get_evidence_projection、compare_evidence 等只读工具按需展开，不在每一轮重发全量。
7. fresh_session 可不重放 Pi 对话历史，只从持久化 Case Snapshot 重建当前上下文，适合低带宽评测和长期 Case。模型私有推理不持久化，只保留决策摘要、工具序列和审计事件。

当前真实 Token 台账：

- 真实未知拓扑 Pi 调查共 19 个 ModelAttempt，累计 input 28,834、output 29,583、cache-read 610,816 Token，累计模型等待约 195 秒；报告中的 cost 为 0，表示该次 Provider/SDK 没有返回可用成本值，不能解释成免费。
- 历史基线：8 个真实 GitHub PR 单轮评测共 76 个 ModelAttempt，累计 input 101,143、output 8,059、cache-read 111,744 Token，累计模型等待约 295 秒，记录成本约 0.016729。该数据属于旧的 8-PR 只读 Case 总计，不能外推长期自主调查成本；新版 9×3 结果以 `docs/evidence-native-live-eval-2026-08-25.md` 和 `live-v2` 报告为准。

当前限制必须说明：24,000 是字符预算，不是 tokenizer 精确 Token 硬预算；RuntimeOptions.max_tokens 在当前 Pi 集成中只是实验元数据。系统已记录每次 ModelAttempt 的 Token、缓存、时延和成本，但还没有在在线 Gateway 中实现“累计 Token 达阈值就强制停止”的确定性 Case 级硬门禁。

### 13.5 长期累积问题与偶发问题的采集设计

长期问题与偶发问题使用不同的观测策略，但进入同一 Evidence 链。

长期累积问题（如内存泄漏、缓慢退化）应采用低开销多窗口趋势：

- 常态用 sys_metrics 等低风险 Collector 获取 CPU、RSS、I/O、网络和进程基线；
- 将大量时序样本确定性压缩为分位数、斜率、首尾值和异常分数，而不是把每个点给模型；
- 只有出现持续斜率、相对历史基线偏移或 blocker Gap 时，才升级到 memory_smaps、runtime_snapshot、perf/pyspy 等更深采集；
- 比较故障前、故障中、恢复后的窗口，要求 target incarnation 和时间范围一致。

偶发问题（如瞬时 CPU hotspot、短暂锁竞争）需要在事件发生前已有低频窗口，否则事后无法恢复已经消失的调用栈。当前提供：

- continuous_perf：Linux 上把一次有界 Task 切为低频 perf 窗口，默认信息目标是“热点调用栈的时间变化/间歇性 CPU 异常”；CollectorSpec 默认 60 秒、最大 600 秒、R2，需要审批。
- DiagnosticTargetSession：保存长期目标范围、基线和 Signal policy。
- ProfileWindow 索引：把 continuous_perf 的窗口与稳定 target、PID、时间范围、Artifact hash 关联；详细窗口默认保留 24 小时。
- Signal 触发：外部告警通过 Target Signal API 写入，high/critical 默认自动建立 Case；同一目标默认 900 秒 cooldown 去重。
- 时间截取：告警默认关联前 300 秒、后 60 秒内尚未过期的 ProfileWindow，把关联 Task 作为 Case 初始数据。

但当前不是完整的常驻可观测平台：continuous_perf 仍是受 Task duration 限制的有界采集，不是永久 daemon/ring buffer；Signal severity 由外部监控输入，service_baseline 中的分位基线/异常检测工具尚未统一接成当前 Pi 主线的自动 Detector；告警建 Case 后也不会由该 API 自动提交 Pi Turn。也就是说，“窗口留存和 Case 触发”已实现，“永久低成本观测 -> 内置异常检测 -> 自动启动 Pi -> 自动归因”还未完全闭环。

### 13.6 谁决定采集范围

不是让 AI 自由决定范围，而是“AI 选择信息目标和建议参数，系统决定执行边界”：

- 人或 TargetSession 固定 Case 的 service/host/agent/PID/time range；未知拓扑扩展只能引用已验证的 discovery Evidence 和 membership snapshot。
- AI 从闭合 Collector Catalog 中选择 collector_id、information_goal、target_selector、duration、sample_rate、time_window 和输入 Evidence refs。
- CollectionSupervisor 用 CollectorSpec 重验 schema、目标是否在 scope、Agent capability、风险、时长、采样率、结果大小、预算和幂等；有效参数会被默认值和上下限 clamp。
- 默认在线策略最多 8 个 CollectionRequest、累计最多 240 秒；R2/R3 需审批，任意命令永远禁止。continuous_perf 虽然规格允许最大 600 秒，但默认 RuntimePolicy 的 240 秒总预算会进一步收紧，除非代码所有者调整受控策略。
- Worker 执行前再次检查 PID、进程 incarnation 和本地能力。

因此 AI 有“调查选择权”，没有“权限定义权”。它可以决定下一条最有信息增益的采集建议，但不能扩大目标、风险、时长或数据访问范围。

### 13.7 面向生产的推荐增强

对于真正长期运行的系统，推荐在现有接口上补齐三层，而不是让 LLM 常驻读取流数据：

1. Always-on Signal Plane：Prometheus/Alertmanager、OpenTelemetry Collector、日志引擎或 eBPF/profiling agent 持续产生低成本聚合；使用多分辨率保留（秒级 ring buffer、分钟级 rollup、小时/天级分位和 sketch）。
2. Deterministic Trigger Plane：EWMA/CUSUM、robust z-score/MAD、变化点检测、SLO burn-rate 和复合告警负责发现慢性漂移或偶发尖峰；LLM 不处于毫秒级检测热路径。
3. Evidence Capture Plane：触发时原子冻结 pre-trigger/post-trigger 窗口、目标 incarnation、配置版本和拓扑快照；只把异常区间、对照基线、Top-K 差异与缺口清单投影给 AI，原始数据仍可按 Evidence ID 下钻。

应增加 Case 级硬预算：累计 input/output Token、Provider 成本、ModelAttempt 数、Wall time 和 Wakeup 次数。接近预算时先压缩 CurrentUnderstanding 和历史 Evidence 索引；达到上限后强制 finish 为 PARTIALLY_CONFIRMED 或 INSUFFICIENT_EVIDENCE，而不是继续循环。

---

## 14. 故障恢复

### 14.1 Provider 或 Sidecar 不可用

- Pi 配置缺失时 fail closed；
- 普通采集底座仍可工作；
- Case Turn 记录 Runtime 拒绝/降级原因；
- 不因为模型失败而扩大权限；
- Wakeup 交付失败会重新排队。

### 14.2 Sidecar 重启

- Server binding 仍保存；
- Adapter 发现 Sidecar Session NOT_STARTED；
- 轮换 generation；
- 从最新 Snapshot 重建 Session；
- replay EventSpool 中未 ACK 事件；
- 旧 generation 的新事件被 fence。

### 14.3 Worker 断线

- Agent 心跳更新在线状态；
- TaskAttempt 和超时机制处理失联；
- ResultSpool 保证结果可以在 Server 恢复后补报；
- terminal replay 不重复写 Artifact 或状态事件。

### 14.4 Analyzer 崩溃

- AnalysisJob 使用租约；
- 过期租约可被其他 Worker 重新领取；
- 提交前必须再次续租；
- 失去租约的旧 Worker 无权覆盖结果。

### 14.5 ACK 丢失

- Tool/Runtime Event、Task result 和 Outbox 都有独立幂等身份；
- 重放返回已有结果，不重复副作用；
- Accepted 与完成状态分离，避免 HTTP 200 冒充最终成功。

---

## 15. 安全设计

### 15.1 凭据隔离

- Provider API Key 位于 Sidecar 环境；
- Server 与 Sidecar 使用独立内部 Token；
- Tool payload、ContextPacket、Event 和报告不写 Provider Key；
- 外部 Source Token 通过环境或 Credential/Grant 机制引用，不暴露给模型。

### 15.2 最小权限

- Pi 无 builtin shell/file tools；
- Worker capability 按真实环境上报；
- Collector 与 mutation Action 分注册表；
- AI 当前仅允许 R0/R1 采集；
- R2/R3 需要审批或拒绝；
- SourceGateway 使用 Grant 和一次性 Capability Token。

### 15.3 不可信输入

日志、MCP 返回、文档和 Trace 属性均视为数据，先投影、脱敏、限制大小，再进入模型。外部 Tool 描述不能修改 Mini-Drop Policy。

### 15.4 审计而非思维链

系统保存：

- 模型 provider、model、Prompt/version；
- token、cost、latency；
- response hash；
- 工具序列；
- Evidence refs；
- 决策摘要；
- Runtime Event；
- Proposal、Request、Task 和结论状态。

系统不保存私有 thinking 或原始 Chain of Thought。

---

## 16. 协议与通信

| 链路 | 协议 | 设计理由 |
|---|---|---|
| Web ↔ Server | REST/JSON | 浏览器友好、易调试 |
| Server → Web | SSE | 展示 Task、Evidence 和 Agent 时间线 |
| Server ↔ Pi Sidecar | 内部 HTTP/JSON | 隔离 Node Runtime，接口简单可替换 |
| Sidecar ↔ Provider | Provider API | 模型推理 |
| Sidecar ↔ Tool Gateway | 内部 HTTP + Token | Tool Call 回到业务权威 |
| Server ↔ Worker | gRPC/Protobuf | 强类型、二进制、心跳和任务下发 |
| Server/Analyzer ↔ Storage | MinIO API | 原始 Artifact 存储 |
| Server ↔ External Sources | Source connector / MCP | 受控外部 Evidence 接入 |

---

## 17. 可观测性与评测

### 17.1 运行审计指标

- ModelAttempt 数；
- input/output/cache token；
- cost；
- latency；
- Tool Call 数；
- Collection 数和累计时长；
- Runtime generation 和 event seq；
- Evidence watermark；
- Wakeup 和 Cycle；
- Proposal 接受、拒绝和审批原因。

### 17.2 AI 能力指标

项目不以“报告看起来合理”为主要指标，而评测：

- Evidence sufficiency success；
- weighted information-goal recall；
- Claim support precision；
- correct stop / abstain；
- false certainty；
- acceptable next action Top-1 / Top-K；
- wasteful collector ratio；
- cost 和预算遵守；
- unauthorized execution；
- approval bypass；
- scope violation；
- cleanup failure。

false certainty 的典型定义：模型输出 HIGH，但 Claim 不是全部可验证、Evidence 目标未满足或完整性校验失败。

### 17.3 为什么需要确定性 Evaluator

核心功能不能只由另一个 LLM Judge 评分。Evaluator 应读取结构化 Conclusion、Claim binding、Projection 和 Oracle predicate，确定性判断字段是否满足条件。

### 17.4 假设驱动是否有效：当前证据边界

假设驱动不是产品目的，而是一种受约束的调查控制结构。它只有在以下净收益为正时才有效：

- 提高 Evidence sufficiency、information-goal recall、Claim support precision 和正确停止/拒答率；
- 降低 false certainty、无效 Collector 比例、重复采集和越权行为；
- 增加的 Token、时延、工具调用、状态维护成本仍在预算内。

因此不能用“模型写出了若干假设”证明它有效，也不能只用最终答案准确率判断。必须把正确性、安全性和调查成本同时比较。若假设没有对应可观察事实、反证条件和区分性 Collector，它只是叙事负担；若它能把开放问题变成可验证 Gap，并减少无差别采集，才是有效状态。

当前代码对假设负担做了以下约束：

1. 假设是模型提案，不是事实；只允许通过 Tool Gateway 写入受版本控制的 Case 状态。
2. 每条假设可分别保存 supporting/contradicting Evidence 和 alternatives；Evidence ID 必须是 Case 中 ACTIVE Evidence。
3. 图规模有上限：最多 30 个假设、60 条假设边。生产策略目录只公开 hybrid，其他策略仅在 experiment mode 开放。
4. 下一项采集必须声明 information_goal，并由 CollectorSpec、范围、风险和预算门禁验证。
5. 多个主要替代假设仍未区分时，即使模型请求 CONFIRMED，Verifier 也会降级为 PARTIALLY_CONFIRMED。
6. 假设状态本身不能直接生成权威根因；最终 Claim 仍必须通过 Evidence Projection 的字段级谓词验证。

当前已有两类真实模型证据，但都不是“假设策略优越性”的正式消融结论：

- 更新结果：9 个真实 GitHub PR、每个 3 轮，共 27 轮真实 `deepseek-v4-flash` Provider completion。第二轮结构门禁 `27/27 PASS`，完整 canonical Evidence ID/hash 绑定 `27/27`；按机制归因、Evidence 引用、反证/不确定性、影响边界四项标准做非双盲人工粗评约 9.2–9.6/10（约 9.3/10）。它证明在给定 PR Projection 下的机制归因、证据绑定、校准拒答和范围控制能力较强且重复较稳定；不是双盲 holdout，也不能证明通用 RCA 准确率、动态 telemetry RCA 或生产自治。
- 1 条真实 Pi 未知拓扑调查得到 PASS：从一个 client PID 发现 server，形成 2 个节点、1 条依赖边，双端复核，最终因覆盖不足提交 PARTIALLY_CONFIRMED/abstention，没有把通信关系冒充因果根因。它证明异步采集、Evidence 绑定、边界控制和拒绝错误闭环能工作；它不构成策略间统计对比。

仓库已经具备两套对比基础设施，但当前没有可用于宣称优势的正式多臂报告：

- Agent strategy matrix 定义 rule_tree、hypothesis_first、evidence_first、causal_graph、exploratory、hybrid 六种条件。离线 runner 明确标记 strategy_applied=false，只能作为 rules control，禁止填报策略准确率。live runner 能记录通过率、根因文本匹配、服务端验证引用、工具数、Token、成本、时延和重复一致性；现有配置只有 1 次 repetition，且仓库没有已核验的完整多策略结果。
- Collector Agent v1 定义 B0-B5、M1-M2、H1、S1 对照臂，指标包含 Evidence sufficiency、weighted information-goal recall、Claim precision、correct stop/abstain、false certainty、无效采集比例和安全硬门禁。正式评分要求至少 30 个独立场景；当前 seed suite 小于正式门槛，仓库没有达到该门槛的持久化多臂报告。

所以当前最准确的面试表述是：

> 我们已经验证了假设/Evidence/Verifier 链路在少量真实场景中能够闭环和克制错误确认，也建立了可复现的多策略与多产品对照框架；但尚未完成足够样本、重复运行和同预算控制下的消融实验，不能声称假设驱动已经被统计证明优于其他链路。

正式消融应固定模型、Prompt 版本、工具目录、Provider、Case、预算和风险策略，只改变调查策略。每个场景与策略至少重复多次，使用配对比较：

| 对照 | 要回答的问题 | 主要指标 |
|---|---|---|
| hybrid vs rule_tree | 自适应调查是否优于固定顺序 | sufficiency、根因正确率、无效采集、成本 |
| hybrid vs hypothesis_first | 先宽采再收敛是否比先立假设更稳 | false certainty、遗漏率、时延 |
| hypothesis_first vs evidence_first | 假设是否真正提高区分性 | information-goal recall、Top-1 下一动作、工具数 |
| hybrid vs one_shot_all_evidence | Agent Loop 是否值得额外编排成本 | 正确率、Token、传输量、总时延 |
| hybrid vs direct_model_without_tools | 深度 Collector 是否提供增量价值 | Evidence sufficiency、Claim precision、拒答率 |
| M1 vs H1/S1 | Mini-Drop 治理层是否优于框架替换 | 同工具同预算下的安全门禁、正确率与成本 |

判定规则应该预先注册：安全硬门禁任何一项非零直接失败；在安全通过后，只有质量指标有稳定提升且额外成本在预算内，才判定假设结构有效。若质量无提升、但工具数/Token/时延显著增加，就应把它判为负担并简化。

---

## 18. 关键技术选型比较

### 18.1 Pi 与直接模型 API

直接 API 优点是组件少，但缺少 Session、Tool loop、follow-up、steer、abort、事件和上下文管理。Pi 更适合长周期异步调查。

### 18.2 Pi 与 LangGraph/自研状态机

LangGraph 在 Python 状态图和 checkpoint 上有优势，但仍需适配 Mini-Drop 的 Tool、事件、交互控制和 Sidecar/Provider 隔离。当前通过 AgentRuntimePort 保留未来替换能力，避免框架绑死。

### 18.3 gRPC 与 HTTP Worker

Worker 使用 gRPC 是因为有稳定 Proto 合同、心跳任务下发和结果上报，且采集元数据结构固定。浏览器端仍使用 REST/SSE。

### 18.4 PostgreSQL/SQLite 与消息队列

数据库是状态真源，Outbox 负责可靠事件投递。消息队列即使未来加入，也只负责唤醒和传输，不能成为 Case、Task 或 Evidence 的唯一状态来源。

### 18.5 MinIO 与数据库 BLOB

大型 perf、火焰图和 profiling 产物放对象存储；数据库保存 metadata、hash、lineage 和 locator。这样避免数据库膨胀，也便于下载、保留和完整性校验。

---

## 19. 当前实现与目标设计的差异

### 19.1 必须主动说明的差异

1. 仓库示例默认 Runtime 是 deterministic；面试环境显式启用 pi。
2. 当前 ToolSpec 数为 20，部分旧文档仍写 12。
3. 当前 Worker 注册 13 个 Collector。
4. 旧 DiagnosisOrchestrator、规则候选和数值 confidence 代码仍存在，主要用于兼容和离线基线。
5. Evidence Contract 的所有最少来源/窗口条件还没有统一成为 Pi finish 的硬门禁。
6. Pi Session 仍有内存状态，但业务事实已经由 Server 数据库持久化；需要继续关注极端崩溃点的契约测试覆盖。
7. temperature、max_tokens、seed 在当前 Pi SDK 接入中是实验元数据，不应宣称已真正应用。

### 19.2 不应夸大的能力

- 有 Sidecar health 不代表 Provider completion 可用。
- AcceptedTurn 不代表最终回答完成。
- 有 Evidence ID 不代表 Claim 已被字段验证。
- 有依赖图不代表根因已确认。
- 少量演示场景不能证明普适准确率。
- deterministic fallback 不能作为 Pi 模型结果统计。

---

## 20. 面试主叙事

### 20.1 30 秒版本

> Mini-Drop 的核心是 Evidence-native 受监督调查。Pi 负责长周期 Agent Loop，模型选择缺失事实和下一项 Collector；但模型没有基础设施权限，所有 Tool Call 回到 FastAPI Tool Gateway。CollectionSupervisor 将合法提案编译成原生 Task，Linux Agent 通过 gRPC 执行 perf、eBPF 等采集，Artifact 再物化为带 hash 和 lineage 的 EvidenceProjection。新 Evidence 通过 Outbox/Wakeup 恢复 Pi，最终每个 Claim 都绑定 Evidence ID、Projection hash 和字段谓词，Verifier 决定 CONFIRMED、PARTIAL 或证据不足。

### 20.2 两分钟版本

> 这个项目先把 AI 推理和可靠执行解耦。用户问题进入一个持久 Case，Server 构建包含范围、Evidence、Gap、预算和 Revision 的 Snapshot，交给 Pi Sidecar。Pi 提供 Session、Tool loop 和 follow-up，但只看到 Mini-Drop 白名单工具。模型可以提出假设、分析证据或提议一个 Collector，不能直接执行命令。每次工具调用都由 Tool Gateway 按 Tenant、scope、generation、risk、budget 和 capability 重验。合法采集由 Supervisor 变成 CollectionRequest 和原生 Task，Worker 通过 gRPC 领取并执行。原始 Artifact 在 MinIO，Server 生成稳定 CaseEvidence 和有界 Projection。完成后 Outbox 产生 Wakeup，Pi 基于新 Evidence 继续，而不是轮询。最终模型必须提交结构化 Claim，Server 验证 Evidence、hash、field path 和 predicate，并根据阻断 Gap、反证和因果图决定是否确认。这使模型的开放式推理被限制在可审计、可恢复、可验证的执行边界内。

### 20.3 高频追问短答

**为什么不直接把服务器 Shell 给模型？**

因为 Prompt 不能构成安全边界。模型只能表达信息目标，服务端根据版本化 CollectorSpec 生成 Task，并在 Server 和 Worker 两端校验。

**为什么 Artifact 和 Evidence 要分开？**

Artifact 是物理文件，Evidence 是业务语义和血缘，Projection 是模型可消费视图。分层后才能同时支持原始下载、模型预算、字段引用和人工治理。

**为什么有这么多 Revision？**

不同 Revision 隔离不同竞态：Session、控制命令、目标范围、计划、Evidence 集合、人工 Review 和数据内容不能用一个版本号表达。

**如何避免模型幻觉？**

不能消灭模型生成错误，但可以让错误无法升级为权威事实：必须引用 ACTIVE Evidence、固定 Projection hash 和真实字段，谓词失败就拒绝，证据不足允许 abstain。

**置信度怎么算？**

在线主线不是一个根因概率公式，而是 Evidence 质量、Claim 支持、因果闭合和最终状态四层门槛。旧链有数值权重，但只用于兼容和评测。

**Pi 挂了怎么办？**

Case、Task、Evidence 在 Server 数据库中；Wakeup 可重排队，Session 可从 Snapshot 重建，generation fence 旧事件，采集底座不依赖 Pi 才能存活。

---

## 21. 代码导航

| 主题 | 主要文件 |
|---|---|
| Runtime 接口 | server/app/agent_runtime/port.py |
| Pi Adapter | server/app/agent_runtime/pi_adapter.py |
| Runtime 选择 | server/app/agent_runtime/dispatcher.py |
| Runtime Policy | server/app/agent_runtime/policy.py |
| Runtime Options | server/app/agent_runtime/options.py |
| Tool Catalog | server/app/agent_runtime/catalog.py |
| Pi Session/Prompt/Event | agent_runtime/pi-sidecar/src/runtime.mjs |
| Pi HTTP Surface | agent_runtime/pi-sidecar/src/server.mjs |
| Pi Tool Proxy | agent_runtime/pi-sidecar/src/tools.mjs |
| Sidecar EventSpool | agent_runtime/pi-sidecar/src/event-spool.mjs |
| Runtime Context / Tool Gateway | server/app/v6_routes.py |
| Collection 编译 | server/app/diagnosis/collection_supervisor.py |
| Plan 调度 | server/app/diagnosis/plan_driver.py |
| Worker Collector | agent/mini_drop_agent/main.py |
| gRPC dispatch/result | server/app/grpc_services/ |
| Task 状态机 | server/app/state_machine.py |
| Artifact 服务 | server/app/artifact_service.py |
| Evidence 物化 | server/app/diagnosis/case_evidence.py |
| Projection | server/app/diagnosis/evidence_projection.py |
| Evidence Analysis | server/app/diagnosis/evidence_analysis.py |
| Investigation State | server/app/diagnosis/investigation_state.py |
| Claim Verifier | server/app/diagnosis/v6_policy.py |
| SourceGateway | server/app/diagnosis/source_gateway.py |
| Outbox/Wakeup | server/app/app_factory.py、server/app/jobs/outbox_relay.py |
| CollectorSpec | mini_drop_contracts/collector_spec.py |
| Evidence Contract | server/app/diagnosis/evidence_contracts.py |
| 评测 | scripts/run_collector_agent_eval.py |

---

## 22. 术语表

| 术语 | 含义 |
|---|---|
| Case | 一次长期问题调查的业务聚合根 |
| Turn | 用户或系统触发的一次 Runtime 交互 |
| Cycle | Evidence Wakeup 等事件触发的一次 Agent 推进 |
| Snapshot | 某次模型请求看到的固定 Case 投影 |
| Collector | Worker 上注册的受控深度采集能力 |
| Proposal | 模型提出、尚未获得执行权威的结构化意图 |
| CollectionRequest | 经过 Supervisor 验证的权威采集请求 |
| TaskAttempt | 一次具体执行尝试 |
| Artifact | 原始或派生文件 |
| Evidence | Case 中带血缘和治理状态的稳定证据身份 |
| Projection | Evidence 的有界确定性模型视图 |
| Citation | Fact 指向 Evidence Projection 字段或 span 的引用 |
| Claim binding | 最终 Claim 与 Evidence、字段、谓词的持久绑定 |
| Gap | 阻止 Claim 成立的具体缺失事实 |
| Fence | 拒绝旧 generation/revision 写入当前状态的机制 |
| Outbox | 与业务事务一起持久化、稍后可靠投递的事件 |
| Wakeup | 新 Evidence 或采集终态驱动 Agent 继续的持久事件 |
| Abstention | 因证据不足而明确拒绝确认根因 |

---

## 23. 问答核实记录

本节用于记录后续面试追问中新核实的事实。每条记录应包含日期、问题、结论、代码证据和是否影响主设计。

### Q-000：当前面试运行时是否默认配置 Pi Key？

- 日期：2026-08-22
- 结论：面试和演示环境按 Pi 已配置处理；仓库示例保留 deterministic 默认作为 fail-closed 和控制组。
- 证据：.env.example、server/app/agent_runtime/config.py、docs/environment-setup.md。
- 影响：不改变架构，只明确运行假设。

### Q-001：当前 Tool 和 Collector 数量是多少？

- 日期：2026-08-22
- 结论：当前代码有 20 个服务端 ToolSpec、13 个 Worker Collector。数字属于当前基线，不应写成长期架构常量。
- 证据：server/app/agent_runtime/catalog.py、mini_drop_contracts/catalog/collectors.v1.json、agent/mini_drop_agent/main.py。
- 影响：纠正旧文档中“12 个工具”的过期表述。

### Q-002：假设驱动是有效设计还是额外负担？是否已有数据和多链路对比？

- 日期：2026-08-22
- 结论：假设在当前系统中是受约束的可证伪调查状态，不是事实或根因权威。新版 9×3 PR 矩阵和 1 条真实 Pi 未知拓扑闭环证明链路可完成、可引用反证并能在证据不足时拒绝因果确认；它们没有构成假设策略相对其他策略的正式消融。
- 已有数据：新版真实 PR 矩阵为 27/27 completed，第二轮完整 Evidence 引用为 27/27，非双盲人工粗评约 9.2–9.6/10；未知拓扑链路仍以 PASS、PARTIALLY_CONFIRMED 和明确 abstention 收尾。旧的 8 PR/75/80 结果保留为历史基线。
- 多链路能力：代码定义六种策略矩阵以及 B0-B5/M1-M2/H1/S1 对照臂，但当前没有至少 30 个独立场景、同预算、多次重复的持久化多臂报告。离线策略矩阵明确不应用被选策略，不能冒充策略准确率。
- 证据：reports/evaluation/verified-20260821.md、scripts/run_agent_strategy_matrix.py、benchmarks/agent_experiments/matrix.json、benchmarks/collector_agent_v1/manifest.json、scripts/run_collector_agent_eval.py、server/app/diagnosis/investigation_state.py、server/app/v6_routes.py。
- 当前实现/目标设计/兼容路径：生产只公开 hybrid；其他策略是 experiment-only。目标是完成固定模型/Prompt/目录/预算的配对重复实验；旧 rules-first 只作为控制组。
- 影响：新增 17.4 节，明确不得把少量真实运行或评测框架本身描述成策略优势的统计证明。

### Q-003：没有正式多链路对比，项目价值在哪里？

- 日期：2026-08-22
- 结论：没有正式多链路对比，削弱的是“某种 Agent 策略更准确/更省成本”的产品效果结论，不等于项目没有工程价值。当前项目价值主要在于把不受控的 LLM 诊断问题，收敛为可执行、可审计、可恢复、可拒答的 Evidence-native 服务；真实运行已经验证了这条基础设施和治理闭环。
- 已被真实链路支持的价值：Pi Sidecar 与业务真源解耦；模型不能越权执行；采集经过 Supervisor/Worker 双重校验；长任务用 Artifact、Evidence、Outbox/Wakeup 恢复；Claim 按 projection_hash、field_path 和 predicate 验证；依赖关系与因果关系分离；证据不足时提交 PARTIALLY_CONFIRMED/INSUFFICIENT_EVIDENCE，而不是伪造根因。
- 已有真实证据：9 个真实 GitHub PR 各 3 轮的 DeepSeek 运行全部完成，第二轮 27/27 轮通过结构和引用门禁，人工粗评约 9.2–9.6/10；真实未知拓扑 Pi 链路从一个 client PID 发现 server 并双端复核，因平台覆盖不足主动降级，未把通信依赖冒充因果根因。
- 尚未被证明的价值：假设驱动相对 rule_tree、evidence_first、one_shot 或其他 Agent 框架的准确率、成本、时延优势；这部分必须通过固定模型/工具/预算、多场景、多次重复的配对实验回答。
- 面试回答口径：如果问题是“这个架构有没有价值”，回答有，价值在可信执行和证据治理；如果问题是“假设策略是否优于基线”，回答尚无统计结论，不能越界宣称。把两种命题分开，是比编造准确率更可靠的工程判断。
- 下一步产品化实验：先以同一 Evidence/Collector/Verifier 做策略消融，报告质量、错误确认、工具成本和安全硬门禁；若假设链没有显著净收益，就保留 Hypothesis/Gap 作为可选审计状态，收缩在线编排，不让它成为无证明的复杂度。

### Q-004：Token、长期累积/偶发问题、大数据筛选和采集范围如何设计？

- 日期：2026-08-22
- Token 结论：使用 Artifact -> Projection -> Snapshot 摘要 -> Sidecar Prompt 二次限界 -> Tool 按需展开，默认字符预算 24,000；支持 fresh_session 和 ModelAttempt 台账。当前还没有 tokenizer 精确的 Case 累计 Token 硬门禁，max_tokens 仍是实验元数据。
- 真实台账：未知拓扑 19 attempts，input 28,834、output 29,583、cache-read 610,816、约 195 秒；8 PR 共 76 attempts，input 101,143、output 8,059、cache-read 111,744、约 295 秒、记录 cost 0.016729。前者 cost=0 是缺少成本回传，不代表免费。
- 数据筛选：Projection 默认最多 20 samples、10 Top items、12 log events、10 errors、40 nodes、80 edges；指标进一步变成统计摘要和 slope，日志按相关性/严重度/新近性筛选；省略项留下审计元数据。
- 长期/偶发：长期问题用低开销基线和多窗口趋势再升级深采集；偶发问题用 continuous_perf 分窗和 TargetSession Signal 在告警点关联前 300 秒/后 60 秒窗口，细节默认保留 24 小时。
- 权力边界：AI 选择信息目标和受限参数，Case scope、Catalog、Supervisor、Policy、审批和 Worker 决定是否执行。AI 不能自由扩大范围。
- 当前缺口：continuous_perf 是最长 600 秒的有界 R2 Task，不是永久 ring buffer；Signal 依赖外部系统输入；内置基线检测没有统一接入 Pi 主线；自动建 Case 后不自动发起 Pi Turn；在线缺少 Case 级 Token/成本硬停止。
- 证据：server/app/ai_context.py、agent_runtime/pi-sidecar/src/runtime.mjs、server/app/v6_routes.py、server/app/diagnosis/evidence_projection.py、server/app/agent_runtime/options.py、server/app/agent_runtime/policy.py、server/app/diagnosis/collection_supervisor.py、agent/mini_drop_agent/collectors/continuous.py、server/app/sql_repository.py、server/app/routes/cases.py、tests/test_target_sessions.py、tests/test_continuous_collector.py、真实评测 result.json/round-results.jsonl。
- 影响：新增 13.4-13.7，区分当前能力与生产级持续检测目标设计。

### 待核实问题模板

~~~text
### Q-XXX：问题

- 日期：YYYY-MM-DD
- 结论：
- 证据：文件、测试、配置或运行报告
- 当前实现/目标设计/兼容路径：
- 影响：是否需要更新其他章节或代码
~~~
