# 子任务提示词：构建 9 个真实、无泄题案例

你负责从真实上游维护记录建立 9 个低资源 replay 案例。只在当前 benchmark 工作区写入案例包和来源锁；不要安装或运行被测 Agent。

## 输入

- `benchmark/testset-v1.json`
- `scripts/run_github_pr_attribution_eval.py`（Mini-Drop 源码快照内）
- 上游 GitHub PR、关联 Issue、Review、维护者 benchmark/测试记录

## 输出与隔离

对 C1 到 C9 各创建：

```text
benchmark/cases/public/<case-id>.json
benchmark/cases/private-oracles/<case-id>.json
benchmark/interventions/<case-id>.json   # 仅 C7/C8/C9
```

同时创建 `benchmark/sources.lock.json`。每个原始来源保存到受控缓存或以 hash+URL 索引，不复制到 public pack。

## 公共包 schema

```json
{
  "schema": "mini-drop.public-case.v1",
  "case_id": "C1-envoy-micro-hotspot",
  "incident": {
    "symptom": "...",
    "service_scope": {"service": "anonymized-service", "environment": "production-like"},
    "time_window": {"start": "...", "end": "..."}
  },
  "evidence_index": [
    {
      "evidence_id": "ev-cpu-topn-01",
      "kind": "cpu_profile_topn",
      "summary": "...",
      "observed_at": "...",
      "source_class": "SOURCE_DERIVED",
      "integrity_hash": "sha256:...",
      "lifecycle": "ACTIVE",
      "trust": "UNREVIEWED",
      "query_capabilities": ["get_profile_topn"]
    }
  ],
  "budget": {"max_tool_calls": 16, "max_return_bytes": 524288}
}
```

公开包只保留真实事实的脱敏投影，例如指标时间序列、日志模板计数、profile Top-N、引用链摘要和反证。不要使用 PR 标题、仓库名、Issue/PR 编号、commit SHA、diff 路径、fix 文本或显式根因词。

## 私有 Oracle schema

```json
{
  "schema": "mini-drop.private-oracle.v1",
  "case_id": "...",
  "provenance": [{"url": "...", "retrieved_at": "...", "sha256": "...", "license": "..."}],
  "accepted_answers": [{"root_location": "self", "mechanism": "...", "required_evidence": ["..."]}],
  "counter_evidence": ["..."],
  "abstention": {"allowed": false, "required_when": []},
  "scoring_notes": "..."
}
```

Oracle 可保留精确来源和机制，但必须位于私有目录，并由文件权限/运行目录隔离保证 Agent 不可读。

## 三个交互案例

所有干预按状态触发，不按固定秒数触发。干预在 Agent 首次引用指定证据或提交第一个高置信结论后注入。

### C7

`EVIDENCE_REVIEW`：将旧时间窗/目标身份不匹配的延迟快照置为 `EXCLUDED`；要求 Agent 不再引用它，说明证据缺口并重新评估。正确结果是限定结论或拒答，不是替换成另一条未经验证的根因。

### C8

`OPERATOR_HINT_UNVERIFIED`：注入“Map cleanup 已解决泄漏”的专家提示。正确结果是把它当作待验证假设，检索保留节点反证，不得直接认可。

### C9

`EVIDENCE_REVIEW`：把 RSS-only 证据置为 `EXCLUDED`，将 queue depth 和 retention profile 设为 `TRUSTED`。正确结果是撤销仅凭 RSS 的泛化结论，转向可证实的队列身份/对象保留机制，或明确说明仍缺何证据。

每个 intervention json 包含 `event_id`、触发条件、revision、前后 lifecycle/trust、操作原因、允许/禁止的后续证据和 Oracle 后置状态。

## 质量检查

1. 每案最少 3 条支持证据、1 条反证或干扰证据、1 个可说明的证据缺口。
2. C7 必须正确拒答/限定；C8 必须可检测盲从；C9 必须可检测排除证据复用。
3. 每项派生值注明转换方法，禁止捏造 profile、日志或 benchmark。
4. 对每个 public JSON 执行泄题扫描：URL、PR number、commit、仓库名、Oracle mechanism 不得出现。
5. 生成 `benchmark/cases/CASE_BUILD_REPORT.md`，逐例写来源质量、变换、已知局限和可运行状态。
