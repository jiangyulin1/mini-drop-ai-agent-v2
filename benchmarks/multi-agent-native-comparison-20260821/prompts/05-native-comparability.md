# 原生 Agent 横向对比执行与验收提示词

## 目标

将当前 `THIN_ADAPTER_ONLY` 结果升级为可交付的“多 Agent 原生能力横向对比”。
本轮运行必须由各上游 Agent 的真实运行时负责对话、工具选择、干预处理和最终输出；benchmark-owned replay 只作为统一的本地证据后端，不能替代上游 Agent runtime。

## 绝对门槛

每一次正式运行的 `manifest.json` 必须包含并满足：

```json
{
  "adapter_mode": "native",
  "native_runtime": true,
  "framework_entrypoint": "<real upstream entrypoint>",
  "framework_source_sha": "<pinned source SHA>",
  "dependency_lock_hash": "sha256:<hash>",
  "native_trace_hash": "sha256:<hash>",
  "model_identifier": "deepseek-v4-flash",
  "common_contract_hash": "<sha256 of benchmark/agent-contract-v1.json>",
  "tools_hash": "<framework serialization hash; record separately>"
}
```

以下任一项不满足，该 Agent 只能进入 `thin_adapter_appendix`，不能进入原生主榜：

- 运行入口只是 benchmark 自己的 HTTP/LLM wrapper；
- 源码目录没有被真实 import、启动或执行；
- tool trace 由 benchmark 脚本伪造，而非由上游 Agent runtime 产生；
- `common_contract_hash` 缺失或与 `benchmark/agent-contract-v1.json` 不一致；
- `tools_hash`、model config hash 或 system prompt hash 与其他主榜 Agent 不一致；
- 运行时无法接受同一 system prompt、模型、工具 schema 和 public case；
- 原生框架私自访问 GitHub、shell、文件系统、外部日志或 remediation；
- manifest 没有可复核的进程启动命令、版本、依赖锁和 native trace。

## 统一条件

- 主榜 Agent：Mini-Drop、HolmesGPT、smolagents、ITOps Agent Platform（ITOps 先通过 headless smoke）。
- 专项 Agent：K8sGPT 只运行 `case-06`，不把其他 8 个 case 记为 0 分。
- 模型固定为 `deepseek-v4-flash`，temperature=0，固定 max tokens 和 context budget。
- 只使用匿名 `case-01` 到 `case-09`、同一个 `system-prompt-common.md` 和同一份五工具 schema。
- evidence tool 的实现统一调用 `benchmark/replay/replay_service.py`；Agent 只看到工具返回，不看到 Oracle 或来源 patch。
- 每个 Agent 串行完成 9×3；K8sGPT 串行完成 case-06×3；结束后封存并清理。

## 各 Agent 原生 smoke

### Mini-Drop

通过 Mini-Drop 的真实 Case/Evidence/Agent Runtime 创建一次 case turn。必须看到 Mini-Drop runtime 产生的 model attempt、tool invocation、Evidence 引用和 final answer 事件；禁止直接调用统一 thin runner。

### HolmesGPT

补全并锁定完整 `holmes` Python 包，执行真实 `ToolCallingLLM`/ToolExecutor。`python -c 'import holmes'`、真实工具调用和最终结构化回答都必须出现在 smoke artifact；若包缺失，写 `environment_blocked`，不生成主榜结果。

### smolagents

使用上游 `ToolCallingAgent`（禁止 `CodeAgent`），由 `ToolCallingAgent.run()` 产生 step trace。trace 必须包含框架 step、tool call 和 final answer；不能用普通 DeepSeek HTTP 客户端冒充 smolagents。

### ITOps Agent Platform

启动真实 headless backend，访问其健康检查和诊断入口，并把五个 replay tool 注册到其真实 tool gateway。15 分钟内无法健康启动则 `environment_blocked`，不要用 thin adapter 填满 27 次。

### K8sGPT

仅在 Kubernetes 专项中使用真实 `k8sgpt analyze`/MCP 入口，并将 case-06 证据投影为其真实 source/provider。结果只进入 `k8s_specialty` 附录，不能与通用主榜合并。

## 原生运行产物

原生运行写入独立目录，不能覆盖 thin-adapter 结果：

```text
benchmark/runs-native/<agent-id>/<source-sha>/<case-id>/repeat-<n>/
  manifest.json
  input-hashes.json
  native-runtime.json
  native-trace.jsonl
  tool-trace.jsonl
  interventions.jsonl
  raw-agent-output.txt
  normalized-answer.json
  resource-usage.json
  score.json
```

`native-runtime.json` 至少记录：真实入口、PID/容器标识、启动命令摘要、source path、source SHA、依赖锁 hash、版本输出、关闭时间和清理检查。禁止记录 API key、token、密码或完整环境变量。

每次运行必须在 `manifest.json`、`native-runtime.json` 或 `input-hashes.json` 中记录同一个 `common_contract_hash`；`tools_hash` 只表示框架序列化结果，不能替代 canonical contract hash。

## 原生可比性验收

只有同时满足以下条件，`comparability_acceptance` 才能为 `PASS`：

1. 四个主榜 Agent 各有 27 个 `adapter_mode=native` 且 `native_runtime=true` 的完整运行。
2. 四个主榜 Agent 的 model、model config hash、system prompt hash、`common_contract_hash`、public case hash 一致；framework-specific `tools_hash` 必须可映射回同一个 canonical contract。
3. 每个 Agent 每个 case 至少 2/3 有效重复；C7/C8/C9 有真实 intervention trace。
4. HolmesGPT、ITOps、K8sGPT 的 native smoke artifact 可独立复核；K8sGPT 若使用 fake API，只能标记 `simulated-cluster`。
5. K8sGPT 不进入通用主榜，只报告 case-06 专项结果。
6. 任意 thin-adapter 运行都不计入原生主榜分母。

最终报告必须同时列出：`native_mainboard`、`thin_adapter_appendix`、`k8s_specialty`。只有 `native_mainboard` 可以支持“原生能力横向对比”表述。
