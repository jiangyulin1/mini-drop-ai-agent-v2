# Drop 可恢复执行流水线

本文说明普通 Drop 采集任务的确定性执行底座。AI 诊断建立在该底座之上，但不会参与采集结果是否成功的判定。

## 组件职责

- `server/`：HTTP/gRPC 控制面，创建 Task、分配 TaskAttempt、接收幂等结果、创建 AnalysisJob，并提供状态与审计 API。
- `agent/`：部署在目标 Linux 主机，心跳拉取 attempt，使用参数数组启动采集器，上传或登记制品，持久化待上报结果。运行中取消会终止整个采集进程组。
- `analyzer/`：独立 Worker。通过数据库租约领取 AnalysisJob，验证输入制品，再运行 perf 等确定性分析管线。只有租约 owner 可以提交。
- `proto/`：Server/Agent 的强类型通信契约。TaskDesc 携带 `attempt_id`；TaskResult 携带 attempt、错误码、退出码和资源摘要。
- `migrations/`：TaskAttempt、AnalysisJob、双阶段状态和 Analyzer 心跳表的版本化迁移。
- `web/`：展示聚合状态、采集/分析双状态、attempt 历史和 analysis job 历史。

## 正常执行

1. API 创建 Task，状态为 `PENDING`，同时写入采集队列 deadline。
2. 空闲 Agent 心跳时，Server 以数据库锁领取最早的 Task，创建唯一 TaskAttempt，并下发 `attempt_id`。
3. Agent 运行采集器；结果先原子写入本地 result spool，再调用 `NotifyResult`。网络或确认丢失时会使用相同 attempt 重放。
4. Server 以不可变来源字段生成 artifact identity，重复上报只返回已有 artifact，不产生重复记录。
5. 采集完成后 Task 的 `collection_status=COLLECTED`，Server 创建唯一 AnalysisJob，`analysis_status=PENDING`。
6. Analyzer Worker 领取租约，检查输入的可用性、大小、SHA-256 和最大输入限制，运行分析并发布结果。
7. Worker 在提交前再次验证租约。提交成功后 `analysis_status=SUCCEEDED`，聚合状态进入 `DONE`。

## 失败、取消与恢复

- 排队任务超过 `collection_deadline_at`：失败码 `COLLECTION_QUEUE_DEADLINE_EXCEEDED`。
- Agent 结果超时：attempt 标为 `LOST`，失败码 `AGENT_RESULT_TIMEOUT`。
- Agent 采集失败：上报稳定 `error_code`、`exit_code` 和资源摘要；人类可读信息单独保存。
- 用户取消：Task、活动 attempt 和待执行/运行中的 AnalysisJob 同步失效；Agent 下次心跳收到取消指令并终止采集进程组。
- Analyzer 崩溃：租约过期后其他 Worker 可重新领取；旧 owner 无权提交。
- Analyzer 输入损坏或超限：使用稳定错误码终止，不执行外部分析程序。
- 可重试分析错误：受 `max_retries` 限制，耗尽后确定性失败。

## 健康检查

- `/api/livez`：只表示 Server 进程存活，不因依赖故障触发无意义重启。
- `/api/readyz`：以只读方式检查数据库、对象存储，以及启用时的 Analyzer Worker；未就绪返回 HTTP 503，适合作为流量接入和发布激活门槛。
- `/api/readyz?core_only=true`：容器启动阶段使用，仍严格检查数据库与所需存储，但忽略 Analyzer，避免相互等待。
- `/api/livez`：只检查 Server 进程是否存活；依赖故障不会触发重启风暴。

对象存储 Bucket 只在启动阶段按 `MINIO_AUTO_CREATE_BUCKET` 初始化。健康探针不会创建或修改 Bucket；公开响应只返回稳定错误码，底层连接错误进入结构化日志，避免泄露凭据、主机名或内部拓扑。后台 Agent 离线检测、僵尸任务恢复、指标快照和诊断推进按步骤隔离失败，并通过 `mini_drop_maintenance_runs_total` 与 `mini_drop_maintenance_last_success_unixtime` 暴露运行状态，单个步骤失败不会让整个维护协程永久退出。

完整和 Control 部署设置 `MINI_DROP_REQUIRE_STORAGE=1`，对象存储异常会阻止流量接入；`docker-compose.local.yml` 使用共享本地卷并设置为 `0`，健康报告显示 `storage=disabled`，不会错误等待一个未启动的 MinIO。

## OpenTelemetry Trace

Trace 默认关闭，不影响没有可观测后端的部署。设置下列环境变量后，Server 会从 HTTP `traceparent` 创建 SERVER span，将新的上下文持久化到 Task，并经 gRPC 传播到 Agent；Analyzer 使用同一上下文建立异步 link。API 响应同时返回 `X-Trace-Id`，便于从任务定位整条链路。

```bash
MINI_DROP_TRACING_ENABLED=1
MINI_DROP_TRACE_EXPORTER=otlp        # 联调时也可使用 console
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_EXPORTER_OTLP_INSECURE=1
MINI_DROP_ENVIRONMENT=vm
```

OTLP Collector 不属于 Mini-Drop 的强制依赖；未部署 Collector 时保持关闭，或临时使用 `console` exporter 验证跨进程 trace id。

## Kafka 边界

当前复刻链路不把 Kafka 作为正确性依赖。Task、TaskAttempt、AnalysisJob、租约、幂等产物和审计事件均持久化在 SQL；Server 通过已有 gRPC 心跳分发任务，Agent 使用本地 spool 保证结果重放。该结构已经覆盖当前三节点环境中的崩溃恢复与重复投递问题，也避免额外常驻中间件占用资源。若未来需要把审计/遥测事件分发给多个外部消费者，可再通过 outbox 模式接入 Kafka，但不能用 Kafka 消息替代上述事实表。

## JVM async-profiler

`java_async` 采集器支持 async-profiler 4.x 的 `bin/asprof`，并兼容旧版 `profiler.sh`。Worker 需要安装 JDK/JRE 和 async-profiler，并配置：

```bash
ASYNC_PROFILER_HOME=/opt/async-profiler
```

任务参数 `event` 支持 `cpu`、`alloc`、`lock`、`wall`、`itimer`、`ctimer`；`output_format` 支持 `html`、`jfr`、`both`。`both` 会先生成 JFR，再通过同发行包的 `jfrconv` 转换 HTML。Agent 与目标 JVM 必须位于同一主机，并具备 attach/ptrace 权限。

## 本地验证

```bash
python scripts/compile_proto.py
python scripts/check_migrations.py
python -m pytest tests -q
python scripts/run_diagnosis_eval.py --output-dir reports/eval
npm --prefix web test -- --run
npm --prefix web run lint
npm --prefix web run build
```

Docker Compose 会同时启动 `server` 与 `analyzer`。裸机发布需安装并启用 `deploy/systemd/mini-drop-analyzer.service`。
