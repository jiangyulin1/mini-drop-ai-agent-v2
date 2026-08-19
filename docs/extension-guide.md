# Mini-Drop 能力扩展指南

本文档描述如何以“注册表一致”的方式扩展 Mini-Drop 的 Agent 能力，避免 Python / JS / Worker / Sidecar 多处重复定义后漏注册。

## 1. 能力注册表总览

| 能力 | 注册位置 | 检查脚本 |
|---|---|---|
| Agent Tool | `server/app/agent_runtime/catalog.py` | `scripts/check_registry_consistency.py` |
| Diagnostic Strategy | `server/app/diagnosis/strategies/registry.py` | 运行矩阵 validate |
| RuntimePolicy 字段 | `server/app/agent_runtime/policy.py` | 单元测试 |
| RuntimeOptions 字段 | `server/app/agent_runtime/options.py` | 单元测试 |
| TaskKind | `server/app/task_kinds.py` | `scripts/check_registry_consistency.py` |
| Worker Collector | `agent/mini_drop_agent/main.py` 的 `COLLECTORS` | `scripts/check_registry_consistency.py` |
| Probe | `server/app/diagnosis/probe_registry.py` | `scripts/check_registry_consistency.py` |
| EvidenceContract | `server/app/diagnosis/evidence_contracts.py` | `scripts/check_registry_consistency.py` |
| QueryOperation | `server/app/diagnosis/query_registry.py` | `scripts/check_registry_consistency.py` |
| Sidecar 工具白名单 | `agent_runtime/pi-sidecar/src/tools.mjs` 的 `ALLOWED_TOOL_NAMES` | `scripts/check_registry_consistency.py` |

新增任何能力时，应在同一个变更中完成对应注册、实现、测试、文档和 CI 校验。

> `scripts/check_capability_registry.py` 是 `scripts/check_registry_consistency.py` 的兼容别名，两者行为一致。

## 2. 新增 Agent Tool

1. 在 `server/app/agent_runtime/catalog.py` 的 `TOOL_CATALOG` 增加 `ToolSpec`。
2. 实现对应的 `/internal/agent/tools/*` 路由，并接入 `_tool_fence` / Tool Gateway。
3. 在 `agent_runtime/pi-sidecar/src/tools.mjs` 的 `ALLOWED_TOOL_NAMES` 和 fallback 工具列表中加入同名工具。
4. 补充权限边界、错误路径和契约测试。
5. 运行：
   ```bash
   python scripts/check_registry_consistency.py
   python scripts/run_agent_strategy_matrix.py --matrix benchmarks/agent_experiments/matrix.json --validate-only
   ```
6. 更新 `docs/agent-tool-catalog.md` 和本文档的工具列表。

工具元数据只是发现契约，不能用来提升权限。服务端必须再次根据本地注册表和 `RuntimePolicy` 做最终判定。

## 3. 新增 DiagnosticStrategy

1. 在 `server/app/diagnosis/strategies/` 下新建策略文件，继承或实现 `BaseDiagnosticStrategy` / `DiagnosticStrategy` Protocol。
2. 实现 `build_directive`、`plan_initial_probes`、`select_next_probes`、`should_stop`、`render_prompt_guidance`。
3. 在 `server/app/diagnosis/strategies/registry.py` 的 `STRATEGY_REGISTRY` 注册实例。
4. 在 `server/app/diagnosis/strategies/__init__.py` 导出（如需）。
5. 补充策略单元测试和矩阵 condition。
6. 更新 `docs/agent-runtime-experiments.md` 或 `docs/agent-strategy-matrix.md`。

策略必须来自注册表，模型不能自由声明 `strategy_id`。每个策略应同时支持 deterministic 与 Pi 两条路径。

## 4. 新增 RuntimeOptions / RuntimePolicy 字段

1. 在 `server/app/agent_runtime/options.py` 或 `policy.py` 增加字段和校验。
2. 在 `server/app/agent_runtime/port.py`、`server/app/diagnosis/schemas.py`、`server/app/v6_routes.py` 透传。
3. 在 `agent_runtime/pi-sidecar/src/runtime.mjs` 的 context / prompt / tool envelope 中使用。
4. 补充测试：默认值兼容旧行为、非法值被拒绝、权限只能缩小。
5. 更新 `docs/runtime-policy.md` / `docs/agent-runtime-experiments.md`。

注意：`RuntimePolicy` 中 `allow_arbitrary_command` 永远不允许；`auto_approve` 只允许实验模式，且不能移除 R3 审批。

## 5. 新增 Probe / Collector / TaskKind / QueryOperation

1. 在 `server/app/task_kinds.py` 增加 TaskKind。
2. 在 Worker 的 `COLLECTORS` 增加对应 Collector。
3. 在 `server/app/diagnosis/probe_registry.py` 增加 Probe（引用 TaskKind）。
4. 在 `server/app/diagnosis/query_registry.py` 增加 QueryOperation（引用 Collector）。
5. 在 `server/app/diagnosis/evidence_contracts.py` 增加 EvidenceContract（引用 Probe）。
6. 运行 `python scripts/check_registry_consistency.py`，确保所有交叉引用闭合。

## 6. 运行检查

```bash
# 注册表一致性（TaskKind/Collector/Probe/EvidenceContract/QueryOperation/Tool Catalog/Sidecar）
python scripts/check_registry_consistency.py

# 策略矩阵配置校验
python scripts/run_agent_strategy_matrix.py --matrix benchmarks/agent_experiments/matrix.json --validate-only

# 后端测试
python -m pytest -q
```

CI 已在 `release-baseline` 工作流中执行这些检查。新增能力时如果 CI 报漂移，优先检查是否漏注册或漏同步 Sidecar 白名单。

## 7. 文档更新清单

- 新增/修改 Tool：`docs/agent-tool-catalog.md`
- 新增/修改策略：`docs/agent-runtime-experiments.md`、`docs/agent-strategy-matrix.md`
- 新增/修改权限字段：`docs/runtime-policy.md`
- 新增/修改模型参数：`docs/agent-runtime-experiments.md`
- 新增/修改注册表：`docs/registry-consistency.md`

保持“一个变更同时更新代码、测试、注册表和文档”，是 Mini-Drop 扩展的最低要求。
