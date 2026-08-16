# 文档入口

本目录只保留当前仍需维护的设计和运行文档。带日期的历史审计、旧测试快照、旧演示脚本和已被新版方案取代的设计稿不再保留在工作树中，需要时可从 Git 历史查看。

## 当前文档

| 文档 | 用途 | 状态 |
|---|---|---|
| [`v2_continuation_master_prompt_v1.md`](v2_continuation_master_prompt_v1.md) | 基于 V2 继续推进的架构收敛与产品闭环总提示词：覆盖模块边界、事务/Outbox、Agent 安全、跨平台门禁、前端和候选交付 | 当前主执行提示词与最终交付真源 |
| [`ai_agent_feature_complete_demo_prompt_v6.md`](ai_agent_feature_complete_demo_prompt_v6.md) | v6 功能完整与三节点验收合同，保留 Evidence、Supervisor、Agent Loop、复合因果和公开评测的详细历史需求 | 作为 V2 Prompt v1 的需求素材；冲突时以 V2 Prompt v1 为准 |
| [`ai_agent_feature_complete_demo_prompt.md`](ai_agent_feature_complete_demo_prompt.md) | v5 历史长版及审计批注，仅用于追溯旧需求和评测设计 | 已由 v6 替代，不再作为执行入口 |
| [`ai_agent_runtime_integration_plan.md`](ai_agent_runtime_integration_plan.md) | ResourceRef、Evidence、Plan、Runtime、Tool、集群、AC 与 VM 阶梯的历史详细设计素材 | 仅作参考；冲突时以 V2 Prompt v1 为准 |
| [`drop_ai_exploration_roadmap.md`](drop_ai_exploration_roadmap.md) | 项目审核、探索定位、多路线优先级和旧 12 周计划 | 历史决策背景，不是执行路线 |
| [`autonomous_ops_agent_implementation_plan.md`](autonomous_ops_agent_implementation_plan.md) | 持续事故接管、准确率和自动处置的旧分阶段设计 | 历史设计素材，不是完成度真源 |
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
- 执行 AI 只应把 `v2_continuation_master_prompt_v1.md` 作为主提示词；其他 AI 设计文档中的固定版本、完成度或固定调查方向不得覆盖它。
