# Evidence-native Agent 统一架构

> 状态：当前融合架构与改造合同
> 固化日期：2026-08-20
> 上位产品决策：本文档
> 目标：恢复并完成 2026-08-18 的受监督诊断 Agent，同时保留新版 Evidence/Collector 能力，不恢复 rules-first RCA 主链。

## 1. 统一结论

Mini-Drop 的唯一在线主线是 Evidence-native Supervised Diagnostic Agent：模型负责维护假设与反证、识别缺失事实、提出调查计划、选择下一项已注册采集能力、分析 Evidence、建立因果解释并决定停止；确定性代码负责身份、权限、范围、预算、审批、调度、投影、引用验证和状态提交。Linux 深度采集是执行核心，不是产品能力边界。

```text
Case goal / alert / correction / existing Evidence
                    |
                    v
HypothesisRevision + EvidenceGap + InvestigationPlanRevision
                    |
                    v
Pi Agent Runtime (reasoning and information-gain decisions)
                    |
                    v
Tool Gateway + RuntimePolicy + Supervisor
  schema | scope | risk | budget | revision | generation | approval
                    |
                    v
PlanStep -> CollectionProposal -> CollectionRequest
                    |
                    v
Task -> Attempt -> Agent Collector -> Artifact
                    |
                    v
CaseEvidence -> immutable identity + versioned Projection
                    |
                    v
EvidenceAnalysisRun -> cited facts / conflicts / limits / next gap
                    |
                    v
CausalGraphRevision -> ConclusionRevision / explicit abstention
                    |
                    v
Optional RecoveryPlan -> approval -> execution -> repeated verification
                    |
                    v
Human review/correction -> stale fencing -> exact continuation or Case close
```

规则不再产生或排名根因。规则继续承担三类不可替代的职责：安全策略、事实合同验证和确定性数据投影。

## 2. 从旧设计继承什么

| 旧能力 | 决策 | 在统一架构中的位置 |
|---|---|---|
| Case 是状态真源 | 保留 | goal、scope、command/control/scope revision 和审计入口 |
| Task/Attempt/Artifact 可恢复执行 | 保留 | Collector 的物理执行底座 |
| Supervisor 单一写入权威 | 保留并收紧 | AI 不能直接创建 Task，Proposal 必须经 Supervisor 编译 |
| generation/revision fencing | 保留 | 每次写工具和最终提交都重新检查 |
| Transactional Outbox/Wakeup | 保留 | Artifact、审批、复核等事件驱动 Agent 精确恢复 |
| 原 Tool Call 审批后恢复 | 保留，已接入 CollectionProposal | 不能让模型重新生成一个“相似”调用 |
| Evidence review 与结论失效 | 保留并扩大 | review/projection 变化会使分析运行 stale |
| 重复回答稳定性 | 保留 | 使用 canonical input fingerprint 复用运行与结果 |
| 外部 Holdout、Oracle 隔离、签名 | 保留 | 正式能力声明的信任边界 |
| RulesOnlyReasoner / RCA 候选排名 | 退出在线主线 | 仅允许作为离线实验基线 |
| 受审批恢复执行与重复验证 | 保留 | 只能从 Evidence-bound Conclusion 生成，沿用 digest、operation key、rollback 与 repeated verification |
| 多套 diagnosis strategy 标签 | 退出产品主线 | 只在评测 Harness 中作为明确实现差异的实验条件 |

旧文档是需求与失败经验的来源，不是运行时真源。任何旧能力重新进入默认链路前，必须证明它不与本合同的单一权威和 Evidence 语义冲突。

## 3. 领域边界与写入所有权

### 3.1 Agent Runtime

职责：读取固定 Case Snapshot，调用受控工具，输出可审计的决策记录。它不保存私有思维链，不直接写 Task、Evidence、Conclusion 或恢复动作。

必须持久化：observed projection hashes、候选假设、反证、selected missing fact、选择理由、工具调用 ID、停止原因和 provider response hash。

### 3.2 CollectionSupervisor

职责：将一个 `CollectionProposal` 确定性编译为最多一个 `CollectionRequest` 和一个原生 Task。

提交时必须重新校验：

1. Case 与租户；
2. control/scope revision 与 runtime generation；
3. CollectorSpec、参数 schema、节点实时 capability；
4. target identity 与 resource incarnation；
5. risk、审批和 side-effect policy；
6. 请求数量、持续时间与结果大小预算；
7. input Evidence 是否存在且未排除；
8. scoped idempotency key。

所有由 AI 发起的采集最终只能通过这里创建 Task。人工普通 Task、兼容迁移和系统修复工具可以保留独立入口，但必须明确标注来源，不能伪装成 Agent 自主行为。

`InvestigationPlanService` 不得绕过该权威：普通 Plan Step 必须按 `PlanStep -> CollectionProposal -> CollectionRequest -> Task` 编译，并在 Proposal、Request、Task 上保存 `plan_step_id` 与 `plan_revision`。集群步骤继续走 MembershipSnapshot/Fanout，但服从同一 scope、risk 与 Evidence 语义。

### 3.3 Investigation State

`InvestigationStateService` 是模型提出假设、证据缺口和因果图的唯一状态入口。它验证 Case control/scope revision、ACTIVE Evidence 引用、图节点/边端点和大小上限；模型提出的角色与服务端 verifier 角色分别保存。`finish_investigation` 使用真实 blocker gap、未区分替代假设、因果边状态和 ClaimEvidenceBinding 决定最终状态。

`INSUFFICIENT_EVIDENCE` 是合法终态：只有该状态可以在零 Evidence 时提交，并且必须包含明确 abstention reason。知识与 Skill 只能进入策略上下文，不能算作当前 Evidence。

### 3.4 Evidence Store

`Artifact` 是原始执行产物，`CaseEvidence` 是 Case 内稳定身份，`EvidenceProjection` 是供模型和 UI 使用的有界确定性视图。三者不能混为一张“工具结果”记录。

Evidence 至少包含 source、target、event/ingest time、raw locator、content hash、projection hash、schema、producer、completeness、trust 和 lineage。物理删除不属于普通产品操作；人工使用 review revision 进行 `TRUSTED / LOW_TRUST / EXCLUDED / RESTORED` 治理。

### 3.5 Evidence Analysis

`EvidenceAnalysisRun` 固定以下输入：

- Evidence ID 集合；
- 每条 Evidence 的 review revision/state；
- projection ID/hash；
- analysis mode、model config 和 prompt version。

这些字段形成 canonical `input_fingerprint`。相同输入复用同一运行，不重复调用模型；任何 review revision 或 projection hash 变化都会使旧运行 stale。最终提交必须再次执行行级 fence，不能只在模型开始前检查。

每条事实必须引用 pinned Evidence 和 projection hash，并指向确定字段或文本 span。字段路径统一接受 `items.0.value`、`items[0].value` 和 `projection.items[0].value`，但最终均解析到 Projection content，不能引用模型自行生成的文本。

`LOW_TRUST` 可以用于探索和解释，但不能独立支持 HIGH certainty。`EXCLUDED` 默认不进入多证据分析或最终报告；用户仍可显式发起单 Evidence 分析，以了解为什么它被排除。

## 4. 生命周期与并发语义

### 4.1 Collection

```text
PROPOSED
  -> REJECTED                    validation/policy/budget failed
  -> PROPOSED[awaiting approval] exact proposal persisted
  -> ACCEPTED -> DISPATCHED      request and task durably created
               -> RUNNING
               -> EVIDENCE_READY
               -> FAILED/CANCELLED/FENCED
```

提案不能先标记 `ACCEPTED` 再尝试创建 Request/Task。正确提交点是 Request 与幂等 Task 已持久化之后；中途失败必须保留可恢复状态。审批恢复必须使用原 proposal ID、原参数和原 tool-call identity，并重新校验当前 scope/control revision，禁止让模型重写提案。

### 4.2 Analysis

```text
QUEUED -> RUNNING -> COMPLETED
   |         |           |
   +---------+-----------+-> STALE_INPUT (review/projection changed)
   +-----------------------> FAILED/FENCED
```

`COMPLETED + STALE_INPUT` 是合法历史：说明结果曾经完成，但现在不能作为当前事实使用。状态和输入有效性是两个正交维度，UI 与报告必须同时显示。

### 4.3 事务顺序

1. review revision 与关联分析 stale 标记在同一事务；
2. projection 内容/hash 更新与关联分析 stale 标记在同一事务；
3. analysis completion 锁定运行行并检查 status、input state 和 fingerprint；
4. CollectionRequest 预算预留由 Case 行锁保护；
5. 外部派发使用 Outbox，数据库事务内不假设网络成功。

## 5. 可恢复 Agent 循环

Agent 每个 cycle 只做一个可解释的信息决策：

```text
Observe Case + hypothesis + gap + plan + Evidence + causal snapshot
-> revise competing hypotheses and identify one decision-blocking fact
-> compare eligible collectors by information gain / risk / cost
-> revise Plan and propose one collection or analyze current Evidence
-> persist decision record
-> suspend on task/approval/review boundary
-> wake from durable event
-> rebuild snapshot and fence old generation
-> update contradictions and causal graph
-> stop with cited ConclusionRevision or explicit insufficient-Evidence result
```

恢复依赖数据库状态和 Outbox/Wakeup，不依赖 Sidecar 进程内 memory。重复事件必须由 dedupe key 和 consumer effect 表保证 exactly-once effect；模型调用本身按 request/response idempotency 记录重放。

## 6. 当前代码映射（2026-08-20）

| 合同 | 当前实现 | 完成度 |
|---|---|---|
| CollectorSpec 单一产品目录 | `mini_drop_contracts/collector_spec.py` | 已形成基线，需继续消除旧注册表漂移 |
| Proposal -> Request -> Task | `diagnosis/collection_supervisor.py` | 自动低风险与审批精确恢复已可用；Outbox 原子派发待补 |
| Hypothesis/Gap/Causal state | `diagnosis/investigation_state.py`、`v6_routes.py` | 已恢复受控提案、Evidence 引用与 revision fence |
| Plan -> Proposal lineage | `diagnosis/plan_driver.py`、migration `0028` | 单节点计划已统一经过 CollectionSupervisor；Fanout 保留 |
| Evidence/Projection | `diagnosis/case_evidence.py`、`evidence_projection.py` | Task Artifact 与 Source/MCP Envelope 均进入 canonical Evidence；继续推进 projection 真正版本化而非覆盖 |
| Analysis fingerprint/reuse/fence | `diagnosis/evidence_analysis.py`、`sql_repository_v6.py` | 已落地 |
| Citation verifier | `diagnosis/evidence_analysis.py` | 已支持 projection 前缀和数组路径；需扩展到 interpretations/conflicts |
| Review invalidation | `investigation_plan.py`、`sql_repository_v6.py` | 已落地事务级 stale 标记 |
| Pi Runtime 与安全 Tool Gateway | `agent_runtime/`、`v6_routes.py` | 已落地骨架 |
| Durable Outbox/Wakeup | v6 persistence 与 app factory jobs | 已有基础，Collector 派发尚未完全接入 |
| Supervised Workspace | `CanonicalCaseWorkspace.jsx`、Workspace aggregate API | 已合并 Plan、采集、Evidence、分析、假设、Gap、因果、结论、执行覆盖与建议 |
| 公平 Agent 评测 | `benchmarks/collector_agent_v1/` 与 replay scripts | 已形成基线，真实外部 Holdout 仍需正式执行 |

## 7. 迁移顺序与删除门禁

### Phase A：事实合同闭合

- 完成 EvidenceAnalysis fingerprint、重复复用、提交 fence 和引用路径一致性；
- review/projection 变化可靠传播 stale；
- 所有 Collector 产物形成 raw Artifact、CaseEvidence 和 Projection。

### Phase B：可恢复主动采集

- 保持 Proposal 审批后按原调用精确 resume 的 API/UI 回归门禁；
- Request/Task/Outbox 使用可恢复提交协议；
- Task/Artifact 终态投影回 CollectionRequest，并可靠唤醒 Agent；
- correction/stop/scope change fencing 覆盖所有晚到写入。

### Phase C：产品主线切换

- Case 首屏呈现调查计划、Agent decision、collection timeline、Evidence、假设/反证、Gap、因果图、cited conclusion 与受审批恢复；
- 旧 rules candidate/ranking 和独立 DiagnosisOrchestrator 写入路径默认关闭并标记 compatibility；
- README、API catalog 和前端不再把规则归因描述为 AI。

### Phase D：删除旧 RCA

只有同时满足以下条件才能删除，而不是长期双写：

1. Supervised Diagnostic Agent 主链在公开集和外部 Holdout 上通过门禁；
2. 生产入口不再读取旧 candidate/ranking 输出；
3. 必需的历史报告可通过只读适配器访问；
4. 旧表和 API 有迁移/弃用说明；
5. 回滚只切换兼容读路径，不恢复双主脑写入。

删除对象包括默认在线 `RulesOnlyReasoner`、规则候选排名、伪策略切换和自动恢复产品入口。安全校验、Evidence verifier、Collector parser 和评测基线不在删除范围。

## 8. 评测合同

正式评测比较的是 Agent 的证据获取与判断能力，不是不同工具覆盖率。

固定条件：同模型、同提示预算、同 Collector Catalog、同时间/请求预算、同初始 Evidence、同故障注入和同停止条件。比较 Mini-Drop、外部运维 Agent adapter、通用 Agent adapter 以及 rules-only baseline。

核心指标：

- required-fact coverage、collector selection precision/recall；
- citation validity、unsupported claim rate、correct abstention；
- diagnosis localization/analysis/mitigation 分项分数；
- 请求数、总持续时间、结果字节、token、延迟和失败恢复率；
- 复核/纠正后的 stale-write rejection；
- 相同输入重复运行的 tool sequence 和结论稳定性。

数据集分为开发集、冻结公开集和外部 Holdout。Holdout Oracle 与施工 Agent 隔离；manifest、runner、raw run records、scorer 和 summary 均签名或记录 hash。任何只在开发集提升、但 unsupported claim 或安全错误上升的版本不得晋级。

## 9. 不变量

1. 模型输出永远不是未经验证的数据库命令。
2. 任何当前结论都能回溯到未失效 Evidence projection hash。
3. 人工复核后，旧模型结果不能重新覆盖为 current。
4. 相同 pinned 输入不会重复消费模型。
5. 任何新增采集都受 scope、risk、budget、capability 和 revision 约束。
6. 知识库和 Skill 只能指导过程，不能充当当前事故 Evidence。
7. Evidence 不足、冲突、健康基线和采集失败必须允许正确拒答。
8. 正式评测不能由被测 Agent 读取 Oracle 或自行修改评分器。
