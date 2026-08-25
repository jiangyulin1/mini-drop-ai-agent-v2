# Mini-Drop 答辩 PPT 候选模块与 5–8 页精简版

> 用途：从候选模块中选择最终答辩内容。  
> 设计原则：PPT 只放关键词、数字、状态和图；完整逻辑由演讲者讲解。  
> 建议正文：7 页；可根据答辩时长压缩到 5 页，或扩展到 8 页。  
> 当前口径基线：Evidence-native 受监督诊断 Agent。旧规则 RCA、通用自动修复和正式准确率不作为当前主线成果宣称。

> **v0.2 修订说明**：本版进一步强化复合 User Story、AI 总体能力架构、上下文裁剪/证据隔离设计，并把结果指标拆成“链路成功”“证据绑定”“人工质量估计”三种口径。文末“六、v0.2 修订版 7 页文字稿”优先级最高，可覆盖前面的旧版 7 页成稿。

## 一、候选模块库

### A. User Story 候选

User Story 的任务是让评委先理解“谁在什么场景下遇到什么问题”。一页只选一个故事，不要把所有用户都放进去。

#### A1：线上延迟事故排查（推荐）

**场景**：checkout 服务 P99 延迟升高，但指标只能看到现象。  
**用户**：值班 SRE。  
**触发**：`checkout latency increased in production`。  
**期望**：在不直接登录主机、不执行任意命令的情况下，获得能区分 CPU、IO、内存或下游连接问题的证据。

**适合强调**：AI 选择深度 Collector、Evidence Gap、受控采集。

**页面只放**：

```text
P99 ↑
现有监控只能看到“慢”
需要回答：为什么慢？
```

#### A2：已有 Task 交给 AI

**场景**：用户已经完成一次 `perf_cpu` 或 `sys_metrics` 采集，但不会解释产物。  
**触发**：点击“用该批次更新调查”或提交已有 Task。  
**期望**：AI 读取 Projection，指出支持信号、缺口和下一步，而不是重新采集一遍。

**适合强调**：原生采集链复用、Evidence 分析、避免重复采集。

**页面只放**：

```text
已有 Task
    ↓
AI 读 Evidence
    ↓
下一步 / 停止 / 拒答
```

#### A3：错误证据被发现后的复盘

**场景**：调查中发现某条日志时间窗错误或目标身份不匹配。  
**触发**：专家将 Evidence 标记为 `LOW_TRUST` 或 `EXCLUDED`。  
**期望**：依赖该 Evidence 的结论自动失效，系统从有效证据重新验证。

**适合强调**：Human-in-the-loop、可审计性、失效传播。

**页面只放**：

```text
错误证据
    ↓ 排除
旧结论失效
    ↓
重新验证
```

#### A4：两条调查分支比较

**场景**：两个工程师分别验证 CPU 假设和 IO 假设。  
**期望**：分支互不泄漏，公共证据可显式共享，最终比较调查轨迹。

**适合强调**：branch-local reasoning、盲隔离和公平评测。

**页面只放**：

```text
公共 Evidence
   ├─ Branch A：CPU
   └─ Branch B：IO
```

---

### B. Result 展示候选

Result 页不要展示“模型说了什么”作为唯一结果，要展示“系统留下了什么可验证状态”。

#### B1：Evidence 链路结果（推荐）

```text
Task → Artifact → Evidence → Projection → Claim
```

可展示的 4 个要点：

- Evidence ID
- 目标与时间窗
- Projection / hash
- Claim 的字段引用

**适合**：技术型评委，最能证明项目不是聊天 Demo。

#### B2：调查状态结果

```text
PARTIALLY_CONFIRMED
还有 1 个 Evidence Gap
下一步：perf_cpu
```

**适合**：突出系统不会过度自信，能把“不足”结构化表达。

#### B3：人工排除后的结果

```text
Evidence ev-17 → EXCLUDED
Analysis run-08 → STALE_INPUT
Workspace → RECHECK_REQUIRED
```

**适合**：突出证据治理和状态传播，是最有辨识度的结果展示。

#### B4：安全结果

```text
Model proposed: perf_cpu
Server decision: ACCEPTED
Reason: scope ✓ capability ✓ budget ✓ risk ✓
```

**适合**：安全、平台、工程治理方向答辩。

---

### C. Showcase 候选

Showcase 是现场演示或截图串联页，建议只选一条英雄路径。

#### C1：Evidence-native 英雄路径（推荐）

```text
创建 Case
 → 选择分支
 → 查看 Evidence Projection
 → 提交带引用结论
 → 排除支持证据
 → RECHECK_REQUIRED
```

**优点**：链路短，前后状态变化明显，适合 2–3 分钟现场演示。

#### C2：主动采集路径

```text
模糊问题
 → AI 识别缺口
 → 提议 Collector
 → Server 校验
 → Worker 执行
 → Evidence 回流
```

**优点**：最能体现“AI 做了什么”；缺点是现场依赖 Worker、Linux 工具和模型 Provider。

#### C3：故障治理路径

```text
结论形成
 → 人工排除 Evidence
 → 旧分析失效
 → 新一轮验证
```

**优点**：结果确定性强，适合无模型或 Provider 不稳定时演示。

---

### D. Ideal 候选

Ideal 页不是罗列未来功能，而是回答“一个理想的运维 Agent 应该是什么样”。

#### D1：可信闭环型（推荐）

```text
Observe → Propose → Gate → Collect → Verify → Explain → Recheck
```

理想特征：

- 能主动获取高区分度证据；
- 每个结论都可回到原始来源；
- 证据变化会触发重新验证；
- 证据不足时能够拒答；
- 人可以随时干预。

#### D2：自治等级型

```text
L0 只读回答
L1 受控采集
L2 低风险自动推进
L3 人工审批变更
L4 通用自治（当前不做）
```

**适合**：评委关心“AI 自动化程度”时使用。重点说明 Mini-Drop 当前主要在 L1，部分低风险流程具备 L2 形态，不宣称 L4。

#### D3：证据产品型

```text
Evidence 是资产
不是 Prompt 附件
```

下方放四个词：

```text
可追溯 | 可预览 | 可治理 | 可复用
```

**适合**：突出项目差异化和长期产品价值。

---

### E. 架构与优秀设计候选

架构页最多保留 3 个设计点，避免成为组件清单。

#### E1：双层智能（推荐）

```text
AI Plane：理解 / 规划 / 选择证据
                 ↓ Proposal
Deterministic Plane：授权 / 执行 / 验证 / 恢复
```

**优秀点**：把开放式推理和必须确定的安全、事实、状态分开。

#### E2：三对象证据模型

```text
Artifact = 原始产物
Evidence = 业务身份
Projection = 模型可见视图
```

**优秀点**：解决大结果、数据血缘、Prompt 预算和引用验证问题。

#### E3：Proposal 编译模型

```text
模型意图
  → CollectionProposal
  → Policy / Scope / Capability / Budget
  → CollectionRequest
  → native Task
```

**优秀点**：模型不直接创建 Task，所有动作都经过服务端编译和幂等持久化。

#### E4：数据库真源 + Wakeup 恢复

```text
DB/Event 是真源
Sidecar 是运行时
Outbox/Wakeup 负责续跑
```

**优秀点**：Server、Pi、Worker、Analyzer 重启后可重建上下文，晚到结果由 generation fencing 拒绝。

#### E5：Branch-local Investigation

```text
公共证据
  ├─ 分支 A：假设 A
  └─ 分支 B：假设 B
```

**优秀点**：减少探索路径互相污染，支持人工比较和更公平的 Agent 评测。

#### E6：Citation 与 stale 语义

```text
Claim
  → Evidence ID
  → Projection hash
  → field/span
```

证据被排除或 Projection 改变后：

```text
旧结论 ≠ 当前结论
```

**优秀点**：把“可解释”从文字风格变成系统状态约束。

#### E7：技术选型与 AI 需求一一对应（推荐作为讲解词，不要做组件清单）

| 技术选择 | 解决的 AI 工程问题 | 答辩表达 |
|---|---|---|
| Pi + Node.js Sidecar | 长周期 Agent loop、流式事件、steer/follow-up、凭据隔离 | “用成熟 Runtime 承担会话能力，把业务真源留在 Server” |
| 原生 Task / gRPC Worker | 复用 Linux Collector、任务取消、重试和 Result Spool | “AI 只做选择，不另造一套执行器” |
| Artifact + SQL CaseEvidence | 原始产物、业务身份、审计和版本化状态分离 | “证据进入数据库账本，不停留在聊天记录” |
| Projection + citation verifier | 大结果裁剪、Prompt 预算、字段级引用和不可信输入隔离 | “让模型看到最相关的事实，并且能证明引用位置” |
| Outbox + Wakeup + fencing | 异步任务完成、进程重启、迟到结果和幂等恢复 | “调查依赖持久化状态续跑，不依赖 Sidecar 内存” |

**一句话选型原则**：

> 模型相关能力交给可替换 Runtime；与事实、权限、执行和恢复有关的能力落在确定性服务端。

---

### F. 严重障碍 / 难点候选

建议只选一个作为“开发中遇到的严重障碍”，并完整讲清楚“症状—根因—方案—验证”。

#### F1：多条旧链路争夺 Case 主导权（最推荐）

**症状**：Pi Runtime、旧 Diagnosis、RulesOnlyReasoner、PlanDriver 和 Recovery 路径都可能推进同一个 Case，难以判断结论到底由谁产生。  
**根因**：多个时代的架构叠加，Collector、Task、Evidence 和诊断状态重复注册。  
**克服**：收敛到 Evidence-native 主线；以 Case/Evidence/Revision 为真源；旧 Diagnosis 默认关闭；Proposal → Request → Task 统一经过 CollectionSupervisor；保留可复用 parser、审计和 fence。  
**验证**：架构边界测试、legacy gate、展示英雄路径回归测试。

**页面只放**：

```text
多个主脑
   ↓ 统一真源
Evidence-native 主线
```

#### F2：原始 Collector 结果无法直接进入模型

**症状**：日志/火焰图/进程列表过大、结构不一，模型容易截断或无法引用。  
**根因**：把 Tool Result 当作上下文，而不是产品对象。  
**克服**：Artifact → deterministic parser → canonical Evidence → bounded Projection；固定字节预算和 hash；引用必须通过 verifier。  
**验证**：Projection、citation、EvidenceAnalysis fingerprint 和 stale 测试。

**页面只放**：

```text
Raw 太大、不可引用
        ↓
Projection + Citation + Hash
```

#### F3：Evidence 被人工纠正后，旧结论仍可能有效

**症状**：用户排除一条 Evidence 后，旧分析仍显示为当前结论。  
**根因**：审查只是 UI 标签，未进入推理状态和事务链。  
**克服**：Review Revision 作为输入版本；同事务标记关联 Analysis stale；Workspace 显示 `RECHECK_REQUIRED`；重新调查使用有效祖先和新 generation。  
**验证**：Review/Exclude、stale propagation、revalidation 回归测试。

**页面只放**：

```text
Review 改变
 → stale
 → recheck
```

#### F4：异步系统的迟到结果覆盖当前状态

**症状**：Worker、Analyzer 或 Sidecar 重启后，旧请求仍可能写回新调查。  
**根因**：只依赖进程内状态，没有对 Case、范围、计划、运行代次做 fencing。  
**克服**：持久化 revision、generation、幂等键；Outbox 派发；提交前二次校验；旧代次只能保留历史 Artifact，不能覆盖 current。  
**验证**：late-result、ACK 重放、重启恢复和 generation fence 测试。

#### F5：模型 Provider 不稳定，无法保证现场演示

**症状**：Sidecar 存活但 Provider completion 失败；演示链路被外部 API 阻断。  
**根因**：运行时健康与模型可用性混为一谈。  
**克服**：Provider completion smoke test；Pi / deterministic 双模式；模型不可用时明确 fail closed；准备无模型的 Evidence 治理演示。  
**验证**：Provider failure、deterministic fallback、真实 Pi Turn 验收。

#### F6：无法诚实证明 AI 比规则或竞品好

**症状**：改变策略标签不一定改变真实决策，少量开发案例无法支持准确率结论。  
**根因**：评测测到了报告格式或规则结果，没有隔离 Collector 选择和 Evidence 获取能力。  
**克服**：固定模型、Prompt、Catalog、预算、故障注入和停止条件；加入 rules-only、Evidence replay、VM smoke、external holdout；测证据覆盖、引用、拒答、稳定性和成本。  
**验证**：run manifest、hash、scorer 和多次运行记录。

---

### G. 结尾 / 价值主张候选

#### G1：工程闭环型（推荐）

```text
AI 选择证据
系统保证边界
人在环治理
结论可复查
```

#### G2：差异化型

```text
不是更会猜
而是更会证明
```

讲解时补充：证明不是数学证明，而是 Evidence、引用、版本和审计链的可复核。

#### G3：产品演进型

```text
从一次性回答
到可恢复调查运行时
```

---

## 二、5–8 页组合方案

### 方案 P5：5 页极简版（约 5 分钟）

适合时间极短、只允许讲清楚项目主线的场合。

| 页 | 内容 | 图形主轴 |
|---|---|---|
| 1 | User Story + 痛点 | 现象 → 证据缺口 |
| 2 | Showcase / Result | Case → Evidence → Conclusion |
| 3 | Architecture + 优秀设计 | AI Plane ↔ Deterministic Plane |
| 4 | 严重障碍与克服 | 多主线 → 单一 Evidence-native 主线 |
| 5 | Ideal + 结论 | Observe → Verify → Recheck |

**取舍**：不单独讲评测，只在第 5 页口头补充“通过测试和真实受控 Turn 验收”。

### 方案 P6：6 页平衡版（约 6–7 分钟）

| 页 | 内容 | 图形主轴 |
|---|---|---|
| 1 | User Story | SRE 面对 P99 延迟 |
| 2 | Result | Artifact → Evidence → Projection → Claim |
| 3 | Showcase | 排除 Evidence → RECHECK_REQUIRED |
| 4 | Architecture + 3 个优秀设计 | Proposal / Gateway / Supervisor |
| 5 | 严重障碍与克服 | 旧链路收敛 |
| 6 | Ideal + 当前边界 | 可验证、可恢复、可拒答 |

**取舍**：评测放到口头问答或备份页。

### 方案 P7：7 页推荐版（约 8–10 分钟）

| 页 | 内容 | 图形主轴 |
|---|---|---|
| 1 | User Story | “服务变慢，但为什么？” |
| 2 | Result | 证据链和状态结果 |
| 3 | Showcase | 一条英雄路径 |
| 4 | Ideal | 可信调查闭环 |
| 5 | Architecture + 优秀设计 | 双层智能 + Evidence 三对象 |
| 6 | 严重障碍与克服 | 多主脑/多链路收敛 |
| 7 | 验收、边界与结论 | 已完成 / 未宣称 / 下一步 |

**推荐理由**：完整覆盖用户要求的六个主题，同时保留一页收束事实和边界，避免只讲设计、不讲落地。

### 方案 P8：8 页完整版（约 10–12 分钟）

| 页 | 内容 | 图形主轴 |
|---|---|---|
| 1 | User Story + 痛点 | 现象层 / 机制层 |
| 2 | Result | Evidence 结果卡 |
| 3 | Showcase | 现场操作链 |
| 4 | Ideal | 理想 Agent 闭环 |
| 5 | Architecture | 组件和数据流 |
| 6 | 优秀设计 | 3 个关键不变量 |
| 7 | 严重障碍与克服 | 选择一个深讲 |
| 8 | 评测、边界、结论 | 证据化验收 |

**适合**：评委技术背景较强，且允许演示或追问较多的场合。

---

## 三、推荐 7 页低文字量成稿

下面是一版可以直接交给 PPT 制作者的页面脚本。页面文字只保留“标题 + 关键词 + 状态”，其余放在演讲者备注。

### 第 1 页：User Story

**屏幕文字**：

```text
服务变慢了
但为什么？

指标看到现象
进程内部缺证据
```

**画面**：左侧 P99 延迟上升，右侧四个待区分方向：CPU / IO / Memory / Dependency。

**演讲者讲解**：

值班 SRE 已经知道服务变慢，但现有监控只说明“慢”，无法说明是 CPU 饱和、IO 阻塞、内存压力还是下游连接问题。Mini-Drop 的任务不是直接猜一个答案，而是决定下一步应该获取哪种证据。

**转场句**：

> 所以我们把问题从“生成答案”改成“补齐证据”。

### 第 2 页：Result

**屏幕文字**：

```text
Task → Artifact → Evidence → Projection → Claim
```

**画面**：一条横向链路；下方只标 4 个小标签：`ID`、`Hash`、`Time window`、`Citation`。

**演讲者讲解**：

采集结果首先保存为原始 Artifact，再生成有稳定身份的 CaseEvidence 和有界 Projection。模型和 UI 读取的是 Projection；最终 Claim 必须绑定 Evidence ID、Projection hash 以及具体字段或文本位置。这样结果不仅能看，还能复查。

### 第 3 页：Showcase

**屏幕文字**：

```text
创建 Case
 → 分支调查
 → 查看 Evidence
 → 提交结论
 → 排除证据
 → RECHECK_REQUIRED
```

**画面**：建议使用 3 张实际 UI 截图或一张连续工作台截图，用箭头连接；不要放大段说明。

**演讲者讲解**：

先在一个隔离分支查看 Evidence Projection，提交一个带引用的部分确认结论。随后人工排除一条支持证据，系统不删除历史，也不继续把旧结论当成 current，而是将关联分析标为 stale，工作区进入 RECHECK_REQUIRED，等待重新验证。

**现场演示优先顺序**：

1. 展示 Evidence ID、目标和时间窗；
2. 展开 Projection 和引用字段；
3. 执行 Exclude；
4. 展示 stale / revalidation 状态。

### 第 4 页：Ideal

**屏幕文字**：

```text
Observe
   ↓
Propose → Gate → Collect
   ↓
Verify → Explain → Recheck
```

**画面**：圆环或闭环流程；在 `Gate` 下方标 `scope / risk / budget`，在 `Verify` 下方标 `citation / revision`。

**演讲者讲解**：

理想的运维 Agent 不是永远给出答案，而是能观察、提出下一步、接受确定性门禁、采集、验证、解释，并在证据发生变化后重新检查。证据不足时停止或拒答，也是闭环的一部分。

### 第 5 页：Architecture + 优秀设计

**屏幕文字**：

```text
AI：理解 / 规划 / 选择证据
             ↓ Proposal
系统：授权 / 执行 / 固化 / 验证
```

**画面**：上下两层架构，中间只保留 `Tool Gateway`、`CollectionSupervisor`、`Evidence Store` 三个节点。

**演讲者讲解**：

这是系统最重要的设计：模型负责开放式决策，但不拥有基础设施权限。Tool Gateway 重新校验调用，Supervisor 把 Proposal 编译为原生 Task，Worker 执行已注册 Collector，Analyzer 生成 Projection，Verifier 决定结论能否提交。Pi Sidecar 是运行时，不是业务真源。

**本页口头补充的优秀设计**：

- Artifact / Evidence / Projection 三对象分离；
- 数据库真源 + Outbox/Wakeup 恢复；
- branch-local 状态和 generation fencing。

### 第 6 页：严重障碍与克服

**推荐选择 F1：多条旧链路争夺 Case 主导权**

**屏幕文字**：

```text
多个主脑
Pi / Diagnosis / Rules / Recovery
              ↓ 收敛
Evidence-native 主线
```

右下角只放：

```text
统一真源：Case / Evidence / Revision
```

**画面**：左侧多个入口汇入“谁在决定？”问号；右侧单一主线。

**演讲者讲解**：

开发中最严重的问题不是某个模型 API 调不通，而是系统同时存在 Pi、旧 Diagnosis、规则推理、计划驱动和恢复路径，导致同一个 Case 可能有多个“主脑”，也无法清楚归因。解决方案不是继续叠加逻辑，而是收敛产品主线：Case、Evidence、Revision 作为唯一业务真源；新采集统一走 Proposal → Request → Task；旧 Diagnosis 默认冻结；可复用的采集器、解析器、审计和 fence 继续迁移保留。

**验证口径**：

```text
legacy gate
architecture tests
showcase regression
```

### 第 7 页：验收与结论

**屏幕文字**：

```text
已实现：Evidence / Review / Branch / Fence / Pi MVP

不宣称：通用自动修复 / 正式准确率 / 全量生产自治

AI 选择证据
系统保证边界
人在环治理
结论可复查
```

**画面**：左侧“已实现”绿色状态，右侧“不宣称”灰色边界，底部四句价值主张。

**演讲者讲解**：

当前已经能够证明的是 Evidence-native 主路径、受控采集、证据治理、分支隔离、可恢复运行时和真实受控 Pi Turn。仍未把通用自动修复或正式 AI 准确率作为成果宣称。项目的核心贡献是把 AI 的开放式推理放在确定性系统边界内，形成一个可执行、可恢复、可审计、可评测的调查闭环。

**结束句**：

> Mini-Drop 不是让 AI 更大胆地执行，而是让 AI 更有依据地行动，也更有依据地停止。

---

## 四、严重障碍的选择建议

按答辩方向选择，不建议同时讲两个障碍。

| 评委关注点 | 优先选项 | 原因 |
|---|---|---|
| 系统架构、工程能力 | F1 多主脑收敛 | 能体现架构治理和产品取舍 |
| AI 工程、上下文设计 | F2 Projection | 能体现大数据、Prompt 和引用设计 |
| 可信 AI、人在环 | F3 Evidence 失效 | 能体现结论不是一次性文本 |
| 分布式系统 | F4 迟到结果 fencing | 能体现异步恢复和并发控制 |
| 现场演示稳定性 | F5 Provider 不稳定 | 能体现 fail-closed 和双运行时 |
| 算法评测、科研性 | F6 评测失真 | 能体现公平对照和实验严谨性 |

## 五、PPT 减字规则

1. 每页只回答一个问题：用户为什么需要、系统做了什么、结果如何可信、难点如何解决。
2. 每页屏幕文字控制在 15–35 个汉字或 5–8 个关键词以内。
3. 架构页只保留 5–7 个节点，其他组件放讲稿或备份页。
4. 结果页优先展示状态变化、ID、hash、引用和 UI 截图，不展示长 JSON。
5. 现场演示只展示一条路径，不同时展示长期目标、恢复动作、MCP 和竞品。
6. 所有“已实现”内容旁边准备一个代码、测试、API 或运行记录作为证据。
7. 所有“理想/未来”内容使用虚线、灰色或 `Next` 标识，不与当前成果混在一起。

## 六、v0.2 修订版 7 页文字稿

这一版按“一个复杂事故贯穿全场”的方式组织。它不是把功能逐页罗列，而是让同一个 User Story 依次经过 AI 理解、上下文治理、证据获取、隔离调查、人工纠正和结果验证。

### 贯穿全场的复合 User Story

**演示设定**：

> 生产环境的 `checkout` 服务从上午开始出现间歇性超时。首页仍然正常，但购物车和支付链路受影响；部分实例 CPU 偏高，同机邻居也有波动；日志同时出现连接等待和请求堆积。值班 SRE 希望“自动定位，但不做生产修改”。

这个场景故意包含多个可能方向：CPU 噪声、运行时等待、下游连接、局部实例问题和时间窗错位。它能一次展示以下 AI 设计：

```text
自然语言意图
 → 服务/环境/时间窗聚焦
 → 上下文裁剪与证据隔离
 → 竞争假设与 Evidence Gap
 → 主动选择 Collector
 → 证据治理与重新验证
 → 部分确认或正确拒答
```

**重要口径**：这是答辩演示用的复合场景。除非现场运行记录明确支持，不要说“本次真实生产事故就是这个根因”；应说“系统用这个场景展示如何处理多个竞争解释”。

### 第 1 页：复合 User Story

**屏幕文字**：

```text
checkout 间歇性超时
首页正常，支付链路受影响

CPU？Runtime？下游？
```

**画面建议**：

- 左侧：服务链路 `frontend → checkout → payment / redis`；
- 中间：P99、timeout、实例局部异常三个小信号；
- 右侧：四个待区分方向 `CPU / Runtime / IO / Dependency`；
- 右下角小字：`只读调查 · 不做生产修改`。

**演讲者文字稿**：

我们从一个值班 SRE 的真实工作问题开始：服务不是完全不可用，而是局部、间歇性变慢。现有监控能告诉我们“发生了超时”，但不能直接说明是 CPU 压力、运行时锁等待、IO 阻塞、下游依赖，还是某个实例身份和时间窗不一致。用户还明确要求只读调查，不允许生产修改。这个约束会一路传递到后面的 RuntimePolicy 和 Tool Gateway。

**本页要讲出的价值**：

```text
不是把一个问题丢给模型
而是把一个含噪、含冲突、带安全约束的问题
转化为可验证的信息目标
```

### 第 2 页：AI 如何处理复杂上下文

**屏幕文字**：

```text
原始世界：大、杂、会变化
        ↓
Task-specific Snapshot
        ↓
目标 / 时间窗 / 缺口 / 高价值 Evidence
```

**画面建议**：左侧堆叠 raw logs、metrics、profiles、topology；中央漏斗写 `Projection / Budget / Redaction`；右侧显示模型只看到的 Context Snapshot。旁边用两条虚线标出 `Branch A`、`Branch B`，说明分支默认不互相读取。

**演讲者文字稿**：

复杂调查的第一个难点不是模型不会推理，而是模型看到的东西太多、太杂、太容易过期。我们不把原始日志和整份 profile 直接塞进 Prompt，而是先根据当前目标生成有界 Projection，再构造本轮 Task-specific Snapshot。Snapshot 优先保留目标身份、时间窗、当前缺口、关键指标、Evidence hash 和正在执行的任务；低价值重复字段、无关历史和超预算内容被裁剪，但原始 Artifact 仍可追溯和下载。

这里的“提高模型注意力”应准确表述为：**通过确定性上下文治理降低噪声、提高有效信息密度和引用稳定性**。目前不要宣称已经用独立实验测得“注意力提升百分比”。

同时，每条调查分支只看到公共初始 Evidence 和自己的新证据。Branch A 验证 CPU，Branch B 验证下游依赖；一个分支的早期猜测不会自动污染另一个分支。

**本页只保留三个设计词**：

```text
裁剪（Compaction） | 隔离（Isolation） | 版本（Revision）
```

### 第 3 页：AI 调查与主动采集

**屏幕文字**：

```text
Hypothesis
    ↓
Evidence Gap
    ↓
Collector Proposal
    ↓
Evidence / Stop / Abstain
```

**画面建议**：做成两轮循环。第一轮已有 `sys_metrics` 和日志摘要，但不能区分 CPU 噪声与 runtime stall；第二轮模型提出 `runtime_snapshot` 或 `connection_probe`，而不是自由生成 Shell。

**演讲者文字稿**：

AI 这一轮不需要一次性回答根因。它先维护竞争假设：CPU 压力、运行时等待、下游连接分别由哪些事实支持，又缺什么事实。然后选择一个最能区分当前假设的已注册 Collector。比如，CPU 指标只能说明某个实例忙，不能说明请求是否卡在锁或下游连接上；这时更有价值的下一步可能是运行时快照或连接探测，而不是再次采集同一类 CPU 数据。

模型只生成结构化 `CollectorProposal`。服务端检查目标、Worker 能力、参数 Schema、风险、预算、审批、幂等键和当前 revision，之后才编译成原生 `Task`。如果证据仍不足，Agent 可以继续提出低风险补证，也可以明确提交 `INSUFFICIENT_EVIDENCE`，而不是为了给出答案强行闭合根因。

### 第 4 页：Showcase 全展示

**屏幕文字**：

```text
采集 → 证据 → 分支 → 结论
              ↓
        人工排除 / 重新验证
```

**画面建议**：使用 3–4 张实际界面截图或一张连续 Workspace 截图，展示状态变化，不要在 PPT 上放长 JSON。

**展示顺序与演讲文字稿**：

1. **创建 Case**：展示服务、环境、目标进程和只读策略。
2. **创建隔离分支**：说明 Branch A 和 Branch B 的 Evidence 可见性不同。
3. **查看 Projection**：展示 Evidence ID、目标、时间窗、Projection hash 和字段摘要。
4. **提交结论**：结论绑定 Evidence 引用；如果仍有缺口，状态可以是 `PARTIALLY_CONFIRMED`。
5. **人工排除证据**：把一条误导性 CPU Evidence 标记为 `EXCLUDED` 或 `LOW_TRUST`。
6. **观察状态传播**：关联 Analysis 变为 `STALE_INPUT`，Workspace 进入 `RECHECK_REQUIRED`。
7. **重新验证**：历史结论仍可审计，但不能继续作为当前结论；Agent 从有效证据重新调查。

**这一页的结束句**：

> Showcase 展示的不是一段漂亮的模型回答，而是证据进入、结论形成、人工纠正和旧结论失效的完整状态变化。

### 第 5 页：AI 功能总体架构

**屏幕文字**：

```text
Interaction & Intent
        ↓
Context / Attention Governance
        ↓
Reasoning & Investigation Planning
        ↓
Evidence Acquisition / Analysis
        ↓
Human Review + Verifier
```

**画面建议**：从上到下画 5 个 AI 能力层，右侧接一个贯穿全层的确定性 Control Plane：`Scope / Policy / Budget / Revision / Audit`。底部接 Worker、Analyzer、Evidence Store。

**演讲者文字稿**：

从 AI 功能角度看，系统不是一个单独的 Chat Completion，而是五个相互衔接的能力层：第一，理解用户意图并聚焦服务、环境、进程和时间窗；第二，针对当前任务裁剪上下文并隔离分支证据；第三，维护竞争假设、缺口和调查计划；第四，选择采集能力并分析 Evidence；第五，在人工 Review 和确定性 Verifier 之后提交结论或拒答。

这些 AI 能力都运行在确定性控制面的约束内。控制面不负责替模型猜根因，但负责身份、范围、权限、风险、预算、版本、证据生命周期、引用校验和最终状态提交。可以概括为：**AI 负责探索，系统负责证明边界**。

**架构页建议只强调三个优秀设计**：

1. `Context Snapshot`：每轮重新构造当前世界，不依赖 Sidecar 内存；
2. `Artifact / Evidence / Projection`：原始产物、业务证据和模型视图三者分离；
3. `Branch + Revision + Fencing`：证据隔离、状态可回退、迟到写入不可覆盖当前结论。

**技术选型的口头收束**：

> Pi 解决长周期 Agent loop，原生 Task/gRPC 解决 Linux 深度采集，SQL/Outbox 解决可恢复业务状态，Projection 解决上下文预算和引用验证。每一项选型都对应一个 AI 落地问题，而不是为了堆组件。

### 第 6 页：严重障碍与克服方案

**推荐障碍主题**：上下文与证据的双重失真

**屏幕文字**：

```text
太多 → 看不见重点
串线 → 分不清来源
过时 → 旧结果覆盖新状态
          ↓
Projection + Isolation + Fencing
```

**画面建议**：左侧是一团混杂的 raw context；中间依次经过 `bounded projection`、`branch visibility`、`revision fence`；右侧是可验证的当前结论。

**演讲者文字稿**：

开发中最难、也最值得讲的障碍，不是把模型 API 接通，而是长周期异步调查中的“上下文与证据双重失真”。第一种失真是上下文过载：原始 Artifact 太大，模型容易忽略真正区分假设的字段。第二种失真是证据串线：不同分支的探索结果、不同时间窗的数据或已被人工排除的 Evidence 可能混在一起。第三种失真是状态过时：Worker、Analyzer 或 Sidecar 重启后，旧代次的结果可能晚到并覆盖新结论。

我们用三层机制克服它：

```text
1. Projection：确定性裁剪，保留 hash、身份、时间窗和高价值字段；
2. Isolation：branch-local 可见性，跨分支共享必须显式授权；
3. Fencing：revision / generation / idempotency，旧结果只能保留历史，不能写入 current。
```

这三个机制共同把“模型记得什么”转化为“系统允许它看到什么、引用什么、写回什么”。Review/Exclude 还会触发 stale propagation 和新一代 revalidation，因此人工纠正不是 UI 标签，而是进入推理状态的确定性事件。

**如果评委更关注架构治理，可替换为 F1 主题**：多个 Pi、旧 Diagnosis、RulesOnly、PlanDriver 和 Recovery 路径曾经可能推进同一个 Case。克服方式是统一 Case/Evidence/Revision 真源、冻结旧在线入口、让新采集统一走 Proposal → Request → Task。两种障碍可以共用同一套“上下文—证据—状态”解决框架。

### 第 7 页：结果、成功率、边界与价值

**屏幕文字**：

```text
27/27  Provider + Runtime 结构闭环
27/27  Evidence ID / Hash 完整引用
9.2–9.6/10  非双盲公开 PR 人工粗评

不是通用 RCA 准确率
```

页脚小字可放：

```text
Pi/Sidecar · Native Task · SQL/Outbox · Evidence Ledger
```

**画面建议**：上半部放三个大数字，下半部用三条状态横条：`链路成功`、`证据可信`、`质量信号`。右下角灰色边界：`非双盲 / 非生产 telemetry / 不含自动修复`。

**演讲者文字稿**：

结果必须区分三种口径。

第一，工程链路层面，Evidence-native 9×3 矩阵使用真实 DeepSeek Provider 完成了 9 个案例、每个 3 轮、共 27 轮；`27/27` 完成并通过结构门禁。这证明 Runtime、只读策略、工具审计、Evidence 绑定和多轮重复链路是通的。

第二，证据绑定层面，第一轮发现只有 `8/27` 轮完整写出三份 canonical Evidence ID，之后收紧答案合同重新运行，达到 `27/27` 轮完整绑定 Evidence ID 和 Projection hash。这说明我们不仅测模型，也会把发现的引用缺口变成可验证的工程修复。

第三，质量信号层面，在给定公开 PR 和 Projection 的非双盲人工/Oracle 评估中，综合粗评约 `9.2–9.6/10`，约 `9.3/10`。它反映的是机制归因、Evidence 引用、反证/不确定性和影响边界，不是通用生产 RCA 准确率。

从“解决问题的能力”看，当前结果已经覆盖四个层次：能否完成受控调查链路，能否把结果绑定到正确 Evidence，能否处理反证和不确定性，能否在证据不足时停止。技术选型的价值也在这里体现：Pi 提供持续调查运行时，原生 Task 复用真实 Linux 采集，SQL/Outbox 保证异步恢复，Evidence Ledger 和 Projection 让结果可审计、可回放。

因此，本项目当前能诚实宣称的是：**完成了一个可运行、可审计、可纠正、可评测的 Evidence-native AI 调查闭环**。不能宣称通用自动修复、正式盲测准确率或生产自治。

**结束句**：

> Mini-Drop 的价值不是让模型更大胆地猜，而是让它在更少、更相关、彼此隔离且可验证的上下文中行动；当证据不足或被推翻时，它也能有依据地停止和重新调查。

## 七、结果数字的答辩口径表

| 数字 | 可以说明什么 | 不能说明什么 |
|---|---|---|
| `27/27 completed` | 9×3 公开 PR Evidence-native 链路、Provider、Runtime 和结构门禁闭环 | 不能说明通用根因准确率或生产自治 |
| `27/27 citation-complete` | 收紧答案合同后，三份 canonical Evidence ID/hash 均被完整引用 | 不能说明每个机制判断都正确 |
| `9.2–9.6/10` | 非双盲公开 PR Projection 条件下的人工质量估计 | 不能当作 92–96% RCA accuracy |
| `75/80` | 2026-08-21 8 个 PR 单轮人工评分的历史结果 | 不能与 9×3 结果直接拼成一个成功率 |
| `56.7%` | 旧三节点 30 案例单轮严格命中基线，可用于解释评测难点和历史瓶颈 | 不能说成当前 Evidence-native AI 成功率 |
| `36.7%` | 旧 30×3 回放重复一致率，说明环境/诊断稳定性曾是瓶颈 | 不能直接代表当前 9×3 Evidence-native 稳定性 |

**建议答辩现场只主动展示前三个数字**；后面三个数字放备答页，只有评委追问“准确率/历史对照”时再解释。

## 八、用户可能追问“裁剪上下文真的提高注意力吗？”

推荐回答：

> 我们把它定义为上下文治理能力，而不是未经实验验证的心理学结论。确定性 Projection 会裁剪无关字段、限制字节预算、保留目标/时间窗/缺口/高价值 Evidence，并保留 raw locator 供追溯。它的直接可验证收益是上下文大小受控、字段引用稳定、Evidence hash 可复核；“模型注意力提高”是设计目标，正式因果收益还需要在同一 Case、同一模型和同一预算下做 paired ablation。

## 九、用户可能追问“隔离证据的价值是什么？”

推荐回答：

> 隔离不是为了让模型少看信息，而是为了让每条推理的来源可解释。两个分支可以分别验证 CPU 和下游假设，默认只看到公共 Evidence 与本分支结果；如果要共享，必须校验目标身份、时间窗、Collector 版本、Review 状态和 Projection hash，并创建新的 revision。这样我们既能防止早期猜测互相污染，也能把 Agent 的探索路径做成公平评测对象。
