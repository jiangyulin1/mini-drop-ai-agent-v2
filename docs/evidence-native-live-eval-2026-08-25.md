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

新版 9x3 PR Evidence-native 稳定性矩阵已在同一 JYL active release 上完成：

- 公开数据与 oracle：`reports/eval/github-pr-attribution-9x3/`
- 第一轮 live 结果：`reports/eval/github-pr-attribution-9x3/live/round-results.jsonl`
- 第一轮结构门禁：`reports/eval/github-pr-attribution-9x3/live/structural-score.json`
- 结果：9 个真实 GitHub PR、每个 3 轮，`27/27 completed`，结构门禁 `PASS`。
- 每轮均使用真实 `deepseek-v4-flash` Provider completion；三份 Projection 每 Case 只导入一次，后续轮次复用 canonical Evidence ID/hash。
- 该矩阵的 `simulated_runtime` 明确标记为 synthetic wiring probe，不代表真实生产 telemetry；模型机制、反证和影响边界仍需要人工/Oracle 质量评分。
- 首轮还发现答案层引用缺口：只有 `8/27` 轮完整写出三份 canonical Evidence ID；收紧 prompt 后第二轮结果在 `reports/eval/github-pr-attribution-9x3/live-v2/`，完整 ID/hash 引用达到 `27/27`，结构门禁仍为 `PASS`。
- 第二轮最终结果：`reports/eval/github-pr-attribution-9x3/live-v2/round-results.jsonl`；结构结果：`reports/eval/github-pr-attribution-9x3/live-v2/structural-score.json`。

### 9x3 人工质量粗评（非双盲）

结构 scorer 的 `quality_score` 保持为空，因为它只验证链路和策略门禁，不从关键词推断 RCA 质量。基于第二轮全部 9 案例、27 轮答案与本地 private oracle 的直接人工判断，按每轮 10 分标准给出以下粗评：

| 质量维度 | 粗评 | 判断 |
|---|---:|---|
| 机制归因 | 3.7–3.9 / 4 | 能定位具体文件、函数、数据结构和机制链；少数地方依赖 PR 作者自述，未被独立运行时证据闭环。 |
| Evidence 引用 | 3.0 / 3 | 第二轮 `27/27` 轮完整绑定三份 canonical `evidence_id` 和 `projection_hash`。 |
| 反证与不确定性 | 1.7–1.9 / 2 | 能处理负向 control、revert 和 benchmark pending，并明确 abstain；仍需限制 synthetic 信号的外推。 |
| 影响边界 | 0.8–0.95 / 1 | 通常能区分 PR 局部机制、模拟信号和生产效果，少量答案有重复和措辞漂移。 |
| **综合** | **9.2–9.6 / 10** | **当前可用的非双盲人工能力估计约 9.3/10。** |

这表示约 `25–26/27` 轮达到高质量水平，不表示真实生产 RCA 准确率为 93%，也不表示模型具备自动修复或生产自治能力。代表性强项包括 Grafana workqueue 去重失效、Redis active-expire starvation、Kubernetes 不确定 revert，以及 Grafana detached-DOM 负向证据案例。

Pi 报告通过以下门槛：Sidecar ready、真实 Provider completion、分支 Evidence 可见性、`tool_execution_start/end` 审计、`finish_investigation` 完成、分支 Workspace 中存在 Evidence-bound Conclusion。最新结论为 `INSUFFICIENT_EVIDENCE`，引用当前分支唯一可见 Evidence；这表示系统正确拒答，不表示模型已经证明根因。

## 已验证能力

- Worker 在线，`sys_metrics` Task 完成，Artifact 上传并物化为 CaseEvidence。
- A/B 分支 Evidence 隔离，Branch Hypothesis、Causal Graph、Conclusion 可持久化。
- A 分支排除 Evidence 后生成 `RECHECK_REQUIRED` revision，同时保留历史结论；B 分支仍保持原结论和 revision。
- Pi 使用真实 DeepSeek 模型调用只读工具、采集提案、Evidence 分析和 `finish_investigation`，没有把普通文本当作终态。
- JYL Web 入口为 `https://<control-address>:80`。无 Key 的 `/api/livez` 为健康白名单；带 Key 的 `/api/agents` 和 `/api/readyz` 通过认证。默认 443 属于另一套 cloud Compose，不作为 JYL 评测入口。
- 9x3 live runner 的策略为 `READ_ONLY` + `deny_write` + `ANSWER_ONLY`；27 轮均记录 provider attempt、tool start/end 和 Evidence 引用，未发送 raw pack。
- 在给定 PR/diff/讨论和 Evidence Projection 的条件下，机制级代码归因、字段级证据引用、负向证据处理和影响范围约束已经达到高水平且三轮重复较稳定。

## 尚未宣称的能力

- 自动证明任意业务根因或完整拓扑 RCA。
- 通用多支持集真值维护、自动选择准确祖先的局部回溯。
- 自动修复、生产级公网信任证书和模型准确率基准。
- 9x3 结构门禁通过不等于通用 RCA 准确率通过；质量评分必须单独按 oracle 检查机制、反证、不确定性和影响边界。
- 本矩阵不是双盲 holdout：素材来自已知公开 PR，模型可能利用标题、diff 和讨论上下文；因此不能外推为通用根因定位率。
- `simulated_runtime` 是合成、低规模的 wiring probe，不是真实故障日志、指标、拓扑或修复后回归验证；动态 RCA、拓扑发现、故障恢复、自动修复和生产自治仍未被本轮证明。
- 评测完成后已将 JYL `MINI_DROP_EVAL_IMPORT_ENABLED` 恢复为 `0`，并清空临时 import token。

## 公开 PR 扩展评测（evidence-native-public-6）

为降低上传流量并增加跨语言/跨机制覆盖，使用 6 个 pinned public GitHub PR 做了一轮真实 JYL 评测：Kubernetes 2 例、Prometheus、OpenTelemetry Python、Envoy、Redis 各 1 例。

- 数据集契约：`benchmarks/evidence-native-public-6/manifest.json`；完整本地抓取与 oracle 在 `reports/eval/github-pr-public-6-v1/`。
- JYL live 报告：`reports/eval/github-pr-public-6-jyl-live-v1/`；`6/6 completed`，每 Case 三份 projection 只导入一次，raw pack、仓库 clone、Worker/Analyzer artifact 均未发送。
- 传输统计：请求约 `339,697` bytes、响应约 `484,565` bytes；18 次 projection import、6 次 Agent turn，远低于完整仓库上传。
- 非双盲人工粗评分：`56.0/60`，约 `9.3/10`。这是公开 PR、单轮、已知 oracle 条件下的能力估计，不是生产 RCA 准确率。
- 低规模 `simulated_runtime` 只验证 Evidence 接线和方向性趋势；真实生产内存、CPU、安全触发条件和修复后回归仍未被证明。

初次使用离线 cache miss 生成的输入会得到空 projection，模型正确 abstain；随后使用完整 pinned pack 新建 Case 重跑。该失败输入和修复过程保留在本地报告中，说明评测不会把数据缺失误报为模型成功。

评测入口有两个边界：公网 `https://<control-address>:80` 适合 Web/API；JYL 内部评测应使用 server 容器的内网 `8191` 地址，否则 `/internal/runtime/...` 可能被 Web 返回 HTML。内网复跑 `reports/eval/github-pr-public-6-jyl-runtime-audit-k139850/` 捕获到 37 条 runtime event、3 次 DeepSeek attempt 和 `tool_execution_start/end`。评测结束后 import 开关为 `0`，临时 token 为空，`readyz` 为 200。

## P07 隐藏事实动态补证验收

为验证此前 9x3/公开 PR 评测没有覆盖的“盲区推理 + 证据链动态调整”，新增了本地 evaluator-controlled 测试：

- 测试：`tests/test_blind_gap_dynamic_evidence.py`
- 执行：`./.venv/bin/pytest -q tests/test_blind_gap_dynamic_evidence.py tests/test_agent_runtime_local_loop.py tests/test_investigation_state.py`
- 结果：`25 passed`（含 1 个环境已有的 httpx/Starlette deprecation warning）

测试严格分两轮。第一轮只给 Agent 一个 CPU distractor projection，不写入决定性锁事实；Agent 通过 Tool Gateway 记录具体 `EvidenceGap`，带 blocker 的高置信 `CONFIRMED` 请求只能得到 `PARTIALLY_CONFIRMED`。评测器随后才开放已注册的 `runtime_snapshot` Collector，并用 native Task 完成一个包含 waiter/holder 的运行时快照。第二轮验证新 Artifact 经过现有 parser 生成 canonical Evidence，runtime wakeup 只携带该 Task 的新 Evidence，Gap 能以该 Evidence 解决，Hypothesis/Causal Graph 可更新，最终结论不再引用开放 Gap；携带旧 scope revision 的迟到写入返回 `STALE_SCOPE`。

这项测试的结论是 `PASS`：服务端的隐藏事实隔离、缺证拒绝、补证、Evidence materialization、wakeup 和 revision fencing 状态链已具备可验收闭环。它仍是 deterministic integration test，不证明 DeepSeek 在真实对话中一定会主动识别正确缺口、选择正确 Collector 或解释新证据；下一步应在 JYL Pi 上用最多两轮、低 token 的真实模型 smoke 复现同一协议，并人工判定 Agent 是否自主提出 `runtime_snapshot`。

## 操作注意

Candidate archive 不包含受保护的 Compose `.env` 和 TLS 证书。部署到 `/jyl` 时必须从当前 active release 复制 `.env`，并将 `deploy/certs/{ca.crt,server.crt,server.key}` 复制到新 release；否则 Server/Worker 会因证书缺失无法启动。API Key 和 DeepSeek Key 只由受保护 env 注入，不写入报告或仓库。