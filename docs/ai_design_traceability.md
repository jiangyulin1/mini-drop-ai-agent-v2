# AI 诊断需求—实现—测试追踪矩阵

> 更新：2026-08-08。状态只有 `完成`、`需 Linux 验证`、`未实现`；拆分能力边界，不用含糊百分比。

| ID | 设计不变式 / 用户能力 | 实现位置 | 自动化验证 | 状态 |
|---|---|---|---|---|
| R01 | Case、事件、假设、上下文和变更按 tenant 隔离 | `main.py`、`sql_repository.py`、`models.py` | `test_incident_cases.py`、`test_change_registration.py` | 完成 |
| R02 | 纠正范围必须取消并解除旧诊断，不能混用旧证据 | `case_collaboration.py`、`main.py` | `test_case_correction_cancels_and_detaches_superseded_diagnosis` | 完成 |
| R03 | 初始任务必须 DONE、有结构化产物、同实例范围、同事故窗口 | `sql_repository.py` | `test_case_initial_tasks_*` | 完成 |
| R04 | 初始证据必须先分析；足够时不得创建冗余探针 | `diagnosis/orchestrator.py` | `test_case_data_driven_initial_tasks_preload_evidence` | 完成 |
| R05 | 结论、当前理解和提案必须从完整诊断详情读取 | `main.py` | `test_incident_cases.py` | 完成 |
| R06 | 缺失证据引用不能被称为已确认 | `diagnosis/current_understanding.py` | `test_missing_support_reference_is_not_reported_as_confirmed` | 完成 |
| R07 | 候选缺失只能使用注册能力；未知域不能随意凑采集器 | `diagnosis/probe_registry.py` | `test_candidate_gap_fallback.py` | 完成 |
| R08 | 候选缺失提案必须单次审批 | `probe_registry.py`、`proposal_card.py` | `test_candidate_gap_fallback.py`、`test_proposal_card.py` | 完成 |
| R09 | 变更可登记、可按租户/服务/环境查询，且只作为待验证相关性 | `main.py`、`sql_repository.py`、`case_collaboration.py`、Web workspace | `test_change_registration.py`、Web lint/test/build | 完成 |
| R10 | 模型不能伪造证据、工具、知识引用或绕过审批 | Verifier、Action/Source registry、授权层 | 7 个 golden scenarios + authorization/action tests | 完成 |
| R11 | Schema 迁移必须与 ORM 一致 | `migrations/`、`models.py` | `scripts/check_migrations.py` | 完成 |
| R12 | 真实测试集必须区分公开输入和私有 oracle | manifest `user_query` / `oracle_visibility=private` | `scripts/validate_testsets.py` | 完成 |
| R13 | 故障案例必须有 preflight、基线、注入、精确回滚、恢复观察、GT | `testsets/real/online-boutique` scripts | JSON Schema + shell syntax/static contract | 需 Linux 验证 |
| R14 | 网络故障不能默认修改生产网卡 | `fault-delay`、`preflight.sh` | shell 静态校验；运行时要求 `FAULT_IFACE` | 需 Linux 验证 |
| R15 | 目标级长期会话可接收幂等信号并按策略孵化 Case | `DiagnosticTargetSessionModel`、`TargetSignalModel`、target session API、Web | `test_target_sessions.py`、Web test/lint/build | 完成 |
| R16 | Agent 心跳/拓扑流自动订阅并生成规范化信号 | 尚未接入 Agent/event bus | 尚无 | 未实现 |
| R17 | 持续 profiling detail 可按历史窗口检索并关联事故 | `ProfileWindowModel`、`index_profile_task`、profile window API | `test_profile_task_indexes_queryable_windows_and_signal_links_them` | 完成 |
| R18 | profile 7 天降采样聚合与 90 天每日基线 | 尚无 | 尚无 | 未实现 |
| R19 | Case 修复方案必须预检、审批、执行、服务端验证并可回滚 | recovery plan model/API、`actuation.py` | `test_case_recovery_plans.py`、`test_actuation.py` | 完成 |
| R20 | 执行前持久化锁；崩溃后不得重复副作用或虚报状态 | `EXECUTING` 状态、后置条件对账、部分执行日志 | `test_recovery_execute_reconciles_crash_after_side_effect` | 完成 |
| R21 | `verified_vm` 必须来自足量真实运行、稳定性、安全和恢复门禁 | `run-result.schema.json`、`score_testset_runs.py` | `test_testset_scoring.py` | 完成 |

## 发布判定

- 本地逻辑发布：R01–R12 全绿，并通过实施状态文档中的所有门禁。
- Linux VM 回归：R13–R14 每个案例至少重复 3 次，由评分器生成报告并晋级，不能手工把 manifest 改为 `verified_vm`。
- R16、R18 仍不得写成已交付；它们分别是自动数据流订阅与长期降采样保留层。
