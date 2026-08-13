# Mini-Drop AI 实施状态

> 基线日期：2026-08-10。本文只记录可由当前仓库和三节点实验集群验证的事实。
> 设计真源：[`ai_diagnosis_agent_design.md`](ai_diagnosis_agent_design.md)；
> 需求追踪：[`ai_design_traceability.md`](ai_design_traceability.md)；
> 测试执行：[`ai_testset_design.md`](ai_testset_design.md)。

## 当前完成度

| 能力 | 当前状态 | 关键契约 |
|---|---|---|
| Case 协作 | 已实现 | 租户隔离；消息、暂停/恢复/停止/纠正；乐观锁；诊断替换与审计 |
| 数据驱动入口 | 已实现 | `initial_tasks` 必须完成、有结构化产物、匹配实例范围且与事故时间窗重叠；先分析旧证据，充分时不再创建探针 |
| 当前理解 | 已实现 | `GET /api/v1/cases/{id}/understanding` 从持久化假设图和实时证据确定性派生；缺失引用不得算作确认 |
| 动作提案卡 | 已实现 | `GET /api/v1/cases/{id}/proposals` 展示依据、预期作用、影响、成本、可逆性和审批策略 |
| 变更登记 | 已实现 | Web + `POST/GET /api/v1/changes`；变更进入诊断输入，但只作为待验证相关性 |
| 候选缺失兜底 | 已实现 | 只允许已注册 TaskKind 且必须有域映射；强制单次审批；未知域不随意选采集器 |
| Case 内受控修复 | 已实现首个闭环 | 仅注册且可执行的维护动作；持久化方案→只读预检→显式审批→执行锁→服务端验证→失败回滚；进程崩溃可对账 |
| 目标级长期会话 | 已实现 | tenant 隔离；暂停/恢复/归档；幂等信号；严重度策略与冷却窗口；自动孵化并关联 Case；Web 可创建与复用 |
| profiling 历史窗口 | 已实现检索地基 | 已完成 `continuous_perf` Task 可按明确实例范围建索引、按事故时间检索并自动关联信号/Case；7 天/90 天降采样层尚未实现 |
| VM 测试集评分 | 已实现 | 重复次数、Top-1/3、证据域、时限、双节点 Linux、安全、恢复、Oracle 隔离和 Jaccard 稳定性共同控制 `verified_vm` 晋级 |
| 对话式 Agent Runtime | 已实现首版 | `case-agent-turn.v1` 统一自然语言回合；解释/纠错/调查/状态/部署评估；决策摘要与证据链，不保存私有思维链 |
| MCP 工具编排 | 已实现首版 | Planner 只从 Source Registry 选择；MCP 继续经过租户、Grant、Capability Token、脱敏与结果预算 |
| 部署承载力预测 | 已实现保守基线 | 结构化需求 + allocatable 容量 + 安全余量；缺容量时返回 `insufficient_data`，不以瞬时利用率替代容量 |

数据库迁移链为 `0001` 至 `0014`；新增恢复方案、目标会话/信号和 profiling 窗口索引均有 Alembic 迁移与漂移检查。

## 质量基线

| 门禁 | 结果 |
|---|---|
| Python 全量测试 | 769 passed（含对话式 Agent Runtime 与容量评估回归） |
| Python 覆盖率 | 78%（13,502 statements）；新增 Agent/Analyzer 失败分支回归 |
| Golden 诊断集 | 7/7 passed；分类、证据引用、安全动作全部通过 |
| 数据库迁移 | 空库升级通过；Alembic schema drift 为 0 |
| 仓库卫生 | passed |
| 测试集静态契约 | 8/8 manifest、采集器、生命周期脚本、shell 语法通过 |
| Web | lint passed；51 tests passed；production build passed |
| Web 生产依赖审计 | 0 vulnerabilities |
| 三节点发布 | Control/worker1/worker2 已切换到 `mini-drop-release-20260810-ai-agent-v1`；服务健康，旧版本保留可回滚 |
| VM 运行态冒烟 | 两个 `sys_metrics` 采集与分析完成；`continuous_perf` 1/1 窗口成功，产物上传、索引和信号关联通过 |

覆盖率的主要薄弱区是 Agent 主循环（31%）、Analyzer CLI（52%）和少数系统集成分支。它们不阻断本地逻辑门禁，但 Linux VM 验收必须重点覆盖。

## 已修正的关键设计/逻辑问题

1. 初始任务过去在诊断规划后才附加，无法影响结论；现在在规划新探针前完成校验、装载和分析。
2. 提案接口过去读取会话摘要，取不到 `latest_conclusion`；现在读取完整诊断详情。
3. `current_understanding` 过去只在初始上下文生成；现在提供实时 Case 投影端点和 Web 展示。
4. recent changes 过去只存上下文包、不进入诊断查询；现在作为明确标注的待验证事实进入输入。
5. 未知证据域过去会随机拿白名单采集器兜底；现在没有有效映射就诚实请求补充能力/方向。
6. 进程扫描测试假定 4 KiB page size；现在使用运行平台实际 page size，macOS 可通过。
7. ServiceChange 索引与迁移定义不一致；现在统一为租户、服务、时间复合索引。
8. 测试引擎重置过去不释放连接；现在会 dispose 旧引擎。
9. 修复动作过去可能先产生副作用、再因状态冲突丢失执行记录；现在先持久化 `EXECUTING`，失败记录部分结果，重启后按后置条件对账。
10. 长期目标过去只是文档概念；现在信号去重、冷却、Case 孵化和历史 profile 关联均为持久化契约。
11. `verified_vm` 过去可手工改 manifest；现在必须由评分器满足全部运行门禁，且评分后修改清单会使晋级失效。

## 尚需 Linux 环境完成的验收

- `testsets/real/online-boutique` 的 8 个案例目前均为 `designed`，不是 `verified_vm`。
  2026-08-10 已在 worker1 通过 Compose 和全部 shell 脚本的 Linux 静态检查；
  运行态的唯一当前阻塞是官方 Google Artifact Registry 从 Mac/VM 均访问超时，
  且 worker 无本地镜像。
- 故障脚本已具备 preflight、基线、注入、可靠回滚、恢复观察与私有 GT 记录；只有在隔离 Linux VM 实跑成功后才能升级状态。
- 网络案例必须显式设置 `FAULT_IFACE`；所有案例必须设置 `TARGET_URL`。测试查询与运行参数可给 AI，`expected`/ground truth 只能留在评分器侧。
- Agent 常驻 profiling 的 7 天聚合与 90 天每日基线尚未实现；当前完成的是 24 小时 detail 索引与事故窗口检索。
- 通用生产服务修复仍是 `policy_only`；当前可执行闭环只覆盖注册的 Mini-Drop 维护动作，不会把未实现动作伪装成已开放。

## 推荐验收顺序

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/check_migrations.py
.venv/bin/python scripts/check_repo_hygiene.py
.venv/bin/python scripts/validate_testsets.py testsets
.venv/bin/python scripts/score_testset_runs.py --help
.venv/bin/python scripts/run_diagnosis_eval.py --output-dir reports/eval

cd web
npm run lint
npm test -- --run
npm run build
npm audit --omit=dev --audit-level=high
```
