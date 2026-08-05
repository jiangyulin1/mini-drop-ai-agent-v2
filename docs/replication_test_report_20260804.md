# Mini-Drop 复刻功能与三节点测试报告（2026-08-04）

## 本轮范围

- 完善可恢复的 Task → TaskAttempt → AnalysisJob 执行链路。
- 增加可选 OpenTelemetry Trace，并贯通 HTTP、Server、Agent、Analyzer。
- 修复并实测 JVM async-profiler 4.4 的 JFR 与 HTML 火焰图采集。
- AI 功能不在本轮范围内；Kafka 不作为当前链路的必要依赖。
- 所有改动保留在本地工作区，没有 commit 或 push。

## 环境

| 节点 | 地址 | 角色 | 最终发布目录 | 关键能力 |
|---|---|---|---|---|
| control | 192.168.10.10 | Server、Analyzer、MinIO、Nginx | `/home/control/mini-drop-release-20260804-replica-v3` | SQL 迁移 0004、Console Trace |
| worker1 | 192.168.10.11 | Agent、JVM 采集节点 | `/home/worker1/mini-drop-release-20260804-replica-v3` | OpenJDK 17、async-profiler 4.4、`java_async` |
| worker2 | 192.168.10.12 | Agent、对照节点 | `/home/worker2/mini-drop-release-20260804-replica-v3` | 不声明 `java_async` |

## 测试集设计

| 层级 | 目标 | 主要用例 | 通过条件 |
|---|---|---|---|
| 静态契约 | 防止协议/迁移漂移 | Proto 重生成、Alembic 从空库升级、schema drift | 生成文件一致，数据库到 0004，无漂移 |
| 单元 | 隔离边界条件 | spool v1/v2/v3、trace 开关/W3C 上下文、asprof 命令/JFR/目录权限 | 断言稳定错误码、字段与命令参数 |
| 组件 | 验证持久化执行语义 | attempt 唯一性、结果重放、artifact 幂等、分析租约丢失、取消、超时 | 无重复事实记录，只有 lease owner 可提交 |
| API/UI | 验证操作者入口 | 任务详情、attempt/job 历史、产物下载、前端测试/Lint/Build | API 状态一致，前端可构建 |
| 三节点黑盒 | 验证真实跨进程链路 | Worker2 `sys_metrics`、Worker1 `java_async both` | Task DONE，Analyzer SUCCEEDED，产物可下载 |
| 故障发现 | 验证真实权限模型 | root Agent 采集普通用户 JVM | JVM 可创建输出；父目录仅增加 traverse 权限 |
| 可观测性 | 验证异步关联 | 固定 W3C traceparent 创建任务 | HTTP/dispatch/collector/result 同 trace id；Analyzer span 含原 trace link |

## 已执行结果

- 后端：443 tests passed。
- 前端：10 个测试文件、25 tests passed；ESLint 通过；Vite production build 通过。
- Proto：重新生成通过。
- Alembic：空库升级至 `0004_task_trace_context`，schema drift 检查通过。
- Control 健康检查：database、storage、analyzer 均为 `ok`。
- Mini-Drop systemd 服务：Server、Analyzer、MinIO、Nginx、两个 Agent 均为 active。

### Trace 黑盒任务

- Task：`task_20260804_115903_f95cb4`
- Collector：Worker2 `sys_metrics`
- 结果：`DONE`；Attempt `COLLECTED`；AnalysisJob `SUCCEEDED`。
- 入口和同步阶段 trace id：`aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`。
- Analyzer 使用独立异步 span，并在 links 中保存上述入口 trace/span 上下文。

### JVM async-profiler 黑盒任务

- 最终 Task：`task_20260804_120404_5869cd`
- Collector：Worker1 `java_async`，`event=cpu`，`output_format=both`。
- 结果：`DONE`；Attempt `COLLECTED`；AnalysisJob `SUCCEEDED`。
- JFR：20,618 bytes，SHA-256 `f832248d5111f0693be88730afbd4ab1cd9fd7736593faaa7c34da3f0ca7673a`，文件魔数 `46 4c 52 00`。
- HTML：14,924 bytes，SHA-256 `a420c009e91ead1287386bb95e432e3e87ffe9be195a97e9e1a5beaebb8bfb38`。
- 两项产物均已上传对象存储、经 API 下载并重新计算哈希一致。
- Trace id：`99999999999999999999999999999999`，Server/Agent 同链路可检索，Analyzer 通过 link 关联。

## 测试中发现并修复的问题

首次 JVM 真机采集返回 `Could not open Flight Recorder output file`。原因是 root Agent 创建 `/tmp/mini-drop` 为 0750，目标 JVM 用户无法穿越父目录，即使任务子目录已更换所有者也无法打开 JFR。最终修复为：父目录只开放 traverse（0711，不开放目录列表），任务目录 chown 给目标 JVM UID/GID 并保持 0750。修复后 JFR 与 HTML 均通过真实采集和下载校验。

两个失败的 Java 尝试作为故障审计记录保留；临时 Java 压测进程、源码、class 与日志已经停止并清理。

## 后续讨论点

- 当前用 Console exporter 证明 Trace 语义正确；若需要跨重启查询、检索和 UI，应再部署 OpenTelemetry Collector 与后端（例如 Tempo/Jaeger），把 exporter 切为 OTLP。
- Kafka 只有在出现多个外部事件消费者、跨系统流式订阅或显著吞吐压力时才值得引入；当前三节点复刻不需要。
- Worker1 有一个与 Mini-Drop 无关的 `fwupd-refresh.service` failed 状态；Mini-Drop Agent 本身正常，不影响本轮结果。
