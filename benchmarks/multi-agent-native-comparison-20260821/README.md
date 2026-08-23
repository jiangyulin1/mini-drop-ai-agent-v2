# Multi-Agent Native Comparison

这是 Mini-Drop 运维 Agent 多赛道评测的精简、可复现交付包。

## 内容

- `benchmark/cases/public/`：9 个公开案例描述，不包含 PR 标题、修复提交和私有答案。
- `benchmark/cases/replay/`：从真实 PR/Issue/Review 派生的结构化回放证据。
- `benchmark/interventions/`：case-07/08/09 的专家干预定义。
- `benchmark/agent-contract-v1.json`：统一输入、工具、输出和审计契约。
- `benchmark/adapters/`：Mini-Drop、HolmesGPT、smolagents、ITOps、K8sGPT 的运行适配入口。
- `benchmark/run_native_*.py`：原生运行入口；需要相应 Agent 源码、服务和运行环境。
- `benchmark/score_runs.py`：使用私有 Oracle 评分。公开 GitHub 包不提供私有 Oracle。
- `benchmark/native_audit.py`：原生来源、哈希和可比性审计。
- `prompts/`：从案例构建到原生可比性验收的完整流程提示词。
- `comparisons/`：当前结果摘要、赛道对比和审计摘要。

## 测试规模

- 9 个案例，4 个主榜 Agent，各 3 次重复，共 108 次主榜运行。
- K8sGPT 仅执行 case-06 Kubernetes 专项，3 次真实 kind 集群运行。
- C7/C8/C9 含专家干预和二轮重新评估。

## 推荐执行顺序

```bash
python benchmark/preflight.py
python benchmark/run_native_minidrop_pi.py
python benchmark/run_native_holmesgpt.py
python benchmark/run_native_smolagents.py
python benchmark/run_native_itops_http.py
python benchmark/run_native_k8sgpt_real.py
python benchmark/score_runs.py
python benchmark/native_audit.py
python benchmark/generate_reports.py
```

不要把 API Key 写入脚本、manifest、日志或提交。通过环境变量或本地未跟踪配置注入。

## 结果口径

当前结果显示：smolagents/HolmesGPT 的单轮诊断推理领先；Mini-Drop 的 Evidence 引用链路已明显改善，并在持续 Case、Evidence 生命周期和专家协作方面形成差异化。Mini-Drop C7/C8/C9 的当前正确修订为 3/3、0/3、3/3，不能表述为 9/9 hard gate 全部通过。

公开包不包含完整 `benchmark/runs-native/`、虚拟环境、`node_modules`、Agent 源码快照、Kubeconfig、服务日志或私有 Oracle。完整内部归档应使用桌面上的私有交付包。
