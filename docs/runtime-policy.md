# RuntimePolicy 运行权限策略

`RuntimePolicy` 是单次 Agent Turn / 实验条件可传入的“运行级权限配置”。它和 `RuntimeOptions` 分离：`RuntimeOptions` 控制模型与思考成本，`RuntimePolicy` 控制工具、操作、风险和命令执行边界。

核心原则：**请求只能缩小权限，不能扩大权限**。服务端代码始终是最终权限边界。

## 1. 字段与默认值

| 字段 | 默认值 | 说明 |
|---|---|---|
| `side_effect_policy` | `AUTO_READ_LOW` | `READ_ONLY` / `PROPOSE_ONLY` / `AUTO_READ_LOW` |
| `enabled_tools` | `null` | 显式工具白名单；为 `null` 表示不额外限制 |
| `disabled_tools` | `[]` | 显式禁用的工具名 |
| `enabled_operations` | `null` | 显式采集操作白名单；为 `null` 表示不额外限制 |
| `allowed_risk_levels` | `["R0","R1"]` | 允许的操作风险级别，实验不能超过 R1 |
| `execution_mode` | `normal` | `normal` / `dry_run` / `sandbox` / `deny_write` |
| `auto_approve` | `false` | 实验模式可自动批准部分动作；R3 仍不可绕过 |
| `require_approval_for` | `["R2","R3"]` | 需要人工审批的风险级别 |
| `allow_arbitrary_command` | `false` | 永远不允许任意命令，代码强制拒绝 |

## 2. 权限边界

- `enabled_tools` / `disabled_tools` 只能引用 `TOOL_CATALOG` 中已注册的工具；未注册工具直接报 `UNREGISTERED_RUNTIME_TOOLS`。
- `allowed_risk_levels` 只能是 `R0` / `R1` 的子集；请求不能扩大到 `R2` / `R3`。
- `allow_arbitrary_command=True` 会被 `RuntimePolicy` 校验直接拒绝，因为 Mini-Drop 不提供任意 Shell/命令能力。
- 即使 `auto_approve=True`，`R3` 审批要求也不能被移除。
- `execution_mode` 只会让执行更严格：`deny_write` 拒绝写操作，`dry_run` / `sandbox` 不落真实执行器。

## 3. 与 Tool Gateway 的关系

Pi Sidecar 每次工具调用都会携带 `runtime_policy`。服务端 `_tool_fence` 重新解析策略并检查：

1. 工具是否在 `TOOL_CATALOG` 注册；
2. 策略本身是否合法；
3. 工具是否在 `effective_tools()` 内；
4. 操作风险是否在 `allowed_risk_levels` 内；
5. 是否命中人工审批要求。

Sidecar 拉取到的 Tool Catalog 只是发现元数据，不能提升权限。即使修改 Sidecar 本地白名单，也不能调用服务端未注册工具。

## 4. 示例

### 只读实验

```json
{
  "side_effect_policy": "READ_ONLY",
  "execution_mode": "deny_write",
  "allowed_risk_levels": ["R0", "R1"]
}
```

### 窄工具集 + 只读

```json
{
  "side_effect_policy": "READ_ONLY",
  "enabled_tools": ["get_case_snapshot", "get_evidence_projection", "list_case_evidence"]
}
```

### 提案 + dry-run

```json
{
  "side_effect_policy": "PROPOSE_ONLY",
  "execution_mode": "dry_run"
}
```

## 5. 在 API 中使用

`POST /api/v1/cases/{case_id}/agent/turn` 请求体可增加可选 `runtime_policy`：

```json
{
  "message": "请定位 checkout 延迟",
  "runtime_policy": {
    "side_effect_policy": "READ_ONLY",
    "execution_mode": "deny_write"
  }
}
```

不传时保持默认 `AUTO_READ_LOW + normal`，兼容旧行为。

## 6. 扩展指引

新增工具、操作或风险级别时，必须同步修改：

- `server/app/agent_runtime/catalog.py`：ToolSpec；
- `server/app/agent_runtime/policy.py`：若引入新的风险级别或权限类别；
- `server/app/diagnosis/v6_policy.py`：Tool Gateway 判定；
- `agent_runtime/pi-sidecar/src/tools.mjs`：Sidecar 兼容白名单；
- 对应测试与 `scripts/check_registry_consistency.py`。

不要只改 Sidecar 或 API 文档，服务端权限边界必须始终由代码决定。
