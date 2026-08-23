# 文档入口

本目录只保留当前仍需维护的设计和运行文档。带日期的历史审计、旧测试快照、旧演示脚本和已被新版方案取代的设计稿不再保留在工作树中，需要时可从 Git 历史查看。

## 当前文档

| 文档 | 用途 | 状态 |
|---|---|---|
| [`asset-map.md`](asset-map.md) | 当前代码、运行链路、上下游拓扑、Evidence、Agent Runtime、Pi、Web、恢复和测试资产的事实盘点 | 当前资产地图；随重大代码变更维护 |
| [`open-source-components.md`](open-source-components.md) | LangGraph、Temporal、NetworkX 等开源组件的选型、许可证和 Mini-Drop 边界 | 当前开源组件决策 |
| [`ai_current_design_interview_handbook.md`](ai_current_design_interview_handbook.md) | AI 功能当前设计、Pi Agent 接入、结构化证据、执行链、分层门禁、可信度与面试追问核实 | 当前设计与面试答辩入口 |
| [`environment-setup.md`](environment-setup.md) | Python/Node/Pi/DeepSeek、SQLite 与本机轻量启动 | 当前环境入口 |
| [`deployment-profiles.md`](deployment-profiles.md) | Native、Local Compose、Linux 全栈、Control/Worker、Pi 与低带宽评测模式 | 当前部署入口 |
| [`repository-maintenance.md`](repository-maintenance.md) | 文档归档、生成物清理、凭据、依赖和提交门禁 | 当前维护规范 |
| [`../reports/evaluation/verified-20260821.md`](../reports/evaluation/verified-20260821.md) | DeepSeek 真实 PR 单轮与未知拓扑 Pi 链路人工核验 | 带日期的验证证据 |
| [`evidence_native_agent_unified_architecture.md`](evidence_native_agent_unified_architecture.md) | 恢复旧 v2/v6 受监督调查闭环并融合新版 Evidence/Collector，定义唯一主线、写入权威、并发语义、迁移和删除门禁 | 当前产品与实施架构合同 |
| [`ai_collector_architecture_and_migration_plan.md`](ai_collector_architecture_and_migration_plan.md) | AI 深度采集与 Evidence 分析阶段的架构审计、删除门禁和评测计划 | 历史阶段决策；Collector 是当前诊断 Agent 的执行核心而非产品边界 |
| [`ai_diagnostic_agent_evolution_plan.md`](ai_diagnostic_agent_evolution_plan.md) | 本轮并行架构审计的细节稿，包含旧链路、Evidence 完成度和早期迁移取舍 | 历史审计素材；当前执行只以 AI Collector 基线为准 |
| [`v2_continuation_master_prompt_v1.md`](v2_continuation_master_prompt_v1.md) | V2 架构收敛与产品闭环总提示词，包含事务/Outbox、Agent 安全、跨平台门禁和候选交付素材 | 历史实施合同；仅复用不与 AI Collector 基线冲突的机制 |
| [`ai_agent_feature_complete_demo_prompt_v6.md`](ai_agent_feature_complete_demo_prompt_v6.md) | v6 功能完整与三节点验收合同，保留 Evidence、Supervisor、Agent Loop、复合因果和公开评测的详细历史需求 | 历史需求素材；不再作为当前执行入口 |
| [`ai_agent_feature_complete_demo_prompt.md`](ai_agent_feature_complete_demo_prompt.md) | v5 历史长版及审计批注，仅用于追溯旧需求和评测设计 | 已由 v6 替代，不再作为执行入口 |
| [`ai_agent_runtime_integration_plan.md`](ai_agent_runtime_integration_plan.md) | ResourceRef、Evidence、Plan、Runtime、Tool、集群、AC 与 VM 阶梯的历史详细设计素材 | 仅作参考；冲突时以 AI Collector 基线为准 |
| [`drop_ai_exploration_roadmap.md`](drop_ai_exploration_roadmap.md) | 项目审核、探索定位、多路线优先级和旧 12 周计划 | 历史决策背景，不是执行路线 |
| [`autonomous_ops_agent_implementation_plan.md`](autonomous_ops_agent_implementation_plan.md) | 持续事故接管、准确率和自动处置的旧分阶段设计 | 历史设计素材，不是完成度真源 |
| [`ai_authorization_and_tooling.md`](ai_authorization_and_tooling.md) | Source、Probe、Grant、Policy、Action 和安全执行约束 | 当前安全规范 |
| [`mcp_integration.md`](mcp_integration.md) | MCP Server、外部 MCP 数据源、部署和安全边界 | 当前集成指南 |
| [`../benchmarks/lightweight_ai_eval/README.md`](../benchmarks/lightweight_ai_eval/README.md) | 旧规则链的秒级回归、MCP 安全门禁与 Hyper-V 抽检入口 | 历史回归入口；不能证明 AI 自主选择 Collector |
| [`drop_execution_pipeline.md`](drop_execution_pipeline.md) | 普通采集任务、TaskAttempt、Analyzer 和 Artifact 的可恢复执行语义 | 当前底座说明 |
| [`release-baseline-runbook.md`](release-baseline-runbook.md) | 质量门禁、数据库迁移、备份恢复和对象对账 | 当前运行手册 |
| [`security-baseline.md`](security-baseline.md) | 凭据、仓库和 Web 依赖安全基线 | 当前安全基线 |

## 其他目录

- `prototypes/`：只用于界面讨论，不代表当前生产实现。
- 正式评测数据和机器报告位于 `reports/evaluation/` 或被忽略的 `reports/eval/`，不作为长期设计规范。
- 数据集契约和场景位于 `benchmarks/`，以版本化 manifest 和 Oracle 为准。

## 维护规则

- “当前实现”只能写在 README 或当前实施基线中，并附验证日期或版本。
- 一次性审计和测试结果写入 `reports/`，不再新增到 `docs/`。
- 新方案替代旧方案时，直接更新当前基线；需要保留的历史使用 Git 标签或发布记录。
- 文档不得包含密码、Token、私钥或真实生产凭据。
- 环境和清理规则以 `environment-setup.md`、`deployment-profiles.md`、`repository-maintenance.md` 为准。
- AI 产品方向和具体实施以 `evidence_native_agent_unified_architecture.md` 为准；Collector 计划与 v2/v6 提示词作为历史需求素材。不得恢复 rules-first RCA 或第二套独立调查主脑；受审批恢复与重复验证属于统一主线。
