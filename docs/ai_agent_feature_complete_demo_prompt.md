# Mini-Drop 内嵌诊断 Agent 功能完整演示交付总提示词 v5.x（已归档）

> 文档受众：接手本仓库并持续修改、测试、部署和验收的 AI/Codex
> 文档状态：历史长版及 2026-08-16 审计批注；已由 `docs/ai_agent_feature_complete_demo_prompt_v6.md` 完整替代，不再作为执行入口
> 事实基线：`main@41f41a04f94cbe19e10c7fb41061f8a42ed99637` 加当前未提交工作树；开始执行时必须重新计算完整内容指纹
> 关联设计附件：`docs/ai_agent_runtime_integration_plan.md`
> 本文作用：保留旧需求和评测细节用于追溯；执行时只使用 v6 主提示词

## 1. 你的任务

你要把当前 Mini-Drop 从“自然语言分类入口 + 确定性规则/编排器 + 若干 Agent 组件”完整收敛为真正的内嵌诊断 Agent，并最终交付一个能够在当前 Mini-Drop 单用户/实验集群中直接部署、覆盖主要诊断场景、完整跑通功能链路并稳定演示的 Agent Beta。

这是一个单一交付目标，不是分批交付计划。本文中的 G0-G11 仅用于表示依赖和验证顺序，不是可向用户交付的阶段版本，不允许完成其中一部分就停止、汇报“首版完成”或等待用户再次发送“继续”。执行 AI 必须在权限范围内持续修改代码、补测试、部署当前候选、运行真实验收、修复失败并自动领取下一项，直至最终 Definition of Done 全部满足。

开始实现前，必须完整阅读本文，并按当前 G 阶段定向读取 `docs/ai_agent_runtime_integration_plan.md` 的相关协议、当前数据库模型/迁移、Agent Runtime、Supervisor、Worker、前端和 VM Harness；不要每轮把整份附件无差别塞进模型上下文。关联设计中的 ResourceRef、EvidenceAttachment、CollectionFingerprint、InvestigationPlan、RuntimeBinding、Tool Envelope、Case Event、Membership Snapshot、Fanout Run、AC-01～AC-23 和 V0～V9 继续作为详细参考；其中关于迁移编号、当前完成度和验收结果的描述仅是历史线索，必须由当前代码、Alembic 当前唯一 head 和机器结果重新判定。

本项目当前不以真实生产投运、合规认证、大规模多租户和极限并发为目标。不得把大量上下文和时间消耗在全面漏洞治理、SBOM、密钥轮换、全量供应链审计、多发布周期兼容或灾难恢复体系上。只保留防止演示环境被误操作、Secret 泄漏、Agent 任意执行和故障注入无法清理所需的最小安全边界。

最终 `DEMO_READY` 交付必须同时包含：

- 真正由 Agent 持续参与的事故调查主循环；
- 两种用户入口、持续会话、解释、纠错、补证、干预和结束能力；
- 原有 Drop 采集服务、原生 Task、Collector、Artifact 和 Worker 数据面的完整复用；
- 已有数据复用、Evidence 证据链、计划控制、集群扇出、MCP、领域 Skill 和部署承载评估；
- 面向非技术用户和专家用户均可用的完整前端；
- 身份、范围、授权、预算、最小安全、审计、恢复、基础观测和演示环境回退；
- 共同基线与按节点角色/异常假设差异化的集群采集；
- 受控低风险查询命令、领域 Skill、知识库检索与 MCP 补证；
- 主根因、贡献因素、放大因素、传播路径、症状和伴生异常的复合因果分析；
- 当前精确候选在三节点环境上的功能、质量、恢复和最终清理证据；
- 固定公开 Case、执行 Agent 不可见的 Holdout Case、真实故障生效证明、独立评分和需求追踪矩阵；
- 安装、配置、演示运行、常见故障和环境清理文档。

不要通过继续增加孤立模块、空 API、数据类、说明文档或 Mock 演示来宣称完成。下一阶段唯一优先目标是跑通并证明下面这条真实纵向链路：

```text
用户自然语言或已有数据
→ 持久 Case Turn
→ Pi Agent Runtime
→ Mini-Drop 受控 Tool Catalog
→ 持久 InvestigationPlan
→ CaseSupervisor 编译 Campaign/ExecutionUnit
   ├→ 原生 Task/Fanout → Drop Worker 执行 Collector/Query → Artifact
   └→ 受治理 SourceCall → SourceGateway/MCP Result
→ 统一写入 canonical Case Evidence Store
→ Durable Event/Outbox 唤醒 Pi
→ Pi 更新假设、补采、解释或结束
→ 用户可追问、纠错、暂停、转向和终止
```

只要这条链路还没有在当前代码、当前候选包和当前实验环境中形成机器证据，就不得把系统描述为“完整 Agent”“已收敛”或“可稳定演示”。本项目不以真实生产投运为目标，任何时候都不得使用“生产可用”。

### 1.1 v6 审计复位与执行优先级

当前版本已经新增大量表、API、Sidecar 工具、测试和报告，但 2026-08-16 的只读审计确认：这些组件尚未形成真实业务闭环。执行 AI 必须把所有进度文件中的勾选项降为“待代码和运行证据复核”，先修下面十二个审计回归，再继续扩展功能：

1. Pi 只能看到 Evidence 元数据，看不到 CPU 数值、日志、Trace、连接、火焰图等安全投影；
2. Pi 的最终回答只在 Runtime Event 中，未成为持久 AssistantMessage，前端用户看不到；
3. `investigation_directive` 把模型锁进固定 Collector 顺序，稳定性由禁止推理分叉实现，不是证据驱动的自适应调查；
4. `ANSWER_ONLY` 只靠提示词约束，Pi 仍能看到并调用写工具；
5. Sidecar Session 上下文只在首次创建时写入，后续 Turn/Wakeup 继续使用陈旧快照；
6. Session、seq、未确认事件和最后回答主要在内存中，重启会丢 Wakeup，重复 subscribe 会重复事件；
7. pause/stop/correction 没有调用 Pi `abort/steer`，迟到 Tool Call 没有 generation/scope/plan fence，停止后仍可能创建 Task；
8. Campaign 只是临时编译为普通 Step，逻辑 service/host/workload 不筛成员、不验 Collector capability，Fanout 完成后不会自动 aggregate；
9. Pi 路径绕开 MCP、容量评估和旧分析能力，Query、PlanDriver、Fanout、旧 Agent 与 Pi 形成多个调度中心；
10. `finish` 没有结构化因果结论和真实 verifier，只验证 Evidence ID 是否存在；
11. 前端 Workbench 使用错误的 Axios 返回层级，Runtime/Evidence/Campaign/Capacity API 大量未消费，AI 任务仍被第一页部分过滤；
12. Public/Holdout/VM Runner 可以把 PARTIAL、AWAITING、假值或自签结果判为成功，候选包和 VM 报告也没有严格绑定当前全部文件内容。

执行顺序必须服从“用户可见纵向闭环优先”：

```text
Evidence 可读且回答可见
→ 单一 Supervisor 与机器级 Turn 权限
→ 可中断、可恢复、上下文会刷新
→ 单节点真实三轮自适应调查
→ 集群异构 Campaign 与 MCP/Query/Skill
→ 复合因果、精确 Gap、修复与验证
→ 完整前端与当前候选 VM 验收
→ 独立 Holdout（有外部 Authority 时）
```

在前四项通过前，禁止优先建设新的空数据类、独立 API、评测包装、状态文档或更多静态 Skill。每个新增对象必须在同一个 Pull-through Test 中被真实 Turn、Tool、Task/SourceCall、Evidence、AssistantMessage 或 UI 消费，否则不算进度。

最终状态严格区分：

- `DEMO_READY`：本文规定的本地、浏览器、真实 Provider、当前候选和三节点公开业务门禁全部通过；这是本项目当前交付目标；
- `INDEPENDENTLY_VALIDATED`：在 `DEMO_READY` 之上，再导入由候选仓库之外的只读 Acceptance Authority 签名的 Holdout 成绩；
- 缺少外部 Evaluator 时可以交付 `DEMO_READY`，但状态只能是 `AWAITING_EXTERNAL_HOLDOUT`，不得声称盲测通过；
- 本地自签、开发者公钥、仓库内 Oracle 或施工 AI 自己生成的隐藏集，永远不能升级为 `INDEPENDENTLY_VALIDATED`。

## 2. 不可改变的产品和架构边界

### 2.1 Mini-Drop 仍然是执行与事实系统

- 第一页及原有 Task、Collector、Artifact、Worker、Supervisor 数据面继续保留；
- AI 工作区仍然是第二入口，不取代原有采集服务；
- Pi/其他模型不能直接运行 Bash、Shell、perf、eBPF、kubectl、SQL、SSH 或任意 MCP；
- 模型只能调用 Mini-Drop 注册、投影、鉴权和审计后的工具；
- Mini-Drop 负责 Case、Evidence、Plan、Task/SourceCall、权限、预算、风险、去重、取消、审计和恢复；
- Pi 负责会话、模型回合、工具选择、上下文压缩、`steer`、`follow_up` 和 `abort`；
- Pi Session 不是业务真源。Sidecar 丢失全部内存后，必须能从 Mini-Drop 持久状态重建；
- 不保存或展示模型私有思维链，只保存可复核的决策摘要、假设、证据引用、反证、缺失事实、工具理由和局限；
- 当前阶段不开放自动修复，不允许模型绕过原生 Task/Supervisor 执行采集；
- 前端业务重构必须等后端 Turn/Event/Plan/Evidence/Command 契约稳定后再开始，避免用界面掩盖错误语义；但完整前端是最终交付硬门禁，不能作为“后续版本”；
- 在当前候选通过真实 VM 门禁前，冻结 E9，不继续删除旧接口、旧字段或规则降级链路。

### 2.2 Agent Runtime 必须可替换

继续使用 `AgentRuntimePort` 隔离领域系统和 Pi。Pi 是首个候选，不是不可替换的业务依赖：

```text
Case API / Turn Router
        ↓
AgentRuntimePort
        ├── DeterministicAgentRuntime  # 降级、回归、对照
        └── PiAgentRuntimeAdapter      # 当前首选候选
                 ↓
             Pi Sidecar
```

不得把 Pi 的内部消息结构、Session 文件或 SDK 类型直接写入 Case、Evidence、Plan、Task 等领域表。

只有完成本文 G1/G2 的 Contract 和 Shadow 门禁后，才允许 Pi 创建可执行计划。如果 Pi 无法通过门禁，保留全部 Mini-Drop 领域改造，只替换 `AgentRuntimePort` 后面的实现；不要再自研一套完整通用 Agent Loop。

## 3. 当前代码真实状态

接手时必须重新验证，不允许仅复制下面结论。但在 `41f41a0` 上已经确认：

### 3.1 已有且应保留

- Case、消息、Evidence Contract、确定性分析器、Supervisor、原生 Task 和 Worker 数据面；
- ResourceRef、EvidenceAttachment、InvestigationPlan/Step、EvidenceReview 的首版模型和迁移；
- EnvironmentProfile、ClusterResource、MembershipSnapshot、FanoutCollectionRun、覆盖率的首版实现；
- MCP Client/Server、SourceGateway、MissingFact Resolver、投影与预算控制的首版实现；
- DeploymentRequirement 与保守容量检查原型；
- 本地总门禁、候选包构建和 VM 脚本框架；
- RulesOnly/Deterministic Runtime，可继续作为降级和对照组。

### 3.2 当前已接线但不能误判为闭环

以下能力在当前工作树中确实存在，应保留并修通，而不是重复再造：

- `POST /api/v1/cases/{case_id}/agent/turn` 已能在 PI/PI_SHADOW 模式进入 `AgentRuntimePort`；
- Pi Sidecar 0.83.0、内部 Token、受控 Query Tool、RuntimeBinding/Turn/Event 表和 0021/0022 迁移已经存在；
- Query 可以创建原生 Task，Artifact 可物化为首版 CaseEvidence，Task 完成时会尝试一次 `follow_up`；
- Campaign、Skill、Knowledge、Capacity、Runtime State、Evidence API 已有首版后端接口；
- 前端已有部分 Runtime 文案、火焰图/TopN/eBPF 预览和尚未接通的 Workbench；
- Python、Sidecar、Web、Lint、Build 和迁移测试当前均可通过。

这些事实只证明“接线和脚手架存在”，不证明模型读到了采集内容、真正决定了下一步、用户收到了回答或重启后还能继续。

### 3.3 当前必须修复的真实断点

1. `CaseContextSnapshot` 与 `get_case_snapshot` 只返回 Evidence 元数据，缺少可供模型判断的安全内容投影；
2. `find_reusable_evidence`、`evaluate_hypotheses` 等工具返回的信息不足以完成证据解释和因果判断；
3. Assistant final 只进入 Runtime Event，没有成为持久 CaseMessage；AgentTurn 长期停留在 ACCEPTED，前端不消费 final；
4. `ANSWER_ONLY/execute_safe_tools/max_tool_calls` 没有被传到 Sidecar 并机器级裁剪 Tool Catalog；
5. `investigation_directive.py` 使用固定证据顺序并禁止模型新方向，必须改为只约束范围、风险、预算和输出合同的 `InvestigationPolicyContext`；
6. Sidecar 命中已有 Session 后不刷新 Context，每 Turn 重复 subscribe，Session/seq/answer 均为内存状态；
7. Task wake 是一次 best-effort `follow_up`，异常被吞，Sidecar 重启后没有持久 RuntimeWakeup 重放；
8. Case pause/stop/correction 只控制旧诊断或已存在 Task，不调用 Pi abort/steer，也没有阻止迟到 Tool Call 的完整 Fence；
9. Plan Tool 要求的 CAS 信息没有全部提供给模型，真实 Pi 容易绕过 Plan 直接走 Query；
10. 现有 `CaseSupervisor` 只是旧 `AutonomousIncidentAgent` 的租约包装，不是 Case 派生 Task/SourceCall 的唯一编译和写入者；
11. PlanDriver、Query Gateway、Fanout、旧 Orchestrator、旧 Agent 和 Pi 仍可形成多个推进/创建入口；
12. Campaign 没有持久 Assignment/ExecutionUnit，service/host/workload selector 不真正筛选，成员以 Worker 为中心且不验 operation capability；
13. Fanout Task 终态不会自动聚合 Run 和 PlanStep；resume 也不能恢复已取消的原生任务；
14. 查询目标无法解析时会回退第一实例、任意在线 Worker或 PID 1，而不是生成 `TARGET_UNRESOLVED`；
15. Evidence 去重只看同 Collector 的 DONE Task，忽略目标身份、时间窗、参数、版本、质量、新鲜度和用户排除；
16. MCP、Capacity、Skill/Knowledge 与 Pi 主循环分离；MCP Result 没有统一物化为 canonical CaseEvidence；
17. Skill 主要是静态 Prompt 片段，选择缺少 hash/reason/negative trigger/tool policy；Knowledge 返回的文档内容对 Pi 不可读；
18. `finish` 只接受 summary/evidence IDs，尚无 Claim、CausalGraph、EvidenceGap、Recommendation 和可执行 Verifier；
19. Workbench 读取了已经被 Axios interceptor 解包后的 `.data`，真实接口下计划、审查和 Fanout 为空；多个新 API 只有 client 声明没有页面调用；
20. AI 派生的部分 Task 被第一页按 `diagnosis_step_id/registered_probe` 过滤，人工和 AI 异构 Campaign UI 都未完成；
21. 候选指纹没有覆盖未跟踪文件内容，release 可覆盖，部署脚本没有完整安装/校验 Sidecar 和三节点同一候选；
22. Public Runner 接受 PARTIAL/AWAITING，VM `check()` 不验证谓词，Holdout importer 可由调用者自带公钥和指纹自签 VERIFIED；
23. 当前已有 VM 报告绑定的是旧候选，不能证明当前未提交工作树；执行时必须重新部署和复验。

### 3.4 不可信的完成信号

以下结果只能说明局部模块存在，不能证明 Agent 闭环完成：

- `/health` 返回 200；
- Sidecar 能返回固定消息；
- Pi 生成一个 JSON Plan，但没有使用真实 Case/Evidence；
- PlanDriver 单独创建了 Task；
- 测试手工创建三轮 Plan；
- MCP 独立 API 能返回数据；
- 前端显示“调查中”；
- 旧 VM 报告、旧 release 或不含当前 diff 的包通过；
- 使用仓库内公开 Oracle 调参后宣称“隐藏评测”通过；
- 故障注入命令成功但没有独立探针证明故障真实生效；
- 使用 Fixture、预制 Evidence 或 Scripted Provider 代替最终真实模型/Worker/服务链路；
- 单元测试全绿，但真实工具鉴权、事件唤醒、重启恢复没有覆盖。
- 只返回 AcceptedTurn，但没有持久 AssistantMessage 和 `turn.completed`；
- 模型被提示“请调用某个确定工具”后完成一次 Tool Call；
- 固定 Planner 先生成三步，再把它记成三轮自适应调查；
- API client function 已声明，但浏览器从未发出对应请求；
- 测试 mock 了错误的 Axios 双层 `.data` 仍然通过；
- Runner 中函数不抛异常，但谓词实际返回 false、空列表或 FAILED；
- `PARTIAL`、`AWAITING_*`、`SKIPPED` 或 `NOT_RUN` 被计作通过或返回退出码 0；
- 开发者现场生成 key 并自签的成绩被标为独立 VERIFIED；
- 只哈希 tracked diff 或 `index.html` 的候选包被称为不可变候选。

## 4. 应用场景、需要的能力与设计论证

执行 AI 不能只围绕已有测试补接口。必须用下面场景反推领域模型、工具、Skill、知识、采集和验收。各场景最终共享同一个 Case、Evidence、Plan、Task 和报告体系，不得各自形成独立演示逻辑。

| 场景 | 用户期望 | 系统必须采取的方案 | 设计理由 |
|---|---|---|---|
| 模糊语言自主定位 | 用户只描述“服务变慢/报错”，让 AI 自行推进 | Agent 建立候选假设和 Missing Facts，先复用，再执行低风险查询/采集，事件唤醒后持续收敛 | 证明 AI 不只是意图分类器 |
| 已有 Task/Collection 分析 | 用户从第一页把现有数据交给 AI | ResourceRef → EvidenceEnvelope → Inventory；先解释已有数据，仅补缺口 | 避免重复采集，复用 Drop 核心资产 |
| 解释型追问 | 用户询问图、字段、火焰图或某条证据含义 | `ANSWER_ONLY` + 只读 Evidence/Knowledge Tool，不创建新调查 | 防止每次追问都重启流程 |
| 用户纠错和补证 | 用户改目标、时间、假设或 `@` 新数据 | 确定性更新 scope/evidence revision，废弃旧计划，再 steer Agent | 用户必须真正参与调查 |
| 单节点单根因 | CPU 热点、锁、OOM、磁盘满、网络丢包 | 对应领域 Skill + EvidenceContract + 最小充分采集 | 为复杂调查提供可信基础能力 |
| 同构集群对比 | 多个相同角色节点中识别异常实例 | 共同轻量基线 + 相同 Collector Fanout + 健康对照 | 同构采集仍是建立可比性的必要能力 |
| 异构集群调查 | API、数据库、网关需要不同数据 | CollectionCampaign 把共同基线与按角色/异常的差异化 Assignment 编译为单目标 Task | 解决“所有机器只能采 CPU”问题 |
| 复合传播故障 | 多个异常同时出现，需要区分根因、传播、放大和症状 | 时间/拓扑约束的 Causal Graph、逐边 EvidenceContract、差异化补证和 Causal Oracle v3 | 多异常共存不等于多个独立根因 |
| 间歇性/历史故障 | 故障已恢复或偶发，当前采集可能正常 | 使用历史 Evidence、变更、基线、时间窗对齐和明确的不可恢复缺口 | 避免用当前健康状态否认历史故障 |
| 数据冲突或采集失败 | 不同来源矛盾，部分 Agent/Collector 失败 | EvidenceGapReport 精确说明已知、未知、失败原因、影响和下一动作 | 禁止泛化“证据不足” |
| 低风险即时查询 | Agent 需要进程、连接、服务状态、短日志等轻量事实 | QueryOperation Registry → 原生 `task_kind=QUERY,risk=READ_LOW` Task → Artifact/Evidence | 提高 Agent 灵活性但不开放任意 Shell |
| 外部系统补证 | 需要 CMDB、拓扑、变更、K8s/Swarm 或监控信息 | Missing Fact → SourceGateway/MCP → EvidenceProjection | MCP 扩充事实来源，不取代证据治理 |
| 知识辅助诊断 | 需要理解 GC、火焰图、Linux 指标和企业运行手册 | Skill 负责调查策略，Knowledge Retrieval 负责按需解释，均带版本和引用 | 避免把全部知识塞入 Prompt，区分知识和事实 |
| 部署承载评估 | 判断新进程/服务能否在环境部署 | DeploymentRequirement + 容量/峰值/N-1/约束 Evidence；数据不足时精确列缺口 | 复用同一 Agent、Evidence 和工具体系 |
| Runtime/模型不可用 | Pi、Provider 或 MCP 暂时失效 | Case 状态保留，普通 Drop 继续工作，可切 deterministic 并清晰显示降级 | 演示时也必须避免整套系统被 AI 拖垮 |

### 4.1 总体方案为什么采用“Agent + 可信执行内核”

纯规则流程难以处理开放式追问、差异化补证和复杂因果链；直接把 Shell、MCP 和集群权限交给通用 Agent 又会绕开 Mini-Drop 的任务、取消、审计和数据资产。目标方案因此是：

```text
Pi/可替换 Agent Runtime
  负责：理解、假设、Missing Fact、策略选择、工具选择、因果候选、解释和停止

Mini-Drop Domain Kernel
  负责：身份、范围、Evidence、Plan、采集矩阵、Query Registry、Task、预算、取消、事件和校验

Drop Worker/Data Plane
  负责：执行注册 Collector/Query Operation，生成 Artifact

Deterministic Analyzers/Verifier
  负责：结构化分析、因果边校验、报告校验、降级和对照
```

该方案能够提高 AI 参与度，同时保证人工采集和 AI 采集走同一条真实链路。Pi 若不适配，只替换 Runtime，不推翻其余领域设计。

### 4.2 当前还欠缺、必须补齐的设计

1. **Acquisition Campaign**：当前一个集群 Step 基本只能把同一个 Collector 扇出到多个目标，缺少共同基线和差异化 Assignment；
2. **Query Operation Gateway**：缺少可由人和 AI 共用、经原生 Task 执行的结构化低风险查询目录；
3. **Skill Runtime**：已有开关和设计描述，但没有完整的版本、装载、选择、正反触发和工具约束闭环；
4. **Knowledge Base**：当前知识映射偏静态，缺少文档摄取、分块、混合检索、引用、新鲜度和环境范围；
5. **Causal Graph v2**：当前图主要由最终分类投影而来，缺少原因角色、时间约束、拓扑验证、逐边证据和伴生异常；
6. **Causal Oracle/评分**：当前复合案例只评分 `compound_incident + domain/location`，无法惩罚“发现数据库压力但把它误判为根因”；
7. **Evidence Gap Report**：缺少“采集尝试、失败原因、当前数据含义、不能证明什么和下一动作”的统一契约；
8. **Repair Recommendation Contract**：当前建议按领域泛化，缺少与原因/传播边绑定的缓解、根修复、放大治理和验证；
9. **Turn/Runtime 主链**：用户回合仍没有真正由 Pi 持续掌握，Sidecar 的 Assistant/Tool/结束事件也没有可靠回传和持久化；
10. **唯一编排权威**：Plan、Campaign、Query、PlanDriver 和旧 Autonomous Supervisor 可能各自推进，需要收敛为一个 Supervisor；
11. **唯一 Evidence 真源**：Attachment、旧 DiagnosisEvidence 和 EvidenceEnvelope 还没有统一的 Case Evidence Store；
12. **逻辑资源级集群模型**：Worker 不能等同于调查目标，同一 Worker 上的服务、容器、进程和宿主必须独立建模；
13. **真实可观测能力**：当前三节点环境缺少验证完整复合传播链所需的延迟、GC 和 span 事实，需要 Capability Gate 和受控真实工作负载；
14. **盲测与防泄漏**：公开回归、执行模型不可见的 Holdout、故障生效探针和独立评分器尚未分离；
15. **规则外假设**：RulesOnly 只能作为候选和对照，不能继续限制 Agent 的候选根因全集；
16. **真实端到端验收**：单测和旧 VM 报告尚不能证明以上能力在当前候选跑通。

这些缺口均属于最终 Agent Beta 的核心范围，不得标记为“以后再做”。

## 5. 核心设计决策与领域契约

### 5.1 会话不是“每句话都新建调查”

实现持久化 `TurnDisposition`，至少包含：

- `ANSWER_ONLY`：解释现有数据、证据、状态或概念；只允许只读工具，不创建新 Plan/Task；
- `CORRECT_CONTEXT`：用户纠正目标、范围、时间或假设；更新 scope revision，使旧计划失效，然后唤醒 Agent；
- `ATTACH_EVIDENCE`：用户通过 ResourceRef/`@` 补充已有数据；先物化/附加 Evidence，再唤醒 Agent；
- `INVESTIGATE`：开始或继续调查；允许提出 Plan；
- `CONTROL`：暂停、恢复、停止、取消步骤、改目标、重排；先走确定性命令通道，再向 Pi 发送 `steer`/`abort`；
- `DEPLOYMENT_ASSESSMENT`：使用独立工具策略和报告契约，不与事故调查 Plan 混写。

暂停、停止、取消等安全控制不得等待模型理解。除 `CONTROL` 外的自然语言由 Pi 参与理解和回答；不得继续用一次性 `classify_turn → start_case_diagnosis` 代替 Agent 回合。

`TurnDisposition` 必须持久化。`ANSWER_ONLY` 只允许只读工具，并以本回合前后的 Plan、Campaign、ExecutionUnit、Task 和“采集/推理唤醒 Outbox”增量全部为 0 作为机器断言；AssistantMessage/会话 SSE 事件仍须正常持久化。解释与调查有歧义时，默认解释或询问，不得擅自采集。

Conversation 生命周期与 Investigation Run 生命周期分离。`RESOLVED/STOPPED/INSUFFICIENT_EVIDENCE` 后仍允许解释、质疑和读取历史证据；这些状态只禁止自动创建新 Task。`INSUFFICIENT_EVIDENCE` 是一次 Run 的结果，不是不可逆关闭 Case。用户补证或显式继续后创建新的 scope/plan revision 和 Investigation Run。

### 5.2 InvestigationPlan 是唯一编排权威

业务从属关系固定为：

```text
InvestigationPlanRevision
└── PlanStepRevision(kind = ACQUIRE_EVIDENCE)
    └── CampaignRevision
        ├── common_baseline
        └── AcquisitionAssignment[]
            └── operation_ref
                ├── Worker CollectorOperation
                ├── Worker QueryOperation
                └── Control-plane SourceOperation（MCP/SourceGateway）
                    └── ExecutionUnit → Task 或 SourceCall → Artifact → CaseEvidence
```

`PlanStep.kind` 固定为 `ACQUIRE_EVIDENCE | ANALYZE | ASK_USER | WAIT_EVENT | FINISH | DEPLOYMENT_ASSESSMENT`。`ANSWER_ONLY` 是 TurnDisposition，不是暗藏的 Plan；单次 Query 或 MCP 请求也必须编译为单 Assignment Campaign，不能形成第二套 Query/MCP 调度器。Campaign 只描述获取矩阵，不是调度器。

只有持有 Case Lease 的统一 `CaseSupervisor` 可以把当前 Plan/Campaign Revision 编译为 ExecutionUnit，并创建由 Case/Plan 派生的原生 Task 或受治理的 SourceCall。Pi、Case API、PlanDriver、MCP、旧 `AutonomousIncidentAgent` 和旧 sweeper 均不得绕过它直接创建 Case 派生 Task；它们要么并入统一 Supervisor，要么在 Pi 模式关闭。第一页用户直接创建、且不属于 Case Plan 的普通 Drop Task 继续走现有入口和 Supervisor，不得被本规则阻断。Deterministic 调查也必须经过同一 CaseSupervisor。

Pi 只能通过 `upsert_investigation_plan` 提议完整的新版本，Mini-Drop 必须：

1. 校验 Case row、scope、plan 和 campaign revision；
2. 校验 operation、逻辑 target、risk、budget、权限、能力和依赖；
3. 固化逻辑资源级 Membership Snapshot；
4. 执行 CollectionFingerprint 去重和 Evidence 复用决策；
5. 持久化 Plan/Campaign Revision、AgentDecisionRecord 和编译结果；
6. 由唯一 Supervisor 创建 Task/SourceCall；
7. 把接受、拒绝、修改、复用和 stale 原因返回 Pi。

`WAITING_APPROVAL` 永远不可调度；批准后记录审计并迁移为 `QUEUED`。调度前验证全部 `depends_on` 且拒绝循环，前置失败/取消使下游 `BLOCKED`。所有删除、重排、改目标、改操作、锁定和禁用都产生新 Revision，不得原地修改历史。`step_id` 跨版本稳定，`step_revision_id` 不可变并记录 `supersedes_step_revision_id`；运行中 retarget 先取消旧 Task，再创建替代 Step。

每个由 Case/Plan 派生的 Task/SourceCall 必须保存 `plan_step_id/step_revision_id/campaign_id/campaign_revision/assignment_id/execution_unit_id`；第一页 standalone Drop Task 不要求这些字段，但被 `@` 到 Case 后通过 Evidence lineage 关联。模型不得直接写数据库，也不得直接枚举集群节点。

### 5.3 canonical Case Evidence Store 是唯一事实真源

所有进入 AI 的数据必须经过：

```text
ResourceRef
→ ReferenceResolver
→ Artifact/Source Result validation
→ EvidenceEnvelope
→ canonical Case Evidence Store
→ EvidenceAttachment/旧 DiagnosisEvidence 兼容投影
→ CaseContextSnapshot projection
```

每条 Evidence 至少具有：

- `evidence_id`、`tenant_id`、`case_id`；
- `investigation_run_id` 与来源 Task/Artifact/SourceCall/人工输入的不可变 lineage；
- target identity、资源 incarnation 和采集时 Membership Snapshot；
- `event_time_start/end`、`ingested_at`、`clock_id`、`clock_offset_ms`、`clock_uncertainty_ms` 和新鲜度；
- schema/collector version、内容哈希、质量状态；
- `source_channel = COLLECTOR | QUERY | MCP | USER`；
- `data_origin = FIXTURE | REPLAY | LIVE`；Run 级 `realness = R0 | R1 | R2 | R3 | R4` 单独保存，不混进 Evidence 来源；
- `ACTIVE/LOW_TRUST/EXCLUDED/STALE` 状态；
- 原始对象定位信息和安全裁剪后的模型投影。

必须扩展现有 Evidence 表或迁移为一个 canonical Store，禁止新增第四套并行事实表。`EvidenceEnvelope` 只有持久化成功后才算 Evidence；Attachment 只保存 ResourceRef 与 Evidence 的关联，不能充当事实本身。`EXCLUDED/LOW_TRUST` 不删除来源 ID，而由最新 EvidenceReviewRevision 计算有效状态。

引用一个 Task 但没有生成或关联 CaseEvidence，不算“数据已提交给 AI”。Evidence 被降信任、排除或对目标 Claim 变得不适用后，必须使相关假设和结论失效并触发重新评估。新鲜度必须区分 `wall_clock_freshness` 与 `claim_window_applicability`：历史/间歇事故 Evidence 即使相对当前时间陈旧，只要覆盖目标事故窗口、身份和 Claim 仍可引用；当前状态 Claim 则必须满足 TTL。`finish_investigation` 必须逐条验证 Evidence 存在、同 tenant、同 case、状态允许、Claim 窗口/目标/身份匹配，并在同一事务中持久化 Conclusion、引用、限制与 Case/Run 状态。

### 5.4 连续执行和可打断

- READ_LOW 且在预算、权限、范围和功能开关内的步骤可以自动执行，不逐轮要求用户确认；
- READ_ELEVATED 及更高风险只能提出建议并等待批准；
- Task 完成、失败、超时、取消、Evidence 状态变化通过 Durable Outbox 唤醒 Agent，不由模型轮询；
- 用户暂停后不得创建新 Task；停止后必须阻止迟到事件推进 Case；
- 用户改目标或范围时递增 scope revision，旧 Tool Call、旧 Plan 和迟到结果不能写入新 revision；
- `abort` 用于中断当前模型回合，原始 Task 取消仍由 Mini-Drop 确定性执行；
- 每次工具调用必须有 `turn_id/tool_call_id/idempotency_key/generation/expected_revision`。

Sidecar 接受回合与回合完成必须分离。`POST .../turn` 返回 AcceptedTurn 只表示入队；Sidecar 通过带双向鉴权的内部 Runtime Event API 回传：

```text
case_id / turn_id / runtime_binding_id / generation / seq
event_type / model_request_id / tool_call_id / idempotency_key
payload / occurred_at
```

数据库对 `(runtime_binding_id, generation, seq)` 唯一约束，先持久化再投影 SSE，重试不得重复业务副作用。`AgentTurn` 状态机固定为 `RECEIVED → ROUTED → RUNNING → WAITING_TOOL | WAITING_USER | RECOVERY_REQUIRED | COMPLETED | FAILED | CANCELLED`。Assistant 消息、Tool Call、Decision、Compaction、错误和最终状态都必须进入持久表，不能只存在 Pi Session 或进程内 Map；generation 由 Mini-Drop 分配并跨 Sidecar 重启单调增加。

事件传输使用 ACK/Replay：Sidecar 在本地 append-only transport spool 保存未确认 RuntimeEvent；Mini-Drop 每次 ACK 返回该 generation 的 `highest_contiguous_committed_seq`，Sidecar 只删除已连续确认项，重连后从下一 seq 重放。若 Sidecar 连 spool 一并丢失，Mini-Drop 将未完成 Turn 标记为 `RECOVERY_REQUIRED`，分配新 generation，以 `recovery_of_turn_id` 从持久 Snapshot 重建并重试；任何已发出的 Tool 使用原逻辑 action 的稳定 idempotency key，Tool Gateway 返回既有结果而不重复副作用。测试必须覆盖“生成事件后 ACK 前崩溃”和“工具完成后完成事件 ACK 前崩溃”。

新 generation 与旧 generation 的 fencing 必须在同一数据库事务完成：RuntimeBinding 记录 `active_generation` 和每代 committed watermark；旧 generation 被标记 `FENCED` 后，只允许重放 `seq <= watermark` 的已提交重复事件并返回原 ACK，任何新的旧代事件返回 `409 GENERATION_FENCED`，保存为审计 orphan 但不得投影 Assistant/Turn 状态、触发 Wakeup 或产生业务副作用。恢复逻辑先读取已持久 ToolResult，再用稳定 idempotency key 衔接。增加“旧 Sidecar 在新代开始后迟到发送 assistant.completed/turn.completed”的强制测试。

“连续三轮”必须是三个独立真实模型回合：新 Evidence 已持久化 → Durable Wakeup → 新 `model_request_id` → 读取包含新 Evidence hash 的 Snapshot → 新 Decision/PlanRevision。不得在首轮预制三个 Step 冒充自适应调查；相同首轮输入而第二轮 Evidence 不同，第三轮 Missing Fact/Operation 必须合理分叉。

### 5.5 集群采集必须同时支持同构与异构

- Agent 和用户都可以选择逻辑资源、采样策略和采集矩阵，不直接枚举失效的临时 PID；
- Membership Snapshot 的成员是逻辑 Target Resource，不是 Worker；至少记录 `resource_ref/resource_type/service_role/instance_uid/process_start_time/executor_agent_id/host_ref/fault_domain/capability_versions/clock_quality`；
- 同一 `executor_agent_id` 可以承载多个资源成员，Assignment/Task 映射键必须是 `assignment_id + resource_ref`，不得以 agent_id 作为唯一成员键；
- Target Resolver 根据冻结的 Membership Snapshot 展开成员；
- 保留“同一个 Collector 扇出到多个目标”的 Homogeneous Fanout，用于建立共同基线和同角色横向对比；
- 新增 `CollectionCampaign`：由 `common_baseline` 和多个 `AcquisitionAssignment` 组成；Assignment 按角色、实例、异常分组、故障域或明确目标选择不同 Collector/Query Operation；
- Planner 将 Campaign 编译成原生 Task；一个 Task 仍只对应一个目标和一个 Collector/Query Operation；
- Task 保留现有 `target_pid` 兼容字段并增加结构化 `target_ref`；process 操作必须校验 PID incarnation，host/service/container Query 不要求 PID；禁止用 `pid=1` 或任意在线 Worker 伪装未解析目标；
- 人工创建、AI 创建和 Skill 建议必须共用同一个 Campaign API、校验器和任务列表；
- 异构调查仍应保留足够共同基线。没有共同指标时不得对节点数值做横向强比较；
- 去重按 `operation + target identity + time window + parameters + schema/version + quality` 执行；
- 并发、总目标数、每故障域预算必须在创建 Task 前强制执行，而不是只写提示；
- 离线、能力不匹配、身份漂移和版本过旧必须进入覆盖率分母和排除原因；
- 目标无法解析时形成 `TARGET_UNRESOLVED` EvidenceGap，不得回退到任意 Worker；
- Coverage 同时统计节点、角色、故障域、必需事实和采集能力覆盖；覆盖不足时禁止生成超出实际覆盖范围的结论；
- 迟到结果只能归入原 Snapshot/Revision，不能污染新调查轮次。

### 5.6 受控低风险查询操作

不得给模型开放任意 Shell。新增 `QueryOperationRegistry`，每个 Operation 至少声明：

```text
operation_id / version
description / supported_target_types
parameters JSON Schema
required_worker_capabilities
renderer/executor
execution_kind = QUERY
risk = READ_LOW | READ_ELEVATED
timeout / max_output_bytes
redaction / parser / evidence_schema
cache_ttl / fingerprint_fields
capability_version / renderer_hash
```

全系统统一使用正交枚举：`execution_kind = COLLECTOR | QUERY | SOURCE`，`risk = READ_LOW | READ_ELEVATED | CHANGE | FAULT_INJECTION`。`QUERY_READ_LOW` 不得同时充当 Task 类型和风险等级；当前 Agent 只能自动执行 `READ_LOW`，`CHANGE/FAULT_INJECTION` 仅由受控 Harness 或未来明确授权流程使用。

第一批应覆盖：

- `process.list`、`process.status`、`process.open_files_summary`；
- `system.load`、`system.memory`、`filesystem.usage`、`filesystem.inodes`；
- `network.connections`、`network.listeners`、`network.routes`；
- `service.status`、`service.logs_tail`；
- `container.list`、`container.inspect`、`docker.service_status`。

调用链必须是：

```text
用户或 Agent request_query
→ 编译为当前 Plan 中的单 Assignment CampaignRevision
→ Tool Gateway 校验 Operation/参数/目标/预算
→ 唯一 Supervisor 创建原生 `task_kind=QUERY,risk=READ_LOW` Task
→ Drop Worker 执行注册 Renderer
→ 结构化 Artifact
→ EvidenceEnvelope
→ 唤醒 Agent
```

Renderer 必须产生固定 executable 与 argv 数组并以 `shell=False` 执行；用户/模型不得控制 executable、cwd、env、绝对路径或输出文件。Worker 使用独立进程组，超时后终止整个进程组，限制 stdout/stderr 字节、固定 locale，并记录规范化 argv hash、实际 capability/renderer version。Parser 失败、截断或超时必须生成精确 EvidenceGap，不能把原始文本当成功 Evidence。

禁止管道、重定向、命令替换、`sudo`、任意路径读取、任意 `curl`、修改型 systemctl/docker/kubectl 和无限日志。日志查询限制时间、行数和字节并做基本脱敏。所有 AI 查询任务必须出现在第一页任务系统和第二页调查工作台。

### 5.7 Skill、知识库和 MCP

Skill 是领域调查策略，不是新的执行后门。Skill 可以告诉 Pi 应检查哪些事实、何时停止、需要哪些 Evidence，但仍只能调用 Mini-Drop Tool Catalog。

第一批 Skill 实现并验收：

1. `linux_cpu_saturation`；
2. `linux_memory_pressure`；
3. `linux_io_latency`；
4. `linux_network_degradation`；
5. `runtime_gc_and_lock`；
6. `docker_swarm_service_diagnosis`；
7. `cluster_outlier_comparison`；
8. `compound_causal_analysis`；
9. `flamegraph_interpretation`；
10. `deployment_capacity_assessment`。

每个 Skill 必须有版本、内容 hash、适用条件、初始假设、共同基线、差异化采集/查询建议、所需能力、预算、停止/拒答条件、报告要求、正向触发和负向不触发测试。每个最终 Skill 至少在 R1 真实领域链路留下 `skill_id/version/hash/selection_reason/tool trace` 和负向不触发记录；CPU、内存、网络、集群差异和复合因果五个核心 Skill 还必须至少各有一个 R3/R4 或适用的 R2 Holdout 证据。关闭 Skill 功能开关后，Sidecar 不得发现个人目录、项目目录或 Pi 默认 Skill。

建立仓库内受控 Knowledge Base，首批来源为项目文档、Collector/Query 字段说明、Linux/运行时/容器诊断知识、演示环境运行手册和已经验证的历史 Case。知识资产必须记录 `knowledge_id/source/version/hash/scope/freshness/citation`，并交付可重复构建命令、corpus manifest、chunk 清单/数量、索引 hash、删除/重建流程、引用解析以及至少 10 条 golden retrieval query。以确定性词法检索为最低可运行实现，可选本地 embedding/hybrid 增强；空向量库、只有接口、只返回打不开的标题均不算完成。只向 Agent 返回与当前 Missing Fact 相关的小投影。

必须区分：

- Knowledge：解释机制、提出假设和采集策略；
- Historical Case：提供有出处的经验类比；
- Current Evidence：证明当前事故中实际发生了什么。

知识和历史经验不能直接确认当前根因。任何结论仍必须引用 Current Evidence。

MCP 只通过 SourceRegistry/SourceGateway 暴露。Missing Fact 路由必须从实际 SourceRegistry 的 capability/fact declarations 生成，禁止维护与部署连接器脱节的硬编码 Source ID 表。Agent 提交 Missing Fact，不接触 MCP URL、Token 和原始连接器。所有 MCP 路径必须在 SourceGateway 边界统一完成注入清洗、大小/脱敏/新鲜度门禁、canonical Evidence 持久化和失败 EvidenceGap，禁止普通 Turn 绕过治理直接读取原始结果。MCP Evidence 与原生采集使用相同的去重、审计和 lineage。

### 5.8 复合故障因果图 v2

复合故障不是“同时出现两个分类”。统一原因角色：

- `PRIMARY_ROOT_CAUSE`：最早触发且能够解释主要传播链；
- `CONTRIBUTING_FACTOR`：提高发生概率或严重度，但不是起点；
- `AMPLIFIER`：故障发生后形成正反馈或扩大影响；
- `PROPAGATED_EFFECT`：传播链上的中间异常；
- `SYMPTOM`：用户/监控最终观察到的现象；
- `COINCIDENTAL_ANOMALY`：真实存在但未能接入主要因果链的伴生异常；
- `RULED_OUT`：有反证或显著低于当前候选的原因。

`CausalNode` 至少包含实体、机制、角色、onset/time window、支持/反对 Evidence、置信和角色理由。`CausalEdge` 至少包含 source/target、`CAUSES | PROPAGATES | AMPLIFIES | COINCIDES | CORRELATES`、传播机制、时间先后/lag、拓扑校验、Evidence refs、Knowledge refs、置信和 `OBSERVED | SUPPORTED | PLAUSIBLE | UNVERIFIED | REFUTED` 状态。

因果链必须通过 `CausalGraphVerifier`，至少校验：

1. 根因信号在 clock uncertainty 和 Oracle 容差内早于或同时于下游效应，事件时间不能用 ingestion time 代替；
2. 服务/进程/宿主拓扑存在允许传播的路径；
3. 每条关键边有当前 Evidence 支持，知识只证明机制合理；
4. 主要替代根因有反证或更低 EvidenceContract 覆盖；
5. 贡献因素和放大器使用独立证据，不能把一条 Evidence 无限复用；
6. 健康实例、前后窗口或修复后验证提供差分/反事实支持；
7. 无法验证的边明确标为 `UNVERIFIED`，不得伪装成事实。

因果图默认是跨 epoch 的有向无环图；只有明确正 lag 的 `AMPLIFIES` 反馈边允许构成环，并必须记录迭代/epoch，禁止用无时间语义的环解释一切。Primary 只有在必要传播边达到 `SUPPORTED/OBSERVED`、关键替代项被区分且证据覆盖足够时才能 `CONFIRMED`；否则输出部分确认和逐边缺口。

RulesOnly/Candidate Analyzer 只负责候选生成、反证、确定性降级和对照，不得限制 Pi 的候选根因全集。Pi 可以提出规则库外 mechanism，但必须给出实体、机制、支持/反对 Evidence、Knowledge 机制引用和可区分替代假设的 Missing Fact；由 Verifier 判定，不能因为规则库未登记而拒绝，也不能无 Evidence 确认。

Agent 先提出候选因果图，Verifier 返回逐边缺口，Agent 使用 CollectionCampaign/Query/MCP 对最有区分度的边补证；新 Evidence 到达后重建图，直到确认、明确部分确认或达到停止条件。

### 5.9 Causal Oracle v3、真实生效与盲测边界

公开开发 Oracle 与最终 Holdout Oracle 必须物理和权限分离。仓库内 tracked Oracle 只能用于 Contract/回归；最终 Holdout Oracle 由独立 evaluator 保管，不进入被测服务、Pi/施工 AI 上下文、候选包、日志和公开报告。Oracle 在该 Case 第一个 Agent Turn 前产生 commitment/hash，正式评分只接受第一次完整结果，禁止挑选多次运行中的最好结果。

Oracle 必须区分“设计注入的故障”与“独立探针实际观测到的生效事实”。注入命令成功不代表 Case 有效；`realization.valid=false` 时标记 `HARNESS_INVALID`，不得计入模型准确率。复合案例 Oracle 至少表达：

```json
{
  "primary_root_causes": [{"entity": "service-A", "mechanism": "memory_leak"}],
  "contributing_factors": [],
  "amplifiers": [{"entity": "service-B", "mechanism": "retry_amplification"}],
  "required_propagation_edges": [
    ["A.memory_growth", "A.gc_pressure"],
    ["A.gc_pressure", "A.latency"],
    ["A.latency", "B.timeout"],
    ["B.retry", "DB.connection_pressure"]
  ],
  "symptoms": ["A.cpu_high", "A.p99_high", "B.5xx_high", "DB.connections_high"],
  "optional_edges": [],
  "allowed_shortcuts": [],
  "forbidden_reversals": [],
  "forbidden_primary_causes": ["database_connection_pool_pressure"],
  "prediction_contract": {
    "conclusion_revision": "final_revision_referenced_by_finish_event",
    "candidate_states": ["OBSERVED", "SUPPORTED", "PLAUSIBLE", "UNVERIFIED"],
    "excluded_states": ["REFUTED", "RULED_OUT"]
  },
  "entity_aliases": {},
  "mechanism_taxonomy": {"exact": {}, "family": {}},
  "fact_predicates": [],
  "required_evidence_facts": [],
  "required_gaps": [],
  "repair_expectations": [],
  "topology_aliases": {},
  "clock_tolerance_ms": 2000,
  "realization": {"valid": true, "fact_refs": []},
  "cleanup_expectations": []
}
```

评分至少拆为：Primary Root Cause、角色区分、传播边、Evidence/时间/目标一致性、缺口/校准、修复建议、获取效率和停止质量。传播边按有向 Precision/Recall 评分，Oracle 可以声明合理快捷边和可选边。命中 `forbidden_primary_causes`、引用不存在/错误目标/错误时间 Evidence、把 Knowledge 当当前事实、泄露 Oracle、使用 Scripted Provider 作为最终结果或清理失败均为硬失败。

确定性 Evaluator 只读取 Case finish event 引用的 `ConclusionRevision` 及其 `causal_graph_revision_id`，不能挑选历史最好版本。实体先按 Oracle alias 规范化；机制按固定 taxonomy 分 exact/family，不能用字符串模糊猜测。每个 `fact_predicate` 必须声明 Evidence schema/version、field path 或 `extractor_id/version/hash`、operator、threshold、window、target 和 clock tolerance；非结构化日志只能通过锁定的 deterministic extractor 判事实，核心分不得使用 LLM Judge。RepairExpectation 同样以结构化 target/category/prerequisite/verification predicate 判定，不以“报告中出现类似建议文字”为通过。

### 5.10 精确 Evidence Gap Report

禁止只输出“证据不足”。每个阻断结论的缺口必须记录：

```text
gap_id / blocked_claim
required_fact
attempted_task/query/collector/source
target / requested_time_window
status / reason_code / raw_error_ref
observed_evidence
what_it_supports
what_it_does_not_support
conflicting_evidence_refs
retryable / next_best_action
```

标准原因至少包含 `COLLECTION_FAILED/CAPABILITY_UNAVAILABLE/TARGET_UNRESOLVED/TARGET_OFFLINE/TIME_WINDOW_MISMATCH/CLOCK_UNCERTAIN/OBSERVATION_TOO_SHORT/ARTIFACT_MISSING/EVIDENCE_LOW_QUALITY/EVIDENCE_CONFLICT/COVERAGE_INSUFFICIENT/PERMISSION_DENIED/CAUSAL_FACT_UNSUPPORTED`。

用户可见表达必须说明：采集是否执行、为什么失败；当前数据实际观察到什么；它能支持哪个较弱判断；为什么不能证明更强结论；下一步最小补证是什么。

### 5.11 结构化修复建议

每条建议绑定一个 Cause 或 Propagation Edge，并包含：

```text
recommendation_id / cause_or_edge_ref
category = temporary_mitigation | root_fix | amplifier_control | validation
target / concrete_action / rationale
evidence_refs / prerequisites
risk / approval
expected_effect
verification_operations
success_criteria
rollback_or_failure_condition
confidence / limitations
```

必须区分临时缓解和根因修复。发现数据库连接数高但它位于传播链末端时，不得把“扩大连接池”作为首要根修复。缺少对象级、调用栈或配置证据时，可以明确建议下一项定位动作，但不能假装知道具体代码修改。修复后使用同一传播链上的关键指标进行验证。

### 5.12 结构化 ConclusionRevision 合同

自然语言报告是结构化结论的投影，不能作为评分或状态真源。每次结束/重评估持久化不可变 `ConclusionRevision`：

```text
conclusion_id / revision / investigation_run_id
state = CONFIRMED | PARTIALLY_CONFIRMED | INSUFFICIENT_EVIDENCE | NO_FAULT_FOUND
primary_root_causes[]
ranked_primary_candidates[] = {
  rank, entity_ref, mechanism, confidence_0_to_1,
  verification_state, supporting_evidence_refs,
  opposing_evidence_refs, missing_fact_ids
}
contributing_factors[] / amplifiers[] / symptoms[] / coincidental_anomalies[]
causal_graph_revision_id
claims[] = {claim_id, status, evidence_refs, counter_evidence_refs}
evidence_gap_ids[] / recommendation_ids[] / limitations[] / abstention_reason
created_from_turn_id / model_request_id / verifier_version
```

`rank` 从 1 连续递增且无并列，Top-1/Top-3 直接取该数组；`reported_confidence` 固定为 rank=1 的模型报告 confidence，不能由 evaluator 猜文本。多根已确认结果使用 `primary_root_causes[]` 无序集合；`PARTIALLY_CONFIRMED` 只能列已支持子链和候选，不能把未验证候选放入 confirmed roots；`INSUFFICIENT_EVIDENCE` 必须引用阻断 Gap；`NO_FAULT_FOUND` 的 confirmed roots 为空并给出停止/abstain 理由。报告文本若与结构化 state/roots/confidence 冲突，ReportVerifier 拒绝 finish。DeploymentAssessment 使用独立 `DeploymentVerdict`，不伪装成事故 Root Cause。

### 5.13 必须落地的对象、工具与事件

执行 AI 可以根据现有仓库命名调整，但不得省略以下语义。

持久对象：

```text
AgentTurn / RuntimeEvent / RuntimeBinding / RuntimeWakeup
CaseEvidence / EvidenceLineage / EvidenceReviewRevision
CollectionCampaign / AcquisitionAssignment / CampaignRevision
ExecutionUnit / SourceCall
QueryTaskMetadata（Operation ID、版本、参数、Renderer、输出限制）
KnowledgeAsset / KnowledgeChunk / KnowledgeCitation
SkillBinding（skill_id/version/hash/selection_reason）
CausalGraphRevision / CausalNode / CausalEdge
EvidenceGap
ConclusionRevision / RankedPrimaryCandidate / StructuredClaim
RepairRecommendation / VerificationPlan
```

Agent Tool Catalog 至少增加：

```text
list_acquisition_capabilities
create_or_update_campaign
request_query_operation
search_knowledge
get_causal_graph
submit_causal_graph_revision
get_evidence_gaps
evaluate_causal_graph
submit_repair_recommendations
```

`create_or_update_campaign` 与 `request_query_operation` 是易用型工具，但它们只能提议/编译当前 `InvestigationPlanRevision` 的 ACQUIRE_EVIDENCE Step，不能直接调度或形成第二套状态机。每个写工具只携带与自身对象相关的 CAS 字段和幂等键：Case 写入带 `expected_case_row_version/expected_scope_revision`，Plan 更新带 `expected_plan_revision`，Campaign 更新带 `expected_campaign_revision`，CausalGraph 更新带 `expected_causal_revision`；首次创建对应对象时该 `expected_*_revision=null`，Mini-Drop 以“不存在”作为 CAS 前提。禁止要求首次创建携带尚不存在的 revision，也禁止省略更新时的 CAS。Agent 只能提出 Query Operation ID、逻辑目标和结构化参数；不能提交最终 shell argv。

至少持久化并通过 SSE/时间线投影：

```text
turn.received / turn.running / turn.waiting_tool / turn.completed / turn.failed
runtime.model_requested / runtime.assistant_message / runtime.tool_called / runtime.compacted
campaign.created / campaign.revised / assignment.compiled
query.requested / query.completed / query.failed
knowledge.retrieved / skill.selected / skill.rejected
causal_graph.proposed / causal_edge.verified / causal_edge.rejected
evidence_gap.opened / evidence_gap.resolved
recommendation.created / verification.completed
```

API 至少能够支持：人工创建/修改 Campaign、查看编译后的 Task；列出 Query Capability；查看 Knowledge 引用；读取每版 Causal Graph、Evidence Gap 和 Repair Recommendation。具体 URL 应遵循现有 `/api/v1/cases/{case_id}/...` 风格并进入 OpenAPI/前端客户端。

### 5.14 Causal Evidence Capability Gate 与真实性等级

每个最终因果 Case 在运行前必须提交机器可读 Capability Matrix。每个 required fact/edge 必须声明：

```text
fact_id / edge_id
source_kind / collector|query|mcp operation_id
real_field_path / target_resource
event_time_semantics / clock_requirement
minimum_observation_window
activation_probe / baseline_probe / recovery_probe
support_status = SUPPORTED | UNSUPPORTED
```

任一 required fact 在当前环境为 `UNSUPPORTED`，Case 标记 `NOT_ELIGIBLE`，不得计入成功，也不得用预制 Evidence、Fixture 字段、日志猜测或模型常识补齐。执行 AI 必须先补齐真实可观测能力，或改用暴露所需信号的受控开源工作负载。

所有报告标注 realness：

- `R0`：纯 Mock/Fixture，只能做 schema 单测；
- `R1`：Scripted Provider + 真实领域存储/调度，可做确定性集成；
- `R2`：真实模型 + 执行上下文不可见的 Replay，可评估推理与泛化；
- `R3`：真实模型 + 本地真实服务/Worker/采集/故障；
- `R4`：真实模型 + 当前精确候选 + 三节点真实 Worker/工作负载；仅在 Case 要求故障注入时额外必须有真实 fault realization/cleanup，健康或解释型 Case 不因“没有故障”降级。

最终动态核心 Case 必须达到 R4；R0/R1 的通过不能替代 R2-R4。每个 Evidence、Case 结果和报告都记录实际等级，禁止笼统写“E2E”。

### 5.15 状态与结果枚举不得混用

- `InvestigationRunStatus = RUNNING | WAITING_USER | PAUSED | RESOLVED | INSUFFICIENT_EVIDENCE | STOPPED | FAILED | CANCELLED`；
- `DeploymentVerdict = FIT | CONDITIONAL | INSUFFICIENT_DATA | NOT_FIT`；
- `FaultLifecycleState = PREPARED | INJECTING | ACTIVE | RECOVERING | CLEAN | INVALID`；
- `HarnessOutcome = PASS | FAIL | HARNESS_INVALID`；`FaultLifecycleState.INVALID` 映射为 `HARNESS_INVALID`，不算 Agent 失败；
- `CaseEligibility = ELIGIBLE | NOT_ELIGIBLE`；Capability Gate 不满足只能是 `NOT_ELIGIBLE`，不得算 PASS；
- `EvaluationTrustLevel = DEVELOPMENT_EVAL | INDEPENDENT_HOLDOUT`；
- `HoldoutAcceptanceStatus = NOT_RUN | AWAITING_EXTERNAL_HOLDOUT | AWAITING_ENVIRONMENT | RUNNING | VERIFIED | REJECTED | BUDGET_EXHAUSTED`。

禁止再使用含义不明的 `INSUFFICIENT` 或把 `INSUFFICIENT_DATA` 当 Case 终态。外部 Evaluator 不可用时进入 `AWAITING_EXTERNAL_HOLDOUT`，本地任务继续；只有签名成绩导入后才能 `VERIFIED/REJECTED`。

## 6. 单一功能完整交付的依赖图

不要再平均推进旧 E0-E9，也不要把下面的工作流解释成多批交付。执行时按依赖关系收敛，接口稳定后可以并行处理互不覆盖的工作，但每个工作流通过后必须自动进入下一项。只有 G0-G11 全部通过，才能向用户交付最终版本。

### G0：恢复事实、门禁和回退安全

工作：

- 记录当前 Commit、tracked diff、依赖锁和候选包指纹；
- 创建并持续维护 `reports/implementation/ai-agent-runtime-state.json` 与 Evidence JSONL；
- 为需求分配稳定 ID `D01-D20`，建立机器可读需求—Case—测试—证据追踪矩阵；完成状态只能由 evaluator 根据测试结果晋级；
- 将旧状态文档中与当前代码矛盾的“已实现”声明降级或标记基线；
- 冻结旧框架删除；功能完整前只修兼容问题，不继续清理旧路由和字段；
- 让所有 Agent 自动执行开关真正约束 PlanDriver、MCP、Skill 和 Fanout；
- 依赖漏洞扫描降为一次性信息报告，不作为日常门禁；只有会导致任意命令执行、Secret 泄漏或演示环境直接破坏且位于实际调用链上的严重问题才阻断；
- 为 `deterministic` 模式保留可用回退。
- 以 Alembic 实际唯一 head（当前基线为 `0020_cluster_scope`）创建后续迁移；附件中的 `0020_agent_runtime/0022_cluster_scope` 是历史设计编号，禁止照抄、复用编号或创建平行 head；验证空库升级、当前库升级和单一 head。

退出条件：功能开关、状态 Schema、普通 Drop 回归和本地总门禁通过；关闭 Agent 自动执行时绝不创建新采集/查询 Task。

### G1：Pi SDK 和内部协议 Contract

工作：

- 将 package lock、运行时 banner、适配测试和部署版本统一到同一个 Pi 精确版本；
- 使用 Pi SDK 官方类型和构造参数，不以注释代替验证；
- 显式提供隔离的 Auth/Model 配置，禁止读取 `~/.pi`；
- 使用 full-control ResourceLoader，禁止默认文件、Prompt、Extension、Theme 和 Skill 发现；
- 明确禁用 Bash/read/write/edit/grep/find/ls 等内置工具，同时证明自定义 Tool Catalog 仍可调用；
- 统一 Sidecar 和 Python Adapter 路由。可以实现受控 `shadow-plan`，也可以使用统一 `turn` + `execution_mode=shadow`，但不得两端各自假设；
- 为内部 HTTP 双向配置 Secret，Sidecar→FastAPI 和 FastAPI→Sidecar 都要鉴权，Sidecar 工具调用必须发送 `X-Internal-Token`；
- 修复 Plan Tool schema 的三个 revision、target refs、hypothesis refs、selection strategy 和风险字段；
- 统一成功/失败信封、错误码、超时、取消和重试语义；
- 实现 AcceptedTurn 与完成分离的 Runtime Event 回传协议、持久 Turn 状态机、generation/seq 唯一约束和 assistant/tool/final 事件落库；
- 实现 Sidecar transport spool、contiguous ACK/replay、generation fencing 和崩溃点 Contract Test；
- 增加真实 Tool Call、steer、follow-up、abort、重启重建和无内置工具 Contract Test。

退出条件：Faux/Scripted Provider 下完成至少一次真实自定义工具调用；鉴权成功；非法工具不可见；超时和 abort 可重复验证；Sidecar 重启后同一 Case 从 Mini-Drop Snapshot 重建且 generation 单调递增；实际 npm package version、lock、banner 与部署 manifest 完全一致。

### G2：只读 Shadow Agent 纵向切片

工作：

- 将 Case Agent Turn 接入 `AgentRuntimePort`；
- 持久化 RuntimeBinding、Turn、归一化事件和 AgentDecisionRecord；
- 实现 `get_case_snapshot`、Evidence Inventory、Collector/Query/Skill Capability Catalog 等只读工具；
- Pi 生成结构化 Shadow Plan，但不能创建 Task；
- 对同一 Case 同时生成 Deterministic Plan 和 Pi Shadow Plan；
- 记录缺失事实、工具轨迹、Plan 差异、非法 collector/target/risk 拒绝原因；
- `ANSWER_ONLY` 必须能解释已有证据且不产生 Plan/Task。

退出条件：固定 Evidence Bundle 上 Shadow Plan 质量达到上位设计门槛；解释型追问连续多轮不启动新调查；每条回答的证据引用都能从 Case Inventory 反查。

### G3：已有数据统一进入 Evidence

工作：

- 完成 Task、Collection、Artifact、MCP Result 到 EvidenceEnvelope 的统一 Ingestion；
- 选定并迁移到唯一 canonical Case Evidence Store；Attachment 与旧 DiagnosisEvidence 只作为关联/兼容投影，不得继续并行写入不同事实；
- ReferenceResolver 对所有支持的 ResourceRef 做真实租户、状态、目标、时间和完整性校验；
- Attachment 返回 accepted/rejected/duplicate/stale 原因；
- 实现完整 CollectionFingerprint：collector、目标身份、时间窗、规范化参数、schema version 和质量；
- 在计划补采前先搜索复用，明确记录“为什么复用/为什么不能复用”；
- EvidenceReview 必须触发假设、计划和结论重新评估。
- finish 在单事务内逐条验证有效 Evidence 并持久化 Conclusion、引用、限制和 Run 状态。

退出条件：从第一页 Task、Collection 和 API 模拟的 `@ResourceRef` 进入同一 Evidence Inventory；Agent 确实消费对应 `evidence_id`；相同有效采集不会创建重复 Task。

### G4：差异化 Acquisition Campaign 与低风险 Query Gateway

工作：

- 新增 CollectionCampaign、common baseline、AcquisitionAssignment、编译结果和多维 coverage；
- 将 MembershipSnapshot 成员改为逻辑 Target Resource；允许同一 Worker 承载多个服务/容器/进程，禁止 `agent_id` 唯一键覆盖；
- 同时支持同构 Fanout 和按服务角色、异常节点、故障域、明确目标的异构采集；
- 人工和 AI 使用同一 Campaign API，前端可查看和修改编译前矩阵；
- 实现 QueryOperationRegistry、Capability Catalog、参数校验、Renderer、输出限制、解析器和 Evidence 映射；
- 注册首批进程、系统、文件系统、网络、服务、容器和 Swarm 只读查询；
- 所有 Query 通过 Supervisor/Worker 形成原生 Task，不在 Sidecar/FastAPI 直接运行；
- Query 使用固定 argv、`shell=False`、进程组超时清理、输出上限、parser 和 renderer hash；host/service/container Query 不伪造 PID；
- Collector 和 Query 使用统一 Fingerprint、复用、预算、取消和事件语义；
- 为危险参数、任意命令、路径逃逸、无限日志和不支持目标增加拒绝测试。

退出条件：一个三角色 Fixture 同时生成共同 sys_metrics 基线、API 网络查询、数据库 IO 采集和网关服务状态查询；人工与 AI 产生相同任务结构；所有任务进入原任务列表并形成 Evidence。

### G5：持久 Plan、控制和事件闭环

工作：

- Plan 的所有结构变更产生新 revision，不原地篡改历史；
- 对 Case/Plan 派生执行收敛为一个持 Case Lease 的 CaseSupervisor；Pi/Case API/PlanDriver/MCP/旧 autonomy 均不能绕过它直接创建 Case Task/SourceCall；第一页非 Case 的普通 Drop Task 保留原入口；
- Campaign、Query 和 MCP Operation 全部从属于 ACQUIRE_EVIDENCE Step，不保留第二套调度状态机；
- 实现取消、删除、重排、改目标、禁止 collector、暂停、恢复、停止的确定性语义；
- 运行中改目标时取消旧 Task，并在新 revision 创建替代 Step/Task；
- 实现 Task/Event Durable Outbox、去重消费和崩溃恢复；
- finish 前验证 Evidence 存在、归属、状态和最低覆盖，持久化结论和限制；
- `WAITING_APPROVAL` 不可调度，依赖满足后才能运行，失败/取消依赖使下游 BLOCKED；
- stale scope/plan/tool result 必须被拒绝且留下审计事件。

退出条件：不依赖 Pi 也能通过竞态、重启、重复事件、迟到结果和取消传播测试；用户停止后无新 Task，恢复后不重复执行已完成步骤。

### G6：Pi 驱动 READ_LOW 连续调查

工作：

- 在 G1-G5 通过后开放 Pi `upsert_investigation_plan/request_query/create_campaign`；
- Mini-Drop 校验并接受计划后，由 Supervisor 创建真实 Task；
- Task 完成自动唤醒 Pi，Pi 读取新 Evidence 并决定补采、解释、询问或结束；
- 连续三轮必须产生三个真实 model_request，且自适应分叉测试证明不是预制固定脚本；
- 决策必须从 Provider response/tool-call hash 追溯到 AgentDecisionRecord 和实际 Plan/Tool；P03 从同一首轮 Snapshot fork 两个 Case、只改变第二轮 Evidence，下一 Missing Fact/Operation 必须分叉；
- 用户自然语言可开始调查、补充旧数据、追问解释和转向；
- 用户控制命令同时影响 Mini-Drop 执行状态和 Pi 回合；
- 低风险自动执行受全局、Case、Plan 和集群预算共同约束；
- 任一 Runtime 错误可回退到 deterministic，不能损坏 Case 状态。

退出条件：两个入口各通过至少一条端到端用例；至少一条用例完成连续三轮 `Turn→Plan/Campaign/Query→Task→Evidence→Wakeup→Turn`；解释型追问不误触发采集；中断、复用、去重和 Evidence 引用验收通过。

### G7：Skill、知识库、MCP 与集群调查闭环

工作：

- 接入十个首批 Skill，完成选择理由、版本、正向触发、负向不触发、预算和停止测试；
- 建立 Knowledge Asset 摄取、分块、索引、混合检索、引用和删除/重建流程；
- 交付 corpus manifest/hash、可重复构建命令和至少 10 条 golden retrieval query；
- 证明 Knowledge 只用于解释/规划，不能作为 Current Evidence 确认根因；
- Agent 从 Missing Fact 选择 Source Capability，而不是手工指定 MCP URL；
- Missing Fact 路由从实际 SourceRegistry declarations 生成，所有路径统一经过 SourceGateway 清洗和 Evidence 持久化；
- 演示环境至少接入一个真实只读 MCP Source；第二个 Source 可以使用本地受控实现证明多 Source 路由；
- MCP 结果经过大小、脱敏、新鲜度、注入和权限门禁后形成 Evidence；
- Source 失败时允许降级、询问或停止，不得虚构结果。
- 使用冻结 Membership Snapshot 执行同构/异构 Campaign，支持部分失败、Agent 离线、能力不匹配、取消、迟到结果和身份漂移；
- Agent 根据节点、角色、故障域、事实和能力覆盖率限制结论范围。

退出条件：真实 Case 中证明 Skill/Knowledge 改变调查策略但不替代 Evidence；MCP 形成可引用 Evidence；两个真实 Worker 完成差异化 Campaign、部分失败和覆盖率限制结论。

### G8：复合因果推断、精确缺口与修复建议

工作：

- 先为每个 required fact/edge 建 Causal Evidence Capability Matrix；当前环境不支持则先补可观测能力或切换受控真实工作负载，不得用 Fixture 补齐；
- 实现 Causal Graph v2 原因角色、时间、拓扑、逐边证据和验证状态；
- 新增 CausalGraphVerifier，使用知识图谱/Skill 作为机制先验，使用当前 Evidence 验证事故事实；
- Agent 根据未验证的关键边生成差异化 Campaign/Query/MCP Missing Fact；
- 建立 EvidenceGapReport，并从 Task/Artifact/Agent/能力/时间窗/质量/冲突中生成精确原因；
- 升级报告结构，明确 Primary Root Cause、Contributing、Amplifier、Propagation、Symptoms、Coincidental、Ruled Out 和 Unexplained；
- 建立 Causal Oracle v3、独立 Holdout 传播图、forbidden primary、边级评分和修复建议评分；区分 intended fault 与 independently observed realization，并支持 allowed shortcuts、forbidden reversal、时钟容差、required gaps 和 cleanup expectation；
- 允许规则库外新 mechanism，增加“正确根因不在 rules.json”的盲测 Case；
- 至少新增“内存泄漏→GC→延迟→超时→重试放大→DB 压力”以及一个无关伴生异常场景；
- 实现结构化 Recommendation Contract，分别给出临时缓解、根因修复、放大治理和修复后验证；
- 证据不足报告必须列出当前观测、含义边界、采集失败/不足原因和下一动作。

退出条件：示例链路中把 DB 连接压力判为 Primary 必须失败；正确根因、放大器、必要传播边和症状达到约定评分；故意让 GC 采集失败时，报告精确指出失败与可/不可推断内容；所有 required causal fact 来自实际支持的数据源且故障已被独立探针证明生效。

### G9：部署承载评估

在核心事故调查闭环稳定后再推进：

- 最低可演示合同必须覆盖 CPU、内存、磁盘、replica、allocatable、当前 reservation、安全余量和数据新鲜度；峰值窗口在数据可用时纳入；
- N-1、调度/亲和性、Quota、依赖资源若环境无法提供，必须列为具体 EvidenceGap 和限制，不能伪造，也不要求为此建设完整生产调度平台；
- 明确区分节点容量、集群容量、故障域容量和可调度容量；
- 通过历史回测评估误差，缺关键事实必须返回 `DeploymentVerdict=INSUFFICIENT_DATA`；
- 只提供预测和证据，不自动执行部署。

退出条件：最低合同的 Evidence 可追溯、计算可重复；缺少峰值/N-1/Quota/调度事实时给出 `DeploymentVerdict=CONDITIONAL|INSUFFICIENT_DATA`，补证后可重评估；不得用瞬时利用率冒充确定可部署容量。

### G10：前端产品化和完整用户体验

只有 G5 的 Turn、Event、Plan、Evidence 和 Command 契约稳定后开始前端实现，但最终交付必须完成本节，不能只交付后端。

工作：

- 第一页继续作为 Drop 采集主入口；AI 创建的原生 Task 必须出现在同一个采集任务列表，并显示来源、Case、计划步骤、风险、状态和取消结果；
- 第二页提供持久会话和调查工作台。消息、解释和卡片按时间线追加，不得通过刷新或创建新卡片让上一轮内容消失；
- Composer 支持自然语言、`@Task/@Collection/@Artifact/@Evidence/@Service/@Process`、引用 Chip、只解释、继续调查、补充数据和明确采集快捷动作；
- 当前工作、下一步、等待批准、历史步骤、Campaign/Assignment/Fanout 子任务、Query、Evidence、Hypothesis、Causal Graph、覆盖率、缺口和结论来自真实后端状态；
- AI 计划卡支持取消、删除、重排、改目标、锁定顺序和禁用 Collector，并展示真实接受、拒绝、冲突和版本过期结果；
- Evidence 卡支持查看关键字段、趋势、时间范围、新鲜度、来源和质量，支持 `LOW_TRUST/EXCLUDED` 并展示对假设和结论的影响；
- 诊断详情、火焰图和关键图表默认在当前上下文内展示可用的核心视图；“完整结果”只作为深度查看入口，不能成为看到数据的唯一方式；
- 重新定义“诊断数据台”为 Evidence Explorer：按 Case、目标、时间、来源、Collector、质量和假设筛选，明确它与第一页原始数据、第二页调查工作的关系；无独立价值的重复入口应合并；
- 非技术用户默认看到问题摘要、AI 正在做什么、证据是否充分、下一步和是否需要参与；专家可以展开原始字段、Tool Trace、Plan Revision、Task/Artifact/Evidence 和覆盖率；
- 使用持久 Event ID/SSE 恢复，刷新、断网重连和跨设备重新打开后恢复完整消息、卡片、当前任务和进度；
- 复合故障报告分区显示主根因、贡献因素、放大器、传播路径、症状、伴生异常、未验证边和针对性修复建议；
- Evidence Gap 直接显示采集是否执行、失败原因、当前数据说明什么/不能说明什么和下一动作；
- 人工集群采集和 AI 采集都能查看共同基线与差异化矩阵，并对 Assignment 删除、排序、改目标/操作；
- 加载、空数据、部分失败、Sidecar 不可用、模型限流、任务取消中、证据过期和覆盖不足均有明确状态，不显示空白卡片或假成功；
- 键盘操作、焦点、色彩、响应式布局和基本无障碍达到可用门槛；中文术语一致，不向普通用户暴露内部 Runtime 术语。

退出条件：浏览器 E2E 覆盖两个入口、解释不触发调查、连续三轮、`@` 补证、控制、断线恢复、部分失败、集群覆盖和最终报告；非技术用户无需理解 Collector/PID 就能完成一次自主调查，专家能从结论反查到原始 Evidence。

### G11：当前候选演示环境收敛和最终交付

工作：

- 修复 VM Runner 的过期 release、候选哈希、Sidecar 部署和 Worker 一致性问题；
- 保留 Secret 不入库、内部工具鉴权、注册操作 allowlist、超时、输出限制、幂等和故障自动清理等演示最低安全线；
- 为 Server、Sidecar、Supervisor、Outbox、MCP、Worker 和前端补齐定位演示故障所需的健康状态、结构化日志和关联 ID；
- 验证至少两个并发 Case、Provider 超时、Tool 超时、Sidecar 重启和一个 Worker 离线，不做大规模压力/灾难恢复；
- 提供本地和三节点实验环境部署配置，锁定依赖并生成不可变 Release Manifest；
- 使用当前工作区候选包，不复用旧 release 报告；
- 锁定每个外部测试项目的 repository、tag/commit、license、镜像/构建物 hash 和 workload；升级版本只有在资源预检与离线镜像可用后进行，不能为了“最新”破坏已验证环境；
- 将现有 `ai_ops_v2`、`github_cases` 和仓库内 private Oracle 明确降级为 legacy regression，最终质量门禁使用独立 Holdout；
- 每个真实故障执行 baseline→prepare→inject→independent activation probe→observe→recover→cleanup→health probe，未生效标记 HARNESS_INVALID；
- 按精简演示阶梯执行预检、部署、健康、两种入口、连续调查、已有数据复用、解释、人工干预、同构/异构集群、Query、Skill/Knowledge、MCP、复合因果、容量评估、前端、重启恢复和最终清理；
- 每次失败按 INFRA/COLLECTION/EVIDENCE/PLAN/RUNTIME/MODEL/MCP/SKILL/WEB/HARNESS 分类，修复最小根因后重跑最小失败集，再扩大回归；
- 报告绑定 Commit、工作树指纹、archive hash、DB migration head、Sidecar/Pi 版本和各节点 release；
- VM 凭据、私有地址、Oracle 和 Secret 不进入 Git、Prompt、模型上下文或公开报告；
- 编写并实际验证安装、配置、启动、演示步骤、Sidecar/Provider/MCP 故障、环境清理和回退到 deterministic 的简明 Runbook；
- 保留旧规则/一次性链路作为兼容和降级，不为演示目的进行大规模删除或不可逆字段迁移。

退出条件：当前精确候选在三节点实验环境完成全部代表场景和浏览器 E2E；关键场景重复运行结果稳定；故障和临时数据全部清理；Control、Worker 和工作负载健康；普通 Drop 与 deterministic 回退可用；交付报告不把未验证能力写成已完成。

## 7. 演示交付的最低非功能要求

### 7.1 可用性和恢复

- Pi、Provider、MCP 或 AI 页面不可用时，第一页普通采集、Worker、Task 和原始结果查看继续工作；
- Sidecar 健康状态区分进程、模型和工具可用性，错误在界面明确显示；
- Runtime、Outbox、Supervisor 和 Worker 重启后从持久状态恢复，不重复创建 Task/Query 或消费旧结果；
- 外部调用有超时、取消和有限重试；禁止无限重试和静默吞错；
- Provider 故障时保留 Case，可重试或切 deterministic，不伪造回答；
- 演示开始和结束都运行健康/残留检查。

### 7.2 最小安全边界

- 单用户/单实验环境可以不完成完整多租户、合规和密钥轮换体系；现有 tenant 字段和隔离不得故意破坏；
- Secret、VM 密码、API Key、私有 Oracle 不进入 Git、Prompt、日志、报告和候选包；
- Sidecar 内部工具调用保持 Token 鉴权；模型只看 allowlist Tool 和严格 Schema；
- Agent 不得直接运行任意 Shell、文件读写、SSH、SQL 或未注册 MCP；低风险命令只通过 QueryOperation Registry；
- 故障注入、压力任务和查询必须有超时、范围限制与最终清理；
- 不持久化或显示私有思维链；
- `npm/pip` 漏洞扫描只在最终候选记录一次，不要求清零。只有实际调用链上可直接造成任意执行、Secret 泄漏或环境破坏的问题阻断演示。

### 7.3 性能和成本 sanity check

- 大 Artifact 不直接进入 Prompt，使用摘要、分页、按需查询和字节预算；
- 每个 Case 设置模型回合、Token、Tool、Task/Query、Fanout、运行时间和 MCP 调用上限；
- 至少验证两个并发 Case、一个较大 Evidence Bundle 和一次长会话 Compaction；
- 记录关键路径耗时、Token/Tool/Task 数和失败率即可，不要求完整 P99、长期压力或容量证明；
- 不允许为了提高表面准确率无限扩大采集范围和模型预算。

### 7.4 基础可观测性

每个用户 Turn 必须可以通过一个 correlation chain 追踪：

```text
tenant_id / case_id / turn_id
→ runtime_generation / model_request_id
→ tool_call_id / idempotency_key
→ plan_revision / step_id
→ task_id / fanout_run_id / membership_snapshot_id
→ artifact_id / evidence_id
→ hypothesis_revision / conclusion_id
```

至少在日志、状态 API 或页面中可查看：

- Runtime 活跃 Session、队列、Turn/Model/Tool 延迟、失败、重试、abort、steer、重建和 Compaction；
- Evidence 复用率、重复采集率、Missing Fact 闭合率、每轮信息增益、引用有效率；
- Plan stale reject、Task dispatch/cancel、Outbox 延迟/积压/重复、迟到结果隔离；
- 集群 eligible/covered/failed/excluded、节点/故障域覆盖率、Fanout 并发和身份漂移；
- MCP 成功率、延迟、裁剪、注入命中、Source 新鲜度和成本；
- Skill 候选、选择、拒绝、版本、误触发和相对无 Skill 的质量变化；
- Provider Token/成本/限流、Case 预算耗尽和自动停止原因；
- Policy deny、非法工具、查询拒绝和 Evidence Gap 原因。

### 7.5 打包、兼容和演示 Runbook

- Release Manifest 关联源代码、工作树、Web、依赖锁、Pi 版本、迁移 head 和环境 Profile；
- 候选包不包含 `.env`、凭据、缓存、私有 Oracle和本地用户配置；
- 数据库迁移完成空库升级和当前库升级检查，不要求多发布周期向后兼容；
- 不做大规模旧框架删除，保留 deterministic 和普通 Drop 作为回退；
- Runbook 至少包含安装、配置、启动、准备演示数据、运行核心场景、故障恢复、清理和回退，并在当前候选实际执行。

## 8. 固定真实测试集合与独立评测契约

本节是最终门禁，不是供执行 AI 自选的案例建议。必须新增：

```text
benchmarks/agent_beta/
  contracts/public-contract-v1.json
  conformance/
  manifests/public-v1.yaml
  schemas/oracle-v3.schema.json
  schemas/fault-contract.schema.json
  schemas/holdout-score-v1.schema.json
  sources.lock.json
  replay/
  causal_lab/
scripts/run_agent_beta_eval.py
scripts/validate_agent_beta_suite.py
scripts/import_agent_beta_score.py
```

`public-v1.yaml` 固定公开 Case 和需求映射；执行 AI 不得删除难例、替换场景、跳过用例或修改阈值。Evaluator 配置与 Holdout Oracle 位于被测服务和施工/模型上下文之外，不能提交到本仓库。

### 8.1 稳定需求 ID

| ID | 必须证明的需求 |
|---|---|
| D01 | 解释型追问不误启动调查 |
| D02 | 第一页或 `@` 已有数据物化、引用和零重复采集 |
| D03 | 模糊语言触发真实、连续、自适应 Agent 调查 |
| D04 | 暂停、转向、补证、降信任、恢复和 revision 隔离 |
| D05 | 人工共同基线 + 异构 Assignment |
| D06 | AI 异构 Campaign、部分失败和覆盖率约束 |
| D07 | 注册低风险 Query 的正向执行与越界拒绝 |
| D08 | Skill 版本、选择理由、正触发与负触发 |
| D09 | 可构建 Knowledge、可打开引用、不冒充 Current Evidence |
| D10 | SourceRegistry/MCP 补证、清洗、失败缺口和 lineage |
| D11 | Primary/Contributing/Amplifier/Propagation/Symptom/Coincidental 及有向边 |
| D12 | 采集失败、冲突、陈旧、时钟和覆盖不足的精确 EvidenceGap |
| D13 | 与 Cause/Edge 绑定的缓解、根修复、放大治理和验证 |
| D14 | Sidecar/Server/Supervisor/Worker 重启、重复事件和幂等恢复 |
| D15 | 持久会话、真实进度、任务双入口和关键图内嵌展示 |
| D16 | 部署承载两阶段评估和条件化拒答 |
| D17 | 非技术用户可完成、专家可追溯和纠错 |
| D18 | 真实故障、盲测 Oracle、独立评分和规则外假设 |
| D19 | Pi/Provider/MCP 故障时普通 Drop 与 deterministic 回退 |
| D20 | 故障 TTL、清理、最终健康和候选证据绑定 |

一个 Case 可以覆盖多个 D，但 D01-D20 必须全部至少被一个最终 Case 覆盖。`completed_acceptance_ids` 只能由 evaluator 读取真实测试结果生成，不能由自然语言或手工编辑晋级。

### 8.2 外部真实项目选择与锁定

截至本提示词审查时，优先项目如下；执行前重新验证 upstream，但正式 Run 必须使用全局 `sources.lock.json` 索引和各 suite 的 `source-lock.json`，固定 repo、tag、commit、license、image/artifact digest、deployment manifest hash、Collector 版本和 fault-injector 版本，禁止隐式拉取 `latest`：

| 项目 | 已审查候选 | License/使用约束 | 在本项目中的用途 | 决策 |
|---|---|---|---|---|
| [Google Online Boutique](https://github.com/GoogleCloudPlatform/microservices-demo) | 当前已验证 v0.8.0 (`d54af3d2510995d33521ac543dd885b150213f95`)；upstream 曾审查至 v0.10.6 (`5b3a712ab85ccb8f6f7cd5b720d36ba9a8d041eb`) | Apache-2.0；候选锁镜像 digest | 三节点 Worker、Task、集群 Campaign、控制、恢复、UI | 必选；保持已验证 v0.8.0，升级不是门禁 |
| [OpenTelemetry Demo](https://github.com/open-telemetry/opentelemetry-demo) | v3.0.0 (`1755859a9de82c2e5e225be68abc401a5ebf2b4f`) | Apache-2.0；锁 compose/image digest | 内置高 CPU、手工 GC、内存泄漏、不可达、流量、Kafka 等动态因果事实 | 必选 compact profile；按约 3 GB base、约 6 GB 推荐和当前 VM 预检组合组件，禁止启动其内置 Agent/MCP/Chatbot |
| [Steadybit Shopping Demo](https://github.com/steadybit/shopping-demo/tree/steadybit-shopping-demo-1.3.1) | 1.3.1 (`6b5cddd3f683d5a569a590cc82c136af34826e6d`) | Apache-2.0；tag 内 `latest/develop` 镜像必须改为从锁定 Commit 构建或固定 digest | retry 放大、timeout 和 circuit-breaker 抑制的变形测试 | 扩展因果门禁；不能成为唯一主项目 |
| [RCAEval](https://github.com/phamquiluan/RCAEval) | 审查 Commit `4695aa69f4f1f57b9094ca04ff235908b73a8e24` | MIT；仅使用锁定数据，保留第三方 baseline 各自许可 | Online Boutique/Sock Shop/Train Ticket 的匿名真实指标、日志、Trace Holdout 回放 | 必选 R2 泛化集；只下载锁定子集并校验每文件 hash |
| [Microsoft AIOpsLab](https://github.com/microsoft/AIOpsLab) | 审查 Commit `b9a814e75a98e670787dac7c2ed6794b4b68dae2` | MIT | 交互式 Agent、故障、工作负载、遥测和 evaluator 方法参考 | 只借鉴方法；Kubernetes/Helm 依赖过重，不作为 Runtime 依赖 |
| [DeathStarBench](https://github.com/delimitrou/DeathStarBench) | 精确 Commit 由 source lock 冻结 | 顶层 LICENSE 与 README 许可表述需先复核 | Swarm/Jaeger 和复杂微服务扩展 | 非阻断未来扩展，不纳入当前三节点硬门禁 |

外部项目的 README 或 fault flag 只能证明“提供某种机制”，不能证明本次传播链实际发生。现有 `benchmarks/github_cases` 四例和 `ai_ops_v2` 仍保留为 legacy single-fault regression，但不计入 Agent Beta 独立质量证明。Online Boutique VM v0.8.0、`testsets/real` 中 v0.10.3 描述和 upstream v0.10.6 是三套不同资产，必须各自 source lock，不能混写成同一环境。Train Ticket 过重、Sock Shop 已归档、Bank of Anthos 与当前项目重合、MicroRemed 许可/成熟度不足，均不作为当前门禁。

### 8.3 四层测试与真实性

低层通过不能替代高层：

| 层 | 允许的替身 | 必须真实的部分 | 目标时限 | 最低 realness |
|---|---|---|---:|---:|
| T0 Domain/Contract | Mock | Schema、revision、评分、Renderer、预算、拒绝逻辑 | 8 分钟 | R0 |
| T1 本地纵向集成 | 仅 Scripted Provider | HTTP、Sidecar、鉴权、DB、Plan、Campaign、Supervisor、Worker、Collector/Query、Artifact、Evidence、Outbox、重启 | 25 分钟 | R1 |
| T2 匿名遥测回放 | 不允许模型替身 | 真实 Pi/Provider，ReplaySource→SourceGateway/MCP→Evidence，匿名 Holdout | 每例 20 分钟 | R2 |
| T3 三节点动态 | 不允许模型/Worker/结果替身 | 当前候选、真实 Pi/Provider、三节点 Worker、真实服务/故障/采集/控制/UI/清理 | 简单 25、复合 45 分钟 | R4 |

T1 的强制纵向链为：

```text
HTTP Turn → Sidecar → Tool → Plan → Campaign → CaseSupervisor
→ 真实 Worker → Collector/Query → Artifact → CaseEvidence
→ Durable Wakeup → 新 Agent Turn
```

T2 数据只能通过 Replay Source 的受控分页/查询接口按 Missing Fact 进入 Case，测试代码禁止把 DataFrame、全量目录或答案直接塞进 Prompt。目录、文件名、环境变量和元数据中的 root service/fault label 必须匿名化。

### 8.4 固定公开验收集 P01-P12

下面的 P01-P12 表和 D01-D20 映射是 normative contract，不是可被施工 AI“重新理解”的示例。G0 必须逐字段物化为 `contracts/public-contract-v1.json`，记录本提示词内容 hash，并由 validator 对 Manifest 做语义等价/只增强校验；删除断言、缩小 realness、减少重复、扩大预算或降低阈值一律 `CONTRACT_DRIFT`。候选 Manifest 记录 contract digest，外部 Holdout 签名成绩也回传该 digest。`conformance/` 至少包含 wrong-primary、fake-evidence、no-native-task、oracle-leak、cleanup-failure、answer-starts-investigation 六个必失败 Fixture，validator/scorer 必须全部拒绝。公开套件仍是开发合同而非盲测质量证明；其可信边界来自 Git/用户审查和外部 evaluator，不得谎称施工 AI 对自己写的测试具有独立性。

| ID | 场景、核心断言 | Scoring profile | 最低 realness | 覆盖需求 | 重复 |
|---|---|---|---:|---|---:|
| P01 `conversation.explain_only` | 问“这张 CPU 图是什么意思”；`ANSWER_ONLY`，引用现有 Evidence，Plan/Campaign/ExecutionUnit/Task/采集唤醒 Outbox 增量均为 0；AssistantMessage/SSE 正常持久化，刷新后仍存在 | `FUNCTIONAL_CONTRACT` | R4 | D01,D09,D15 | 1 |
| P02 `ingress.existing_evidence_reuse` | `@Task/@Collection` 物化为 CaseEvidence；充分时新增 Task=0；Task↔Artifact↔Evidence↔Conclusion 双向追溯 | `FUNCTIONAL_CONTRACT` | R4 | D02,D17 | 1 |
| P03 `autonomy.single_fault` | 用户只说“商城变慢，请自行定位”；真实 Query+Collector 和至少三次 model turn；从同一首轮 Snapshot fork 两 Case，只改变第二轮 Evidence，Provider Ledger、Missing Fact 和下一 Operation 必须合理分叉 | `FUNCTIONAL_CONTRACT`, `CAUSAL_SINGLE` | R4 | D03,D07,D18 | 连续 3 次 |
| P04 `control.revision_interrupt` | 慢采集中暂停、改目标、补证、降低旧证据、恢复；旧 revision/tool/迟到结果不推进，时间线完整 | `FUNCTIONAL_CONTRACT` | R4 | D04,D14 | 1 |
| P05 `campaign.manual_heterogeneous` | 所有目标采共同基线，网关查服务/连接、API 查网络/短日志、DB 查 IO/连接；矩阵和原生 Task 一一对应 | `FUNCTIONAL_CONTRACT` | R4 | D05,D07 | 1 |
| P06 `campaign.agent_heterogeneous_partial` | AI 对网关/API/DB 生成差异化 Assignment；一个 Worker 离线/缺能力；Coverage 列节点/角色/事实缺口并拒绝全局结论 | `FUNCTIONAL_CONTRACT` | R4 | D06,D12,D17 | 连续 3 次 |
| P07 `query.registry` | 正向运行 process/network/service/log 操作；管道、命令替换、路径逃逸、自定义 executable/cwd/env、无限日志和 scope 外目标在 Worker 启动前拒绝 | `FUNCTIONAL_CONTRACT` | R4 | D07,D19 | 1 |
| P08 `skill_knowledge_mcp` | Skill 改变策略，Knowledge 引用可打开但不作当前事实，MCP 形成 Evidence；Source 失败生成具体 Gap，文档指令不能扩大工具权限 | `FUNCTIONAL_CONTRACT` | R4 | D08,D09,D10,D12 | 1 |
| P09 `causal.public_cascade` | 真实 `A内存增长→GC→A延迟→B超时→B重试放大→下游压力`；A 为 Primary、B 重试为 Amplifier、逐边真实 Evidence、下游非根因 | `CAUSAL_SINGLE` | R4 | D03,D11,D13,D18 | 连续 3 次 |
| P10 `causal.missing_edge` | 屏蔽/失败一条决定性边并加入更显著下游 distractor；只允许部分确认，Gap 明确已知/未知/失败/最小补证，DB 不升为 Primary | `CAUSAL_PARTIAL` | R4 | D11,D12,D13,D18 | 1 |
| P11 `capacity.two_phase` | 两个 required subcase 首轮都遮蔽明确事实并必须返回 `INSUFFICIENT_DATA`；补证后一例 `FIT`、一例 `NOT_FIT`，固定限制资源/公式/单位/容差；始终返回 CONDITIONAL 失败，不实际部署 | `CAPACITY` | R4 | D10,D12,D16 | 1 |
| P12 `recovery.ui.cleanup` | 重启 Sidecar、Server/Supervisor、Worker，注入重复事件与 SSE 重连；无重复副作用，普通 Drop 可用，AI Task 双入口可见，关键图内嵌，全部清理 | `FUNCTIONAL_CONTRACT` | R4 | D14,D15,D17,D19,D20 | 1 |

P03/P06/P09 的“连续 3 次”指三次独立正式运行全部通过，不是运行到成功三次；P03 的三轮调查是三个新 `model_request_id`，不是首轮预制三步计划。

P11 的 Evaluator 先签名冻结 baseline，再按 baseline 构造一项明显可承载和一项明显不可承载的 DeploymentRequirement；统一计算 `available = allocatable - current_reservations - safety_margin`、`required = per_replica_requirement × replicas + deployment_overhead`，CPU 使用 millicore、内存/磁盘使用 byte。CPU 默认容差为 `max(1 millicore, 1%)`，内存/磁盘为 `max(1 MiB, 1%)`。Oracle 固定 limiting resource、reservation 语义和被遮蔽 fact；补证后 Verdict、限制资源和逐项中间值都必须匹配。

### 8.5 执行上下文不可见的 Holdout H01-H19

具体服务、故障、时间和答案只存在 evaluator：

- H01：RCAEval RE2 CPU；H02：MEM；H03：DISK；H04：DELAY；H05：LOSS；H06：SOCKET，服务/系统按运行前冻结分层选择；
- H07、H08、H09：RCAEval RE3 三个不同代码级 mechanism family，至少一个不在 rules.json，且来自至少两个系统；
- H10：级联的实体映射变化；H11：故障强度/时间变化；H12：传播拓扑变化，三者均保持一个 Primary 但不能共享硬编码服务名；
- H13：两个独立 Primary；
- H14：一个 Primary + 一个 Amplifier；
- H15：主根因 + 数值更显著但无关的真实 distractor；
- H16：关键中间边被屏蔽或采集失败；
- H17：陈旧时间窗、时钟不确定或因果顺序相反的 Evidence；
- H18a：Membership/实例 incarnation 变化，旧实例 Evidence 不得归并到新实例；
- H18b：容量约束盲测，缺证后补证并得到可复核 Verdict；
- H19：健康基线或非故障波动，必须正确 abstain，不得为了输出答案制造根因或采集风暴。

固定 Profile/realness 分层：

| Required slot | Scoring profile | 最低 realness | 来源 |
|---|---|---:|---|
| H01-H09 | `CAUSAL_SINGLE` | R2 | 匿名 RCAEval Replay |
| H10-H12 | `CAUSAL_SINGLE` | R3 | 隔离动态因果栈 |
| H13 | `CAUSAL_MULTI` | R3 | 隔离动态双根因 |
| H14-H15 | `CAUSAL_SINGLE` | R3 | 动态 Amplifier/distractor |
| H16-H17 | `CAUSAL_PARTIAL` | R3 | 动态遮蔽/时间反证 |
| H18a | `FUNCTIONAL_CONTRACT` | R4 | 三节点 Membership/incarnation |
| H18b | `CAPACITY` | R4 | 三节点容量证据 |
| H19 | `HEALTHY_ABSTAIN` | R4 | 三节点健康基线 |

Holdout 必须使用随机 opaque Case ID、匿名实体别名、无标签文件/目录名；不能向 Agent 暴露注入时间文件，只给用户合理可知的事故窗口。Evaluator 进程独占 Oracle 读取权限；首回合前提交 salted commitment；同一候选只计第一次正式结果，代码变化后使用新 seed 并保留历史尝试，禁止 best-of 选择。

同一个拥有仓库和完整测试目录读取权限的施工 AI，不能“自行生成私有 Oracle 后再自行宣称盲测通过”。Holdout 必须由用户控制的另一目录/VM/CI Job 或远程 Evaluator 执行，且 Oracle 不挂载进 SUT/Sidecar。若没有这一隔离条件，只能标记 `DEVELOPMENT_EVAL`，不能计入最终 Holdout。

必须实现但不包含答案的 `holdout-evaluator.v1` 交换合同。H18a/H18b 是两个独立 required slot，因此 Holdout 一共必须得到 20 个有效 slot 结果：

```text
Evaluator 输入：candidate_manifest、candidate_archive/hash、SUT base URL、
               model/prompt/skill/knowledge manifests、suite capability token
Evaluator 驱动：只通过公开 Case/Turn/Task/MCP API 和外部 Fault/Replay Controller
Evaluator 输出：schema_version、verdict、suite_id/version/hash、
               public_contract_digest、evaluator_build_digest、source_lock_digest、
               candidate/archive/deployed_release_manifest digests、
               model/provider/prompt/skill/knowledge manifest digests、
               started/finished_at、aggregate metric numerators/denominators、
               hard_failures、provider_ledger_root_hash、suite_budget_actual、
               evidence_pack_root_hash、oracle_commitments、
               case_results[]、signature

case_results[]：opaque_case_token、required_h_slot、scoring_profile、
               required_realness/actual_realness、realization_status、
               attempt_count/substitution_reason、case_verdict、metric numerators/denominators、
               hard_failures、evidence_pack_subtree_hash
```

输出不得包含具体 Oracle、故障标签或隐藏数据路径。成绩 JSON 使用 RFC 8785 JCS canonicalization；`signature` 为 Ed25519 对“移除 signature 字段后的 canonical JSON bytes”的签名。证据包 root 固定为 `SHA256(JCS(按 UTF-8 relative_path 排序的 [{path,size,sha256}]，排除 score/signature 文件))`，每个 Case 使用相同算法计算 subtree hash。

信任根不能在候选仓库内自举。用户或外部 CI 以只读环境/挂载注入 `expected_key_fingerprint`、`expected_public_contract_digest` 和 `expected_evaluator_build_digest`；最终验签也在候选包之外执行。仓库只提供 schema、无信任权的公钥缓存、验证器和 `scripts/import_agent_beta_score.py`。正式模式禁止任意 `--public-key`；验证器必须同时校验外部固定 key fingerprint、Ed25519 签名、contract/evaluator/source/candidate/release/model manifests、时间、20 个 required slot、逐项分母、预算和 evidence root。开发者自带 key 只能使用 `--development` 并写 `EvaluationTrustLevel=DEVELOPMENT_EVAL`，永远不能把 Holdout 状态升级为 VERIFIED。

外部 Provider Ledger 由 Evaluator 控制的模型代理/观测网关生成，记录 provider request ID、请求/响应/tool-call hash、时间和用量；每个实际驱动 Plan/Tool 的响应必须与 Mini-Drop 的 `model_request_id/AgentDecisionRecord/tool_call_id` 对上，Ledger root 进入签名成绩。只装饰性调用一次真实模型、随后由固定 Planner 决策是硬失败。

没有外部 capability/authority 时，施工 AI继续完成 T0/T1、P01-P12、候选打包和只读预检，最后将唯一外部动作记为 `AWAITING_EXTERNAL_HOLDOUT` 并请求最小输入，不得伪造通过。

### 8.6 受控真实复合因果实验栈

当前 Online Boutique 环境缺少 GC、请求延迟和真实 span，不能直接承担 P09。优先从锁定的 OpenTelemetry Demo 3.0.0 构建 memory profile（base + Prometheus + Jaeger，关闭 OpenSearch/Grafana/OpAMP/内置 Agent/MCP/Chatbot）和 queue profile（base + Kafka + Prometheus，资源允许再加 Jaeger）；重试放大/熔断变形可复用锁定的 Steadybit Shopping Demo。若仍缺精确传播边，再增加最小受控 overlay，而不是伪造 Evidence：

```text
固定负载发生器
→ retrying service-B
→ runtime service-A
→ 真实 PostgreSQL/连接消费者
→ 独立 Prometheus/OTel Observer
```

P09 的 R4 topology 固定为：Control 运行当前 Mini-Drop Server/Pi、负载发生器和隔离 Observer；Worker1 运行 service-A/runtime 与 Drop Worker；Worker2 运行 retrying service-B、真实 PostgreSQL/连接消费者与 Drop Worker。`B→A` timeout/retry 所对应的 required edge 必须跨 Worker，source lock 记录每个服务、容器、Worker、Collector 和端口映射；不能把全部业务容器塞进一个 VM 后仍标 R4。Preflight 至少要求每个 workload Worker 在保留 1 GiB 宿主余量后仍满足锁定 container limits、Control 有 2 GiB 可用余量、整个 profile 有 14 GiB 可用镜像/临时磁盘且各根分区 <70%。不满足则 `AWAITING_ENVIRONMENT`，不能以 R3 代替 P09。结束后恢复锁定 Online Boutique v0.8.0，并验证 12 个 Swarm service、两个 Drop Worker、普通采集和用户交易 Smoke 全部健康。

要求 A 的内存增长/GC、A 请求延迟、B timeout/retry 和下游真实连接/负载均来自真实进程、请求和遥测，不得写入伪造的 `connection_count/latency` 字段。Mini-Drop 只能通过 Worker Query/Collector 或 SourceGateway/MCP 获取；独立 Observer 只验证生效和清理，不向 Agent 提供根因标签。外部 Demo 的 feature flag、retry 配置和 injection operation 不能直接成为 Agent 可见证据，每条 required edge 仍要独立激活断言。运行 profile 前停止冲突 workload 并做 CPU/内存/磁盘/端口预检，结束后恢复原三节点环境。

### 8.7 FaultContract 与安全清理

每种故障使用 schema 校验的合同：

```yaml
fault_id: opaque-id
mechanism: evaluator-only
target_selector: {...}
preconditions: []
activation_operation: {...}
activation_assertions:
  - assertion_id: a1
    observer_source: evaluator-observer
    query_or_field_path: metric.path
    target_ref: opaque-target
    baseline_window: {...}
    active_window: {...}
    predicate: relative_increase
    direction: greater
    threshold: 1.5
    minimum_samples: 5
    event_time_semantics: source_event_time
    clock_tolerance_ms: 250
    required_fact_or_edge_id: fact-or-edge-id
max_duration_seconds: 180
blast_radius: {...}
cleanup_operation: {...}
cleanup_assertions: []
visible_to_agent: []
```

生命周期固定为 `PREPARED → INJECTING → ACTIVE → RECOVERING → CLEAN`，生效失败进入 `INVALID`。只有独立 Observer 证明必要异常真实出现才能开始评分；未生效记 `HARNESS_INVALID`，不能算 Agent 通过或失败。

P09 的 realization 必须按同一冻结负载执行并分别保存 `baseline → primary_only → primary_plus_amplifier → primary_with_amplifier_disabled → recovery` 五个 epoch。Oracle 为每个 epoch 固定可执行 fact predicate；只有开启 B 重试后下游影响相对 `primary_only` 显著扩大、禁用重试后传播显著减弱，B 才能标为 `AMPLIFIER`。若只观测到多个指标同时升高而缺少这种差分/反事实，最多标 `PLAUSIBLE`，不得算 Amplifier 命中。

演示默认上限：CPU 最多一个逻辑核且 ≤180 秒；内存 ≤可用内存 25%、单实例 ≤256 MiB 并保留宿主 1 GiB；指定服务流延迟 ≤500 ms、丢包 ≤20%、≤180 秒；磁盘只写唯一临时目录 ≤512 MiB 且根分区 >70% 时拒绝；服务暂停/Worker 离线 ≤120 秒。所有故障有外部 TTL、`finally` 清理、运行前残留扫描和运行后健康探针；共享物理宿主的破坏型 Case 串行执行。

### 8.8 确定性评分与硬失败

每个 Manifest Case 必须声明 `contract_assertions[]`、`scoring_profiles[]`、`applicable_metrics[]`、各指标分母、`n_a_policy`、`expected_conclusion_state` 和可见/遮蔽事实。固定 Profile：

- `FUNCTIONAL_CONTRACT`：行为/链路逐断言 pass/fail，本身无 Primary 分母，可与 `CAUSAL_SINGLE` 等质量 Profile 同时存在；
- `CAUSAL_SINGLE`：一个可观察 Primary，参与 Top-1/Top-3/机制/边/100 分；
- `CAUSAL_MULTI`：多个 Primary，以无序集合的实体+机制最大匹配计算 set Precision/Recall/F1，不套用单根 Top-1；
- `CAUSAL_PARTIAL`：决定性边被遮蔽，评分候选排序、已证实边、Gap 和校准；把候选写成 `CONFIRMED` 是硬失败，不因未确认 Primary 扣成 0；
- `CAPACITY`：只评分 Verdict、输入 Evidence、计算、缺口与补证重评估；
- `HEALTHY_ABSTAIN`：无 Primary 分母；不虚构根因、不过度采集且正确停止才通过。

N/A 指标从该 Case 分母排除，不能按 0 分计算，也不能把不存在该能力的 Case 算成功；每个 Profile 的权重由 canonical Manifest 固定并在适用项内归一化。`Top-1/Top-3` 只统计 `CAUSAL_SINGLE && realization.valid && decisive_facts_visible`；机制准确率只统计 Oracle 定义机制的有效因果 Case；Required Edge F1 对适用 Case 做 micro 聚合并同时报告 macro，不得择优选一种。中位数/最低单例只统计有效的数值因果 Profile，功能、容量和 abstain 另报通过率。

`CAUSAL_SINGLE/CAUSAL_MULTI` 事故 Case 总分 100：

| 项目 | 分值 |
|---|---:|
| Primary 实体与机制 | 25 |
| 原因角色区分 | 10 |
| Required 有向传播边 | 15 |
| Evidence 内容/时间/目标一致性 | 15 |
| EvidenceGap 与置信校准 | 10 |
| 修复和验证建议 | 10 |
| 采集策略、复用和信息增益 | 10 |
| 正确停止 | 5 |

Primary 实体和机制全对得满分；实体正确且机制族正确但粒度不足得 75%；实体正确机制错误最多 40%；机制正确实体错误最多 25%。`CAUSAL_MULTI` 的 Primary 分使用无序集合最大匹配后的 set F1；`CAUSAL_PARTIAL` 使用下表固定权重。传播边使用有向 Precision/Recall/F1，allowed shortcut 只得部分分，反向边不得分。Evidence 不只检查 ID，还要检查同 Case、target、event window、有效状态以及内容是否真正支持 Claim。核心分均由确定性 evaluator 计算；LLM-as-Judge 只能作为非阻断可读性参考。

其他数值 Profile 权重固定为：

| Profile | 固定权重（总和 100） |
|---|---|
| `CAUSAL_PARTIAL` | 候选实体/机制排序 15；已验证可见边 10；Evidence 有效性 20；精确 Gap 25；ConclusionState/置信校准 15；采集策略 10；正确停止 5 |
| `CAPACITY` | 首轮 Verdict+具体 Gap 20；Evidence/时间 15；公式/单位/中间值 25；补证后 Verdict+limiting resource 25；不执行部署+停止 15 |
| `HEALTHY_ABSTAIN` | 无虚构 Root+正确 abstain 40；Evidence/基线有效性 20；采集有界 20；停止和说明 20 |

`FUNCTIONAL_CONTRACT` 仍是全部断言通过才 PASS，不用加权分掩盖失败。

以下任一项使 Case 直接失败，不能被综合分补偿：

- 命中 forbidden primary、因果反转达到 Oracle 禁止条件或将无关异常作为已确认根因；
- 引用不存在、跨 Case、错误目标/时间、已排除 Evidence；
- Knowledge/历史经验被当作当前环境事实；
- 模型直接 Shell、绕过 Task/SourceGateway、伪造 Artifact/Evidence；
- Oracle/故障标签泄漏或针对 benchmark ID/标签的生产代码分支；
- Scripted Provider 冒充最终模型或旧 `/diagnoses` 结果冒充 Agent Turn；
- 决定性 Evidence 被屏蔽仍输出高置信“已确认”；
- 故障/临时数据清理失败或最终健康失败。

公开门禁：P01-P12 的 contract assertions 全部通过；P03/P06/P09 连续 3 次通过；仅 P03/P09 等标记 `CAUSAL_SINGLE` 的公开 Case 要求 Primary 100%，非因果 Case 不进入该分母。Holdout 门禁：适用的 `CAUSAL_SINGLE` Exact Primary Top-1 ≥80%、Top-3 ≥94%、机制 ≥75%；H13 `CAUSAL_MULTI` Primary set F1=100%；适用复合 Case Required Edge micro F1 ≥80% 且报告 macro；H19 abstain=100%；20 个 required slot 全部有有效结果；forbidden primary=0、高置信错误=0、预期 Gap 完整率=100%、Evidence 引用有效率=100%、有效数值因果 Case 总分中位数 ≥80、最低单例 ≥60。RulesOnly 对照采用下面的可达 headroom 公式，且错误自信率不得更高。Holdout 不以全部 Case 根因 100% 迫使实现硬编码。

指标机器公式至少固定为：

```text
invalid_acquisition_rate = 无关闭 MissingFact、无区分候选、无提升覆盖/质量且未被报告引用的 AI 新派发 ExecutionUnit / 全部 AI 新派发 ExecutionUnit
reuse_task_reduction = 1 - data_ingress_case_new_tasks / paired_no_data_case_new_tasks
plan_consistency = canonicalized_plan_similarity（去随机 ID/时间戳，保留 operation/role/order/dependency）
confidence_error = wrong_primary AND reported_confidence >= 0.8
drop_success_delta = agent_unavailable_drop_success_rate - deterministic_baseline_success_rate
rules_target = rules_median + min(15, 0.5 * (100 - rules_median))
```

用户明确要求的采集不进入 `invalid_acquisition_rate` 分母；AI 没有新派发时该率定义为 0 并单列 `new_execution_units=0`。`reuse_task_reduction` 的 paired no-data 分母必须 >0，否则该对记 N/A 并不能用于“下降 30%”结论。正确拒答/校准率只以 Oracle 标记 `CAUSAL_PARTIAL/HEALTHY_ABSTAIN` 或容量缺证的 Case 为分母。`drop_success_delta` 不得低于 -2 个百分点；paired baseline 使用相同故障、负载、预算和 seed。

Plan canonicalization 固定为：移除随机 ID/时间戳，把每个 Step 变为 `(kind,operation_id,target_role_or_type,normalized_parameters_hash,risk,expected_fact_ids)`；参数 JSON 键排序，保留重复 Step 数量；dependency 转为上述 Step signature 对。两 Plan 的 `node_multiset_f1`、signature 序列 `lcs/max_len`、`dependency_edge_f1` 分别计算，`similarity = 0.50*node_f1 + 0.25*order_lcs + 0.25*dependency_f1`。只比较同 seed、同 scope、同 observed Evidence hash 序列的运行，至少三次全 pair 平均 ≥0.80；P03 Evidence fork 属于分叉正确性，不进入一致率分母。

RulesOnly 必须通过 Adapter 输出同一 Conclusion/Prediction Schema。R2 使用完全相同冻结 Replay；R3/R4 使用随机运行顺序的独立 realization，只有 required fact/强度/窗口均落入 Oracle 等价带时才配对。只比较双方共同适用的因果/Evidence/Gap/Repair 指标，交互和工具自主性记 N/A，不能给 RulesOnly 填 0 制造提升。Agent 在适用 Case 的 median 必须达到 `rules_target`；RulesOnly=100 时要求 Agent=100。

### 8.9 Flake、预算与停止

- 故障前默认稳定基线 120 秒；固定 workload seed/rate、故障起点和观察窗口，使用相对基线而非跨机器绝对阈值；
- 时钟偏差/不确定度超过 Oracle 要求时不做时间因果评分，Case 进入 HARNESS_INVALID 或预期 Gap；
- 每个 Run 使用唯一 namespace/label/temp dir/Case ID；静态回放最多并发 2，破坏型 Case 串行；
- 激活失败在完整清理后最多重试一次；Fault 已激活后的模型/Agent/Task 失败必须计分，不能改称 infra 波动；
- 每个 required slot 必须最终产生一个有效评分；激活失败只能按运行前冻结规则从同一 stratum 替换一次，原实例、替代实例、原因和 attempt 都进入签名结果。任一 slot 无有效结果时 Holdout 不得 VERIFIED；`HARNESS_INVALID >10%` 只是整轮 Harness 健康失败阈值，不能让难例从分母消失。Cleanup 失败立即停止后续注入；禁止永久 skip；
- 默认预算：解释/复用 4 Turn、10 Tool、2 Task、2 MCP、10 分钟；单故障 8/24/10/4/25 分钟；复合因果 12/36/16/6/45 分钟；容量 8/24/8/6/20 分钟；
- 单 Case 默认模型总输入输出 ≤120k Token、单 Evidence 投影 ≤512 KiB、Artifact 总量 ≤100 MiB、Fanout 目标 ≤8；原始 Artifact 不直接进 Prompt；
- 连续两轮未关闭缺口、未区分候选且未提高 Evidence 质量时停止，并精确报告剩余缺口；
- 大 Evidence Bundle 固定为至少 50 条 Evidence/累计模型投影 2 MiB，长会话固定为至少 40 条消息并发生一次可验证 Compaction。

正式预算分开核算，不能让外部 Evaluator 为它未执行的公开用例签名：

- Public：19 个 top-level run（含 P03/P06/P09 重复和 P11a/P11b）；P03 每个 run 含两个 fork branch，因此共有 22 个 branch execution。最多 24 次 attempt、250 Model Turn、2M Token、700 Tool、300 Task/Query、90 MCP、8 小时。实际用量写入 candidate-bound `public-score.json/provider-ledger-root.json`，不冒充外部签名；
- Holdout：20 个 required slot，最多 24 次 attempt（至多 4 次、每 slot 至多 1 次同层替代）、250 Model Turn、2M Token、700 Tool、250 Task/Query、90 MCP、10 小时。实际用量由外部 Evaluator 写入 Ed25519 签名 score；
- 两者共用外部数据/镜像缓存上限 40 GiB；Provider 费用必须分别显式配置，合计默认不超过 50 USD 或等值。

Public 超预算使公开门禁 FAIL；Holdout 超预算使 `HoldoutAcceptanceStatus=BUDGET_EXHAUSTED`。均不得静默加预算、重跑选优；只有用户显式批准新的 Suite Run、预算和 commitment 才能再执行。

### 8.10 需求—测试—证据追踪与证据包

机器生成 Traceability Matrix 至少包含：

```text
requirement_id / public_case_ids / hidden_family_ids
test_files / harness_commands / expected_evidence_files
last_result / candidate_hash / realness
```

另生成 `skill-operation-coverage.json`：十个 Skill 均映射至少一个 positive 和一个 negative Case/Trace；所有注册 Query Operation 均映射参数 schema、一个成功 contract test、危险参数/越界 negative test 和 renderer/parser/version 证据。P07 只承担核心 Query 的动态纵向证明，未在 P07 列出的 Operation 仍必须在 T0/T1 逐项通过，不能因接口已注册而视为实现。任何最终 Skill/Operation 没有映射时，D07/D08 不得完成。

每次正式运行生成只追加证据包：

```text
reports/benchmarks/<run-id>/
  candidate-manifest.json
  benchmark-source.lock
  traceability.json
  environment-snapshot.json
  preflight.json
  causal-capability-matrix.json
  oracle-commitment.json
  baseline-observations.json
  fault-events.jsonl
  model-provider.json
  provider-ledger-root.json
  agent-turns.jsonl
  runtime-events.jsonl
  tool-calls.jsonl
  plan-campaign-task-map.json
  artifact-manifest.json
  evidence-snapshot.json
  score.json
  signed-holdout-score.json
  acceptance-verification.json
  suite-budget.json
  cleanup.json
  final-health.json
  browser/
```

`oracle-commitment = SHA256(canonical_oracle + random_salt)` 在首回合前生成，评分时再揭示并验证。生产目录不得包含按 benchmark ID、服务名或故障标签选择答案的分支；Harness 只通过公开 API、MCP/SourceGateway 和原生 Task 接口驱动系统。

## 9. 机器证据和进度协议

每完成一个 G 阶段，必须保存：

- 当前 Commit、工作树指纹和依赖锁哈希；
- `sources.lock.json`、需求追踪矩阵、实际 Pi/Provider 版本和 realness；
- 测试命令、退出码、报告路径和失败样例；
- 一条完整事件时间线，包含 Turn、Tool Call、Plan Revision、Task、Evidence 和 Wakeup；
- 开关组合和实际生效结果；
- 已知限制、回退结果和下一动作；
- 如涉及 VM，保存候选 Manifest、各节点 release、清理和最终健康结果。

至少运行：

```bash
.venv/bin/python scripts/run_local_gate.py \
  --python .venv/bin/python \
  --frontend \
  --run-id <commit-or-worktree-id>

npm --prefix agent_runtime/pi-sidecar test
npm --prefix agent_runtime/pi-sidecar audit --omit=dev  # 仅最终候选记录，不作为每轮门禁

.venv/bin/python scripts/package_candidate.py \
  --build-web \
  --verify \
  --python "$(pwd)/.venv/bin/python"

.venv/bin/python scripts/validate_agent_beta_suite.py \
  --manifest benchmarks/agent_beta/manifests/public-v1.yaml \
  --sources benchmarks/agent_beta/sources.lock.json

.venv/bin/python scripts/run_agent_beta_eval.py \
  --suite public-v1 \
  --manifest benchmarks/agent_beta/manifests/public-v1.yaml \
  --candidate <candidate-manifest> \
  --run-id <run-id>

# Holdout 由隔离 Evaluator 执行；仓库内只导入并验证不含答案的签名结果
.venv/bin/python scripts/import_agent_beta_score.py \
  --score <holdout-score-v1.json> \
  --candidate <candidate-manifest> \
  --authority <read-only-external-acceptance-authority.json>
```

最后两个脚本是本提示词要求施工 AI 落地的新入口；不存在时先实现和测试，不能用旧 `/diagnoses` Runner 代替。具体子系统修改后先跑最小测试，再跑总门禁。不得为了通过门禁删除测试、放宽断言、吞掉异常、默认降级成功或把失败改名为 warning。

## 10. 执行纪律

- 开始工作前先检查 Git diff，保护用户已有修改；
- 不信任提交标题、旧状态文档、旧报告和 TODO 完成标记；
- 不把仓库内可见 Oracle 称作隐藏集，不读取 evaluator-only Oracle，不针对 Case ID/服务标签硬编码；
- 优先修改最小纵向切片，不一次重写所有领域模型；
- 每个修复必须先有能失败的测试或 Contract Trace；
- 不在缺少模型凭据时停下：使用 Faux/Scripted Provider 完成确定性 Contract；到真实模型 Smoke 门再记录最小阻塞；
- 不在缺少 VM 凭据时停下：先完成所有本地、容器、打包和只读预检；
- 低风险、本仓库内的代码修改和测试持续推进；需要 Secret、生产变更、不可逆数据库操作、推送 Git 或真实故障注入权限时再请求用户；
- 用户可以随时打断、改变目标或缩小范围，收到后先安全停止正在运行的外部动作，再更新状态；
- 每轮结束必须说明实际完成、未通过、是否真正使用 Pi、是否真正创建原生 Task、是否真正进入 VM，以及下一条可执行动作；
- Definition of Done 未满足时禁止使用“全部完成”“已经收敛”“生产可用”。

## 11. 自动续跑和跨回合恢复协议

首次执行从 G0 中第一个没有机器证据的条目开始。此后始终运行下面的闭环：

```text
读取当前 Git/状态文件/证据索引/最近 Gate/最近 VM Run
→ 验证事实，不相信自然语言完成声明
→ 选择离最终 DoD 最近的最小失败链路
→ 先建立能复现失败的测试或 Trace
→ 修复生产代码、迁移、配置和必要文档
→ 运行最小测试
→ 运行受影响子系统回归
→ 运行本地总门禁和与本次改动相关的最小安全检查
→ 更新状态、失败分类和机器证据
→ 满足准入即构建同一工作树候选并进入下一验证层
→ 部署实验环境、运行最小失败 VM 用例
→ 修复后逐级扩大到代表场景集
→ 自动领取下一项，直到 G0-G11 和最终 DoD 全部通过
```

宿主对话回合结束、上下文压缩、进程重启或机器暂时不可达，都不能把任务退化为重新规划。必须维护并恢复：

```text
reports/implementation/ai-agent-runtime-state.json
reports/implementation/ai-agent-runtime-evidence.jsonl
```

状态文件至少记录当前工作流、D01-D20 与 P01-P12 状态、Holdout 聚合结果、工作树指纹、测试报告、候选 Manifest、VM Run、真实性等级、唯一下一动作和阻塞恢复命令。状态只能由 Traceability Matrix、测试结果和 `score.json` 晋级。

缺少模型凭据时继续完成 Scripted Provider、Contract、安全、持久化和 UI Fixture；缺少 VM 凭据时继续完成本地、容器、候选构建和只读预检。只有剩余动作确实需要新的 Secret、外部权限、生产变更或不可逆操作时，才向用户请求最小输入。用户提供后从恢复点继续，不重新按批次汇报或等待“继续”。

## 12. 最终完成定义和演示交付门禁

系统只有同时满足以下全部条件，才能称为“功能完整、可稳定演示的 Mini-Drop 内嵌 Agent Beta”并向用户交付：

- 两种入口共享同一个持久 Case、Evidence Inventory 和 InvestigationPlan；
- InvestigationPlan 是 Case 调查的唯一编排真源，只有一个持 Case Lease 的 Supervisor 创建 Case/Plan 派生 Task/SourceCall；第一页非 Case 的普通 Drop Task 仍可独立使用；
- canonical Case Evidence Store 是唯一事实真源，Attachment 和旧 DiagnosisEvidence 不形成平行结论链；
- Agent 能回答问题而不误启动调查，也能从模糊问题自主开始调查；
- 已有数据优先复用，缺失事实才触发受控采集；
- READ_LOW 可自动连续执行，用户可实时暂停、转向、补证和纠错；
- Pi 真正参与每轮计划、证据观察和停止判断，而不是只做首次意图分类；
- Assistant/Tool/Decision/结束事件从 Sidecar 回传并持久化，AcceptedTurn 不冒充完成；
- Worker 侧 Collector/Query 必须经过 Mini-Drop Task/Supervisor/Worker；控制面 MCP 必须经过 SourceCall/SourceGateway；不存在模型直连执行面；
- Evidence、决定、工具、计划、任务、覆盖率和结论都能审计；
- Sidecar、Server 或 Worker 重启后可以恢复且不重复产生副作用；
- 集群结论受 Membership Snapshot 和覆盖率约束；
- 集群既能同构基线采集，也能按角色/异常/假设差异化采集，人工和 AI 共用同一 Campaign；
- Agent 可自主执行注册的低风险查询，但没有任意 Shell 权限；
- Skill、Knowledge 和 MCP 扩大认知能力但不替代当前 Evidence、也不扩大执行权限；
- 复合故障能够区分 Primary、Contributing、Amplifier、Propagation、Symptom 和 Coincidental Anomaly；
- 证据不充分时逐项说明采集结果、失败原因、现有数据含义边界和下一动作，不使用空泛结论；
- 修复建议绑定原因/传播边，区分临时缓解、根因修复、放大治理和验证；
- deterministic 降级、功能开关和候选回退经过实际演练；
- 当前精确候选在三节点实验环境通过量化门禁、故障清理和最终健康检查；
- 前端只展示并操作真实后端状态，不再用一次性卡片伪装持续会话；
- 非技术用户可以只描述现象并选择自动调查，不需要理解 PID、Collector、Evidence、MCP 或 Agent Runtime；
- 专家用户可以干预计划、补充/排除数据、检查覆盖率，并从结论追溯到原始采集；
- AI 创建的所有采集任务同时出现在原任务系统中，状态、取消和结果只有一个事实来源；
- 部署承载评估最低覆盖 CPU、内存、磁盘、副本、allocatable、reservation、安全余量和新鲜度；峰值/N-1/调度/Quota/依赖缺失时明确条件和可靠拒答；
- 安装、配置、演示流程、MCP 接入、故障恢复、环境清理和 deterministic 回退文档经过实际演练；
- 旧链路保留为兼容和降级；第一页 Drop 服务始终可以脱离 AI 独立工作；
- 候选没有会阻断核心演示链路的缺陷、假实现或占位接口。依赖漏洞作为已知限制记录，除非能够直接破坏当前实验环境或泄露 Secret，不要求全部清零。
- P01-P12、Holdout H01-H17/H18a/H18b/H19 共 20 个 required slot、Causal Capability Gate、Fault realization 和 cleanup 都有与当前候选绑定的 evaluator 证据。

### 12.1 功能与证据硬门禁

- 两种入口 E2E 通过率 100%；
- 用户显式引用的有效 Task/Collection 消费率 100%；
- Conclusion Evidence 引用有效率 100%；
- 已排除 Evidence 后续引用次数为 0；
- 相同 Fingerprint 并发重复 Task 为 0；
- 旧 plan/scope revision 创建 Task 次数为 0；
- 集群结论的成员、故障域、时间和覆盖率记录率 100%；
- 身份变化后的实例/进程 Evidence 错误归并次数为 0；
- 人工和 AI 的差异化 Campaign 均能生成正确的共同基线与不同 Assignment；
- 所有注册 Query 都通过原生 Task 产生 Artifact/Evidence，直接 Sidecar Shell 次数为 0；
- Sidecar/Provider 不可用期间普通 Drop Task 成功率相对 deterministic 配对基线下降不超过 2 个百分点。

### 12.2 Agent、因果与效率硬门禁

- 固定 P01-P12 全部完成；P03/P06/P09 连续 3 次均达到对应核心断言；
- Holdout 20 个 required slot 全部产生有效、签名且与当前 candidate/contract/source/model manifests 绑定的结果；
- 公开 `CAUSAL_SINGLE` Case Primary 命中率 100%；Holdout 仅在适用分母内 Exact Top-1 ≥80%、Top-3 ≥94%、机制 ≥75%、必需传播边 micro F1 ≥80% 并报告 macro；H13 多根集合 F1=100%；
- H19 健康场景正确 abstain 率 100%，不得无故障强行输出根因；
- 命中 forbidden primary cause 的运行次数为 0；
- 证据不足/采集失败场景的 Evidence Gap 完整率 100%，不得只返回泛化“证据不足”；
- 修复建议对 Primary、Amplifier 和验证链的覆盖率 100%，不把末端症状当首要根修复；
- Oracle 标记应拒答/部分确认的 Case 中，正确校准率不低于 90%；高置信错误为 0，整体错误自信率不高于 RulesOnly；
- 共同适用 Profile 的 Agent median 达到第 8.8 节 `rules_target`，RulesOnly 不适用指标不以 0 分制造提升；
- Provider Ledger 中每个驱动 Plan/Tool 的模型响应均能绑定 AgentDecisionRecord，装饰性模型调用次数为 0；
- 无效新采集率低于 10%；可完全复用时新建 Task 数为 0；
- 相同故障/负载/预算/seed 的配对测试中，数据驱动入口相对无数据入口的平均新增 Task 数下降至少 30%；
- 去除随机 ID/时间戳后，重复调查的 operation/role/order/dependency 计划一致率至少 80%，但 Evidence 分叉时必须产生合理差异；
- 连续低信息增益超过 2 轮的 Case 为 0；
- Skill 误触发导致越界/无关采集次数为 0；Knowledge 被当作 Current Evidence 的次数为 0；
- 每个 Case 的模型、Token、Tool、Task/Query、Fanout、Artifact 和 MCP 均在预设演示预算内。
- 正式 Suite 总 attempt、Token、Tool、Task/MCP、时间、缓存和 Provider 费用均在第 8.9 节总预算内，未发生 best-of 重跑。

### 12.3 交互、恢复与性能门禁

- 本地实验网络正常时，Case Event 和用户控制在数秒内可见/生效，不要求生产级 P99；
- Task 完成事件能够自动唤醒 Agent，不依赖浏览器或模型轮询；
- 用户纠正后下一 Plan 100% 使用新的 scope/constraint revision；
- 刷新、断线重连、Server/Sidecar/Supervisor/Worker 重启后恢复完整时间线和当前状态；
- 重复事件、重试和恢复产生的重复副作用为 0；
- 两个并发 Case、较大 Evidence Bundle 和长会话 Compaction 可以完成且界面可继续使用；记录耗时和资源即可。

### 12.4 最小安全与环境门禁

- 任意 Shell/文件工具、未注册命令和修改型 Query 调用均为 0；
- Oracle、Secret、私有思维链泄漏均为 0；
- MCP/Knowledge 中的指令不能改变 Tool allowlist、风险和 Case scope；
- 当前精确候选在三节点实验环境完成代表场景和三个关键重复场景；
- 最终动态 Case 标记 R4，R2 Replay 不能冒充三节点动态闭环；
- 外部只读 Acceptance Authority 验签通过，`EvaluationTrustLevel=INDEPENDENT_HOLDOUT` 且 `HoldoutAcceptanceStatus=VERIFIED`；仓库自签或 DEVELOPMENT_EVAL 不满足门禁；
- 故障注入、Worker 离线、进程重启和 deterministic 回退后环境全部清理并回到健康基线；
- 最终依赖漏洞报告存在并记录结论，但不要求修复与当前演示链路无关的全部 High/Moderate 项；
- `ai-agent-runtime-state.json` 中完成项都有可读取机器证据，`blocked_items` 为空；
- 不得通过 Mock 替代最终真实链路、跳过核心场景、泄露 Oracle、使用旧报告或默认降级成功获得通过。

只有以上门禁全部通过，才能生成最终演示交付报告。报告必须明确当前 Commit、候选 Manifest、环境、模型/Prompt/Skill/Knowledge/MCP 版本、代表场景、三节点结果、功能边界和已知限制。任何核心链路未满足时继续修复，不交付只会播放静态数据、依赖 Mock、无法连续调查或功能缺失的“假 Demo”。
