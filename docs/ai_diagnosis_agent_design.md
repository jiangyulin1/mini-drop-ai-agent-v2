# Mini-Drop 诊断 AI Agent 设计（统一版）

> 状态：可执行设计真源（2026-08-08）。本文同时标明**当前契约**与**目标演进**，替代此前叠着的
> `ai_functional_design_v3.md`、`ai_authorization_and_tooling.md`、`ai_feature_extension_design.md`。
> 旧的文档保留作实现参考，不再作为设计依据。
> 代码基线：GitHub `main` @ `6c90e1a`。
> 实现映射与验收状态见 [`ai_design_traceability.md`](ai_design_traceability.md)。

---

## 0. 一句话定义

> **一个以 Incident Case 为持久协作单元、由结构化证据驱动的诊断助手**——先复用有效证据，再最小化采集，所有结论可追溯，高风险动作必须审批。

当前版本已经实现 Case 协作、目标级长期会话、规范化信号孵化、profiling detail 窗口检索，以及注册维护动作的受控修复闭环。Agent 数据流自动订阅、7/90 天降采样层和通用生产服务修复仍是后续演进。

---

## 1. 四个支柱

| 支柱 | 含义 |
|---|---|
| **持续跟进** | 会话长期挂载目标，数据流驱动，新信号随时推进；本次故障处理完不关闭，线索跨故障串联 |
| **多数据接收判断** | 系统指标 / Profile / 拓扑 / 异常事件 / 用户描述五路数据融合，程序投影成紧凑信号包，AI 据此判断 |
| **主动推进根因** | 核心动作是"选采集器 + 推进"，默认自动推进到根因收敛；人在窗口里实时监控、可随时打断 |
| **权限内直接修复** | 权限内低风险修复直接尝试→验证→回滚；权限外出提案卡等你批准或接手 |

---

## 2. 对象模型：当前 Case 与目标嵌套会话

### 2.1 当前已实现

`IncidentCase` 是租户隔离的协作对象，持有问题、恢复目标、范围、时间窗、初始任务、消息、状态、诊断引用和审计事件。`DiagnosisSession` 是一次可取消的诊断运行；Case 纠正范围时会使旧运行失效并解除关联。

### 2.2 目标会话（当前已实现的核心契约）

```text
DiagnosticTargetSession  目标级·长期（挂载在 service/cluster 上，一个目标一个）
├── 长期积累：目标基线、历史故障索引、学习到的模式、变更记录、Owner
├── 数据流订阅：agent 心跳指标 / 异常事件 / 拓扑 / 持续 profiling / 变更登记
└── children: IncidentSession[]（子会话，按需孵化）

IncidentCase  故障级·子会话（问题驱动，一次一个线程）
├── problem_description / recovery_goal / 时间窗
├── current_understanding     ← AI 的 mental model（见 §4）
├── evidence_set（本轮证据，带哈希/质量/时间/域）
├── action_history
├── status: monitoring → collecting → analyzing → repairing → verifying → resolved
│           可中断：paused / aborted
└── parent: DiagnosticTargetSession

关系：目标会话负责"持续跟进"，按需孵化子会话处理故障；
     子会话的结论、修复结果、变更与失败回流到目标会话的积累。
```

**当前两种创建方式**（对应"问题驱动 + 数据驱动"双入口）：
- 从空开始：用户描述问题 + 目标范围；
- 从已有数据开始：选择已有 Task，且任务必须 `DONE`、具有结构化产物、与 Case 明确实例范围及事故时间窗一致。当前 Task 没有 tenant/environment 归属字段，因此**不承诺跨租户、跨环境证据复用**。

---

## 3. 数据流与持续跟进

### 3.1 信号输入面

| 信号 | 来源 | 触发 |
|---|---|---|
| 异常事件 | Agent 心跳 `sys_metrics.v2` 越界 / event_bus | 孵化或推进子会话 |
| 周期/趋势信号 | 基线+斜率检测（程序，非模型） | 慢性问题（如内存泄漏）自动发现 |
| 用户消息 | 交互窗口 | 立即重新评估 |
| 动作完成 | 采集/修复/验证回填 | 新证据回流 → 判断收敛或继续 |
| 变更登记 | 用户登记 / AI 追问（§7） | 变更关联 |
| 持续 profiling 窗口 | ring buffer 检索（§3.3） | 补历史现场 |

### 3.2 成本控制

- **数据流不全部进模型**：程序侧先做信号筛选，只有越阈值的信号唤醒会话；
- 被唤醒后 AI 只读**紧凑信号包**（程序投影，保留哈希/质量/时间窗），不读原始数据；
- 一次会话默认只做**最小充分动作**，避免发散采集。

### 3.3 持续 profiling（detail 检索已实现，长期聚合待实现）

| 维度 | 默认值 |
|---|---|
| 运行方式 | Agent 常驻低频采样，持续运行 |
| CPU 开销上限 | ≤5% 单核（按环境可调；test 可更高，prod 默认此值） |
| 采样频率 | perf 20–49Hz 常驻；JVM 场景 async-profiler 连续模式 |
| ring buffer | 当前：原始 detail 索引默认保留 24h；目标：降采样聚合 7 天 → 每日基线聚合 90 天 |
| 磁盘预算 | 每节点约 5–10GB |
| 检索 | `GET /api/v1/target-sessions/{id}/profile-windows?start=&end=`；只返回 tenant、目标实例与时间范围匹配的索引 |

作用：异常信号到达时**不需要"正在采"**，直接从 ring buffer 查窗口证据；"历史即现场"。

---

## 4. 当前理解（程序化投影）

当前由 Case 假设图和诊断证据**确定性派生**，不是 AI 自由重写；假设图仍是持久化推理结构，`current_understanding` 是面向用户的安全投影：

```json
{
  "target": "order-svc: m1/m2/m3",
  "symptom": "延迟 p95 每日 14:23 尖峰约 60s",
  "understanding": "怀疑 m1 进程级 CPU 压力（指向单机）",
  "confirmed":   ["m1 CPU 92% @14:23:00-14:23:40", "m2/m3 正常"],   // 引用 evidence_refs
  "contradictions": ["db-1 无异常", "同宿主无噪声"],                // 引用反证 evidence_refs
  "missing": ["m1 进程级 CPU 分配", "该时段进程清单"],              // 按证据域判定
  "next": "perf_cpu(m1) 或 持续 profiling 窗口证据"
}
```

- **confirmed / contradictions / missing 由程序从新证据自动推导**；不存在的证据引用必须进入 missing，不能进入 confirmed；
- 证据域：`host / process / container / dependency / database / runtime / network`；
- `missing` 是确定性骨：AI 只能在它驱动出的候选里选（见 §5）；
- 用户随时问"为什么这么判断"→ 直接引用 confirmed 证据链回答，不重新跑模型。

---

## 5. 采集器选择与 AI 自主权

### 5.1 场景 → 候选映射表（确定性骨）

每个场景类型有确定候选集，AI 在其中选择并给出理由：

| 场景 | 症状 → 证据域 | 候选采集器 |
|---|---|---|
| S1 每日瞬态波动 | 单机 CPU → process | perf_cpu、持续 profiling 窗口、进程清单 |
| S2 慢性内存泄漏 | 趋势 → memory/runtime | 当前：memory_smaps、sys_metrics；目标：语言运行时 heap profile |
| S3 发布后回归 | 变更关联 → 前后对比 | 变更登记、同参采集前后对比 |
| S4 下游共享资源 | 全机受影响 → dependency/network/database | 当前：log_scan、sys_metrics、eBPF IO；目标：mysql_lock、tcp_retransmit |
| S5 同宿主噪声邻居 | 单容器 → container | 同宿主实例对比、cgroup 指标 |
| S6 偶发不可复现 | 窗口重建 → 任意域 | 持续 profiling buffer、Need You |

候选由确定性注册表（`probe_registry`）生成，最小充分原则。

### 5.2 候选缺失兜底（AI 自主权）

| 情况 | 规则 |
|---|---|
| 候选集非空 | AI 只能在候选集里选（默认路径） |
| **候选缺失** | 仅当证据域存在经过设计的 TaskKind 映射时才提案；未知域或映射采集器不可用时明确暴露缺口，不拿任意采集器凑数 |
| 白名单外 | **禁止**。AI 永不发明命令 |

**候选缺失的提案永远走审批（USER_APPROVAL）**，`missing` 中标 `candidate_gap: true`。自主权只在"已存在、未入默认候选表"的采集器里。

---

## 6. 审批海拔与权限

**海拔（已确认）**：权限内自动推进，权限外/高影响停下等你。

| 动作 | 默认 |
|---|---|
| 候选内采集 / 只读分析 / 持续 profiling 窗口查询 | 自动（会话内授权包络内） |
| 候选缺失提案、扩大目标范围 | USER_APPROVAL |
| 低风险修复（缓存清理等） | 权限内自动；权限外 USER_APPROVAL |
| 高影响动作（重启核心实例、配置变更、回滚） | USER_APPROVAL / CHANGE_APPROVAL |
| 代码级修复 | manual_only，只给建议+命令+价值 |

`REMEDIATE` 已接入 Case 恢复方案，但执行面仍采用窄白名单：只有 `implementation_status=executable` 且有服务端执行器的动作可以进入预检和审批。其余生产动作保持 `policy_only`，只生成建议。

授权模型复用现有 `authorization.py`（Source/Grant/OperationClass/ImpactLevel），AI 以**发起用户身份和资源范围**代理执行，全程审计。

---

## 7. 变更登记（方案 C：AI 问 + 用户登记）

解决 S3 死结——AI 看不到发布/配置变更记录，只能从用户获取：

- **用户登记**：Web「变更登记」入口，登记一条（服务/时间/内容/版本/开关），AI 自动做"变更前 vs 变更后"对比；
- **AI 追问**：检测到回归但无登记时，AI 走 Need You 问"最近有发布或改配置吗"，回答后记录关联；
- 变更记录进入目标会话积累，参与前后对比和回归关联。

---

## 8. 修复执行与验证（首个闭环已实现）

```text
根因收敛（current_understanding 的 understanding + confirmed 证据链）
→ 修复提案卡（依据/推断作用/影响面/价值/验证方法）
→ 审批（按 §6 海拔）
→ 执行：白名单命令（action_registry + actuation gateway，dry-run→execute→rollback）
→ 验证：复用采集 + No-Regression（目标达到、保护指标未退化、同故障域无新异常）
→ 未恢复 → 回滚并升级人工
→ 恢复 → 跨天/跨窗口稳定观察 → resolved → 结果回流目标会话积累
```

修复后子会话**不马上关闭**——进入持续观察（这本身是"持续跟进"的闭环）。

---

## 9. 交互窗口

对话流 + 动作卡 + 实时数据台。AI 的推理、动作、证据、修复都出现在对话里；用户用自然语言控制。

```text
┌─────────────────────────────────────────────┐
│  ● order-svc · 生产 · 目标会话（第 4 轮）      │
│  你：cpu 飙高，帮我看下                       │
│  AI：收到。五路数据已核对（3 confirmed）：      │
│     · m1 CPU 92% @14:23，m2/m3 正常           │
│     → 当前理解：怀疑 m1 进程级 CPU 压力         │
│     → 下一步：perf_cpu(m1) [卡片：依据/作用/影响]│
│  ── 采集完成，新证据回流 ──                  │
│  AI：perf 显示 60% 时间在批处理脚本（每日 14:23 启动）│
│     → 修复提案[低风险·权限内]：错峰批处理        │
│       [执行] [先看验证方案]                  │
│  你：为什么不是下游 db？                      │
│  AI：（confirmed 证据链 + contradictions 排除）…│
└─────────────────────────────────────────────┘
```

用户可随时：打断、改向、追问"为什么"、暂停、停止、撤销授权、手工完成并回填、纠正判断、标结论对错。

---

## 10. 六种业务场景（全部纳入范围）

| # | 场景 | 关键难点 | 设计响应 |
|---|---|---|---|
| S1 | 每日一分钟瞬态波动 | 捕捉、定位、周期线索 | 持续 profiling、信号驱动、多机时间对齐、跨天验证 |
| S2 | 慢性内存泄漏（周级） | 趋势、长周期、现场难留 | 基线+斜率信号、多子会话、heap/memray、manual 修复 |
| S3 | 发布后性能回归 | 变更关联、前后对比 | 变更登记（C 方案）、feature-flag 回滚 |
| S4 | 下游共享资源抖动 | 上下游归因 | 多机时间对齐、dependency/database 证据域 |
| S5 | 同宿主噪声邻居 | 容器隔离、宿主竞争 | container 证据域、同宿主对比 |
| S6 | 偶发不可复现故障 | 现场捕捉 | 持续 profiling 唯一通道、诚实降级、Need You |

统一判定路径：**症状 → 多机时间对齐定位根因位置 → 证据域驱动缺什么 → 候选选采集器 → 收敛 → 修复 → 验证**。

---

## 11. 与现有代码的关系

| 动作 | 内容 |
|---|---|
| **保留** | 确定性采集内核、`sys_metrics.v2`、`probe_registry`、Evidence 哈希/质量/域、`authorization.py`（Source/Grant/Impact）、`action_registry.py`（分级/预检/回滚）、`actuation.py`（dry-run→execute→rollback）、No-Regression 思想、审计 |
| **现状** | `IncidentCase` + `DiagnosisSession`、12 节点可审计流水线、持久化假设图、程序化当前理解、显式风险门 |
| **演进** | 在现有目标会话、窗口检索和受控修复闭环上增加 Agent 自动信号订阅、长期聚合和更多可验证执行器 |
| **新增** | `DiagnosticTargetSession`、持续 profiling（复用 `continuous_perf`）、变更登记、场景→候选映射表、`current_understanding` 状态、提案卡展示层 |
| **不变式** | 假设与结论必须引用真实证据；模型不能扩大范围、伪造工具或绕过审批；GET 投影不能改变诊断决策 |

---

## 12. 分阶段路线

| 阶段 | 内容 | 退出条件 |
|---|---|---|
| **当前交付** | 当前理解、目标会话、规范化信号、detail 窗口检索、受控维护修复、VM 评分门禁 | 本地代码、迁移、黄金集、Web 与静态测试集门禁全部通过 |
| **P1 持续数据增强** | Agent/event bus 自动订阅、7 天聚合、90 天基线 | 一个瞬态场景无需人工登记信号即可唤醒并重建历史现场 |
| **P2 修复扩展** | 更多生产对象执行器、通用 No-Regression 探针与稳定窗口自动结案 | 一次真实服务会话完成"根因→提案→审批→执行→验证→结案" |
| **P3 沉淀与治理** | 知识沉淀（结案→KnowledgeCandidate→审批→复用）、拓扑双轨 + AI 推断、审批策略可配置表 | 二次相似故障可引用历史结论；策略可配置审计 |

每阶段保 `make eval` Golden 回归；新增可执行 Action 必须配回归用例与回滚测试。

---

## 13. 评测

- **场景覆盖**：六场景各配 golden 样本（含三节点/多机时间窗对齐样本）；
- **调查质量**：根因 Top-1/Top-3、关键证据召回与引用准确率、反证覆盖率、无效采集调用率、调查成本；
- **安全**：未授权动作次数=0、Secret 进模型=0、Scope 扩大=0、错误自动批准率、自动修复成功率与回滚成功率、No-Regression 违反次数；
- **体验**：Time to First Useful Finding、Time to Verified Recovery、用户接管次数、Need You 有效率。

---

## 附：本次设计已确认的决策清单

1. 对象模型：目标级长期会话 + 故障级子会话（双轨）；
2. 审批海拔：权限内自动、权限外/高影响停下；
3. 修复能力开通：测试默认开、生产显式；
4. 证据集：当前仅允许同 Case 明确实例范围与事故窗口；跨环境需先补 Task 归属与授权模型；
5. 知识沉淀：推迟到 P3；
6. 变更登记：C（AI 问 + 用户登记）；
7. 六场景全部纳入；
8. 持续 profiling 上限按高设计（≤5% 单核、24h 原始 detail）。
