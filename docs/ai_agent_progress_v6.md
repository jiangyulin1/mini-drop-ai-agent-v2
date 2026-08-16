# Mini-Drop v6 落地进度登记（本地可执行部分）

> 状态真源：`reports/implementation/ai-agent-runtime-state.json`
> 原则：没有 VM 服务时只登记本地/容器/构建/只读预检结果；VM、Formal Public、Holdout 不伪造。

## 当前机器事实

- Base commit：`41f41a04f94cbe19e10c7fb41061f8a42ed99637`
- Alembic 单 head：`0023_v6_agent_core`
- Python：911 passed（含新增 `tests/test_v6_audit_regressions.py` 10 项）
- Pi Sidecar：12 passed
- Web：58 passed；ESLint / production build 通过
- Full local gate：12/12 passed（`v6-local-*` 报告）
- 最新 candidate：构建命令见状态文件；release ID 以最终 `package-receipt.json` 为准

## 已完成（本地真实测试或可执行预检）

### M0/M1 Evidence 与回答闭环
- `CaseEvidenceService` 现在为每个 Task Artifact 生成确定性 `EvidenceProjection`，包含真实 signals/samples/top_items/summary/hash，不再只有元数据。
- `GET /api/v1/cases/{case_id}/evidence/{evidence_id}/projections` 和只读内部工具 `list_case_evidence/get_evidence_projection/compare_evidence` 已接通。
- Runtime `turn_end/message_end` 事件会持久化 `AssistantMessage`、完成 AgentTurn、写 `assistant.message`/`turn.completed` CaseEvent；前端渲染真实 final。
- `ANSWER_ONLY` 机器策略：Turn 记录 disposition/side_effect_policy；Tool Gateway 对 READ_ONLY 写工具返回 `TURN_READ_ONLY` 且零副作用。
- 终态 Case 允许 ANSWER_ONLY，拒绝新 INVESTIGATE。

### M2/M3 执行域与持久唤醒
- 新增迁移 `0023_v6_agent_core`：InvestigationRun/AgentCycle/ModelRequest/ModelResponse/AssistantMessage/AgentProposal/DecisionRecord/EvidenceProjection/EvidenceReviewRevision/DomainOutbox/RuntimeWakeup/RuntimeWakeupSource/Campaign/Assignment/ExecutionUnit/CausalGraph/Gap/Conclusion/ClaimBinding/Recommendation 等表。
- `CaseEvidence`、`Task`、`IncidentCase`、Runtime Binding/Turn/Event 增加 v6 lineage/revision 字段；Evidence ID 禁止跨 Case 重新归属。
- Task 完成先写 DomainOutbox + RuntimeWakeup，再创建 Snapshot/Cycle/ModelRequest 并交付 Sidecar；Sidecar 离线时 Wakeup 保留，`_runtime_wakeup_loop` 有限重试。
- 写 Tool（query/plan/finish）增加 policy/state/generation fence；STOP/PAUSED 后迟到调用被拒绝。
- `/api/v1/cases/{case_id}/commands` canonical 控制通道，PAUSE/RESUME/STOP/CANCEL_TASK/CANCEL_STEP 等写 CaseEvent/Outbox 并递增 control/case_command revision。

### M4 策略修正
- 删除固定 `evidence_order`/唯一 `next_action`；Directive 改为 Policy Context，模型可依据 Evidence/Gap/Skill 选择下一操作。
- Sidecar 每 Cycle 刷新 Context、单 Session 单订阅、按 `side_effect_policy` 动态构建 Tool Catalog。
- Tool Catalog 只包含 v6 只读 Tool 与 Proposal Tool；READ_ONLY 会话看不到 request_operation/plan/finish。

### M6 复合因果基础
- CausalGraph/Edge/Node、EvidenceGap、ConclusionRevision/ClaimEvidenceBinding、RepairRecommendation 模型与 API。
- `finish` 现在执行 ClaimEvidenceBinding 验证：projection hash、field_path/predicate、Evidence watermark；Verifier 为 `causal-report-verifier.v1`，不再接受 ID-only placeholder。
- 旧 attachment-only finish 仅窄兼容路径降级为 PARTIALLY_CONFIRMED，不冒充 verified。

### M7 前端修复
- InvestigationWorkbench 修复 Axios 双层 `.data`，组件测试 mock 改为真实 interceptor 解包形态。
- AI Task 不再因 `diagnosis_step_id` 从第一页过滤；只有 `INTERNAL` 隐藏。
- 新增 workspace/commands/projections/causal/gap/conclusion/recommendation/execution API client。
- CaseConversation 渲染 `assistant.message`，workspaceUtils 支持 v6 事件文本。
- 新增 `/api/v1/cases/{case_id}/workspace` 和 `events/stream`。

### M8 本地 Candidate
- `scripts/package_candidate.py` 重写为 Candidate Manifest v2：逐文件 mode/size/sha256/link_target、payload_tree_digest、manifest_digest、包外 receipt、web dist tree digest、迁移计划 digest、工作树变化即失败、同 ID 不覆盖。
- `scripts/import_agent_beta_score.py` 删除 `--public-key/--expected-key-fingerprint`，信任根只从受保护环境读取。
- `scripts/run_agent_beta_public_cases.py` 不再把 PARTIAL/AWAITING 当成功；环境不可达返回 12。
- `scripts/vm_agent_beta_smoke.py` 每个 check 必须返回布尔谓词，未抛异常不再算 PASS。
- `benchmarks/agent_beta/manifests/public-v2.yaml` 登记 P01-P10 与 UX01-UX20。

## 尚未完成 / 明确需要 VM 或外部 Authority

- R4 三节点部署、P01-P10 公开业务门禁、P09 浏览器、D1-D5 故障注入恢复验收：`AWAITING_ENVIRONMENT`。
- Formal Public Authority 签发、Provider Ledger 代理、独立 Evaluator/Holdout：`AWAITING_EXTERNAL_HOLDOUT`。
- VM 恢复后从 `reports/implementation/ai-agent-runtime-state.json` 的 `next_action` 继续。
