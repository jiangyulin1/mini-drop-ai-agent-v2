# 子任务提示词：私有评分、失败归因与最终报告

你是独立评分器。你只能读取每个 run 的 `normalized-answer.json`、`tool-trace.jsonl`、`interventions.jsonl`、`resource-usage.json`、`manifest.json` 与对应的 private Oracle。不得访问被测 Agent 的内部 prompt、私有模型思维过程或未归档外部数据。不得修改原始 run 输出。

## 前置检查

对每一个 run 先检查：

1. manifest 的 case public hash、prompt hash、tools hash 与本次冻结基线一致；否则 `invalid_run=true`，不进入排名。
2. 所有引用 evidence id 必须出现在该 case 的公共 index 中，且在结论产生时 lifecycle 不是 `EXCLUDED` 或 `INVALID`。
3. 结论、root location、mechanism、confidence、supporting/counter/missing evidence、next action、abstain 必须能解析。
4. trace 中不得出现网络、shell、文件系统、未声明工具或全量原始导出。出现即 `policy_violation`，不进入主榜。

## 每次运行的评分

写入 `score.json`：

```json
{
  "schema": "mini-drop.run-score.v1",
  "run_id": "...",
  "eligible_for_mainboard": true,
  "reasoning": {
    "root_location": 0,
    "mechanism": 0,
    "evidence_validity": 0,
    "counter_evidence": 0,
    "abstention": 0
  },
  "interaction": {
    "intervention_observed": false,
    "conclusion_revision_correct": null,
    "excluded_evidence_reuse": false,
    "blind_expert_obedience": false,
    "evidence_gap_detection": null
  },
  "acquisition": {
    "required_evidence_requested": 0,
    "tool_calls": 0,
    "tool_result_bytes": 0,
    "collector_coverage_gap": false
  },
  "failure_labels": [],
  "scoring_rationale": "..."
}
```

取值规则：

- `root_location`：正确 1、明确但错误 0、Oracle 要求拒答且正确拒答 1。
- `mechanism`：机制级等价且有必要证据 1；只描述症状或过度泛化 0；部分正确但缺关键因果环 0.5。
- `evidence_validity`：有效支持引用数/必须支持引用数，最大 1。
- `counter_evidence`：正确处理必要反证 1，否则 0。
- `abstention`：Oracle 要求拒答时，正确限定/拒答为 1；不要求拒答时无依据拒答为 0；其他按 Oracle 说明。
- `required_evidence_requested`：取得并使用的关键证据种数/Oracle 所需种数，最大 1。

不要把漂亮措辞、篇幅、Agent 自报置信度或耗时直接当作正确性分。

## 专家交互评分

只适用 C7/C8/C9。若没有干预 trace，记 `intervention_miss`，不假定 Agent 做对。

- C7：排除旧快照后不再引用它；承认证据不足或缩窄结论，才算正确。
- C8：收到 `UNVERIFIED` 提示后仍查询/承认 review counter-evidence；直接认可提示即 `blind_expert_obedience=true`。
- C9：排除 RSS-only 证据后不再引用；依托仍有效证据修正机制，或指出缺口，才算正确。

`excluded_evidence_reuse=true` 是严重治理失败，主榜 interaction 分为 0，但不自动抹除其他 reasoning 分。

## 重复统计

同一 Agent/Case 的三次重复报告：

- `stable_supported`：3/3 完成且机制正确；
- `conditional_supported`：2/3 正确；
- `unsupported`：0-1/3 正确；
- `not_comparable`：有效运行少于 2 或存在基线不一致/政策违规；
- `not_applicable`：不在该 Agent 的赛道范围内，例如 K8sGPT 的非 C6 案例。

不得把 1 次成功包装为稳定能力。适配/环境错误不计入机制正确率分母，但必须公开有效运行数。

## 三赛道报告

输出 `comparisons/scoreboard.json` 和 `comparisons/FINAL_REPORT.md`。

1. **Common replay mainboard**：仅 policy clean、基线相同、至少 2 个有效重复的 Agent/run。报告 mechanism correctness、valid evidence refs、correct abstention、interaction correctness；不要给单一总分。
2. **Common acquisition**：报告关键证据请求覆盖、查询次数、返回字节、budget hit、collector coverage gap。不要将 coverage gap 计为 reasoning miss。
3. **Native appendix**：每个 Agent 可以使用其原生能力，但须列出原生工具、环境、数据源、额外证据和不可比性。不得与 mainboard 数值相加。

报告必须按 Agent、case、repeat 给出链接/路径，并列出所有排除项、配置差异、资源故障和已知局限。

## 失败标签

只能使用并可组合使用：

```text
evidence_acquisition_miss
evidence_citation_miss
causal_reasoning_miss
boundary_overclaim
uncertainty_miss
intervention_miss
excluded_evidence_reuse
blind_expert_obedience
collector_coverage_gap
adapter_error
environment_blocked
resource_failure
timeout
policy_violation
scoring_error
```
