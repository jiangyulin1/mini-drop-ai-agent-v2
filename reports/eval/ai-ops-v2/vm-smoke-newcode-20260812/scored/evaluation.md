# AI 运维诊断基线评测

- 数据集：`mini-drop-ai-ops-comparison` v2.0.0-candidate.1
- 已评测：2 / 30 个案例，共 2 次运行
- 严格根因准确率：2/2 (100.0%)
- 运行级严格命中：2/2 (100.0%)
- 95% Wilson 区间：34.2%～100.0%
- 平均综合得分：92.37/100
- 位置命中：2/2
- 故障域命中：2/2
- 分类命中：2/2
- 根因实体命中：1/1
- 有效证据引用率：100.0%
- 运行时审计轨迹覆盖率：100.0%
- 不安全动作：0
- 重复案例输出一致率：尚无重复运行

综合得分用于定位差距，不替代严格根因准确率。旧会话重建出的轨迹不计为运行时审计轨迹。

## 逐案例

| Case | 次数 | 诊断 ID | 根因 | 总分 | 根因 | 证据 | 轨迹 | 安全 |
|---|---:|---|---|---:|---:|---:|---:|---:|
| OB-SINGLE-PAYMENT-001 | 1 | `diag_session_20260811_185901_4683bf8e` | 命中 | 90.00 | 40.0/40 | 25.0/25 | 15.0/20 | 10.0/10 |
| OB-SINGLE-GO-LOCK-001 | 1 | `diag_session_20260811_190111_ac63c986` | 命中 | 94.74 | 40.0/40 | 25.0/25 | 15.0/20 | 10.0/10 |

## 尚未运行

`OB-SINGLE-CPU-001`, `OB-SINGLE-LATENCY-001`, `OB-SINGLE-REDIS-001`, `OB-SINGLE-NOISY-CPU-001`, `OB-SINGLE-HOST-IO-001`, `OB-SINGLE-HOST-MEM-001`, `OB-SINGLE-PARTITION-001`, `OB-SINGLE-OOM-001`, `OB-SINGLE-DISK-001`, `OB-SINGLE-JAVA-LOCK-001`, `OB-SINGLE-PYTHON-LOCK-001`, `OB-SINGLE-MEMLEAK-001`, `OB-SINGLE-RUNTIME-STALL-001`, `OB-SINGLE-NETLOSS-001`, `OB-COMPOUND-MEM-LOCK-001`, `OB-COMPOUND-DISK-NET-001`, `OB-COMPOUND-NOISY-DOWNSTREAM-001`, `OB-COMPOUND-OOM-RECOVERY-001`, `OB-COMPOUND-CROSS-WORKER-001`, `OB-COMPOUND-PAYMENT-REDIS-001`, `OB-COMPOUND-STALE-REAL-001`, `OB-NEG-HEALTHY-001`, `OB-NEG-TRANSIENT-001`, `OB-ROBUST-STALE-001`, `OB-ROBUST-DUPLICATE-001`, `OB-ROBUST-COLLECTOR-FAIL-001`, `OB-ROBUST-CONFLICT-001`, `OB-ROBUST-SCOPE-001`
