# Mini-Drop MCP 集成

Mini-Drop 使用 MCP 2026-07-28 作为 AI 能力接入协议。MCP 是边界协议，不能替代
Mini-Drop 的租户隔离、Grant、Capability Token、EvidenceEnvelope、探针审批或动作策略。

## 能力边界

Mini-Drop MCP Server 暴露：

- Resources：Incident Case、Diagnosis Session、单条 Evidence；
- READ Tools：Case 列表、诊断读取、注册数据源查询、能力目录；
- COLLECT Tools：启动注册诊断流程。R2 探针仍进入原有审批状态；
- CHANGE 辅助 Tools：确定性动作评估和 dry-run。

MCP Server **不暴露动作 execute/rollback 工具**。生产变更只能通过 Mini-Drop Web/API
审批链执行，MCP 模型调用不能被解释为用户批准。

## 安装与启动

MCP SDK 2.x 要求 Python 3.10+。核心 Mini-Drop 仍兼容 Python 3.9，因此 MCP 是可选依赖：

```bash
python -m pip install -e '.[mcp]'
```

本地 MCP Host 使用 stdio：

```bash
export MINI_DROP_MCP_AUTH_ENABLED=0
export MINI_DROP_MCP_TRANSPORT=stdio
make mcp
```

Docker Compose 可选 profile：

```bash
MINI_DROP_MCP_TOKEN="$(openssl rand -hex 32)" docker compose --profile mcp up -d mcp
```

远程 Streamable HTTP：

```bash
export MINI_DROP_MCP_TRANSPORT=streamable-http
export MINI_DROP_MCP_HOST=127.0.0.1
export MINI_DROP_MCP_PORT=8192
export MINI_DROP_MCP_AUTH_ENABLED=1
export MINI_DROP_MCP_TOKEN="$(openssl rand -hex 32)"
export MINI_DROP_MCP_ISSUER_URL=http://localhost:8192
export MINI_DROP_MCP_RESOURCE_URL=http://localhost:8192/mcp
make mcp
```

远程部署应把服务置于 TLS 网关之后，并把两个公开 URL 配置为最终 HTTPS 地址。

## 连接外部 MCP 数据源

外部 MCP Server 通过 `MINI_DROP_MCP_CONNECTORS_JSON` 声明。只支持 Streamable HTTP，
非回环地址强制 HTTPS，URL 禁止内嵌用户名或密码：

```json
[
  {
    "source_id": "ops-observability",
    "name": "Operations observability",
    "url": "https://mcp.example.com/mcp",
    "operations": {
      "metrics.query": "query_metrics",
      "logs.search": "search_logs"
    },
    "resource_dimensions": ["cluster_id", "service_id"],
    "data_classes": ["operational_metric", "log_pattern"],
    "token_env": "OPS_MCP_TOKEN",
    "timeout_sec": 20,
    "max_result_bytes": 1048576
  }
]
```

`token_env` 是环境变量名，不是 Token 本身。Connector 配置和 `/api/v1/mcp/status` 都不会
返回凭据。配置加载后，还必须像内置信息源一样创建匹配的 AuthorizationGrant；否则
调用会得到 `SOURCE_APPROVAL_REQUIRED`。

外部工具统一接收：

```json
{
  "resource": {"cluster_id": "prod-a", "service_id": "checkout"},
  "parameters": {"window": "5m"},
  "case_id": "case_...",
  "requested_time_range_minutes": 15
}
```

返回值进入 SourceGateway 后才会变成 EvidenceEnvelope。调用路径依次执行：

```text
MCP tool discovery/call
  -> Source Registry
  -> tenant/principal Grant policy
  -> short-lived Capability Token
  -> result size limit and sensitive-field redaction
  -> content/projection hash
  -> EvidenceEnvelope and audit record
```

## 运维检查

- `GET /api/v1/sources`：内置及外部 MCP 信息源目录；
- `GET /api/v1/mcp/status`：MCP Server 与 Connector 的脱敏配置状态；
- MCP Inspector：连接 `http://127.0.0.1:8192/mcp` 检查 Tools/Resources/Prompts；
- `pytest tests/test_mcp_integration.py`：协议 facade、适配、授权、脱敏测试。

所有 MCP 返回内容都视为不可信数据。外部 Tool 描述不能修改 Mini-Drop 的策略、权限、
预算或证据约束；工具错误也不会回显 Token。
