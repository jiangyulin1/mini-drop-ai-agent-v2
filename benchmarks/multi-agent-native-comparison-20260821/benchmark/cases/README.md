# 案例包约定

每个案例目录建议包含：

```text
public/case-XX.json      # 匿名症状、范围、L0/L1 摘要、可用查询类型
replay/case-XX.json      # 受统一服务投影的派生证据，不含原始大文件
interventions/case-XX.json # 状态触发的专家事件
private-oracles/case-XX.json # 仅评分端读取，不进入 Agent 上下文
```

证据生命周期使用逻辑状态，不物理删除：`ACTIVE`、`EXCLUDED`、`INVALID`、`SUPERSEDED`。专家变更必须产生 revision、原因、操作者和影响预览。

每个案例至少准备一个干扰证据和一个缺失证据。干扰证据用于测反证处理；缺失证据用于测 Agent 是否知道不能下确定结论。
