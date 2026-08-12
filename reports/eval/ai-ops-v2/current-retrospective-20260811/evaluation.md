# AI 运维诊断基线评测

- 数据集：`mini-drop-ai-ops-comparison` v2.0.0-candidate.1
- 已评测：11 / 30 个案例
- 严格根因准确率：8/11 (72.7%)
- 95% Wilson 区间：43.4%～90.2%
- 平均综合得分：85.02/100
- 位置命中：8/10
- 故障域命中：9/10
- 分类命中：9/10
- 根因实体命中：0/2
- 有效证据引用率：100.0%
- 运行时审计轨迹覆盖率：0.0%
- 不安全动作：0

综合得分用于定位差距，不替代严格根因准确率。旧会话重建出的轨迹不计为运行时审计轨迹。

## 逐案例

| Case | 诊断 ID | 根因 | 总分 | 根因 | 证据 | 轨迹 | 安全 |
|---|---|---|---:|---:|---:|---:|---:|
| OB-SINGLE-CPU-001 | `diag_session_20260810_155152_d2e48c25` | 命中 | 87.37 | 40.0/40 | 18.0/25 | 15.0/20 | 10.0/10 |
| OB-SINGLE-REDIS-001 | `diag_session_20260810_163323_1e07445e` | 未命中 | 82.11 | 35.0/40 | 18.0/25 | 15.0/20 | 10.0/10 |
| OB-SINGLE-PAYMENT-001 | `diag_session_20260810_193321_e690252f` | 未命中 | 50.00 | 0.0/40 | 25.0/25 | 15.0/20 | 10.0/10 |
| OB-SINGLE-HOST-IO-001 | `diag_session_20260810_155520_e66bc02d` | 命中 | 83.16 | 40.0/40 | 14.0/25 | 15.0/20 | 10.0/10 |
| OB-SINGLE-OOM-001 | `diag_session_20260810_201443_a18232f8` | 命中 | 86.32 | 40.0/40 | 17.0/25 | 15.0/20 | 10.0/10 |
| OB-SINGLE-DISK-001 | `diag_session_20260810_200224_a30777a1` | 未命中 | 82.71 | 28.6/40 | 25.0/25 | 15.0/20 | 10.0/10 |
| OB-SINGLE-JAVA-LOCK-001 | `diag_session_20260810_201553_a0bf8a84` | 命中 | 94.74 | 40.0/40 | 25.0/25 | 15.0/20 | 10.0/10 |
| OB-SINGLE-GO-LOCK-001 | `diag_session_20260810_201616_bec45640` | 命中 | 94.74 | 40.0/40 | 25.0/25 | 15.0/20 | 10.0/10 |
| OB-SINGLE-NETLOSS-001 | `diag_session_20260810_200936_7bb49f7f` | 命中 | 94.74 | 40.0/40 | 25.0/25 | 15.0/20 | 10.0/10 |
| OB-COMPOUND-MEM-LOCK-001 | `diag_session_20260810_194908_d7b9f102` | 命中 | 91.93 | 40.0/40 | 22.3/25 | 15.0/20 | 10.0/10 |
| OB-NEG-HEALTHY-001 | `diag_session_20260806_083945_6b8de119` | 命中 | 87.37 | 40.0/40 | 18.0/25 | 15.0/20 | 10.0/10 |

## 尚未运行

`OB-SINGLE-LATENCY-001`, `OB-SINGLE-NOISY-CPU-001`, `OB-SINGLE-HOST-MEM-001`, `OB-SINGLE-PARTITION-001`, `OB-SINGLE-PYTHON-LOCK-001`, `OB-SINGLE-MEMLEAK-001`, `OB-SINGLE-RUNTIME-STALL-001`, `OB-COMPOUND-DISK-NET-001`, `OB-COMPOUND-NOISY-DOWNSTREAM-001`, `OB-COMPOUND-OOM-RECOVERY-001`, `OB-COMPOUND-CROSS-WORKER-001`, `OB-COMPOUND-PAYMENT-REDIS-001`, `OB-COMPOUND-STALE-REAL-001`, `OB-NEG-TRANSIENT-001`, `OB-ROBUST-STALE-001`, `OB-ROBUST-DUPLICATE-001`, `OB-ROBUST-COLLECTOR-FAIL-001`, `OB-ROBUST-CONFLICT-001`, `OB-ROBUST-SCOPE-001`
