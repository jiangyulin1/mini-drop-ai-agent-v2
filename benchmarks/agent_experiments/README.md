# Agent Experiments

本目录存放 Agent 策略矩阵等实验配置。

- `matrix.json`：Canonical 策略矩阵示例，覆盖 `rule_tree`、`hypothesis_first`、`evidence_first`、`causal_graph`、`exploratory`、`hybrid` 六种策略，并组合不同的 `RuntimeOptions` 与 `RuntimePolicy`。
- 运行方式见 [docs/agent-strategy-matrix.md](../../docs/agent-strategy-matrix.md)。

CI 会对 `matrix.json` 做 `--validate-only` 校验，确保策略 ID、RuntimePolicy 和场景 ID 都合法。
