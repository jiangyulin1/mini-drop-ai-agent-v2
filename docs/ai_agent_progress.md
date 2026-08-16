# Mini-Drop AI Agent Beta 推进记录

> 历史进度记录：本文记录旧 v5 推进过程，其中大量 `[x]` 已被 2026-08-16 审计证明只完成接口/脚手架或存在假绿，不能作为当前完成度真源。
> 后续执行以 `docs/ai_agent_feature_complete_demo_prompt_v6.md` 为唯一主提示词；状态只能由当前候选的机器测试/评测结果晋级。

## 当前环境

- Commit：`41f41a04f94cbe19e10c7fb41061f8a42ed99637`
- 分支：`main`
- VM：可用（control/worker1/worker2，SSH `ssh -F ssh/vm-config`）
- 本地测试基线：Python `901 passed`；Sidecar `10 passed`；Web `58 passed`；Full local gate `12 passed`

## 推进原则

- 没有 VM 时继续完成本地、容器、候选构建和只读预检；
- 不伪造 VM 验收、Holdout 或独立盲测结果；
- 每个改动尽量带测试或本地验证；
- 进度写入本文件、`reports/implementation/ai-agent-runtime-state.json` 和 Evidence JSONL。

## 已完成 / 进行中

### G0：本地基线与状态记录

- [x] 本地状态文件与 Evidence JSONL
- [x] `benchmarks/agent_beta/contracts/public-contract-v1.json`、schema、6 个必失败 fixture、source lock、traceability matrix
- [x] `scripts/validate_agent_beta_suite.py`、`run_agent_beta_eval.py`、`import_agent_beta_score.py`
- [x] `tests/test_agent_beta_suite.py`
- [ ] 正式独立盲测、外部验签（需外部 Evaluator，阻塞）

### G1：Pi SDK 与内部协议 Contract

- [x] Pi 版本统一为实际安装的 `0.83.0`，并新增一致性测试
- [x] Sidecar 工具调用携带 `X-Internal-Token`
- [x] Sidecar case routes 在配置 Token 后强制鉴权
- [x] Python Adapter 到 Sidecar 也携带同一 Token
- [x] Sidecar `/shadow-plan` 路由（credential-free，模型不可用也可返回结构化计划）
- [x] Plan Tool Schema 补全 case/scope/plan revision、target_refs、hypothesis_refs、selection_strategy
- [x] Sidecar 事件回传：丢弃 thinking、seq<=0 拒绝、客户端 idempotency dedupe
- [x] Sidecar `submitTurn` 区分 `pi` / `pi_shadow`，shadow 不触发 prompt
- [ ] Pi SDK 官方类型与 full-control ResourceLoader 的完整证明（依赖可用 Pi 版本与 Provider）

### G2：只读 Shadow Agent 纵向切片

- [x] `/api/v1/cases/{case_id}/agent/turn` 在 PI/PI_SHADOW 模式接入 `AgentRuntimePort`
- [x] Sidecar 不可用 fail-closed，记录 `agent_runtime_turn_rejected`
- [x] `deterministic` 模式旧链路保持
- [x] 迁移 `0021_agent_runtime`：binding/turn/event 持久化
- [x] RuntimeBinding 恢复时 generation 单调递增（CaseContextSnapshot 携带下一次 generation）
- [x] `GET /api/v1/cases/{case_id}/agent/runtime-state`
- [ ] `ANSWER_ONLY` 机器断言和 Shadow Plan 质量门禁（仍待 Sidecar 真实模型闭环）

### G3：已有数据统一进入 Evidence

- [x] 迁移 `0022_case_evidence`：canonical Case Evidence Store
- [x] Task Artifact → Case Evidence 自动物化（attach 与 task wake 两条路径）
- [x] Attachment evidence_ids 更新为真实 Evidence ID
- [x] `internal_tool_finish` 校验 canonical evidence，被 EXCLUDED 的 Evidence 不可引用
- [x] EXPLAIN/STATUS 回答增加 side_effect_delta 机器断言（plan/task/fanout 增量为 0）
- [x] `finish` 结论草稿持久化到 Case current_finding
- [x] `GET /api/v1/cases/{case_id}/evidence`（Evidence Explorer 后端）
- [x] Evidence Review `EXCLUDED` 同步 canonical store
- [ ] 旧 DiagnosisEvidence 的全部兼容投影与迁移

### G4：差异化 Acquisition Campaign 与 Query Gateway

- [x] `QueryOperationRegistry`：process.list / system.metrics / service.connection / service.logs
- [x] Query 目录 API 与 Case Query API；全部编译为原生 Task
- [x] Sidecar `create_case_query` 受控工具 + FastAPI internal tool endpoint
- [x] Campaign API：common baseline + heterogeneous assignments 编译为 InvestigationPlan
- [x] Sidecar `create_case_query` 工具与 FastAPI internal tool endpoint
- [x] `GET /api/v1/cases/{case_id}/campaigns/current` 矩阵投影
- [ ] MembershipSnapshot 成员从 Agent 升级为逻辑 Target Resource（同一 Worker 多服务）
- [ ] Query renderer/parser/output limit 完整实现

### G5：持久 Plan、控制与事件闭环

- [x] `MINI_DROP_AGENT_AUTO_READ_LOW` 约束后台自动扫描
- [x] Sidecar case routes 配置 Token 后强制鉴权，Python Adapter 同步发送 Token
- [x] Sidecar shadow turn 不触发 prompt，pi_shadow 返回独立模式
- [x] 取消 Step 同步取消原生 Task / Fanout 子任务
- [x] 运行中 retarget 先取消旧 Task
- [x] 停止/解决 Case 取消所有 Case 派生活跃 Task
- [x] Task 完成唤醒 Pi：物化 Evidence → `follow_up` 到 Runtime
- [x] 新增 `tests/test_agent_runtime_local_loop.py`：无真实模型的 T1 本地纵向链（Turn→Query→Task→Evidence→Wakeup）
- [ ] Durable Outbox 去重消费与崩溃恢复的完整压力测试
- [ ] 迟到 tool/event 的 generation fencing 完整测试

### G6-G11：后续阶段

- [ ] G6 Pi 驱动 READ_LOW 连续调查（本地已有 query/task/wake 链路，缺真实 Provider 闭环）
- [x] G7 Skill / Knowledge 首版落地（MCP 自动路由和完整集群闭环仍待后续）
- [ ] G8 复合因果推断、EvidenceGap、修复建议
- [x] G9 部署承载评估：P11 公式 + 独立 `/deployment-assessment` 端点
- [ ] G10 前端产品化（已补 runtime/query/campaign/evidence API 与事件渲染）
- [ ] G11 三节点演示环境收敛与最终交付（依赖 VM）

## 本轮代码改动

- `agent_runtime/pi-sidecar/src/server.mjs`：shadow-plan、双向 Token 鉴权
- `agent_runtime/pi-sidecar/src/runtime.mjs`：shadow-plan、事件转发与去重、generation 递增、shadow turn 隔离
- `agent_runtime/pi-sidecar/src/tools.mjs`：Plan schema、`X-Internal-Token`、create_case_query 工具
- `agent_runtime/pi-sidecar/test/server.test.mjs`：新增 auth、shadow-plan、schema、header、event 测试
- `server/app/agent_runtime/config.py`：版本 `0.83.0`
- `server/app/agent_runtime/pi_adapter.py`：版本统一、向 Sidecar 发送内部 Token
- `server/app/agent_runtime/port.py`：`CaseContextSnapshot` 携带 runtime_generation/session_id
- `server/app/diagnosis/agent_runtime.py`：`DeploymentRequirements` 增加 overhead；P11 公式；`AgentTurnResult` 新状态
- `server/app/diagnosis/case_evidence.py`：新增 canonical Case Evidence Service
- `server/app/diagnosis/evidence_attachments.py`：attach 后自动物化 Evidence
- `server/app/diagnosis/investigation_plan.py`：EXCLUDED review 同步 canonical store
- `server/app/diagnosis/query_registry.py`：新增 Query Operation Registry
- `server/app/diagnosis/campaign.py`：新增 Campaign 编译服务
- `server/app/main.py`：
  - PI/PI_SHADOW turn 路由、runtime-state、runtime events、query/campaign/evidence API
  - finish 证据校验与结论持久化
  - task wake 物化 Evidence 并唤醒 Pi
  - 取消/retarget/stop 同步取消原生 Task
  - AUTO_READ_LOW 后台门禁
  - P11 容量公式
- `server/app/models.py` / `sql_repository.py`：runtime 持久化模型与方法、case evidence 模型与方法
- `migrations/versions/0021_agent_runtime.py`、`0022_case_evidence.py`
- `web/src/api/client.js`：runtime-state、evidence、query、campaign API
- `web/src/pages/ai-workspace/workspaceUtils.js`：新事件文本
- `web/src/pages/ai-workspace/CaseConversation.jsx`：Runtime Turn 事件消息渲染
- `tests/`：新增/更新 `test_agent_runtime_turn_endpoint.py`、`test_case_evidence.py`、`test_query_gateway.py`、`test_campaign.py`、`test_agent_beta_suite.py`、`test_pi_runtime_contract.py`、`test_agent_runtime.py`、`test_plan_driver.py`、`test_agent_tool_gateway.py`、`test_database.py`

## 验证

- [x] Python 全量测试：`901 passed`
- [x] Sidecar Node 测试：`10 passed`
- [x] Web 测试：`58 passed`
- [x] Ruff：通过
- [x] Web ESLint：通过
- [x] Migration drift check：通过
- [x] Agent Beta suite validator：通过
- [x] Full local gate（含 frontend）：`12 passed`

## Skill / Knowledge / 稳定性补充

- [x] 网络调研：通过 VM GitHub API 检索 `anthropics/claude-plugins-official`、`VoltAgent/awesome-agent-skills`、`openai/openai-cs-agents-demo`、`langchain-ai/langgraph`、`ClawProBench` 等
- [x] 调研记录：`docs/ai_agent_stability_research.md`
- [x] 新增 `server/app/diagnosis/skill_registry.py`：8 个版本化 Skill，正/负触发与停止条件
- [x] 增强 `server/app/diagnosis/knowledge.py`：中文二元组/单字检索，修复“CPU 用户态高”匹配失败
- [x] CaseContextSnapshot 增加 `knowledge_context` / `skill_context`
- [x] Pi 系统提示词与 CaseContext 注入 Skill/Knowledge
- [x] VM 设置 `MINI_DROP_PI_THINKING_LEVEL=high`
- [x] 新增 `tests/test_skill_knowledge.py`：4 个测试
- [x] 新增 `server/app/diagnosis/investigation_directive.py`：
  - 对目标/范围/已有证据/Skill 生成确定性 `directive_key`
  - 固定 `evidence_order` 和唯一 `next_action`
  - 时间窗、Case ID、Task ID 被归一化，不改变 directive
- [x] Sidecar 系统提示词与 Turn Prompt 注入 Binding Directive，禁止模型自行改变证据顺序或引入新方向
- [x] 新增 `scripts/vm_pi_cross_case_stability.py`，三个不同时间窗的独立 Case：
  - `directive_key` 完全一致：`84cce1074a9967af`
  - 三次均只给出 `collect sys_metrics`
  - `next_action_mentioned = [true, true, true]`
  - `tool_sequence_identical = true`
  - `direction_consistent = true`
- [x] 新增 `scripts/vm_pi_repeatability.py`，真实 Pi 同问题重复 3 次：
  - 首轮后注入 `previous_answer`，重复轮显式复用既有结论
  - run1 vs run2/run3 Jaccard：`0.915 / 0.915`
  - run2 vs run3 Jaccard：`1.000`
  - 工具序列一致：`true`
  - 三轮结论均为“无 Evidence 时明确 abstain”，没有方向分叉
- [x] 重复性报告：`reports/implementation/vm-pi-repeatability.json`

## 复杂跨功能测试集合

- [x] 新增 `tests/test_agent_beta_cross_feature.py`，5 个跨 G 阶段场景：
  1. 数据驱动入口 → Evidence 物化 → 解释不误采集 → 重复 @ 去重 → 排除/恢复 → finish
  2. PI Turn → 内部 Query Tool → 原生 Task → Evidence → Runtime FollowUp → Stop 取消 Task
  3. Campaign → PlanDriver → 集群 Fanout → Step 取消传播 → stale plan revision 拒绝
  4. Runtime 事件摄入：thinking 丢弃、幂等去重、binding last_event_seq 更新
  5. Query 幂等 key 复用与危险参数拒绝
- [x] 修复测试暴露的缺陷：Evidence Review `RESTORED` 现在会把 canonical evidence 从 `EXCLUDED` 恢复为 `ACTIVE`
- [x] 新增 `scripts/vm_agent_beta_smoke.py`，可在三节点 VM 上自动验证 15 项 smoke checks
- [x] VM smoke 实际跑通，包含真实 Pi 闭环：
  - `query-native-task` / `query-evidence-materialized`
  - `pi-turn-accepted` / `pi-native-task` / `pi-evidence-materialized`
  - `pi-tool-calls` / `pi-no-thinking`
- [x] VM smoke 报告：`reports/implementation/vm-agent-beta-smoke.json`

## 三节点 VM 部署与验证

- [x] 候选包：`cand-41f41a04f9-5d44e0e708`
  - Commit：`41f41a04f94cbe19e10c7fb41061f8a42ed99637`
  - Archive SHA-256：`492f3bfdb6393751afda2ae0c5b99766a9ebdd7399d83a8cb64219b3fbe3a8b8`
- [x] Control 服务：
  - `mini-drop-server` / `mini-drop-analyzer` / `mini-drop-s3` active
  - `mini-drop-pi-sidecar` 已创建并 active
  - `MINI_DROP_AGENT_RUNTIME=pi`
  - Pi `0.83.0` + `deepseek-v4-flash`
- [x] Worker1/Worker2 已部署当前候选，`mini-drop-agent` active
- [x] 数据库迁移 head：`0022_case_evidence`
- [x] `readyz=true`，两个 Worker ONLINE
- [x] 普通采集 E2E：`sys_metrics` 任务 DONE + Artifact 可读
- [x] Query Gateway：`process.list` 原生任务 DONE
- [x] Campaign：共同基线 + 异构 Assignment 编译为 Plan
- [x] Canonical Evidence：Task Artifact 物化、排除同步、Evidence API 可用
- [x] 真实 Pi 闭环验证：
  - Turn → `get_case_snapshot` → `find_reusable_evidence` → `create_case_query`
  - Pi 创建的原生 `system.metrics` Task DONE
  - Task 完成后自动物化 Case Evidence
  - Runtime 事件持久化 45 条，无 private thinking
- [x] VM 全量 pytest：`887 passed, 4 skipped`（跳过项为依赖本机 `pi` CLI 的测试）
- [x] VM 部署报告：`reports/implementation/vm-deploy/cand-41f41a04f9-5d44e0e708.deploy.json`

## 资源受限替代集 A01-A08

- [x] 用户已确认接受替代方案
- [x] 新增 `benchmarks/agent_beta/adapted/adapted-contract-v1.json`
- [x] 已通过：A05、A07、A08
- [x] 部分通过：A06
- [ ] 待执行：A01、A02、A03、A04（需要按 FaultContract 顺序执行 baseline→inject→probe→observe→recover→cleanup）

## 外部 Holdout 执行方案

- [x] 新增 `docs/external_holdout_runbook.md`
- [x] 新增 `scripts/external_evaluator_keygen.py`
- [x] 新增 `scripts/sign_holdout_score.py`
- [x] 导入验签：`scripts/import_agent_beta_score.py`

## UI 交互降载设计

- [x] 新增 `docs/ai_agent_ux_design.md`
- 核心：默认只展示问题摘要 / 当前唯一下一步 / 证据是否充分 / 是否需要用户参与
- 内部术语全部折叠到专家模式
- 后端 directive 单一下一步直接驱动单卡片

## P01-P12 / Holdout 执行状态

- [x] 新增 `scripts/run_agent_beta_public_cases.py`
- [x] 实际在三节点 VM 执行结果：
  - P01：`PASS`（解释不产生新 Task/Plan）
  - P02：`PASS`（initial_tasks 物化 Evidence，零新增 Task）
  - P07：`PASS`（正向原生 Task DONE，三类危险参数被拒绝）
  - P11：`PASS`（phase1 `insufficient_data`，phase2 `ready`）
  - P12：`PARTIAL`（Sidecar 重启与 readyz 恢复通过，完整 UI/SSE 恢复未自动执行）
  - P03/P04/P05/P06/P08/P09/P10：`AWAITING_ENVIRONMENT_OR_INDEPENDENT_EVALUATOR`
- [x] 报告：`reports/implementation/public-cases-status.json`
- [x] 新增 Holdout 交换合同：`benchmarks/agent_beta/schemas/holdout-score-v1.schema.json`
- [x] 增强 `scripts/import_agent_beta_score.py`：RFC8785 风格 canonical JSON + Ed25519 验签 + 外部 fingerprint 绑定
- [x] 新增 `tests/test_holdout_import.py`：有效签名 VERIFIED、错误公钥 INVALID_SIGNATURE、缺 slot 拒绝、development 不升级
- [x] Holdout 状态：`reports/implementation/holdout-status.json`，20 个 required slot 均为 `AWAITING_EXTERNAL_HOLDOUT`
- [ ] H01-H19 真实盲测成绩导入：等待外部 Evaluator 和用户提供外部公钥 fingerprint

## 阻塞项

- P01-P12 完整 R4 验收与故障注入（当前已完成基础三节点部署和真实 Pi 闭环，但尚未执行全部公开 Case 与 Holdout）
- 外部独立 Holdout Evaluator 尚未配置
- 三节点真实故障注入、独立探针、恢复和最终清理尚未完整执行

## 下一步

1. 推进 G6：用 Scripted Provider 完成本地 Pi 闭环 Contract（T1）
2. 推进 G4：Membership 逻辑目标升级
3. 推进 G8：Causal Graph v2 / EvidenceGap / Recommendation
4. 推进 G10：前端工作台接入新 API
