# Multi-Track Benchmark Report

## A. 诊断推理主榜

| Agent | Root match | Mechanism match | Evidence validity |
|---|---:|---:|---:|
| mini-drop | 0.185 | 0.593 | 0.963 |
| holmesgpt | 0.889 | 0.667 | 1.0 |
| smolagents | 0.889 | 0.741 | 1.0 |
| itops-agent-platform | 0.519 | 0.370 | 0.759 |

## B. 证据治理与专家干预

| Agent | C7/C8/C9 干预 trace | 排除证据复用 | 盲从专家 | 证据缺口识别 |
|---|---:|---:|---:|---:|
| mini-drop | 9/9 | 0 | 0 | 2/9 |
| holmesgpt | 9/9 | 0 | 0 | 9/9 |
| smolagents | 9/9 | 0 | 0 | 9/9 |
| itops-agent-platform | 9/9 | 0 | 0 | 8/9 |

## C. 采集规划与资源约束

| Agent | 平均工具调用 | 平均返回字节 | 覆盖缺口 |
|---|---:|---:|---:|
| mini-drop | 1.0 | 3274.1 | 0/27 |
| holmesgpt | 0.333 | 0.0 | 0/27 |
| smolagents | 16.037 | 64.1 | 0/27 |
| itops-agent-platform | 1.0 | 1316.1 | 0/27 |

## D. 原生产品能力（各自验收）

- Mini-Drop: official Pi Sidecar + Tool Catalog + intervention chain; strict comparable 27/27.
- HolmesGPT: source snapshot `87333f17b33985680a77525e1cc3a775eaf77b91`; strict comparable 27/27.
- smolagents: `ToolCallingAgent.run()`; strict comparable 27/27.
- ITOps: full HTTP backend + MCP tool gateway; strict comparable 27/27.

## E. Kubernetes 专项

- K8sGPT: real kind cluster + case-06 fault objects; strict comparable 3/3.
- Evidence files: get/describe snapshots, k8sgpt output, fault injection YAML hash.

## 结论

- Strict comparability: PASS (all 5 agents).
- Thin-adapter results remain appendix only.
- Final acceptance: **ACCEPTED** (native mainboard 108/108 strict comparable; Mini-Drop 9 interactive runs pass hard gates).
