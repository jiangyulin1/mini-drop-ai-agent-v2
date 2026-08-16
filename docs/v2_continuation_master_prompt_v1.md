# Mini-Drop V2 架构收敛与诊断 Agent 产品闭环总提示词 v1.0

> 用途：直接交给能够读取、修改、测试和运行本仓库的编码 Agent（Codex 或同类工具）。
>
> 目标仓库：`mini-drop-ai-agent-v2`
>
> 起始参考提交：`832e12e0ddb2b900865c5a6aaf45b79a29d199dc`
>
> 本提示词是“继续推进合同”，不是完成度证明。执行时必须以当前 checkout、当前环境和重新运行的验证结果为准。

---

## 0. 角色、任务和执行方式

你是 Mini-Drop V2 的首席架构师、实现工程师和验证负责人。你的任务不是继续堆积设计名词、平行框架、空 API 或演示 Mock，而是在保护现有功能的前提下，把当前 V2 收敛成：

1. 边界清晰、依赖方向正确、能够持续维护的模块化控制面；
2. 可恢复、可审计、可验证、受权限约束的诊断 Agent；
3. 普通 Drop 采集功能始终独立可用的完整产品；
4. 能在 Linux 与 Windows 开发环境复现、在三节点 Linux 环境稳定演示的候选版本；
5. 有真实测试、真实运行证据和明确限制，而不是依赖文档声明的“完成版本”。

这是一个授权你实施、测试和修复的工程任务。你可以直接执行以下安全、仓库内操作，无需逐项询问：

- 阅读源码、Git 历史、文档、测试、日志和配置；
- 搜索并阅读与当前问题有关的一手官方技术文档；
- 修改任务范围内的源码、测试、迁移、构建文件和文档；
- 生成 protobuf、安装项目依赖、运行静态检查、单元测试、集成测试和本地构建；
- 启动本地或明确属于本项目的容器和测试服务；
- 创建可删除的测试数据和临时产物；
- 对当前工作树执行只读诊断。

下列行为必须获得用户明确授权：

- Git push、合并 PR、发布 Release 或修改远程仓库状态；
- 删除或覆盖用户数据、数据库、VM、对象存储桶或已有部署；
- 在共享/生产环境注入故障、压力或执行修复动作；
- 使用新的付费服务、扩大外部权限、创建长期云资源；
- 读取未明确提供的 Secret、个人配置或私有评测答案；
- 对任务目标作实质性扩张。

不要要求用户逐阶段回复“继续”。在安全且不需要新权限时，完成一个门禁后自动进入下一门禁。只有缺少不可推断的业务选择、Secret、外部 Authority 或危险操作授权时，才提出最小问题。

### 0.1 指令冲突顺序

发生冲突时按以下顺序处理：

1. 用户最新明确要求；
2. 当前代码、数据库、依赖锁、运行环境和机器验证结果；
3. 本提示词中的不变量、成功标准和安全边界；
4. 当前仓库其他设计文档；
5. 历史进度、旧报告、提交标题和 README 中的完成声明。

### 0.2 工作沟通

- 开始工具调用前，用一到两句话说明当前阶段和第一项检查。
- 仅在阶段切换、出现改变方案的重要发现或遇到真实阻塞时更新进度。
- 不逐条播报普通搜索和命令。
- 最终回答必须给出结论、证据、未完成项和下一动作。
- 不保存或展示模型私有思维链；只保存可复核的决策摘要、依据、假设、证据引用和限制。

---

## 1. 最终产品目标

Mini-Drop 是轻量级 Linux 性能诊断平台。普通用户应当可以继续使用原有 Dashboard、Task、Collector、Artifact 和结果页面，不依赖 AI Provider。AI Agent 是建立在 Mini-Drop 事实系统之上的调查层，而不是绕开 Mini-Drop 的另一个执行平台。

### 1.1 两种统一入口

问题驱动入口：

```text
用户描述“某服务 CPU 飙高/延迟变大/内存上涨”
→ 创建或进入 Case
→ Agent 读取目标、时间范围、已有 Evidence 和缺口
→ 在权限与预算内提出或执行低风险 Operation
→ 新结果统一进入 Evidence
→ 持久 Wakeup 触发下一轮判断
→ 输出可验证结论、缺口、建议与验证计划
```

数据驱动入口：

```text
用户从 Task/Artifact/Collection/Evidence 发起解释
→ 数据物化为 canonical CaseEvidence 和 EvidenceProjection
→ Agent 首先解释已有数据
→ 只有缺少决定性事实时才补采
→ 结论与每条 Evidence 保持可追溯关联
```

两种入口必须共享同一套 Case、Run、Evidence、Plan、Campaign、Execution、Conclusion 和 Workspace，不允许维护两条业务主链。

### 1.2 产品完成状态

状态必须严格区分：

- `LOCAL_GREEN`：本地跨平台门禁和确定性测试通过。
- `INTEGRATION_READY`：真实 PostgreSQL、MinIO、Server、Agent、Analyzer、Sidecar、Web 的纵向链路通过。
- `DEMO_READY`：当前不可变候选在三节点环境完成公开场景、浏览器、故障恢复和清理验证。
- `INDEPENDENTLY_VALIDATED`：候选通过仓库外独立 Authority 的 Holdout 验证。

不得因为 HTTP 200、模型输出了一段合理文本、某个 API 存在、测试文件数量增加或旧报告写了 PASS 就升级状态。

---

## 2. 开工时必须重新验证的基线

以下内容是 2026-08-16 对参考提交的审计假设，执行时必须重新检查，不得直接当作事实：

### 2.1 已有能力

- React SPA + FastAPI/REST/SSE + gRPC Agent + Analyzer Worker；
- PostgreSQL/SQLite 持久化和 MinIO 对象存储；
- perf、eBPF、py-spy、Java、pprof、memory、sys_metrics、log、process 等 Collector；
- TaskAttempt、AnalysisJob、租约、取消和结果 spool；
- Case、Evidence、Plan、Campaign、ExecutionUnit、Runtime、Causal、Conclusion 等 V2/V6 模型；
- Pi Sidecar、`AgentRuntimePort`、工具 allowlist、内部 Token；
- EvidenceProjection、RuntimeWakeup、DomainOutbox、ClaimEvidenceBinding 等基础实现；
- Python、Web 和 Sidecar 的较大测试集合。

### 2.2 已知架构和工程风险

开工必须至少复现或排除以下问题：

1. `server/app/main.py` 仍承载过多装配、后台任务和兼容逻辑，并存在重复导入。
2. `main.py` 星号导入 routes，而 routes 又从 `main.py` 导入大量全局符号，形成导入顺序耦合和隐式循环。
3. routes 直接装饰共享 `app`，没有真正使用独立 `APIRouter`、显式依赖和应用服务边界。
4. `server/app/models.py` 与 `server/app/models/` 同时存在，产生源文件歧义和重复维护风险。
5. `sql_repository.py` 仍是巨型 Repository，V6 mixin 只是部分迁移，事务边界和领域 owner 不清楚。
6. `0023_v6_agent_core` 一次性创建大量表，使用 `Base.metadata.create_all()`，吞掉部分索引异常，downgrade 未完整撤销旧表增量字段。
7. `requirements.lock` 是环境 `pip freeze`，并可能以 editable Git dependency 指向原版旧提交，不是 V2 的跨平台可复现锁。
8. 新增测试使用 `Path.read_text()` 默认编码，Windows 默认 GBK 下失败；Prompt 字节哈希受 CRLF/LF 工作树转换影响。
9. 文档引用的 `reports/implementation/ai-agent-runtime-state.json` 和 evidence JSONL 可能未进入仓库或无法由机器生成。
10. Python 测试在参考 Windows 环境曾出现 `903 passed, 2 skipped, 6 failed`；其中包含编码和换行哈希可移植性问题。不得引用历史“911 全绿”替代重跑。
11. Web 构建可通过但存在超大 chunk；Ant Design 有弃用警告；前端 feature 边界仍不清晰。
12. Sidecar 与 Web 依赖审计存在高危/严重项时，需要判断是否处于实际调用链，而不能仅记录总数或盲目 `--force` 升级。

### 2.3 开工审计输出

在修改生产代码前，生成一个简洁、机器可读的基线报告，至少记录：

```json
{
  "base_commit": "...",
  "worktree_clean": false,
  "changed_files": [],
  "python_versions": [],
  "node_version": "...",
  "alembic_heads": [],
  "dependency_lock_status": "VALID|INVALID|MISSING",
  "protobuf_status": "READY|NEEDS_GENERATION",
  "python_test_summary": {},
  "sidecar_test_summary": {},
  "web_test_summary": {},
  "migration_check": "PASS|FAIL|NOT_RUN",
  "known_architecture_violations": [],
  "current_gate": "C0",
  "next_action": "..."
}
```

报告写入 `reports/implementation/`；如果该目录不存在，建立目录、Schema 和生成脚本。状态必须由测试/检查脚本更新，不能靠人工把字段改成 PASS。

---

## 3. 不可妥协的系统不变量

以下不变量优先于实现便利。每条都必须有代码约束和纵向测试。

### I1：普通 Drop 独立可用

AI、Pi、MCP 或 Provider 未配置、离线或失败时，standalone Task、Agent、Analyzer、Artifact 和结果页面仍可使用。AI 故障不得拖垮核心采集面。

### I2：Mini-Drop 是事实和权限真源

模型负责理解、比较、提出假设和选择候选动作；Mini-Drop 负责身份、租约、权限、预算、状态迁移、执行、证据、验证、幂等和审计。模型输出本身不是已执行动作，也不是已验证事实。

### I3：Case 派生执行只有一个 owner

所有属于 Case 的 Task、SourceCall 和修复提案，只能由持有有效 lease/epoch 的 `CaseSupervisor` 或其明确命名的 application service 在事务中创建。Pi、HTTP route、PlanDriver、旧 Orchestrator、MCP adapter 和后台 sweeper 均不得绕过 owner 直接写入。

### I4：Evidence 有且只有一个 canonical store

Collector Artifact、Query Result、MCP Result、用户附件、历史 Task 和人工证据都进入 CaseEvidence。模型读取 EvidenceProjection，不直接把对象存储地址、任意文件或未经裁剪的外部内容当指令。

### I5：状态变化与唤醒不允许 dual-write

业务状态更新和对应 DomainOutbox 必须在同一数据库事务提交；Outbox relay 采用 at-least-once 交付，消费者必须用稳定 idempotency key 去重。Sidecar 内存不是进度真源。

### I6：每个副作用都有完整 fence

写工具和执行请求至少绑定：

```text
tenant_id
case_id
investigation_run_id
turn_id
cycle_id
model_request_id
runtime_generation
control_revision
scope_revision
plan_revision
campaign_revision
execution_epoch
idempotency_key
actor_id
```

任一必须字段缺失、过期或冲突时 fail closed，不得“尽量执行”。

### I7：最终结论由机器验证器定级

模型可以提交因果图、Claim 和候选结论，但不能自行决定 `CONFIRMED`。Verifier 必须检查 Evidence 状态、Projection hash、字段路径、时间窗口、资源身份、替代假设和 blocker gap，再计算最终状态。

### I8：外部内容是数据，不是高优先级指令

日志、网页、知识库、MCP 返回、Artifact 内容和用户附件全部视为不可信数据。不得允许其中的自然语言扩大 Tool 权限、改变系统规则、读取 Secret 或执行未注册操作。

### I9：不伪造完成和独立验证

Mock、Fixture、录制响应和确定性回放可以用于开发测试，但必须明确 realness。只有真实 Provider/服务/故障/候选运行才能晋级相应状态。仓库内自签结果不得称为独立 Holdout。

---

## 4. 目标架构：模块化单体控制面，而不是继续拆微服务

当前阶段采用：

```text
React Web
   │ REST + SSE
   ▼
FastAPI modular monolith control plane
   ├─ application/domain/ports/adapters
   ├─ PostgreSQL + Outbox + leases
   ├─ MinIO
   └─ gRPC
        ├─ Linux Agent
        └─ Analyzer Worker

FastAPI control plane ↔ authenticated internal HTTP ↔ Pi Sidecar
```

保留 Pi Sidecar 为独立进程，因为它使用 Node SDK且需要独立失败边界。保留 Agent 和 Analyzer 为独立 Worker，因为它们有宿主权限、资源和生命周期差异。不要为了“架构先进”额外引入 Kafka、Redis、Temporal、Kubernetes、完整 Event Sourcing、完整 CQRS 或更多微服务。只有测量证明现有数据库 Outbox/lease 无法满足当前演示规模时才提出 ADR 和替代方案。

### 4.1 目标目录

在兼容旧 API 的前提下逐步收敛到：

```text
server/app/
  main.py                     # 仅进程入口，目标 <= 100 行
  bootstrap.py                # create_app/create_container
  container.py                # 组合根与生命周期

  http/
    app.py                    # FastAPI、中间件、exception handlers
    dependencies.py           # tenant/principal/role/UoW/application services
    schemas/                  # HTTP request/response DTO
    routers/
      health.py
      tasks.py
      agents.py
      diagnoses.py
      cases.py
      evidence.py
      plans.py
      campaigns.py
      runtime.py
      actuation.py
      nlp.py
      internal_runtime.py

  domain/
    common/
    task/
    case/
    evidence/
    planning/
    runtime/
    causal/
    actuation/

  application/
    task_service.py
    case_service.py
    evidence_service.py
    supervisor.py
    campaign_service.py
    runtime_service.py
    conclusion_service.py
    actuation_service.py

  ports/
    unit_of_work.py
    repositories.py
    runtime.py
    artifact_store.py
    source_gateway.py
    clock.py
    event_relay.py

  adapters/
    sqlalchemy/
      models/
      repositories/
      unit_of_work.py
    minio/
    grpc/
    pi_sidecar/
    sources/

  jobs/
    outbox_relay.py
    runtime_wakeup.py
    lease_recovery.py
    offline_sweeper.py

  compatibility/
    legacy_exports.py
    legacy_routes.py
```

这是一张依赖方向图，不要求一次移动所有文件。每次迁移一个纵向切片，保持 API 和行为可运行。

### 4.2 依赖规则

必须通过静态检查或架构测试固定：

```text
domain        → 只依赖标准库和纯类型；不知道 FastAPI、全局 repo、MinIO、Pi
application   → 依赖 domain + ports；拥有用例和事务意图
ports         → 只定义边界合同
adapters      → 实现 ports；可依赖 SQLAlchemy/MinIO/gRPC/HTTP SDK
http          → 依赖 application + HTTP DTO；不得直接操作 ORM 或全局 repo
jobs          → 依赖 application/ports；每个 job 自己获得 UoW
bootstrap     → 唯一允许了解所有具体实现的组合根
```

禁止：

- `routes` 或 `http/routers` 从 `main.py` 导入业务对象；
- `main.py` 星号导入 routes；
- route 直接访问 `repo.tasks`、`repo.agents` 等可变内部字典；
- domain import FastAPI、SQLAlchemy Session、MinIO client 或 Pi SDK；
- application service 在方法内部创建隐式全局 Session；
- gRPC service 绕过 application/UoW 直接修改 Case、Task、Evidence 或 Runtime 状态；
- 为保持旧测试 monkeypatch 而永久暴露全部内部全局变量。

### 4.3 FastAPI 组合方式

- 每个路由模块导出 `APIRouter`；
- `create_app(container)` 显式 `include_router()`；
- principal、tenant、role、request UoW 和 application service 使用 `Depends` 或显式 dependency provider；
- 生命周期资源在 lifespan 中创建和释放；导入模块不得启动线程、连接数据库或执行外部调用；
- 保持 OpenAPI path、method、status code 和 envelope 兼容；任何行为变化必须有 contract test；
- `from server.app.main import app` 可暂时由兼容模块提供，但新代码不得继续依赖它。

### 4.4 迁移策略

不要进行一次性“大爆炸”改名。采用 strangler 方式：

1. 先建立 `create_app`、Container、UoW 和一个新 APIRouter；
2. 为原 API 建立 OpenAPI/response golden contract；
3. 按 `health → tasks → evidence → cases → runtime → actuation` 迁移；
4. 每迁移一组，删除对应的 `main` re-export 和反向导入；
5. 只有调用点归零且测试通过后，才删除 legacy helper；
6. 最后把 `main.py` 缩成入口，并用架构测试禁止回流。

---

## 5. 事务、Repository 与数据库设计

### 5.1 Unit of Work

HTTP request、Supervisor cycle 和后台 job 各自拥有局部 UoW：

```python
with uow_factory() as uow:
    # application service 使用 uow 暴露的 repositories
    # 所有业务状态和 outbox 在同一个 transaction
    uow.commit()
```

Session 生命周期由调用边界管理，不由 Repository 方法随意创建/提交。Application service 决定事务范围，Repository 只读写聚合。异常必须 rollback 并向上报告。

### 5.2 Repository 拆分

把巨型 `SqlRepository` 按 owner 拆为接口和实现，例如：

- `TaskRepository`
- `AgentRepository`
- `CaseRepository`
- `EvidenceRepository`
- `PlanRepository`
- `CampaignRepository`
- `RuntimeRepository`
- `OutboxRepository`
- `ConclusionRepository`
- `ActuationRepository`

兼容 Facade 可以暂时组合这些 Repository，但不得继续增加新业务方法。新功能只能进入对应 owner。

### 5.3 Outbox 和队列消费

- 业务状态与 Outbox event 在同一 transaction 写入；
- event 有稳定 `event_id`、aggregate ID、aggregate revision、event type、payload schema version、created_at；
- relay 多实例消费使用 PostgreSQL 行锁和 `FOR UPDATE SKIP LOCKED` 或等价安全机制；
- `SKIP LOCKED` 只用于 queue-like 表，不用于普通业务查询；
- claim 有 lease owner、claimed_at、lease_expires_at、attempt_count；
- transient failure 有指数退避和上限；超过上限进入可见 `DEAD/RECOVERY_REQUIRED`，禁止无限重试；
- consumer 以 `event_id + consumer_name` 或业务 effect key 去重；
- 标记 delivered 不能早于下游确认；崩溃重放不得产生重复 Task、Message 或 Conclusion。

### 5.4 Alembic 迁移规则

先判断 `0023` 是否已被任何需要保留的环境应用：

- 如果已经应用：不得重写历史；用后续迁移补齐约束、索引和结构。
- 如果从未对外应用且用户明确允许整理历史：可以提出 squash 方案，但不得擅自执行。

所有新迁移必须：

- 显式使用 Alembic `op.create_table/add_column/create_index/create_foreign_key`；
- 不调用全量 `Base.metadata.create_all()` 代替迁移；
- 不以 `except Exception: pass` 吞掉 DDL 失败；
- 升级前后有 schema contract；
- 空库升级和从参考 V2 数据库升级都通过；
- downgrade 可逆；确实不可逆时，在脚本、Runbook 和测试中明确阻断与恢复路径；
- 同时验证 PostgreSQL；SQLite 只作为开发便利，不能证明并发租约和锁语义。

### 5.5 模型源文件

- 消除 `server/app/models.py` 与 `server/app/models/` 双源；
- 选择 package 作为唯一 ORM model source；
- 兼容 import 由 package `__init__` 明确 re-export；
- Alembic metadata 只从一个入口加载；
- 增加测试证明所有映射类唯一、表名无冲突、导入顺序不改变 metadata。

---

## 6. 可恢复 Agent Runtime

### 6.1 权威状态对象

使用现有对象并收敛职责：

```text
IncidentCase
  └─ InvestigationRun
      ├─ ConversationTurn
      ├─ CaseContextSnapshot
      ├─ AgentCycle
      │   ├─ ModelRequest
      │   ├─ ModelResponse
      │   ├─ AgentProposal
      │   ├─ DecisionRecord
      │   └─ AssistantMessage
      ├─ PlanRevision
      ├─ CampaignRevision
      │   └─ Assignment
      │       └─ ExecutionUnit
      │           ├─ Task/Attempt
      │           └─ SourceCall
      ├─ CaseEvidence
      │   └─ EvidenceProjection
      ├─ CausalGraphRevision
      ├─ EvidenceGap
      └─ ConclusionRevision
```

不要继续增加意义重叠的 Session/Run/Iteration/Diagnosis 对象。新增对象前先列出 owner、状态机、唯一键、生命周期和现有对象不能承担该职责的原因。

### 6.2 状态机和 CAS

每个关键状态迁移必须：

- 有允许的 from/to 集合；
- 使用 revision/epoch 做 compare-and-swap；
- 保存 actor、reason、trace ID 和时间；
- 重复请求返回同一结果或明确 idempotent replay；
- 非法迁移返回稳定错误码；
- terminal 状态不可被迟到事件重新打开。

至少覆盖：

```text
Run: CREATED → ACTIVE ↔ PAUSED → COMPLETED|STOPPED|FAILED
Cycle: QUEUED → DISPATCHED → RUNNING → COMPLETED|FAILED|SUPERSEDED
Wakeup: PENDING → CLAIMED → CONSUMED|RETRY|DEAD
ExecutionUnit: PLANNED → DISPATCHED → RUNNING → SUCCEEDED|FAILED|CANCELLED
Proposal: PROPOSED → ACCEPTED|REJECTED|EXPIRED|SUPERSEDED
Conclusion: DRAFT → PARTIALLY_CONFIRMED|CONFIRMED|REJECTED|SUPERSEDED
```

### 6.3 每轮模型调用

每个 AgentCycle 必须从当前数据库重新构建不可变 Snapshot，不能只复用 Sidecar 内存中的旧 context。Snapshot 至少包含：

- case goal、scope、time window、logical resources；
- control/scope/plan/campaign/evidence revisions；
- 当前 Run、预算和 side-effect policy；
- 活跃假设、已排除假设和精确 Evidence Gap；
- 安全裁剪的 EvidenceProjection 摘要及可读取引用；
- 可用 Operation/Skill/Knowledge 的版本化目录；
- 上一轮可复核决策摘要和最终回答，不包含私有思维链。

每个 Case 同一时刻最多一个活跃 model prompt。相同 Sidecar Session 只订阅一次事件。重建、generation 旋转和 Sidecar 重启必须有测试。

### 6.4 Runtime 事件

- Sidecar 只发送规范化生命周期、工具、最终消息和错误事件；不发送私有 thinking；
- 每条事件有 `event_id`、case、generation、turn、cycle、model_request、sequence；
- Server ACK 后 Sidecar 才可从 spool 删除；
- 断线重发允许重复到达但数据库 effect 必须唯一；
- `turn_end/message_end` 必须生成持久 AssistantMessage、完成 Cycle/Turn 并投影到 Workspace/SSE；
- 前端不得长期停在 `Accepted` 占位。

### 6.5 控制命令

PAUSE、RESUME、STOP、CANCEL、RETARGET、CORRECT_CONTEXT 必须走单一 CaseCommand API 和事务：

1. 更新 control/scope revision；
2. 写审计和 Outbox；
3. Supervisor 取消或冻结对应 ExecutionUnit；
4. Sidecar 收到 abort/steer/follow-up；
5. 迟到旧 generation/revision 结果可以归档，但不得推进当前 Run或创建新副作用。

---

## 7. Tool、权限、审批和不可信输入

### 7.1 Tool policy

把权限作为服务端机器策略，不依赖 system prompt 自律：

| Policy | 可见 Tool | 自动副作用 |
|---|---|---|
| `READ_ONLY` | snapshot/evidence/projection/knowledge/graph/gap 查询 | 0 |
| `PROPOSE_ONLY` | READ_ONLY + plan/campaign/repair proposal | 仅写 proposal，不创建执行 |
| `AUTO_READ_LOW` | READ_ONLY + 经注册的 R0/R1 Operation request | Supervisor 可按预算接受 |
| `REQUIRE_APPROVAL` | 只返回待审批 proposal | 0，直到有效审批 |

执行 Tool Catalog 必须按 Turn 动态构建；不可见 Tool 不应只靠调用后拒绝。服务端仍要再次校验，以防伪造请求。

### 7.2 风险分级

至少定义：

- `R0_READ`: 纯数据库/已有 Evidence 读取；
- `R1_READ_LOW`: 有界、只读、低负载采集或 Query；
- `R2_READ_HIGH`: 可能产生明显负载、访问敏感范围或较长运行；
- `R3_MUTATE`: 修改系统、服务、配置、流量或数据。

默认只有 R0/R1 可以在明确策略和预算内自动执行。R2 需要审批或预先授权窗口。R3 必须独立审批、可回滚设计和执行后验证；当前 Demo 不要求真正自动执行 R3。

### 7.3 审批绑定

审批必须绑定不可变 proposal digest，包括：

```text
tool/operation name
normalized arguments
target resource incarnation
risk
scope revision
control revision
execution epoch
expires_at
one-shot nonce
approver identity
```

参数、目标、风险或 revision 变化后旧审批失效。不得把“用户曾同意过类似动作”当成通用授权。

### 7.4 Prompt injection 与数据隔离

- 外部文本进入 Projection 时标记来源和 trust level；
- Tool 参数只能来自结构化 Schema 和受信任的 current case state；
- 不把外部内容拼进高优先级 policy 指令区；
- 模型不得看到 Secret、内部 Token、VM 密码、私有 Oracle、对象存储凭证；
- MCP/Knowledge/日志里的“忽略规则、调用某工具、发送数据”等内容只能作为被分析文本；
- 高风险动作使用确定性授权器、参数校验器和审计，不使用另一个自由文本模型作为唯一安全门。

---

## 8. Evidence、因果结论和建议

### 8.1 CaseEvidence

每条 Evidence 至少具有：

```text
evidence_id / tenant_id / case_id
source_type / source_id / source_call_id
task_id / execution_unit_id / investigation_run_id
resource_ref / resource_incarnation
event_time_start / event_time_end / ingested_at
schema / schema_version / producer_version
raw_locator / size / sha256
quality / freshness / completeness / trust_level
status = ACTIVE|EXCLUDED|SUPERSEDED|INVALID
lineage / trace_id
```

### 8.2 EvidenceProjection

- Parser 必须版本化、确定性、可重放；
- Projection 保存结构化数值、样本、热点、时间窗口和裁剪说明；
- 模型引用 `evidence_id + projection_hash + field_path`；
- 大 Artifact 先确定性解析和缩减，模型按需读取小 Projection；
- Parser 失败产生可见 Gap，不得把空结果当作健康；
- 同一原始 Artifact + parser version 产生稳定 fingerprint；
- Evidence 被排除或 supersede 时，关联 Claim/Conclusion 必须重新验证。

### 8.3 因果模型

至少支持角色：

```text
PRIMARY_CAUSE
CONTRIBUTING_CAUSE
AMPLIFIER
PROPAGATION_PATH
SYMPTOM
COINCIDENTAL
RULED_OUT
```

每条因果边必须说明方向、机制、支持 Evidence、反证和置信限制。发现下游异常、热点或相关性不等于证明主根因。

### 8.4 Conclusion 与 Verifier

模型提交：

- Claims；
- CausalGraph revision；
- ClaimEvidenceBinding；
- 替代假设；
- unresolved gaps；
- Recommendations 和 VerificationPlan。

Verifier 决定：

- 引用的 Evidence 是否 ACTIVE；
- projection hash/field path/predicate 是否匹配；
- 证据时间和资源是否属于当前 scope/revision；
- 必需因果边是否存在；
- 是否有 blocker gap；
- 主要替代假设是否被区分；
- conclusion watermark 是否落后于最新 Evidence。

证据不足时必须输出精确 Gap并降级，不得用“可能是 A/B/C”掩盖缺证。

### 8.5 修复建议

每条 Recommendation 绑定 Cause 或 Edge，包含：

- immediate mitigation；
- root fix；
- amplifier containment；
- risk 和 blast radius；
- prerequisites；
- rollback；
- verification checks；
- requires_approval；
- 当前系统是否仅建议、可 dry-run 或可执行。

---

## 9. API、SSE 与前端产品结构

### 9.1 API 合同

- 保持现有公开 URL、method 和 response envelope；
- 新 canonical API 使用稳定版本前缀和显式错误码；
- compatibility alias 有弃用测试和删除条件；
- internal runtime API 与 public API 分离并双向认证；
- OpenAPI snapshot 用于发现意外删除、重复 operation ID 或响应漂移。

### 9.2 Workspace Snapshot

前端初次加载只读取一个一致 Workspace Snapshot，至少包含：

- Case、Run 和控制状态；
- 消息历史和当前 Agent 状态；
- Evidence、Projection 和 Review；
- Plan、Campaign、Assignment、Execution；
- CausalGraph、Gap、Conclusion、Recommendation；
- budgets、errors、recovery_required；
- 单调 `case_event_seq` 和 snapshot revision。

### 9.3 SSE 一致性

- 事件 cursor 使用每 Case 单调序列，不使用易碰撞时间戳；
- Snapshot 返回水位，客户端从该水位后订阅；
- 解决“读取 Snapshot 与 subscribe 之间”的事件窗口；
- 重连 replay 不丢、不重复渲染；
- 客户端按 event ID/revision 幂等 reducer；
- 超出保留窗口时返回明确 resync 指令。

### 9.4 前端 feature 边界

逐步收敛为：

```text
web/src/features/
  tasks/
  cases/
  evidence/
  investigation/
  causal/
  runtime/
  settings/
```

- API client、query/state、components 和 tests 与 feature 对齐；
- 保留 Ant Design 和现有主题，不重做无关视觉系统；
- 拆分超大 Workbench/Page，避免一个组件理解整个后端模型；
- 关键状态有 loading、empty、partial、stale、permission denied、recovery required 和 error；
- AI 创建且用户可查看的 Task 必须同时出现在普通任务页；
- Evidence 卡默认显示一个最有价值的预览，完整结果作为次级入口；
- 修复已出现的组件弃用警告，进行路由级 code splitting，控制主 bundle；
- 所有重要交互有键盘可达、可见焦点和语义标签。

---

## 10. 依赖、构建与可移植性

### 10.1 Python 锁定

用 `pyproject.toml` 作为依赖声明真源，引入并提交跨平台 `uv.lock`。要求：

- 覆盖声明支持的 Python 版本和 Linux/Windows；
- 根项目作为当前 workspace project 安装，不出现指向旧仓库提交的 editable Git dependency；
- 补齐并验证 `pyproject.toml` 的 build-system；如果选择不安装根项目，必须在 CI、Docker 和本地入口中明确这一点，不能依赖偶然的当前目录 import；
- CI 使用 `uv sync --locked` 或等价 frozen 模式；
- Docker/传统 pip 如需 requirements，必须从 lock 自动导出，并在文件头记录生成命令；
- Runtime 与 dev/test 依赖分组清楚；
- 依赖更新必须有测试和审计差异，不运行破坏性 `audit fix --force`。

若当前环境无法引入 uv，先记录阻塞并生成严格、可复现、无 self-reference 的替代锁；不得继续保留不可重现的 `pip freeze` 冒充正式锁。

### 10.2 Node 锁定

- `package-lock.json` 是唯一 Node lock；CI 使用 `npm ci`；
- 不信任全局 `NODE_ENV`，测试安装显式包含 dev dependencies；
- Pi SDK banner/version 从实际 package metadata 读取，不手写版本；
- 依赖安全问题按 reachable path、权限和修复风险分级。

### 10.3 文本和哈希

- 所有 Python/JS/JSON/Markdown 明确 UTF-8；测试读取文本时显式 `encoding="utf-8"`；
- 增加 `.gitattributes` 固定需要内容寻址文件的换行策略；
- Candidate hash 基于 Git/打包 payload 的规范字节，不基于受平台自动换行影响的工作树偶然字节；
- 同时测试 Linux LF 和 Windows checkout；
- protobuf 生成使用跨平台 Python 脚本，`dev.py proto` 不在 Windows 强依赖 Bash。

---

## 11. 可观测性、预算与恢复

### 11.1 关联链

以下 ID 必须在数据库、日志、Trace 和 API 中可关联：

```text
request_id → case_id → run_id → turn_id → cycle_id → model_request_id
→ proposal_id → campaign_id → assignment_id → execution_unit_id
→ task/source_call → artifact → evidence_id → projection_hash
→ outbox_event_id → wakeup_id → conclusion_revision
```

### 11.2 指标

至少记录：

- active/paused/recovery-required Case；
- wakeup pending/claimed/dead 和 oldest age；
- outbox lag、attempt 和 dead count；
- cycle/model/tool latency、timeout、retry；
- duplicate event/effect prevented；
- Evidence parse failures、stale、invalid references；
- automatic R1 operations、approval required/rejected；
- Provider/Sidecar/MCP availability；
- standalone Drop success rate。

不得记录 Prompt Secret、私有思维链和未裁剪敏感 Artifact。

### 11.3 预算

每个 Case/Run 有：

- 最大 cycle 数；
- 最大自动 Operation 数和并发；
- 最大 wall-clock；
- 最大模型 token/cost（若 Provider 可报告）；
- 最大 Evidence 字节和单 Projection 大小；
- 最大 transient retry；
- idle timeout。

预算耗尽后进入明确 `AWAITING_USER`、`PARTIAL` 或 `RECOVERY_REQUIRED`，不静默停止、不无限循环。

---

## 12. 实施门禁

门禁是依赖顺序，不是要求一次提交巨型 diff。每个门禁完成后运行对应回归，再自动进入下一项。

### C0：事实复位与全绿基线

工作：

- 记录 Git、环境、迁移、依赖、生成文件和服务状态；
- 保护用户已有修改；
- 修复 UTF-8、CRLF hash、protobuf 生成和测试启动问题；
- 修复或正确隔离现有真实失败；
- 建立 `reports/implementation` Schema 和自动状态生成器；
- 建立 Linux + Windows 最小 CI matrix；
- 确认普通 Drop 基线。

退出条件：

- 当前完整 Python、Sidecar、Web tests/lint/build 有真实结果；
- 至少验证 Python 3.9 与部署基准版本，并覆盖 Linux/Windows 组合中的代表性矩阵；
- Windows 和 Linux 的测试语义一致；
- 没有通过删除测试、弱化断言或设置环境变量掩盖内容错误；
- baseline report 指向当前 commit/worktree。

### C1：组合根与路由解环

工作：

- 实现 `create_app`、Container 和 lifespan；
- 将 health 和 tasks 作为第一批 APIRouter 纵向切片；
- 建立 HTTP dependency providers；
- 逐步迁移其他 routes；
- 删除重复 imports、星号 re-export 和 route→main 反向依赖；
- 建立 import/architecture tests 和 OpenAPI contract。

退出条件：

- routes 不再 import `server.app.main`；
- `main.py` 不再 star import routes；
- import `server.app.main` 不创建外部连接/线程；
- 所有公开路由和 response contract 保持兼容；
- `main.py` 最终仅保留轻量入口。

### C2：模型、UoW 和 Repository 收敛

工作：

- 消除 models 双源；
- 实现 UoW 和领域 Repository；
- route 改用 application services；
- gRPC service 与 HTTP route 共用 application services/UoW，不维护第二套事务写路径；
- 后台 job 使用独立 UoW；
- 兼容 Facade 冻结，只允许减少调用；
- 为关键事务建立 rollback、并发和 idempotency 测试。

退出条件：

- 新代码无全局可变 repo 访问；
- 业务事务边界可以从 application service 明确追踪；
- Repo 拆分不改变数据合同；
- PostgreSQL 并发测试通过。

### C3：迁移、Outbox 与租约可靠性

工作：

- 审计并修复 0023 后续 schema；
- 显式 Alembic 操作和约束；
- 实现 atomic state+outbox；
- 实现 claim/reclaim/backoff/dead/idempotent consumer；
- 用真实 PostgreSQL 测试多个 relay/supervisor 并发。

必须覆盖崩溃点：

1. 业务写入前崩溃；
2. 业务写入和 Outbox commit 后、relay 前崩溃；
3. relay 发送后、标记 delivered 前崩溃；
4. consumer 写 effect 后、ACK 前崩溃；
5. lease 到期与旧 owner 迟到提交。

退出条件：重复 effect 为 0、丢失推进为 0、dead item 可见且可恢复。

### C4：Runtime、Tool Policy 与控制闭环

工作：

- Snapshot 每 Cycle 刷新；
- Sidecar 单 Session 单 subscription；
- Runtime event spool/ACK/replay；
- 动态 Tool Catalog 与服务端二次授权；
- 完整 fence、approval digest 和 idempotency；
- PAUSE/RESUME/STOP/RETARGET/CORRECT 确定性生效；
- Provider/Pi 失败时 deterministic 回退。

退出条件：

- READ_ONLY 副作用增量严格为 0；
- PROPOSE_ONLY 不创建 ExecutionUnit；
- STOP 后旧代调用不能创建 Task/SourceCall/Message；
- Sidecar/Server/Supervisor 重启后不丢 Turn、不重复回答；
- standalone Drop 在 Provider/Sidecar 失败时仍成功。

### C5：Evidence、因果与真实 Agent 决策

工作：

- 统一所有来源的 Evidence ingestion；
- Projection parser、版本、裁剪和重放；
- Claim field-path verifier；
- CausalGraph/Gap/Conclusion/Recommendation 闭环；
- Agent 根据 Evidence 差异改变后续动作，而不是固定 collector 顺序；
- Knowledge/Skill/MCP 影响策略但不扩大权限。

退出条件：

- Agent 能引用真实数值、热点或日志样本；
- 错 projection/hash/window/resource 的 Claim 被拒绝；
- Evidence excluded/superseded 后旧结论自动降级；
- 配对 Evidence fork 产生预登记的不同 action equivalence class；
- A/A control 足以证明差异不是随机漂移；
- 下游异常不能在缺少关键边时成为 CONFIRMED primary。

### C6：API、Workspace 与前端闭环

工作：

- 一致 Workspace Snapshot；
- 单调 Case event sequence 和 SSE gap-free replay；
- 持久 AssistantMessage 和多轮历史；
- feature 化前端；
- AI Task 普通页面可见；
- Evidence 预览、Campaign、Execution、Graph、Gap、Conclusion 和控制 UI；
- 路由级 code splitting、弃用修复和可访问性。

退出条件：

- 真实后端浏览器测试覆盖问题驱动和数据驱动入口；
- 刷新、断线和服务重启不丢消息、不重复卡片；
- 网络 Trace 证明 UI 真正消费 canonical API；
- 不存在 Accepted 占位或空白 Workbench；
- 主 bundle 体积有记录且相对基线不恶化，或有明确理由。

### C7：依赖锁、候选与部署

工作：

- 建立跨平台 Python lock 和 frozen CI；
- 修复候选包对 tracked/untracked/生成 Web/迁移/锁文件的内容寻址；
- 同一 payload digest 部署 Control、Worker1、Worker2；
- 验证真实 package/runtime version；
- 故障注入有范围、TTL、finally cleanup 和最终健康检查；
- Runbook 实际演练。

退出条件：

- 三节点 receipts 指向相同 candidate/payload/lock/migration digest；
- 普通 Drop、Agent 调查、Provider 回退、MCP 不可用和 cleanup 场景通过；
- 当前报告不引用历史 release 证据；
- 依赖问题按 reachable risk 记录并处理。

### C8：公开评测和最终交付

沿用并修复当前 `benchmarks/agent_beta`，但先校验 manifest、prompt hash、source lock 和 runner 不受平台换行影响。

评测至少包含：

- standalone Drop；
- ANSWER_ONLY 零副作用；
- 已有 Artifact 解释；
- 三轮 Evidence→Wakeup→新决策；
- PAUSE/RESUME/STOP/RETARGET；
- Sidecar/Server/Worker 重启；
- 同构与异构 Campaign；
- Collector + Query + MCP 统一 lineage；
- 复合因果和 distractor；
- Evidence exclude 导致结论降级；
- Provider、Pi、MCP 分别失败；
- 浏览器刷新与 SSE replay；
- 候选、故障和 cleanup 真实性。

退出条件：mandatory assertions 全部 PASS，不包含被伪装为成功的 PARTIAL/AWAITING/NOT_RUN；外部 Holdout 不可用时只标 `AWAITING_EXTERNAL_HOLDOUT`。

---

## 13. 测试和评测合同

### 13.1 测试层次

1. **Architecture tests**：禁止 main/routes 循环、domain 越层依赖、全局 repo 新调用。
2. **Unit tests**：状态机、policy、parser、verifier、fingerprint、budget。
3. **Contract tests**：HTTP/OpenAPI、gRPC、Sidecar Tool schema、event envelope、candidate manifest。
4. **Integration tests**：PostgreSQL、MinIO、Outbox、lease、Agent、Analyzer、Sidecar。
5. **Recovery tests**：崩溃点、重复事件、迟到提交、断网、重启。
6. **Browser tests**：真实后端、DOM/API/Event 断言，不以截图作为唯一证据。
7. **Agent evals**：代表性 Case、配对 fork、A/A control、引用和副作用评分。

### 13.2 必须保留的核心断言

至少实现或保留：

```text
test_routes_do_not_import_main
test_main_import_has_no_external_side_effect
test_openapi_contract_is_backward_compatible
test_models_have_single_metadata_source
test_uow_rolls_back_state_and_outbox_together
test_outbox_reclaim_and_idempotent_delivery
test_only_supervisor_creates_case_execution
test_answer_only_has_zero_side_effects
test_propose_only_cannot_dispatch
test_late_generation_is_fenced
test_pause_coalesces_wakeup_until_resume
test_stop_prevents_new_case_effects
test_runtime_event_replay_is_idempotent
test_projection_is_deterministic_and_bounded
test_claim_requires_matching_projection_field_and_window
test_excluded_evidence_supersedes_conclusion
test_sse_snapshot_subscribe_window_has_no_gap
test_windows_utf8_and_line_endings_are_reproducible
test_candidate_hash_is_platform_stable
test_standalone_drop_survives_ai_dependency_failures
```

### 13.3 运行顺序

先跑受影响最小集合，再扩大：

```bash
python scripts/compile_proto.py
python -m ruff check server agent analyzer
python -m pytest -q <targeted tests>
python scripts/check_migrations.py
python -m pytest -q

npm --prefix agent_runtime/pi-sidecar ci
npm --prefix agent_runtime/pi-sidecar test

npm --prefix web ci --include=dev
npm --prefix web test
npm --prefix web run lint
npm --prefix web run build
```

加入 uv 后，CI/本地正式入口改为 `uv sync --locked` 与 `uv run ...`，并保留传统环境所需的明确导出方式。

### 13.4 反假绿

禁止通过以下方式让门禁变绿：

- 删除、skip 或弱化能暴露真实缺陷的测试；
- 捕获广义异常后返回成功；
- 把 timeout/unavailable/partial 当作 pass；
- 使用 Mock Provider 冒充真实 Provider；
- 使用仓库内 Oracle 评估同一仓库生成的答案并称为独立；
- 手工把状态 JSON 改成 PASS；
- 只验证 API function 存在而不验证 UI 消费；
- 只验证 event 已发送而不验证 effect；
- 只比较自由文本“看起来合理”；
- 通过扩大预算、无限重试或固定操作顺序隐藏策略失败。

---

## 14. 代码变更纪律

- 优先做小而完整的纵向切片，不做数万行单提交重写。
- 每个切片先有失败测试或可复现 Trace，再修改生产代码。
- 不改与当前门禁无关的代码风格。
- 不覆盖用户已有修改；冲突时先说明具体文件和重叠范围。
- 新 abstraction 必须消除已有重复或建立真实边界；不增加只有一个调用者且不改善测试/替换性的空接口。
- 兼容层必须有 owner、使用计数、删除条件和测试；不得成为永久双实现。
- 不把所有逻辑塞进 Pydantic/ORM model、route 或 Repository。
- 删除代码前证明调用点归零并运行回归。
- 引入库前说明当前标准库/已有依赖为什么不够，并检查维护和许可证。
- 任何架构偏离本提示词的决定写 ADR，包含问题、选项、决策、后果和回退。

### 14.1 联网研究规则

遇到版本敏感或不确定的技术决策时，读取当前官方一手文档，不依赖记忆或二手博客。优先：

- FastAPI 官方关于 `APIRouter` 和 dependencies 的文档；
- SQLAlchemy 官方 Session/transaction 文档；
- Alembic 官方 migration operations/cookbook；
- PostgreSQL 官方锁、`SKIP LOCKED` 和事务文档；
- Pi SDK 当前 lock 对应版本的官方源码/类型；
- OpenAI 官方 prompting/agent/eval/safety 文档（若涉及模型策略）；
- uv 官方 lock/sync 文档（若采用 uv）。

研究结论必须转成：当前问题、约束、选择、未选方案、代码影响和测试，而不是把链接堆进报告。

---

## 15. 完成定义

只有以下全部成立，才能声明 `DEMO_READY`：

### 架构

- `main.py` 是轻量入口；routes 使用 APIRouter 且不反向依赖 main；
- application/domain/ports/adapters 依赖方向有自动检查；
- ORM models 单一来源；
- 新业务不再进入巨型兼容 Repository；
- Case 派生执行只有 Supervisor 一个 owner；
- 不存在新旧两套平行 Agent 调度链。

### 数据与可靠性

- 状态与 Outbox 同事务；
- 多 relay/supervisor 并发不产生重复 effect；
- Sidecar/Server/Worker 重启、断网和迟到事件可恢复；
- dead/recovery-required 状态可见且有 Runbook；
- PostgreSQL 是并发语义验证环境。

### Agent 与安全

- 模型能读取真实、裁剪、版本化 EvidenceProjection；
- READ_ONLY 和 PROPOSE_ONLY 有机器级零副作用证明；
- 所有写操作有完整 fence、幂等和审批绑定；
- 外部内容不能扩大权限；
- 模型输出不能绕过 Verifier 自封 CONFIRMED；
- 不记录 Secret 或私有思维链。

### 产品

- 问题驱动和数据驱动入口共用同一 Case 链；
- 用户能看到真实 AssistantMessage、Evidence、执行、因果、Gap 和建议；
- 用户可以暂停、恢复、停止、纠正和重新定向；
- AI Task 同时进入普通任务页；
- 刷新、重连和服务重启不丢历史或重复 UI；
- 普通 Drop 不依赖任何 AI 组件。

### 工程

- Python/Node 锁跨平台可复现，无旧仓库 self-reference；
- Linux/Windows 文本、测试和候选 hash 稳定；
- Python、Sidecar、Web、migration、build 门禁通过；
- 关键依赖漏洞已按实际调用链评估；
- Candidate 内容寻址且三节点 receipts 一致；
- 当前 Runbook 已实际演练。

### 评测

- mandatory public assertions 全部真实 PASS；
- 引用有效率 100%，无效 Claim binding 为 0；
- duplicate effect、旧 revision 副作用、STOP 后副作用为 0；
- Agent 的 Evidence fork 行为变化有预登记 Oracle 和 A/A control；
- 故障 cleanup 和 final health 通过；
- optional Holdout 状态诚实记录。

如果任一核心条件缺失，不得使用“全部完成、生产可用、完全收敛或独立验证通过”。

---

## 16. 每轮恢复协议

长任务、对话压缩或进程重启后，按固定顺序恢复：

```text
读取用户最新要求
→ 读取 git status / HEAD / diff
→ 读取机器状态 JSON 和最近 evidence JSONL
→ 验证状态文件指向当前工作树
→ 读取最近失败测试和唯一 next_action
→ 重跑最小失败例确认仍成立
→ 继续当前门禁，不重新发明路线图
```

每个工作闭环结束时，状态生成器写入：

- 当前 gate；
- 本轮事实和决策；
- 修改文件；
- 执行命令及退出码；
- 测试/构建报告路径；
- 新增或关闭的风险；
- 是否可安全恢复；
- 唯一 `next_action`；
- 外部阻塞及最小所需输入。

不要把完整日志塞入状态 JSON；保存路径和摘要，原始日志单独存放。

---

## 17. 最终交付格式

最终回答必须按以下顺序：

1. **结果**：当前达到 `LOCAL_GREEN / INTEGRATION_READY / DEMO_READY / INDEPENDENTLY_VALIDATED` 中哪一级。
2. **架构变化**：组合根、边界、事务、Runtime、Evidence 和 UI 的关键变化。
3. **兼容性**：保留的 API、迁移路径和普通 Drop 结果。
4. **验证证据**：命令、通过/失败/跳过数量、环境、candidate digest 和报告路径。
5. **真实纵向链路**：

```text
Turn
→ Snapshot/Cycle/ModelRequest
→ Proposal/Decision
→ Plan/Campaign/ExecutionUnit
→ Task 或 SourceCall
→ Artifact/CaseEvidence/Projection
→ Outbox/Wakeup
→ 新 Cycle
→ CausalGraph/Conclusion/Recommendation
→ AssistantMessage/Workspace/SSE
```

6. **安全和恢复**：policy、fence、审批、崩溃恢复和 cleanup 结果。
7. **未完成项**：只列真实存在的限制、影响和下一动作。
8. **外部验证状态**：明确区分 public development、formal 和 independent holdout。

失败或阻塞时也使用同一格式，但结果必须如实降级，并给出最小恢复命令。

---

## 18. 执行开始指令

现在开始执行：

1. 不要先复述整份提示词。
2. 先检查当前 Git、环境、依赖、迁移、生成文件和现有状态报告。
3. 运行能确认 C0 真实状态的最小验证。
4. 输出基线报告和唯一下一动作。
5. 如果没有需要用户授权的阻塞，直接修复 C0，并在通过后进入 C1。
6. 持续推进直到当前环境能完成的所有门禁收敛；外部 VM/Secret/Authority 缺失时，完成其余工作后再请求最小输入。

不要用更多规划替代实现，不要用更多测试数量替代真实闭环，也不要用架构拆文件替代依赖方向和事务正确性。

---

## 附录 A：本提示词采用的关键设计依据

以下资料用于解释本提示词的选择；执行时应重新读取当前版本：

- OpenAI Model Guidance：复杂编码 Prompt 应聚焦目标、约束、授权边界、成功标准和输出格式，并通过代表性 eval 验证，避免重复指令。
  - https://developers.openai.com/api/docs/guides/latest-model
- FastAPI Bigger Applications：使用独立 `APIRouter`、dependencies 和 `include_router()` 组织大型应用。
  - https://fastapi.tiangolo.com/tutorial/bigger-applications/
- SQLAlchemy Session Basics：Session/transaction 生命周期应由外层调用边界管理，并使用 context manager 明确 commit/rollback。
  - https://docs.sqlalchemy.org/en/20/orm/session_basics.html
- Alembic Cookbook/Operations：Schema 迁移应使用明确迁移操作，并针对具体应用设计 upgrade/downgrade。
  - https://alembic.sqlalchemy.org/en/latest/cookbook.html
  - https://alembic.sqlalchemy.org/en/latest/ops.html
- PostgreSQL Locking：`SKIP LOCKED` 适合多消费者竞争的 queue-like 表，不适合一般一致性查询。
  - https://www.postgresql.org/docs/current/sql-select.html
- Transactional Outbox：业务状态和事件同事务提交，由独立 relay 进行至少一次投递，consumer 负责幂等。
  - https://learn.microsoft.com/en-us/azure/architecture/databases/guide/transactional-out-box-cosmos
- uv Projects：`uv.lock` 是跨平台项目锁；locked/frozen sync 用于可复现安装。
  - https://docs.astral.sh/uv/guides/projects/
  - https://docs.astral.sh/uv/concepts/projects/sync/

## 附录 B：明确不采用的方案

除非有测量数据和用户批准，不采用：

- 把 FastAPI 控制面继续拆成多个业务微服务；
- 为 Outbox 引入 Kafka/Redis，仅为显示“事件驱动”；
- 用完整 Event Sourcing 替换当前关系模型；
- 让 Pi/LLM 直接访问 Shell、SSH、SQL、文件系统或任意 MCP；
- 用自由文本模型判断代替确定性权限、幂等和 Verifier；
- 重写全部前端或替换现有设计系统；
- 无条件升级所有依赖到最新版；
- 为追求测试全绿而取消 Windows、PostgreSQL、真实 Provider 或故障场景。

这些方案不是永远禁止；只有当前约束变化且 ADR 证明收益超过迁移和运行成本时才重新评估。
