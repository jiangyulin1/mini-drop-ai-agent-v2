# Mini-Drop AI Case 解决服务与评测体系

> 状态：生产级目标架构与当前实施基线（2026-08-05）  
> 产品目标：多租户、多集群、可扩展的生产级 AI 故障调查与恢复服务。  
> 当前验证环境：一个 Control、两个 4 vCPU / 4 GB Linux Worker 的 Hyper-V 三节点实验 Profile；它不构成产品规模边界。

## 1. 产品定位

Mini-Drop AI 服务的目标不是生成一次性的性能报告，而是维护一个可恢复的故障
Case：持续观察、补齐证据、请求必要授权、提出处理方案，并在处理后重新测量，直到
达到恢复标准或诚实地以 `INSUFFICIENT_EVIDENCE` 结束。

普通用户默认只需要看到四件事：问题和恢复目标、当前发现、正在进行或准备进行的
下一步、需要自己确认的唯一操作。十二步流水线、候选图、完整 Evidence 表和策略详情
保留在“调查详情”中，不作为默认交互负担。

## 2. 服务边界和职责

```text
Web / CLI
  -> Incident Case API：对话、状态、时间线、暂停/恢复/停止/纠正
  -> Diagnosis Orchestrator：确定性状态机、租约、幂等、唤醒
       -> 事实平面：Agent、Collector、Analyzer、Artifact、Evidence
       -> 推理平面：广度异常扫描、候选解释图、探针规划、报告校验
       -> Source Gateway：授权后的指标、日志、Trace、拓扑、变更与知识源
       -> Policy Engine：身份、范围、预算、风险、Grant、审批和审计
       -> Actuation Gateway：预检查、低风险修复、验证和回滚
       -> LLM Gateway：意图理解、候选扩展、自然语言解释
```

### 2.1 确定性事实平面

- Agent 和采集器只产生可追溯事实，不输出最终根因。
- Analyzer 将原始产物归一化为 Evidence，并保存时间窗、目标、采集器、Artifact
  哈希和数据质量。
- Evidence 不可原地改写；结论只能引用 Evidence ID。
- baseline、incident、verification 必须使用相同负载和正式测量窗口。

### 2.2 诊断推理平面

- 首轮先做 CPU、I/O、内存、网络、运行时和依赖的广度扫描，再建立候选。
- `Hypothesis` 只作为可撤销的调查候选，并永久保留 `OTHER_UNKNOWN`。
- 每个候选分别保存支持证据、反驳证据和缺失证据，不只保存一个置信度数字。
- 下一探针按“预期信息增益 × 成功率 /（成本 + 风险 + 等待时间）”排序。
- 当现有候选均被排除、新证据无法解释、连续两轮没有信息增益或证据冲突时，
  重新生成候选集合。

### 2.3 策略与控制平面

诊断权限、信息读取权限和变更权限必须分开：允许读取系统指标，不代表允许读取日志
正文；允许单次 15 秒 Profile，不代表后续 Profile 自动继承授权；允许修改测试环境，
也不代表可以修改生产。风险描述潜在影响，授权描述谁可以在什么边界内做什么，不能
继续压缩为同一个等级。

- 低成本只读探针可按环境策略自动执行。
- 高成本或扩大范围的探针需要单次授权。
- 经过注册、预授权、预检查、可回滚且可验证的低风险修复可以自动执行。
- 超出授权包络、缺少安全前置条件或故障域不确定时，自动降级为人工审批或拒绝。
- 配置修改、重启、迁移和扩缩容不是固定风险；必须结合冗余、环境、时段、范围和
  当前事故状态由确定性 Policy Engine 计算影响等级。
- 用户暂停后不得创建新任务；停止后取消可取消任务并进入终态；纠正目标后必须
  更新 Scope 并重新规划。
- 无论人工还是 AI 执行修复，都必须重新压测、检查保护指标并满足稳定观察窗口。

### 2.4 LLM 的明确边界

LLM 可以做：自然语言意图结构化、候选扩展、证据缺口建议和面向用户的解释。

LLM 不可以做：伪造事实、自由生成命令、绕过 Probe Registry、直接决定授权、把
当前拓扑当历史拓扑、把小数置信度冒充校准概率，或在没有验证数据时宣布修复成功。
模型不可用时，采集、规则分析、Evidence 校验和状态机必须继续运行。

模型可以提出读取或变更意图，但实际授权与执行由模型之外的 Source Gateway、Policy
Engine 和 Actuation Gateway 强制完成。完整设计见
[`ai_authorization_and_tooling.md`](ai_authorization_and_tooling.md)。

## 3. Case 生命周期

建议在现有 `DiagnosisSession` 上逐步增加 Case 协作语义，而不是另建一套不相容的
任务系统：

```text
CREATED
  -> SCOPING <-> NEEDS_SCOPE_CONFIRMATION
  -> OBSERVING -> INVESTIGATING
  -> WAITING_APPROVAL / WAITING_USER / PAUSED
  -> PROPOSING_FIX -> POLICY_EVALUATING
  -> WAITING_MANUAL_ACTION / PREFLIGHTING
  -> EXECUTING_FIX -> VERIFYING
       |-> OBSERVING（未恢复或需要新方案）
       |-> ROLLING_BACK（保护指标退化）
       |-> RESOLVED
  -> INSUFFICIENT_EVIDENCE / STOPPED / FAILED
```

每次推进记录结构化 `investigation_step`：观察、当轮候选、选定动作、策略判定、
执行结果、Evidence 引用、成本和耗时。服务重启后由持久化状态与短租约继续推进，
`diagnosis_step_id` 保证同一步不会重复下发采集。

## 4. API 与前端演进

现有创建、查询、审批和 SSE 接口继续保留。下一阶段按顺序增加：

```text
POST /api/v1/diagnoses/{id}/pause
POST /api/v1/diagnoses/{id}/resume
POST /api/v1/diagnoses/{id}/stop
POST /api/v1/diagnoses/{id}/corrections
POST /api/v1/diagnoses/{id}/manual-actions
POST /api/v1/diagnoses/{id}/verification
GET  /api/v1/sources
POST /api/v1/grants
DELETE /api/v1/grants/{grant_id}
GET  /api/v1/actions
POST /api/v1/actions/{action_id}/evaluate
POST /api/v1/actions/{action_id}/execute
POST /api/v1/actions/{action_id}/rollback
```

默认页面只展示 Case 摘要、重要事件时间线、当前动作和单一待确认项；Evidence、内部
节点、候选变化和策略日志放在可展开的调查详情中。

## 5. 评测必须分成四条轨道

### 5.1 Case 质量门禁

先判断题目是否值得测、环境是否能测。`benchmark_audit.py` 从性能需求、Oracle、
答案泄漏、可复现性、环境适配和问题解决闭环六个维度评分。这个分数不是 AI
正确率。环境缺能力的 Case 必须标为 `PARTIAL` 或 `UNSUPPORTED`，不能算作模型失败。

运行当前团队测试包：

```powershell
.\.venv\Scripts\python.exe scripts\audit_diagnosis_dataset.py `
  "..\测试集\Mini-Drop统一诊断测试集-v1.1.1" `
  --output-dir reports\eval\dataset-audit-v1.1.1
```

### 5.2 被动回放

不同策略读取完全相同的离线 Evidence，比较 Root Cause Top-1/Top-3、关键证据召回、
无证据结论率、开放集拒答和重复运行稳定性。Oracle 不进入模型上下文。

### 5.3 主动诊断

AI 在可执行 Fixture 中选择下一探针。除最终答案外，还评估下一动作正确率、无效
探针率、自动推进率、预算、耗时和越权次数。

### 5.4 协作与恢复

测试暂停/恢复、停止、目标纠正、单次授权、人工代执行、修复后复测和持续观察。
主指标为 `Case Resolution Rate`，安全硬指标为未经授权的高风险执行次数必须等于零。
低风险自动修复还要评估授权越界、错误自动批准、回滚成功率、保护指标退化和用户停止
生效延迟。

## 6. 测试集 v1.2 契约

v1.1.1 不能把同一个 JSON 同时交给 AI、故障注入器和评测器。v1.2 应物理分离：

```text
cases/public/<case-id>.input.json       # 仅用户可见症状、范围和恢复目标
cases/private/<case-id>.trigger.json    # 仅 Runner 读取
cases/private/<case-id>.oracle.json     # 仅 Scorer 读取
fixtures/<case-id>/                     # setup/baseline/inject/recover/verify/cleanup
environments/<environment-id>.json      # 采集器、证据类型、资源和限制
protocol/session-output.schema.json     # 完整调查轨迹
```

Oracle 必须拆分 `incident_trigger`、`root_mechanism`、`root_entity`、
`affected_entities`、`propagation_path`、`symptom` 和 `recovery_criteria`，并允许语义
等价答案，避免“判断正确但标签名称不同”被误判。

每个 Case 还必须给出固定负载、baseline 稳定标准、incident 退化阈值、正式测量
窗口、恢复阈值和连续稳定观察时间。只有 lifecycle 可执行且自动清理成功的 Case
才能进入正式主动评测。

## 7. 当前三节点实验 Profile 的测试范围

| Case | 当前状态 | 说明 |
|---|---|---|
| CPU 热点 | RUNNABLE | `sys_metrics + perf_cpu/py-spy` |
| 共享 I/O 争抢 | RUNNABLE | `sys_metrics + ebpf_io` |
| 同宿主机噪声邻居 | RUNNABLE | 目标 Profile 与宿主机/进程压力对照 |
| 源码热点 | RUNNABLE | Profile 符号与源码位置 |
| 内存增长 | PARTIAL | 可测 RSS，缺语言级保留对象证据 |
| 流量饱和 | PARTIAL | 可测资源饱和，缺请求率和请求延迟指标 |
| JVM GC | UNSUPPORTED | 尚无 async-profiler/JFR/GC 指标 |
| 网络依赖边延迟 | UNSUPPORTED | 尚无分布式 Trace 和服务延迟数据源 |
| Kafka 队列积压 | UNSUPPORTED | 尚无 Kafka lag 与生产消费速率指标 |
| 下游不可达 | UNSUPPORTED | 尚无 Trace 错误边和统一连接错误证据 |

RUNNABLE 只说明证据能力接近满足，不代表 v1.1.1 已具备固定 Fixture 和量化验收阈值。
同时，三个 VM 共用一台物理主机，因此不能把实验结果解释为真实物理故障域隔离能力。
生产级测试应增加多集群、多租户、跨故障域、权限隔离、数据源降级、控制面高可用和
大规模调度轨道；这些能力不能因当前实验资源有限而从目标架构中删除。

## 8. 实施顺序

1. 已完成：质量/环境门禁、三节点 EnvironmentProfile、机器与人工报告。
2. 已完成第二阶段：可信主体、内置 Source Registry、AuthorizationGrant 持久化、
   Source PolicyDecision、短期 Capability Token、Source Gateway、三个只读 Connector、
   EvidenceEnvelope、Action Registry 策略评估，以及模型输入的程序化脱敏、聚合、去重
   和预算控制；下一步接入 OIDC、真实外部 Connector、Action Grant 与 Actuation Gateway。
3. 实现 IncidentCase 协作层、暂停/恢复/停止/纠正、授权撤销和用户优先事件。
4. 实现版本化 ContextPacket、ModelAttempt、HypothesisGraph 和 InvestigationIteration。
5. 把四个 RUNNABLE Case 改造成 public/private 分离的 v1.2 可执行 Fixture，增加健康
   无故障 Case，并接入被动回放 Runner 和统一 Scorer。
6. 接入 OIDC 与真实 Prometheus、Topology、发布、日志和 Trace Connector。
7. 实现 RecoveryPlan、人工动作回填、正式验证窗口和 No-Regression 判定。
8. 最后增加 Action Grant、JTI 防重放和 Actuation Gateway，在实验环境实现一个
   Mini-Drop 自身可逆动作，通过安全门禁后再扩展业务动作。

在第 5 步的 public/private Fixture 和统一 Runner 完成前，不运行“10 Case × 3 策略 ×
3 次”的 90 次正式实验；否则数字仍主要反映答案泄漏和环境缺能力，而不是 AI 的诊断水平。

完整功能规格见 [`ai_functional_design_v3.md`](ai_functional_design_v3.md)。
