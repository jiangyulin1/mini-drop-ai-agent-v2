# Agent 参与矩阵

| Agent | 统一回放附录 | Native 工件 | 严格主榜 | 当前运行类别 | 主要限制 |
|---|---:|---:|---:|---|---|
| Mini-Drop | 是 | 27/27 | 0/27 | native-adapted-runtime | 入口为 benchmark adapted loop，未证明完整 Case/Evidence/Agent Runtime |
| HolmesGPT | 是 | 27/27 | 0/27 | pypi-runtime-source-mismatch | 实际包为 PyPI 0.34.0，未闭合参与矩阵中的源码 SHA |
| smolagents | 是 | 27/27 | 0/27 | native-runtime | framework tool hash 未与其他 Agent 统一 |
| ITOps Agent Platform | 是 | 27/27 | 0/27 | native-runtime | tool/model contract 需继续与 canonical contract 闭合 |
| K8sGPT | 专项 | 3/3 | 0/3 real cluster | simulated-cluster | 使用 fake Kubernetes API；不进入通用主榜 |

## 公平性规则

1. 主榜不允许访问 GitHub、PR URL、修复提交、原始文件系统和原始日志。
2. 所有 Agent 使用 benchmark-owned 的证据 schema 和查询结果格式。
3. 需要深度 profile 的证据必须能通过统一工具获取；原生工具额外发现的数据只能进入原生附录。
4. 工具调用、输入证据、模型、系统 prompt 和输出 schema 固定；运行速度只记录，不参与主分。
5. 采集器缺失记录为 `collector_coverage_gap`，与 `causal_reasoning_miss` 分开。
