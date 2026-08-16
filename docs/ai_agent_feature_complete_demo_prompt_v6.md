# Mini-Drop 内嵌诊断 Agent 完整落地总提示词 v6.0

> 受众：接手本仓库并持续修改、测试、部署和收敛结果的编码 AI/Codex
> 目标：把当前工作树一次性收敛为功能完整、链路真实、可稳定演示的 Mini-Drop 内嵌诊断 Agent Beta
> 基线日期：2026-08-16；开始执行时必须重新读取当前 Git、迁移、依赖、环境和机器证据
> Git 基线规则：执行时读取当前 checkout，并绑定实际存在的 pushed commit、candidate manifest 或用户提供的完整 bundle；历史审计曾基于 `41f41a0` 加未提交工作树，但复制本文到另一台机器时不得假定那些未提交内容存在
> 详细历史附件：`docs/ai_agent_runtime_integration_plan.md` 仅作协议素材；其固定版本、迁移编号、完成度和阶段状态不再具有权威性
> 冲突顺序：用户最新要求 > 当前代码与运行事实 > 本文 > 其他旧设计/进度/报告

## 0. 给执行 AI 的直接指令

你不是来继续写规划，也不是来增加更多 Agent 名词、空 API、Mock 或演示脚本。你必须直接修改当前代码、迁移、测试、前端、部署和必要文档，持续运行验证并修复失败，直到本文 `DEMO_READY` 的全部条件满足。

这是一个单一交付目标，不是按周计划，也不是等待用户逐批发送“继续”的路线图。本文的 M0-M8 只是依赖门禁；前一门禁通过后立即自动领取下一项。对话压缩、宿主回合结束、进程重启或 VM 暂时不可达都不能把工作退化成重新规划。

执行时必须遵守：

1. 开工先读取当前 Git diff、未跟踪文件、Alembic heads、实际 package locks、主要服务和最近机器报告；保护用户已有修改。
2. 不相信提交标题、进度勾选、旧 VM JSON、API 存在、测试数量或 README 中的“完成”声明。
3. 每项功能先建立一个能在当前版本失败的纵向 Contract Test，再实现生产链路，最后保存事件和数据库证据。
4. 低风险、仓库内的实现、测试、构建和只读环境检查持续推进；只有 Secret、外部权限、故障注入授权、不可逆操作、Git push 等确需新权限时才请求最小输入。
5. VM 暂时不可达时继续完成所有本地、容器、浏览器、打包和只读预检；恢复后从状态文件中的唯一下一动作继续。
6. 缺少 Formal Public Authority 时继续完成实现、Development Eval、候选构建和预检，只在其他 mandatory 项已收敛后请求用户/宿主提供只读 Authority；不得自行生成信任根冒充 Formal。
7. 不擅自提交或推送 Git；除非用户明确授权。
8. 不保存或展示模型私有思维链；只保存可复核的决策摘要、候选假设、证据引用、反证、缺失事实、工具理由和限制。
9. 不把大量时间用于全量漏洞清理、SBOM、合规、多租户、生产级灾备或长期压力。仅保留防止任意命令、Secret 泄漏、故障无法清理和候选失真的最小安全线。
10. Definition of Done 未满足时禁止使用“全部完成”“成熟交付”“已收敛”“盲测通过”或“生产可用”。

交付状态严格区分：

- `DEMO_READY`：本文规定的本地、真实 Provider、浏览器、当前候选和三节点公开业务门禁全部通过；这是当前项目的完成目标。
- `INDEPENDENTLY_VALIDATED`：在 `DEMO_READY` 上再导入候选仓库之外的 Acceptance Authority 签名的 Holdout 成绩。
- 外部 Holdout Evaluator 不可用不阻塞 `DEMO_READY`，但必须保持 `AWAITING_EXTERNAL_HOLDOUT`，不得把本地自签或仓库内 Oracle 称为独立验证；`DEMO_READY` 本身仍要求 11.6 节候选外 Formal Public contract authority。

## 1. 最终产品定义

Mini-Drop 继续是采集、执行和事实系统，AI 是第二页的内嵌调查 Agent，不喧宾夺主。

### 1.1 两种用户入口

入口一：问题驱动。

```text
用户用模糊自然语言描述现象
→ Agent 理解目标和范围
→ 复用已有 Evidence
→ 自主选择最有区分度的低风险 Query/Collector/MCP
→ 持续收到新 Evidence 并更新判断
→ 输出结构化结论、缺口、修复和验证
```

入口二：数据驱动。

```text
用户从第一页或会话中 @Task/@Collection/@Artifact/@Evidence
→ 数据先物化为 canonical CaseEvidence
→ Agent 先解释和判断充分性
→ 只对真正缺失的事实补采
→ 避免重复采集并持续收敛
```

调查过程中用户可以继续发送自然语言、补充已有数据、要求解释、纠正目标/时间/假设、降低或排除证据、调整采集目标和顺序、暂停、恢复或停止。低风险任务默认自动执行，不逐项确认；用户可以随时打断。

### 1.2 Mini-Drop 与 Pi 的边界

保留并复用：

- 第一页 Dashboard、原生 Task、Collector、Artifact、Worker、任务取消和结果查看；
- Case、Evidence、规则分析器、容量计算、MCP/SourceGateway 等已有领域能力；
- `AgentRuntimePort` 作为可替换边界；Pi 是首选 Runtime，deterministic 是降级和对照。

Pi 负责：

- 对话理解和回答；
- 候选假设、Missing Fact 和信息增益判断；
- 在受控 Operation Catalog 中选择下一动作；
- 根据新 Evidence 改变调查方向；
- 提议 Plan/Campaign/CausalGraph/Recommendation；
- 判断询问、继续、部分确认、拒答或停止；
- `follow_up`、`steer`、`abort` 和上下文压缩。

Mini-Drop 领域内核负责：

- Case、Run、Turn、身份、范围和权限；
- Evidence、Projection、质量、时间、lineage 和 Review；
- Plan、Campaign、Assignment、ExecutionUnit；
- Task、SourceCall、预算、风险、去重、取消和覆盖率；
- Runtime generation、revision fence、Outbox/Wakeup、审计和恢复；
- Causal/Report Verifier、结论持久化和前端状态投影。

Pi 不得直接运行 Bash、Shell、perf、eBPF、kubectl、SQL、SSH、文件读写或任意 MCP。不得从头自研另一套通用 Agent Loop；继续使用 Pi SDK 的会话、工具和事件能力，在 Adapter/Sidecar 层适配。若 Pi 最终不满足合同，只替换 `AgentRuntimePort` 后面的实现，不推翻 Mini-Drop 领域内核。

Pi 版本以当前 `package-lock`、实际 `node_modules` 和运行时导出的 package version 为准，不信任旧文档 banner，也不为了“最新”无条件升级。优先阅读本机已安装包的 SDK 类型/源码；需要补源码时只获取与 lock 精确匹配的 upstream tag/commit 并记录 hash。对 Pi 的必要修改优先放在 Mini-Drop Adapter/Sidecar；只有已证明官方扩展点无法满足且修改范围可控时才维护轻量 fork，并保留上游 Contract Test。

### 1.3 不可妥协的六个系统不变量

1. Case 派生的 Task/SourceCall 只能由一个持有效 Case Lease 的 `CaseSupervisor` 创建。
2. 模型必须能读取安全裁剪后的真实证据内容，而不只是 ID、状态、哈希和“已采集”备注。
3. 每批对当前活跃 Run 有效且推进 evidence watermark 的新 Evidence，必须经持久 Outbox/Wakeup 最终产生恰好一个新 AgentCycle 和 `model_request_id`；暂停时延后，旧 revision、重复或不适用 Evidence 不自动唤醒当前 Run。Sidecar 内存不是真源。
4. 每个写工具必须受 runtime generation、control/scope/plan/campaign revision、Turn 权限和 idempotency fence 约束。
5. Collector、Query 和 MCP 都以 Operation 进入 Campaign→Assignment→ExecutionUnit，并汇入同一个 CaseEvidence Store。
6. 最终结论必须是结构化、可验证的因果结论；发现一个真实异常不等于它是主根因。

任一不变量没有真实纵向测试时，不得声明 Agent 已完成。

## 2. 当前版本的审计基线

当前工作树已经有较大进展：Pi Sidecar、内部 Token、RuntimeBinding/Turn/Event 表、Query→原生 Task、Artifact→CaseEvidence、Campaign/Skill/Knowledge/Capacity API、部分内嵌图表以及大量测试均已出现。普通 Python/Web/Sidecar 测试和构建可以通过。

但 2026-08-16 审计确认以下断点仍阻止真实业务闭环，执行 AI 必须先复现并逐项消除：

1. Runtime Snapshot 和 `get_case_snapshot` 只提供 Evidence 元数据；Pi 看不到 CPU 数值、日志行、Trace、连接、火焰图热点等内容。
2. Pi 最终回答停留在 Runtime Event；AgentTurn 没有完成，Case 会话和前端只显示 Accepted 占位。
3. `investigation_directive` 用固定 `sys_metrics→process→log→...` 顺序禁止模型改变方向，所谓稳定性是硬编码而非证据驱动。
4. `ANSWER_ONLY/execute_safe_tools/max_tool_calls` 没有成为机器级 Tool Policy，模型仍可能创建采集任务。
5. Sidecar 命中旧 Session 后不刷新 Context，每轮重复 subscribe；Session、seq、未确认事件和最后回答主要在内存中。
6. Task wake 只是一次 best-effort `follow_up`，Sidecar 重启或暂时离线会永久丢失推进。
7. pause/stop/correction 没有真正 abort/steer Pi；内部 Query 等写工具缺完整 generation/control/scope/plan fence，迟到调用可能在停止后创建任务。
8. 当前 `CaseSupervisor` 仍是旧 AutonomousAgent 的租约包装；PlanDriver、Query、Fanout、旧 Orchestrator 和 Pi 仍形成多个 Case 调度入口。
9. Campaign 只是临时编译为 PlanStep，没有持久 Assignment/ExecutionUnit；逻辑 service/host/workload 不真正筛成员，不验 operation capability，Fanout 不自动 aggregate。
10. 目标解析失败会回退第一实例、任意在线 Worker 或 PID 1；Evidence 去重只看同 Collector 的 DONE Task。
11. MCP、容量评估、Skill/Knowledge 与 Pi 主循环断开；MCP Result 没有统一进入 canonical Evidence。
12. `finish` 只接受 summary 和 Evidence ID，Verifier 仍是占位，不能表达复合故障、精确 Gap 和修复验证。
13. Workbench 错误读取 Axios 的双层 `.data`，大量后端 API 未被组件消费；AI 派生任务仍有一部分被第一页过滤。
14. Candidate 没有哈希全部未跟踪内容，release 可覆盖，三节点部署没有严格证明相同候选。
15. Public Runner 可把 PARTIAL/AWAITING 当成功，VM `check()` 不验证布尔谓词，Holdout 可用调用者自带 key 自签 VERIFIED。

必须先新增并跑出失败的 `audit_regressions` 测试，至少一一覆盖上述断点。旧进度文件的 `[x]` 只能作为代码导航，不能作为完成证据。

以下全部是不可信的完成信号：HTTP 200、Sidecar ready、AcceptedTurn、一个被明确指令要求调用的 Tool、Mock 三轮、手工 aggregate、只有 Evidence ID、独立 MCP API、空 UI 卡片、测试数量、旧 VM 报告、PARTIAL/AWAITING、开发者自签和只哈希 tracked diff 的候选包。

## 3. 唯一目标架构

```text
用户消息 / @已有数据 / 确定性控制命令
                │
                ▼
       ConversationTurn + CaseCommand
                │ 同事务写 DomainOutbox
                ▼
      CaseSupervisor（唯一 Case 调度者）
       ├─ 持久 Lease / execution_epoch
       ├─ 构建 CaseContextSnapshot
       ├─ 创建 AgentCycle / ModelRequest
       ├─ 校验 Pi Tool Proposal
       ├─ 持久 Plan/Campaign Revision
       └─ 编译 ExecutionUnit
             ├─ COLLECTOR → 原生 Drop Task → Worker
             ├─ QUERY     → 原生 Drop Task → Worker
             └─ SOURCE    → SourceCall → SourceGateway/MCP
                                      │
               Artifact / SourceResult
                         │
                         ▼
       Evidence Ingestion + EvidenceProjection
                         │ 同事务写 Outbox
                         ▼
               Durable RuntimeWakeup
                         │
                         ▼
        新 AgentCycle / 新 model_request_id
                         │
       更新假设、因果图、补采、询问或 Finish
                         │
                         ▼
       AssistantMessage + CaseEvent + Workspace/SSE
```

禁止存在第二套 Case 执行路径：

- Pi、Case API、Query API、MCP API、PlanDriver、旧 AutonomousAgent 和旧 sweeper 不能直接创建 Case 派生 Task/SourceCall。
- 旧规则/分析器可成为 candidate generator、verifier 或 deterministic strategy，但不能拥有独立调度权。
- 普通第一页 `/api/tasks` 继续可独立创建 standalone Drop Task；若 Task 属于 Case，必须有有效 ExecutionUnit、Supervisor dispatch lease epoch 和完整 lineage。Lease token 只在 Supervisor 内部使用，绝不交给 Pi。
- 新主链通过后，可以保留旧字段和只读兼容投影，但必须删除或在 Case 模式禁用所有平行写入/调度入口；“保留旧框架”不能成为多脑并存的理由。

## 4. 会话、Run、AgentCycle 和控制状态

### 4.1 ConversationTurn

每条用户输入持久化：

```text
turn_id / case_id / actor_id / client_command_id / message
disposition
side_effect_policy = READ_ONLY | PROPOSE_ONLY | AUTO_READ_LOW
status / created_at / completed_at
```

`TurnDisposition` 至少包含：

- `ANSWER_ONLY`：解释现有数据、图表、状态或概念；只能使用只读工具。
- `ATTACH_EVIDENCE`：结构化绑定已有 Task/Collection/Artifact/Evidence，再决定解释或继续。
- `INVESTIGATE`：开始或继续调查。
- `CORRECT_CONTEXT`：纠正目标、时间、范围、拓扑或假设。
- `CONTROL`：暂停、恢复、停止、取消、排序、改目标、禁用 operation。
- `DEPLOYMENT_ASSESSMENT`：使用独立 Verdict 合同。

Disposition 与机器策略固定映射：

```text
ANSWER_ONLY                          → READ_ONLY
INVESTIGATE + execute_safe_tools=false → PROPOSE_ONLY
INVESTIGATE + execute_safe_tools=true  → AUTO_READ_LOW
```

`PROPOSE_ONLY` 可以持久化 Hypothesis/Plan/Campaign Proposal，但 Plan accepted/ExecutionUnit/Task/SourceCall/acquisition wakeup 增量必须为 0；用户确认或新 Turn 才能进入执行。`AUTO_READ_LOW` 仍受每 Turn `max_tool_calls`、Case budget、Operation allowlist 和全部 fence 强制约束。`READ_ELEVATED` 及以上永远进入 `WAITING_APPROVAL`，不能由 `execute_safe_tools=true` 自动执行。

未接受 Proposal 使用独立对象，不占用正式 revision：

```text
AgentProposal:
proposal_id / object_type / payload / validation_result
source_cycle_id / status = PROPOSED | ACCEPTED | REJECTED | EXPIRED
created_at / decided_at
```

只有 Supervisor 接受后才创建 Plan/Campaign/Graph Revision并可能调度；Proposal 本身不能进入 active plan。

状态机：

```text
RECEIVED → ROUTED → RUNNING
RUNNING → WAITING_USER | WAITING_EVIDENCE | COMPLETED | FAILED | CANCELLED
WAITING_* → RUNNING | CANCELLED
```

控制类输入先走确定性 CaseCommand，不等待模型理解。终态 InvestigationRun 仍允许 `ANSWER_ONLY` 读取历史证据，但禁止自动产生新 ExecutionUnit。

### 4.2 ANSWER_ONLY 的机器约束

用户显式选择“只解释”，或询问“这张图表示什么/为什么升高/这条证据说明什么”时，默认 `READ_ONLY`；有歧义时先询问，不得默认采集。

Sidecar 每轮 Tool Catalog 必须按 `side_effect_policy` 动态构建。READ_ONLY 回合只能看到：

```text
get_case_snapshot
list_case_evidence
get_evidence_projection
compare_evidence
search_knowledge
get_causal_graph
get_evidence_gaps
```

即使模型构造了写 Tool Call，Gateway 也必须返回 `TURN_READ_ONLY` 且副作用为零。固定机器断言：

```text
ΔPlanRevision = 0
ΔCampaignRevision = 0
ΔExecutionUnit = 0
ΔTask = 0
ΔSourceCall = 0
ΔAcquisitionWakeup = 0
AssistantMessage = 1
TurnStatus = COMPLETED
```

如果现有数据不足以回答，应解释当前数据实际说明什么、不能说明什么，并提供“继续调查”的可选动作；不能自动开始调查。

### 4.3 InvestigationRun、AgentCycle 和 ModelRequest

Conversation 与 InvestigationRun 分离：一次 Case 可有多个 Run，追问不自动新建 Run。

```text
InvestigationRun:
run_id / case_id / status
scope_revision / control_revision
active_plan_revision / evidence_watermark
created_from_turn_id

AgentCycle:
cycle_id / case_id / run_id
trigger_type = USER_TURN | EVIDENCE_COMMITTED | CONTROL_CHANGED |
               TOOL_RESULT | RETRY | RECOVERY
trigger_ref / trigger_turn_id(nullable) / origin_turn_id(nullable)
context_snapshot_id / evidence_watermark
runtime_binding_id / generation / status

ModelRequest:
model_request_id / cycle_id / provider_request_id
input_snapshot_hash / evidence_projection_hashes
status / usage / started_at / completed_at

ModelResponse:
model_response_id / model_request_id / provider_request_id / idempotency_key
canonical_visible_content / proposed_tool_calls / response_hash
durable_spool_offset / accepted_at
```

AgentCycle 状态：

```text
QUEUED → RUNNING
RUNNING → WAITING_TOOL | WAITING_EVIDENCE | WAITING_USER |
          COMPLETED | FAILED | CANCELLED | RECOVERY_REQUIRED | FENCED
WAITING_* → RUNNING | CANCELLED | RECOVERY_REQUIRED | FENCED
RECOVERY_REQUIRED → FENCED | FAILED | CANCELLED
```

ModelRequest 状态：

```text
QUEUED → RUNNING
RUNNING → WAITING_TOOL | COMPLETED | FAILED | CANCELLED | FENCED
WAITING_TOOL → RUNNING | COMPLETED | FAILED | CANCELLED | FENCED
```

Evidence/Control/Recovery 触发的 Cycle 可以没有新用户 Turn；不得为它伪造用户消息。此时使用 nullable `trigger_turn_id` 并保留 `origin_turn_id` 追溯最初调查请求。

恢复不能把原 Cycle 原地改回 RUNNING：旧 Cycle `RECOVERY_REQUIRED→FENCED`，创建 `trigger_type=RECOVERY`、带 `recovery_of_cycle_id` 的新 Cycle、新 generation 和新 model_request_id。只读/同步 ToolResult 可以在同一 ModelRequest 内返回；异步采集只有形成新的 Evidence/Gap 才触发新 Cycle。

Run 状态：

```text
CREATED → RUNNING
RUNNING → WAITING_USER | WAITING_EVIDENCE | PAUSED |
          RESOLVED | INSUFFICIENT_EVIDENCE | STOPPED | FAILED
PAUSED → RUNNING | STOPPED
WAITING_* → RUNNING | PAUSED | STOPPED
```

“连续三轮”必须是三个新的 `model_request_id`。第二/三轮 Snapshot 必须包含上一轮新 Evidence 的 projection hash。不得用首轮预制三步计划、手工调用内部 Tool 或固定 Planner 冒充自适应循环。

Turn API 必须先路由 disposition，再检查 Run 状态：旧 Run/legacy Case 为 RESOLVED、STOPPED 或 INSUFFICIENT_EVIDENCE 时仍接受 ANSWER_ONLY；只有用户显式“继续/重新调查”才创建新 InvestigationRun 和新的 scope/control revisions。系统 Evidence wake 不得悄悄复活 terminal Run。

### 4.4 CaseSupervisor Lease 和唯一写权限

持久 Lease：

```text
case_id / lease_owner / lease_token
lease_epoch / deployment_epoch / expires_at / heartbeat_at
```

- 同一 Case 同时只有一个有效 Lease。
- Supervisor 创建 ExecutionUnit、Task、SourceCall 或提交终态前，在同一数据库事务验证 lease token/epoch。
- 崩溃后新实例获得更高 `lease_epoch`；旧实例写入返回 `LEASE_FENCED`。
- 不能依赖进程内锁。
- 重构当前 `CaseSupervisor`，使其成为编译/调度/聚合/控制/恢复的唯一拥有者，而不是旧 AutonomousAgent 的包装器。

## 5. canonical Evidence 与模型可读投影

### 5.1 唯一 CaseEvidence Store

所有进入 AI 的当前事实统一经过：

```text
ResourceRef
→ ReferenceResolver
→ Artifact/SourceResult validation
→ EvidenceEnvelope
→ canonical CaseEvidence
→ EvidenceProjection
→ CaseContextSnapshot
```

CaseEvidence 至少包含：

```text
evidence_id / tenant_id / case_id / investigation_run_id
execution_unit_id / task_id / artifact_id / source_call_id
source_channel = COLLECTOR | QUERY | MCP | USER
data_origin = FIXTURE | REPLAY | LIVE
target_ref / resource_incarnation / membership_snapshot_id
event_time_start/end / ingested_at
clock_id / clock_offset_ms / clock_uncertainty_ms
artifact_schema / schema_version / producer_version
content_hash / quality / effective_status
raw_locator
```

约束：

- Evidence ID 不得在 upsert 时被重新归属给另一 tenant/case；选择全局不可变 ID或真正的复合键。
- Attachment 与 legacy DiagnosisEvidence 只作兼容关联/投影，`finish` 不再直接接受它们。
- `ACTIVE/LOW_TRUST/EXCLUDED/STALE` 由不可变 EvidenceReviewRevision 计算。
- 新鲜度区分 wall-clock freshness 与目标事故窗口 applicability。
- Evidence 被排除/降信任后，引用它的 Claim/CausalEdge/Conclusion 标记为 `SUPERSEDED_REVIEW_REQUIRED`，写 Wakeup 并重新评估。

### 5.2 EvidenceProjection

这是当前版本的第一优先级对象：

```text
projection_id / evidence_id
projection_kind = MODEL_SUMMARY | TIMESERIES | TOP_ITEMS |
                  LOG_EVENTS | TRACE_PATH | FLAMEGRAPH_HOTSPOTS |
                  COVERAGE | RAW_PREVIEW
projection_schema / projection_version
content_json / projection_hash
truncated / source_bytes / projected_bytes
parser_version
```

`content_json` 至少能表达：

```json
{
  "summary": "checkout 进程 CPU p95 从 22% 上升到 91.2%",
  "signals": {
    "cpu_pct_p95": 91.2,
    "rss_mb_delta": 428,
    "gc_pause_ms_p99": 180
  },
  "top_items": [],
  "samples": [],
  "errors": [],
  "coverage": {},
  "interpretation_hints": [],
  "raw_ref": {"artifact_id": "..."}
}
```

要求：

- 每种可支持的 Artifact/SourceResult 使用确定性、版本化 parser 生成投影。
- 系统指标返回指标值/窗口/聚合；日志返回时间化样本和模式；Trace 返回关键路径/span；火焰图返回热点栈和占比；连接/进程返回 Top 项；所有投影带目标、时间、质量和截断状态。
- Snapshot 只携带小摘要、artifact type、关键 signals 和 projection hash；Pi 通过分页只读 Tool 按需展开。
- 大 Artifact 不直接进入 Prompt；但不能用“控制上下文”作为不提供任何内容的理由。
- Parser 失败、投影为空或截断影响判断时生成精确 EvidenceGap，不能把“已有 evidence_id”当作已分析。
- `interpretation_hints` 明确标记为派生提示，Verifier 不能把它当作原始 Current Evidence 字段；Claim 必须绑定 signals/samples/field extractor。

只读 Tool：

```text
list_case_evidence(filters, cursor)
get_evidence_projection(evidence_ids, projection_kinds, fields, max_bytes, cursor)
compare_evidence(evidence_ids, dimensions)
```

返回包含 `evidence_watermark`、projection hash、target/window/quality 和明确截断信息。最终 Claim 引用至少绑定 `evidence_id + projection_hash + field_path/extractor + time window`，不能只引用一个 ID。

### 5.3 已有数据和去重

`@Task/@Collection/@Artifact/@Evidence` 必须产生结构化 ResourceRef，经过真实归属、状态、目标、时间和完整性校验后才进入 Evidence。

Fingerprint 至少包含：

```text
operation/version + target identity/incarnation + requested time window +
normalized parameters + schema/parser version + quality requirement
```

复用前必须确认 Evidence 仍 ACTIVE、适用于当前 Claim 窗口、覆盖目标、质量足够且未被用户排除。不得把“存在同 collector 的 DONE Task”当成可复用。复用或拒绝复用都记录理由。

## 6. Plan、Campaign、Operation 和统一执行链

### 6.1 InvestigationPlan

PlanStep kind 固定：

```text
ACQUIRE_EVIDENCE | ANALYZE | ASK_USER |
WAIT_EVENT | FINISH | DEPLOYMENT_ASSESSMENT
```

PlanStepRevision 状态：

```text
DRAFT → QUEUED
QUEUED → DISPATCHING | WAITING_APPROVAL | CANCELLED | BLOCKED | SUPERSEDED
DISPATCHING → RUNNING | FAILED | CANCELLED | SUPERSEDED
RUNNING → COMPLETED | PARTIAL | FAILED | CANCELLED | SUPERSEDED
WAITING_APPROVAL → QUEUED | CANCELLED | SUPERSEDED
```

Plan、Step 和 Campaign 的每次结构变化都创建不可变 revision。删除、排序、改目标、改 Operation、锁定和禁用不能原地覆盖历史。CAS 校验、旧版 supersede 和新版创建必须在同一事务完成。

`WAITING_APPROVAL` 不可调度；`depends_on` 必须真正阻止提前执行并拒绝循环；前置失败或取消使后继 `BLOCKED`。Pi 只能提议 Plan，CaseSupervisor 校验后接受、修改或拒绝，并把原因返回 Pi。

### 6.2 OperationSpec：统一 Collector、Query 与 Source

```text
operation_id / version
execution_kind = COLLECTOR | QUERY | SOURCE
backend_ref / description
supported_target_types
parameters_schema / evidence_schema
required_capabilities / capability_version
risk = READ_LOW | READ_ELEVATED | CHANGE | FAULT_INJECTION
timeout_sec / max_output_bytes
parser_version / renderer_hash 或 connector_version
cache_ttl / fingerprint_fields
enabled / auto_allowed
```

- COLLECTOR、QUERY 由原生 Drop Task/Worker 执行。
- SOURCE 由 SourceCall/SourceGateway/MCP 执行。
- 三者共用 Assignment、ExecutionUnit、Fingerprint、预算、Evidence、Gap 和 Wakeup。
- `request_query`、`request_mcp_fact` 只能形成当前 ACQUIRE_EVIDENCE Step 的 Proposal，不能直接创建 Task/SourceCall。
- Missing Fact 从实际 Operation/Source capability declarations 路由，不能维护与连接器脱节的硬编码 Source ID。

Query Renderer 必须使用固定 executable 与 argv 数组、`shell=False`、固定 locale、独立进程组、超时后终止进程组、输出字节上限和版本化 parser。禁止管道、重定向、命令替换、sudo、任意路径、任意 curl、自定义 executable/cwd/env、修改型命令和无限日志。

首批实际可用 Operation 至少覆盖：

- `process.list/status/open_files_summary`；
- `system.load/memory`、`filesystem.usage/inodes/io`；
- `network.connections/listeners/routes`；
- `service.status/logs_tail`；
- `container.list/inspect`、`docker.service_status`；
- 项目现有 sys_metrics、process/log/connection/runtime/flamegraph Collector；
- 至少一个真实只读 Source/MCP capability。

### 6.3 CollectionCampaign 和逻辑资源

```text
CampaignRevision
  campaign_id / revision / plan_step_revision_id
  membership_snapshot_id / coverage_policy / status
  common_baseline_assignment_ids[]
  differential_assignment_ids[]

AcquisitionAssignment
  assignment_id / role / operation_ref
  target_selector / parameters / requested_window
  required_fact_ids[] / risk / priority / depends_on / required_coverage

ExecutionUnit
  execution_unit_id / assignment_id / resource_ref
  operation_id/version / normalized_parameters
  evaluation_run_id(nullable) / deployment_epoch
  control/scope/plan/campaign revisions
  fingerprint / status / task_id 或 source_call_id
  cancel_epoch / cancel_command_id / cancel_requested_at / terminal_result_status
```

状态机固定：

```text
ExecutionUnit:
PLANNED → READY → DISPATCHING → RUNNING
RUNNING → SUCCEEDED | FAILED | TIMED_OUT | CANCEL_REQUESTED
PLANNED/READY/DISPATCHING → CANCEL_REQUESTED
CANCEL_REQUESTED → CANCELLED | COMPLETED_LATE | FAILED_LATE | TIMED_OUT
PLANNED/READY/DISPATCHING → BLOCKED | CANCELLED | SUPERSEDED

SourceCall:
PLANNED → DISPATCHING → RUNNING
RUNNING → SUCCEEDED | FAILED | TIMED_OUT | CANCEL_REQUESTED
PLANNED/DISPATCHING → CANCEL_REQUESTED
CANCEL_REQUESTED → CANCELLED | COMPLETED_LATE | FAILED_LATE | TIMED_OUT
PLANNED/DISPATCHING → BLOCKED | CANCELLED | SUPERSEDED

Campaign:
DRAFT → QUEUED → RUNNING
RUNNING → COMPLETED | PARTIAL | FAILED | CANCELLED | SUPERSEDED
```

MembershipSnapshot 的成员必须是逻辑资源而不是 Worker：

```text
resource_ref / resource_type / service_role
instance_uid / process_start_time
executor_agent_id / host_ref / fault_domain
capability_versions / clock_quality
```

同一 Worker 可以承载多个服务/容器/进程；Assignment/coverage/idempotency 以 `assignment_id + resource_ref` 为核心，不能以 agent_id 唯一。service/host/workload selector 必须真正筛选冻结 Snapshot；无法解析时生成 `TARGET_UNRESOLVED`，Task 增量为 0，禁止回退任意 Worker、第一实例或 PID 1。

每个 ExecutionUnit 终态后由 Supervisor 自动重算 Campaign。必需 Unit 全 `SUCCEEDED` 时 Campaign=COMPLETED；至少一个成功但必需覆盖不足/部分失败时 PARTIAL；全部必需 Unit 无成功时 FAILED；只有整个 Campaign/Step 被用户取消时 Campaign=CANCELLED，单个 Task 取消则按其 required coverage 与其他 Unit 结果聚合为 PARTIAL/FAILED。`COMPLETED_LATE/FAILED_LATE` 对当前 Campaign 的 coverage 按取消处理，不能冒充成功。Campaign COMPLETED/PARTIAL/FAILED/CANCELLED 分别自动映射 PlanStep COMPLETED/PARTIAL/FAILED/CANCELLED，绝不等待人工 `/aggregate`。Coverage 至少统计资源、角色、故障域、required fact 和 capability，并约束结论范围。

数据库/仓储必须强制唯一调度权：Case 派生 Task 必须有非空 `execution_unit_id` FK 且一对一唯一；Case SourceCall 同理；standalone Task 必须 `case_id=null`。任何携带 Case lineage 却没有当前 Supervisor `lease_epoch` 的 repo create 在事务内拒绝，避免未来新增 endpoint 绕开约束。

人工和 AI 必须使用同一个 Campaign preview/create/revise/compiler。示例：

```yaml
common_baseline:
  - operation: collector:sys_metrics@v1
    selector: all_eligible
assignments:
  - role: gateway
    operation: query:service.status@v1
    selector: service_role=gateway
  - role: api
    operation: query:network.connections@v1
    selector: service_role=api
  - role: api-outlier
    operation: query:service.logs_tail@v1
    selector: outlier(service_role=api)
  - role: database
    operation: collector:filesystem.io@v1
    selector: service_role=database
```

### 6.4 Skill、Knowledge 与 MCP

Skill 是调查策略，不是固定采集序列或执行后门。确定性策略只约束 scope、risk、budget、可复用 Evidence、停止条件和输出 Schema；不能固定下一 Collector，也不能禁止模型提出规则库外新机制。

至少实现并真实使用：Linux CPU、内存、IO、网络；runtime GC/lock；容器/Swarm；集群 outlier；分布式 timeout/retry；数据库连接压力；复合因果；火焰图解释；容量评估。可合并为合理数量，但每个 Skill 必须有：

```text
skill_id / version / content_hash
positive_triggers / negative_triggers
applicable_targets / required_capabilities
hypothesis templates / missing facts / stopping conditions
allowed_operations / budget / report requirements
selection_reason
```

禁止始终加载无关 `answer_stability` 或固定 direction。每个最终 Skill 至少有正触发和负触发测试，Sidecar 关闭 Skill 后不能发现个人目录、项目目录或 Pi 默认 Skill。

Knowledge 必须有真实内容摄取、分块、索引、引用和可重复重建：

```text
knowledge_id / source / version / hash / scope / freshness
chunk_id / excerpt / citation / index_hash
```

返回可读 excerpt，不是只有标题/文件名。Knowledge 用于解释机制和提出策略，Historical Case 用于经验类比，Current Evidence 才能证明本次事故。Knowledge/MCP 文本中的指令永远不能扩大 Tool allowlist、risk 或 Case scope。

MCP 只通过 SourceRegistry/SourceGateway 暴露。Agent 提交 Missing Fact，不接触 MCP URL、Token 或原始连接器；结果统一清洗、限长、脱敏、检查新鲜度、生成 SourceCall→CaseEvidence→Projection。Source 失败生成具体 Gap，不得虚构或静默降级成功。

### 6.5 最小用户 API 与 Pi Tool Catalog

以下是本交付的 canonical URL 与语义，必须进入 OpenAPI，正式前端、Formal Public 测试和证据 runner 必须调用这些 canonical URL。为兼容旧客户端可以额外保留 alias，但不得只实现 alias、在测试中绕开 canonical URL，或让两条路径产生不同语义。用户/前端 API 至少包括：

```text
POST /api/v1/cases/{case_id}/agent/turn
GET  /api/v1/cases/{case_id}/workspace
GET  /api/v1/cases/{case_id}/events
GET  /api/v1/cases/{case_id}/events/stream

POST /api/v1/references/search
POST /api/v1/cases/{case_id}/attachments
GET  /api/v1/cases/{case_id}/evidence
GET  /api/v1/cases/{case_id}/evidence/{evidence_id}/projections
POST /api/v1/cases/{case_id}/evidence/{evidence_id}/reviews

GET  /api/v1/acquisition-operations
POST /api/v1/cases/{case_id}/campaigns/preview
POST /api/v1/cases/{case_id}/campaigns
PUT  /api/v1/cases/{case_id}/campaigns/{campaign_id}
GET  /api/v1/cases/{case_id}/campaigns/current
GET  /api/v1/cases/{case_id}/execution-units

POST /api/v1/cases/{case_id}/commands
POST /api/v1/tasks/{task_id}/cancel
GET  /api/v1/cases/{case_id}/causal-graphs
GET  /api/v1/cases/{case_id}/evidence-gaps
GET  /api/v1/cases/{case_id}/conclusions
GET  /api/v1/cases/{case_id}/recommendations
POST /api/v1/cases/{case_id}/deployment-assessments

POST /api/v1/internal/evaluation-runs/bootstrap
```

`/internal/evaluation-runs/bootstrap` 只用于 11.6 的 Formal Harness，不是普通用户功能；必须验证 Authority 授权的签名启动凭据、仅接受受保护 evaluator transport，并返回绑定当前候选和 `evaluation_run_id` 的短期 session。Development/普通使用不调用它。

Pi 只读 Tool：

```text
get_case_snapshot
list_case_evidence
get_evidence_projection
compare_evidence
list_operations
find_reusable_evidence
search_knowledge
get_hypotheses
get_causal_graph
get_evidence_gaps
```

Pi 写入的全部是 Proposal：

```text
propose_hypothesis_revision
propose_plan_revision
propose_campaign_revision
request_operation
submit_causal_graph_revision
submit_repair_recommendations
finish_investigation
```

不存在直接 `create_case_query`、直接 `call_mcp`、直接 `create_task` 或直接改 Case 状态的模型 Tool。`request_operation` 只能提出当前 Plan 中的 ACQUIRE_EVIDENCE/Campaign 变更，Supervisor 验证并编译。每个工具使用统一成功/拒绝信封，返回接受/修改/复用/冲突/stale 的明确原因和当前 revisions。

## 7. Agent Runtime、持久唤醒与 Fence

### 7.1 每个 Cycle 必须刷新 Snapshot

`start_or_resume` 不能在已有 Session 命中后直接沿用旧 context。每次 Cycle 必须从 Mini-Drop 获取并替换最新快照，再创建 ModelRequest：

```text
snapshot_id / case_id / investigation_run_id
case_command_revision / control_revision / scope_revision
plan_revision / campaign_revision / evidence_watermark
goal / logical target resources / user constraints
active Plan/Campaign/Execution summary
Evidence Inventory（artifact_type、关键 signals、projection_hash）
open EvidenceGap / current hypotheses
current CausalGraph/Conclusion
SkillBinding / KnowledgeCitation
Turn disposition / side_effect_policy / budget
```

Snapshot 必须包含写 Tool 所需的全部 CAS 值。不能要求模型提交它永远拿不到的 row/scope/plan revision，也不能通过取消 CAS 绕开问题。

UI 默认只展示一个“当前最重要动作”，不代表后端只能有固定 evidence order。模型可以维护多个候选和下一动作，依据区分度、成本、复用和风险排序；用户默认看到 active action 和紧随其后的 next action，专家模式可展开完整 Plan。

### 7.2 RuntimeBinding、Turn、事件与 AssistantMessage

```text
RuntimeBinding:
runtime_binding_id / case_id / evaluation_run_id(nullable)
deployment_epoch / active_generation / status
highest_contiguous_committed_seq / last_snapshot_id

RuntimeEvent unique key:
(runtime_binding_id, generation, seq)
```

`generation` 只能由 Mini-Drop 在数据库事务中单调分配。Sidecar 永不自报、猜测、递增或覆盖 generation；RuntimeBinding upsert 不得使 generation 或 committed watermark 倒退。Sidecar 建立/恢复连接时从 Server 返回的 generation、binding ID 和 ACK watermark 初始化下一 seq。

规范 RuntimeEvent 至少包含：

```text
case_id / investigation_run_id / evaluation_run_id(nullable) / deployment_epoch / cycle_id
trigger_turn_id(nullable) / origin_turn_id(nullable) / model_request_id
runtime_binding_id / generation / seq
event_type / tool_call_id / transport_dedupe_key / domain_effect_fingerprint
payload / occurred_at
```

Sidecar 每个 Session 只注册一次 event subscription。只有需要传输的规范事件分配 seq，不能先给 thinking/delta 分配序号再丢弃造成空洞。thinking 不保存；最终 assistant 内容必须原子写入持久 `AssistantMessage`、更新 Turn/Cycle 状态并产生 CaseEvent。`POST .../agent/turn` 的 AcceptedTurn 永远不冒充最终回答。

公开 `AssistantMessage` 固定包含：

```text
message_id / case_id / trigger_turn_id(nullable) / origin_turn_id(nullable)
cycle_id / model_request_id
content / evidence_refs[] / limitation_refs[]
conclusion_revision_id / created_at
```

同一事务顺序固定为：

```text
persist AssistantMessage
→ append assistant.message CaseEvent
→ AgentCycle = COMPLETED；仅当 trigger_turn_id 非空时 AgentTurn = COMPLETED
→ append cycle.completed；trigger_turn_id 非空时再 append turn.completed
```

Workspace/Event API 返回该公开投影，前端不解析 Pi 私有 `turn_end.message.content`。失败或超时必须产生持久 failure 状态和可重试信息，不能永远停在“正在分析”。用户 Turn 触发时浏览器断言 `assistant.message.trigger_turn_id == AcceptedTurn.turn_id`、内容不是 accepted 占位、Evidence 可反查，刷新后 message_id/content 不变且该 Turn=COMPLETED。系统 Wakeup 产生的 AssistantMessage 只关联 nullable trigger/origin turn，不得伪造新的用户 Turn，也不能错误完成已终态旧 Turn。

同一 Case 只允许一个活跃 model prompt。普通新消息可靠排队或作为受控 follow-up；控制命令立即走确定性通道。Runtime busy 不能在已经返回 accepted 后丢消息。

### 7.3 Tool Envelope 与事务 Fence

所有写 Tool 统一携带：

```json
{
  "case_id": "...",
  "investigation_run_id": "...",
  "evaluation_run_id": null,
  "deployment_epoch": 7,
  "trigger_turn_id": null,
  "origin_turn_id": "...",
  "cycle_id": "...",
  "model_request_id": "...",
  "tool_call_id": "...",
  "runtime_binding_id": "...",
  "generation": 3,
  "expected_case_command_revision": 12,
  "expected_control_revision": 4,
  "expected_scope_revision": 6,
  "expected_evidence_watermark": 27,
  "expected_plan_revision": 2,
  "expected_campaign_revision": 1,
  "expected_causal_graph_revision": 3,
  "expected_conclusion_revision": null,
  "transport_dedupe_key": "<adapter-derived>",
  "payload": {}
}
```

Gateway 在一个数据库事务内验证：

1. evaluation binding（Formal 时）、deployment epoch、runtime binding/generation 当前有效；
2. ModelRequest 仍允许运行，Turn side-effect policy 允许该工具；
3. Run 未暂停/停止/终结；
4. case command/control/scope/evidence watermark 以及该 Tool 实际修改对象的 revision 匹配；首次创建 Plan/Campaign/Graph/Conclusion 时对应 expected revision 允许且必须为 null；更新时必须匹配；
5. operation、目标、能力、风险、预算和依赖合法；
6. transport tool call 和 domain effect 尚未产生结果，或已有结果可幂等返回。

幂等采用双层 key，均不能由模型自由字符串决定：

```text
transport_dedupe_key =
  hash(runtime_binding + generation + model_request_id + tool_call_id)
  # 由 Adapter 生成，去重同一传输调用

domain_effect_fingerprint =
  hash(case/run + control/scope/plan/campaign revisions +
       assignment + resource incarnation + operation/version +
       normalized parameters + requested window)
  # 由 Server 生成，恢复后的新 tool_call 仍复用同一业务效果
```

重复调用返回持久 ToolResult，不重复产生业务对象。标准拒绝码至少包含：

```text
GENERATION_FENCED / MODEL_REQUEST_FENCED / CYCLE_FENCED
LEASE_FENCED / TURN_READ_ONLY
CASE_COMMAND_REVISION_STALE / EVIDENCE_WATERMARK_STALE
CONTROL_REVISION_STALE / SCOPE_REVISION_STALE
PLAN_REVISION_STALE / CAMPAIGN_REVISION_STALE
RUN_PAUSED / RUN_TERMINAL / TOOL_CALL_ALREADY_COMPLETED
```

`case_command_revision` 只随用户/系统的语义配置命令变化；追加 CaseEvent、AssistantMessage、RuntimeEvent 不得推进它，否则模型刚拿到 Snapshot 就会因自己的 running 事件永久 stale。Tool Gateway 只持久化 Proposal/ToolResult并校验 Turn/fence；真正编译、dispatch 和 finish commit 由持 Lease 的 Supervisor校验 lease token/epoch。Pi Tool Envelope 中不得出现 lease token。

旧 generation 已提交且 `seq <= watermark` 的重复事件返回原 ACK；新的旧代事件返回 `409 GENERATION_FENCED`，可进入 orphan audit，但不得投影到会话、改变状态、创建 Task 或触发 Wakeup。RuntimeEvent handler 还必须验证 ModelRequest/Cycle 未 `CANCELLED/FENCED`；stop/retarget 时 fence 活跃 ModelRequest，必要时旋转 generation，因此旧 `assistant.completed/turn.completed` 也不能穿透。

### 7.4 Durable Outbox、Wakeup 和 Sidecar spool

```text
DomainOutbox:
outbox_id / aggregate_type / aggregate_id
event_type / payload / dedupe_key
status = PENDING | CLAIMED | DELIVERED | DEAD
available_at / claim_token / claimed_by / claim_expires_at
attempts / last_error

RuntimeWakeup:
wakeup_id / case_id / investigation_run_id
reason / source_refs[]
control_revision / scope_revision / reason_class
from_evidence_watermark / to_evidence_watermark
status = PENDING | CLAIMED | SEALED | DELIVERED | CONSUMED | CANCELLED | DEAD
claim_token / claim_expires_at / dedupe_key
sealed_at / sealed_to_evidence_watermark / cycle_id

RuntimeWakeupSource:
wakeup_id / outbox_id / source_ref / evidence_watermark
mapped_at / unique(outbox_id)
```

- DB 与 MinIO/对象存储不假装跨系统 ACID。Worker/ingestor 先按内容哈希幂等上传 blob；随后一个数据库事务原子写 Artifact manifest、CaseEvidence、Projection 和 DomainOutbox，或从 durable task-terminal event 完成同样写入。上传后 DB commit 前崩溃只留下可 GC orphan blob；DB commit 后重试按 content hash/domain effect fingerprint 返回同一对象，不重复 Evidence/Wakeup。
- DomainOutbox 是领域事务真源；RuntimeWakeup 是 Dispatcher 从 wakeup-eligible Outbox 幂等投影的 Agent 消费项。eligible 基数固定为“一个或多个 DomainOutbox → 一个可合并 PENDING Wakeup”，以 `RuntimeWakeupSource` 持久记录每个 outbox_id 的唯一消费归属，绝不宣称一一关联。Dispatcher 在一个数据库事务中 claim Outbox、查找或创建符合 pending key 的 Wakeup、插入 mapping、提升水位/source refs，再把对应 Outbox 标 DELIVERED；mapping 已存在则幂等返回原 wakeup。已 SEALED 的 Wakeup 不再接纳 mapping，后续 Outbox 必须映射到下一条 PENDING Wakeup，因此多个 Evidence 不会因唯一键或提前 DELIVERED 而丢失。旧 revision、重复或仅审计事件必须保存确定性的 `NO_WAKEUP(reason)` dispatch outcome 后再 DELIVERED，不能伪造 mapping，也不能无限 PENDING。
- CLAIMED 超时后可由其他 consumer 重新 claim；完成必须同时匹配 claim_token，防止旧 consumer 迟到提交。
- Sidecar 不在线时 Wakeup 保留并有限重试，不能吞异常后让 sweeper 因“已有 Evidence”永远跳过。
- 唯一 pending key 使用 `(run_id, control_revision, scope_revision, reason_class)`；仅 `status=PENDING` 的行可以提升 `to_evidence_watermark=max` 并合并 source refs。Dispatcher 在一个数据库事务中 claim、固定 `sealed_to_evidence_watermark`、创建对应 Snapshot/Cycle/ModelRequest并把 Wakeup 置 SEALED；封口后到达的更高 Evidence 必须进入下一条 PENDING Wakeup，不能并入已创建 Cycle，避免 Snapshot 漏证据。PAUSED 时保持 deferred；旧 revision/stale result 只入 Evidence/audit，不唤醒；恢复后合并消费一次。
- Sidecar 按 `wakeup_id` 去重，持久接受后才标 DELIVERED；新 Cycle 消费后标 CONSUMED。
- Sidecar→Server RuntimeEvent 使用磁盘 append-only spool；Server 返回 `highest_contiguous_committed_seq`，Sidecar 只删除连续 ACK 之前的记录。
- Provider 返回后、任何 Decision/Tool 解析前，Sidecar 必须先把 canonical `ModelResponse` fsync 到同一 durable spool；只保存用户可见回答、结构化 Tool proposal、用量和恢复所需 envelope，剥离模型私有 reasoning。`provider_request_id + idempotency_key` 唯一；Server 接受后物化恰好一个 ModelResponse，Evaluator Proxy Ledger/receipt 只能旁证，不能用于 SUT 恢复。
- spool 丢失时 Server 把未完成 Cycle 标为 `RECOVERY_REQUIRED`，分配更高 generation，用 Snapshot 和已持久 ToolResult 重建。
- 崩溃注点只有以下 canonical D1-D5，7.4、M3 和 P03 均引用这些 ID，禁止各自定义另一组近似场景：D1=`ModelRequest` 已持久化并提交 Provider 后；D2=Provider response 已进入 SUT/Sidecar 自身的 durable spool、`AgentDecisionRecord` 提交前，Evaluator Proxy receipt 只作旁证且绝不能充当恢复数据源；D3=`AgentDecisionRecord/ExecutionUnit` 已提交、`Task/SourceCall` 完整关联前；D4=`TaskResult/Evidence` 已提交、对应 Wakeup ACK 前；D5=`AssistantMessage/Finish` 已提交、Runtime ACK 前。五点都必须验证恢复后领域副作用恰好一次、消息恰好一次、旧 generation 不穿透。D1/D2 网络重试必须复用同一 provider idempotency key，Provider Ledger 显式记录 retry/原 response 关联；恢复后只允许一个 canonical `ModelResponse` 和一个 `AgentDecisionRecord` 被接受。

Wakeup 到 Cycle 的唯一持久顺序：

```text
Evidence/Control DB transaction → DomainOutbox
→ Supervisor/dispatcher claim
→ DB transaction claim并封口 RuntimeWakeup 水位
   + 创建 Snapshot + AgentCycle(QUEUED) + ModelRequest(QUEUED)
→ Dispatcher 将已持久 cycle_id/snapshot_id 交给 Sidecar
→ Sidecar ACK
→ RuntimeEvent 推进 ModelRequest/Cycle
```

Sidecar 只是 Cycle 执行器，不能在收到内存 follow-up 后自行创造权威 Cycle。断电后 Mini-Drop 根据 QUEUED/RUNNING/RECOVERY_REQUIRED 重发。

Outbox/Wakeup 达最大重试进入 DEAD 时，必须把 Cycle/Run 置为 `RECOVERY_REQUIRED` 或 `WAITING_USER`，持久化 `system.error` CaseEvent，并在 Workspace 显示明确重试动作；Turn/Cycle 不能永久停在 RUNNING/WAITING_EVIDENCE。

### 7.5 暂停、停止、纠错和转向

PAUSE/RESUME/STOP/RETARGET/CORRECT_CONTEXT 均递增 `case_command_revision` 和全局 `control_revision`；RETARGET/CORRECT_CONTEXT 同时递增 `scope_revision`。`CANCEL_STEP/CANCEL_TASK` 只递增 `case_command_revision` 和目标自己的 `cancel_epoch/step_control_epoch`，不推进全局 control/scope/plan/campaign revision，避免把同 Case 其他合法在途 ExecutionUnit 的结果误判 stale；Step 的配置 revision 保持不可变，取消是独立持久运行状态。针对性取消仍必须旋转活跃 ModelRequest generation、使取消前未提交的 Tool proposal 失效，并产生 durable `TARGET_CANCELLED` Outbox/Wakeup，让下一 Snapshot 看到目标状态。

`CANCEL_STEP/CANCEL_TASK` 在同一事务中把受影响的每个非终态 ExecutionUnit/SourceCall/Task 置为 `CANCEL_REQUESTED`，为目标分配单调 cancel epoch，写入 `cancel_command_id/cancel_requested_at` 和 outbox；已经在取消事务前终态提交的对象返回 `applied=false/ALREADY_TERMINAL`，不得事后改写。Supervisor 对带 cancel marker 的 assignment/fingerprint 禁止自动重派；只有用户显式创建新的 Plan/Campaign revision 才能再次执行。Worker/Source 不支持硬取消时允许底层任务自然结束，但 result ingestion 必须在事务内按 execution/step ID 命中 cancel marker：被取消目标的原始 Artifact 和 canonical Evidence 仍按 lineage 保存，ExecutionUnit 转 `COMPLETED_LATE/FAILED_LATE`，Evidence 标 `late_after_cancel=true, stale_for_current_revision=true`，且不得计入当前 coverage、触发 Wakeup、关闭 Gap 或进入 Conclusion；未命中 cancel marker、scope/plan 仍适用的其他在途 Unit 保持原 dispatch revision 并正常计入当前结果，不因 CaseCommand 或 Model generation 变化而 stale。支持取消时也必须先出现 `CANCEL_REQUESTED`，收到确认后才转 `CANCELLED`。这些字段和状态必须在第一页 Task、Workspace、CaseEvent 与恢复扫描中来自同一真源。

暂停顺序固定：

1. 原子持久 Run=PAUSED、revision 和 `control.applied`；
2. 立即禁止 Supervisor 创建新 ExecutionUnit/Task/SourceCall；
3. 调用 Runtime `abort` 中断当前模型回合；
4. 取消当前 Run 的所有 Case 派生非终态 ExecutionUnit/Task/SourceCall，不只 `origin=AI_CASE`；standalone Drop Task 不受影响；
5. 迟到 Tool/Event/Task Result 仅审计，不推进新 revision；
6. UI 在状态持久化后显示“已暂停”，取消尚未结束时显示“正在停止采集”。

纠正/改目标先废弃旧 Plan/Campaign，取消旧执行，并持久化 `CONTROL_CHANGED` Outbox/Wakeup；`steer` 只作低延迟提示，丢失不影响最终由 Wakeup 启动的新 Cycle。停止后旧 generation 的 final assistant 不能进入有效会话。恢复只能从最新 revision 继续，已完成且仍适用的 Evidence 可复用。

迟到 Task/Source Result 不能丢失：仍按原 run/scope/resource lineage 物化为 Evidence，并标记 `stale_for_current_revision=true`；它可供历史解释，但不能触发当前 Run Wakeup、满足新 Plan 或进入新 Conclusion，除非用户/Agent 显式重新审查后复用。

### 7.6 可追踪关联链

每个用户 Turn 必须能从状态 API、日志或专家页面追踪：

```text
tenant/case/turn
→ investigation_run/cycle/model_request
→ runtime_generation/tool_call/transport_key/domain_effect_fingerprint
→ plan/step/campaign/assignment/execution_unit
→ task 或 source_call
→ artifact/evidence/projection
→ hypothesis/causal_graph/conclusion/recommendation
→ assistant_message/case_event
```

至少记录 Runtime queue/abort/recovery、Outbox backlog/retry、Evidence reuse/duplication、Plan stale reject、Task dispatch/cancel、cluster coverage、MCP/Skill selection、Provider usage 和 Policy deny。日志不可替代领域表，但必须能用 correlation IDs 定位演示失败。

## 8. 假设、复合因果、精确缺口与容量

### 8.1 Agent 必须真正参与决策

每轮可持久化不可变 `HypothesisRevision`，至少包含：

```text
hypothesis_id / revision / entity_ref / mechanism
status = PROPOSED | SUPPORTED | WEAKENED | RULED_OUT | CONFIRMED
supporting_evidence_refs[] / opposing_evidence_refs[]
missing_fact_ids[] / confidence / rationale
created_from_cycle_id / model_request_id
```

Evidence Review、scope correction 或新 Evidence 到达后，旧假设不原地改写，而是产生新 revision；用户可以看到候选如何被支持、削弱或排除。

每个 ModelRequest 输出可审计 `AgentDecisionRecord`：

```text
decision_id / cycle_id / model_request_id
observed_projection_hashes[]
hypotheses[] / opposing_evidence[]
selected_missing_fact / selection_reason
proposed_operation_or_action
alternatives_considered[] / stop_reason
provider_response_hash / tool_call_ids[]
```

`alternatives_considered` 只保存候选 ID、选择/排除的简短可审计理由和对应 Evidence/Gap，不要求也不保存私有思维链。

确定性规则可提出候选、拒绝非法计划和验证事实，但不能预先选完全部下一动作。相同首轮 Snapshot 分叉成两例、只改变第二轮 Evidence 内容时，后续 Missing Fact/Operation 必须按允许集合产生合理分叉；如果仍固定为同一 Collector，验收失败。

### 8.2 CausalGraphRevision

统一角色：

```text
PRIMARY_ROOT_CAUSE
CONTRIBUTING_FACTOR
AMPLIFIER
PROPAGATED_EFFECT
SYMPTOM
COINCIDENTAL_ANOMALY
RULED_OUT
```

CausalNode：

```text
node_id / entity_ref / mechanism / role
onset_start/end
supporting_evidence_refs[] / opposing_evidence_refs[]
confidence / role_rationale
```

CausalEdge：

```text
edge_id / source_node_id / target_node_id
relation = CAUSES | PROPAGATES | AMPLIFIES | COINCIDES | CORRELATES
mechanism / expected_lag / observed_lag
topology_path_refs[]
supporting_evidence_refs[] / knowledge_refs[]
verification_state = OBSERVED | SUPPORTED | PLAUSIBLE | UNVERIFIED | REFUTED
```

每个 Graph 是不可变 revision，提交带 `expected_causal_graph_revision` 和 `expected_evidence_watermark`。持久模型区分 `model_proposed_role/model_proposed_state` 与 `verifier_role/verifier_state`。Pi 只能提交 proposed node role、edge relation 和候选状态；最终 `verification_state`、Hypothesis `CONFIRMED` 和 Conclusion state 只能由 Verifier 写入，不能信任模型 payload。Finish 时若当前 evidence watermark 已超过 expected watermark，必须拒绝并重新验证，不能用旧快照确认结论。

`CausalGraphVerifier` 是确定性事实门禁，不是固定候选 Planner。至少验证：

- source event time、clock uncertainty 和因果先后；
- 服务/进程/宿主拓扑是否允许传播；
- Evidence target/window/incarnation 和内容谓词；
- 每条关键边是否有 Current Evidence；
- 下游异常能否解释上游，避免反向根因；
- 主要替代根因是否被区分；
- Amplifier 是否有开启/关闭、强弱或前后 epoch 的差分/反事实；
- Knowledge 只证明机制合理，不证明当前事故已发生。

Primary 和 Conclusion 只有同时满足以下条件才能 `CONFIRMED`：Primary 及 required edges 均为 OBSERVED/SUPPORTED；ClaimEvidenceBinding 的 field/extractor predicate 全部通过；coverage 达到合同；没有 blocker Gap；主要替代根因已经被区分。否则强制 `PARTIALLY_CONFIRMED` 或 `INSUFFICIENT_EVIDENCE`。

因果图默认跨 epoch 为 DAG；只有带明确正 lag、epoch/迭代和差分证据的 `AMPLIFIES` 反馈边允许成环，禁止用无时间语义的环解释所有异常。

规则库外机制可以由 Pi 提出，但必须说明实体、机制、当前支持/反对 Evidence、知识引用和可区分替代假设的 Missing Fact。

### 8.3 EvidenceGap

禁止只说“证据不足”。每个阻断 Claim 的 Gap 至少包含：

```text
gap_id / blocked_claim / required_fact
attempted_execution/task/query/source
target / requested_time_window
status / reason_code / raw_error_ref
observed_evidence
what_it_supports
what_it_does_not_support
conflicting_evidence_refs
retryable / next_best_action
```

标准原因至少包括：

```text
COLLECTION_FAILED / CAPABILITY_UNAVAILABLE / TARGET_UNRESOLVED
TARGET_OFFLINE / TIME_WINDOW_MISMATCH / CLOCK_UNCERTAIN
OBSERVATION_TOO_SHORT / ARTIFACT_MISSING / PROJECTION_FAILED
EVIDENCE_LOW_QUALITY / EVIDENCE_CONFLICT
COVERAGE_INSUFFICIENT / PERMISSION_DENIED
CAUSAL_FACT_UNSUPPORTED
```

用户可见文本必须说明：是否真的采集、失败原因、当前数据观察到什么、能支持哪个较弱判断、不能证明什么、最小下一步是什么。

### 8.4 ConclusionRevision 与 Finish

```text
conclusion_id / revision / investigation_run_id
state = CONFIRMED | PARTIALLY_CONFIRMED |
        INSUFFICIENT_EVIDENCE | NO_FAULT_FOUND
primary_root_causes[]
ranked_primary_candidates[]
contributing_factors[] / amplifiers[] / propagated_effects[]
symptoms[] / coincidental_anomalies[] / ruled_out[]
causal_graph_revision_id
claims[] / evidence_gap_ids[] / recommendation_ids[]
limitations[] / abstention_reason
created_from_cycle_id / model_request_id / verifier_version
```

`finish` 必须在一个事务完成：

1. 验证 Run、lease、generation、control/scope/plan/campaign revision；
2. 验证 Evidence 均为当前 Case 的 canonical Evidence，状态、目标、窗口、incarnation 和 projection 内容有效；
3. 验证 Claim→Evidence 和 CausalEdge→Evidence；
4. 执行 CausalGraphVerifier 与 ReportVerifier；
5. 持久化 ConclusionRevision、Claim links、Recommendations；
6. 迁移 Run 状态并写 CaseEvent/Outbox。

Claim 不能只存 Evidence ID，必须持久 `ClaimEvidenceBinding`：

```text
claim_id / evidence_id / projection_hash
field_path 或 extractor_id/version/hash
target_ref / resource_incarnation
event_window / predicate / observed_value
support_kind = SUPPORTS | OPPOSES
verifier_result
```

自然语言报告只是结构化 Conclusion 的投影。报告文本与结构化状态/根因/置信冲突时拒绝 finish。Attachment ID、legacy DiagnosisEvidence ID、Knowledge Citation 不能作为 Current Evidence 直接 finish。

### 8.5 RepairRecommendation

每条建议绑定 Cause 或 Propagation Edge：

```text
recommendation_id / cause_or_edge_ref
category = temporary_mitigation | root_fix | amplifier_control | validation
target / concrete_action / rationale
evidence_refs / prerequisites
risk / approval / expected_effect
verification_operations / success_criteria
rollback_or_failure_condition
confidence / limitations
```

必须区分临时缓解、根修复、放大治理和验证。若数据库压力是传播末端，不得把扩大连接池列为首要根修复；缺少代码栈/配置证据时要明确下一项定位动作，不能编造具体代码修改。

### 8.6 部署承载评估

DeploymentAssessment 使用独立 `DeploymentVerdict = FIT | CONDITIONAL | INSUFFICIENT_DATA | NOT_FIT`，不伪装成事故根因。

最低合同覆盖 CPU、内存、磁盘、副本、allocatable、当前 reservation、安全余量、数据新鲜度和中间计算。统一公式：

```text
available = allocatable - current_reservations - safety_margin
required = per_replica_requirement × replicas + deployment_overhead
```

CPU 使用 millicore，内存/磁盘使用 byte。峰值、N-1、调度/亲和、Quota、依赖资源若不可得，生成具体 Gap 和 `CONDITIONAL/INSUFFICIENT_DATA`；不能用瞬时利用率伪装确定可部署。Agent 可自主使用 Query/MCP 补证，但绝不执行真实部署。

## 9. 前端产品与交互合同

旧 `docs/ai_agent_ux_design.md` 中“当前不推进 UI 验收”和“后端固定唯一 next_action”的表述被本文覆盖。界面降载不等于限制 Agent 推理。

### 9.1 页面所有权

- `/` 或现有 Dashboard 永远是默认首页和 Drop 主入口。
- `/ai-diagnosis` 是第二页；普通采集不强制创建 Case。
- Pi/Provider/Sidecar/AI 页面故障时，第一页创建 Task、Worker 执行、查看结果和取消继续可用。
- 第一页和第二页共享同一原生 Task 状态与取消 API，不复制 Task 真源。
- 第二页采用 Case 列表 + 持久聊天 + 调查工作窗口；Evidence Explorer 是工作窗口中的 Tab/Drawer，不再是含义不清的第三套数据入口。

### 9.2 一致 Workspace Snapshot

新增并实际消费：

```http
GET /api/v1/cases/{case_id}/workspace
```

在同一数据库快照内返回解包后的领域对象：

```json
{
  "case_projection_version": 12,
  "revisions": {
    "case_command": 12,
    "control": 4,
    "scope": 6,
    "plan": 8,
    "campaign": 3
  },
  "case": {},
  "engine": {
    "mode": "pi",
    "availability": "READY|DEGRADED|UNAVAILABLE",
    "state": "IDLE|RUNNING|WAITING_TOOL|WAITING_USER|PAUSED|RECOVERY_REQUIRED"
  },
  "active_turn": {},
  "active_action": {},
  "next_action": {},
  "user_action_required": null,
  "plan": {},
  "campaign": {},
  "executions": [],
  "evidence": [],
  "hypotheses": [],
  "causal_graph": {},
  "evidence_gaps": [],
  "conclusion": null,
  "last_event_seq": 1234
}
```

`case_projection_version` 是 Workspace 投影自己的单调版本，不能复用 command revision、Runtime seq 或 case_event_seq。细粒度 API 可保留给专家页，但首屏不能并发拼接没有共同 projection version 的多份状态。`client.js` interceptor 已解包 `body.data`；组件读取的就是领域对象。组件测试 mock 必须使用真实解包形态，并增加从 `{code:0,data:...}` 经真实 interceptor 到组件的 Wire Contract Test，防止再次出现双层 `.data` 假绿。

`active_action/next_action` 不是 raw model suggestion，也不是旧 `investigation_directive`。Server 从已经接受的 PlanStep/Campaign/ExecutionUnit 确定性投影：

```text
action_id / kind / summary / status
source_plan_revision / source_campaign_revision
execution_unit_ids[] / completed_count / total_count
started_at / can_cancel / blocked_reason
```

多个 Assignment 并行时，默认动作应聚合为“正在检查 3 个角色的资源与连接情况（2/5 完成）”，展开后显示全部子执行，不能挑一个 Task 冒充全局状态。`next_action` 是当前已接受 Plan 中最高优先级且依赖满足的下一 Step；没有时为 null，前端和模型不能临时编造。默认只突出 active/next，完整 Plan、候选和并行执行仍持久存在并可在专家模式查看。

### 9.3 持久聊天与实时事件

```http
GET /api/v1/cases/{case_id}/events?after_seq=&before_seq=&limit=
GET /api/v1/cases/{case_id}/events/stream?after_seq=
```

- Case 使用专门单调 `case_event_seq`，数据库唯一键 `(case_id, case_event_seq)`；UUID event_id 只作实体 ID，不能充当顺序游标。默认最近 100 条并支持向上加载，不固定截断 300 条。
- SSE 的 `Last-Event-ID` 表示 case_event_seq。连接时先在数据库固定 high watermark H，replay `after_seq < seq <= H`，再从 H+1 读取 durable event store/订阅并补缝，避免“先 replay 再 subscribe”间隙丢事件；前端按 `(case_id,seq)` 去重，游标缺口时重拉 Workspace。
- 有限轮询只作断线兜底；不能以 5 秒整页轮询作为主机制。
- AcceptedTurn 只显示同一 Turn 的“正在分析”，最终 `assistant.message + turn.completed` 才显示回答。
- 历史消息和历史结果卡按事件追加，后续追问、刷新、重连或新结论不能让旧卡片消失。
- 当前状态卡可更新，历史时间线不可覆写；卡片使用 turn/step/task/evidence/conclusion 的稳定领域 ID。
- 用户消息以稳定 `client_command_id` 乐观展示，服务端确认后绑定持久 event ID。

普通时间线事件至少投影：

```text
user.message / assistant.message / turn.status_changed
runtime.status_changed / plan.revised / step.status_changed
campaign.revised / assignment.status_changed / task.status_changed
evidence.added/reviewed / hypothesis.revised
causal_graph.revised / evidence_gap.opened/resolved
conclusion.revised / control.applied/rejected / system.error
```

普通用户不应理解 Pi 私有事件类型、generation 或 seq；专家 Trace 可查看。

### 9.4 Composer、引用和会话语义

Turn 请求至少包含：

```json
{
  "client_command_id": "stable-browser-id",
  "message": "用户原文",
  "requested_disposition": "ANSWER_ONLY|INVESTIGATE|ATTACH_EVIDENCE|CORRECT_CONTEXT|DEPLOYMENT_ASSESSMENT|null",
  "references": [
    {"type": "task|collection|artifact|evidence|service|process", "id": "...", "version": "..."}
  ],
  "execute_safe_tools": true,
  "after_attach": "ANSWER_ONLY|INVESTIGATE"
}
```

同步只返回 `{accepted, turn_id, status=RECEIVED|ROUTED, duplicate}`。最终回答走事件。

`@` 输入必须提供键盘可操作搜索、结构化 Chip、逐项 accepted/duplicate/rejected/stale/evidence_ids/reason。不能仅把 `[task:xxx]` 拼入文本。用户明确选择“只解释这些数据”走 ANSWER_ONLY；只有“基于这些数据继续定位”才走 INVESTIGATE。

引用提交顺序只能选择一种并做事务测试：

1. 先完成 Attachment，收到 canonical `evidence_ids` 后再提交 Agent Turn；或
2. Turn 服务在同一服务端事务中解析、验证、附加 references，成功后才入 Runtime 队列。

禁止 Attachment 未完成就启动模型。逐项结果固定为：

```text
ref / status = ACCEPTED | DUPLICATE | REJECTED | STALE
evidence_ids[] / reason_code / display_message
```

Turn 请求增加 `after_attach = ANSWER_ONLY | INVESTIGATE`，默认 `ANSWER_ONLY`，避免用户仅引用数据就误开调查。

### 9.5 调查工作窗口

至少展示五个真实状态区：

1. 正在进行：活跃 Turn、Step、Task/Query/Source、目标、已运行时间和停止入口。
2. 接下来：有序 Step/Assignment、目的、目标、operation、风险和复用决定。
3. 依据：关键值、趋势、时间、新鲜度、来源、质量、内嵌预览和信任状态。
4. 因果演进：候选根因、支持/反证、传播边及相对上一 revision 的变化。
5. 结论与缺口：Primary、Contributing、Amplifier、Propagation、Symptoms、Coincidental、Gap 和建议。

默认用户只看问题摘要、AI 正在做什么、依据是否充分、下一步和是否需要参与；专家模式展开 revisions、coverage、Tool Trace 和 lineage。加载、无证据、部分失败、Runtime 不可用、模型限流、取消中、证据过期、coverage 不足和 409 冲突必须有明确可恢复文案，禁止空白卡片或假成功。

计划卡支持取消、删除、排序、改目标/operation、锁定和禁用；Evidence 卡支持 LOW_TRUST/EXCLUDED/RESTORED。每项操作调用真实 Command/CAS API，返回 applied/rejected/conflicts/new revision/affected tasks/runtime instruction；409 时保留用户草稿并重拉 Snapshot，不能提示成功。

统一 Command API：

```http
POST /api/v1/cases/{case_id}/commands
```

```json
{
  "client_command_id": "...",
  "command": "PAUSE|RESUME|STOP|CANCEL_STEP|CANCEL_TASK|REMOVE_STEP|REORDER_STEPS|RETARGET_STEP|LOCK_STEP|UNLOCK_STEP|DISABLE_OPERATION|ENABLE_OPERATION|REVIEW_EVIDENCE",
  "target_id": "...",
  "payload": {},
  "expected_case_command_revision": 12,
  "expected_control_revision": 4,
  "expected_scope_revision": 6,
  "expected_plan_revision": 8,
  "expected_campaign_revision": 3
}
```

响应至少包含 `command_id/applied/new_case_command_revision/new_control_revision/new_scope_revision/new_plan_revision/new_campaign_revision/affected_step_ids/affected_task_ids/runtime_instruction/conflicts`。`REORDER_STEPS` 提交完整稳定的 `ordered_step_ids[]`，禁止使用分组后数组下标；RETARGET 使用 Operation/Capability 目录选择器，禁止 `window.prompt`。`POST /api/v1/tasks/{task_id}/cancel` 是第一页和 Task 详情页使用的 canonical 取消入口；Case 所属 Task 必须由服务端映射为同一 `CANCEL_TASK` 命令、写入同一 command/outbox/event 链并回投 Case，不能形成第二套取消真源。

暂停的 UI/机器验收要求：Command 后 3 秒内出现 `control.applied` 和 abort/cancel 状态；从 PAUSE applied 到 RESUME applied 之间 Case 派生 ExecutionUnit/Task 增量为 0；运行中 AI Task 先进入 `CANCEL_REQUESTED` 再终态；恢复只使用最新 revision。STOP 后同一 Run 永久零新执行；RETARGET 后只要求旧 revision 零新执行，新 revision 可按策略继续。

人工/AI Campaign 使用 6.5 节相同领域 schema 和同一服务层，仅可信 actor/source 的产生入口不同；前端请求不得声明或伪造 `actor=AI`。preview 必须返回 resolved resources、unsupported/excluded、风险、预计 Task/SourceCall 和 Coverage；每个 Assignment 在 UI 中独立选 target/operation，create/revise 生成不可变 revision。浏览器 HAR 必须证明一次人工 Campaign 的 preview/create/revise，并证明 Workspace/SSE 读取和展示了 AI Campaign；AI Campaign 的创建证明必须来自 Runtime ToolCall、AgentDecisionRecord、`CampaignRevision(actor=AI)` 和对应 CaseEvent 的可追溯关联，而不能要求浏览器发起服务端 AI 决策。三个角色至少使用两种不同 operation，Assignment 与 ExecutionUnit/Task 一一对应。

### 9.6 AI Task 必须进入原任务页

Task 投影稳定包含：

```text
origin = USER_DROP | USER_CASE | AI_CASE
visibility = USER_VISIBLE | INTERNAL
case_id / case_title / turn_id
plan_step_id / step_revision_id
campaign_id / campaign_revision / assignment_id
execution_unit_id / risk / purpose
```

AI 调用 Collector/Query 并生成用户可查看数据时必须 `USER_VISIBLE`。不得因 `diagnosis_step_id`、`registered_probe` 或 AI 来源过滤。第一页显示“AI 调查”、Case、目的、目标、风险和状态，可跳回 Case并通过同一 API 取消；第二页由 Outbox 同步结果。

### 9.7 关键图内嵌

Evidence/Task 卡默认内嵌最关键的一个可用视图：火焰图、TopN、时间序列、eBPF Histogram、Trace 路径、日志关键段或结构化关键字段。“打开完整结果”只是次级入口。DONE 且已有可解析 Artifact 时不能只显示跳转按钮；无图时说明具体原因。Pi Query/Campaign 的 Task 与旧 Probe 共用预览映射。

## 10. 不可越级的实现门禁

下面不是分批交付，而是单次任务中的依赖顺序。可以并行处理不冲突文件，但不得用后层脚手架掩盖前层真实断点。

### M0：事实复位与审计回归

工作：

- 记录 base commit、tracked diff hash、全部拟打包未跟踪文件的内容 hash、依赖锁、实际 Pi 版本、Alembic 唯一 head 和当前服务能力。
- 新增 `audit_regressions`，在未修代码上稳定复现：Evidence 只有元数据、final 不可见、READ_ONLY 可写、Session context 陈旧、Wakeup 丢失、stop 后迟到任务、cluster selector/aggregate、MCP 未入 Evidence、finish 无 verifier、Workbench 双层 data、AI Task 过滤、Runner 假绿和 candidate hash 漏洞。
- 把旧进度和报告全部降为历史线索；状态只能由当前测试/运行自动晋级。
- 保留普通 Drop 和 deterministic 回退，关闭所有 Agent 自动执行时 Case 不产生新 Task/SourceCall。
- 以当前实际 Alembic head 创建后续迁移，确保单 head、空库升级和当前库升级。

第一批必须先失败的测试至少固定为：

```text
test_answer_only_can_run_on_terminal_run
test_propose_only_creates_no_accepted_plan_or_execution
test_case_events_do_not_invalidate_semantic_cas
test_first_plan_campaign_graph_use_null_cas
test_finish_rejects_new_evidence_watermark
test_direct_case_task_requires_execution_unit_and_lease_epoch
test_execution_unit_task_unique_under_dispatch_crash
test_campaign_terminal_auto_transitions_plan_step
test_waiting_run_can_pause
test_expired_outbox_claim_is_reclaimed
test_multiple_artifacts_coalesce_to_one_cycle
test_evidence_committed_while_paused_runs_once_after_resume
test_recovery_creates_new_cycle_and_generation
test_dead_wakeup_becomes_visible_recovery_required
test_same_generation_late_final_after_stop_is_fenced
test_domain_effect_dedupes_across_new_tool_call_and_generation
test_system_cycle_does_not_create_fake_user_turn
test_causal_verifier_owns_verification_state
test_confirmed_downgrades_when_required_edge_missing
test_finish_rejects_wrong_projection_field_or_window
test_excluded_evidence_supersedes_conclusion
test_sse_event_between_replay_and_subscribe_is_delivered_once
test_case_event_cursor_uses_monotonic_sequence
```

退出：上述失败测试真实失败且失败原因与审计一致；随后每个修复有对应机器断言。不得把“新测试文件存在”当退出。

### M1：Evidence 可读、解释回答可见

工作：

- 实现 canonical Evidence ownership、版本化 parser 和 EvidenceProjection。
- 扩展 Snapshot 与只读 Tool，使 Pi 能读取实际数值/样本/热点/Trace 路径，而不是元数据。
- 完成 Task/Collection/Artifact/User/MCP Result 的统一 Ingestion 和精确 Fingerprint。
- 持久化 AssistantMessage，完成 AgentTurn/Cycle，投影 CaseEvent；前端能看到真实 final。
- 机器级实现 ANSWER_ONLY 动态 Tool Policy 和零副作用。
- Evidence Review 级联撤销旧 Claim/Conclusion 并 Wakeup。

退出：真实 Artifact 中预置的已知数值被 Pi 通过 Projection 读到并在持久回答中正确引用；刷新后仍存在；ANSWER_ONLY 六类副作用增量为 0。

### M2：单一 Supervisor 与统一执行域

工作：

- 重构 CaseSupervisor 为唯一 lease/compile/dispatch/aggregate/control owner。
- 删除或禁用 Case 模式下 PlanDriver、Query、MCP、旧 Agent/Orchestrator 的直接 Task/SourceCall 写入口。
- 实现 Plan/Campaign/Assignment/ExecutionUnit 的持久模型、CAS 事务和 depends_on。
- 统一 OperationSpec；Collector/Query/Source 全部经 ExecutionUnit。
- 修复逻辑 Membership、capability、selector、target unresolved、incarnation 和多维 coverage。
- 自动 aggregate Campaign 和 PlanStep；人工与 AI 共用同一 Campaign API。

退出：代码搜索和动态测试都证明只有 Supervisor 能创建 Case 派生执行；standalone Drop 正常；三角色异构矩阵无需人工 aggregate 自动完成。

### M3：持久 Runtime、控制和崩溃恢复

工作：

- 每 Cycle 刷新 Snapshot，单 Session 单 subscription，单 Case 单活跃 prompt。
- 实现 AgentCycle/ModelRequest/DecisionRecord、RuntimeEvent ACK/replay、Sidecar spool、generation fence。
- 实现 DomainOutbox/RuntimeWakeup、有限重试、去重、重启重建。
- 写 Tool 全部加完整 envelope/fence/idempotency。
- pause/stop/retarget/correction 确定性更新 revision、abort/steer Pi、取消执行并隔离迟到结果。

退出：7.4 节五个规定崩溃点均可恢复，重复 Task/SourceCall/AssistantMessage 为 0；PAUSE→RESUME 窗口内和 STOP 后无新 Case 派生执行，RETARGET 后旧 revision 无新执行；旧代 final 不能污染会话。

### M4：真实 Pi 三轮自适应调查

前 M1-M3 没通过前不得开放 Pi 自动 READ_LOW。

工作：

- 用实际安装并锁定的 Pi SDK full-control 模式；不读取 `~/.pi`，禁用默认 Bash/read/write/edit/find/Skill discovery，只注册 Mini-Drop Tool。
- 把旧固定 `investigation_directive` 改为不指定下一 operation 的 Policy Context。
- Pi 根据 Evidence/Gap/Skill 选择 Query/Collector，Supervisor 决定是否接受。
- Task/Evidence→Wakeup 后创建新 ModelRequest；用户可追问、补证、纠错和中断。
- Provider/Runtime 失败时保留 Case并可切 deterministic，不损坏普通 Drop。

退出：问题驱动和数据驱动各有一条真实 E2E；至少一条三轮调查包含两个 Evidence→Wakeup；配对 Evidence fork 除目标 `EvidenceProjection` 外保持候选、初始数据库快照、上下文、故障状态、模型/Provider 参数一致，分支结果命中运行前 Oracle 冻结的 allowed assertions 且不命中 forbidden assertions。Provider 支持确定性 seed 时 A/B 必须使用同一 seed；不支持时必须额外跑至少一个 A/A control，A/A 落在同一 Oracle `action_equivalence_class` 后，A/B 才允许跨到 Oracle 规定的另一 class。A/B ModelResponse 必须结构化引用发生变化的 Evidence ID 和 field，Provider Ledger 证明该 Projection 确实进入下一请求，并证明驱动 Plan/Tool 的确是模型输出，不是装饰性调用。A/A 不稳定、只得到不同自由文本或由人工判断“看起来合理”都不能证明证据驱动分叉。

### M5：集群、Query、MCP、Skill 与 Knowledge 闭环

工作：

- 完成注册 Query 的真实 Renderer/Parser/Output limit/Capability 和正负测试。
- 接入一个真实只读 MCP Source，另做 Source unavailable 场景；结果进入同一 Evidence/Projection。
- Skill 选择记录 version/hash/reason/negative trigger/allowed operations；Knowledge 真实摄取、检索、引用和重建。
- 同构共同基线和异构 Assignment 均可由人/AI创建；部分失败和 capability 缺失精确限制结论。

退出：一个 Case 中 Collector、Query、MCP 各走一次统一 lineage；Skill 改变调查策略但不越权；Knowledge 可打开但不冒充当前事实；两个 Worker 完成差异化 Campaign 和部分失败。

### M6：复合因果、Gap、修复和容量

工作：

- 实现 Hypothesis、CausalGraphRevision、Claim、EvidenceGap、ConclusionRevision、Recommendation/VerificationPlan。
- 完成 CausalGraphVerifier/ReportVerifier 和 finish 事务。
- 用真实轻量因果栈验证 Primary、Amplifier、传播和 distractor；关键边失败时部分确认并精确 Gap。
- 容量完成 `INSUFFICIENT_DATA→FIT` 与 `INSUFFICIENT_DATA→NOT_FIT` 两个子例。

退出：DB 压力被错当 Primary 必须失败；GC 等决定性事实缺失时不能 CONFIRMED；所有建议绑定 Cause/Edge；容量公式和限制资源可复算。

### M7：前端完整产品化

工作：

- 实现 Workspace snapshot、Case event replay/SSE、真实 final、多轮历史和错误恢复。
- 接入 Evidence/Campaign/Execution/Graph/Gap/Conclusion/Capacity/Reference APIs，不以 client function 存在代替页面使用。
- 修复 Workbench 响应形态和布局；完成默认/专家两层。
- AI Task 进入第一页；人工异构 Campaign；`@` 引用；控制与 revision conflict；关键图内嵌。

退出：真实后端 Playwright 用例全部通过；网络 Trace 证明上述 API 被消费；刷新/断线/重启不丢消息或重复卡片；非技术用户与专家路径均可完成。

### M8：不可变候选、三节点与真实验收

工作：

- 构建内容寻址候选并部署 Control/Worker1/Worker2 同一 release。
- 部署并验证 Server/Web/Analyzer/Pi Sidecar/Worker、迁移和 actual package version。
- 跑 Public P01-P10、真实故障生效、恢复、清理和最终健康。
- 修复最小失败集，再逐级扩大；旧报告不能复用。
- 生成安装、配置、演示、Provider/MCP/Sidecar 故障、清理和 deterministic 回退 Runbook，并实际演练。

退出：Public required assertions 全通过，当前候选/三节点/浏览器/Provider/Evidence/Fault/Cleanup digests 一致，普通 Drop 和业务工作负载恢复健康。

## 11. 真实测试、Oracle 和反假绿合同

### 11.1 Realness 等级

| 等级 | 用途 | 允许替身 | 必须真实 |
|---|---|---|---|
| R0 | Schema、CAS、Renderer、评分器负例 | 可 Mock | 合同和确定性断言 |
| R1 | 本地纵向与崩溃恢复 | 仅 Scripted Provider | Server、DB、Sidecar、Supervisor、Worker、Task、Artifact、Evidence、Outbox |
| R2 | 不可见 Replay 推理 | 不允许模型替身 | 真实 Pi/Provider、Replay Source、Evidence/Turn/Tool 链 |
| R3 | 动态隔离栈 | 不允许模型/结果替身 | 真实服务、Worker、故障、采集、Pi/Provider |
| R4 | 最终三节点演示 | 无替身 | 当前精确候选、三节点、真实 Worker/工作负载/Pi/Provider/清理 |

`actual_realness` 由 Evaluator 根据运行证据计算，不能由 SUT 或 score 自报。R4 至少要求三节点 build-info 候选一致、真实 Provider Ledger、在线 Worker 执行原生 Task、LIVE Evidence；故障 Case 还要求独立 Observer 证明生效和清理。

Realness 偏序固定为 `R0 < R1 < R2 < R3 < R4`，但高层不能跳过低层的协议断言；例如一个 R4 smoke 不能替代 D1-D5 的 R1 崩溃注点。

### 11.2 DEMO_READY 固定 Public P01-P10

Manifest 中每条 assertion 都必须有可执行 evaluator，不允许只有描述。

| ID | 场景和硬断言 | Realness | 正式重复 |
|---|---|---:|---:|
| P01 | `conversation_and_reuse`：解释已有 Evidence 时六类副作用为 0、持久回答引用真实 Projection；刷新仍在。随后 `@Task/@Collection` 物化 canonical Evidence、重复引用去重、充分时不补采、Conclusion 可反查 lineage | R3；P09 复验 UI 部分为 R4 | 1 |
| P02 | `autonomous_single_fault`：用户只说“商城变慢，请自行定位”；至少 3 个真实 model_request、两次 Evidence→Wakeup；AI 自主运行至少一个 Query 和一个 Collector；根因和修复正确；Evidence fork 后第三轮命中 Oracle 允许分支且不命中禁止分支 | 一个 R3 变体 + 一个 R4 变体 | 预登记 2 个变体均通过 |
| P03 | `interrupt_restart_retarget`：慢任务中暂停、改目标、恢复；可取消任务进入 CANCEL_REQUESTED/CANCELLED；不可取消任务可自然结束但标 late/stale；两者都不推进旧 revision；重启恢复无重复，generation 单调且旧代完成事件被拒绝 | D1-D5 为 R1；一个真实 pause/retarget/restart 为 R4 | 1 组 R1 + 1 个 R4 smoke |
| P04 | `heterogeneous_cluster`：三个逻辑角色分布于两个 Worker，共同基线加不同 Query/Collector；同一 Worker 多资源不覆盖；一个 Worker/能力离线后 Coverage 精确列 Gap并限制结论；人工和 AI 共用 Campaign | R4 | 1 |
| P05 | `query_mcp_skill_knowledge`：四个核心 Query 经 Worker Task；Missing Fact 自主触发一个真实只读 MCP Source并形成 Evidence；Source 失败精确 Gap；Skill 版本/理由可追，Knowledge 引用可打开但不作当前事实；危险输入不越权 | 四 Query 正负矩阵 R1；一个真实 Query+MCP+Skill/Knowledge 闭环 R4 | 1 组 R1 + 1 个 R4 |
| P06 | `compound_causal`：真实 A 内存增长→GC→A 延迟→B 超时→重试放大→下游连接压力；A 为 Primary、B 重试为 Amplifier、下游不是 Primary；required edge 有证据；建议包含缓解、根修复、放大治理和验证 | 一个 R3 变体 + 一个 R4 变体 | 预登记 2 个变体均通过 |
| P07 | `precise_gap_distractor_and_healthy`：一例屏蔽 P06 决定性事实并加入更显著下游 distractor，最多 PARTIALLY_CONFIRMED且 Gap 完整；另一健康基线/正常波动必须 abstain、有界停止、不制造根因 | R3 | 2 个 required subcase |
| P08 | `capacity_two_phase`：FIT 与 NOT_FIT 两个 required subcase；首轮都遮蔽事实并返回 INSUFFICIENT_DATA，Query/MCP 补证后重算；Verdict、limiting resource、单位、中间值和容差正确，不执行部署 | R4 | 2 个 required subcase |
| P09 | `ux_end_to_end`：真实浏览器复用同一 Formal run 中 P01-P08 的 Case；多轮/刷新/SSE 不丢，AI Task 双入口可见，当前/下一工作真实，关键图和因果链内嵌，暂停/改目标/排除证据生效，非技术和专家路径可完成；UX18-UX20 使用同一 run、attempt manifest 预登记且在 run-start 后创建的专用 Case，执行并发隔离、大 Evidence Bundle、40 Turn/Compaction 三个 required subcase | R4 | 1 |
| P10 | `fallback_cleanup`：Provider/MCP/Sidecar 故障时普通 Drop 继续；deterministic 降级明确；重启 Server/Worker 无重复副作用；故障、临时文件、网络规则全部清理，最终采集和业务 Smoke 健康 | R4 | 1 |

P02/P06 使用运行前冻结的变体/nonce；故障强度、实体别名和 distractor 位置始终冻结。Evidence fork 还必须遵守 M4 的相同 seed 或 A/A action-equivalence control，不能以“Provider 不支持 seed”为由省略随机性对照。禁止失败后换变体重跑。Scripted Provider 只可用于 R1 协议测试；R3/R4 推理必须使用真实模型。任何 Case 通过旧 `/diagnoses`、直接数据库插 Evidence、测试代码直接 finish、强制告诉模型具体 Tool/operation 或预制答案，立即失败。

P03 的 D1-D5 唯一定义见 7.4；五点都在 R1 跑，R4 只做一个代表性真实重启。测试清单、fault injector、报告和 scorer 必须保存相同 ID，任何缺失、重命名或用近似崩溃点替代都使 P03 失败。

所有注册 Operation 都必须有 R0/R1 参数 schema、成功 Renderer/Parser 和越界负向测试；R4 只动态覆盖 P01-P10 实际使用的核心 Operation，不能因未在三节点逐个跑全部目录而阻塞演示。

### 11.3 复合因果实验栈

公开 P06/P07 使用一套资源可控但事实真实的服务：

```text
Control：Mini-Drop Server/Pi、固定负载发生器、独立 Observer
Worker1：runtime service-A + Drop Worker
Worker2：retrying service-B + PostgreSQL/连接消费者 + Drop Worker
```

可以从锁定 OpenTelemetry Demo compact slice、Steadybit Shopping Demo 或最小开源 overlay 组合，但不启动无关 Grafana/OpenSearch/内置 Agent。所有关键事实来自真实进程、请求、指标、日志或 span；Harness 不得向 Agent 写预制 latency/retry/connection_count。

固定五个 epoch：

```text
baseline
→ primary_only
→ primary_plus_amplifier
→ primary_with_amplifier_disabled
→ recovery
```

只有放大开启后下游影响显著扩大、关闭后显著减弱，B 才可标 Amplifier。外部项目的 repo、commit/tag、license、镜像/构建物 digest、manifest 和 fault injector 全部写入 `sources.lock.json`，禁止 `latest`。现有 Online Boutique 用于三节点业务/集群，RCAEval 锁定子集可用于 R2 Replay；正式运行前重新验证源，不把 README 当故障生效证明。

### 11.4 FaultContract

动态故障生命周期：

```text
PREPARED → INJECTING → ACTIVE → RECOVERING → CLEAN
                       ↘ INVALID
```

合同至少包含 opaque fault_id、target selector、preconditions、activation operation、独立 activation predicates、TTL、blast radius、cleanup operation/predicates 和 final health predicates。注入命令成功不等于生效；只有独立 Observer 满足事实谓词才开始评分，否则 `HARNESS_INVALID`。

演示上限：CPU 最多一个逻辑核且 ≤180 秒；单实例附加内存 ≤256 MiB 并保留宿主 1 GiB；延迟 ≤500 ms、丢包 ≤20%、≤180 秒；服务暂停/Worker 离线 ≤120 秒；磁盘只写唯一临时目录 ≤512 MiB。所有故障有外部 TTL、`finally` 和幂等 cleanup；cleanup 失败立即停止后续破坏型 Case。

P07/P08 的“屏蔽事实”必须由候选外 Evaluator 控制的 SourceGateway/capability policy 在事实进入 Agent 可见边界前实施，并作为 attempt manifest 的预冻结状态机；不能由 SUT、Prompt 或测试 fixture 直接写预期结论。机器证据必须同时证明：Agent 实际请求了该 Missing Fact；真实 Collector/Query/MCP 返回 `UNAVAILABLE/DENIED/COLLECTION_FAILED` 之一及稳定 reason；决定性事实从未进入该阶段任何模型可见 `EvidenceProjection`；distractor 是来自真实采集且模型可见的 canonical Evidence；补证阶段由 Evaluator 进行一次已记录的 capability/source 状态切换，随后 Agent 自主发起真实 Query/MCP 并获得恢复后的事实。禁止事后删除 Evidence、直接插入 Gap、在测试代码中强制返回 `INSUFFICIENT_DATA/PARTIALLY_CONFIRMED`，或把 Oracle 内容注入模型上下文；任一发生都使 Case 硬失败。

### 11.5 Oracle、评分与硬失败

Oracle schema 可以在仓库，具体 Oracle instance 只能在 evaluator。它至少表达：

```text
expected conclusion state
primary/contributing/amplifier/symptom/coincidental/forbidden primary
required/optional edges / allowed shortcuts / forbidden reversals
evidence-fork action_equivalence_classes / changed fact bindings
required facts and deterministic predicates
required EvidenceGap
repair expectations
capacity expectation
acquisition constraints / budget
fault realization / cleanup predicates
```

Fact predicate 固定 schema/version、field path 或 extractor version/hash、target alias、event window、operator/threshold、minimum samples 和 clock tolerance。核心评分不使用 LLM-as-Judge。

`FUNCTIONAL_CONTRACT/UX_CONTRACT` 必须全部 required assertion 为真。因果 Case 总分：Primary 25、角色 10、有向边 15、Evidence 目标/时间/内容 15、Gap/校准 10、修复/验证 10、采集/复用/信息增益 10、停止 5。`CAUSAL_SINGLE` 用于单 Primary 机制；`CAUSAL_MULTI` 使用同一 100 分量表，但强制至少一个非 Primary 因素及其角色（Contributing/Amplifier/Coincidental 由 Oracle 指定）、传播边和反向/下游 forbidden-primary 断言，不能退化成只猜中一个异常标签；`CAUSAL_PARTIAL` 额外强制校准与 Gap。P06 必须正确 Primary、Amplifier、required edges 和 forbidden primary；P07 不得 CONFIRMED且 Gap 完整；Evidence 引用有效率、cleanup/final health 均为 100%。

每个 Case 只有同时满足以下条件才 PASS：

```text
actual_realness >= assertion.required_realness
AND required attempts/subcases 全部存在且唯一
AND required assertions 全真
AND score >= profile threshold
AND hard_failures 为空
AND 未超预算
AND cleanup/final health 通过
```

Public Profile/阈值固定：

- P01/P03/P04/P05/P10：`FUNCTIONAL_CONTRACT`，全部 required assertion 为真；
- P02：`CAUSAL_SINGLE >= 80`，Primary 实体、机制、Evidence 引用、三轮和 allowed/forbidden branch 为必过；
- P06：`CAUSAL_MULTI >= 85`，Primary、Contributing/Amplifier 角色、required edges、forbidden primary 和四类修复/验证为必过；
- P07a：`CAUSAL_PARTIAL >= 80`，不得高于 PARTIALLY_CONFIRMED，Gap 字段全真；P07b：`HEALTHY_ABSTAIN >= 90`，根因列表为空且停止有界；
- P08：`CAPACITY >= 90`，两个子例的 Verdict、公式、单位、容差、limiting resource 全真；
- P09：`UX_CONTRACT`，UX01-UX20 全真。

Recommendation 的 category、target、Cause/Edge binding、verification operation 由结构化 Oracle 判断，自由文本相似度不作为核心评分。

任一项硬失败：错误下游被确认为 Primary；引用不存在/跨 Case/错目标/错时间/已排除 Evidence；Knowledge 当 Current Evidence；模型直接 Shell/MCP/Worker；假 Artifact；Oracle 泄漏；装饰性模型调用；决定性 Evidence 被屏蔽仍高置信 CONFIRMED；旧 diagnosis 路径冒充；cleanup/final health 失败。

### 11.6 Runner 与反假绿

`PASS` 只能由 Evaluator 根据原始断言计算，不能由 Runner、Manifest、progress 文件或人工填写。

Formal Public 必须使用候选包之外、运行期间只读的 `demo-acceptance-authority.json`。它在首个 Case 前冻结：payload/package receipt、v6 prompt digest、Public contract digest、source/environment digests、预算、repeats/变体、run nonce，以及候选外 `formal-harness-manifest.json` 的 digest。Harness Manifest 逐文件固定 formal verifier/importer、VM runner、browser scorer、fault/cleanup observer、Provider Proxy 的 binary/config digest，并固定 Provider Ledger 签名公钥和本次结果签名公钥 fingerprint；只写一个 Evaluator binary digest 不够。Authority 由宿主/用户预配置的固定 trust root 签名；正式 importer 内置或从受保护系统配置读取该 root，不接受 CLI `--public-key/--fingerprint/root`。候选、合同或 Harness 任一字节变化都必须由 trust root 重新签发新 Authority，重新计算自报 digest 不能延续旧授权。

Evaluator 在调用 SUT 前使用 Authority 授权、私钥不对 SUT 可见的 run key 签发 `run-start-receipt`，至少绑定 candidate digest、authority digest、harness manifest digest、run nonce、唯一 run namespace/tenant marker 和开始时间。Formal 中所有 Case、Task、SourceCall、Evidence、Conclusion、事件和报告必须在该 receipt 之后创建并携带同一 candidate/run nonce/namespace；Importer 显式拒绝历史 Case、旧候选 Evidence、预存 Conclusion 或跨 run 对象。P09 只允许引用本次运行 attempt manifest 记录的 Case ID：P01-P08 的复用 Case，或 P09 为 UX18-UX20 预登记并在 run-start 后创建的专用 Case。

命名固定区分：`evaluation_run_id`/`formal_namespace` 是一次 Formal Harness 运行；`InvestigationRun.run_id` 是某个 Case 内的业务调查，二者严禁复用字段或 ID。Evaluator 通过受保护 transport 调用 canonical bootstrap API，提交 `run-start-receipt + authority_digest + candidate_digest`；Server 使用宿主只读挂载的 Authority/run-key public material 验签后持久化 `EvaluationRunBinding`，返回短期、run-scoped、不可由普通浏览器自选 namespace 的 evaluation session。Evaluator-owned reverse proxy 必须剥离外部伪造的 evaluation header，并为该 session 的 API/browser 请求注入带签名或 mTLS 绑定的 `evaluation_run_id`。

Case 创建只能从已验证 session 继承 `evaluation_run_id/candidate_digest/run_nonce`，请求 payload 不能覆盖；Case 的 InvestigationRun/Turn/Cycle/ModelRequest/Decision/Plan/Campaign/ExecutionUnit/Task/SourceCall/Evidence/Event/Conclusion 通过非空父 FK 与数据库 insert guard 继承并校验同一绑定。Sidecar Snapshot/Tool Envelope、Task dispatch envelope、Worker result 和 Outbox/Wakeup 都必须携带并验证 `evaluation_run_id`，不匹配即拒绝且不投影。普通/Development 对象该字段为 null 或独立 development namespace，永远不能被 Formal importer 接受。Evaluator 的 API transcript 和 attempt manifest 记录 bootstrap receipt、binding ID 和本次 P01-P10 Case IDs；Importer 沿 FK 重算整条闭包，任何缺失、跨 namespace 或早于 run-start 的对象都使结果无效。

只要已经签发 `run-start-receipt`，候选外 Evaluator 就必须在 `finally` 中尝试 cleanup/final-health，并在每一种终态签发不可变 `terminal-score-envelope`，不能只给 PASS 签名。终态至少包括 `PASS/FAIL/ABORTED/HARNESS_INVALID/CLEANUP_FAILED/EVALUATOR_ERROR`；envelope 至少绑定 run-start digest、最终 status、score（不可评分时为 null）和退出码、全部已计划/已执行 attempt digests（包括失败 attempt）、Evidence Pack root、Provider Ledger root、fault realization root、cleanup attempt/result/root、final-health result/root、candidate/authority/harness/run nonce。Importer 必须重算所有可得 root，验证 Authority trust chain、Harness 文件、run-start 和 terminal envelope 签名及对象 namespace 后才能晋级。有效签名 envelope 报告 `CLEANUP_FAILED` 时按优先级退出 16，`ABORTED` 且 cleanup 成功时退出 18，不能因业务失败改报 15；只有 Authority/run-key/签名链或绑定 digest 本身无效才退出 15，Evaluator 在已授权情况下因内部崩溃无法产出 envelope 则退出 17并由独立 cleanup guard 保存结果，不能冒充签名错误。

无 Authority 的本地运行只能输出 `DEVELOPMENT_EVAL`，可以用于开发但不能晋级 `DEMO_READY`。该 Authority 只锁定公开合同，并不代表独立盲测；`INDEPENDENTLY_VALIDATED` 仍必须通过 11.8 的外部 Holdout。

- Formal Public Runner 只有全部 required Case 在要求 realness 下 PASS 才退出 0。
- `PARTIAL/AWAITING_*/NOT_RUN/SKIPPED/NOT_ELIGIBLE/HARNESS_INVALID` 都不是成功。
- Preflight 使用独立命令和报告，不能生成或覆盖正式 score。
- VM `check()` 必须显式 assert predicate，例如 readyz 为 true、runtime mode=pi、online workers 数正确、Task=DONE、expected tool 存在、thinking 未持久化；“未抛异常”不算 PASS。
- 每个 assertion 结果保存 expected、observed、evidence refs 和 evaluator version。
- 正式报告写唯一 run-id 目录，不允许 pytest 或开发命令覆盖 canonical 报告。
- Manifest 删除断言、降低 realness/repeats/阈值、扩大预算必须 `CONTRACT_DRIFT`。

退出码至少区分：

```text
0  all required PASS
10 functional/quality failure
11 contract drift
12 awaiting environment or not eligible
13 harness/fault invalid
14 budget exhausted
15 signature/authority/digest invalid
16 cleanup/final health failure
17 evaluator internal error
18 user aborted
```

所有退出路径先执行 cleanup。多条件并存时优先级固定：cleanup/final health 失败始终 16；Formal authority/signature/digest 无效为 15，contract drift 为 11，二者不得开始评分；fault 未生效为 13且不计 Agent 质量；环境未就绪 12；预算 14；功能/质量 10；Evaluator 内部错误 17；用户中止且 cleanup 成功 18。Public Formal、Public Development、Holdout、Preflight 分别写不同 report type 和目录；只有 Formal Public 的 0 可以晋级 `DEMO_READY`。

`conformance/` 必须是可执行反例，不是只有 `MUST_FAIL` 描述。至少覆盖：placeholder/self-sign、伪造/替换 final score、错误 profile/realness、缺/重 slot、历史/跨 run Case、旧 diagnosis、无原生 Task/SourceCall、假/跨 Case/已排除 Evidence、Scripted Provider 冒充 R4、绕过代理直连 Provider、Oracle 泄漏、cleanup 失败、candidate mismatch、PARTIAL 返回 0、合同降级、虚假 ledger/root、case FAIL 但 global VERIFIED。

### 11.7 Provider Ledger 与真实模型参与度

R2-R4 的 Provider egress 必须经过 Evaluator 拥有的观测代理，由代理捕获并签名 Ledger root；SUT 自己生成的无密钥 hash chain只能作调试，不能证明真实模型参与。Provider credential 只由代理持有，SUT 仅获得绑定 candidate/run nonce、有效期和模型 allowlist 的 scoped proxy token；测试网络策略必须阻断 SUT 直连 Provider，并由独立负向探针证明阻断。Authority/Harness Manifest 固定 proxy binary、配置、endpoint policy 和 Ledger 签名 key fingerprint。Importer 重算 Ledger chain/root并验证代理签名、key fingerprint、run namespace 和 egress coverage；缺签名、存在直连或无法解释的模型流量均为硬失败。Ledger 条目为：

```text
provider_request_id / case/turn/cycle/model_request_id
candidate digest / context snapshot hash
visible evidence projection hashes
request/response/tool-call hashes
model/provider/version / token usage / timestamps
previous_entry_hash / entry_hash
```

Evaluator 必须验证时序和内容：

```text
request[n] 确实包含 EvidenceRoot/ProjectionHashes[n]
→ provider response[n] 确实输出 proposed operation/target/parameters
→ 实际 ExecutionUnit/Task/SourceCall digest 与被接受 proposal 一致
→ 新 EvidenceRoot[n+1] 在其后形成
→ request[n+1] 确实读取该新 Evidence
```

每个驱动 Plan/Tool 的 response 必须关联 `AgentDecisionRecord→Plan/Tool→ExecutionUnit→Task/SourceCall`。三个装饰性模型调用加固定规则调度同样属于硬失败。P02 Evidence fork 的 allowed/forbidden branch assertions 在运行前由 Oracle 冻结，不以人工主观判断“看起来合理”。

### 11.8 可选独立 Holdout H01-H09

Holdout 不阻塞 `DEMO_READY`，但决定能否标记 `INDEPENDENTLY_VALIDATED`：

| Slot | 场景 | Profile | Realness |
|---|---|---|---:|
| H01 | 匿名 CPU/内存 Replay | CAUSAL_SINGLE | R2 |
| H02 | 匿名网络/超时/依赖故障 Replay | CAUSAL_SINGLE | R2 |
| H03 | 至少一个不在 rules.json 的代码/运行时机制 | CAUSAL_SINGLE | R2 |
| H04 | 动态级联的实体映射、强度或拓扑变化 | CAUSAL_MULTI | R3 |
| H05 | Primary + Amplifier + 更显著无关 distractor | CAUSAL_MULTI | R3 |
| H06 | 决定性边屏蔽、采集失败或时间反证 | CAUSAL_PARTIAL | R3 |
| H07 | 三节点资源 incarnation 变化和部分覆盖 | FUNCTIONAL_CONTRACT | R4 |
| H08 | 三节点容量缺证→补证→FIT/NOT_FIT | CAPACITY | R4 |
| H09 | 健康基线/正常波动，正确 abstain | HEALTHY_ABSTAIN | R4 |

外部只读 `acceptance-authority-v1.json` 在首个 Turn 前冻结 candidate、contract、evaluator、source lock、run nonce 和 Ed25519 key fingerprint。Authority 本身必须由候选外的固定 root 签名；正式 importer 的 root 来自内置/系统受保护配置，不能由调用者或 CLI 提供。正式 importer 只接受 `--authority <read-only-file>`；禁止任意 `--public-key`、expected fingerprint 或 root。开发 key 只能写 development 报告，永远不能升级状态。使用真正 RFC 8785 JCS、标准向量、严格 schema 和重新计算的 candidate/evidence/provider/cleanup roots。

Holdout 通过标准固定为：H01-H09 每个 slot 恰好一个有效正式结果，无缺失/重复，candidate/run nonce/required realness 一致，hard failure=0；H01-H05 Top-1 Primary 至少 4/5、Top-3=5/5、机制至少 4/5、数值因果中位分≥80、最低≥60；H04/H05 required-edge micro F1≥0.80并报告 macro；H06 正确 partial/Gap、无高置信确认、forbidden primary=0；H07 required assertions 全过；H08≥85；H09正确 abstain。签名 score 绑定每次 attempt、Oracle commitment、Evidence Pack root、Provider Ledger root和 cleanup root。缺少 Holdout 只产生 `optional_validation_status=AWAITING_EXTERNAL_HOLDOUT`，不能进入 mandatory blocked items。

所有正式 attempt 都进入签名 score，禁止 best-of。只有首个 Agent Turn 前 fault activation 独立探针失败且 cleanup 完成时，允许同 slot 按预冻结规则重试/替换一次；fault 已 ACTIVE 后的模型、Agent、Task 或结论失败必须计分。代码、Prompt、Skill、Knowledge、模型或候选发生变化时必须使用新 authority/run nonce，不能沿用旧成绩。

### 11.9 预算、停止与重复规则

默认单 Case 上限：

| 类型 | Model Turn | Tool | Task/Query | MCP | 时限 |
|---|---:|---:|---:|---:|---:|
| 解释/复用 | 4 | 10 | 2 | 2 | 10 分钟 |
| 单故障 | 8 | 24 | 10 | 4 | 25 分钟 |
| 复合因果 | 12 | 36 | 16 | 6 | 45 分钟 |
| 容量 | 8 | 24 | 8 | 6 | 20 分钟 |
| LONG_SESSION（仅 UX20） | 48 | 64 | 16 | 8 | 60 分钟 |

每 Case 模型输入输出默认 ≤120k Token、Artifact 总量 ≤100 MiB、单投影 ≤512 KiB；只有 UX20 使用显式 `LONG_SESSION` profile，模型上限 48 Turn/500k Token，不能把该例外套到其他 Case。每个 Campaign revision 解析后的不同逻辑资源数和任一 Step 派生的 ExecutionUnit 数都以 8 为硬上限；第 9 个必须在 dispatch 前以 `BUDGET_FANOUT_EXCEEDED` 拒绝并形成可见 Gap，不能把“Fanout 目标 ≤8”当软建议。Public 总计默认 ≤180 Model Turn、450 Tool、200 Task/Query、60 MCP、1.5M Token、8 小时；Manifest validator 必须证明 suite 总预算覆盖全部 required subcase/repeats 的声明上限。超过即失败，不能静默放宽。

连续两轮没有“Evaluator 可计算的有效进展”时必须停止并输出精确剩余缺口。有效进展只包括：新增一个通过 lineage/target/time/freshness 校验且 fingerprint 未出现过的 EvidenceProjection；关闭一个既有 Gap；新 Evidence 使 Oracle 定义的候选排序或因果边状态发生可验证变化；或形成一次真实、确定性的采集失败记录（operation/target/attempt/reason 完整且不是重复失败）。SUT 自报“质量提高”、改写摘要、重复采集或只增加 token 不计进展。只有故障在独立 activation probe 前未生效时，才允许完整 cleanup 后重试一次；故障已生效后的 Agent/模型失败必须计分，不能重跑选优。

## 12. 不可变 Candidate 与三节点部署

### 12.1 Candidate Manifest v2

必须避免 Manifest 自哈希循环。定义三个分离对象：

1. `payload files`：实际部署文件，排除 embedded manifest、archive 容器、deploy receipts、运行报告和非确定时间戳；
2. `candidate-manifest.json`：列 payload 清单和稳定构建元数据；
3. 包外 `package-receipt.json`：归档完成后记录 manifest digest 和 archive digest。

Candidate Manifest 必须列出实际 payload 的每个文件：

```text
release_id / base_commit
payload_files[{relative_path,file_type,mode,size,sha256,link_target}]
payload_tree_digest / tracked_diff_sha256
included_untracked_files[{path,size,sha256}]
web_dist_tree_sha256
python_lock_sha256 / sidecar_package_lock_sha256
python_resolved_inventory_sha256 / sidecar_resolved_inventory_sha256
optional_dependency_bundle_sha256
actual_pi_version / migration_head
prompt/skill/knowledge/source-lock digests
public_contract_digest / environment_profile_digest
source_date_epoch
```

```text
payload_tree_digest =
SHA256(RFC8785_JCS(sorted payload_files[path,file_type,mode,size,sha256,link_target]))

release_id = "cand-" + payload_tree_digest 的固定前缀

manifest_digest =
SHA256(RFC8785_JCS(candidate manifest，排除 manifest_digest 字段))

package-receipt = {
  release_id, payload_tree_digest, manifest_digest, archive_sha256
}
```

- release ID 只来源于全部 payload 字节，不把 manifest 自身、created_at、archive hash 或 receipt 纳入 payload digest；未跟踪内容变化必须产生新 ID。
- 构建期间工作树变化立即失败。
- 已存在同 ID archive/staging/remote release 只有 hash 完全一致才可复用，不一致必须失败，禁止覆盖。
- 解压验证必须拒绝未在 Manifest 中列出的额外 payload；symlink 必须记录并验证 link_target，或项目统一禁止 symlink；可执行 mode 必须参与 digest。
- Web hash 覆盖全部 JS/CSS/assets，不只 index.html。
- Python/Node 必须从当前 lock 安装或解析，记录三节点 resolved inventory digest并运行 `pip check`、核心 import 和 `npm ls --omit=dev`；禁止复制任意旧 release 的 site-packages/node_modules。完整离线 wheel/npm bundle 是可选加固，不是 DEMO_READY 阻断项。
- 候选不含 Secret、私有 Oracle、个人 Pi 配置、缓存或本地 SSH 凭据。
- Formal Public Authority 必须冻结 `package-receipt.json` digest；包外 receipt 本身不是信任根。

### 12.2 三节点部署

使用仓库现有 `ssh/vm-config` 和 Environment Profile；不得把地址、密码、旧 release、Python 小版本或数据库路径硬编码进脚本。典型只读入口为：

```bash
ssh -F ssh/vm-config control
ssh -F ssh/vm-config worker1
ssh -F ssh/vm-config worker2
```

部署顺序：

1. 本地 gate、Web build 和 candidate verify。
2. 三节点上传并分别验证 archive SHA。
3. 解压到新的不可覆盖目录。
4. Control 准备 Server/Web/Analyzer/Pi Sidecar；Worker1/2 准备同一候选 Worker；unit 文件只 staged，不先重启旧 active link。
5. 外部注入 env/Secret，不从任意旧 release 盲拷依赖或配置；Control 的 server env 和 Pi `sidecar.env` 都必须存在并校验必需变量，但不进入候选包。
6. 从当前数据库读取实际 Alembic head，并验证候选内 `migration-plan.json` 的 from/to head、迁移文件 digest 和回退合同。DEMO_READY 候选在“前一 release”回退窗口内只允许 `EXPAND_ONLY_ROLLBACK_SAFE` 迁移；删列、改义、不可逆数据重写必须拆到未来 contract release，否则部署前失败。这里只验证备份/恢复命令、目标空间和权限，不得把 writer 仍运行时的早期快照当最终恢复点。
7. 在数据库事务中递增持久 `deployment_epoch` 并进入 deployment fence：停止接受新的 Agent Turn 和 Case 派生 dispatch，撤销旧 Supervisor lease，fence 活跃 RuntimeBinding/generation，把未完成 Cycle 标为 `RECOVERY_REQUIRED`，保留 Wakeup；有界排空已提交领域事务。随后按依赖停止旧 Sidecar/Supervisor/Analyzer/Server 和两个 Worker，确认没有旧 release 进程继续写数据库。所有 writer 停止并完成 fsync/checkpoint 后，才创建最终 pre-migration 备份/快照，记录 digest、实际 head 和恢复命令；从该时刻到迁移结束禁止旧 writer 重启。普通 Drop 在该明确维护窗口可短暂不可用，但不得丢已持久 Task/Result。
8. 部署 runner 获取数据库 migration lock，使用新 release 的迁移代码实际执行 `alembic upgrade <candidate_expected_head>`，再从数据库重新读取并断言唯一 head、核心表/insert smoke 和迁移 digest；“验证 migration”不能只比较文件名。迁移失败时在 active link 尚未切换的前提下停止部署，按预演恢复命令还原备份并验证旧服务可启动。
9. 安装/enable unit，记录三个节点 previous release 和 link-switch timestamp，协调切换全部 active link 到新 release；任一 link 失败立即恢复所有已切换 link。
10. 切换后执行 `daemon-reload`，按 `Server → Analyzer/Supervisor → Pi Sidecar → Worker1/2 → Web` 的依赖顺序启动；逐一验证 PID start time 晚于 link switch time，进程 cwd、可执行文件和已加载代码路径均指向新 active release，实际 Pi version 匹配。新服务读取同一 `deployment_epoch`，以更高 generation 恢复持久 Cycle/Wakeup。旧 epoch 的 Runtime Tool proposal、AgentDecision、Assistant final、Supervisor lease/dispatch 一律 fence；但部署前已持久合法 ExecutionUnit 对应的 Task/Source Result 必须按 lineage/content hash 幂等接纳，随后依据当前 scope/control/cancel marker 标为 current 或 stale。部署前已持久 DomainOutbox/RuntimeWakeup 必须由新 epoch Dispatcher 认领或重建新 Cycle，保留原 source mapping，不能因 epoch 变化丢事实或吞唤醒；没有持久 ExecutionUnit/SourceCall 的孤儿结果只进 audit。
11. 任一启动、身份或健康验证失败时，停止新服务、恢复所有 active link 并重启旧 release。因为第 6 步强制 expand-only，旧 binary 必须通过针对新 schema 的预登记 smoke；若该兼容 smoke 失败则不得提前激活候选。禁止仅切 link 不重启、尝试在有新写入后盲目 downgrade，或留下新旧版本混跑。
12. 验证服务、数据库实际 head、deployment fence 已解除、实际 Pi package version、Web build ID、两个 Worker online、恢复中的 Case、普通 Drop Task 和业务工作负载。
13. 生成包含 migration/deployment epoch 和回退结果的机器 receipt，禁止手写 deploy JSON。

每个节点 receipt 至少包含：

```text
node / role / uploaded_archive_sha256
active_release_id / active_manifest_sha256 / active_release_path
service_states / actual_pi_version / web_build_id
migration_from_head / migration_head / migration_plan_digest
deployment_epoch / link_switch_at / rollback_smoke
checked_at
```

Receipt 字段按角色显式取值而不是伪造统一能力：Control 记录 `actual_pi_version/web_build_id/server_build_id`，Worker 记录 `worker_build_id/agent_binary_sha256`；不适用字段必须为 `N/A` 并附 `not_applicable_reason`，不能复制 Control 值冒充验证。

Build identity 的实现固定为：Control `GET /api/build-info`；Worker 通过 heartbeat/build-info 字段或 `mini-drop-agent --build-info`；Web 提供 `<meta name="mini-drop-build-id">` 或静态 `build-info.json`。三者的 payload tree digest、manifest digest 和 package receipt/archive hash 必须与输入候选一致，不要求 Worker 假装提供 HTTP Server。

远端先校验 archive SHA 再解压，解压后由 runner 逐文件重算 Manifest并拒绝额外文件；`/build-info` 只是交叉证明，不能代替远端重算；receipt 只能由 runner 生成。必须验证回退命令和前一 release 可解析；人为制造部署激活失败并实际回滚属于可选加固，不阻断 DEMO_READY。任何 `vm_*`、repeatability、public-case 和 demo script 都从 active manifest 解析，不得引用历史 `cand-*` 路径。VM 不可达时只能 `AWAITING_ENVIRONMENT`，不能复用旧 PASS。

### 12.3 浏览器与 VM 机器证据

P09 的 Playwright 必须使用部署后的真实 Web/API，不 mock。至少覆盖：首页/AI 第二页；真实 final；三轮历史；ANSWER_ONLY；@Task/@Collection/@Evidence；暂停/改目标/排序；AI Task 双入口；人工/AI 异构；部分失败；内嵌图；SSE/刷新/Sidecar/Server 重启；空数据/限流/409。

Public Manifest 固定以下不可删除的 UX assertions：

```text
UX01 default_route_is_drop_dashboard
UX02 ai_page_is_secondary_navigation
UX03 drop_task_works_when_sidecar_down
UX04 accepted_is_pending_not_answer
UX05 persisted_final_visible_after_refresh
UX06 three_turn_history_preserved
UX07 answer_only_zero_side_effects
UX08 task_collection_evidence_mentions_materialized
UX09 active_and_next_match_workspace_snapshot
UX10 pause_abort_and_zero_new_dispatch
UX11 reorder_retarget_conflict_visible
UX12 identical_task_id_state_and_cancel_on_both_pages
UX13 manual_and_ai_heterogeneous_campaign
UX14 inline_visual_visible_without_navigation
UX15 sse_replay_no_missing_or_duplicate_event
UX16 empty_partial_error_states_have_text
UX17 expert_mode_traces_conclusion_to_evidence
UX18 concurrent_cases_are_isolated
UX19 large_evidence_bundle_is_real_and_usable
UX20 forty_turn_compaction_preserves_history_and_lineage
```

每项记录 expected/observed/API refs/event refs/DOM refs；任一失败使 P09 FAIL，不能用截图或“整体看起来可用”综合通过。UX12 还必须证明：首页 `task.id == Workspace execution.task_id`，两页状态来自同一 Task API/Event；首页取消后同一 Task 进入 `CANCEL_REQUESTED/CANCELLED`，Case timeline 用相同 task_id 更新对应 ExecutionUnit/Step，且没有第二条镜像 Task。

UX18 必须让两个 Case 的 active interval 至少真实重叠 5 秒，且重叠期间各自至少 dispatch 一个 ExecutionUnit；每个 Case 通过各自真实 Collector/Query 产生并只可见一个绑定各自 run nonce 输入标记的 sentinel Evidence，Provider 可见 Projection、结论和引用中的跨 Case sentinel/foreign evidence 计数均为 0，禁止直接写 Evidence 表。顺序执行两 Case 不算并发。UX19 从对象真源重算并达到 `canonical_evidence_count >=100`、raw Artifact `>=32 MiB`、未压缩 Projection `>=4 MiB`、单 Projection `<=512 KiB`；Workspace/Evidence Explorer 的数量、分页、预览和引用需与真源一致，不能预置假卡。UX20 使用独立 `LONG_SESSION` 预算，至少 40 个持久用户 Turn 和对应终态 AssistantMessage，在第 20-35 Turn 间发生真实 context compaction；刷新/重启后 40 Turn 仍可分页查看，压缩后的回答至少正确引用一个压缩前 Evidence，lineage 不变且无跨 Case 内容。

保存：

```text
network.har / trace.zip / screenshots
console-errors.json / DOM domain-id snapshot
candidate/build-id
```

截图只作证据；核心判分来自 DOM、API、Event 和持久状态。正式证据包还应包含 candidate/source/environment manifests、API transcript、Provider Ledger、Turn/Runtime/Tool events、Plan-Campaign-Execution-Task map、Artifact manifest、Evidence/Conclusion snapshot、fault realization、score、cleanup 和 final health。

### 12.4 当前项目的最小安全与可用性边界

- Secret、VM 密码、API Key、私有 Oracle、个人 Pi 配置和私有思维链不进入 Git、Prompt、日志、报告或候选包。
- Sidecar↔Server 内部调用双向鉴权；模型只看到按 Turn policy 过滤后的 allowlist Tool 和严格 Schema。
- 任意 Shell、文件读写、SSH、SQL、未注册 MCP 和修改型 Query 对模型始终不可见；只读 Query 也必须经过 Worker/Supervisor。
- 外部调用有超时、取消和有限重试；禁止无限重试或静默吞错。
- 故障、压力和临时数据有范围、TTL、`finally` cleanup 和最终健康检查。
- 最终候选记录一次依赖漏洞报告即可；只有实际调用链上可直接导致任意执行、Secret 泄漏或实验环境破坏的问题阻断 `DEMO_READY`，其余作为已知限制，不要求清零。
- 并发 Case、大 Evidence Bundle 和 40 Turn/Compaction 不是游离的“压力建议”，而是 P09 的 UX18-UX20 required assertions，阈值写入 Public Manifest 并由原始对象重算；只需证明演示可用、不串 Case且 Compaction 后引用仍有效，不建设生产级 P99/灾备。

## 13. 进度、自动续跑和机器证据

维护：

```text
reports/implementation/ai-agent-runtime-state.json
reports/implementation/ai-agent-runtime-evidence.jsonl
```

状态文件至少记录：当前 M 门禁、audit regression 状态、P01-P10/UX assertion 状态、工作树/candidate digest、测试报告、VM/browser run、实际 realness、唯一下一动作、阻塞类型和恢复命令。完成状态只能由 evaluator 读取真实结果晋级，不能手工把 M/P/UX 标成 PASS。

每次工作闭环：

```text
读取 Git/状态/最近失败/候选/环境
→ 验证事实
→ 选择离最终 DoD 最近的最小失败链路
→ 建立失败测试或 Trace
→ 修改生产代码/迁移/配置/前端
→ 跑最小测试
→ 跑受影响回归
→ 跑本地总门禁
→ 更新机器证据和唯一下一动作
→ 条件满足则构建同一工作树候选
→ 部署并跑最小 VM/browser 失败例
→ 修复后逐级扩大
→ 自动领取下一项
```

至少保留并修通这些入口；不存在时实现，存在但假绿时修复：

```bash
.venv/bin/python scripts/run_local_gate.py \
  --python .venv/bin/python \
  --frontend \
  --run-id <worktree-or-candidate-id>

npm --prefix agent_runtime/pi-sidecar test

.venv/bin/python scripts/package_candidate.py \
  --build-web --verify \
  --python "$(pwd)/.venv/bin/python"

.venv/bin/python scripts/validate_agent_beta_suite.py \
  --manifest benchmarks/agent_beta/manifests/public-v2.yaml \
  --sources benchmarks/agent_beta/sources.lock.json

.venv/bin/python scripts/run_agent_beta_eval.py \
  --suite public-v2 \
  --candidate <candidate-manifest> \
  --mode formal \
  --authority <read-only-demo-acceptance-authority.json> \
  --run-id <run-id>
```

开发阶段使用单独的 `--mode development`，报告写 `reports/development/`，不得覆盖或晋级 Formal 状态。

先跑最小相关测试，再跑总门禁。不得通过删除测试、改弱断言、吞异常、默认降级成功、跳过难例或扩大预算通过。

## 14. DEMO_READY Definition of Done

只有下面全部成立才可交付：

- 第一页 Drop 始终独立可用，AI 仍在第二页。
- 两种入口共享同一持久 Case、Evidence、Plan、Campaign 和结论链。
- Pi 能读取真实 EvidenceProjection、解释已有数据且不误采集。
- Pi 真正参与至少三轮证据驱动决策，Evidence fork 会改变后续动作。
- CaseSupervisor 是唯一 Case 派生执行者；不存在多脑调度。
- Collector、Query、MCP 都经过 Operation→Campaign→ExecutionUnit，落 canonical Evidence并 Wakeup。
- READ_LOW 可连续自动执行；用户可暂停、停止、纠错、改目标、重排、补证和排除 Evidence。
- Server/Sidecar/Supervisor/Worker 重启、重复事件和迟到调用不产生重复副作用。
- 集群支持同构基线与按角色/异常/假设差异化采集；逻辑资源、能力和 coverage 正确。
- Skill/Knowledge/MCP 改善策略但不扩大权限、不替代 Current Evidence。
- 复合故障正确区分 Primary、Contributing、Amplifier、Propagation、Symptom、Coincidental 和 Ruled Out。
- 证据不足逐项说明采集、失败、已知、未知和下一动作；不使用空泛语言。
- 修复建议绑定 Cause/Edge并包含缓解、根修复、放大治理和验证。
- 容量评估完成缺证→补证→FIT/NOT_FIT，不实际部署。
- 真实 AssistantMessage、消息历史、工作状态、Evidence、图表、因果链和控制在前端可用；不存在空白 Workbench 或 Accepted 占位答案。
- 所有用户可查看的 AI Task 同时出现在第一页，状态和取消只有一个真源。
- 当前精确候选在三节点上 release/build/digest 一致；正常安装/激活、Provider/deterministic 回退、故障 cleanup 经过实际演练，部署回退命令与前一 release 可解析并验证。
- Public P01-P10 required assertions 全部 PASS；P02/P06 预登记重复均通过；不含 PARTIAL/AWAITING/NOT_RUN。
- Evidence 引用有效率=100%、无效引用计数=0；旧 revision 新执行、重复副作用、forbidden primary、任意 Shell、Secret/Oracle 泄漏和 cleanup 失败计数均为 0。
- 在 Pi、Provider、MCP 分别故障时，固定 standalone `sys_metrics`（或 Manifest 指定等价基础 Operation）均在自身配置超时内达到 DONE、Artifact 可解析且 Case 派生副作用为 0；性能数值只记录，不作生产级门禁。
- `mandatory_blocked_items=[]`，最终健康通过，Runbook 与机器报告指向当前候选而非历史 release；Holdout 仅记录在 `optional_validation_status`。

外部 H01-H09 全部签名通过后，才可在 `DEMO_READY` 之外增加 `INDEPENDENTLY_VALIDATED`；否则如实写 `AWAITING_EXTERNAL_HOLDOUT`，不影响功能完整交付。

## 15. 最终交付报告

最终报告必须简洁但可复核，至少包含：

- base commit、工作树、payload tree、manifest 和 archive digest；
- actual Pi/Provider、Prompt、Skill、Knowledge、Operation、Source 和迁移版本；
- 一条完整链路：

```text
Turn
→ AgentCycle/ModelRequest/Decision
→ Plan/Campaign/Assignment/ExecutionUnit
→ Task 或 SourceCall
→ Artifact/CaseEvidence/EvidenceProjection
→ Outbox/Wakeup
→ 新 Cycle
→ CausalGraph/Conclusion/Recommendation
→ AssistantMessage/Workspace/SSE
```

- P01-P10 的命令、realness、断言结果和证据目录；
- 三节点 receipts、浏览器 trace、故障 realization/cleanup 和 final health；
- deterministic/普通 Drop 回退结果；
- 尚未完成的外部 Holdout 状态及不影响当前 Demo 的明确边界；
- 已知限制，只能写真实存在且不破坏核心链路的限制。

如果任何核心条件不满足，继续修复，不得交付只能播放静态数据、依赖 Mock、无法连续调查、无法看到真实回答、无法打断恢复或把下游异常当根因的“假 Agent”。
