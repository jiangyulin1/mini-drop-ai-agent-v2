# Mini-Drop AI 功能增量设计（在 Case Agent 之上）

> 状态：历史设计参考。现行产品与行为契约以
> [`ai_diagnosis_agent_design.md`](ai_diagnosis_agent_design.md) 为准；本文不再作为新实现依据。
>
> 定位：本文是 [`ai_functional_design_v3.md`](ai_functional_design_v3.md)（生产级 Case Agent，F1–F3 已落地）
> 的**增量设计**，只写尚未实现、且与当前需求直接相关的部分；已实现内容引用 v3 与代码定位，不重复建设。
> 代码基线：GitHub `main` @ `6c90e1a`（2026-08-07 同步）。

## 0. 现状基线

| 需求（已对齐） | 现状 | 代码/文档定位 |
|---|---|---|
| Case 协作层（消息/修正/暂停/停止/结案/五块摘要） | ✅ 已实现（v3 F1） | `server/app/case_collaboration.py`、`web/src/pages/ai-workspace/` |
| ContextPacket + 模型调用审计 | ✅ 已实现（v3 F2） | `case-context.v1`、`model-attempts` API |
| 候选图 + 信息增益排序 + 停止规则 | ✅ 已实现（v3 F3） | `investigation_planner.py`、HypothesisGraph |
| 授权模型（Source/Grant/OperationClass/Impact） | ✅ 已实现 | `diagnosis/authorization.py` |
| Action Registry（分级/预检/回滚） | ✅ 已实现 | `diagnosis/action_registry.py` |
| 受控修复执行（dry-run→execute→rollback） | 🟡 仅 2 个缓存动作可执行 | `diagnosis/actuation.py` |
| 数据驱动入口 | 🟡 单任务 `source_task_id` + Web「交给 AI 分析」 | `case_collaboration.py:56`、`Dashboard.jsx:172` |
| 审批决策 | 🟡 代码级 Grant，无可配置策略表 | `authorization.py` |
| 服务拓扑 | 🟡 仅请求上下文快照，无 AI 推断 | `TopologySnapshot` |
| 修复闭环接入 Case 循环 | ❌ v3 F5/F6 未完成 | `actuation.py` 未接入 Case |
| 提案卡效果/影响展示 | ❌ 无 `predicted_effect`/`impact` 展示层 | — |
| 跨会话知识沉淀 | ❌ 仅 `knowledge/` 静态文档；v3 §9.2 未落地 | — |

**本轮只做六件事（增量 A–F），不重写 v3 已定的内核。**

---

## 增量 A：跨 Agent 多任务证据集（数据驱动入口落地）

`CreateCaseRequest` 现有 `source_task_id`（单任务），扩展为多任务证据集：

```python
source_task_id: Optional[str] = None          # 兼容保留
initial_tasks: list[str] = Field(default_factory=list, max_length=16)  # 新增
```

创建 Case 时：

1. 校验每个 `initial_tasks` 对发起用户可见、有 RESULT 产物、时间窗有效；
2. 逐任务经**证据装载**（复用 `orchestrator._add_task_evidence` / EvidenceEnvelope 路径）转成初始 Evidence 集，保留 target/时间/质量/SHA-256；
3. `build_case_context_packet` 把初始证据并入 Evidence Manifest，AI 第一步产出 ANALYZE 型提案（解读已有数据），缺口才提案 `collect`。

Web：「交给 AI 分析」由单选升级为**多选跨 Agent 任务**（结果页/数据台勾选多个 Task → 携带 `initial_tasks` 进入 Case），对应 v3 §3.1「从已有 Task 创建 Case」的多任务版。

验收：跨两台 Agent 的 CPU+内存产物一次性装载，AI 首轮解读引用两者，Verifier 通过。

---

## 增量 B：提案卡（推断作用与可能效果展示）

目标：每条待审批动作在审批界面呈现「依据 → 推断作用 → 影响面 → 成本 → 可逆性 → 置信度 → 预期价值」。

内核 `Action`/`ActionPolicyDecision` 不改，新增**服务端派生的展示层** `ProposalCard`：

```python
class ProposalCard(StrictModel):
    step_id: str
    action_id: str
    action_type: Literal["collect", "inspect", "remediation"]
    target_summary: str
    rationale: str                       # 依据：关联 evidence_refs 的可读摘要
    predicted_effect: str                # 推断作用：本动作会揭示什么/改变什么
    impact: str                          # 影响面：耗时/开销/爆炸半径（派生自 ImpactLevel）
    cost_breakdown: dict                 # latency/resource/monetary/risk/approval_wait
    reversible: bool
    confidence_level: str
    value_after_fix: str = ""            # 修复提案专用：预期恢复价值
    verification_method: str = ""        # 修复后如何验证（No-Regression）
```

- `predicted_effect`/`rationale` 由 Communicator 角色（v3 §5 逻辑角色，已有）在**已认证结构化数据之上**生成文案，不产生新事实；
- `impact`/`reversible`/`cost_breakdown` 从 `ActionDefinition`/`PolicyDecision`/`InvestigationActionCandidate` 派生，不经模型；
- Web 审批弹窗按 `rationale → predicted_effect → impact → cost → 审批` 顺序渲染（现有弹窗组件扩展字段）。

验收：任意 pending 动作在 Web 上可见四要素（依据/作用/影响/成本），且模型只改文案不改决策。

---

## 增量 C：修复执行闭环接入 Case（v3 F5/F6 落地路径）

现状：`actuation.py` 只有 `cleanup-expired-cache`/`restore-cache-quarantine` 可执行；`action_registry.py` 的
`service.drain/restart/feature-flag` 均为 `policy_only`。缺口是**闭环**：从 Case 根因到「审批→执行→验证→回滚」没有走通。

按 v3 §11 RecoveryPlan（Mitigate→Stabilize→Correct→Verify→Observe→Close）接入：

```text
Case CONVERGED（根因候选 + 证据链）
→ Recovery Planner 产出 RecoveryPlan（每步含 ActionDefinition、理由、Impact、前置、dry-run、幂等键）
→ Action Registry 评估（evaluate_action）→ Policy 判定（AUTO/APPROVAL/DENIED）
→ 单次人工审批 → 签发 Action Grant（授权模型复用）
→ ActuationGateway.execute（dry-run attempt 校验、路径/幂等/审计）
→ 会话进入 VERIFYING：复用 R1 采集 + No-Regression 判定（v3 §11.1）
→ 未恢复 → 回滚（rollback_action_id）并升级人工
→ 恢复 → 稳定观察 → RESOLVED → 沉淀 KnowledgeCandidate
```

**分两步落地，避免一次摊开**：

1. **闭环先通**：仅用现有 2 个可执行缓存动作，把「Case → RecoveryPlan → 审批 → 执行 → 验证 → 结案」整条链走通（fake/tro 节点验证）；
2. **服务级动作**：为 `service.restart-single-stateless-instance` 等实现 Agent 端 Executor（复用现有 Runner 生命周期 + argv 白名单 + `REMEDIATE` capability，按节点开通），逐步晋级。

`value_after_fix`（预期价值）与 `verification_method` 随 ProposalCard 一并展示，满足「定位到根因后给出修复命令和价值」。

---

## 增量 D：审批策略可配置化

`authorization.py` 的 Grant/决策保留为底层执行引擎，新增**策略数据层**：

```text
approval_policies:
  environment × role → action_class(COLLECT/ANALYZE/REMEDIATE_LOW/REMEDIATE_HIGH)
                      → decision(AUTO_GRANTED / USER_APPROVAL / DENIED)
```

- 策略存库、可配置、改动进审计；**只能收紧**，不能放开硬约束（R2 单次审批、R3 manual_only 等下限不变）；
- 推荐默认：production × operator → COLLECT=USER_APPROVAL、REMEDIATE_LOW=USER_APPROVAL、REMEDIATE_HIGH=DENIED；
  test × operator → COLLECT=AUTO_GRANTED、REMEDIATE_LOW=AUTO_GRANTED、REMEDIATE_HIGH=DENIED；
- Admin 面提供策略编辑页（复用 v3 §3 时间线审计）。

---

## 增量 E：服务拓扑双轨（人工 + AI 推断）

现有 `TopologySnapshot` 只来自请求上下文（人工/外部），补**推断层**：

- 来源：采样证据中的跨节点调用（call stack 里 RPC client 调用目标主机、跨主机火焰图同函数出现在不同 Agent）、Case 期间收集的调用关系；
- 存储：新 `topology_inference_edges`（from/to/置信度/来源 evidence_ref/有效期），**不得覆盖人工层**，冲突以人工层为准；
- 用途：进入 `build_case_context_packet` 的拓扑子图，供 Correlation Reasoner 判断传播路径（对应 v3 需要的 CMDB 拓扑的本地轻量替代）。

---

## 增量 F：跨会话知识沉淀 MVP

v3 §9.2 知识晋升流程已设计，落地 MVP：

```text
Case RESOLVED → 脱敏事实提取 → KnowledgeCandidate（绑定证据/版本/适用环境）
→ Owner 审批（POST /knowledge-candidates/{id}/approve|reject）
→ KnowledgeVersion 发布 → 新 Case 检索复用（只作建议，不覆盖当前证据）
```

- 复用 `knowledge/` 现有检索；`KnowledgeCandidate` 存库；
- 用户结案反馈（v3 §14 正确/错误/部分）回流到候选排序。

---

## 分阶段

| 阶段 | 内容 | 退出条件 |
|---|---|---|
| **P1** | 增量 A（多任务证据集）+ 增量 B（提案卡） | 跨 Agent 任务装载 + 审批界面四要素可见，Verifier 通过 |
| **P2** | 增量 C（修复闭环先通 2 个动作，再服务级 Executor） | 一次会话完成「根因→审批→执行→验证→回滚/结案」，No-Regression 生效 |
| **P3** | 增量 D（策略表）+ E（拓扑推断）+ F（知识 MVP） | 策略可配置审计、拓扑推断入上下文、结案可沉淀复用 |

每阶段保 `make eval` Golden 回归；新增可执行 Action 必须配回归用例与回滚测试。

---

## 待确认清单

1. **修复目录优先序**：先走通现有 2 个缓存动作的闭环，还是直接做 `service.restart-single-stateless-instance` 的 Agent 端 Executor？（建议先闭环后服务级）
2. **Agent 修复能力开通**：`REMEDIATE` capability 默认全关、按节点开通，对吗？
3. **跨 Agent 证据集范围**：`initial_tasks` 是否允许跨环境（如生产+预发对比），还是限定同环境？
4. **知识沉淀时机**：随 P2 结案即沉淀，还是推迟到 P3？
