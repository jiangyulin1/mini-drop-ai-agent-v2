# Mini-Drop Evidence-native Investigation Runtime

> 状态：当前产品定位与后端设计原则
> 固化日期：2026-08-24
> 适用范围：AI 采集、证据治理、假设推进、回滚、共享和调查恢复

## 1. 一句话定位

**Mini-Drop 是一个以 Evidence 生命周期为真值的动态调查运行时。**

它让用户和 AI 在受控范围内共同获取、审核、组合、撤销和重新验证证据，并支持多个调查分支在互不可见的条件下独立探索。系统不把 AI 的一次回答当成结论，而把结论视为一组当前仍有效的证据、依赖关系和推理代际的结果。

答辩时可以这样描述：

> Mini-Drop 面向受限主机和明确 Case scope，允许用户或 AI 根据待验证假设提出采集。每个调查分支拥有隔离的 Evidence Workspace，不能读取其他分支的证据、假设或工具结果；系统在服务端维护全局 lineage，用于审核和失效传播，但不会把它直接注入 Agent 上下文。用户可以排除证据或否定假设；系统会只使受影响的推理分支失效，保留原始记录，从有效祖先节点创建新一代调查并继续探索。证据不足时，系统明确拒答，而不是强行生成根因。

## 2. 设计主张

### 2.1 Evidence 是真值，全局图是治理投影

“证据链”有两个不同层次，必须分开：

- **Branch-local Evidence Workspace**：单个 Agent 实际能看到和使用的证据集合；默认只包含公共初始证据和本分支自产生的证据。
- **Global Evidence Lineage**：服务端用于审计、引用验证和失效传播的全局依赖图；默认不暴露给任何分支。

全局 lineage 可以表示为：

```text
Case / Goal
    |
    +--> EvidenceSet / Projection
              |
              +--> Claim（事实解释）
              |       |
              |       +--> Hypothesis
              |                  |
              |                  +--> Gap / 下一信息目标
              |                             |
              |                             +--> CollectionProposal
              |
              +--> 另一条 Hypothesis（可共享经过授权的 Evidence）
```

全局 lineage 是治理和回放投影，不是 Agent 的共享黑板。Evidence、Review、Projection、分支可见性和引用状态共同决定当前分支能使用什么。

### 2.2 分支默认盲隔离

每个独立探索分支都必须绑定一个 `BranchWorkspace` 和可见性策略：

- 分支只能读取 Case 在分叉时明确冻结的公共初始 Evidence；
- 分支不能读取其他分支的 Evidence、Hypothesis、Gap、Tool Call、分析文本或结论；
- 分支不能通过列表、计数、名称、错误消息或推荐结果推断其他分支的调查内容；
- Collector 结果首先进入产生它的分支，不自动提升为 Case-wide Evidence；
- Agent Prompt、Tool Catalog 和 Projection API 都必须执行 branch visibility fence。

这样可以让多个 Agent 在相同初始问题上进行真正独立的探索，避免一个 Agent 的早期猜测污染其他 Agent 的上下文。

### 2.3 Evidence 不可变，排除是语义撤销

用户所谓“删除证据”在产品中实现为治理操作，而不是物理删除：

- 原始 Artifact、Evidence 身份和审计事件保留；
- 当前 Evidence lifecycle 变为 `EXCLUDED`、`LOW_TRUST` 或 `RESTORED`；
- 依赖它的 Claim、Hypothesis、Causal Edge 和 Conclusion 被标记为 `STALE`、`INVALIDATED` 或 `RECHECK_REQUIRED`；
- 旧结果只用于审计和 replay，不再进入当前 Evidence Projection；
- 新一代调查从仍然有效的祖先或 frontier 继续。

因此，“后续证据保留”与“后续推理继续有效”是两个不同判断。原始数据可以保留，但失去前提的派生结论不能自动复用。

### 2.4 回滚是局部分支操作

冲突出现时，系统按依赖关系计算最小影响范围：

1. 定位冲突 Evidence 或矛盾 Claim；
2. 找到依赖它的节点和后代；
3. 使这些节点失效或放弃；
4. 保留不依赖冲突节点的旁支；
5. 从最近有效祖先重新打开 sibling frontier；
6. 创建新的 cycle 和 `runtime_generation`；
7. 拒绝旧 generation 的迟到写入。

只有共享根证据失效时，多个旁支才会同时受到影响。系统不应因为一个局部冲突而删除整个 Case 的历史。

“冲突”也不能只由模型自行判定。服务端至少要先比较 target identity、观测时间窗、字段语义、Producer 版本和 Evidence trust：不同时间窗、不同实例或不同指标定义的差异不应直接触发回滚；只有在可比范围内的不可兼容观测，才升级为 `CONFLICT_REQUIRES_RECHECK`，再决定回到哪个祖先节点。

### 2.5 共享不是默认能力

Evidence 默认只属于产生它的调查作用域。独立探索阶段禁止跨分支共享。只有用户或治理服务显式发起共享流程后，其他分支才可能获得内容，而且必须创建新的 workspace revision。共享前可以只返回盲化结果，例如“该条件是否被满足”或“是否存在矛盾”，不直接暴露原始 Evidence，以减少上下文污染。

若确实需要共享原始 Evidence，以下条件必须全部满足：

- target identity / resource incarnation 匹配；
- 时间窗和 scope revision 匹配；
- CollectorSpec、参数和版本匹配；
- Evidence review state 允许使用；
- Projection hash 和引用路径可验证；
- 当前分支显式选择了该 Evidence。

共享记录必须保存为可撤销的 `EvidenceReuseDecision` 或等价授权，并记录接收分支和新 workspace revision；不能由相同 PID、Collector 名称或相似文本隐式推断。

### 2.6 采集事实和推理前提必须分离

父假设失效，不代表在该假设下采集到的原始事实就是假的。系统必须区分：

- **Evidence validity**：原始观测本身是否完整、可信、未被排除；
- **Provenance validity**：该观测是否仍然由当前有效的调查路径支持；
- **Inference validity**：基于该观测形成的 Claim 或 Conclusion 是否仍然成立。

因此，回滚时：

- 原始 Artifact 和 Evidence 默认保留；
- 依赖失效父假设的 Claim、Conclusion 和 Collection rationale 失效；
- 后续采集结果保留在原分支，但标记为 `COLLECTED_UNDER_INVALIDATED_PATH`；
- 新分支不能自动看到这些结果，除非重新通过 Evidence review、scope 和 explicit reuse 校验；
- 重新纳入时，必须以新的 Claim 或新的采集理由建立关系，不能恢复旧的推理边。

这保证了“后续证据保留”不会变成“旧结论偷偷复活”。

### 2.7 分支不能自动合并

独立分支的结果默认不会自动合并为 Case-wide truth。合并必须经过一个显式的 promote/review 操作：

1. 用户或确定性 Verifier 选择要提升的 Claim/Evidence；
2. 校验引用、Review、scope、时间窗和冲突；
3. 创建新的 Case baseline revision；
4. 其他分支只在新 revision 创建后，按可见性策略获得共享内容。

这样“独立探索”和“最终汇总”是两个不同阶段，避免分支之间在研究过程中互相污染。

## 3. AI 与确定性系统的边界

### AI 负责

- 维护竞争假设和反证；
- 识别阻塞当前决策的 Evidence Gap；
- 选择下一项已注册采集能力；
- 解释 Evidence 之间的支持、矛盾和因果关系；
- 提出继续、暂停、回滚、拒答或请求用户审核。

### 服务端负责

- 身份、租户、Case scope、权限和预算；
- CollectionProposal → CollectionRequest → Task 的编译；
- Evidence 生命周期和不可变 Projection；
- 引用、共享、revision 和 generation 校验；
- 失效传播、分支重开和迟到结果 fencing；
- 结论提交、审批和审计。

AI 可以提出回滚，但不能直接删除 Evidence、创建未注册 Task、绕过审批或覆盖历史结论。

## 4. 统一运行链路

```text
User goal / alert / correction
        |
        v
Case Snapshot + BranchWorkspace @ revisions
        |
        v
Pi Runtime：只读取本分支假设、反证、Gap 和 Evidence
        |
        v
Tool Gateway + CollectionSupervisor
  scope | risk | budget | capability | idempotency | generation
        |
        v
CollectionProposal -> CollectionRequest -> Task / Outbox
        |
        v
Worker Collector -> Artifact -> CaseEvidence -> immutable Projection
        |
        v
Evidence Analysis -> branch-local Claim / Hypothesis / Causal Graph / Conclusion
        |
        +--> User review / exclude / correction
        |       |
        |       +--> stale propagation -> new cycle -> new generation
        |
        +--> explicit insufficient evidence / case close
```

每个 Agent cycle 只能提交一个可解释的信息决策。Sidecar 内存不是恢复依据；恢复必须依赖数据库状态、Outbox 和 Wakeup。Global lineage 只能由服务端治理流程读取，不能直接作为 Agent 的公共上下文。

## 5. 状态模型

### Evidence

`CAPTURED → REVIEWED → ACTIVE | LOW_TRUST | EXCLUDED → RESTORED`

Evidence 的身份、原始内容 hash、producer、target、时间窗和 Projection 版本不可变；Review 和 Projection 变化会使引用它的 AnalysisRun 变为 `STALE_INPUT`。

### 推理节点

`OPEN → SUPPORTED | RULED_OUT | WAITING_EVIDENCE → INVALIDATED | ABANDONED | CLOSED`

- `INVALIDATED`：自身依据失效；
- `ABANDONED`：因祖先失效而不能继续；
- `RULED_OUT`：仍然有效，但已被证据反驳；
- `STALE`：历史分析曾完成，但输入已不再是当前有效输入。

### 调查代际

每次 scope、review、projection、workspace visibility 或用户纠正改变当前事实边界时，创建新的 cycle / generation。旧 generation 可读、可审计、可 replay，但不能写入当前分支。

## 6. 与普通 AI SRE Agent 的区别

普通产品主要优化“更快找到一个可能的根因”。Mini-Drop 优化的是“在事实变化后仍能知道哪些结论有效”：

| 维度 | 普通 AI SRE | Mini-Drop |
|---|---|---|
| 证据 | 查询结果和引用 | 有身份、版本和生命周期的 Evidence |
| 调查结构 | 单次运行或时间线 | 盲隔离分支 + 服务端全局 lineage |
| 用户否定证据 | 修改最终回答 | 触发依赖传播和新一代调查 |
| 冲突处理 | 重新提问或覆盖答案 | 局部回滚、重开 frontier、generation fencing |
| 证据复用 | 相似结果自动复用 | 默认禁止跨分支；只有显式授权、指纹、作用域和版本均匹配才复用 |
| 失败输出 | 尽量给出答案 | 允许 `PARTIALLY_CONFIRMED` / `INSUFFICIENT_EVIDENCE` |
| 审计 | 调查日志 | 原始证据、旧分支、失效原因和新结论全部可追溯 |

## 7. 产品边界

Mini-Drop 不是：

- 通用可观测性平台；
- 已证明能够自动定位所有生产根因的模型；
- 默认自动修复系统；
- 任意主机访问或无限制 Shell Agent；
- 把知识库内容直接当作事故 Evidence 的 RAG 问答系统。

它当前适合展示和验证：

- 受限 Worker 上的主动采集；
- 用户与 AI 共同推进调查；
- Evidence 审核和引用验证；
- 证据排除后的失效传播；
- 共享证据的重新评估；
- 多分支盲探索和受控共享；
- 冲突后的局部回滚与正确拒答。

## 8. 后端完成顺序

围绕一条英雄链路验收，不继续堆叠 Collector 或 Agent 数量：

1. Proposal、Request、Task、Outbox 原子派发；
2. Evidence 排除 → 依赖节点失效 → sibling frontier 重开；
3. 新 cycle / generation 和 late-result fencing；
4. Projection 真正不可变版本与全量 citation verifier；
5. 多分支 EvidenceReuseDecision 和共享失效传播；
6. 通过 API transcript 验收重复采集、冲突回滚、正确拒答和 Sidecar 重启恢复。

### 当前代码完成度（2026-08-24）

答辩 MVP 已完成：Case/Evidence/Projection/Review、受控采集、单条 Evidence 分析、Evidence 排除后的失效传播、generation fence、branch-local Tool Gateway 可见性、branch-local Hypothesis/Gap/Causal Graph/Conclusion/Dependency 持久化、Evidence promote、分支创建/切换工作区均已有代码和回归测试。

仍未完成的目标语义：多个替代支持集的统一真值维护、冲突字段可比性判定后的自动局部回溯、Source/MCP 与 Task Artifact 完全统一的 Ingestion contract。当前分支上下文使用 `branch_id` 持久化推理状态，并结合 `InvestigationTree.branch_id` 和 `CaseEvidence.lineage` 实现，适合内部答辩展示，不应宣称为通用 ATMS/ECRD 实现。

旧 `DiagnosisSession`/规则 RCA 仅保留为默认关闭的兼容链：旧 API、CLI 和维护任务必须显式设置 `MINI_DROP_ENABLE_LEGACY_DIAGNOSIS=1` 才能使用；它不再是新 Case 的 AI 能力证据，也不再接收新设计扩展。旧链中可复用的 Collector parser、Evidence 字段转换、审计/fence、授权和 benchmark 继续迁移或保留。

## 9. 最终答辩叙事

不要说：

> 我们做了一个会自动分析服务器的 AI Agent。

应该说：

> 我们研究的是 AI 调查在证据变化下如何保持可信。Mini-Drop 让 AI 可以主动获取信息，但任何结论都必须绑定到当前有效 Evidence。用户可以撤销中间事实，系统会保留历史、使受影响分支失效、阻止旧推理继续写入，并从有效祖先重新探索。这使调查从一次性回答变成可治理、可回放、可纠错的运行时。
