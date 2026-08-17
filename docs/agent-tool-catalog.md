# Agent Tool Catalog

Mini-Drop 的 Agent 工具目录以 `server/app/agent_runtime/catalog.py` 为唯一规范源。它描述工具名、参数 JSON Schema、内部路由和权限类别；目录是发现元数据，不是授权凭证。

## 调用链

1. Pi Sidecar 启动或刷新 Case Session 时，通过带内部令牌的 `GET /internal/agent/tools/catalog` 拉取目录。
2. Sidecar 校验版本、工具集合和内部路由前缀，仅注册本地兼容白名单中的工具。
3. 拉取失败时保留上次已校验目录；无缓存时降级到随版本发布的最小兼容目录。
4. 每次工具调用都携带 Case revision、runtime generation、诊断策略和 RuntimePolicy。
5. Control 的 Tool Gateway 重新查找本地 ToolSpec、检查 Case/租户/版本/策略，再进入确定性服务。

因此，修改 Sidecar 返回值、模型参数或目录响应都不能增加服务端权限。生产模式不提供 shell、任意命令、文件读写或原始 Pi RPC。

## 增加工具

在同一个变更中完成：

- 在 `TOOL_CATALOG` 增加 ToolSpec；
- 实现并测试对应 `/internal/agent/tools/*` 路由；
- 将工具名加入 Sidecar 兼容白名单；
- 运行 `python scripts/check_registry_consistency.py`；
- 增加权限边界、错误路径和契约测试。

工具应优先属于 `READ_ONLY` 或 `PROPOSE_ONLY`。执行类能力必须进入原生审批、fencing、幂等、验证和回滚链路，不能直接由模型执行。
