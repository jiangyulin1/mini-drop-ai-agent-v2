# 文档入口

本目录只保留当前仍需维护的设计和运行文档。带日期的历史审计、旧测试快照、旧演示脚本和已被新版方案取代的设计稿不再保留在工作树中，需要时可从 Git 历史查看。

## 当前文档

| 文档 | 用途 | 状态 |
|---|---|---|
| [`drop_ai_exploration_roadmap.md`](drop_ai_exploration_roadmap.md) | 当前项目审核、AI 探索定位、多路线优先级、12 周计划与晋级门槛 | 当前路线决策基线 |
| [`autonomous_ops_agent_implementation_plan.md`](autonomous_ops_agent_implementation_plan.md) | 持续事故接管、准确率提升、自动处置闭环和分阶段验收 | 当前实施基线 |
| [`ai_authorization_and_tooling.md`](ai_authorization_and_tooling.md) | Source、Probe、Grant、Policy、Action 和安全执行约束 | 当前安全规范 |
| [`mcp_integration.md`](mcp_integration.md) | MCP Server、外部 MCP 数据源、部署和安全边界 | 当前集成指南 |
| [`../benchmarks/lightweight_ai_eval/README.md`](../benchmarks/lightweight_ai_eval/README.md) | 秒级 AI 回归、MCP 安全门禁与 Hyper-V 抽检入口 | 当前评测入口 |
| [`drop_execution_pipeline.md`](drop_execution_pipeline.md) | 普通采集任务、TaskAttempt、Analyzer 和 Artifact 的可恢复执行语义 | 当前底座说明 |
| [`release-baseline-runbook.md`](release-baseline-runbook.md) | 质量门禁、数据库迁移、备份恢复和对象对账 | 当前运行手册 |
| [`security-baseline.md`](security-baseline.md) | 凭据、仓库和 Web 依赖安全基线 | 当前安全基线 |

## 其他目录

- `prototypes/`：只用于界面讨论，不代表当前生产实现。
- 正式评测数据和机器报告位于 `reports/eval/`，不作为长期设计规范。
- 数据集契约和场景位于 `benchmarks/`，以版本化 manifest 和 Oracle 为准。

## 维护规则

- “当前实现”只能写在 README 或当前实施基线中，并附验证日期或版本。
- 一次性审计和测试结果写入 `reports/`，不再新增到 `docs/`。
- 新方案替代旧方案时，直接更新当前基线；需要保留的历史使用 Git 标签或发布记录。
- 文档不得包含密码、Token、私钥或真实生产凭据。
