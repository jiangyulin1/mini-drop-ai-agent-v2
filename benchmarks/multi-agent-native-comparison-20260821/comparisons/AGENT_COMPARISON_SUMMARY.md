# Agent 对比汇总（当前数据版）

> 数据来源：`benchmark/runs-native/**/score.json`、`comparisons/NATIVE_AUDIT.json`、K8sGPT case-06 工件。四个主榜 Agent 各 9 个案例 × 3 次，共 108 次；K8sGPT 为 case-06 原生专项 3 次。该文件按当前工件重新汇总，不替代最终验收文件。

## 一、参与 Agent 与运行状态

| Agent | 原生运行 | 严格可比 | 运行入口/环境 | 当前定位 |
|---|---:|---:|---|---|
| Mini-Drop | 27/27 | 27/27 | Case/Evidence/Agent Runtime + 官方 Pi Sidecar | 证据治理、持续调查、专家协作 |
| HolmesGPT | 27/27 | 27/27 | `ToolCallingLLM.call()` + 源码快照 | Kubernetes/运维诊断推理 |
| smolagents | 27/27 | 27/27 | `ToolCallingAgent.run()` | 工具调用和诊断推理基线 |
| ITOps Agent Platform | 27/27 | 27/27 | HTTP backend + MCP gateway | 平台化 Agent、工具编排 |
| K8sGPT | 3/3 专项 | 3/3 真实 kind | `k8sgpt analyze` | Kubernetes 专项，不纳入通用主榜 |

## 二、A 赛道：诊断推理

四个主榜 Agent 的平均分，范围为 0 到 1，越高越好。

| Agent | Root match | Mechanism match | Evidence validity | Abstention | 判断 |
|---|---:|---:|---:|---:|---|
| Mini-Drop | 0.185 | 0.593 | 0.963 | 0.889 | 证据引用已明显修复，根因定位仍弱 |
| HolmesGPT | 0.889 | 0.667 | 1.000 | 0.889 | 根因定位和证据引用领先 |
| smolagents | 0.889 | 0.741 | 1.000 | 0.889 | 当前综合诊断推理最强 |
| ITOps | 0.519 | 0.370 | 0.759 | 0.593 | 中等，机制识别和拒答稳定性不足 |

### A 赛道结论

- smolagents 在机制匹配上最高，HolmesGPT 与 smolagents 的根因定位相同。
- Mini-Drop 的 Evidence validity 已接近领先 Agent，说明之前的低分主要包含 Evidence ID/输出链路损失。
- Mini-Drop 的 Root match 仍明显落后，后续应优先优化根因位置判定和最终结构化答案稳定性。

## 三、B 赛道：证据治理与专家干预

这里仅统计 C7/C8/C9 的互动案例；`结论修订正确`要求干预后重新评估并形成符合 Oracle 的结果。

| Agent | 干预可观察 | 结论修订正确 | 排除证据复用 | 盲从专家 | 证据缺口识别 |
|---|---:|---:|---:|---:|---:|
| Mini-Drop | 9/9 | **6/9** | 0/9 | 0/9 | 2/9 |
| HolmesGPT | 9/9 | 6/9 | 0/9 | 0/9 | 9/9 |
| smolagents | 9/9 | 6/9 | 0/9 | 0/9 | 9/9 |
| ITOps | 9/9 | 7/9 | 0/9 | 0/9 | 8/9 |

### Mini-Drop 分案例

| 案例 | 结论修订 | 当前观察 |
|---|---:|---|
| case-07 | 3/3 | 排除错误时间/目标证据后能降低结论范围 |
| case-08 | 0/3 | 能读取保留节点证据，但最终结构化修订仍不稳定 |
| case-09 | 3/3 | 排除 RSS 相关性后能转向队列/保留证据，2/3 能识别缺口 |

### B 赛道结论

Mini-Drop 已经证明了“证据状态变更 + 二轮读取”的产品链路，但目前不能宣称 9/9 互动 hard gate 通过。它的特色能力已经存在，稳定性主要受 case-08 的最终结论生成影响。

## 四、C 赛道：采集规划与资源约束

这些是当前工件记录的描述性统计，不应直接当作跨框架资源效率排名，因为各 Agent 的工具埋点粒度仍不同。

| Agent | 平均工具调用 | 平均返回字节 | 覆盖缺口 | 解释 |
|---|---:|---:|---:|---|
| Mini-Drop | 1.000 | 3,274.1 | 0/27 | 外层调用轻量，但仍有 Collector Runtime fallback 记录 |
| HolmesGPT | 0.333 | 0.0 | 0/27 | 当前工件没有可比的返回字节统计 |
| smolagents | 16.037 | 64.1 | 0/27 | 工具轨迹最细，调用次数不能直接与外层 Agent turn 比较 |
| ITOps | 1.000 | 1,316.1 | 0/27 | HTTP 请求级统计，和 Pi 内部工具粒度不同 |

### C 赛道结论

Mini-Drop 的外层运行开销较低，但“低调用”不能等同于“采集效率最高”。后续应统一按实际 Tool Call、Tool Result 字节、Evidence 数量和总耗时重新计量。

## 五、D 赛道：原生产品能力

| 能力 | Mini-Drop | HolmesGPT | smolagents | ITOps |
|---|---|---|---|---|
| 持续 Case 状态 | 强 | 弱/未作为主能力验证 | 弱/未作为主能力验证 | 中 |
| Evidence 生命周期与信任 | 强，已完成排除重跑 | 依赖工具/环境 | 依赖 Agent 工具 | 中，支持后端治理 |
| 专家干预后二轮推理 | 已实现，6/9 修订正确 | 6/9 | 6/9 | 7/9 |
| 工具调用框架 | 官方 Pi Sidecar | Holmes ToolCallingLLM | ToolCallingAgent | HTTP + MCP |
| 审计和权限边界 | Case、Evidence、风险策略 | 工具执行器 | Agent 工具集合 | 平台工具网关 |

### D 赛道结论

Mini-Drop 的差异化不在当前单轮诊断分数，而在持续调查、Evidence 治理、专家干预和审计边界。当前这些能力已经有运行证据，但 case-08 稳定性和 Collector Runtime 配置仍需继续完善。

## 六、E 赛道：Kubernetes 专项

| Agent | 原生真实集群运行 | 真实对象发现 | case-06 根因机制命中 | 结论 |
|---|---:|---:|---:|---|
| K8sGPT | 3/3 kind | 3/3 发现 `workload-benchmark` Deployment 异常 | 0/3（当前评分） | 能发现对象健康异常，但未证明恢复应用级 full-sync 机制 |

K8sGPT 的真实 kind 执行和对象快照是有效证据，但不能把“发现 Deployment 不可用”扩展为“识别了案例根因”。

## 七、综合判断

| 方向 | 当前领先者/特点 | Mini-Drop 当前情况 |
|---|---|---|
| 单轮诊断推理 | smolagents、HolmesGPT | 机制理解中等，根因定位偏弱 |
| 有效 Evidence 引用 | HolmesGPT、smolagents；Mini-Drop 已接近 | 0.963，链路修复收益显著 |
| 专家干预稳定性 | ITOps 略高；其他 Agent 6/9 | 6/9，case-07/09 已通过，case-08 仍需修复 |
| 持续调查与治理 | Mini-Drop 的主要差异化 | 产品链路最完整，但 Collector Runtime 尚有 fallback |
| Kubernetes 对象诊断 | K8sGPT 专项 | 不适合与通用 Agent 综合排名 |

## 八、发布口径

可以发布：

> 在当前统一案例和原生运行条件下，smolagents/HolmesGPT 的单轮诊断推理领先；Mini-Drop 的 Evidence 引用链路已显著改善，并在持续 Case、证据生命周期和专家协作方面形成差异化能力。

暂不应发布：

> Mini-Drop 在所有诊断或专家干预赛道全面领先，或 9/9 互动 hard gate 已全部通过。

本汇总不改变最终验收状态。由于 `FINAL_REPORT.md`、`agent-matrix.md` 仍存在旧文案，且安全配置文件仍需清理，正式对外交付前仍应完成报告同步和密钥轮换。
