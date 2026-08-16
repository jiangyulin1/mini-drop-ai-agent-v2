# AI Agent 交互降载与可用性设计

> 历史辅助说明：规范性产品与验收合同已迁移到 `docs/ai_agent_feature_complete_demo_prompt_v6.md` 第 9 节。本文不得用于推迟 UI 验收，也不得把默认界面只展示一个当前动作解释为后端固定采集顺序。冲突时以 v6 为准。

## 1. 用户只做一个决定

调查入口只保留两个动作：

```text
1. 描述现象
2. 选择：
   - 先解释（只读）
   - 自动定位（创建 Case 并推进）
```

不要默认展示：

- PID
- Collector
- Plan Step
- MCP
- Evidence ID
- Runtime 术语

## 2. 主界面只显示四块状态

### 问题摘要

用一句自然语言表达：

```text
checkout 服务延迟升高，正在检查宿主资源基线。
```

### AI 正在做什么

只显示当前唯一动作：

```text
下一步：采集 checkout 宿主系统指标
```

这消费 Workspace 的当前优先动作投影；后端仍可维护多个候选并根据新证据自适应改变方向。

### 证据是否充分

```text
证据充足 / 仍缺少证据 / 无法确认
```

不展示原始 Evidence JSON。

### 用户是否需要参与

```text
无需操作 / 需要补充范围 / 需要批准高风险采集
```

## 3. 复杂操作折叠到专家模式

默认隐藏：

- Plan Revision
- Campaign Assignment 矩阵
- Query Operation 目录
- Tool Trace
- Evidence Raw
- Coverage

专家模式只多一个开关，不新开页面。

## 4. 交互逻辑降载

- 默认视图突出一个 active action 和一个 next action；专家模式可展开完整 Plan，界面降载不限制 Agent 的自适应推理
- 重复问题显示“与上次结论一致”徽标，不重新渲染长篇回答
- `ANSWER_ONLY` 时禁用所有采集按钮
- 只有 `risk != READ_LOW` 或目标不明确时才弹确认
- 刷新/断线后恢复消息时间线和当前唯一状态卡，不重新生成卡片
- 空状态、失败、取消、降级、等待中分别有固定文案，不显示空白

## 5. 任务双入口

AI 创建的原生 Task 出现在第一页任务列表，增加：

```text
来源：AI 调查
Case：checkout 延迟
风险：READ_LOW
状态：进行中/已完成/已取消
```

用户可从第一页直接取消，结果同步回 Case。

## 6. 文案统一

不暴露内部词，使用用户语言：

| 内部 | 用户界面 |
|---|---|
| Collector | 采集方式 |
| Evidence | 依据 |
| InvestigationPlan | 诊断步骤 |
| Runtime | 后台诊断引擎 |
| READ_LOW | 低风险自动执行 |
| abstain | 暂时无法确认 |

## 7. 可访问性底线

- 核心操作可键盘完成
- 状态不只靠颜色表达
- 加载/错误/空数据有文本
- 专家模式不破坏默认模式的单卡片布局

## 8. 后端为上述设计提供的支撑

- Workspace Snapshot 提供当前优先动作投影，确定性策略只约束范围、风险、预算和合法性
- `side_effect_delta` 提供解释不误采集断言
- 默认用户视图只突出一个主下一步；领域状态可以保留多个候选与有序 Plan
- `runtime_turn_accepted / runtime_unavailable` 可映射为固定状态文案
- 第一页 Task options 已带 `source=query_gateway` / `case_id`
