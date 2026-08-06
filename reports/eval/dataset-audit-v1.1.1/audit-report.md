# Mini-Drop 统一诊断测试集质量审计

- 测试集：`mini-drop-team-unified-diagnosis-benchmark` v1.1.1
- 环境：`mini-drop-hyperv-three-node`
- Case 数：10
- 成熟度与环境就绪度：**28.90 / 100**
- 本报告不代表 AI 正确率；它只衡量题目成熟度与环境就绪度

## 六维评分

| 维度 | 得分 | 满分 |
|---|---:|---:|
| 性能需求定义 | 2.00 | 20 |
| Oracle 质量 | 8.00 | 20 |
| 答案泄漏防护 | 2.20 | 15 |
| 可复现性 | 7.40 | 15 |
| 三节点环境适配 | 7.50 | 15 |
| 持续诊断与问题解决 | 1.80 | 15 |

## 环境就绪度

| Case | 状态 | 总分 | 缺失能力 |
|---|---|---:|---|
| T1-CODE-001 | RUNNABLE | 35.00 | — |
| T1-CPU-001 | RUNNABLE | 37.00 | — |
| T1-DOWNSTREAM-001 | UNSUPPORTED | 21.00 | connection_error, trace_error_edge |
| T1-GC-001 | UNSUPPORTED | 22.00 | gc_pause_or_count_change, latency_correlation |
| T1-IO-001 | RUNNABLE | 35.00 | — |
| T1-LOAD-001 | PARTIAL | 30.50 | latency_change, request_rate_change |
| T1-MEM-001 | PARTIAL | 29.50 | memory_profile_growth |
| T1-NET-001 | UNSUPPORTED | 23.00 | service_latency_change, trace_edge_latency |
| T1-NOISY-001 | RUNNABLE | 34.00 | — |
| T1-QUEUE-001 | UNSUPPORTED | 22.00 | producer_consumer_rate_gap, queue_lag_growth |

## 阻断项

- `T1-CODE-001` / `PERF_REQUIREMENTS_MISSING`：Define workload, baseline, incident, measurement and recovery thresholds.
- `T1-CODE-001` / `PUBLIC_PRIVATE_NOT_SEPARATED`：Store only the user-visible input under cases/public and move trigger/oracle to cases/private.
- `T1-CODE-001` / `EXECUTABLE_LIFECYCLE_INCOMPLETE`：Provide allowlisted commands or scripts for every lifecycle phase.
- `T1-CPU-001` / `PERF_REQUIREMENTS_MISSING`：Define workload, baseline, incident, measurement and recovery thresholds.
- `T1-CPU-001` / `PUBLIC_PRIVATE_NOT_SEPARATED`：Store only the user-visible input under cases/public and move trigger/oracle to cases/private.
- `T1-CPU-001` / `EXECUTABLE_LIFECYCLE_INCOMPLETE`：Provide allowlisted commands or scripts for every lifecycle phase.
- `T1-DOWNSTREAM-001` / `PERF_REQUIREMENTS_MISSING`：Define workload, baseline, incident, measurement and recovery thresholds.
- `T1-DOWNSTREAM-001` / `PUBLIC_PRIVATE_NOT_SEPARATED`：Store only the user-visible input under cases/public and move trigger/oracle to cases/private.
- `T1-DOWNSTREAM-001` / `EXECUTABLE_LIFECYCLE_INCOMPLETE`：Provide allowlisted commands or scripts for every lifecycle phase.
- `T1-GC-001` / `PERF_REQUIREMENTS_MISSING`：Define workload, baseline, incident, measurement and recovery thresholds.
- `T1-GC-001` / `PUBLIC_PRIVATE_NOT_SEPARATED`：Store only the user-visible input under cases/public and move trigger/oracle to cases/private.
- `T1-GC-001` / `EXECUTABLE_LIFECYCLE_INCOMPLETE`：Provide allowlisted commands or scripts for every lifecycle phase.
- `T1-IO-001` / `PERF_REQUIREMENTS_MISSING`：Define workload, baseline, incident, measurement and recovery thresholds.
- `T1-IO-001` / `PUBLIC_PRIVATE_NOT_SEPARATED`：Store only the user-visible input under cases/public and move trigger/oracle to cases/private.
- `T1-IO-001` / `EXECUTABLE_LIFECYCLE_INCOMPLETE`：Provide allowlisted commands or scripts for every lifecycle phase.
- `T1-LOAD-001` / `PERF_REQUIREMENTS_MISSING`：Define workload, baseline, incident, measurement and recovery thresholds.
- `T1-LOAD-001` / `PUBLIC_PRIVATE_NOT_SEPARATED`：Store only the user-visible input under cases/public and move trigger/oracle to cases/private.
- `T1-LOAD-001` / `EXECUTABLE_LIFECYCLE_INCOMPLETE`：Provide allowlisted commands or scripts for every lifecycle phase.
- `T1-MEM-001` / `PERF_REQUIREMENTS_MISSING`：Define workload, baseline, incident, measurement and recovery thresholds.
- `T1-MEM-001` / `PUBLIC_PRIVATE_NOT_SEPARATED`：Store only the user-visible input under cases/public and move trigger/oracle to cases/private.
- `T1-MEM-001` / `EXECUTABLE_LIFECYCLE_INCOMPLETE`：Provide allowlisted commands or scripts for every lifecycle phase.
- `T1-NET-001` / `PERF_REQUIREMENTS_MISSING`：Define workload, baseline, incident, measurement and recovery thresholds.
- `T1-NET-001` / `PUBLIC_PRIVATE_NOT_SEPARATED`：Store only the user-visible input under cases/public and move trigger/oracle to cases/private.
- `T1-NET-001` / `EXECUTABLE_LIFECYCLE_INCOMPLETE`：Provide allowlisted commands or scripts for every lifecycle phase.
- `T1-NOISY-001` / `PERF_REQUIREMENTS_MISSING`：Define workload, baseline, incident, measurement and recovery thresholds.
- `T1-NOISY-001` / `PUBLIC_PRIVATE_NOT_SEPARATED`：Store only the user-visible input under cases/public and move trigger/oracle to cases/private.
- `T1-NOISY-001` / `EXECUTABLE_LIFECYCLE_INCOMPLETE`：Provide allowlisted commands or scripts for every lifecycle phase.
- `T1-QUEUE-001` / `PERF_REQUIREMENTS_MISSING`：Define workload, baseline, incident, measurement and recovery thresholds.
- `T1-QUEUE-001` / `PUBLIC_PRIVATE_NOT_SEPARATED`：Store only the user-visible input under cases/public and move trigger/oracle to cases/private.
- `T1-QUEUE-001` / `EXECUTABLE_LIFECYCLE_INCOMPLETE`：Provide allowlisted commands or scripts for every lifecycle phase.

## 下一道门禁

Split public input from private trigger/oracle, add executable fixtures and quantified baseline/incident/recovery criteria before active AI trials.
