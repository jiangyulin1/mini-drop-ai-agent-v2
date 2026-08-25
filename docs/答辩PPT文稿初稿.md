# Mini-Drop AI 答辩 PPT 文稿初稿

> 版本：v0.1（基于 2026-08-24 当前代码与验收文档）  
> 建议时长：10–12 分钟，正文 13 页；如答辩要求 8 分钟，可合并第 5、6 页和第 10、11 页。  
> 核心口径：只把当前代码、API、测试和真实验收能够证明的能力称为“已实现”；规划能力单独标注。

## 总体叙事

### 一句话定位

> Mini-Drop 是一个 Evidence-native 的受监督诊断 Agent：AI 在受控权限、范围和预算内选择 Linux 深度采集器，把采集结果固化为可追溯 Evidence，再基于带引用的证据推进调查或正确拒答。

### 最短答辩句

> 模型提议，网关裁决，Supervisor 编译，Worker 执行，Evidence 固化，Wakeup 续跑，Verifier 定案。

### 叙事主线

```text
传统监控信息不足
    -> Mini-Drop 能主动补采 Linux 进程/运行时深度证据
    -> 采集结果不只是聊天上下文，而是可治理 Evidence
    -> 人工可以审查、排除、降低信任并触发重新调查
    -> 因此系统的价值是“可验证的调查过程”，不是一次性猜根因
```

## 正文页面

### 第 1 页：封面

**标题**：Mini-Drop：Evidence-native AI 深度采集与诊断工作台  
**副标题**：让 AI 获取证据，但不让 AI 越过系统边界  
**页脚**：姓名 / 项目 / 日期

**讲解词**：

本项目面向 Linux 进程性能排查和故障调查。核心不是让模型直接给出一个“看起来合理”的根因，而是让它在安全边界内决定下一步需要什么证据，并把整个调查过程留下可复查的证据链。

**图示建议**：左侧问题输入，中央 Evidence Ledger，右侧结论/拒答；不要使用营销式大图。

---

### 第 2 页：问题背景与痛点

**标题**：为什么普通 AI 问答不足以完成运维调查？

**页面文案**：

- 监控指标、日志和 Trace 往往只能说明“现象”，不能解释进程内部机制。
- Linux 深度证据分散在 `perf`、eBPF、`smaps`、运行时快照等工具中。
- 原始产物体量大、格式不一，直接塞进 Prompt 会导致成本、截断、引用和可信度问题。
- 只输出一个根因分数，无法回答：证据来自哪里？是否过期？被排除后结论怎么办？

**讲解词**：

运维调查的难点不是“能不能生成一段解释”，而是能不能把问题拆成可验证的信息目标，再获得足够、正确、可追溯的证据。

**图示建议**：现象层（指标/日志）与机制层（进程/IO/内存/运行时）之间的证据缺口图。

---

### 第 3 页：项目目标与边界

**标题**：我们解决什么，不解决什么？

**页面文案**：

**目标**

1. 根据问题、范围、已有 Evidence 和预算选择下一项高信息增益 Collector。
2. 复用原生 Task、Worker 和 Analyzer，不另起一套执行器。
3. 将 Artifact 固化为 `CaseEvidence` 和有界 `EvidenceProjection`。
4. 让每条结论绑定 Evidence、Projection hash 和字段/文本位置。
5. 支持人工治理、失败恢复、正确停止和正确拒答。

**当前边界**

- 不提供任意 Shell、SSH、SQL、kubectl 或任意 MCP 执行。
- 不把规则 Top-N 当作 AI 核心能力。
- 不默认执行生产恢复动作。
- 不宣称通用拓扑平台、通用自动修复或正式根因准确率。

**讲解词**：

这里的边界是系统设计的一部分。证据不足是合法结果，自动拒答比错误自信更符合生产安全要求。

---

### 第 4 页：总体架构

**标题**：双层智能：模型负责决策，确定性内核负责可信执行

**页面文案**：

```text
Web / REST / SSE
        |
        v
FastAPI Control Plane
  Case / Policy / Revision / Audit
  AgentRuntimePort
       |---------------- Deterministic Runtime
       |---------------- Pi Adapter -> Pi Sidecar -> Model Provider
       |
       +-> Tool Gateway -> CollectionSupervisor -> native Task
                                              |
                                              v
                                     Linux Worker Collector
                                              |
                                              v
                                   Artifact -> Analyzer
                                              |
                                              v
                                  CaseEvidence -> Projection
                                              |
                                              +-> Wakeup -> next Agent cycle
```

**讲解词**：

模型没有数据库写权限，也不能直接创建 Task。模型只能提出结构化工具调用；Server 每次重新检查身份、租户、范围、能力、风险、预算、审批和版本，再决定是否进入原生采集链。

**关键技术点**：

- Pi 通过 Node.js Sidecar 接入，凭据留在 Sidecar 环境。
- Deterministic Runtime 作为无模型控制组和 fail-closed 路径。
- PostgreSQL/SQLite 中的 Case、Task、Evidence、Revision、Event 是业务真源，Sidecar 内存不是业务真源。

---

### 第 5 页：一次调查如何运行

**标题**：从用户问题到 Evidence-bound Conclusion

**页面文案**：

```text
用户问题 / 已有 Evidence
        -> Context Snapshot
        -> AI 维护 Hypothesis 与 Evidence Gap
        -> 提出 CollectorProposal
        -> Tool Gateway + Policy 校验
        -> CollectionRequest -> native Task
        -> Worker 采集 -> Artifact -> Analyzer
        -> CaseEvidence -> EvidenceProjection
        -> 引用校验的 Evidence Analysis
        -> 继续采集 / 停止 / 正确拒答
```

**讲解词**：

Agent 每一轮只做一个可解释的信息决策：当前最影响判断的缺口是什么，哪个已注册 Collector 最能区分替代假设。采集完成后，系统通过 Outbox/Wakeup 重建最新上下文继续调查，而不是依赖进程内状态。

**图示建议**：用一条横向时序图，突出 Proposal、Task、Evidence 三个持久化边界。

---

### 第 6 页：为什么要拆成 Artifact / Evidence / Projection

**标题**：把“工具结果”升级为可治理的证据对象

**页面文案**：

| 层次 | 作用 | 关键字段 |
|---|---|---|
| Artifact | 保存原始物理产物 | locator、size、SHA-256 |
| CaseEvidence | 表示 Case 中的稳定证据身份 | source、target、时间窗、lineage、lifecycle |
| EvidenceProjection | 给模型和 UI 的有界确定性视图 | schema、signals、top items、quality、raw ref |

**页面底部**：

`raw output -> deterministic parser -> canonical Evidence -> bounded Projection -> cited claim`

**讲解词**：

原始日志、火焰图和进程列表不能直接进入 Prompt。Projection 先按固定预算做字段投影和裁剪，同时保留原始产物、hash 和下载入口。这样模型看到的内容可控，结论引用的位置也可验证。

---

### 第 7 页：Evidence 治理与人在环

**标题**：人不是“改一个置信度数字”，而是治理证据是否准入

**页面文案**：

- Evidence 生命周期：`ACTIVE / EXCLUDED / INVALID / SUPERSEDED`
- 人工信任：`UNREVIEWED / TRUSTED / LOW_TRUST`
- 页面隐藏/归档与推理准入相互独立。
- 排除、降信任、恢复均追加 Review Revision，原始 Artifact 不覆盖。
- Review 变化会让依赖它的旧分析失效，并生成 `RECHECK_REQUIRED`。

**讲解词**：

专家不能直接把“可能”改成“确定”，但可以明确哪些证据不应继续影响推理。系统随后传播失效，保留历史结论，并从有效祖先重新调查。这是 Evidence 治理，不是一个模型概率调节器。

**图示建议**：Evidence → Review → stale propagation → revalidation 的四步流程。

---

### 第 8 页：分支隔离与可恢复调查

**标题**：让不同探索路径可比较、可回退、可审计

**页面文案**：

- 每个调查分支默认只能看到公共初始 Evidence 和本分支采集结果。
- `Hypothesis / Evidence Gap / Causal Graph / Dependency / Conclusion` 支持 `branch_id`。
- 公共 Evidence 可以显式 promote，不允许隐式跨分支泄漏。
- `control / scope / plan / evidence / runtime` 多类 revision 共同参与 fencing。
- 晚到的模型、工具、采集和分析结果保留，但不能覆盖当前结论。

**讲解词**：

这个设计解决两个问题：第一，两个 Agent 分支不会因为共享聊天上下文而互相污染；第二，用户在调查中途修改范围或排除 Evidence 后，旧结果不能静默写回当前状态。

**图示建议**：公共 Evidence 层，上方两个隔离分支，各自有 Hypothesis、Collector 和 Conclusion。

---

### 第 9 页：安全与权限边界

**标题**：模型提议，服务端裁决

**页面文案**：

每次工具调用都经过：

```text
身份 / 租户
AND Case Scope
AND Worker Capability
AND Tool Schema
AND Risk / Approval
AND Budget / Rate / Duration
AND Revision / Idempotency / Fence
```

**明确禁止**：

- 任意命令执行；
- 通过 Sidecar 目录扩大服务端权限；
- 绕过 R3/高影响审批；
- 把日志、文档或外部 Tool Result 当作系统指令；
- 将模型私有思维链写入业务记录。

**讲解词**：

Tool Catalog 只是发现元数据，不是授权凭证。真正的授权在 Server Tool Gateway 和确定性 Policy 中，每次调用都重新计算。

---

### 第 10 页：答辩演示设计

**标题**：一条可复现的 Evidence-native 英雄路径

**页面文案**：

```text
创建 Case
  -> 创建隔离调查分支
  -> 读取当前分支 Evidence
  -> 展开 Projection
  -> 形成 Hypothesis / Causal Graph
  -> 提交带引用的 Conclusion
  -> 排除一条 supporting Evidence
  -> 看到 RECHECK_REQUIRED
  -> 保留历史结论并重新验证
```

**演示重点**：

1. Evidence ID、目标、时间窗、Projection 和引用位置可见。
2. 结论允许为 `PARTIALLY_CONFIRMED`，不把单条信号包装成绝对根因。
3. Review/Exclude 后，Workspace 显示失效传播和下一步，而不是直接删除历史。

**讲解词**：

这一条路径由 `test_showcase_hero_path.py` 等回归测试保护，展示的是当前真正收敛的产品主线，不依赖旧 Diagnosis/RCA 兼容链。

---

### 第 11 页：实现与验收证据

**标题**：用代码、测试和真实运行证明闭环

**页面文案**：

- Task / Artifact / Analyzer / CaseEvidence / Projection 主链已打通。
- Evidence Review、Exclude、stale 传播和 `RECHECK_REQUIRED` 已有回归测试。
- Branch-local Hypothesis / Gap / Causal Graph / Conclusion 已持久化。
- Pi/DeepSeek 真实受控 Turn、工具事件审计和 Evidence-bound Conclusion 已验收。
- 检查点记录：后端完整测试通过，前端测试通过并成功构建；迁移和架构边界检查通过。

**建议在答辩现场展示的证据**：

- 一张 Workspace 截图；
- 一段工具调用审计事件；
- 一条 Evidence 的 projection/hash/citation；
- 一次排除后的 stale/revalidation 状态；
- 测试命令和结果摘要。

**讲解词**：

这里不把“仓库里存在某个模块”当作能力证明，而是展示真实 API、状态变化和自动化测试共同形成的闭环。

---

### 第 12 页：评测设计

**标题**：评测 AI 的证据获取能力，而不是只评最终文案

**页面文案**：

**固定条件**：同模型、同 Prompt 预算、同 Collector Catalog、同时间/请求预算、同初始 Evidence、同故障注入和同停止条件。

**核心指标**：

- required-fact coverage；
- Collector selection precision / recall；
- citation validity、unsupported claim rate、correct abstention；
- 结论稳定性与重复运行一致率；
- 工具参数合规率、越权尝试数；
- 采集请求数、持续时间、结果字节、Token 和延迟。

**评测分层**：

`L0 契约测试 -> L1 Evidence replay -> L2 VM smoke -> L3 多次运行的 release/holdout`

**讲解词**：

当前可以证明的是工程闭环和 Evidence 治理能力；正式端到端 AI 准确率需要冻结公开集和外部 Holdout，并进行多次重复运行。不能用少量开发场景直接宣称优于竞品。

---

### 第 13 页：当前成果、局限与下一步

**标题**：从可验证 MVP 走向可比较的 Agent Runtime

**已完成**

- Evidence-native Workspace 主路径；
- 原生采集到 Evidence Projection；
- Review/Exclude 失效传播；
- 分支隔离与持久化推理状态；
- 受控 Pi Runtime、Tool Gateway、generation fencing；
- 正确停止和 `INSUFFICIENT_EVIDENCE` 语义。

**仍需完善**

- 多支持集统一真值维护；
- 冲突字段的可比性判断与局部回溯；
- Source/MCP 与 Task Artifact 的统一 Ingestion contract；
- 更大规模、重复运行的真实 Holdout 评测；
- 有人工审批的有限恢复动作。

**结尾讲解词**：

Mini-Drop 的核心贡献不是让模型替代运维工程师，而是把“AI 选择证据、系统保证边界、人在环治理、结论可复查”做成一个可执行、可恢复、可评测的闭环。

---

## 备答页：高频追问口径

### 1. 你们的 AI 和规则到底是什么关系？

当前主线中，模型负责理解问题、维护假设和缺口、选择下一项注册 Collector、分析 Evidence 并提交结构化结论；规则和确定性代码负责权限、范围、预算、风险、状态、Projection、引用和提交验证。旧规则 RCA 保留为默认关闭的兼容/基线路径，不作为新 AI 主线。

### 2. 为什么不让模型直接执行命令？

因为命令执行无法稳定约束目标、参数、权限和影响范围。模型只能提出 CollectorProposal，Server 将其编译成经过 Schema、Scope、Capability、Risk、Budget 和 Fencing 检查的原生 Task。

### 3. Evidence 被人工排除后会怎样？

原始 Artifact 和历史 Review 不删除；新的 Review Revision 使依赖该 Evidence 的分析和结论变为 stale，Workspace 进入 `RECHECK_REQUIRED`，Agent 只能基于有效 Evidence 重新调查。

### 4. 证据不足时系统会不会硬给答案？

不会。`INSUFFICIENT_EVIDENCE` 是合法终态，必须说明缺失信息、已尝试范围和停止原因。它是安全能力，不是模型失败的隐藏包装。

### 5. Pi Session 挂了怎么办？

业务真源在数据库。系统通过 Outbox/Wakeup、Context Snapshot、runtime generation 和幂等键恢复；旧代次的迟到写入会被 fence，不能覆盖当前 Case。

### 6. 现在能不能说“准确率很高”或“优于竞品”？

不能直接这样说。当前可证明的是主链、治理和真实受控 Turn 已闭环。准确率、稳定性和竞品优势必须在固定工具/模型/预算的公开集与外部 Holdout 上多次运行后再声明。

## 绝对不要在答辩中说

- “系统已经实现通用自动修复。”
- “模型自己发现并执行了任意命令。”
- “规则 Top-N 就是 AI 的根因推理。”
- “所有 Collector 在任何 Linux 主机都可用。”
- “Pi Sidecar 存活就代表模型 Provider 一定可用。”
- “Evidence 排除等于物理删除历史证据。”
- “当前少量测试结果已经证明正式准确率或竞品领先。”

