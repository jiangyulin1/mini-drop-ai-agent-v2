# Mini-Drop 智能归因评测报告

## 1. 评测目标

验证智能归因引擎（5 层流水线：证据采集 → 候选生成 → 置信度校准 → LLM 推理 → 修复计划）在真实环境下的行为——规则引擎匹配是否准确、LLM 输出的 evidence_refs 是否能通过校验、校验失败时自修复机制是否生效。

本报告的**所有数据均来自真实运行的 Mini-Drop 实例**（Server: Docker Desktop Windows, Agent: Linux VM root 权限, DeepSeek v4-flash）。

---

## 2. 评测方法

构造 4 个已知根因的性能场景，每个场景执行完整诊断管线，逐项检查：

1. 规则引擎是否匹配合适的候选原因
2. LLM 推理输出是否能通过 Schema + evidence_refs 完整性校验
3. 校验失败时是否触发自修复重试（最多 2 次）
4. 修复计划是否根据验证结果生成合理动作

---

## 3. 测试场景与实测结果

### 场景 A：CPU 递归热点（证据不足导致校验失败）

- **负载**：`demo/cpu_hotspot.py` PID=307763（递归 fib + 排序 + JSON）
- **采集器**：perf_cpu, 15s, 99Hz
- **任务 ID**：`task_20260621_041406_7dcb08`
- **预设根因**：`cpu_hotspot_recursive`
- **任务状态**：DONE（全链路 5 步迁移正常）

**实测数据**：

| 指标 | 值 |
|------|-----|
| perf.data 大小 | 24,868 B |
| TopN 热点 | **空**——符号全部为 `[unknown]`，规则引擎关键词无法匹配 |
| flamegraph.json | 39 B（空树） |
| `get_flamegraph_top` 工具状态 | `missing` |

> **根因**：VM 上的 Python 未安装 debug symbols，perf 无法将地址解析为函数名。规则引擎的 `cpu_hotspot_recursive` 规则要求 TopN[0].name 包含 `fib`/`hotspot` 等关键词，但 TopN 为空 → **无规则匹配** → 产生 `insufficient_data` 候选（rule_score=0.10）。

**诊断结果**：

| 指标 | 值 |
|------|-----|
| validated | **false** |
| 自修复尝试 | 2 次 |
| ranked_causes | 0（LLM 输出中 evidence_refs 引用不存在的 `tool_results[0].output.top_percent` 路径） |
| 修复计划 | `collect_more_evidence`（建议补充 baseline + TopN + eBPF） |

**结论**：❌ Top-1 归因未匹配预设根因，但系统行为正确——证据不足时不强行输出高置信度结论，校验层拦住了 LLM 幻觉的引用路径。

---

### 场景 B：eBPF IO 延迟异常 ✅ 实测通过

- **负载**：`dd if=/dev/zero of=/tmp/eval-io-stress bs=4M count=512 oflag=direct` 制造 2GB 块设备写入
- **采集器**：ebpf_io, 10s
- **任务 ID**：`task_20260621_041406_0832a5`
- **预设根因**：`io_wait_high`

**实测数据**：

| 指标 | 值 |
|------|-----|
| eBPF IO 延迟样本 | 22 个请求分布在 5 个延迟区间 |
| eBPF 延迟区间 | `[64,128)`: 4, `[128,256)`: 0, `[256,512)`: 3, `[512,1K)`: 0, `[2K,4K)`: 8 |
| `get_ebpf_latency_summary` 工具 | `success` |
| 规则引擎 `io_wait_high` 规则匹配 | ✅ 触发（ebpf_latency_present） |

**诊断结果**（已验证通过）：

```json
{
  "cause_id": "io_wait_high",
  "confidence": 0.35,
  "claim": "块设备IO延迟存在高值分布（2000-4000μs占8/22持续增长），
            样本量小且无基线对比，无法判断是否为异常",
  "evidence_refs": [
    "ebpf_metrics.io_latency_us",
    "tool_results.get_ebpf_latency_summary"
  ],
  "uncertainties": [
    "样本量仅22个统计意义不足",
    "缺乏历史基线对比数据",
    "未区分读写类型和具体设备"
  ],
  "verification_steps": [
    "增加采样时长以获取更多样本",
    "获取历史基线数据对比延迟分布",
    "结合iostat确认磁盘队列深度"
  ]
}
```

| 指标 | 值 |
|------|-----|
| validated | **true** |
| Top-1 归因 | `io_wait_high` ✅ |
| evidence_refs 完整 | ✅ 2/2 通过校验 |
| 修复计划 | 2 条动作（`create_followup_task` + `system_tuning_suggestion`） |

修复计划自动执行的 safe_auto 动作：

```
[已执行] create_followup_task
  → 已创建二次采集任务 task_20260621_041804_7bda2a
[待确认] system_tuning_suggestion
  → 建议人工检查磁盘队列、IO 调度器和底层存储层
```

**结论**：✅ Top-1 归因与预设根因一致。evidence_refs 真实可追溯。修复计划自动创建了跟进采集任务。置信度 0.35 反映了样本量小（22 个）和缺少基线对比的事实——系统诚实地报告了不确定性，而非强行输出高置信度。

---

### 场景 C：目标 PID 不存在（LLM 校验失败）

- **负载**：无，`target_pid=999999`
- **采集器**：perf_cpu, 5s
- **任务 ID**：`task_20260621_041320_5a207e`
- **预设根因**：`target_pid_invalid`

**实测数据**：

| 指标 | 值 |
|------|-----|
| 任务状态 | FAILED |
| 失败原因 | "目标 PID 999999 不存在" |
| `inspect_task_events` 工具 | `success`（正确捕获了失败事件） |
| 规则引擎 `target_pid_invalid` 规则匹配 | ✅ 触发（failure_contains + rule_score=0.95） |

**诊断结果**：

| 指标 | 值 |
|------|-----|
| validated | **false** |
| 自修复尝试 | 2 次 |
| 校验失败原因 | LLM 输出的 evidence_refs 引用了不存在的 `failure_events` 路径（schema 中的实际字段名为 `task_metadata.status` + `task_metadata.status_reason`） |

**结论**：❌ 未产生可用的 ranked_causes，但规则引擎正确匹配了 `target_pid_invalid`（rule_score=0.95）。问题出在 **LLM 推理层的 evidence_refs 路径命名与 schema 不匹配**——LLM 在 Few-Shot 约束下仍会自行发明字段名。这是校验层存在的价值，也暴露了 prompt 中的 Few-Shot 示例需要补充更多失败场景的精确 ref 格式。

---

### 场景 D：采样时长过短（LLM 校验失败）

- **负载**：无，`target_pid=1, duration_sec=1`
- **采集器**：perf_cpu, 1s
- **任务 ID**：`task_20260621_041320_e8bfe4`
- **预设根因**：`insufficient_data`

**实测数据**：

| 指标 | 值 |
|------|-----|
| 任务状态 | DONE（Analyer 虽完成但产物质量差） |
| TopN | 空 |
| flamegraph.json | 39 B（空树） |
| 有效样本 | 极少，不足以生成有意义的火焰图 |

**诊断结果**：

| 指标 | 值 |
|------|-----|
| validated | **false** |
| 自修复尝试 | 2 次 |
| 校验失败原因 | LLM 输出引用 `tool_results[0].error_message`、`tool_results[1].error_message` 等不存在的路径 |
| 规则引擎 | 无匹配 → `insufficient_data`（rule_score=0.10） |
| 修复计划 | `collect_more_evidence`（建议补充采集） |

**结论**：✅ 规则引擎正确判定 `insufficient_data`。LLM 校验失败暴露了与场景 A/C 相同的问题——Few-Shot prompt 需要在 evidence_refs 命名上更精确的约束。

---

## 4. 评测指标汇总

| 场景 | 预设根因 | 规则引擎匹配 | validated | ranked_causes | 结论 |
|------|---------|-------------|-----------|---------------|------|
| A | cpu_hotspot_recursive | ❌（符号缺失→无匹配） | false | 0（校验拦截） | 数据质量问题，非引擎问题 |
| B | io_wait_high | ✅ | **true** | 1 (`io_wait_high` 0.35) | ✅ **通过** |
| C | target_pid_invalid | ✅（rule_score 0.95） | false | 0（校验拦截） | 规则正确，LLM ref 命名需修 |
| D | insufficient_data | ✅（rule_score 0.10） | false | 0（校验拦截） | 规则正确，LLM ref 命名需修 |

**关键数据点**：

| 指标 | 值 |
|------|-----|
| 校验通过率 | 1/4（25%） |
| 规则引擎匹配率 | 3/4（75%，场景 A 符号缺失不可归因于引擎） |
| LLM 自修复成功率 | 2/2 失败场景各重试 2 次（达到上限），均未修复 hallucinated refs |
| validated=true 场景的 evidence_refs 完整性 | 2/2（100%） |
| 修复计划生成 | 4/4（100%，包括 validated=false 场景也能生成降级建议） |

---

## 5. 发现的问题与根因

### 5.1 符号解析问题（场景 A）

VM 上的 Python 未安装 dbgsym 包，perf 采样后栈帧全部为 `[unknown]`。**这不是 RCA 引擎的问题**——任何依赖 perf 符号解析的工具在同样环境下都会失败。

**解决方案**：安装 `python3-dbg` 或在 Agent 部署文档中说明需要 debug symbols。

### 5.2 LLM evidence_refs 幻觉（场景 A、C、D）

LLM 在 Few-Shot 约束下仍会自行发明不存在的 evidence_refs 路径（如 `failure_events`、`tool_results[0].error_message`）。校验层正确拦截了这些引用，但 2 次自修复均未成功修正。

**根因**：当前 Few-Shot prompt 中的示例 `evidence_refs` 只覆盖了成功路径（`top_functions[0].percent`、`ebpf_metrics.io_latency_us`）。失败场景（invalid PID、短采样）的 ref 格式未在 prompt 中给出精确样例。

**解决方案**：
1. 在 prompt 的 Few-Shot 示例中增加失败场景的精确 evidence_refs 格式
2. 考虑在 validator 中实现 fuzzy match（如 `failure_events` → 自动映射到 `task_metadata.status_reason`）
3. 增加规则引擎输出直接作为 fallback ranked_cause，绕过 LLM 推理层（纯规则模式）

### 5.3 样本量影响置信度（场景 B）

22 个 eBPF 样本产生的置信度仅 0.35。这个数字诚实地反映了数据不足，但不是引擎缺陷——增加采样时长（如 30s）预计可将置信度提升到 0.5+。

---

## 6. 与原始报告（旧版）的差异

| 旧版声称 | 实测结果 |
|----------|----------|
| 4/4 准确率 | 1/4 通过校验（3/4 被校验层正确拦截） |
| 平均置信度 0.79 | B 场景 0.35，其他 0 |
| "诊断报告引用 top_functions[0]" | 只有 eBPF 场景的 refs 通过了校验 |

**影响**：旧版报告的 `0.79`、`0.66`、`0.91` 是手工推算的理想值，非真实管线输出。本次实测暴露了 LLM 推理层的 evidence_refs 幻觉问题——这正是校验层存在的原因，也是真实的工程发现。

---

## 7. 改进计划

| 优先级 | 改进项 | 预期效果 |
|--------|--------|----------|
| P0 | Few-Shot prompt 补充失败场景的精确 evidence_refs | 场景 C/D validated=true 比例提升 |
| P1 | Validator 实现 fuzzy match（别名映射） | 容忍 LLM 轻微命名偏差 |
| P1 | 校验失败时降级为规则引擎纯输出 | validated=false 时仍有可展示的 ranked_causes |
| P2 | 建立 regression test suite | 防止 prompt 修改后已有场景退化 |
| P2 | 补充 10+ 真实环境场景测试 | 覆盖 sys_metrics / memory / cross_evidence 等多维规则 |
| P3 | 对比 DeepSeek-V4-Flash 与 V4-Pro 的 evidence_refs 准确率差异 | 验证模型升级是否能显著降低幻觉 |

---

## 8. 总结

本次评测**基于真实运行的 Mini-Drop 实例**完成了 4 个场景的端到端诊断测试。

- **5 层管线各层均正常工作**：证据采集正确识别了数据可用性，规则引擎在 3/4 场景中正确匹配，校验层成功拦截了 LLM 的 3 次幻觉输出。
- **唯一通过校验的场景 B** 正确输出了 `io_wait_high` 归因，evidence_refs 真实可追溯，修复计划自动创建了跟进采集任务。
- **核心待改进项**是 LLM Few-Shot prompt 中失败场景 evidence_refs 的精确性——这是可定位、可修复的 prompt 工程问题。

**实测日期**：2026-06-21

**测试环境**：Mini-Drop v0.1.0 · DeepSeek v4-flash · Docker Desktop Windows Server · Linux VM 5.15.0 Agent · Ubuntu 22.04
