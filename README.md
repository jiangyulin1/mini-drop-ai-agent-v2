<p align="center">
  <h1 align="center">🔥 Mini-Drop</h1>
  <p align="center"><strong>轻量级 Linux 性能诊断平台</strong> — 火焰图 · eBPF · Evidence 调查 · 自然语言采集</p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.9+-blue" alt="Python">
  <img src="https://img.shields.io/badge/react-18.x-61dafb" alt="React">
  <img src="https://img.shields.io/badge/gRPC-1.80-2ca5aa" alt="gRPC">
  <img src="https://img.shields.io/badge/license-MIT-yellow" alt="License">
</p>

---

## 目录

- [快速开始](#快速开始)
- [项目概览](#项目概览)
- [环境要求](#环境要求)
- [整体架构](#整体架构)
- [核心流程](#核心流程)
- [采集器](#采集器)
- [任务级规则归因](#任务级规则归因兼容路径)
- [任务状态机](#任务状态机)
- [Web 前端](#web-前端)
- [自然语言采集](#自然语言采集)
- [CLI 命令体系](#cli-命令体系)
- [API 速览](#api-速览)
- [部署与运维](#部署与运维)
- [安全设计](#安全设计)
- [AI Provider](#ai-provider)
- [开发命令](#开发命令)
- [仓库结构](#仓库结构)
- [设计原则](#设计原则)
- [关键决策与取舍](#关键决策与取舍)
- [文档入口](docs/README.md)

---

## 快速开始

```bash
# 1. 克隆 + 配置
git clone https://github.com/jiangyulin1/mini-drop.git && cd mini-drop
cp .env.example .env

# 2. 启动全栈服务（PostgreSQL + MinIO + Server + Agent + Web）
docker compose up -d

# 3. 端到端演示：启动热点进程 → 创建采集任务 → 轮询完成 → 验证火焰图
bash demo/demo.sh

# 4. 浏览器打开 http://localhost 查看火焰图与诊断
```

> **纯净 Ubuntu 22.04 首次运行**：需要安装 `make` 和 Docker，见下方[环境要求](#环境要求)和[部署与运维](#部署与运维)章节。

**本地运行（无 Docker）：**

```bash
pip install -e ".[dev]"
python dev.py proto       # 编译 gRPC stub
python dev.py server      # 终端 1：FastAPI :8191 + gRPC :50051
python dev.py agent       # 终端 2：Agent 注册并心跳
python dev.py analyzer-worker  # 终端 3：独立分析 Worker
python dev.py test        # 运行测试
```

---

## 项目概览

- **核心能力**：
  - Web UI 指定目标 PID、采样率、时长，通过 Server 下发任务给 Agent。
  - Agent 在目标主机上执行 perf、eBPF、运行时快照、日志、内存和持续采样，产物上传 MinIO。
  - Analyzer 将 perf.data 转为 D3 交互式火焰图 + ECharts TopN 热点排行。
  - 受控 Case/Evidence 调查链路；Pi 模式下模型只能调用已注册工具并提交有证据引用的结论。
  - 自然语言采集——用户输入"mysqld CPU 飙高"，系统自动匹配进程、选采集器、定参数。
- **运行形态**：React SPA 前端 + FastAPI/gRPC 控制面 + gRPC Agent 采集端 + 独立 Analyzer Worker + PostgreSQL 持久化 + MinIO 对象存储。

## 关键设计亮点

- **分体部署架构**：Web/Server/DB/MinIO 跑在 Docker 里，Agent 裸机运行且需要 `privileged` + `pid:host`。权限隔离明确，Agent 可独立升级重启，不影响 Web 服务。
- **gRPC 契约优先**：5 个 `.proto` 文件定义全部通信接口，强类型编译期发现字段不匹配，二进制序列化比 JSON 小 3-5 倍。
- **采集器即插件**：所有采集器实现 `Collector(Protocol)` 协议——新增采集器只需实现 `collect(task) → CollectorResult`，Server 不绑定具体工具。
- **Evidence 驱动的 AI 调查**：Pi 模式读取 canonical Evidence、识别缺口并提出受控采集；服务端负责 scope、权限、审批、引用和副作用校验。旧 `rules.json` 候选排序仅作为兼容基线，不属于生产 AI Runtime。
- **持续 Evidence Case**：保存范围、信息目标、采集提案、原生 Task、Evidence、人工 Review 和受引用分析；当前能力与门禁见 [`docs/ai_collector_architecture_and_migration_plan.md`](docs/ai_collector_architecture_and_migration_plan.md)。
- **自然语言采集**：用户描述意图 → LLM function calling 解析 → `/proc` PID 匹配 → 参数 clamp 安全范围 → 自动创建任务。
- **可恢复执行流水线**：Task 保留兼容聚合状态，同时持久化 `collection_status` 与 `analysis_status`；每次下发创建唯一 `TaskAttempt`，采集成功后投递带租约的 `AnalysisJob`，结果重放不会重复写入。
- **真实运行中取消**：Server 将取消指令通过心跳下发，Agent 终止采集进程组、持久化取消结果并安全重放；排队超时、Agent 失联和 Analyzer 租约过期均有确定性恢复路径。
- **输入完整性门禁**：Analyzer 执行前校验制品可用性、登记大小与 SHA-256，并限制最大输入；只有仍持有租约的 Worker 才能提交结果。
- **AI 开关分层降级**：不配 API Key 时采集、火焰图和 Evidence 工作台继续可用；确定性规则输出必须标为兼容基线，不能作为 AI 结果上报。
- **eBPF 零侵入观测**：bpftrace 内核探针实时采集块设备 IO 延迟分布，不改代码、不重启服务。Web 端 ECharts histogram 绿→红渐变着色 + P50/P95/P99 分位估算。
- **交互式火焰图 + TopN 联动**：D3 火焰图支持缩放、搜索、hover 详情；点击 TopN 柱状图的函数名，火焰图自动高亮对应栈帧。

---

## 环境要求

| 项 | 要求 |
|------|------|
| **操作系统** | Ubuntu 22.04 / 20.04（其他 Linux 发行版需自行适配） |
| **Linux 内核** | 5.4+（eBPF 需要内核支持 BPF 特性） |
| **Docker** | Engine 20.10+ + Compose v2 |
| **make** | `sudo apt-get install -y make`（纯净 Ubuntu 需额外安装） |
| **内存** | 8 GB 以上（PostgreSQL + MinIO + Server + Web 合计约 2 GB） |
| **磁盘** | 20 GB 可用空间（Demo 产物约 500 MB） |
| **Python**（仅本地模式） | 3.9+ |
| **perf** | `linux-tools-$(uname -r)` — 用于 CPU 火焰图采集 |
| **bpftrace** | 0.14+ — 用于 eBPF IO 延迟采集 |
| **py-spy** | 0.3+ — 用于 Python 用户态采样 |

**纯净 Ubuntu 22.04 首次准备（以下命令全部复制执行即可）：**

```bash
# 安装 Docker（如未安装）
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# 登出后重新登录使组生效

# 安装 make
sudo apt-get update && sudo apt-get install -y make

# 安装 perf 和 bpftrace（可选，用于本地模式和 eBPF 演示）
sudo apt-get install -y linux-tools-$(uname -r) bpftrace
pip install py-spy

# 设置 perf 权限（容器内也需要宿主机允许）
sudo sh -c 'echo kernel.perf_event_paranoid=1 > /etc/sysctl.d/99-mini-drop.conf'
sudo sysctl -p /etc/sysctl.d/99-mini-drop.conf

# 克隆项目
git clone https://github.com/jiangyulin1/mini-drop.git && cd mini-drop
cp .env.example .env

# Docker 全栈启动
docker compose up -d

# 一键演示
bash demo/demo.sh
```

**Agent 容器权限：** perf 和 bpftrace 需要访问宿主机内核。Docker Compose 已配置 `privileged: true` + `pid: host` + `SYS_ADMIN` + `BPF` + `PERFMON`。

**bpftrace 兼容性说明：**
- 内核 5.15 上 bpftrace 0.14 不支持 `BEGIN` / `END` 特殊探针，Agent 采集脚本已改用 `interval:s:1` 定时打印替代，采集器端 SIGTERM 终止。
- 内核 5.15 使用 `blk_account_io_done` 替代 `blk_update_request`（后者在 5.15 的 kprobe 列表中不存在）。

---

## 整体架构

```mermaid
flowchart LR
    User["用户浏览器"] --> Web["React SPA\nAnt Design + ECharts"]
    Web -->|REST + SSE| Server["Server\nFastAPI :8191"]
    Server -->|任务下发 / 心跳| GRPC["gRPC :50051"]
    GRPC --> Agent["Agent\nprivileged + pid:host"]
    Agent --> Collectors["采集器\nperf / eBPF / runtime / logs\npy-spy / java / pprof / memory\nsys_metrics / process / continuous"]
    Agent --> Analyzer["Analyzer CLI\n火焰图 + TopN + 建议"]
    Agent -->|上传产物| MinIO["MinIO\n对象存储 + 预签名 URL"]
    Server -->|持久化| Postgres["PostgreSQL"]
    Server -->|读取产物| MinIO
    Server -->|可选| AI["OpenAI-compatible\nDeepSeek / OpenAI 等"]
    Web -->|实时事件| SSE["SSE Stream\n任务 / Agent / 诊断"]
```

**核心端口：**

| 服务 | 端口 | 说明 |
|------|------|------|
| Web (nginx) | 80 | React SPA + API 反向代理 + SSE |
| Server HTTP | 8191 | FastAPI REST + Swagger `/docs` |
| Server gRPC | 50051 | Agent 通信 |
| PostgreSQL | 5432 | 任务/事件/审计/诊断 |
| MinIO API | 9000 | 对象存储 |
| MinIO Console | 9001 | 管理面板 |

端口冲突时可通过 `MINI_DROP_GRPC_PORT` 覆盖 Server gRPC 监听端口；Agent
侧的连接地址需同步指向该端口。

### 架构决策

**为什么 gRPC？** Server ↔ Agent 使用 gRPC，5 个 `.proto` 文件定义全部通信接口，参考 DeepFlow `message/` 模式。强类型契约编译期发现字段不匹配，二进制序列化比 JSON 小 3-5 倍。Web ↔ Server 保留 REST/JSON——浏览器原生支持，易于 debug 和 curl 测试。

**为什么分体部署？** Agent 需要 `privileged` + `pid:host` + `BPF` 等内核级权限，与 Web/Server 混在一个 Docker 里权限模型很脏。分开后，Agent 可以独立升级、独立重启，不影响 Web 服务。生产环境中一台 Server 管理多台主机的 Agent 是标准拓扑。

**采集器统一接口。** 所有采集器实现 `Collector(Protocol)` 协议，Server 不绑定具体工具。新增采集器只需实现 `collect(task) → CollectorResult`。

**Analyzer 火焰图管线。** Agent 只负责采集并登记原始制品；独立 Analyzer Worker 通过数据库租约领取 `AnalysisJob`，校验制品大小与 hash 后执行 `perf script → stackcollapse-perf.pl → flamegraph.pl`，产出 JSON、TopN 与 SVG。Worker 崩溃可重领，失去租约的旧 Worker 不能提交。

**MINIO_PUBLIC_ENDPOINT 设计。** Docker 内部 MinIO 使用 `minio:9000`。Agent 通过 gRPC `FetchConfig` 获取 MinIO 地址时，Server 优先下发 `MINIO_PUBLIC_ENDPOINT`（外部可达地址），确保分体部署时 VM Agent 能直传产物到 Windows MinIO。浏览器预签名 URL 同理使用外部地址。

**SQLAlchemy + PostgreSQL 持久化。** 开发默认通过 `docker-compose.yml` 使用 PostgreSQL，`docker-compose.local.yml` 则切 SQLite 零配置。`expire_on_commit=False` 允许 session 关闭后继续读取数据。

---

## 核心流程

### 1) 端到端采集全链路

用户创建采集任务 → Server 写入 PostgreSQL 并置 `PENDING` → Agent 心跳领取唯一 `TaskAttempt` → Agent 执行 perf/eBPF 采集并上传到 attempt 专属对象前缀 → Agent 将结果写入本地 spool 后调用 `NotifyResult` → Server 幂等登记制品并置 `collection_status=COLLECTED` → Server 创建唯一 `AnalysisJob` → 独立 Analyzer Worker 领取租约、校验输入、生成 `flamegraph.json` / `top.json` / `flamegraph.svg` → Worker 原子提交结果 → Server 置 `analysis_status=SUCCEEDED` 与聚合状态 `DONE`。

全程每一步迁移写入 `task_status_events` 表（`from_status → to_status, reason, actor`）。

### 2) eBPF IO 延迟采集链路

Agent 启动 `bpftrace io_latency.bt -o io_latency.txt` → 脚本挂载 `kprobe:blk_mq_start_request` 记录提交时间戳 → 挂载 `kprobe:blk_account_io_done` 计算 `(nsecs - start) / 1000` μs 延迟 → `interval:s:1` 定时打印 histogram → Agent SIGTERM 终止 bpftrace → 解析 regex 提取区间计数 → 输出 `ebpf_metrics.json`（`{io_latency_us: {"[32,64)": 9, ...}}`）→ Web 端 EBPFHistogram 组件渲染 ECharts 柱状图 + P50/P95/P99 分位。

### 3) 兼容规则归因链路（不计作 AI）

触发兼容诊断 → 从产物提取结构化数据 → `rules.json` 生成候选 → 固定权重校准和排序 → 可选 LLM 在既有候选内整理输出 → 引用校验。候选、排名和默认结论均由规则链决定，因此该链只用于历史读取和离线控制组，不用于证明 AI 准确率。

### 4) 自然语言采集链路

用户输入 "mysqld CPU 飙高，帮我看看" → `POST /api/nlp/parse` → LLM function calling 解析意图（进程名 + 采集器类型 + 时长 + 采样率）→ 参数 clamp 到安全范围 → 前端展示确认界面 → 用户选择候选 PID → `POST /api/tasks` 创建任务 → 完成后 `POST /api/nlp/summarize` 生成自然语言总结 + 追问建议。

---

## 采集器

| 采集器 | 类型 key | 采集工具 | 产出物 | Web 可视化 |
|--------|----------|----------|--------|------------|
| **perf CPU** | `perf_cpu` | perf record | flamegraph.json + SVG + top.json | D3 交互式火焰图 + ECharts TopN 联动 |
| **eBPF IO** | `ebpf_io` | bpftrace | IO 延迟 histogram JSON | ECharts 柱状图 + P50/P95/P99 |
| **py-spy** | `pyspy` | py-spy | 火焰图 SVG（--native 混合栈） | iframe SVG 渲染 |
| **Java** | `java_async` | async-profiler | HTML 火焰图 + JFR | iframe HTML 渲染 |
| **Go pprof** | `go_pprof` | pprof | pprof 原始数据 + SVG | SVG / Alert 提示 |
| **Memory** | `memory_smaps` | /proc/PID/smaps | 内存分段 + RSS 趋势 | ECharts 内存时序折线图 |
| **SysMetrics** | `sys_metrics` | /proc 多维 | CPU/线程/FD/网络/IO 时序 | ECharts 多维仪表盘 |
| **Continuous** | `continuous_perf` | perf record（周期） | 多窗口火焰图 + 汇总 | 窗口选择器 + 时间轴回放 |
| **Log Scan** | `log_scan` | 受控日志尾部扫描 | 错误簇、连接与超时模式 | 日志摘要与原文预览 |
| **Process Scan** | `process_scan` | /proc 进程发现 | PID、命令、运行时和资源候选 | 多 Worker 进程选择器 |
| **Runtime Snapshot** | `runtime_snapshot` | procfs / 运行时接口 | 线程状态、锁等待、前进状态 | 运行时诊断摘要 |

所有采集器实现统一接口：

```python
class Collector(Protocol):
    def collect(self, task: CollectorTask) -> CollectorResult: ...
```

---

## 任务级规则归因（兼容路径）

```
┌──────────┐    ┌───────────┐    ┌──────────┐    ┌────────┐    ┌──────────┐
│ ① 证据   │ → │ ② 候选    │ → │ ③ 置信度 │ → │ ④ LLM  │ → │ ⑤ 修复   │
│ 采集     │    │ 生成      │    │ 校准     │    │ 推理   │    │ 计划     │
└──────────┘    └───────────┘    └──────────┘    └────────┘    └──────────┘
     ↑                                                            │
     └─────────────── ⑥ 反馈闭环 (用户标注修正权重) ─────────────┘
```

**逐层说明：**

| 层 | 职责 | 关键设计 |
|----|------|----------|
| **① 证据采集** | 从产物提取结构化证据——TopN 热点、栈深度、IO P99、RSS 趋势 | 不送整个火焰图 JSON 给 LLM，Token 太大且引入幻觉 |
| **② 候选生成** | 规则引擎匹配 `rules.json` 生成候选原因 | `rules.json` 外部化——运维团队不开 IDE 即可扩展诊断规则 |
| **③ 置信度校准** | 五维打分——正确性、完整性、可操作性、时效性、一致性 | 低于阈值剪枝，避免 AI 被低质量候选污染 |
| **④ LLM 推理** | 高置信度候选 + 原始证据发给 LLM，Few-Shot + JSON Schema 硬约束 | 核心原则：**不让 LLM 输出自由文本**。输出过 Schema 校验 + 引用完整性校验，失败自动重试 2 次 |
| **⑤ 修复计划** | claims 转分级修复建议——紧急/高/中三级，每条带预估工作量 | `requires_user_confirm` 标记需人工介入的风险操作 |
| **⑥ 反馈闭环** | 用户标注"准确/不准确"回写校准层权重矩阵 | 持续优化，不是一次性推理 |

**约束：** 每条 claim 必须带 `evidence_refs`；未配置 AI Key 时 → 规则引擎独立输出降级报告，火焰图等核心功能不受影响。

该路径保留用于单个已完成 Task 的历史结果读取和离线规则基线。跨服务事故统一进入 Case/Evidence 调查链路，不再在这条兼容路径上扩展 AI 或自动处置能力。

### Case/Evidence 调查控制层

`/api/v1/cases` 是当前主线；`/api/v1/diagnoses` 仅保留旧规则结果的兼容读取。Pi Runtime 读取 canonical Evidence 和 Collector Catalog，由模型选择信息目标并提出 `CollectorProposal`；确定性 Supervisor 校验 schema、scope、capability、风险、审批、幂等与预算后，才创建唯一 `CollectionRequest -> Task`。

每个 Case 默认最多创建 8 个实际采集请求、累计预留 240 秒，RuntimePolicy 只能收紧该上限。重复提案复用原 Request/Task 且不重复消耗预算；多 Worker 部署时，预算会在数据库事务中锁定 Case 后再次校验。模型分析必须引用 Evidence 的 projection hash 与具体字段/文本跨度，人工将 Evidence 标记为 `LOW_TRUST`、`EXCLUDED` 或恢复后，会使相关 AnalysisRun 失效或改变可引用状态。

未配置 Pi 时系统返回 `AI_RUNTIME_NOT_CONFIGURED`，仍可人工采集、预览、下载和治理 Evidence，但不会回退到规则根因排名。自动恢复和旧因果归因不属于当前产品主线。

AI 的目标是在受控范围和预算内选择高信息增益 Collector，形成 canonical Evidence，并输出带字段引用的事实、冲突、限制和下一信息目标。历史 90 轮评测的 48.9% 是旧规则归因链的运行级控制组结果，不是当前 AI 正确率；在 `collector_agent_v1` 达到独立 holdout 数量和安全门禁前，项目不声明正式 AI 准确率。当前架构基线与分阶段门禁见 [`docs/ai_collector_architecture_and_migration_plan.md`](docs/ai_collector_architecture_and_migration_plan.md)。新的本地评测可先验证公私隔离、Catalog/Evidence hash 和评分合同：

```bash
uv run python scripts/run_collector_agent_eval.py --validate-only
```

---

## 任务状态机

```
PENDING → RUNNING → UPLOADING → ANALYZING → DONE
   │         │          │            │
   └─────────┴──────────┴────────────┘→ FAILED
```

- 每次迁移必须提供非空 `reason`，写入 `task_status_events` 表（`from_status → to_status, reason, actor`）
- DONE / FAILED 是终态，拒绝再迁移
- 合法迁移路径由 `ALLOWED_TRANSITIONS` 白名单控制——不允许跳过中间状态
- 每个 Actor（web / server / agent / analyzer / ai）的迁移可审计
- Web 端 SSE 实时推送状态变更 + toast 通知

---

## Web 前端

| 页面 | 路由 | 功能 |
|------|------|------|
| 任务面板 | `/` | 统计卡片、NLP 输入、任务搜索/排序/删除、Agent 列表、SSE 实时通知 |
| 任务详情 | `/task/:id` | D3 交互式火焰图 + ECharts TopN 联动、eBPF IO Histogram、状态时间线、兼容规则归因 |
| AI 集群诊断 | `/ai-diagnosis` | 自然语言诊断、拓扑目标、假设、受控探针审批、证据血缘与等级置信报告 |
| AI Case 工作台 | `/ai-cases` | 创建 Case、五块恢复摘要、消息/修正、Pause/Resume/Stop、候选/迭代、时间线与模型审计 |
| 诊断历史 | `/diagnoses` | 全量诊断记录、置信度筛选、搜索过滤 |
| Agent 详情 | `/agent/:id` | 资源趋势折线图、采集能力标签、关联任务搜索 |
| 审计日志 | `/audit` | 事件筛选、自由搜索、时间倒序 |
| 系统设置 | `/settings` | AI 连通性测试、API Key 管理、服务健康 |

**技术栈：** React 18 + Ant Design 5 + d3-flame-graph + ECharts + Vite 5 + SSE + React Router 6

**交互设计：**
- **火焰图 + TopN 联动**：点击 ECharts 柱状图的函数名 → 通过 React ref 调用 `flameRef.current.search(funcName)` → D3 火焰图高亮匹配帧
- **暗色模式持久化**：localStorage 存 `mini-drop-theme`，切换即时生效
- **ErrorBoundary 全局捕获**：渲染异常降级为友好错误页（重试/回首页），不白屏
- **自动轮询 + SSE 双通道**：任务执行中 5s 轮询 + SSE 实时事件，确保数据不丢

---

## 自然语言采集

**设计思路：** 用户描述意图 → LLM function calling 解析 → `/proc` 进程名 PID 匹配（**不在 LLM 中做 PID 解析**——这是安全关键点）→ 参数 clamp 安全范围 → 前端确认 → 自动创建任务。

```
用户输入 "mysqld CPU 飙高，帮我看看"
  → POST /api/nlp/parse {query}
    → LLM function calling → {process_name: "mysqld", collector_type: "perf_cpu", duration_sec: 15, sample_rate: 49}
    → /proc 扫描匹配 mysqld → candidate_pids: [{pid: 1234, comm: "mysqld"}]
  → 前端展示确认界面 + PID 选择器
  → POST /api/tasks {name: "NLP: mysqld", agent_id: ..., target_pid: 1234, collector_type: "perf_cpu", ...}
  → 完成后 POST /api/nlp/summarize → AI 总结 + 追问建议
```

---

## CLI 命令体系

所有命令默认 JSON 输出，退出码语义明确（`diff-top` 超阈值返回 2，可做 CI 门禁）。

```bash
# 基础
micro-drop serve                    # 启动 Server
micro-drop agent                    # 启动 Agent
micro-drop version                  # 显示版本
micro-drop ai-config                # AI 配置 + feature flag 状态
micro-drop install-check            # 检查系统依赖和权限

# 采集 / 管理
micro-drop collect --agent agent_1 --pid 1234 --collector perf_cpu  # 远程采集
micro-drop status                   # Server/Agent/Task 概览
micro-drop task-cancel --task-id xxx # 取消任务
micro-drop watch-task --task-id xxx  # 轮询任务直到终态

# NLP / AI
micro-drop parse "nginx CPU 飙高"   # 自然语言解析
micro-drop summarize --top-json top.json           # TopN 总结
micro-drop diagnose-local --evidence evidence.json  # 离线 RCA
micro-drop feedback-stats           # 反馈准确率统计

# 差分 / CI
micro-drop diff-top --base before.json --head after.json --threshold 5
micro-drop ci-check --base before.json --head after.json   # CI 门禁 (exit 2)
micro-drop alert --top-json top.json --hotspot-threshold 70 # 热点告警 (exit 2)

# 本地采集（无需 Server）
micro-drop perf-top --pid 1234 --duration 10  # 本地 perf TopN

# 存储 / 报告
micro-drop storage-ls                          # 列举 MinIO 产物
micro-drop storage-prune --older-than-days 30  # 清理旧产物（dry-run）
micro-drop report --top-json top.json --format markdown --output report.md

# Shell 补全
micro-drop completion --shell bash
# eval "$(micro-drop completion --shell bash)"
```

---

## API 速览

### 任务

```bash
POST   /api/tasks                          # 创建采集任务（支持 Idempotency-Key）
GET    /api/task-kinds                     # TaskKind 元数据（表单/能力/参数边界）
GET    /api/tasks?search=&sort_by=&sort_order=  # 列表（搜索+排序+分页）
GET    /api/tasks/{id}                     # 详情
POST   /api/tasks/{id}/cancel              # 幂等取消运行中的任务
POST   /api/tasks/{id}/retry               # 从终态任务创建新的重试任务
DELETE /api/tasks/{id}                     # 删除（仅终态 + 级联删除关联数据）
GET    /api/tasks/{id}/events              # 状态迁移链
GET    /api/tasks/{id}/artifacts           # 产物列表
GET    /api/tasks/{id}/artifacts/{type}/content  # 产物内容
POST   /api/tasks/{id}/diagnose            # AI 诊断
GET    /api/tasks/{id}/diagnoses           # 诊断历史
```

Web 创建与重试任务时会自动生成 `Idempotency-Key`，避免双击或网络重放产生
重复任务。Agent 会先将 `NotifyResult` 写入 `AGENT_RESULT_SPOOL_DIR`，收到
Server 确认后才删除；Server 对重复结果和 Artifact 元数据执行幂等处理。

### 诊断 + Agent + NLP

```bash
GET    /api/diagnoses/{id}                 # 诊断详情（报告+工具+修复计划）
POST   /api/diagnoses/{id}/feedback        # 提交反馈
POST   /api/v1/diagnoses                    # 创建 AI 集群诊断会话
GET    /api/v1/diagnoses/{id}               # 会话详情并推进可恢复工作流
POST   /api/v1/diagnoses/{id}/approvals     # R2 探针单次批准/拒绝
GET    /api/v1/probes                       # 受控探针注册表
POST   /api/v1/cases                        # 创建租户级 Incident Case
GET    /api/v1/cases/{id}                   # Case 五块摘要与协作状态
GET    /api/v1/cases/{id}/events            # 不可变 Case 时间线
POST   /api/v1/cases/{id}/messages          # 追加用户消息/回答
POST   /api/v1/cases/{id}/corrections       # 修正范围并使旧计划失效
POST   /api/v1/cases/{id}/diagnoses         # 固化 ContextPacket 并启动关联诊断
GET    /api/v1/cases/{id}/context-packets   # 版本化模型输入投影
GET    /api/v1/cases/{id}/model-attempts    # 模型调用审计元数据
GET    /api/v1/cases/{id}/hypotheses        # Case 级候选图、反证和缺口
GET    /api/v1/cases/{id}/iterations        # 调查动作、Policy、成本与停止判断
POST   /api/v1/cases/{id}/pause             # 暂停 Case 和关联诊断
POST   /api/v1/cases/{id}/resume            # 恢复 Case 和关联诊断
POST   /api/v1/cases/{id}/stop              # 停止并撤销 Case Grant
GET    /api/agents                         # Agent 列表（含离线检测）
GET    /api/audit-logs                     # 审计日志
POST   /api/nlp/parse                      # 自然语言解析
POST   /api/nlp/summarize                  # 任务结果 AI 总结
GET    /api/storage/presign?key=...        # MinIO 预签名 URL
GET    /api/tasks/{id}/artifacts/{type}/download  # 经 Server 流式下载产物
GET    /api/metrics                        # Prometheus 指标
GET    /api/events/stream                  # SSE 实时事件流
GET    /api/livez                         # 进程存活检查（不检查依赖）
GET    /api/readyz                        # 发布/流量就绪门槛；失败返回 503
GET    /api/healthz                       # 依赖诊断报告；请读取 healthy 字段
```

---

## 部署与运维

### Docker 部署

```bash
git clone https://github.com/jiangyulin1/mini-drop.git && cd mini-drop
cp .env.example .env
docker compose up -d
```

### 分体部署（推荐）

```
Windows Browser ── HTTPS 443 ──> Control VM
                                   ├── Server / PostgreSQL / MinIO / Web
Linux Worker 1 ── gRPC TLS 50051 ──┤
Linux Worker 2 ── gRPC TLS 50051 ──┤
Linux Worker 1/2 ── MinIO 9000 ────┘
```

```bash
# Control VM：不会启动本机 Agent，Token 和 TLS 强制开启
cp deploy/env/control.env.example deploy/env/control.env
bash deploy/scripts/generate-dev-certs.sh 10.0.0.10
docker compose --env-file deploy/env/control.env -f docker-compose.control.yml up -d --build

# 每台 Worker：修改 AGENT_ID、AGENT_IP_ADDR 和 Control 地址，并复制 ca.crt
cp deploy/env/worker.env.example deploy/env/worker.env
docker compose --env-file deploy/env/worker.env -f docker-compose.worker.yml up -d --build

# 或使用裸机 systemd Agent（安装后不会自动启动，需先编辑 worker.env）
sudo bash deploy/scripts/install-worker.sh "$PWD"
```

生产或虚拟机部署前必须根据目标环境单独确认地址、证书、身份、存储和防火墙策略；仓库不再维护绑定某次实验 IP 和发布目录的部署快照。

### 离线 / 本地 Docker（SQLite，无需拉取外部镜像）

```bash
npm --prefix web run build
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build server agent web
```

### 一键演示

```bash
# 前提：docker compose up -d 已运行
bash demo/demo.sh

# 快速过场（每个场景 5 秒）
DEMO_QUICK=1 bash demo/demo.sh

# 只跑 CPU + 内存场景
DEMO_SCENES=cpu,memory bash demo/demo.sh

# 分体部署模式（API 在远程）
SPLIT_HOST=172.17.144.1 bash demo/demo.sh
```

### 演示脚本说明

| 脚本 | 用途 |
|------|------|
| `demo/demo.sh` | 主演示：6 个场景，自动检测 Docker/本地模式 |
| `demo/vm_test_targets.py` | 15 种负载场景生成器 |
| `demo/cpu_hotspot.py` | 简单热点进程（fib/sort/json 每 60s 循环切换） |
| `demo/test_runner.py` | 自动化 E2E 测试套件（16 场景 + 报告） |
| `demo/vm_deploy.sh` | 环境一键部署（依赖安装 + 编译 + 测试） |

### VM 端 perf 权限

```bash
sudo sysctl -w kernel.perf_event_paranoid=1
```

### MinIO 公网端点

Docker 内 MinIO 使用 `minio:9000`。三机模式下 `MINIO_PUBLIC_ENDPOINT` 必须填写 Worker
可访问的 Control 地址。浏览器下载已经改为经 Server 流式转发，因此 Windows 无需访问 9000：

```bash
# 分体部署（仅 Worker 访问）
MINIO_PUBLIC_ENDPOINT=http://10.0.0.10:9000
```

---

## 安全设计

| 层次 | 措施 |
|------|------|
| **HTTP API** | Bearer / X-API-Key / query token 三通道认证 |
| **gRPC** | Token 认证拦截器 + TLS；三机 Compose 默认强制启用 |
| **产物读取** | 沙箱限制在 `MINI_DROP_ARTIFACT_ROOT` 内 |
| **产物下载** | Server 校验任务归属后流式转发；浏览器无需直连 MinIO |
| **Agent 保护** | 拒绝自剖析（target_pid == self PID 时拒绝）；参数 clamp 防资源耗尽 |
| **AI 诊断控制层** | Pydantic 拒绝未知字段、服务范围白名单、固定探针注册表、R2 人工审批、主机/实例/并发/时长预算、多机证据对比、命令仅建议不执行 |
| **密钥管理** | `.env` 已 gitignore，`.env.example` 仅模板占位符 |
| **Nginx** | CSP / HSTS / X-Frame-Options / 速率限制 |

**生产开启认证：**

```bash
MINI_DROP_API_KEY=$(openssl rand -hex 32)
MINI_DROP_GRPC_TOKEN=$(openssl rand -hex 32)
MINI_DROP_API_AUTH_ENABLED=1
MINI_DROP_GRPC_AUTH_ENABLED=1
```

Web 顶栏填写的是 `MINI_DROP_API_KEY`（Control REST 访问凭据），不是 AI Provider Key。
认证失败时 Agent/任务状态显示为“未知”并给出明确提示，不会把接口失败误显示成 0 个 Agent。

---

## AI Provider

兼容任意 OpenAI-style `/v1/chat/completions` 接口：

```bash
export MINI_DROP_AI_ENABLED=full
export MINI_DROP_AI_PROVIDER=deepseek
export MINI_DROP_AI_BASE_URL=https://api.deepseek.com
export MINI_DROP_AI_API_KEY=<your-key-here>
export MINI_DROP_AI_MODEL=deepseek-v4-flash
```

**开关层级：**

```
MINI_DROP_AI_ENABLED=none      → nlp=off, rca=off, summarize=off
MINI_DROP_AI_ENABLED=nlp-only  → nlp=on,  rca=off, summarize=off
MINI_DROP_AI_ENABLED=rca-only  → nlp=off, rca=on,  summarize=off
MINI_DROP_AI_ENABLED=full      → nlp=on,  rca=on,  summarize=on
```

不配模型 Runtime 时核心采集、火焰图和 Evidence 工作台不受影响；AI 调查明确显示未配置，不会降级成规则引擎并伪装为 AI。

### MCP 能力接入

Mini-Drop 可作为 MCP Server 向 Codex、IDE 或其他 AI Host 提供受控 Case、Evidence、
诊断和 dry-run 能力，也可将外部 MCP Server 注册为 SourceGateway 数据源。外部 MCP
调用仍强制经过 Grant、Capability Token、脱敏、结果预算、EvidenceEnvelope 和审计；
生产动作执行不会作为模型工具暴露。安装与配置见
[`docs/mcp_integration.md`](docs/mcp_integration.md)。

```bash
python -m pip install -e '.[mcp]'
MINI_DROP_MCP_AUTH_ENABLED=0 make mcp  # 仅本机 stdio 联调
```

启动 Web 后，可通过“AI 集群诊断”标题区的“AI 服务检测”按钮主动运行完整套件。验证覆盖
Provider 账户/模型/对话、自然语言任务解析、集群诊断意图与安全约束、AI 总结、RCA
证据引用校验。弹窗不会返回 API Key、余额金额或原始思维链；并发运行会被拒绝，避免
重复消耗 Token，也不额外占用一级导航页面。

---

## 开发命令

发布基线的质量门禁、数据库迁移、备份恢复和对象对账步骤见
[`docs/release-baseline-runbook.md`](docs/release-baseline-runbook.md)。一次性验证结果保存在
`reports/` 或发布记录中，不再作为长期文档维护。

```bash
# Makefile（Linux / macOS / Git Bash）
make proto          # 编译 gRPC stub
make server         # 启动 Server
make mcp            # 启动独立 MCP Server（需安装 .[mcp]）
make agent          # 启动 Agent
make test           # 运行测试
make eval           # 运行诊断 golden scenarios，生成 JSON/Markdown 报告
make lint           # 语法检查 + ruff + mypy
make fmt            # ruff format
make demo           # bash demo/demo.sh

# dev.py（跨平台）
uv run --locked python dev.py proto
uv run --locked python dev.py server
uv run --locked python dev.py agent
uv run --locked python dev.py test
uv run --locked python dev.py lint

# 完整开发流程
python -m pip install uv==0.12.5
uv sync --locked --extra dev
uv run --locked python dev.py proto
uv run --locked python dev.py server      # 终端 1
uv run --locked python dev.py agent       # 终端 2
uv run --locked python dev.py test
npm --prefix web run dev  # Vite HMR :5173（可选）
```

---

## 仓库结构

```
mini-drop/
├── server/app/           FastAPI + gRPC + RCA + NLP + Prometheus
│   ├── main.py           FastAPI 入口 + 全量 API 路由
│   ├── grpc_server.py    gRPC 后台线程启动（共进程）
│   ├── grpc_services/    gRPC 服务实现
│   ├── nlp/              自然语言意图解析 + 进程 PID 匹配 + AI 总结 + 追问
│   ├── rca/              兼容规则基线（待新 Evidence 主链完成后迁出生产包）
│   ├── diagnosis/        集群诊断会话（intent → topology → hypotheses → probes → evidence）
│   ├── models.py         SQLAlchemy ORM 模型
│   ├── sql_repository.py 数据库仓储层（读写分离 + TTL 缓存 + 级联删除）
│   ├── repository.py     内存仓储层（兼容旧版）
│   ├── state_machine.py  白名单状态机 + 迁移校验
│   └── schemas.py        Pydantic 请求/响应模型 + 参数边界常量
├── agent/mini_drop_agent/ Agent 采集端（gRPC 长连接 + 指数退避重试）
│   ├── main.py           Agent 主循环（注册 → 心跳 → 拉任务 → 执行 → 上报）
│   ├── collectors/       perf/eBPF/运行时/日志/内存/进程等采集器
│   │   └── scripts/      bpftrace 内核探针脚本
│   ├── config.py         环境变量加载 + 参数边界校验
│   └── connection.py     gRPC 连接管理 + 认证拦截器 + 重试逻辑
├── analyzer/             perf.data → stackcollapse → flamegraph JSON 树 + TopN + SVG
├── web/                  React 18 + Ant Design 5 + d3-flame-graph + ECharts
│   ├── src/pages/        Dashboard / TaskResult / AgentDetail / AuditLogs / Settings / DiagnosisHistory
│   └── src/components/   FlamegraphViewer / TopNChart / EBPFHistogram / NLPTaskInput / ErrorBoundary
├── proto/                5 个 gRPC 契约文件（common/init/healthcheck/hotmethod/control）
├── demo/                 演示脚本 & 15 种负载场景生成器
├── deploy/               Dockerfiles + nginx 配置
├── tests/                单元、集成和 E2E 测试
└── docs/                 当前设计、运行与安全文档
```

---

## 设计原则

- **gRPC 契约优先** — proto 是 Server ↔ Agent 唯一契约来源
- **采集器即插件** — 统一 `Collector(Protocol)` 接口，Server 不绑定工具
- **LLM 工具约束** — AI 只能调用预定义 Tool；采集提案和结论必须通过 Schema、Policy 与引用校验
- **结论可追溯** — 每条 claim 带 `evidence_refs`，指向原始证据字段
- **多机 Evidence 对比** — 在同一时间与身份范围内比较目标、同宿主和下游 Evidence，不自动生成根因排名
- **人机协同执行** — 模糊自然语言只生成带注释的可审核命令，高风险变更始终人工确认
- **状态机驱动** — `ALLOWED_TRANSITIONS` 白名单，每步迁移必带 `reason` + `actor`
- **降级友好** — AI 不可用时核心功能不受影响
- **防御性编程** — 路径沙箱、参数 clamp、预签名白名单、拒绝自剖析
- **密钥不入仓库** — `.env` 已 gitignore，`.env.example` 仅模板占位符

---

## 关键决策与取舍

### 为什么 Analyzer 是独立 Worker 而非 Agent 内联步骤？

采集是否成功与分析是否成功必须分别可见。Agent 先完成原始证据采集，Analyzer 再通过持久化租约异步处理；这样 Agent/Server 重启不会丢任务，分析崩溃可以有界重试，多 Worker 也不会覆盖彼此的有效提交。代价是原始 `perf.data` 需要上传，但换来了可恢复性、可审计性和统一分析版本。

### 为什么 D3 火焰图而不是 ECharts 热力图？

D3 火焰图天然支持帧级别的缩放、搜索、hover 详情，这是性能分析的核心交互。ECharts 更适合 TopN 柱状图这种标准图表。两者各自发挥所长，通过 React ref 联动。

### 为什么 bpftrace 而非 libbpf / BCC？

bpftrace 对演示场景足够——Shell 一行命令即可挂载内核探针。libbpf 更适合生产环境（CO-RE 可移植、无运行时依赖），但工程复杂度显著更高。当前阶段 bpftrace 是合理的选择，未来可升级到 libbpf。

### 为什么 AI 不直接接入火焰图全量数据？

火焰图 JSON 树可能有数千个节点，直接送 LLM Token 消耗巨大且容易产生幻觉。证据采集层提取 TopN + 结构化指标，既能被规则引擎处理，也能高效喂给 LLM。这是"人肉分析→结构化证据→LLM 推理"的工程化思路。

---

## Agent Runtime 扩展指南

真实 Linux 快照评测使用 `scripts/capture_live_collector_suite.py` 在隔离节点上运行有界负载并冻结 `/proc`、日志、TCP 探测和 `py-spy` 证据。它会验证故障进程已经清理，再由 `build` 子命令将公共 Evidence 与私有 Oracle 分目录写入；同一会话生成的数据只能作为开发评测，不能冒充独立 holdout。多轮结果使用 `scripts/aggregate_collector_agent_eval.py` 聚合为 JSON/CSV，同时保留样本量与 Wilson 区间。

- [AI 功能介绍、能力边界与设计方案](docs/ai-feature-capability-and-design.md)
- [Canonical Agent Tool Catalog](docs/agent-tool-catalog.md)
- [诊断策略与 RuntimePolicy 实验](docs/agent-runtime-experiments.md)
- [Agent 策略矩阵实验](docs/agent-strategy-matrix.md)
- [RuntimePolicy 运行权限策略](docs/runtime-policy.md)
- [能力扩展指南](docs/extension-guide.md)
- [跨运行时注册表一致性](docs/registry-consistency.md)
