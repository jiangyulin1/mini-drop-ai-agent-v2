# Mini-Drop 开发检查点

日期：2026-08-24
状态：本检查点已完成，后续可从产品增强项继续

## 当前结论

路线已经确定为“改造现有 Evidence-native 主线，不从 GitHub 拉旧版本，不删除大量代码”。公开方案（AWS DevOps Agent、OpenSRE 等）验证了拓扑、竞争假设、证据日志、人工审查是通用骨架；Mini-Drop 的差异化仍然是 Evidence 生命周期、分支盲隔离、失效传播和重新探索。

## 本轮已完成

- 为 Hypothesis、Hypothesis Edge、Evidence Gap、Causal Graph、Conclusion、Evidence Dependency、Assistant Message 增加可空 `branch_id`。
- 新增迁移 `0037_branch_reasoning_scope`，旧数据保持 `NULL` Case-wide 兼容范围，新分支写入显式 branch scope。
- InvestigationStateService 支持 branch-aware snapshot、Hypothesis、Gap、Causal Graph 写入。
- Agent Tool Gateway 的 Hypothesis、Gap、Causal Graph、Dependency 查询和写入支持分支作用域。
- `finish_investigation` 支持 branch-local Conclusion，并避免 branch 结论覆盖 Case-wide current finding/state。
- Branch assistant message 的 idempotency key 包含 branch，避免不同分支消息冲突。
- Case Workspace、Hypothesis、Causal Graph、Evidence Gap、Conclusion 查询支持 `branch_id`。
- 旧 `DiagnosisSession` 后台 `diagnosis_advance` 默认冻结；仅 `MINI_DROP_ENABLE_LEGACY_DIAGNOSIS=true` 显式启用。
- 旧路由、CLI、模型和历史字段保留为冻结兼容链，没有物理删除。
- `docs/asset-map.md`、`docs/evidence-native-investigation-positioning.md`、`docs/evidence_native_agent_unified_architecture.md` 和答辩/迁移文档已更新为新的完成度表述。
- 新增 branch reasoning isolation 回归测试。
- 展示合同已改为使用完成的原生 Task/Artifact，经 Case attachment API 物化 canonical Evidence；Review/Exclude supporting Evidence 后，后端生成 `RECHECK_REQUIRED` revalidation revision，保留历史结论并允许后续重新调查。
- Evidence 维护清理第一阶段已完成：活动 Review 只写入/读取 `EvidenceReviewRevision` canonical ledger；旧 `evidence_reviews` 和 `DiagnosisEvidence` 保留为冻结历史兼容，不再参与新 Workspace 主链。
- 旧模块下线第一阶段已完成：新增统一 `legacy_compat` gate；独立 Diagnosis、Case Diagnosis、旧取消/审批、RCA feedback 和 Autonomous 旧启动 callback 默认关闭；历史读取默认不推进旧会话。
- Case `proposals` / `understanding` 默认读取 canonical collection proposal、Hypothesis 和 CaseEvidence；只有显式 legacy flag 才读取旧 DiagnosisSession 结论。
- 新增旧模块复用与删除门禁记录：`docs/legacy-module-retirement-2026-08-24.md`。采集器、Artifact、Evidence projection/review、Evidence analysis、Supervisor lease/fence、Action Registry、Recovery verification 和审计基础设施明确保留。
- 清理边界已进一步确认：不做旧模块整组删除；旧 Diagnosis/RCA 仅移出在线主线，可复用的 parser、Evidence 字段、benchmark、授权、审计、fence 和恢复治理按迁移/保留策略继续使用。当前本地 `mini_drop.db` 的旧 Diagnosis/RCA 表均为 0 行，无历史记录需要物理清除。

## 已验证

- `.venv` 已安装并可运行 `pytest 9.1.1`。
- `scripts/check_migrations.py` 通过，Alembic schema drift 检查通过。
- 重点测试通过：`89 passed`。
- 完整后端测试：`1243 passed, 6 skipped`。
- 前端测试：`104 passed`；`npm run build` 成功。
- Ruff、Python compileall、git diff check 已通过。

## 已清理的测试问题

`FROZEN_REPOSITORY_SURFACE` 已补齐已有的 `get_investigation_tree_node` 和 `promote_case_evidence` 调用点，架构边界测试和完整测试均通过。

## 仍未完成的产品语义

- 多个替代支持集的统一真值维护。
- 冲突字段的时间窗、实例身份、指标语义可比性判定。
- 根据冲突自动选择准确祖先节点的局部回溯。
- Source/MCP 与 Task Artifact 完全统一的 Ingestion contract。
- Evidence promote 后的 Claim/Hypothesis/Conclusion 完整共享与撤销传播。
- Pi Sidecar 内存 Session 还不是完整的并行业务分支账本。

## 继续命令

```bash
cd /Users/szjyl/Desktop/work/mini-drop
./.venv/bin/python -m pytest -q tests/test_architecture_boundaries.py::test_legacy_repository_facade_surface_cannot_grow_implicitly
./.venv/bin/python -m pytest -q
./.venv/bin/python scripts/check_migrations.py
```

前端验收：

```bash
cd /Users/szjyl/Desktop/work/mini-drop/web
npm test
npm run build
```

## 答辩口径

当前可宣称：Evidence、Task Artifact 采集、Projection、审核、Evidence 排除失效传播和 `RECHECK_REQUIRED` revalidation、generation fencing、分支 Evidence 和分支推理状态持久化、Evidence promote、旧链默认冻结。

不要宣称：通用 ATMS/ECRD、完整多支持集真值维护、完整自动冲突回溯、任意生产自动修复、完整实时拓扑平台。
