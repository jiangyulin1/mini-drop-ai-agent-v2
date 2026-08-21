# Registry Consistency

Mini-Drop 的能力由多个明确注册表组成：Control 的 TaskKind/Probe/EvidenceContract/QueryOperation/Agent Tool，以及 Worker 的 Collector 和 Sidecar 兼容白名单。

运行：

```bash
python scripts/check_registry_consistency.py
```

检查器会验证：

- 每个 TaskKind 都有 Worker Collector；
- Probe 的 runner task kind 同时存在于 TaskKind 和 Collector；
- QueryOperation 引用的 Collector 存在；
- EvidenceContract 和 PROBE_FACTS 引用的 Probe 存在；
- Canonical Agent Tool Catalog 与 Sidecar 兼容白名单完全一致。

CI 在 Windows/Linux 后端矩阵中执行该检查。任何漂移都以退出码 2 阻止合并。注册表只表达“实现存在”；目标节点是否声明 capability 仍由实时 Agent heartbeat 决定，未声明能力不是系统错误。
