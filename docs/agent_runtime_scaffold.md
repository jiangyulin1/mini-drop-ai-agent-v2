# Mini-Drop 对话式 Agent Runtime 脚手架

> 实现基线：2026-08-13。对应 `case-agent-turn.v1`，用于把既有 Case、证据图、探针、MCP、授权和恢复闭环串成一个可持续对话入口。

## 1. 本次补齐的断层

此前 Case 已能保存对话，诊断内核也能采集和推理，但 Web 的“发送并分析”需要客户端自行串联留言、修正 Case、重启诊断，服务端没有统一的 Agent 回合语义。现在新增：

- `POST /api/v1/cases/{case_id}/agent/turn`：自然语言回合入口；
- 意图：`investigate / explain / correct / deployment_assessment / status`；
- 输出：用户答复、决策摘要、证据引用、反证、局限、下一步和工具调用状态；
- 工具：只能从 `SourceRegistry` 选择，通过 `SourceGateway` 执行；外部 MCP 连接器自动继承租户、Case scope、Grant、Capability Token、脱敏和结果预算；
- 部署预测：基于显式可分配容量和带安全余量的资源需求，输出 `ready / conditional / not_ready / insufficient_data`。

## 2. 可审计推理，不记录私有思维链

系统不要求模型输出或保存隐藏思维链。可供用户追问与纠错的是：

1. 当前假设与状态；
2. 支持证据引用和内容哈希；
3. 反证与缺失证据；
4. 工具选择理由、授权结果和调用状态；
5. 最终结论、局限和下一步。

这些字段构成可验证的“决策链 + 证据链”，用户纠正后触发新的 Case scope revision，旧诊断失效。

## 3. Agent 回合示例

```json
POST /api/v1/cases/case_x/agent/turn
{
  "message": "部署 3 个副本，每个 CPU 2 核、内存 4GB、磁盘 20GB，生产集群能承载吗？",
  "execute_safe_tools": true,
  "max_tool_calls": 4
}
```

如果 `MINI_DROP_MCP_CONNECTORS_JSON` 注册了包含 `capacity`、`inventory`、`deployment` 或 `resource` 操作名的 MCP 只读源，运行时会优先选它；没有匹配 Grant 时返回 `tool_approval_required`，不会绕过审批。若没有 `deployment_inventory` 或 MCP 返回的显式 allocatable 容量，则返回 `insufficient_data`，不会拿瞬时 CPU 利用率冒充容量。

也可以直接传结构化需求，避免自然语言单位歧义：

```json
{
  "message": "评估生产部署承载力",
  "intent": "deployment_assessment",
  "deployment_requirements": {
    "replicas": 3,
    "cpu_cores_per_replica": 2,
    "memory_mb_per_replica": 4096,
    "disk_mb_per_replica": 20480,
    "safety_margin_ratio": 0.2
  }
}
```

## 4. 容量数据契约

目标范围可暂时携带以下数据；生产接入推荐由 CMDB/Kubernetes Capacity MCP 提供同构投影：

```json
{
  "deployment_inventory": [
    {
      "node_id": "worker-1",
      "schedulable": true,
      "allocatable_cpu_cores": 8,
      "allocatable_memory_mb": 16384,
      "allocatable_disk_mb": 102400
    }
  ]
}
```

当前判定是保守的一节点一副本可放置性检查。反亲和、Namespace quota、PDB、依赖容量、峰值预测和装箱优化仍应由后续专门的部署规划器补齐；这些能力缺失时会出现在 `assumptions` 或 `limitations`，不会伪装成已验证。

## 5. 后续演进边界

- 将运行时的确定性意图分类升级为严格 JSON Schema 的模型 Planner，但模型输出仍必须经注册表和策略校验；
- 将工具投影转换为正式 `EvidenceEnvelope` 持久证据节点，纳入假设图的支持/反证边；
- 为 Agent 回合建立 Durable Job/Outbox，支持耗时 MCP 与探针异步回填；
- 增加 7/90 天基线、负载预测、Kubernetes 调度约束和容量仿真；
- 将恢复报告、部署评估和用户纠错纳入统一评测集。
