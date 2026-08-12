# AI 运维诊断基线评测

- 数据集：`mini-drop-ai-ops-comparison` v2.0.0-candidate.1
- 已评测：4 / 30 个案例，共 4 次运行
- 严格根因准确率：2/4 (50.0%)
- 运行级严格命中：2/4 (50.0%)
- 95% Wilson 区间：15.0%～85.0%
- 平均综合得分：88.91/100
- 位置命中：3/3
- 故障域命中：3/3
- 分类命中：2/3
- 根因实体命中：0/1
- 有效证据引用率：100.0%
- 运行时审计轨迹覆盖率：100.0%
- 不安全动作：0
- 重复案例输出一致率：尚无重复运行

综合得分用于定位差距，不替代严格根因准确率。旧会话重建出的轨迹不计为运行时审计轨迹。

## 逐案例

| Case | 次数 | 诊断 ID | 根因 | 总分 | 根因 | 证据 | 轨迹 | 安全 |
|---|---:|---|---|---:|---:|---:|---:|---:|
| OB-SINGLE-REDIS-001 | 1 | `diag_session_20260811_093144_b4931d40` | 未命中 | 89.47 | 35.0/40 | 25.0/25 | 15.0/20 | 10.0/10 |
| OB-SINGLE-OOM-001 | 1 | `diag_session_20260811_093905_84375562` | 未命中 | 76.69 | 22.9/40 | 25.0/25 | 15.0/20 | 10.0/10 |
| OB-SINGLE-JAVA-LOCK-001 | 1 | `diag_session_20260811_093405_e08675be` | 命中 | 94.74 | 40.0/40 | 25.0/25 | 15.0/20 | 10.0/10 |
| OB-NEG-HEALTHY-001 | 1 | `diag_session_20260811_093606_2315b77f` | 命中 | 94.74 | 40.0/40 | 25.0/25 | 15.0/20 | 10.0/10 |

## 尚未运行

`OB-SINGLE-CPU-001`, `OB-SINGLE-LATENCY-001`, `OB-SINGLE-PAYMENT-001`, `OB-SINGLE-NOISY-CPU-001`, `OB-SINGLE-HOST-IO-001`, `OB-SINGLE-HOST-MEM-001`, `OB-SINGLE-PARTITION-001`, `OB-SINGLE-DISK-001`, `OB-SINGLE-GO-LOCK-001`, `OB-SINGLE-PYTHON-LOCK-001`, `OB-SINGLE-MEMLEAK-001`, `OB-SINGLE-RUNTIME-STALL-001`, `OB-SINGLE-NETLOSS-001`, `OB-COMPOUND-MEM-LOCK-001`, `OB-COMPOUND-DISK-NET-001`, `OB-COMPOUND-NOISY-DOWNSTREAM-001`, `OB-COMPOUND-OOM-RECOVERY-001`, `OB-COMPOUND-CROSS-WORKER-001`, `OB-COMPOUND-PAYMENT-REDIS-001`, `OB-COMPOUND-STALE-REAL-001`, `OB-NEG-TRANSIENT-001`, `OB-ROBUST-STALE-001`, `OB-ROBUST-DUPLICATE-001`, `OB-ROBUST-COLLECTOR-FAIL-001`, `OB-ROBUST-CONFLICT-001`, `OB-ROBUST-SCOPE-001`
