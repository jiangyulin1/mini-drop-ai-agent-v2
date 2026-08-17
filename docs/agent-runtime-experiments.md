# Agent Runtime Experiments

诊断策略、模型运行参数和执行权限是三个独立维度：

- `strategy_id`：`rule_tree`、`hypothesis_first`、`evidence_first`、`causal_graph`、`exploratory`、`hybrid`；
- `RuntimeOptions`：reasoning effort、model、prompt variant、temperature、max tokens、seed；
- `RuntimePolicy`：工具/操作白名单、风险上限、审批要求和 execution mode。

默认 `hybrid` 保持旧版行为。请求只能缩小代码拥有的权限，不能启用任意命令或绕过 R3 人工审批。`dry_run`、`sandbox` 和 `deny_write` 不会落到本机原生执行器。生产审计只保存策略、工具序列、Evidence 引用和决策摘要，不保存模型私有思维链。

Pi 0.84.2 当前原生应用 model、reasoning effort 和 prompt variant。temperature、max tokens、seed 会进入可复现实验元数据，但该 SDK 的 `createAgentSession` 尚未暴露对应参数，因此不会伪装为已经生效；API 的 `runtime_support` 会明确标记这一点。

## 运行矩阵

```bash
python scripts/run_agent_strategy_matrix.py \
  --matrix benchmarks/agent_strategy_matrix.example.json \
  --output-dir reports/strategy-matrix
```

先用 `--validate-only` 校验配置。报告包含根因准确率、Evidence 引用有效性、工具调用数、副作用数、禁止调用数、重复一致性和估算成本。示例使用离线确定性 Evidence harness；真实 Pi 的延迟、token 和 provider 成本应在隔离 VM profile 中测量。

可复现实验记录 `strategy`、`runtime_policy`、`runtime_options`、seed、代码提交、输入文件 hash 和运行环境。不要把 API Key、Provider Token 或原始推理轨迹写入矩阵或报告。
