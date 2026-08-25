# 答辩 User Story：基于实际后端落地的证据链调查

> 本稿只描述当前 Evidence-native Case Workspace 已有的后端行为，不把规划中的通用多支持集、完整自动冲突回溯或自动修复写成已实现能力。

## 1. User Story 的核心表达

### PPT 标题

> 用户要的不是一个根因答案，而是一条可以被干预、被纠正、被重新验证的证据链

### 一句话故事

值班工程师遇到一个复杂、含噪、不能直接下结论的线上问题。他希望 AI 协助调查，但不希望 AI 默认看到所有历史数据，更不希望一个未经验证的中间证据长期污染后续推理。

Mini-Drop 让这次调查成为一个持久化 Case：用户可以把已有 Task/Collection 添加进 Case，AI 可以在当前调查分支内申请新的受控采集，系统把结果固化为 Evidence；不同分支默认隔离，用户可以审查或排除 Evidence；证据发生变化后，依赖它的推理失效，系统保留有效历史并重新验证当前结论。

## 2. 复杂事故设定

### 用户看到的现象

```text
checkout 服务间歇性超时
首页正常，购物车/支付链路受影响
部分实例 CPU 偏高
同机邻居也出现波动
日志出现连接等待和请求堆积
```

### 用户的限制

```text
请协助定位
只读调查
不要直接修改生产环境
```

### 当前不能直接确定的事实

```text
CPU 高是根因，还是噪声？
请求卡在 Runtime，还是下游连接？
异常属于哪个实例和时间窗口？
已有采集是否足以支持结论？
```

这个场景的关键不是“模型能不能猜中 CPU 或 Redis”，而是它能否把模糊现象拆成多个待验证假设，并让每一条证据的来源和有效范围保持清楚。

## 3. 用户真实操作链

### 第一步：创建 Case

用户创建一个 Incident Case，写入问题描述、环境、目标范围、时间窗口和运行模式。Case 保存后，后续的 Case、Scope、Control、Plan、Evidence 和 Runtime revision 都有持久化依据。

用户不是把一段文本直接交给模型，而是先建立一个有范围的调查对象。

### 第二步：添加已有数据

用户可以把已有 Task、Collection 或对话中的资源引用添加到 Case：

```text
用户添加 ResourceRef
        ↓
ReferenceResolver 校验租户、范围、资源状态和质量
        ↓
Case Attachment：ACCEPTED / PARTIAL / REJECTED
        ↓
Task Artifact 物化为 CaseEvidence + EvidenceProjection
```

这一步的含义是：用户明确把一批已有数据带入当前 Case。系统不会把任意资源直接塞进 Prompt，而是先形成 Attachment，再登记 Evidence ID、Artifact provenance、目标、时间窗、内容 hash 和 Projection hash。

用户添加的已有资源在当前实现中作为 Case 的公共种子 Evidence，可被后续调查分支看到；它仍然需要经过资源解析和生命周期/Review 规则，不等同于无条件可信。

### 第三步：创建独立调查分支

用户可以创建一个新的 Investigation Branch。后端创建 branch、run 和 agent cycle，并在 Workspace 中提供分支句柄。

例如：

```text
Branch A：验证 CPU / Runtime 假设
Branch B：验证下游连接假设
```

分支的默认可见 Evidence 是：

```text
公共种子 Evidence
        +
本分支自己产生的 Evidence
        +
被 operator 显式 promote 到本分支的 Evidence
```

其他分支的 branch-local Evidence 不会自动泄漏。后端的 Agent-facing read boundary 会按 Evidence lineage 的 `PUBLIC_SEED / BRANCH_LOCAL / PROMOTED` 过滤；树节点、Proposal、Request、Hypothesis、Gap、Causal Graph 和 Conclusion 也按 branch 过滤。

### 第四步：AI 在当前分支内调查

AI 读取当前分支的 Case Snapshot 和 Evidence inventory，维护：

```text
Hypothesis
Evidence Gap
Investigation Tree
Causal Graph
Collection Proposal
```

它可以选择：

- 读取当前分支可见的 Evidence；
- 展开指定 Evidence Projection；
- 比较 Evidence 的信号、目标、时间和质量；
- 提出 Hypothesis、Gap、树节点和依赖关系；
- 从 Collector Catalog 中申请一项采集；
- 在证据不足时继续补证、部分确认或拒答。

AI 不能直接创建任意命令。它提出的 `propose_collection` 必须带 Collector、目标、参数、信息目标、输入 Evidence refs 和当前 revision；Tool Gateway 与 CollectionSupervisor 再检查范围、Worker capability、风险、预算、审批、幂等和 fencing，之后才创建原生 Task。

### 第五步：新采集结果只进入申请它的分支

如果 AI 在 Branch A 申请了 `runtime_snapshot`，完成后的 Task Artifact 会通过 CaseEvidenceService 物化为 canonical Evidence 和 Projection，并带有：

```text
lineage.branch_id = Branch A
visibility_scope = BRANCH_LOCAL
```

它不会因为属于同一个 Case，就自动出现在 Branch B 的 Agent Snapshot 中。采集完成后，Outbox/Wakeup 携带本次新 Evidence，Agent 重新构造当前上下文继续调查。

这就是项目中“AI 能拿到的证据不是全局注入”的实际实现含义：AI 只能使用当前分支允许它读取的 Evidence；新的深度证据由该分支的受控采集产生，已有数据则由用户通过 Attachment 明确带入。

### 第六步：用户审查证据

用户看到 Evidence 后，可以先请求影响预览，再提交 Review：

```text
Evidence Review Preview
        ↓
target identity / time alignment / data integrity
source reliability / scope fit / corroboration / freshness
        ↓
TRUSTED / LOW_TRUST / EXCLUDED / RESTORE
```

这里的 Review 不是修改一个模型 confidence 数字：

- `LOW_TRUST`：仍可被查看和引用，但不能单独支撑高确定性结论；
- `EXCLUDED`：从当前推理准入中移除；
- `RESTORE`：在新的 Review revision 下恢复使用；
- `HIDDEN / ARCHIVED`：只整理 UI，不等于改变推理准入。

排除 Evidence 后，原始 Artifact、Evidence 身份和历史事件仍然保留；当前 Agent Snapshot 只保留非内容的审计标记，不把被排除 Projection 的原始数值重新送给模型。

### 第七步：局部失效与重新验证

如果被排除的 Evidence 被某条 Hypothesis、Causal Edge 或 Conclusion 引用，系统会计算影响并更新当前状态：

```text
Evidence Review Revision 变化
        ↓
依赖 Evidence 的 Claim / Hypothesis / Edge 失效
        ↓
AnalysisRun = STALE_INPUT
Conclusion = RECHECK_REQUIRED
        ↓
从仍然有效的 Evidence 重新调查
```

不依赖该 Evidence 的旁支不会被整棵树删除。分支 A 的证据被排除时，分支 B 的独立结论可以保留；对应测试已经验证 branch-local review 只影响受影响分支。

### 第八步：显式跨分支共享

如果 Branch A 产生的 Evidence 对 Branch B 确实有价值，必须由 operator 通过显式 promote 操作授权：

```text
operator promote Evidence
        ↓
lineage.visibility_scope = PROMOTED
promoted_to_branch_id = Branch B
        ↓
Branch B 才能读取该 Evidence
```

共享不是默认行为，也不是模型自行决定的上下文合并。共享事件会记录在 Case timeline，且不会撤销生产分支对该 Evidence 的访问。

### 第九步：提交结论

Agent 不能只发送一段普通文本作为最终结论，必须调用结构化 finish。Server 会验证：

- Evidence 是否属于当前 Case 和当前分支；
- Evidence 是否仍处于有效生命周期；
- Projection hash 是否仍是当前版本；
- claim 的 field path 是否真实存在；
- quote/span 是否与 Projection 内容一致；
- 是否存在阻断 Gap、未区分的替代假设或未验证因果边；
- LOW_TRUST Evidence 是否被错误地单独用于高确定性结论。

最终状态只能落在：

```text
CONFIRMED
PARTIALLY_CONFIRMED
INSUFFICIENT_EVIDENCE
```

因此，系统的结果不是单一“根因文本”，而是带 Evidence binding、限制、Gap、因果引用和 revision 的 ConclusionRevision。

## 4. 这条 User Story 的真正价值

### 对用户

用户不再只能接受一个不可解释的最终答案，而可以看到：

- AI 当前看到了哪些 Evidence；
- 哪些 Hypothesis 正在推进；
- 哪个 Evidence Gap 阻塞结论；
- 某条证据为什么被降信任或排除；
- 排除后哪些结论失效；
- 哪些独立证据仍然有效；
- 下一代调查从哪里重新开始。

### 对系统

系统不再把“证据存在”“证据可见”“证据可信”“证据支持结论”混为一谈：

```text
Case Evidence：证据存在
Branch visibility：证据对谁可见
Review revision：证据是否准入
Claim binding：证据支持什么
Conclusion revision：当前结论是否仍有效
```

### 对 AI 评测

分支隔离让不同探索路径可以在相同公共种子下独立运行。评测不只看最后是否猜中，还可以比较：

- 是否选择了正确信息目标；
- 是否申请了高价值 Collector；
- 是否引用了当前有效 Evidence；
- 是否避免使用被排除 Evidence；
- 是否在证据不足时正确停止；
- 不同分支之间是否发生非授权信息泄漏。

## 5. 答辩时的精简讲法

### 90 秒版本

用户遇到复杂运维问题时，真正需要的不是一段无法检查的 AI 结论，而是一条可以持续验证的证据链。Mini-Drop 让用户先创建一个有范围的 Case，可以添加已有 Task 或 Collection；系统先经过资源解析，再把 Artifact 固化为 canonical Evidence 和 Projection。用户还可以创建独立调查分支：每个分支默认只看到公共种子和自己的 Evidence，其他分支的结果不会自动进入上下文。

AI 在分支内维护假设、Evidence Gap 和调查树，只能申请已注册 Collector，不能直接执行任意命令。新采集结果带着 branch lineage 回到原生 Task 和 Evidence 链，完成后唤醒当前分支继续调查。如果用户发现某条 Evidence 时间不对、目标不对或质量不足，可以把它标记为 LOW_TRUST 或 EXCLUDED。系统不会删除整棵树，而是让依赖它的 Claim、Hypothesis 和 Conclusion 失效，生成 RECHECK_REQUIRED，从有效 Evidence 重新验证。

如果另一个分支需要这条证据，也不能自动共享，必须由用户或 operator 显式 promote。最终结论还要通过 Evidence ID、Projection hash 和字段引用校验，并可以合法地停在 PARTIALLY_CONFIRMED 或 INSUFFICIENT_EVIDENCE。这个项目的核心不是让 AI 更大胆地猜，而是让 AI 的可见证据、推理路径和结论变化都可以被人检查和干预。

## 6. 答辩时必须主动说明的边界

- 当前分支隔离、Evidence Review、Evidence promote、局部失效和 Conclusion recheck 已有主路径和测试；
- 多支持集统一真值、复杂冲突的自动祖先回溯、完整跨分支 Claim/Hypothesis/Conclusion 共享撤销传播仍未完全闭合；
- 普通交互 Turn 的兼容入口与自主 Wakeup 的精确 Evidence watermark 语义不同，现场演示应使用已验收的 branch/Workspace 主路径；
- 不要把 `PUBLIC_SEED` 说成“用户证据天然可信”，它只是用户明确带入 Case 后可供分支读取的公共种子，仍受 Review 和 Citation 校验；
- 不要把 `PROMOTED` 说成模型自动共享，它是 operator 显式授权的跨分支可见性变化；
- 不要把 `INSUFFICIENT_EVIDENCE` 包装成根因定位成功，它表示系统按约束正确停止。

