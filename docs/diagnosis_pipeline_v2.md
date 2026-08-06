# Mini-Drop 证据驱动诊断流水线 v2

> 本文描述当前 v2 实现基线。产品目标是生产级、多租户、多集群服务；本文中的三节点仅是首个实验 `EnvironmentProfile`。目标授权、信息源与安全执行架构见 [`ai_authorization_and_tooling.md`](ai_authorization_and_tooling.md)。

## 1. 核心不变量

本轮不是继续叠加启发式条件，而是把以下约束固化到数据模型、状态机和 Verifier：

- 时间一致：同时保存 requested/effective time range；HISTORICAL 不用当前采集证明历史故障，LIVE 新采集必须落在受预算限制的 effective window。
- 目标可信：必须存在目标服务锚点；实例、Agent、hostname、PID、环境和依赖边都经过校验，不允许无锚点扩散。
- 证据同域：Host/Process/Container/Dependency 分域；Host Evidence 不能单独证明 Process Claim。
- 动作可执行：Action 是严格结构化对象，命令由可信 Renderer 从字段重新生成并校验，不接受模型自由文本命令。
- 副作用唯一：会话和步骤采用数据库 CAS、lease、幂等 step ID、唯一索引和 Task Outbox；同一步骤的并发批准只能创建一个 Task。
- 状态可终止：无计划、超预算、历史证据缺失、拒绝审批、超时和证据不足都进入明确终态。

## 2. 显式流水线

```mermaid
flowchart LR
    A[understand_intent] --> B[resolve_scope]
    B --> C[build_hypotheses]
    C --> D[plan_evidence]
    D --> E[risk_gate]
    E --> F[run_probes]
    F --> G[normalize_evidence]
    G --> H[analyze_evidence]
    H --> I[assess_cluster]
    I --> J[retrieve_knowledge]
    J --> K[generate_actions]
    K --> L[verify_report]
```

每个节点在 `diagnosis_node_runs` 中保存状态、attempt、输入/输出引用、指标、错误码、时间和实现版本。会话带 `row_version`、deadline 和 lease；后台扫描按最久未更新时间公平推进。

覆盖矩阵按 `Target × EvidenceRequirement` 展示 `QUEUED/RUNNING/WAITING_APPROVAL/COMPLETED/FAILED/...`。R1 先覆盖所有目标；只有整轮 R1 仍不足时，才为信息增益最高的单一目标规划 R2。复用证据也会保留对应的 COMPLETED 覆盖行。

## 3. 数据与判定

### 3.1 sys_metrics.v2

Agent 输出 `schema_version=sys_metrics.v2`，顶层严格区分：

- `host`：CPU（含 nice/irq/softirq/steal）、load 窗口均值与斜率、内存、PSI、宿主机网络；
- `process`：按 `/proc/<pid>/stat` tick 增量计算的 CPU 核使用量、RSS/斜率、I/O 速率、FD/线程增长；
- `container`：cgroup v2 CPU、内存和 I/O（可用时）。

解析 `/proc/<pid>/stat` 时兼容带空格和括号的 comm。Server 可归一化旧 v1 数据；`MINI_DROP_SYS_METRICS_STRICT_V2=1` 可在切换完成后拒绝旧版本。

### 3.2 Evidence

Evidence 保存：

- source type/system、incident/reproduction role；
- target、event time range、ingestion time；
- query/probe、raw/derived artifact ref、derivation version；
- observed/baseline/anomaly、quality、fact domains、claim links；
- 对完整规范化记录计算的 SHA-256。

LIVE 默认只复用结束时间不超过 120 秒、DONE、含结构化 `sys_metrics` 且完整覆盖本轮目标的 Task。任一目标缺失或过期时，重新进行整组 R1 采集。

### 3.3 Analyzer 与报告

确定性 Analyzer：

- `os_cpu_analyzer.v2`
- `io_wait_analyzer.v2`
- `memory_pressure_analyzer.v2`
- `network_latency_analyzer.v1`
- `mysql_lock_analyzer.v1`
- `jvm_gc_analyzer.v1`
- `cluster_assessor.v2`

`root_location`（self/same_host/downstream/shared_resource/unknown）与 `domain_cause`（cpu/io/memory/network/database/runtime/unknown）独立输出。CPU 判定会区分宿主机饱和、进程 CPU 压力和已有 Profile 支撑的代码热点。

报告核心使用严格 `DiagnosisReport` Schema。Verifier 检查字段契约、Evidence/Knowledge 引用、时间窗、跨目标时间对齐、证据质量、事实域、目标范围、Hash、Analyzer 注册、Action Renderer、CLI 认证来源和风险策略。失败报告不写入结论版本。

## 4. Action、CLI 与审批

Action 包含 type、collector、target、parameters、renderer version、rendered command、comment、risk、approval policy 和 Evidence refs。所有动作均 `auto_execute=false`：

- R0：只读检查；
- R1：低风险、可自动编排采集；
- R2：只允许 `single_execution` 单次审批；
- R3：在当前 v2 实现中只给人工建议；目标架构改用操作类别、影响等级和授权结果三轴模型，只有满足严格门槛的低风险可逆变更才可自动执行。

CLI 的远端 API 命令统一支持 `--api-key-env`，通过环境变量生成 Bearer Header。`collect` 支持 `sys_metrics`；`diagnosis-inspect` 可直接查看会话。前端审批弹窗展示目标、参数、风险和预计成本。

## 5. 评测与当前三节点实验 Profile 实测

离线 Golden Harness 位于 `golden_scenarios/`，覆盖自身 CPU 热点、同宿主噪声、共享 I/O、下游 CPU、内存增长、网络异常和 MySQL 锁等待：

```bash
make eval
# 或 python scripts/run_diagnosis_eval.py --output-dir reports/eval
```

2026-07-22 最终验证结果：

- 后端全量：363 passed；
- 诊断专项：30 passed；
- Golden：7/7 passed，分类准确率与 Evidence 引用完整性均为 100%，危险自动执行数为 0；
- Web：Vite production build passed；
- 三节点：Control `192.168.10.10`，Worker 1 `.11`，Worker 2 `.12`，测试清理后两个 Agent 均 ONLINE。

真实调用链为 `load → service-a(worker1) → service-b(worker2)`：

- `diag_session_20260722_130907_8827a3dd`：两目标全新 R1 采集，12/12 节点完成，4 条 `incident` Evidence 均有 Hash/质量/时间窗，产物为 `sys_metrics.v2`，分类 `downstream_dependency`，Verifier passed。
- `diag_session_20260722_131128_02666375`：在新鲜度策略内复用同一轮证据；目标 service-a 无压力，下游 service-b 进程约占 1.01 个核心；输出 `root_location=downstream(service-b-1)`、`domain_cause=cpu/process_cpu_pressure`，Verifier 检查 4 Evidence、2 Knowledge、2 Action 后通过。
- `diag_session_20260722_131418_7f9b93fa`：无故障对照场景在 R1 不足后仅规划一个 R2；两个并发批准返回 200/409，只创建一个 Task，完成后进入 `INSUFFICIENT_EVIDENCE`，3 个 child task 均唯一。
- `diag_session_20260722_131708_84d93ed2`：显式 HISTORICAL 且无历史 Evidence，直接进入 `INSUFFICIENT_EVIDENCE`，Task/Probe 均为 0，Verifier passed。

实机升级还发现并修复了三个仅靠全新内存数据库难以暴露的问题：旧 SQLite 缺少 v2 新列、WAITING_APPROVAL 重复轮询触发非法迁移、LIVE 默认 requested window 不包含随后采集时间。三者均已有回归测试。

## 6. 当前能力边界

已经具备：当前三节点实验 Profile 的控制链路、显式流水线、全目标 R1 覆盖、单目标自适应 R2、Task 创建 Outbox、并发幂等、证据时间/目标/质量/域校验、确定性 Analyzer、严格报告、结构化 Action 和可重复评测。

部分具备：拓扑来自请求上下文而非 CMDB/Kubernetes/Service Mesh；SQLite/Moto S3 实验环境不等同于 PostgreSQL/MinIO 生产持久化；网络/MySQL/JVM 有 Analyzer 契约和 Golden 输入，但生产采集 Adapter 尚未全部接入。

尚不能宣称：完整 TaskAttempt/独立 Analyzer 队列、任意外部副作用的分布式 exactly-once、生产对象存储对账、大规模调度与容量隔离、OIDC/RBAC/资源组、经过大规模演练校准的误报漏报率，以及受控的低风险自动修复。

因此当前版本适合作为三节点环境画像下的演示、项目验收和诊断控制面 MVP，而不是产品规模边界；进入生产值班前仍需补齐真实拓扑与 Observability Adapter、TaskAttempt、生产存储、权限体系、Source/Action Gateway 和多集群隔离验证。
