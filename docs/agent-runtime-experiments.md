# Agent Runtime Experiments

诊断策略、模型运行参数和执行权限是三个独立维度：

- 实验 `strategy_id`：`rule_tree`、`hypothesis_first`、`evidence_first`、`causal_graph`、`exploratory`、`hybrid`；
- `RuntimeOptions`：reasoning effort、model、prompt variant、temperature、max tokens、seed；
- `RuntimePolicy`：工具/操作白名单、风险上限、审批要求和 execution mode。

生产 Agent Turn 只允许 `hybrid`。其他策略是实验对照 Arm，必须使用 `dry_run` 或 `sandbox`；它们不是面向用户的并行诊断产品。请求只能缩小代码拥有的权限，不能启用任意命令或绕过 R3 人工审批。`dry_run`、`sandbox` 和 `deny_write` 不会落到本机原生执行器。生产审计只保存策略、工具序列、Evidence 引用和决策摘要，不保存模型私有思维链。

Pi 0.84.2 当前原生应用 model、reasoning effort 和 prompt variant。temperature、max tokens、seed 会进入可复现实验元数据，但该 SDK 的 `createAgentSession` 尚未暴露对应参数，因此不会伪装为已经生效；API 的 `runtime_support` 会明确标记这一点。

## 运行矩阵

```bash
python scripts/run_agent_strategy_matrix.py \
  --matrix benchmarks/agent_strategy_matrix.example.json \
  --output-dir reports/strategy-matrix
```

先用 `--validate-only` 校验配置。离线模式只运行规则控制组，不执行所选策略，因此仅输出 `control_group_root_cause_accuracy`，并把策略 `root_cause_accuracy` 留空；它不能用于比较策略。只有 `--live` 才报告策略的根因文本匹配代理、Evidence 引用有效性、工具调用数、副作用数、重复一致性以及真实 token/cost。

可复现实验记录 `strategy`、`runtime_policy`、`runtime_options`、seed、代码提交、输入文件 hash 和运行环境。不要把 API Key、Provider Token 或原始推理轨迹写入矩阵或报告。
