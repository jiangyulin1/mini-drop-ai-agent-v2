# Evidence-native 真实评测记录

日期：2026-08-25  
环境：JYL 三节点 Compose（`/jyl`），Pi Runtime，DeepSeek `deepseek-v4-flash`

## 评测结论

当前 Evidence-native 主线已经完成真实 Task -> Artifact -> CaseEvidence -> Branch -> Agent Tool -> Conclusion 闭环。正式验收不再使用旧的一次性 RCA 评分脚本，而使用：

- `scripts/run_evidence_native_vm_eval.py`
- `scripts/run_evidence_native_pi_vm_eval.py`

最新报告：

- `reports/evaluation/evidence-native-vm/run-20260824T170157Z.json`
- `reports/evaluation/evidence-native-vm/run-pi-20260824T170449Z.json`

Pi 报告通过以下门槛：Sidecar ready、真实 Provider completion、分支 Evidence 可见性、`tool_execution_start/end` 审计、`finish_investigation` 完成、分支 Workspace 中存在 Evidence-bound Conclusion。最新结论为 `INSUFFICIENT_EVIDENCE`，引用当前分支唯一可见 Evidence；这表示系统正确拒答，不表示模型已经证明根因。

## 已验证能力

- Worker 在线，`sys_metrics` Task 完成，Artifact 上传并物化为 CaseEvidence。
- A/B 分支 Evidence 隔离，Branch Hypothesis、Causal Graph、Conclusion 可持久化。
- A 分支排除 Evidence 后生成 `RECHECK_REQUIRED` revision，同时保留历史结论；B 分支仍保持原结论和 revision。
- Pi 使用真实 DeepSeek 模型调用只读工具、采集提案、Evidence 分析和 `finish_investigation`，没有把普通文本当作终态。
- JYL Web 入口为 `https://<control-address>:80`。无 Key 的 `/api/livez` 为健康白名单；带 Key 的 `/api/agents` 和 `/api/readyz` 通过认证。默认 443 属于另一套 cloud Compose，不作为 JYL 评测入口。

## 尚未宣称的能力

- 自动证明任意业务根因或完整拓扑 RCA。
- 通用多支持集真值维护、自动选择准确祖先的局部回溯。
- 自动修复、生产级公网信任证书和模型准确率基准。

## 操作注意

Candidate archive 不包含受保护的 Compose `.env` 和 TLS 证书。部署到 `/jyl` 时必须从当前 active release 复制 `.env`，并将 `deploy/certs/{ca.crt,server.crt,server.key}` 复制到新 release；否则 Server/Worker 会因证书缺失无法启动。API Key 和 DeepSeek Key 只由受保护 env 注入，不写入报告或仓库。
