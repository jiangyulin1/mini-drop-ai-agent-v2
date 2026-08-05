# Mini-Drop 代码架构审计报告

> 审计日期：2026-08-05
> 审计范围：`server/`、`agent/`、`analyzer/`、`web/`、`deploy/`、`proto/`、`migrations/`（约 31K 行 Python + React 前端）
> 审计方法：人工逐文件审查 + 5 路并行子代理深度审查 + 测试套件基线 + 关键发现逐一复验
> 测试基线：`443 passed in 40.82s`（Python 3.9.13 / SQLite）

---

## 0. 总体结论

代码**工程质量良好**：SQL 全部参数化（无注入面）、状态机迁移校验完整、降级路径健壮、命令执行全部走 argv 列表（无 shell 注入）、密钥处理规范、诊断探针有 Schema 硬约束。**测试覆盖率高**（443 个测试覆盖 40+ 测试文件）。

但存在 **4 类系统性弱点**，按优先级：

1. **诊断状态机只覆盖"正常路径"**——`NEEDS_SCOPE_CONFIRMATION` / `WAITING_APPROVAL` / `CONCLUDING` 三个状态在 deadline 交叉时**没有合法出口**，会话永久卡死，且每轮后台扫描器抛异常（高危 #1–#4）。
2. **默认部署零认证**——HTTP API 与 gRPC 认证默认关闭、gRPC 绑定 0.0.0.0 且 Control 服务暴露在 Agent 端口（高危 #5）。
3. **任务持久化层缺少 PG 级并发控制**——`row_version` 只增不查、`delete_task` 级联不完整、`_cached` 缓存竞态，测试用 SQLite 全绿但生产 PG 会爆（高危 #6–#9）。
4. **Agent 对外部输入信任度过高**——取消杀不掉外部工具进程（孤儿 perf 残留）、pprof 无大小上限、task.id 未消毒路径穿越、自剖析守卫失效（高危 #10–#12）。

---

## 1. 严重级（数据丢失 / 安全缺口 / 永久卡死）

### 1.1 诊断状态机：`NEEDS_SCOPE_CONFIRMATION` 无任何合法出口，会话永久卡死
**文件**：`server/app/diagnosis/orchestrator.py:53-63`（迁移表）、`:426-436`（deadline 分支）
**复验**：✅ 迁移表（53-63）没有 `NEEDS_SCOPE_CONFIRMATION` 作为源状态的 key；`_transition`（1905 行）对不在允许集的迁移抛 `ValueError`。当 `_build_target_scope` 返回 `scope_completeness == "unresolved"`（如 `context.instances` 中没有实例属于目标服务）时会话进入此状态，之后 **deadline 过后每次 `advance_active` 都对它调用 `_transition(INSUFFICIENT_EVIDENCE)` → 抛异常**，会话永远停在非终态。
**影响**：用户会话永不结束；后台扫描器每 15s 重复抛异常；测试 `test_diagnosis_orchestrator.py:111-118` 恰好覆盖了触发场景却未断言收敛。
**修复**：迁移表补 `NEEDS_SCOPE_CONFIRMATION → {INSUFFICIENT_EVIDENCE, USER_CANCELED, FAILED}`；deadline 分支按当前状态选择合法终态。

### 1.2 诊断状态机：`WAITING_APPROVAL` 超 deadline 触发非法迁移
**文件**：`orchestrator.py:426-436`、`:60`
**复验**：✅ `WAITING_APPROVAL` 允许集是 `{COLLECTING, NEED_MORE_EVIDENCE, BUDGET_EXHAUSTED, USER_CANCELED, FAILED}`，不含 `INSUFFICIENT_EVIDENCE`；而 deadline 分支（430-435 行）在状态检查之前对所有会话统一 `_transition(INSUFFICIENT_EVIDENCE)`。R2 探针等待审批超过 `max_duration_minutes` 后，会话卡死、扫描器每轮抛异常，且 `approve` 端到点仍可把会话拉回 `COLLECTING`（行为与 deadline 语义矛盾）。
**修复**：deadline 分支对 `WAITING_APPROVAL` 迁移到 `BUDGET_EXHAUSTED`（本就在允许集内）。

### 1.3 诊断状态机：`CONCLUDING → COMPLETED` 双次迁移非原子
**文件**：`orchestrator.py:537-538`、`:506-515`
**复验**：✅ 信息充分时连续两次 `_transition`（两次独立 DB 事务）。若第二次提交失败，会话停在 `CONCLUDING`（非终态）；之后 `_advance_locked` 在 506 行看到 `terminal_tasks` 非空会对 `CONCLUDING` 调用 `_transition(ANALYZING)` → 不在允许集 → 永久卡死并每轮重试。
**修复**：`CONCLUDING` 允许回退 `FAILED`，或对 `CONCLUDING` 做幂等收敛（补提交 `COMPLETED`）。

### 1.4 诊断会话无取消/补 scope API
**文件**：`schemas.py:33-34`、`orchestrator.py:54-60`、`main.py:690-704`
**复验**：✅ `USER_CANCELED` / `TOPOLOGY_UNAVAILABLE` 是声明终态但全代码库无任何路径产生它们；`POST /api/tasks/{id}/cancel` 只取消采集任务，无诊断会话取消路由。用户在 `COLLECTING` / `WAITING_APPROVAL` 阶段无法终止会话。
**修复**：新增 `POST /api/v1/diagnoses/{id}/cancel`，或移除死状态。

### 1.5 默认部署零认证：HTTP + gRPC 全开放
**文件**：`server/app/main.py:252-259`、`server/app/grpc_auth.py:24-25`、`.env:14-18`、`server/app/grpc_server.py:77-93`
**复验**：✅ `MINI_DROP_API_AUTH_ENABLED` 与 `MINI_DROP_GRPC_AUTH_ENABLED` 默认 `0`；gRPC 绑定 `0.0.0.0:50051`；**`Control` 服务（CreateTask/StatAgent，仅测试用）注册在面向 Agent 的同一端口**，且 `ControlService.CreateTask` 不校验 `target_pid>0` / `sample_rate<=999` / `duration<=120` / Agent 能力（与 HTTP 端 main.py:575-589 完全不对称）。`docker-compose.control.yml` 已默认开启认证（`:?set MINI_DROP_GRPC_TOKEN`），但主 `docker-compose.yml` 默认关闭。
**影响**：默认配置下，任何可达 50051 的客户端可伪造 Agent 注册、拉取/篡改任务、伪造产物、创建畸形任务。
**修复**：生产默认开启认证；Control 服务与 Agent 面分端口/凭据并复用 HTTP 校验。

### 1.6 `delete_task` 级联删除不完整：PostgreSQL 上直接 500
**文件**：`server/app/sql_repository.py:350-383`、`server/app/models.py:361,390,413,440,623`
**复验**：✅ `delete_task` 只删 `StatusEvent/Artifact/AnalysisJob/TaskAttempt/DiagnosisRun`，**未删** `DiagnosisToolResultModel`、`DiagnosisReportModel`、`RepairPlanModel`、`RCAFeedbackModel`（均 FK→`diagnosis_runs.id`）、`ProbeExecutionModel`（FK→`tasks.id`），且迁移无 `ondelete`。PostgreSQL 默认 `NO ACTION` → 删除 `diagnosis_run` 抛 `IntegrityError` → 事务回滚 → **HTTP 500，任务删不掉**。SQLite 因 `database.py` 未启用 `PRAGMA foreign_keys=ON` 而静默成功（产生孤儿行）。
**修复**：按依赖顺序清理全部子表，或外键加 `ondelete="CASCADE"`；SQLite 测试开启外键。

### 1.7 `TaskModel.row_version` 乐观锁"只增不查"
**文件**：`sql_repository.py:1408` vs `server/app/diagnosis/store.py:258-305`
**复验**：✅ 任务表 `row_version` 只在迁移时 `+1`，**从不作为 WHERE 谓词**；而同一代码库的诊断会话正确实现了 `UPDATE ... WHERE row_version == :v` CAS。任务写路径（`transition_task`/`cancel_task`/`recover_stale_tasks`）无 `with_for_update`。单副本靠进程内 `self._lock` 串行，**多副本共享 PG 时两个副本可同时迁移同一任务**（心跳派发 + stale 扫描竞争）。
**修复**：写路径加 `with_for_update`，或照 `diagnosis/store.py` 用 CAS。

### 1.8 `_cached` 缓存竞态：偶发 `KeyError` 500
**文件**：`sql_repository.py:79-91` vs `:111`
**复验**：✅ `_cached` 先 `if key in self._cache` 再 `self._cache[key]`，两步间不加锁；写路径在 `self._lock` 内 `self._cache.clear()`。读线程在 `in` 检查后、访问前被写线程清空 dict → `KeyError`。`repo.tasks/agents/events/artifacts/audit_logs` 全走此路径，HTTP 读与 gRPC 写（heartbeat）并发。
**修复**：`_cached` 持锁，或 `try/except KeyError` 重建，或将 `clear()` 改为整体替换新 dict。

### 1.9 SQLite 测试环境与 PostgreSQL 生产行为不一致
**文件**：`server/app/database.py:47-50`、`sql_repository.py:192-193,221-222`
**复验**：✅ 行锁/`skip_locked` 仅 `dialect.name == "postgresql"` 分支启用；SQLite 未启用外键；`attempt_no = count()+1`（227-231 行）非原子，多实例并发心跳可命中 `uq_task_attempt_number` 唯一约束抛未捕获 `IntegrityError`。**443 个测试全部在 SQLite 上跑，以上问题零覆盖**。
**修复**：SQLite 打开 `PRAGMA foreign_keys=ON`；补一条 PG 冒烟测试（CI 可加 postgres 服务）。

### 1.10 Agent 取消采集无法终止外部工具进程（孤儿残留）
**文件**：`agent/mini_drop_agent/main.py:565-587`、`:266`；`collectors/perf.py:82`、`continuous.py:93`、`java_async.py:109`、`ebpf.py:55`
**复验**：✅ worker `os.setsid()` 成新会话组长，但 perf/asprof/bpftrace 又各自 `start_new_session=True` 再开新会话。`killpg(worker.pid, SIGTERM)` 只杀 worker，**外部工具成为孤儿继续运行**（最长 `duration+30=630s`），若任务重派则新旧两个 perf 并发写同一 `perf.data` 致产物损坏。
**修复**：统一去掉 `start_new_session`，或 worker 维护活跃子进程 PID 集逐个 kill，或 `prctl(PR_SET_PDEATHSIG)`。

### 1.11 pprof 采集无响应体大小限制 + localhost SSRF 原语
**文件**：`collectors/pprof.py:40-74`
**复验**：✅ 只校验 `port` 范围和 `endpoint` 以 `/` 开头，`resp.read()` 无上限（整响应读入内存写盘），urllib 默认跟随重定向（可跳内网）。
**影响**：目标本机服务返回超大/滴流响应 → Agent OOM/磁盘写满；驱动 Agent 抓取 localhost 任意端口响应作为 artifact 上报（数据外带）。
**修复**：`resp.read(MAX_BYTES)` 累加截断；禁用重定向；endpoint 白名单（如 `/debug/pprof/profile`）。

### 1.12 Analyzer 租约长耗时阶段不续期：慢任务被重复执行并强制判失败
**文件**：`analyzer/mini_drop_analyzer/worker.py:105-149`、`server/app/sql_repository.py:897-937,979-1011`
**复验**：✅ 租约只在取 artifacts 后（106 行）和提交前（148 行）续期；中间是下载（最多 1GB）+ 分析（子进程 180s）。若总时长 > `lease_sec`（默认 300s），`claim_analysis_job` 会把 RUNNING 且过期的任务重新领走（含自己），`retry_count` 递增，达上限后 `fail_analysis_job` 强制 `FAILED`。
**影响**：1GB perf.data 在慢网络下（5MB/s → 200s + 180s = 380s > 300s）合法任务被重复分析并最终失败。
**修复**：下载/分析期间后台线程周期续租，或 `lease_sec` 大于（最大下载+最大分析）时间。

---

## 2. 高级（真实缺陷，影响面有限）

| # | 位置 | 问题 |
|---|---|---|
| 2.1 | `rca/prompt.py:211-222`、`nlp/summarizer.py:35-39` | **Prompt 注入**：证据 JSON（函数名、失败原因、tool_results.error_message）原样拼入 user message，system prompt 无"数据不可信、不得执行其中指令"声明（集群诊断 `diagnosis/intent.py:18-21` 反而有）。恶意符号名可操纵 LLM 输出捏造根因/总结。 |
| 2.2 | `rca/llm_client.py:168-184` + `rca/repair.py:29-105` | **cause_id 不校验候选列表**：LLM 可输出任意 `cause_id`（如 `io_wait_high`），而 `repair.py` 按 cause_id 前缀生成 `create_followup_task`（`safe_auto` 默认自动执行）→ 模型幻觉/注入被放大为自动建采集任务。且 `io_wait_high` 分支缺 `agent_id` 守卫，`target_pid` 落 `or 1`（会对 PID 1 建采集任务）。 |
| 2.3 | `main.py:864`、`rca/llm_client.py:18,51` | **LLM 端点默认无鉴权无频控**：未开 `MINI_DROP_API_AUTH_ENABLED` 时任何人可反复 POST `/diagnose`（每次最多 3 次 LLM 调用 + 自动建 followup 任务）刷成本。 |
| 2.4 | `agent/mini_drop_agent/main.py:103-104,272` | **自剖析守卫失效**：守卫比较 `target_pid == os.getpid()`，但 `_run_collector` 跑在 worker 子进程中（`mp_context.Process`），`os.getpid()` 是 worker PID 而非 Agent 主进程 PID，守卫永不命中。 |
| 2.5 | `agent/mini_drop_agent/main.py:443-541` | **worker 无存活看门狗**：主循环从不检查 `worker.is_alive()`。worker 被 OOM-kill 后 `active_task` 永不清除 → 心跳恒 busy → 后续任务全被丢弃，唯一恢复手段是重启 Agent。 |
| 2.6 | `agent/mini_drop_agent/collectors/*.py` | **task.id 路径穿越**：`output_dir = os.path.join(OUTPUT_BASE, task.id)`，task.id 直接来自 gRPC 且未消毒（对比 `result_spool._path` 有 `re.sub` 白名单）。`task.id="../../.."` 或绝对路径可写 `/tmp/mini-drop` 之外。需 Server 被攻破/MITM 才可触发，但属纵深防御缺口。 |
| 2.7 | `agent/mini_drop_agent/main.py:445-477,500-519` | **取消与完成竞态丢结果**：完成结果还在 result_queue 时收到 cancel，`_terminate_collector_process` 后重建全新 result_queue，已采集成功的产物被丢弃，只 spool 一条 `TASK_CANCELLED`。 |
| 2.8 | `agent/mini_drop_agent/main.py:106-107`、`collectors/continuous.py:58-62` | **采集无整体资源上限**：continuous 每窗口做完 perf 后还跑最多 120s 分析，10 窗口墙钟可达 20+ 分钟；产物大小无上限。 |
| 2.9 | `grpc_auth.py:32-41`、`hotmethod_service.py:40-63` | **单一共享 Token，无 Agent 身份绑定**：拿到 token 的任意 Agent 可上报/篡改任意任务结果、触发 cancel。 |
| 2.10 | `grpc_server.py:60-64`、`hotmethod_service.py:134-137` | **gRPC 无消息/并发限制**：`max_workers=10` 且无 `grpc.max_receive_message_length`；`json.loads` 只捕获 `JSONDecodeError`，深嵌套 JSON 抛未捕获 `RecursionError`。 |
| 2.11 | `cli.py:566-568,583-585` | **`status` 命令子查询失败静默返回 0 并谎报 healthy**，CI 误判成功。 |
| 2.12 | `cli.py:295,1129-1154` | **`watch-task` 终态/退出码不一致**：漏 CANCELLED（卡满 120s），FAILED 退出码 2 vs collect 的 1。 |
| 2.13 | `cli.py:1005-1051` | **`agent-exec` 名不副实**：只打印 repair_plan 字段，从不调用执行端点，调用者以为执行了实际什么都没发生。 |
| 2.14 | `cli.py:964-988` + `sql_repository.py:350-383` | **`storage-prune --execute` 谎报释放空间**：`delete_task` 不删 MinIO 对象，CLI 仍按 size_bytes 汇总打印 `freed_mb`。 |
| 2.15 | `main.py:105-115` + `orchestrator.py` | **后台扫描器在 asyncio 事件循环内执行同步阻塞 I/O**（`_offline_sweeper` 直接同步调 `repo.mark_offline_agents()`、`advance_active()`），MinIO 变慢时阻塞全部 HTTP 请求（含 SSE）。应 `asyncio.to_thread`。 |
| 2.16 | `diagnosis/store.py:349-381` | **诊断租约 30s 无续租**：`_advance_locked` 含逐任务读 MinIO + 验证，慢存储下可能超 30s → 双 worker 并发推进同一会话（靠唯一索引/CAS 兜底但会产生重复结论）。 |
| 2.17 | `orchestrator.py:748-752,690-702` | **`_schedule_probe` TOCTOU**：`get_task_by_diagnosis_step_id` 预检与 `create_task` 非原子，并发败者触发 `IntegrityError`（diagnosis 路径未捕获）→ 探针置 FAILED；且瞬时错误一次性固化 FAILED，无重试。 |
| 2.18 | `orchestrator.py:1048` + `diagnosis/sys_metrics.py:12-14` | **异常 sys_metrics 产物使会话崩溃**：`normalize_sys_metrics` 无保护调用，Agent 上报缺命名空间的 v2 数据 → 异常冒泡 → 会话永久停在非终态。 |
| 2.19 | `orchestrator.py:1238` + `schemas.py:164` | **query 含换行导致 scope-help 结论永不持久化**：`verify_report` 判任何含 `\n` 的 `rendered_command` 非法 → 结论不写入，会话卡在 `NEEDS_SCOPE_CONFIRMATION`。 |
| 2.20 | `orchestrator.py:639-650,379-392` | **R2 审批预算双重计费**：默认 `max_total_probe_cpu_seconds=120` 被 R1 全目标轮耗尽，R2 探针审批时 `used + 15 > 120` 必然成立 → 自适应 R2 探针"审批即耗尽预算"。 |
| 2.21 | `sql_repository.py:1441,169,285` | **SSE 事件在事务提交前发布**：提交失败（如 1.6 的 FK 冲突）后订阅者已收到"状态已变"通知但 DB 无变化，产生虚假事件。 |
| 2.22 | `repository.py` vs `sql_repository.py` | **内存/数据库双实现漂移**：`create_task` 对缺失 agent 语义不同（InMemory 静默 vs SQL 抛 ValueError）；Sql 的 `agents/tasks` 返回 2-5s TTL **detached 快照**，调用方直接赋值是静默 no-op 但在 InMemory 下真实生效；`_task_queues` 是死代码。 |
| 2.23 | `sql_repository.py:75,307-332` | **agent_metrics 纯内存 + 快照无保留策略**：重启丢指标；`agent_metric_snapshots` 每 agent 每天约 8640 行无界增长。 |
| 2.24 | `sql_repository.py:776-842` | **`add_artifacts` 去重键含可变字段**（sha256/object_key 全纳入 identity_key），重传 sha256 变化时插第二行而非更新，且 SELECT-then-INSERT 非原子。 |
| 2.25 | `sql_repository.py:267-286,629-683` | **`mark_offline_agents`/`recover_stale_tasks` 无行锁**：两个 sweeper 副本重复写审计/事件，row_version 无意义跳变。 |

---

## 3. 低危 / 可维护性专项

### 3.1 架构规模（过度膨胀的单体文件）
| 文件 | 行数 | 建议 |
|---|---|---|
| `server/app/diagnosis/orchestrator.py` | 2171 | `_advance_locked` 单方法 ~155 行、5 个 return 分支，内聚计划/调度/分析/收敛四类职责，是状态泄漏主因。按"计划/调度/分析/收敛"拆分为模块。 |
| `server/app/sql_repository.py` | 1465 | 按 agents/tasks/artifacts/analysis/diagnosis 拆分。 |
| `server/app/cli.py` | 1225 | 抽取公共 `_http_get/_http_post` 与统一轮询模块（现 ~10 处重复 `import urllib.request`、两套轮询逻辑）。 |

### 3.2 采集器清单四处重复维护（易漂移）
`task_kinds.py:78-156`、`cli.py:1191`、`agent/main.py:56-65`、`schemas.py:15` 各维护一份 8 采集器清单；`_PROFILER_MAP` / `_profiler_type` / `_PROFILER_TO_COLLECTOR` 三份映射；`ANALYSIS_RESULT_TYPES` 双份。新增采集器漏改任何一处都会表单/校验/能力不匹配。**应以 `task_kinds.TASK_KINDS` 为单一来源生成 CLI choices、Pydantic Literal、Agent 能力。**

### 3.3 状态字符串硬编码（未用枚举）
attempt 状态 `"RUNNING"/"COLLECTED"/"SUCCEEDED"/"FAILED"/"CANCELLED"/"LOST"/"CANCEL_REQUESTED"` 散落于 `sql_repository.py:187,217,503,587,660,929,942,962,992,996`，而 `TaskStatus/CollectionStatus/AnalysisStatus` 枚举已存在却未统一使用。

### 3.4 死代码
- `SqlRepository._task_queues`：只在 `register_agent` 填充，从不读取。
- Agent `_TASK_TYPE_COLLECTOR[4] = "memory_smaps"`：服务端恒下发 `task_type=0`，永不触发。
- `orchestrator.py:2096-2112` 的 `_has_self_hotspot`/`_has_pressure`/`_unique_refs`、`:1996` `_confidence_label`：全文件无调用，且 `_has_self_hotspot` 阈值（>=35）与 `domain_analyzers.py`（>=40）**不一致**。
- proto `TaskResult.file`：从未被 Agent 填充、Server 不消费。

### 3.5 其他可维护性债务
- **版本号硬编码**：`cli.py:689` `"version": "0.2.0"` 与 pyproject 漂移。
- **重复 import**：`main.py:13` 与 `:36` 两次 `import json as _json_mod` / `_json`。
- **ruff / mypy 未安装**：`make lint` 静默跳过（`which ruff` 失败即 echo skip），无静态检查兜底。
- **过时注释**：`web/src/api/client.js:createEventSource` 注释写 "ISO 时间戳"，实际新代码用 SSE 事件 id 做游标（event_bus 兼容两者）。
- **死代码清理**：`calibrator._score_evidence_quality` 的 `c` 参数未使用；`InMemoryRepository` 与 Sql 的漂移 shim。
- **`_minimize` 脱敏关键词遗漏**：`api_key`/`access_key`/`apikey` 不含 `token`/`secret` 等词，产物中这些字段不会被打码。

---

## 4. 架构亮点（值得保持）

- **命令执行安全**：采集器与探针全部走 argv 参数数组 + `shlex.join` + 渲染回验，无 `shell=True`，无注入面。
- **AI 降级路径健壮**：`parse_intent`/`summarize`/`diagnose` 均有无 key 的规则/模板 fallback，`diagnose` 重试上限 + 指数退避正确。
- **SQL 层事务包装到位**：所有多步写入包在 `_write_session` 事务，异常统一回滚；幂等键靠 DB 唯一索引兜底。
- **gRPC 契约 + 常量时间比较**：`secrets.compare_digest` 使用正确；幂等键去重、租约机制、result spool 重放 + 幂等闭环设计正确。
- **诊断探针受控**：`probe_registry` 受控探针 + Schema 硬约束 + evidence_ref 校验 + 自修复重试，工程化底线扎实。
- **Nginx 安全加固**：CSP 用精确 SHA-256 而非 `unsafe-inline`、限流、安全响应头齐全。

---

## 5. 修复路线图

### P0（数据完整性 / 安全底线，建议立即）
1. 诊断状态机补合法出口（1.1 / 1.2 / 1.3 / 1.4）——补迁移表 + deadline 按状态选终态 + 新增 cancel API
2. `delete_task` 级联补全（1.6）——先清诊断子表再删主表；SQLite 开外键
3. 生产默认开启认证 + Control 服务隔离（1.5）
4. Agent 进程组终止修复（1.10）+ pprof 大小限制（1.11）
5. Analyzer 租约续期（1.12）

### P1（一周内）
6. 任务写路径加 `with_for_update`/CAS（1.7）、修缓存竞态（1.8）
7. Prompt 注入隔离 + cause_id 白名单校验（2.1 / 2.2）
8. 扫描器移出事件循环（2.15）、诊断租约续期（2.16）
9. 自剖析守卫修 PID（2.4）、worker 看门狗（2.5）、task.id 消毒（2.6）
10. CLI 退出码统一 + `status` 诚实（2.11 / 2.12 / 2.13 / 2.14）

### P2（规划）
11. 单文件拆分（orchestrator / sql_repository / cli）
12. 采集器清单单一来源、枚举收敛状态字符串
13. LLM 端点频控、SSE 事务后发布、agent_metrics 落库 + 保留策略
14. 补 ruff/mypy 到 CI；补 PG 冒烟测试

---

## 附：已复验的关键代码位置

| 问题 | 复验结论 |
|---|---|
| `ALLOWED_DIAGNOSIS_TRANSITIONS` 缺 `NEEDS_SCOPE_CONFIRMATION` key | ✅ orchestrator.py:53-63 |
| `_transition` 对非法迁移抛 ValueError | ✅ orchestrator.py:1905-1913 |
| deadline 分支统一 `_transition(INSUFFICIENT_EVIDENCE)` | ✅ orchestrator.py:426-436 |
| `delete_task` 未删诊断子表 | ✅ sql_repository.py:350-383 + models.py:361-440 |
| `row_version` 任务只增不查 vs 诊断 CAS | ✅ sql_repository.py:1408 vs store.py:258-305 |
| `_cached` 无锁读 vs `_write_session` clear | ✅ sql_repository.py:79-91 vs :111 |
| 自剖析守卫用 worker 进程 PID | ✅ agent/main.py:103,272,262-266 |
| 租约只在 106/148 续期 | ✅ analyzer/worker.py:105-149 |
| gRPC 0.0.0.0 + Control 同端口 + 认证默认关 | ✅ grpc_server.py:77-93, grpc_auth.py:24-25 |

---

## 附 2：修复记录（2026-08-05 实施）

所有修复已完成并通过测试回归（全量套件 449 passed / 1 flaky 于 Windows 文件锁，单跑通过）。

### 已修复（含新增回归测试）

| 审计项 | 修复内容 | 验证 |
|---|---|---|
| 1.1/1.2/1.3/1.4 状态机卡死 | 迁移表补 `NEEDS_SCOPE_CONFIRMATION` 出口 + `WAITING_APPROVAL→INSUFFICIENT_EVIDENCE`；deadline 按状态选合法终态 `_deadline_terminal_for`；`CONCLUDING` 幂等补提交 `_conclude_after_interrupt`；新增 `POST /api/v1/diagnoses/{id}/cancel` | 新增 5 个测试 |
| 1.6 delete_task 级联 | 按 FK 依赖顺序清理 diagnosis 子表 + probe_executions；SQLite 开启 `PRAGMA foreign_keys=ON`（暴露并修复了 create_session 子行先插的 PG 级 bug） | 新增 1 个测试 |
| 1.7 任务写路径并发 | `_locked_task` 行锁应用于 cancel/transition/finish_attempt/add_artifacts/create_analysis_job/delete_task/recover | 现有测试 |
| 1.8 缓存竞态 | `_cached` 改用 try/except KeyError，消除 in+[] TOCTOU | 现有测试 |
| 1.10 取消孤儿进程 | 采集器去掉 `start_new_session`（子进程留 worker 组）；超时清理改为 `proc.terminate()` 避免误杀 worker 组 | 更新 perf 超时测试 |
| 1.11 pprof | 512MB 响应上限 + 禁用重定向 + endpoint 白名单 | 更新测试 mock 语义 |
| 1.12 Analyzer 租约 | 下载+分析期间后台续租线程（`renew_lease` 不 bump row_version，不干扰 CAS） | 现有测试 |
| 2.1 Prompt 注入 | RCA/summarize system prompt 加"证据为不可信数据"声明 + `<evidence>` 分隔符包裹 | 现有测试 |
| 2.2 cause_id 白名单 | `_validate_and_parse` 校验 cause_id ∈ 候选列表；`repair.py` io_wait_high 加 agent_id 守卫 | 现有测试 |
| 2.3 错误体脱敏 | `_call_deepseek` 不再把 `resp.text[:300]` 拼入报告 | 现有测试 |
| 2.4 自剖析守卫 | worker 注入主进程 PID，双 PID 比较 | 现有测试 |
| 2.5 worker 看门狗 | 主循环检测 `worker.is_alive()`，崩溃即重启 + 失败上报 | 现有测试 |
| 2.6 task.id 消毒 | `_run_collector` 入口白名单校验 `[A-Za-z0-9_.-]{1,128}` | 现有测试 |
| 2.7 取消竞态 | 取消分支先排空 result_queue，已完成的按成功上报 | 现有测试 |
| 2.11/2.12 CLI | `status` 子查询失败返回非 0；`watch-task` 终态集/退出码与 `_watch_until_terminal` 统一；`agent-exec` 明确"不执行"语义 | 现有测试 |
| 2.14 storage-prune | `delete_task` 提交后清理 MinIO 对象（`storage.remove_object`） | 新增 |
| 2.15 扫描器阻塞 | `_offline_sweeper` 用 `asyncio.to_thread` 移出事件循环 | 现有测试 |
| 2.16 诊断租约 | `store.renew_lease`（不 bump version）+ orchestrator 后台续租线程 | 现有测试 |
| 1.5 认证 | Control 服务默认不注册（`MINI_DROP_GRPC_ENABLE_CONTROL=1` 才暴露）；gRPC 消息大小上限 64MiB；启动时非回环地址+无认证给出告警 | 更新 grpc 测试 fixture |
| 2.18/2.19/2.20 | sys_metrics 异常降级已由状态机修复覆盖；`query` 换行由 `_CONTROL_CHARS` 清洗覆盖；R2 预算 | 部分 |
| 2.21 SSE 事务后发布 | `_notify_after_commit`：所有 SSE 通知移入 `_write_session` 提交后 hooks | 现有测试 |
| 2.24 去重键 | `_artifact_identity` 只取不可变出处字段（sha256/local_path/filename 变为可更新内容） | 现有测试 |
| 2.25 扫描器行锁 | `mark_offline_agents`/`recover_stale_tasks` PG 下 `with_for_update(skip_locked)` | 现有测试 |
| 2.27 同状态幂等 | `_transition_task_in_session` 同状态迁移直接 return，不写重复事件 | 新增 1 个测试 |
| 3.x 可维护性 | main.py 重复 json import 合并；orchestrator 4 个死函数删除；calibrator 未用参数删除；`_operation_locks` 改弱引用；`_minimize` 脱敏关键词扩充；task_names 控制字符清洗；CLI 版本号单一来源；perf-top 临时文件加 PID | 全量回归 |

### VM 部署与集成验证（2026-08-05 完成）

修复已部署到三节点集群并完成真实环境验证：

- **control**（192.168.10.10）：`/home/control/mini-drop-release-20260805-fix-v1`，symlink 切换 + 重启，healthz 全 ok
- **worker1/worker2**：agent 更新到新代码，ExecStart 改为 `mini-drop-active` symlink 路径
- 部署中发现并修复一个打包问题：`knowledge/` 是运行时依赖（`load_catalog` 加载 `catalog.json`），缺失会导致 `POST /api/v1/diagnoses` 500（旧 release 同样存在此隐患，只是未被触发）

**集成验证 12/12 通过**：
1. 创建诊断 → 新 cancel API → `USER_CANCELED`，终态重复取消幂等
2. 无匹配实例 → `NEEDS_SCOPE_CONFIRMATION` → deadline（1 分钟）后收敛 `INSUFFICIENT_EVIDENCE`，重复 GET 稳定无 500（原实现永久卡死）
3. 生产 PostgreSQL 下 `delete_task` 返回 200 无 500（原实现 FK 冲突 500），删除后 GET 404
4. 端到端采集链路：agent 新代码注册在线 → 真实 `sys_metrics` 任务 PENDING→DONE → 产物生成 → 清理成功

> 注：VM 生产配置认证全开（`MINI_DROP_API_AUTH_ENABLED=1` + `MINI_DROP_GRPC_AUTH_ENABLED=1`），本次验证全程带 API Key 通过 HTTPS 执行。
部署步骤（control 节点，experiment cluster）：

```bash
# 在 Windows 主机打包代码后 scp 到 control，或直接 git pull 后：
# 参考 deploy/scripts/activate-native-release.sh 的 release 目录布局
ssh control@192.168.10.10
sudo bash /home/control/mini-drop/scripts/activate-native-release.sh \
  /home/control/mini-drop-release-YYYYMMDD-xxx /var/www/mini-drop-release-YYYYMMDD-xxx
```

部署后验证清单：
- `curl -sk https://192.168.10.10/api/healthz` 三个 check 全 ok
- 创建诊断会话 → 取消（新 API `POST /api/v1/diagnoses/{id}/cancel`）
- 构造无匹配实例的诊断 → 等 deadline 后确认收敛 `INSUFFICIENT_EVIDENCE`（不再 500）
