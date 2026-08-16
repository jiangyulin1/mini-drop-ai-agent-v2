# Agent 包装方式调研与稳定性/Skill/Knowledge 补充

> 状态：实验与实施记录。外部资料通过 VM 的 GitHub API 查询获得（2026-08-15）。

## 1. 网络调研摘要

| 仓库 | 与本项目相关的模式 |
|---|---|
| `anthropics/claude-plugins-official` | 官方插件/技能目录，证明“Skill 作为可版本化过程提示词”是当前主流包装方式 |
| `VoltAgent/awesome-agent-skills` | 1000+ Agent Skills 索引，常见结构为 SKILL.md + triggers + procedure + negative examples |
| `openai/openai-cs-agents-demo` | OpenAI Agents SDK 的受控工具与任务边界，工具不是任意 Shell |
| `langchain-ai/langgraph` | 将 Agent 建模为可恢复状态图；对应本项目 Case/Plan/Outbox |
| `suyoumo/ClawProBench` | 强调 live-first、deterministic grading、repeated-trial reliability，对应 P01-P12 重复门禁 |
| `gnkbhuvan/ai-engineering-gates` | 用确定性 gate、行为探针和 rubric 约束 Agent，而非只做聊天截图验收 |
| `hwfengcs/SDYJ_Multi_Agents` | 多 Agent 规划与人类介入，对应 CONTROL/ANSWER_ONLY 分离 |

共同结论：

1. 成功包装 Agent 的方式不是“包一层聊天”，而是 **确定性领域内核 + 可替换模型 Runtime + 受控工具目录**。
2. Skill 是给 Agent 的**小型、版本化、带正负触发条件的过程知识**，不能替代运行时 Evidence。
3. Knowledge 只用于解释和规划，**不能当作 Current Evidence 确认根因**。
4. 稳定性来自：固定系统提示词、固定 thinking level、可复用的 Case Snapshot、规范化事件、重复问题复用既有结论、确定性评分。
5. 评估必须用固定公开集 + 外部 Holdout，不能由施工 Agent 自评。

## 2. 本轮新增实现

### 2.1 Skill Registry

新增 `server/app/diagnosis/skill_registry.py`：

- 8 个内置 Skill：
  - `answer_stability`
  - `linux_cpu_diagnosis`
  - `linux_memory_diagnosis`
  - `jvm_gc_diagnosis`
  - `mysql_lock_diagnosis`
  - `tcp_retransmit_diagnosis`
  - `cluster_attribution`
  - `evidence_gap`
- 每个 Skill 有：
  - 正触发词
  - 负触发词
  - 必须 Evidence
  - 过程步骤
  - 停止条件
- 选择是确定性的：`answer_stability` 永远优先，负触发会扣分。

### 2.2 Knowledge 检索增强

- 修复中文知识匹配：
  - 原来只匹配完整中文词串
  - 现在加入中文二元组和单字特征，能命中“用户态高”和“用户态”这类短词重叠
- Knowledge 仍只作为 `knowledge_context` 注入，不写入 Evidence。

### 2.3 Pi 提示词与稳定性约束

- `thinkingLevel` 可通过 `MINI_DROP_PI_THINKING_LEVEL` 固定，VM 当前为 `high`
- 系统提示词增加硬约束：
  - 必须先读 Case Snapshot 和已有 Evidence
  - 重复问题必须复用相同结论和 Evidence ID
  - 没有新证据或用户纠正时不得改变结论
  - 证据不足时输出精确 Evidence Gap 并 abstain
  - 不得给出多方向猜测
- CaseContextSnapshot 增加：
  - `knowledge_context`
  - `skill_context`
- Sidecar Turn Prompt 同时注入 `[CaseContext]` 中的 skills 和 knowledge。

### 2.4 事件稳定性

- 只持久化 normalized lifecycle/tool events
- 私有 thinking 被剥离
- 流式 `message_update` 不落库
- 幂等去重

## 3. 重复问题稳定性实验

新增 `scripts/vm_pi_repeatability.py`，在同一 Case 上重复 3 次相同问题：

实验问题：

> 请基于当前 Case 快照，说明定位 checkout 延迟的稳定诊断步骤；不要创建任何任务，只输出诊断方案。

结果：

| 指标 | 值 |
|---|---|
| 工具调用序列一致 | true |
| run1 vs run2 Jaccard | 0.661 |
| run1 vs run3 Jaccard | 0.656 |
| run2 vs run3 Jaccard | 0.971 |
| 结论方向 | 三次均为“无 Evidence，明确 abstain，不创建任务” |

解释：

- 第一轮回答更详细，与后续回答相似度约 0.66
- 第二轮和第三轮几乎稳定，相似度 0.971
- 结论方向一致，没有出现多方向分歧
- 仍存在的差异主要来自表达长短，而不是结论分叉

## 4. 下一步稳定性增强方向

1. 在 Case 中持久化“canonical answer fingerprint”，重复问题直接返回上次结论投影
2. 将 Skill 触发记录写入 AgentDecisionRecord / Runtime Event
3. 对最终结论做 Schema 约束：Primary/Contributing/Propagation/Evidence IDs 必填
4. 用固定 temperature/seed（若 Provider 支持）或通过 Prompt 明确拒绝改写已有结论
5. 增加 `ANSWER_ONLY` 的 Pi 路径，不创建 Task 且不进入调查
6. 重复实验扩展到 5 次并加入 deterministic evaluator 评分
