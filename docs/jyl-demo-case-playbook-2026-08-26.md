# JYL 演示 Case 手册（2026-08-26）

## 数据边界

本轮演示数据来自 `evaluation-20260826-bundle` 的公开脱敏 replay projection，来源是
`vm-realistic-30`，不是生产原始数据。评测 oracle、private provenance 和原始源码没有导入
JYL。回放 Evidence 的 `source_channel=EVALUATION`、`data_origin=REPLAY`，标题和环境均明确
标注 `evaluation-replay-vm-realistic-30`。

## 演示 Case

| 顺序 | Case | 样例 | 重点 |
| --- | --- | --- | --- |
| 1 | `case_20260826_013920_35b1c32dd6` | case-01 HTTP latency regression | Evidence 四件套、假设图、blocking Gap、因果图、分支、报告入口 |
| 2 | `case_20260826_014355_cfe46675f5` | case-05 Redis eviction storm | 主因/放大因素叙事、Evidence 对比、替代解释 |
| 3 | `case_20260826_014355_ef0e1db4c8` | case-23 conflicting metrics and logs | 时间窗冲突、Evidence Gap、正确 abstain 边界、Case 知识文档 |
| 4 | `case_20260826_013336_9ed5ac2cda` | JYL worker live target | 真实 Agent、只读调查入口、权限/范围/预算门禁、安全失败边界 |

## 推荐讲解顺序

1. 打开 Case A 的 Evidence 工作区，先展示四条 Evidence 的来源、时间窗、trust、projection。
2. 打开 Hypotheses，说明 `cpu-hot-path`、`disk-or-network` 和 `OTHER_UNKNOWN` 是竞争假设。
3. 打开 Evidence Gaps，展示 `PROFILE_MISSING` blocking gap，说明系统不会把“CPU 热点”夸大成具体代码行。
4. 打开 Causal Graph，展示 `cpu -> latency` 的 `CAUSES` 边，以及依赖图与因果图的区别。
5. 切换到 A 的 `branch_a51992d88ad30be0`，说明分支内容按 branch_id 隔离，主 Case Evidence 不会自动污染分支。
6. 打开 Case B，讲解 Redis 驱逐是主因，后端负载是放大因素；不要把放大因素说成根因。
7. 打开 Case C，展示 `TIME_WINDOW_CONFLICT` gap 和 Case 级知识笔记，说明冲突证据应触发复核而不是强行结论。
8. 最后打开 Case D，发送一次“只读调查”请求。当前版本已修复 Tool Fence 的 `NameError`；请求会进入真实采集调度，但目标 PID 已变化时会按范围门禁拒绝并保持 abstain，展示 fail-closed 的权限/身份边界。

## 2026-08-26 复验结果

Case A 已通过一次真实的内部 `finish` 验证，当前状态为 `WAITING_USER`，结论为
`PARTIALLY_CONFIRMED`（revision 1）。结论包含 4 个带 `projection_hash`、字段路径和谓词的
Claim Evidence Binding，并可下载 Markdown 报告。排除 `node_metrics` 的 review preview 已验证
会把影响链标记为 `BROKEN`、要求重新核验和审批，而不是继续沿用旧结论。

发布 Candidate：`cand-d6c8f6e51c33f069`，提交 `128fd83`。线上 active 已切换到该版本，
`/api/readyz`、数据库、MinIO、Analyzer 和 Pi Sidecar 均健康。

## 已准备的功能入口

- 工作区：`GET /api/v1/cases/{case_id}/workspace`
- Evidence：`GET /api/v1/cases/{case_id}/evidence`
- 分支：`GET/POST /api/v1/cases/{case_id}/branches`
- 假设、Gap、因果图：工作区聚合接口及内部 Agent 工具投影
- 结论报告：`GET /api/v1/cases/{case_id}/conclusion/report`
- 知识库：`GET /api/v1/knowledge-documents`、`POST /api/v1/knowledge-search`

## 清理与恢复

清理前 PostgreSQL 备份保存在服务器：
`/jyl/backups/cleanup-20260826/mini_drop-before-cleanup.dump`。

清理前的 MinIO 对象清单保存在：
`/jyl/backups/cleanup-20260826/mini-drop-objects-before.json`。

JYL 的临时 `MINI_DROP_EVAL_IMPORT_ENABLED` 和 token 已在导入完成后移除，当前公网不接受
回放投影导入。`/jyl/testsets` 与发布配置未清理。

## 当前诚实边界

- 回放 Case 可展示证据模型和确定性工作区状态，但不能宣称为真实生产采集。
- Case D 的实时 AI 调查入口可展示权限、范围、预算、采集调度和目标身份门禁；实时目标若已
  发生 PID/身份变化，会安全地停在待补证据状态，不能将其当作已确认根因。
- 结论报告下载和知识库 API 已存在；如果 Case 没有通过 verifier 的正式结论，报告端点会返回
  `CONCLUSION_NOT_AVAILABLE`，这是预期的 fail-closed 行为。
