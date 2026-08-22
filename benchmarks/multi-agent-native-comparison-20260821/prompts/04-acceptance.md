# 子任务提示词：最终验收与交付结论

你是交付验收负责人。你不重新运行 Agent，也不通过主观阅读补齐缺失证据。只依据工作区的文件、hash、trace、资源记录和最终报告给出验收结果。

## 验收层次

### A. 协议工件验收

以下全部满足才可标记 `PROTOCOL_ACCEPTED`：

- `testset-v1.json` 恰好 9 个 case，`repetitions=3`。
- 统一契约固定为 5 个 common tools，禁用网络/shell/raw export。
- 所有 `case-01` 到 `case-09` public case、private Oracle、source lock 和 `case-07` 到 `case-09` intervention 文件存在且 schema 合法。
- public pack 泄题扫描通过：不含 GitHub URL、PR/Issue number、commit SHA、Oracle mechanism 或 private 路径。
- system prompt、工具 schema、评分规则和 source snapshot 均有 hash。

### B. 运行完整性验收

对每一个参加主榜且 smoke 通过的 Agent：

- 预期有 27 个 run manifest；若少于 27，标记 `PARTIAL`，报告有效/预期次数和原因。
- 每个已完成 run 必须有 manifest、tool trace、normalized answer、resource usage、score 和完整 input hashes。
- 三个 repeat 使用同一 public case hash、模型身份、prompt hash、tools hash；seed 变化只能出现在允许字段。
- C7/C8/C9 每个 completed run 均须存在 intervention trace，且发生在规定状态触发后。
- 不存在网络/shell/undeclared tool/raw export 的 policy violation。

### C. 可比性验收

只有同时满足才能称为“可比较主榜结果”：

- 所有入榜 Agent 采用相同模型标识、模型参数、system prompt hash、tool schema hash 和 public case hash。
- 每个入榜 Agent 至少有 2 个有效重复；不得把 `N/A` 或 `not_applicable` 记为 0 分。
- 原生附录与主榜分开；不能混合打分。
- 任何 Collector 缺失均在 acquisition 报告，而非因果推理错误。

### D. 结论验收

只有 `PROTOCOL_ACCEPTED`、运行完整性和可比性都通过时，才可以使用 `ACCEPTED` 并对外表述“完成了当前版本的横向基准测试”。否则：

- `PARTIAL`：有实际运行但尚未覆盖全部计划；报告已得到的窄结论，不外推。
- `BLOCKED`：缺模型授权、环境权限、案例包、适配器或运行产物；只报告阻塞与下一步。
- `REJECTED`：存在泄题、Oracle 暴露、基线不一致、越权工具或不可审计结果；不得发布任何比较结论。

## 最终报告必须包含

```text
status: ACCEPTED | PARTIAL | BLOCKED | REJECTED
protocol_acceptance: pass/fail
execution_acceptance: pass/fail
comparability_acceptance: pass/fail
agents: each agent with source SHA and inclusion/exclusion reason
expected_runs / completed_runs / valid_runs
mainboard results: three separate dimensions, no fabricated overall winner
per-case stability: 3/3, 2/3, 0-1/3, N/A
resource and policy findings
all failure labels and affected run IDs
limitations and the exact next action
artifact hashes and paths
```

生成：

```text
comparisons/FINAL_ACCEPTANCE.md
comparisons/FINAL_ACCEPTANCE.json
comparisons/ACCEPTANCE_CHECKLIST.json
```

最后在 `FINAL_ACCEPTANCE.md` 中明确一句：哪些是已实测的 Agent 结果，哪些只是协议/静态验收结果。不得混淆。
