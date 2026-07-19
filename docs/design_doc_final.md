# Mini-Drop 设计文档

## 1. 项目概述

Mini-Drop 是一个面向 Linux 主机的**轻量级性能诊断平台**。用户通过 Web UI 创建采集任务，Server 负责任务编排和状态管理，Agent 在目标主机执行 perf / eBPF / py-spy / continuous 采集，Analyzer 将原始数据转为 D3 交互式火焰图和热点分析，5 层智能归因引擎提供可追溯的诊断报告。

**核心理念**：让性能诊断从"专家人肉分析"变成"工具驱动决策"。火焰图告诉你在哪慢了，归因引擎告诉你为什么慢，持续 profiling 告诉你什么时候开始慢的。

项目在 3 周内从空仓库逐步构建到完整可演示平台，74 个 commit 记录真实开发过程。每个 commit message 均以中文手写，独立可 review。

### 1.1 系统规模

| 维度 | 数量 |
|------|------|
| 后端代码 (Python) | ~12,000 行 |
| 前端代码 (JSX) | ~4,000 行 |
| gRPC Proto | 5 个契约文件 |
| 测试代码 | 33 个文件 / 303 个测试函数 / 616 个断言 |
| 数据库表 | 14 张（5 张核心 + 9 张增强） |
| 采集器 | 8 种 |
| CLI 命令 | 20+ 个 |
| Commit | 74 个 |

---

## 2. 总体架构

### 2.1 组件拓扑

```
                    ┌──────────────────────────────────────┐
                    │          用户浏览器 (React SPA)         │
                    │    Ant Design 5 + d3-flame-graph       │
                    │    + ECharts + SSE EventSource         │
                    └──────────┬───────────────────────────┘
                               │ REST / SSE
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                    Server (FastAPI :8191)                    │
│                                                             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────────┐ │
│  │ Web API  │ │  gRPC    │ │   SSE    │ │   Prometheus  │ │
│  │ /api/*   │ │ :50051   │ │ /events  │ │   /metrics    │ │
│  └──────────┘ └───┬──────┘ └──────────┘ └───────────────┘ │
│                   │                                         │
│  ┌────────────────┴──────────────────────────────────────┐ │
│  │  RCA 引擎 │ NLP 解析 │ 状态机 │ Repository  │ │
│  └──────────────────────────────────────────────────────┘ │
└──────────┬──────────────────────┬──────────────────────────┘
           │ gRPC                 │ SQL
           ▼                      ▼
┌──────────────────┐    ┌──────────────────┐
│  Agent (裸机/Linux)│    │   PostgreSQL :5432 │
│                   │    │   (任务/事件/审计)  │
│ ┌───────────────┐ │    └──────────────────┘
│ │ 8 种采集器     │ │
│ │ perf/eBPF/    │ │    ┌──────────────────┐
│ │ py-spy/java/  │ │    │    MinIO :9000    │
│ │ pprof/memory/ │ │    │   (产物/火焰图)   │
│ │ sys/continuous│ │    └──────────────────┘
│ └───────────────┘ │
│ ┌───────────────┐ │
│ │ Analyzer CLI  │ │
│ │ 火焰图+TopN   │ │
│ └───────────────┘ │
└──────────────────┘
           │
           │ 可选
           ▼
┌──────────────────┐
│  DeepSeek API    │
│  (NLP + RCA)     │
└──────────────────┘
```

### 2.2 两种部署拓扑

**模式 A：Docker 全栈（单机）**
```
docker compose up -d
→ 5 容器: Web(:80) + Server(:8191/:50051) + PostgreSQL(:5432) + MinIO(:9000) + Agent(privileged)
→ 适用：Demo 演示 / 单机开发
```

**模式 B：Docker Services + VM Agent（分体部署）**
```
Windows Docker Desktop (Server+Web+PG+MinIO)
         │ gRPC :50051
         ▼
Linux VM (Agent 裸机, root)
→ 适用：生产环境 / eBPF 真实采集 / 一台 Server 管理多台主机 Agent
```

### 2.3 核心端口

| 服务 | 端口 | 说明 |
|------|------|------|
| Web (Nginx) | 80 | React SPA + API 反向代理 + SSE |
| Server HTTP | 8191 | FastAPI + Swagger `/docs` |
| Server gRPC | 50051 | Agent 通信（心跳 + 任务下发 + 结果上报） |
| PostgreSQL | 5432 | 任务/事件/审计/诊断持久化 |
| MinIO API | 9000 | 对象存储（产物上传 + 预签名下载） |
| MinIO Console | 9001 | Web 管理面板 |

---

## 3. 核心架构决策

### 3.1 gRPC + Protobuf 契约优先

Server ↔ Agent 使用 gRPC，参考 DeepFlow `message/` 模式。5 个 `.proto` 文件定义全部通信接口：

| Proto 文件 | 对应服务 | 职责 |
|-----------|----------|------|
| `common.proto` | — | 共享类型（CosConfig / PidStats） |
| `init.proto` | InitAgent | Agent 注册元数据 + 拉取远端配置 |
| `healthcheck.proto` | HealthCheck | Agent 心跳 + Server 下发待执行任务 |
| `hotmethod.proto` | Hotmethod | Agent 上报采集结果（产物元数据） |
| `control.proto` | Control | Server 下发控制指令（预留） |

**选择原因**：
- 强类型契约编译期发现字段不匹配
- 二进制序列化（PidStats 等数值数组比 JSON 小 3-5 倍）
- 内置超时和取消传播
- 与腾讯原版 Drop 系统架构一致

Web ↔ Server 保留 REST/JSON——浏览器原生支持，易于调试和 curl 测试。

### 3.2 D3 交互式火焰图 + ECharts TopN 联动

Analyzer 将 perf.data 转换为折叠栈后，构建 `{name, value, children}` JSON 树供 D3 渲染。同时产出 ECharts TopN 柱状图。

**为什么 D3 而非 ECharts 热力图？**
D3 火焰图天然支持帧级别的缩放、搜索、hover 详情，这是性能分析的核心交互。ECharts 更适合 TopN 柱状图这种标准图表。两者各自发挥所长，通过 React ref 联动（点击柱状图 → `flameRef.search(funcName)` 高亮火焰图对应帧）。

**火焰图交互能力**：
- 点击帧放大到子函数
- 右键返回上层
- 搜索框高亮匹配函数帧（黄色标记）
- Hover 显示函数名 / 样本数 / CPU 占比
- 37 色相按函数名哈希——同一函数在不同火焰图里颜色一致，方便对比

**降级策略**：
- 优先加载 D3 交互式火焰图（`flamegraph.json`）
- D3 渲染失败时 fallback 到 SVG（`flamegraph.svg`）
- JSON 树深度 >50 层截断，防止浏览器卡死

### 3.3 工具驱动的 5 层智能归因

核心原则：**LLM 不能自由输出文本**。每一层都被约束和校验。

```
证据采集 → 候选生成 → 置信度校准 → LLM 推理 → 修复计划 → 反馈闭环
```

| 层 | 职责 | 关键设计 |
|----|------|----------|
| ① 证据采集 | 从产物提取 TopN 热点、栈深度、IO P99、RSS 趋势 | 不送整个火焰图 JSON——Token 太大且引入幻觉 |
| ② 候选生成 | 规则引擎匹配 `rules.json` | `rules.json` **外部化**：运维不开 IDE 即可扩展规则 |
| ③ 置信度校准 | 五维打分：正确性、完整性、可操作性、时效性、一致性 | 低于阈值剪枝 |
| ④ LLM 推理 | Few-Shot + JSON Schema 硬约束 | 输出过 Schema + 引用完整性校验，失败自动重试 2 次 |
| ⑤ 修复计划 | 三级风险（safe_auto / confirm_required / manual_only） | `requires_user_confirm` 标记需人工确认的操作 |
| ⑥ 反馈闭环 | 用户标注回写校准层权重 | 持续优化，非一次性推理 |

**降级**：未配 AI Key 时 → 规则引擎独立输出报告，核心采集/火焰图功能不受影响。

**如果重做**：
- 当前 5 层线性管道（~8s 端到端）。若改为 DAG 并行（证据采集 + 候选生成同时启动，LLM 流式输出），预计降到 ~2s。
- `rules.json` 目前镜像固化。可加 gRPC stream 推送——Server 更新规则后实时推给所有 Agent，本地热加载。

### 3.4 自然语言采集

用户描述意图 → LLM function calling 解析 → `/proc` 进程名→PID 匹配 → 用户确认 → 自动创建任务。

**安全关键设计**：进程解析**不做在 LLM 中**（LLM 不知道系统上运行什么进程），而是由 Server 读取 `/proc` 执行 PID 匹配。参数经过 `CLAMP_DURATION` / `CLAMP_SAMPLE_RATE` 限制，防止恶意输入。

### 3.5 SQLAlchemy + PostgreSQL 持久化

所有数据通过 SQLAlchemy ORM 持久化。开发/演示默认 PostgreSQL（`docker-compose.yml`），本地测试用 SQLite（`docker-compose.local.yml`）。

**Repository 模式**：
- 读操作带 TTL 缓存（`_cached(key, ttl_sec, factory)`），减少高频查询数据库压力
- 写操作使用 `_write_session()` context manager，自动 commit / rollback
- `expire_on_commit=False` 允许 session 关闭后继续读取数据

### 3.6 Analyzer 跑在 Agent 侧而非 Server 侧

Agent 本地执行 `perf script → stackcollapse → flamegraph.pl` 流水线。火焰图 JSON 树通常几 KB ~ 几十 KB，比原始 `perf.data`（数百 KB ~ 数 MB）小得多。**上传 JSON 而非原始数据**，节省带宽和存储。

### 3.7 bpftrace 选型

bpftrace 对演示场景足够——Shell 一行命令即可挂载内核探针。libbpf 更适合生产环境（CO-RE 可移植、无运行时依赖），但工程复杂度显著更高。当前阶段 bpftrace 是合理的 MVP 选择，未来可升级。

**内核兼容性处理**：
- 内核 5.15 上 bpftrace 0.14 不支持 `BEGIN`/`END` 特殊探针符号 → 改用 `interval:s:1` 定时打印
- 内核 5.15 无 `blk_update_request` kprobe → 改用 `blk_account_io_done`
- bpftrace 需要 root 权限 → 文档明确注明，VM Agent 以 root 启动

### 3.8 AI 开关分层降级

```
MINI_DROP_AI_ENABLED=none      → NLP=off, RCA=off, 摘要=off
MINI_DROP_AI_ENABLED=nlp-only  → NLP=on,  RCA=off, 摘要=off
MINI_DROP_AI_ENABLED=rca-only  → NLP=off, RCA=on,  摘要=off
MINI_DROP_AI_ENABLED=full      → NLP=on,  RCA=on,  摘要=on
```

不配 API Key 时**火焰图等核心功能完全不受影响**——AI 自动降级为纯规则引擎。**AI 是增强，不是依赖**。

---

## 4. 任务状态机设计

### 4.1 状态迁移图

```
                    ┌─────────┐
                    │ PENDING │ ← Web 创建任务
                    └────┬────┘
                         │ Agent 心跳拉取
                         ▼
                    ┌─────────┐
                    │ RUNNING │ ← Agent 正在执行采集
                    └────┬────┘
                         │ Agent 上报产物
                         ▼
                  ┌───────────┐
                  │ UPLOADING │ ← Agent 上传产物到 MinIO
                  └─────┬─────┘
                        │ 上传完成
                        ▼
                  ┌───────────┐
                  │ ANALYZING │ ← Analyzer 生成火焰图/TopN
                  └─────┬─────┘
                        │ 分析完成
                        ▼
          ┌─────────────┴─────────────┐
          │                           │
          ▼                           ▼
     ┌────────┐                 ┌────────┐
     │  DONE  │                 │ FAILED │
     │ (终态) │                 │ (终态) │
     └────────┘                 └────────┘
```

比题目要求的 5 状态多一个 `ANALYZING`——采集完成和分析完成是两个独立阶段，职责更清晰。

### 4.2 状态机约束

- **白名单驱动**：`ALLOWED_TRANSITIONS` 字典定义合法迁移——不允许跳过中间状态
- **终态保护**：DONE / FAILED 拒绝再迁移
- **每次迁移必写审计**：`from_status → to_status, reason, actor, metadata, created_at`
- **Actor 溯源**：`web` / `server` / `agent` / `analyzer` / `ai`——每一步迁移的责任方可追溯

### 4.3 审计链示例

```
PENDING  → "Web 创建任务: e2e-test, agent=agent_vm_demo, PID=249700"
RUNNING  → "Agent agent_vm_demo 拉取任务并开始采集"
FAILED   → "artifact upload failed: HTTPConnectionPool ... minio:9000"
```

每条记录带 JSON `metadata` 字段，存储请求参数、Agent 信息等上下文。

---

## 5. 数据库设计

### 5.1 核心表（P0）

| 表 | 职责 | 关键字段 |
|----|------|----------|
| `agents` | Agent 注册信息 | id, hostname, ip_addr, status, capabilities, last_heartbeat_at |
| `tasks` | 任务主表 | id, name, agent_id, target_pid, collector_type, status, duration_sec |
| `task_status_events` | 状态迁移审计 | task_id, from_status, to_status, reason, actor, meta_json |
| `audit_logs` | 系统审计日志 | event_type, message, agent_id, task_id, meta_json |
| `artifacts` | 采集产物元数据 | task_id, artifact_type, filename, bucket, object_key, size_bytes |

### 5.2 增强表（P1）

| 表 | 职责 |
|----|------|
| `diagnosis_runs` | 诊断执行记录（模型、校验状态、摘要） |
| `diagnosis_feedback` | 用户对诊断结论的标注反馈 |
| `rca_feedback_weights` | 归因引擎校准层权重（按 cause_id 维度） |
| `tool_results` | LLM tool-use 证据链记录 |
| `repair_plans` | 修复计划（动作、风险等级、状态） |
| `agent_metric_snapshots` | Agent 心跳指标历史（CPU/RSS/IO 时序） |

### 5.3 实体关系（核心）

```
agents 1 ──── N tasks
tasks  1 ──── N task_status_events
tasks  1 ──── N artifacts
tasks  1 ──── N diagnosis_runs
diagnosis_runs 1 ──── N tool_results
diagnosis_runs 1 ──── N diagnosis_feedback
```

---

## 6. CLI 命令体系设计

### 6.1 设计目标

`micro-drop` CLI 是面向**自动化运维**和 **CI/CD 集成**的命令行工具。所有命令默认 JSON 输出（`stream=True` 时 YAML 兼容），退出码语义明确。

### 6.2 命令分类

**基础运维**
```bash
micro-drop serve                    # 启动 Server
micro-drop agent                    # 启动 Agent
micro-drop version                  # 版本信息
micro-drop ai-config                # AI 配置 + feature flag 状态
micro-drop install-check            # 检查系统依赖和权限
```

**远程采集管理**
```bash
micro-drop collect --agent agent_1 --pid 1234 --collector perf_cpu  # 远程下发采集
micro-drop status                   # Server/Agent/Task 实时概览
micro-drop task-cancel --task-id xxx # 取消进行中任务
micro-drop watch-task --task-id xxx  # 轮询任务直到终态
```

**NLP / AI**
```bash
micro-drop parse "nginx CPU 飙高"           # 自然语言解析
micro-drop summarize --top-json top.json    # TopN AI 总结
micro-drop diagnose-local --evidence evidence.json  # 离线 RCA
micro-drop feedback-stats                   # 反馈准确率统计
```

**差分分析 / CI 门禁**
```bash
micro-drop diff-top --base before.json --head after.json --threshold 5
# exit 0 = 正常, exit 2 = 超阈值（可做 CI 阻断）
micro-drop ci-check --base before.json --head after.json
micro-drop alert --top-json top.json --hotspot-threshold 70
```

**存储管理**
```bash
micro-drop storage-ls                          # 列举 MinIO 产物
micro-drop storage-prune --older-than-days 30  # 清理旧产物（dry-run）
micro-drop report --top-json top.json --format markdown --output report.md
```

**Shell 补全**
```bash
micro-drop completion --shell bash
# eval "$(micro-drop completion --shell bash)"
```

### 6.3 退出码语义

| 码 | 含义 | 示例 |
|----|------|------|
| 0 | 成功 | 正常完成操作 |
| 1 | 一般错误 | 参数错误、网络超时 |
| 2 | 阈值/告警 | `diff-top` 超阈值、`alert` 命中 |
| 3 | 认证错误 | API Key 无效 |

---

## 7. 事件总线架构

```
EventBus (内存)
  ├ task_changed    ← Server 状态迁移时发布
  ├ agent_status    ← _offline_sweeper 检测到变更时发布
  └ diagnosis_complete ← RCA 完成后发布
       │
       └── SSE Stream → Web 前端 (实时 toast 通知)
```

**设计要点**：
- EventBus 基于 `queue.Queue` 的单发布-多订阅模式
- SSE 连接断开时 Dashboard 自动切换到 10s 轮询兜底

---

## 8. Web 前端设计

### 8.1 技术选型

| 技术 | 用途 |
|------|------|
| React 18 | SPA 框架 |
| Ant Design 5 | UI 组件库 |
| d3-flame-graph | 交互式火焰图 |
| ECharts (echarts-for-react) | TopN 柱状图 / eBPF Histogram / 内存时序 / 系统指标 |
| React Router 6 | 路由 + 懒加载 |
| Vite 5 | 构建工具 |
| SSE (EventSource) | 实时事件推送 |

### 8.2 页面路由

| 路由 | 页面 | 核心功能 |
|------|------|----------|
| `/` | Dashboard 任务面板 | 统计卡片、NLP 输入、任务搜索/排序/删除、Agent 列表、SSE 通知 |
| `/task/:id` | TaskResult 任务详情 | D3 火焰图+TopN 联动、eBPF Histogram、状态时间线、AI 归因+反馈 |
| `/diagnoses` | DiagnosisHistory 诊断历史 | 置信度筛选、搜索过滤、反馈标注统计 |
| `/agent/:id` | AgentDetail Agent详情 | 资源趋势图、采集能力标签、关联任务搜索 |
| `/audit` | AuditLogs 审计日志 | 事件类型筛选、自由搜索、时间倒序 |
| `/settings` | Settings 设置 | AI 连通性测试、API Key 管理、服务健康 |

### 8.3 核心交互设计

**火焰图 + TopN 联动**：
```
TopNChart.onBarClick(funcName)
  → flameRef.current.search(funcName)
    → FlamegraphViewer D3 chart.search(text)
      → D3 高亮匹配栈帧（黄色标记）
```

**实时通知双通道**：
```
SSE EventSource → connected=true → toast 通知 + 数据刷新
                → connected=false → 10s 轮询兜底 (+ usePolling)
```

**ErrorBoundary 全局边界**：
- 组件渲染异常时显示友好错误页（重试/回首页），不白屏
- DEV 模式展示完整错误栈，生产模式仅提示
- `key={location.pathname}` 路由切换时自动重置

**暗色模式持久化**：
- localStorage 存 `mini-drop-theme`，刷新保持
- Ant Design 主题 token 即时切换

### 8.4 业务逻辑增强（近期新增）

| 功能 | 位置 | 说明 |
|------|------|------|
| 任务搜索 | Dashboard | 按名称/ID 模糊匹配，后端 `?search=` |
| 任务排序 | Dashboard | 按创建时间/名称/状态/Agent/采集器/PID，`?sort_by=&sort_order=` |
| 任务删除 | Dashboard | 确认弹窗，仅终态可删，级联删除事件+产物+诊断+审计 |
| 审计搜索 | AuditLogs | 自由文本 + 事件类型下拉筛选 |
| AI 测试 | Settings | 一键探测 AI Provider 响应 |
| eBPF Histogram | TaskResult | IO 延迟绿→红渐变柱状图 + P50/P95/P99 |
| Agent 优选 | NLPTaskInput | 自动选择在线 + 匹配采集器能力的 Agent |

---

## 9. eBPF 采集器设计

### 9.1 采集链路

```
Server → gRPC HealthCheck.Do → Agent 拉取 ebpf_io 任务
  → EBPFCollector.collect(task)
    → subprocess: bpftrace -o io_latency.txt io_latency.bt
      → kprobe:blk_mq_start_request → @start[arg0] = nsecs
      → kprobe:blk_account_io_done /@start[arg0]/ → @latency_us = hist(...)
      → interval:s:1 → print(@latency_us)
    → SIGTERM → bpftrace 退出
  → _parse_histogram() regex 解析 → ebpf_metrics.json
  → 上传 MinIO
  → Web EBPFHistogram: ECharts 柱状图 + P50/P95/P99
```

### 9.2 内核兼容性

| 内核版本 | 问题 | 解决方案 |
|----------|------|----------|
| 5.15 | bpftrace 0.14 不支持 `BEGIN`/`END` 特殊探针 | 改用 `interval:s:1` 定时打印 histogram |
| 5.15 | kprobe 列表无 `blk_update_request` | 改用 `blk_account_io_done` |
| 全部 | bpftrace 需要 root | 文档注明 + VM Agent root 启动 |

### 9.3 解析器设计

```python
def _parse_histogram(path: str) -> dict[str, int]:
    # regex: \[(\d+[KkMm]?)\s*,\s*(\d+[KkMm]?)\)\s+(\d+)
    # interval:s 模式多次打印 → 相同 key 后者覆盖前者（取最新完整值）
    # bucket value 支持 K/M 后缀 → _normalize_bucket_value() 转换
```

### 9.4 Web 可视化

EBPFHistogram 组件：
- ECharts 柱状图，颜色绿→红渐变（低延迟绿色、高延迟红色）
- 工具提示展示请求数 + 占比
- 累计分布计算 P50/P95/P99
- x 轴 45° 旋转标签，数值自动 k 单位格式化

---

## 10. 持续 Profiling 设计

### 10.1 与单次采集的差异

| 维度 | 单次采集 (`perf_cpu`) | 持续 profiling (`continuous_perf`) |
|------|----------------------|-----------------------------------|
| 采样率 | 99Hz（高精度） | 11Hz（低频低开销） |
| 窗口 | 1 个 | N 个（间隔 period_sec） |
| 适用 | 按需诊断 | 长期监控、间歇性抖动排查 |
| 产物 | 1 份火焰图+TopN | N 份火焰图+TopN + 汇聚 summary |
| Web 展示 | 单帧火焰图 | 窗口选择器 + 时间轴回放 |

### 10.2 设计思路

参考 DeepFlow Agent 的持续 profiling 模式。Agent 内置后台线程，按固定间隔周期低频采样。参数选择：
- **11Hz 采样率**：perf 自身开销 <3% CPU，生产环境可长期挂着
- **每窗口 10s**：足够生成有分辨率的火焰图
- **60s 间隔**：避免连续采样导致 perf.data 积压

### 10.3 Web 时间轴回放

- 底部"连续采样窗口"选择器列出所有窗口
- 切换窗口 → 火焰图和 TopN 即时更新
- 窗口表格展示开始/结束时间、OK/FAILED 状态

---

## 11. 测试策略

### 11.1 测试分层

| 层级 | 文件数 | 测试函数 | 覆盖范围 |
|------|--------|----------|----------|
| 单元测试 | 22 | ~250 | 状态机/采集器/配置/NLP/RCA/存储/Repository |
| 集成测试 | 5 | ~45 | gRPC 服务/Server API/Agent 连接/EventBus |
| E2E 测试 | 1 | 8 | 全链路：创建任务→DONEFAILED→离线检测 |

### 11.2 关键测试场景

**状态机测试** (`test_state_machine.py`)：
- 全部合法迁移路径
- 终态拒绝迁移
- 非法迁移抛出异常
- 白名单完整性

**gRPC 集成测试** (`test_grpc_services.py`)：
- Agent 注册→心跳→任务拉取→结果上报全链路
- 动态端口分配避免 CI 冲突
- 健康检查响应格式

**E2E 测试** (`test_e2e.py`)：
1. 正常路径：创建 CPU 任务 → 轮询 → DONE → 验证产物
2. PID 不存在：任务 FAILED + reason 明确
3. Agent 离线检测：30s 无心跳 → 标记 OFFLINE + 审计日志

**eBPF 测试** (`test_ebpf_collector.py`)：
- bpftrace 不可用时优雅降级
- 脚本文件缺失时明确报错
- histogram 解析器正则验证（含 K/M 后缀）

**NLP 测试** (`test_nlp.py`)：
- function calling 参数 clamp 边界
- `{0, [1, 10000]}` 范围约束
- 关键词 fallback 降级（无 AI Key 时）

### 11.3 测试运行

```bash
make test       # pytest -v
make coverage   # pytest --cov=server --cov=agent --cov=analyzer

# E2E 套件（需要 Docker 环境）
docker compose up -d
sudo python3 demo/test_runner.py          # 16 场景
sudo python3 demo/test_runner.py --quick  # 快速模式
```

---

## 12. 性能自证

Agent 采集期间自身开销（数据来源：Agent 心跳 PidStats）：

| 指标 | 采集期间 | 空闲期间 |
|------|----------|----------|
| CPU | < 5%（单核） | < 0.5% |
| RSS | < 100 MB | ~35 MB |
| IO 读 | < 50 KB/s | ~0 KB/s |
| IO 写 | < 10 KB/s | ~0 KB/s |

**前端性能**：
- Vite 构建产物：首屏 index.js ~884 KB（gzip ~279 KB）
- 路由懒加载：TaskResult / AuditLogs / AgentDetail 按需加载
- React + Ant Design 是当前最大 vendor 体积来源，后续可做 Ant Design 组件级按需优化

---

## 13. 安全加固

| 层次 | 措施 |
|------|------|
| **HTTP API** | Bearer / X-API-Key 双通道认证，常量时间比较防时序侧信道 |
| **gRPC** | Token 拦截器 + 可选 TLS |
| **产物读取** | 路径沙箱限制在 `MINI_DROP_ARTIFACT_ROOT` 内，`resolve()` 逃逸检测 |
| **预签名 URL** | 有效期可配，限制 `tasks/` 前缀 |
| **Agent 保护** | 拒绝自剖析（`target_pid == self PID` 时拒绝）；参数 clamp 防资源耗尽 |
| **Agent 上报清洗** | 产物元数据通过白名单过滤，最多保留 32 个产物，超长字段截断，嵌套 metadata 不落库 |
| **密钥管理** | `.env` 已 gitignore，`.env.example` 仅模板占位符 |
| **Nginx** | CSP / HSTS / X-Frame-Options / 速率限制 (30 req/s, burst 20) |
| **LLM 输出** | JSON Schema 硬约束 + 引用完整性校验 + 自修复重试 |

**生产开启认证**：
```bash
MINI_DROP_API_KEY=$(openssl rand -hex 32)
MINI_DROP_GRPC_TOKEN=$(openssl rand -hex 32)
MINI_DROP_API_AUTH_ENABLED=1
MINI_DROP_GRPC_AUTH_ENABLED=1
```

**当前局限**：
- API Key 是全局共享密钥，后续可升级为多用户 token 体系
- gRPC 当前为 insecure channel，生产需开启 TLS
- 多租户、任务权限隔离尚未实现——当前定位为单租户 MVP

---

## 14. 工程化实践

### 14.1 结构化日志

Server 和 Agent 均输出 JSON Lines 格式日志，便于 Docker 日志收集和后续接入 Loki/ELK：

```json
{"ts": "2026-06-21T03:02:54Z", "level": "info", "event": "agent_registered", "agent_id": "agent_vm_demo", "ip_addr": "172.17.145.228"}
{"ts": "2026-06-21T03:02:57Z", "level": "info", "event": "http_request", "method": "GET", "path": "/api/healthz", "status_code": 200, "latency_ms": 6.22}
```

### 14.2 构建系统

- `Makefile`：标准开发命令入口（`make proto/server/agent/test/lint/fmt/demo`）
- `dev.py`：跨平台开发脚本（Windows 兼容）
- `docker-compose.yml` 三层覆盖：默认（full）→ 离线（local）→ 预构建 Web（prebuilt-web）
- `pyproject.toml`：pip 可编辑安装 + dev extras

### 14.3 常用验收命令

```bash
make test                          # 单元 + 集成测试
make coverage                      # 覆盖率报告
npm --prefix web run build         # 前端构建
docker compose up -d               # Docker 全栈
make demo                          # 6 场景一键演示
```

---

## 15. 如果再有 7 天我会做什么

1. **DAG 并行归因管道**：证据采集 + 候选生成并行启动，LLM 流式输出，端到端 8s → 2s
2. **gRPC stream 热加载规则**：Server 更新 `rules.json` 后实时推 Agent，不停机不重启
3. **HTTP 控制面升级 gRPC streaming**：任务状态推送替代轮询，降低 Server 负载
4. **Analyzer 队列化**：单实例 → 多 worker 并行处理，支撑高频采集
5. **eBPF 探针扩展**：调度延迟、网络延迟、文件系统探针（bcc/libbpf 替代 bpftrace）
6. **完整用户权限模型**：多用户 + RBAC + 任务隔离
7. **归因评测集建设**：量化结论准确性和建议可操作性，防回归
