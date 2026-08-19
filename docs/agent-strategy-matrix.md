# Agent 策略矩阵实验（Experiment Matrix）

`Agent Strategy Matrix` 用一份 JSON 描述“诊断策略 × 运行参数 × 权限策略”的组合，然后批量跑同一批 Case，输出横向对比报告。它把策略、思考成本和权限边界分成三个独立维度，方便回答：

- 哪种 `DiagnosticStrategy` 在同一批 Case 上更稳？
- `reasoning_effort` 降低后准确率下降多少、成本省多少？
- `RuntimePolicy` 收紧到 READ_ONLY / deny_write 后，是否仍能完成根因定位？

## 1. 矩阵文件

推荐放在 `benchmarks/agent_experiments/matrix.json`。示例：

```json
{
  "schema_version": "agent-strategy-matrix.v1",
  "matrix_id": "agent-strategy-matrix-canonical-v1",
  "scenario_root": "golden_scenarios",
  "scenario_ids": ["self_code_hotspot", "downstream_cpu_hotspot", "shared_io_contention"],
  "repetitions": 1,
  "conditions": [
    {
      "condition_id": "rule-tree-readonly",
      "strategy_id": "rule_tree",
      "strategy_params": {},
      "runtime_options": {
        "reasoning_effort": "low",
        "prompt_variant": "concise",
        "seed": 7
      },
      "runtime_policy": {
        "side_effect_policy": "READ_ONLY",
        "execution_mode": "deny_write"
      }
    }
  ]
}
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---|---|
| `schema_version` | 是 | 固定为 `agent-strategy-matrix.v1` |
| `matrix_id` | 建议 | 报告和产物中使用的可读 ID |
| `scenario_root` | 是 | 仓库内 Case 目录，默认 `golden_scenarios` |
| `scenario_ids` | 是 | `"all"` 或具体 Case ID 列表 |
| `repetitions` | 否 | 每个 condition 重复次数，默认 1，范围 1-100 |
| `conditions` | 是 | 非空数组，每个元素是一个实验条件 |
| `condition_id` | 是 | 必须唯一 |
| `strategy_id` | 是 | 必须是 `STRATEGY_REGISTRY` 中的 ID |
| `strategy_params` | 否 | 传给策略的参数，例如 `{"max_hypotheses": 3}` |
| `runtime_options` | 否 | `RuntimeOptions` 字段，例如 `reasoning_effort` / `prompt_variant` / `seed` |
| `runtime_policy` | 否 | `RuntimePolicy` 字段，例如 `side_effect_policy` / `execution_mode` |

## 2. 运行方式

先只校验配置：

```bash
python scripts/run_agent_strategy_matrix.py \
  --matrix benchmarks/agent_experiments/matrix.json \
  --validate-only
```

再跑完整矩阵：

```bash
python scripts/run_agent_strategy_matrix.py \
  --matrix benchmarks/agent_experiments/matrix.json \
  --output-dir reports/strategy-matrix
```

跑 live Pi 矩阵（读取 `model-attempts` 的真实 token/cost）：

```bash
python scripts/run_agent_strategy_matrix.py \
  --matrix benchmarks/agent_experiments/matrix.json \
  --live \
  --control-url http://47.112.10.137 \
  --worker-host <worker-ip> \
  --worker-user root \
  --worker-password <password> \
  --agent-id linux-worker-1 \
  --fault cpu-hotspot \
  --duration 360 \
  --output-dir reports/strategy-matrix-live
```

输出：

- `reports/strategy-matrix/strategy_matrix.json`
- `reports/strategy-matrix/strategy_matrix.md`
- live 模式额外在报告里写入 `model_attempt_count` / `input_tokens` / `output_tokens` / `cache_read_tokens` / `cache_write_tokens` / `cost` / `latency_ms`

## 3. 报告指标

| 指标 | 含义 |
|---|---|
| `scenario_pass_rate` | 场景通过率 |
| `root_cause_accuracy` | 根因位置/分类准确率 |
| `evidence_citation_validity` | Evidence 引用有效性 |
| `tool_call_count` | 工具调用次数 |
| `side_effect_count` | 副作用/写入类动作次数 |
| `prohibited_call_count` | 被策略禁止的调用次数 |
| `repeat_consistency` | 重复实验间结果一致性 |
| `estimated_cost_units` | 离线模式：按工具调用数和 reasoning effort 估算的成本单位 |
| `model_attempt_count` | live 模式：`model-attempts` 审计条数 |
| `input_tokens` / `output_tokens` | live 模式：实际 token 用量 |
| `cache_read_tokens` / `cache_write_tokens` | live 模式：缓存 token 用量 |
| `cost` | live 模式：模型调用实际成本 |
| `latency_ms` | live 模式：模型调用累计耗时 |

## 4. 约束

- `strategy_id` 必须来自注册表，不能由模型自由声明。
- `RuntimePolicy` 只能缩小权限，不能扩大权限。
- `capture_reasoning_trace` 不允许写入矩阵报告；实验报告只保存决策摘要、工具调用序列和最终答案。
- 默认使用离线确定性 Evidence harness；加 `--live` 可连接真实 Pi 环境并读取 `model-attempts` 的 token/cost。live 模式需要 `--worker-host` / `--worker-password` 和可达的 control plane。

## 5. CI

CI 已包含矩阵配置校验：

```bash
python scripts/run_agent_strategy_matrix.py --matrix benchmarks/agent_strategy_matrix.example.json --validate-only
python scripts/run_agent_strategy_matrix.py --matrix benchmarks/agent_experiments/matrix.json --validate-only
```

新增 condition 时，确保 `strategy_id` 已注册、`runtime_policy` 不违反权限边界、`scenario_ids` 都存在。
