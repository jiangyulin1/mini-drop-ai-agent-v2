# 展示闭环收敛记录

日期：2026-08-24  
目标：让 Mini-Drop 先稳定展示一条完整的 Evidence-native 调查路径，不把未完成的通用生产语义混入本轮交付。

## 本轮唯一主路径

```text
创建 Case
  -> 创建隔离调查分支
  -> 读取当前分支 Evidence
  -> 形成 Hypothesis / Causal Graph
  -> 提交 Evidence-bound Conclusion
  -> Workspace 展示范围、证据、验证、结论和下一步
```

演示结论允许是 `PARTIALLY_CONFIRMED`。如果替代假设或关键 Evidence Gap 没有被排除，系统必须展示限制，而不是把单条支持信号包装成绝对根因。

这条路径由 `tests/test_showcase_hero_path.py` 作为可执行合同保护。展示合同通过已完成的原生 Task 和 Artifact，再调用 Case attachment API 物化 canonical Evidence；不直接写入 Case Evidence。

## 收敛边界

本轮纳入展示主路径：

- Case 创建和范围展示；
- Branch 创建、切换和分支 Evidence 可见性；
- Evidence Projection、Hypothesis、Causal Graph；
- Evidence 引用校验和结论提交；
- Workspace 路径状态、失效提示和结论展示；
- Review/Exclude 后的 stale 传播作为第二个演示场景。

本轮冻结为兼容层，不继续扩展：

- 旧 `DiagnosisSession` / 规则 RCA 链；
- 非主路径的长期目标、复杂拓扑、集群 fanout 和恢复动作；
- 通用多支持集真值维护、自动冲突回溯和完整跨分支共享撤销；
- 深层生产部署、TLS、外部 Provider 稳定性和大规模性能优化。

冻结不等于整组删除。旧入口默认不允许创建或推进 DiagnosisSession；只有显式 `MINI_DROP_ENABLE_LEGACY_DIAGNOSIS=1` 才用于兼容维护。可复用的采集器、Artifact、Evidence parser/verifier、审计、fence 和恢复治理继续进入新方案；新展示入口只走 Evidence-native Workspace。

## Evidence 维护清理记录

2026-08-24：新链路的 Review 真值已收敛到 `EvidenceReviewRevision`。活动 Workspace、Agent Tool Gateway、Evidence Analysis 和 Review API 不再读取旧 `evidence_reviews` 表；旧表模型和历史数据暂留，仅用于迁移兼容。旧 `DiagnosisEvidence` 仍属于冻结的 `/diagnoses` 兼容链，不允许新 Case Workspace 读取或写入。

本次清理没有物理删除历史表，也没有删除可复用模块。当前本地 `mini_drop.db` 的旧 Diagnosis/RCA 表均为 0 行，因此本轮没有历史记录需要清除。后续只删除已经迁移、确认无复用价值且完成调用方清零的在线代码；旧表是否 drop 另行经过迁移评审。

## 进度记录

| 项目 | 状态 | 证据 |
|---|---|---|
| 主路径 API 串联 | 已完成 | `test_showcase_hero_path.py` |
| 分支 Evidence 隔离 | 已完成 | `test_agent_tool_gateway.py` |
| Branch Hypothesis/Gap/Causal/Conclusion | 已完成 | `test_investigation_state.py` |
| Review 后失效传播 | 已完成 | `test_agent_beta_cross_feature.py`、`test_case_evidence.py` |
| Task Artifact → Case Evidence → Projection 主路径 | 已完成 | `test_showcase_hero_path.py`、`test_case_evidence.py` |
| Review/Exclude 后 Workspace `RECHECK_REQUIRED` | 已完成 | `test_showcase_hero_path.py`、`test_case_evidence.py` |
| 前端 Workspace 展示 | 已完成 | `CanonicalCaseWorkspace.test.jsx` |
| Source/MCP 统一 Ingestion | 延后 | 非展示主路径 |
| 完整共享撤销传播 | 延后 | 非展示主路径 |
| 冲突比较与自动局部回溯 | 延后 | 非展示主路径 |

## 验收命令

```bash
cd /Users/szjyl/Desktop/work/mini-drop
./.venv/bin/python -m pytest -q tests/test_showcase_hero_path.py
./.venv/bin/python -m pytest -q tests/test_agent_tool_gateway.py tests/test_investigation_state.py
./.venv/bin/python scripts/check_migrations.py

cd web
npm test -- --run
npm run build
```

## 下一步记录规则

每次继续推进只允许回答三个问题：

1. 主路径哪一个节点还不能从真实 API 走通？
2. 修复是否改变了 Evidence、branch 或 conclusion 的真值语义？
3. 是否新增了可执行测试和本文件的状态记录？

如果变更不能改善主路径或验证其可靠性，暂不进入本轮展示候选。
