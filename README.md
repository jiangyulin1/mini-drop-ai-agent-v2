<p align="center">
  <h1 align="center">🔥 Mini-Drop</h1>
  <p align="center"><strong>轻量级 Linux 性能诊断平台</strong> — 火焰图 · eBPF · AI 归因 · 自然语言采集</p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.9+-blue" alt="Python">
  <img src="https://img.shields.io/badge/react-18.x-61dafb" alt="React">
  <img src="https://img.shields.io/badge/gRPC-1.80-2ca5aa" alt="gRPC">
  <img src="https://img.shields.io/badge/tests-300-passed-green" alt="Tests">
  <img src="https://img.shields.io/badge/license-MIT-yellow" alt="License">
  <img src="https://img.shields.io/badge/CLI-28_commands-orange" alt="CLI">
</p>

---

## 🚀 三分钟上手

```bash
# 1. 克隆并启动（Docker）
git clone https://github.com/jiangyulin1/mini-drop.git && cd mini-drop
docker compose up -d
# 浏览器打开 http://localhost

# 2. 一键演示完整采集→分析→火焰图链路
make demo

# 3. 用自然语言创建采集任务
micro-drop parse "mysqld CPU 飙高，帮我看看"
```

**源码运行：**

```bash
pip install -e ".[dev]"
python dev.py proto                    # 编译 gRPC stub
python dev.py server                   # 终端 1：Server (:8191 + :50051)
python dev.py agent                    # 终端 2：Agent (自动注册 + 心跳)
python dev.py test                     # 300 个测试
```

**pip 安装：**

```bash
pip install micro-drop
micro-drop serve                       # 启动 Server
micro-drop agent                       # 启动 Agent
```

---

## 📖 目录

- [三分钟上手](#-三分钟上手)
- [架构总览](#-架构总览)
- [8 种采集器](#-8-种采集器)
- [智能归因（5 层引擎）](#-智能归因5-层引擎)
- [任务状态机](#-任务状态机)
- [Web 前端](#-web-前端)
- [CLI 命令](#-cli-命令)
- [自然语言采集](#-自然语言采集)
- [ChatOps 通知](#-chatops-通知)
- [API 速览](#-api-速览)
- [部署指南](#-部署指南)
- [环境变量](#-环境变量)
- [安全](#-安全)
- [AI Provider](#-ai-provider)
- [开发命令](#-开发命令)
- [仓库结构](#-仓库结构)
- [设计原则](#-设计原则)

---

## 🏗 架构总览

```
用户浏览器 (React SPA)
  │  SSE ← /api/events/stream (实时事件推送)
  │  REST ← /api/* (FastAPI :8191)
  │  Prometheus ← /api/metrics
  ▼
Server (FastAPI + gRPC 双端口)
  ├─ gRPC (:50051) → Agent ──→ 8 种采集器
  │                    │         └─ Analyzer CLI (perf.data → d3 火焰图 JSON 树 + TopN + SVG)
  │                    └─ Agent 心跳上报自身资源指标 (CPU/RSS/IO)
  ├─ RCA 引擎 ──→ AI API (DeepSeek / OpenAI / 任意兼容厂商)
  ├─ NLP 引擎 ──→ AI function calling → 结构化参数
  ├─ EventBus ──→ SSE 实时推送 + ChatOps (微信/钉钉/飞书/Slack/QQ)
  ├─ Repository ──→ SQLite (默认) / PostgreSQL (生产)
  └─ MinIO 对象存储 + 预签名下载 URL
```

**完整请求链路：** 用户输入自然语言 → NLP 解析意图 → `/proc` PID 匹配 → 创建任务 (PENDING) → Agent 心跳拉取 (RUNNING) → 采集执行 + 火焰图生成 (UPLOADING→ANALYZING) → 产物上传 MinIO → AI 诊断 → Web 展示 + ChatOps 推送 (DONE)

**核心端口：**

| 服务 | 端口 | 说明 |
|------|------|------|
| Web (nginx) | 80 | React SPA + API 反向代理 + SSE |
| Server HTTP | 8191 | FastAPI REST + Swagger `/docs` |
| Server gRPC | 50051 | Agent 通信 |
| PostgreSQL | 5432 | 任务/事件/审计/诊断 |
| MinIO API | 9000 | 对象存储 |
| MinIO Console | 9001 | 管理面板 |

---

## 🔬 8 种采集器

| 采集器 | 类型 key | 采集工具 | 产出物 | 适用场景 |
|--------|----------|----------|--------|----------|
| **perf CPU** | `perf_cpu` | perf record | flamegraph.json + SVG + top.json | CPU 热点分析 |
| **eBPF IO** | `ebpf_io` | bpftrace | ebpf_metrics (IO 延迟 histogram) | 磁盘 IO 瓶颈 |
| **py-spy** | `pyspy` | py-spy | 火焰图 SVG (--native 混合栈) | Python 用户态热点 |
| **Java** | `java_async` | async-profiler | HTML 火焰图 + JFR | Java/JVM 性能 |
| **Go pprof** | `go_pprof` | pprof | pprof 原始数据 + SVG | Go 服务分析 |
| **Memory** | `memory_smaps` | /proc/PID/smaps | 内存分段 JSON | 内存泄漏 / OOM |
| **SysMetrics** | `sys_metrics` | /proc 多维读取 | CPU/线程/FD/网络/IO 时序 JSON | 系统资源全景 |
| **Continuous** | `continuous_perf` | perf record (周期) | 多窗口火焰图 + 汇总 JSON | 长期趋势监控 |

所有采集器实现统一 `Collector(Protocol)` 接口：

```python
class Collector(Protocol):
    def collect(self, task: CollectorTask) -> CollectorResult: ...
```

新增采集器只需实现 `collect()` 方法，Server 不绑定任何具体工具。

---

## 🧠 智能归因（5 层引擎）

```
┌──────────┐    ┌───────────┐    ┌──────────┐    ┌────────┐    ┌──────────┐
│ ① 证据   │ → │ ② 候选    │ → │ ③ 置信度 │ → │ ④ LLM  │ → │ ⑤ 修复   │
│ 采集     │    │ 生成      │    │ 校准     │    │ 推理   │    │ 计划     │
└──────────┘    └───────────┘    └──────────┘    └────────┘    └──────────┘
     ↑                                                            │
     └─────────────── ⑥ 反馈闭环 (用户标注修正权重) ─────────────┘
```

| 层 | 职责 | 核心文件 |
|----|------|----------|
| **① 证据采集** | 从多采集器、基线对比、任务事件、Agent 指标汇总结构化证据 | `rca/evidence.py` |
| **② 候选生成** | 外部化 `rules.json`（10 种匹配器：关键词/阈值/趋势/交叉验证/多维组合） | `rca/candidates.py` + `rules.json` |
| **③ 置信度校准** | 五维加权：规则 35% + 证据质量 25% + 基线 15% + 交叉验证 15% + 反馈先验 10% | `rca/calibrator.py` |
| **④ LLM 推理** | DeepSeek function calling + 3 个 Few-Shot 样例 + 近因效应 prompt 设计 + Schema 硬约束 | `rca/llm_client.py` + `prompt.py` |
| **⑤ 修复计划** | `safe_auto`（自动执行二次采集）/ `confirm_required`（需用户确认）/ `manual_only`（人工审查） | `rca/repair.py` |
| **⑥ 反馈闭环** | 用户标注 correct/wrong/partial → 自动调整候选权重 delta → 修正后续归因 | `rca/models.py` → `rca_feedback_weights` |

**核心约束：LLM 不可自由发挥。** 每条 claim 必须带 `evidence_refs` 引用原始证据字段名；校验层检查 JSON Schema + 引用完整性，失败自动重试（最多 2 次自修复）。

**降级行为：** 未配置 AI API Key 时 → 规则引擎独立输出降级报告。

---

## 🔄 任务状态机

```
PENDING → RUNNING → UPLOADING → ANALYZING → DONE
   │         │          │            │
   └─────────┴──────────┴────────────┘→ FAILED
```

| 状态 | 含义 | 触发者 |
|------|------|--------|
| `PENDING` | 任务已创建，等待 Agent 拉取 | Web/CLI |
| `RUNNING` | Agent 正在执行采集 | Agent (心跳) |
| `UPLOADING` | 采集完成，产物正在上传 | Agent |
| `ANALYZING` | 产物已落盘，等待/正在分析 | Server |
| `DONE` | 分析完成，火焰图+TopN 可查看 | Analyzer |
| `FAILED` | 任意环节失败，带 reason | 任意 Actor |

**约束：**
- 每次迁移必须提供非空 `reason`，写入 `task_status_events` 表
- DONE / FAILED 是终态，拒绝再迁移
- 合法迁移路径由 `ALLOWED_TRANSITIONS` 白名单控制
- 每个 Actor（web/server/agent/analyzer/ai）的迁移操作可审计

---

## 🖥️ Web 前端

```
任务面板 (/)          — 总览统计卡片、NLP 输入、任务/Agent 列表、SSE toast 通知、10s 自动轮询
任务详情 (/task/:id)   — d3 交互式火焰图 + ECharts TopN 联动、状态时间线、产物表、AI 归因 + 反馈
诊断历史 (/diagnoses)  — 全量诊断记录、置信度筛选、搜索过滤
Agent 详情 (/agent/:id) — 资源趋势、采集能力标签、关联任务列表
审计日志 (/audit)      — 全量审计事件分页、Agent 上下线/任务创建/诊断完成
系统设置 (/settings)   — AI 配置、ChatOps 配置、服务健康、API 认证状态
```

**技术栈：** React 18 + Ant Design 5 + d3-flame-graph + ECharts + Vite 5

**实时推送：** SSE (Server-Sent Events) + EventBus。前端 `useSSE` hook 支持指数退避自动重连（最大 30s）、页面隐藏时保持连接。`usePolling` hook 在 SSE 断线时以 10s 间隔兜底轮询，页面隐藏自动暂停。

---

## 🔧 CLI 命令（28 条）

所有命令默认 JSON 输出，适合管道 / CI / 脚本。退出码语义明确（`diff-top` 超阈值时返回 2，可做 CI 门禁）。

### 基础命令

```bash
micro-drop serve                                 # 启动 Server
micro-drop agent                                 # 启动 Agent
micro-drop version                               # 显示版本
micro-drop ai-config                             # 打印 AI Provider 配置和开关状态
micro-drop install-check                         # 检查系统依赖 (perf/bpftrace/py-spy) 和权限
micro-drop install-check --full                  # 含可选工具的完整检查
```

### 远程管理

```bash
micro-drop collect --agent agent_1 --pid 1234 --collector perf_cpu  # 一键远程采集
micro-drop status                                # Server/Agent/Task 概览
micro-drop status --agents --tasks               # 含 Agent 资源指标 + 活跃任务
micro-drop task-cancel --task-id task_xxx         # 取消运行中任务
micro-drop diagnose-remote --task-id task_xxx     # 远程触发 AI 诊断
micro-drop watch-task --task-id task_xxx          # 轮询任务直到 DONE/FAILED
```

### NLP / AI

```bash
micro-drop parse "mysqld CPU 飙高"                # 自然语言 → 结构化采集参数
micro-drop summarize --top-json top.json          # TopN 结果 AI 总结
micro-drop diagnose-local --evidence evidence.json # 离线 RCA（无需 Server）
micro-drop feedback-stats                        # RCA 反馈准确率统计
```

### 差分分析 / CI 门禁 / 告警

```bash
micro-drop diff-top --base before.json --head after.json --threshold 5  # TopN 差分
micro-drop ci-check --base before.json --head after.json               # CI 性能门禁 (exit 2)
micro-drop alert --top-json top.json --hotspot-threshold 70             # 热点告警 (exit 2)
```

### 批量 / 报告 / 存储

```bash
micro-drop batch-diagnose --dir evidence/         # 批量离线 RCA
micro-drop report --top-json top.json --format markdown --output report.md
micro-drop export-summary --top-json top.json --format markdown
micro-drop storage-ls                             # 列出 MinIO 产物
micro-drop storage-prune --older-than-days 30     # dry-run 预览清理
micro-drop storage-prune --older-than-days 30 --execute  # 执行清理
micro-drop agent-exec --diagnosis-id diag_xxx --action-index 0  # 查看修复计划
```

### Shell 工具

```bash
micro-drop keywords --kind collectors             # 打印关键词字典
micro-drop suggest per                            # 前缀补全建议
micro-drop completion --shell bash                # Shell 自动补全脚本
# eval "$(micro-drop completion --shell bash)"
```

### ChatOps

```bash
micro-drop chatops-config                         # ChatOps 配置
micro-drop chatops-test                           # 测试 IM 消息发送
micro-drop chatops-notify --title "告警" --content "CPU > 80%" --level warning
```

### 本地采集（无需 Server）

```bash
micro-drop perf-top --pid 1234 --duration 10      # 纯本地 perf TopN
```

---

## 🗣️ 自然语言采集

```
用户输入 "mysqld CPU 飙高，帮我看看"
  → DeepSeek function calling 解析
    → collector_type: perf_cpu, duration: 15s, sample_rate: 99
  → /proc 进程名 → PID 模糊匹配（候选列表）
  → 用户确认 → 创建任务 → Agent 执行 → AI 总结
```

**安全护栏：** LLM 只能调用 `create_profiling_task` 这个预定义 function，不得自由输出决策。参数 clamp 到安全范围（duration 5-120s, sample_rate 1-999Hz）。进程解析不做在 LLM 中（LLM 不知道系统上有什么进程）。

**AI 不可用时：** 基于关键词的保守匹配（"CPU/飙高/卡顿"→perf_cpu, "磁盘/IO/读写"→ebpf_io, "Python/Flask"→pyspy, "持续/监控"→continuous_perf）。

---

## 📢 ChatOps 通知

| 平台 | Provider Key | 模式 |
|------|-------------|------|
| 企业微信 | `wecom` | Webhook |
| 飞书 | `feishu` | Webhook |
| 钉钉 | `dingtalk` | Webhook |
| Slack | `slack` | Webhook |
| QQ 机器人 | `qqbot` | WebSocket 反向连接 |

```bash
export MINI_DROP_CHATOPS_ENABLED=1
export MINI_DROP_CHATOPS_PROVIDER=wecom
export MINI_DROP_CHATOPS_WEBHOOK_URL=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
```

**触发事件：** 任务完成 (`DONE`)、任务失败 (`FAILED`)、Agent 离线 (`OFFLINE`)、诊断完成/失败。通过 EventBus 订阅 → IM 消息格式化 → 异步发送。

---

## 📡 API 速览

### 任务

```bash
POST   /api/tasks                          # 创建采集任务
GET    /api/tasks                          # 任务列表
GET    /api/tasks/{task_id}                # 任务详情
GET    /api/tasks/{task_id}/events         # 状态迁移事件链
GET    /api/tasks/{task_id}/artifacts      # 产物列表
GET    /api/tasks/{task_id}/artifacts/{type}/content  # 产物内容
POST   /api/tasks/{task_id}/diagnose       # 触发 AI 诊断
GET    /api/tasks/{task_id}/diagnoses      # 任务诊断历史
```

### 诊断与反馈

```bash
GET    /api/diagnoses/{diagnosis_id}       # 诊断详情（报告+工具结果+修复计划）
POST   /api/diagnoses/{diagnosis_id}/feedback  # 提交反馈 (correct/wrong/partial) → 修正权重
```

### Agent 管理

```bash
GET    /api/agents                         # Agent 列表（自动检测离线）
GET    /api/audit-logs                     # 审计日志（上下线/任务创建/诊断）
```

### NLP

```bash
POST   /api/nlp/parse                      # 自然语言解析 → 结构化采集参数 + PID 候选
POST   /api/nlp/summarize                  # 任务结果 AI 总结 + 追问建议
```

### 存储 & 监控 & 事件

```bash
GET    /api/storage/presign?key=tasks/xxx/perf.data  # MinIO 预签名下载 URL
GET    /api/metrics                                   # Prometheus 文本格式
GET    /api/events/stream                             # SSE 实时事件流
GET    /api/healthz                                   # 服务健康检查
```

### 认证

API 认证启用后（`MINI_DROP_API_AUTH_ENABLED=1`），所有请求需携带：

```bash
# 方式 1：Bearer Token
curl -H "Authorization: Bearer $MINI_DROP_API_KEY" http://localhost/api/tasks

# 方式 2：X-API-Key Header
curl -H "X-API-Key: $MINI_DROP_API_KEY" http://localhost/api/tasks

# 方式 3：Query Param（预签名 URL 场景）
curl "http://localhost/api/tasks?token=$MINI_DROP_API_KEY"
```

---

## 🐳 部署指南

### 标准部署（PostgreSQL + MinIO 全栈）

```bash
git clone https://github.com/jiangyulin1/mini-drop.git && cd mini-drop
cp .env.example .env          # 按需编辑
docker compose up -d
# 浏览器打开 http://localhost
```

### 离线/本地部署（SQLite，无需拉取外部镜像）

```bash
npm --prefix web run build
docker compose -f docker-compose.yml -f docker-compose.local.yml up -d --build server agent web
```

此模式：SQLite 替代 PostgreSQL、共享 volume 替代 MinIO、使用本地预编译前端。

### 跳过 Node 镜像拉取

```bash
npm --prefix web run build
docker compose -f docker-compose.yml -f docker-compose.prebuilt-web.yml build web
```

### Host 宿主机 perf 权限

```bash
echo 'kernel.perf_event_paranoid=1' | sudo tee /etc/sysctl.d/99-mini-drop.conf
sudo sysctl -p /etc/sysctl.d/99-mini-drop.conf
```

### 容器权限说明

Agent 容器需 `privileged: true` + `pid: host` + `SYS_ADMIN` + `BPF` + `SYS_PTRACE` + `PERFMON`，因为 perf 和 bpftrace 需要访问宿主机内核接口。生产环境可评估以 root 运行 Agent 替代 privileged 模式。

### MinIO 公网端点

Docker 内 MinIO 使用 `minio:9000`（容器网络），浏览器需通过宿主机端口访问。设置 `MINIO_PUBLIC_ENDPOINT` 为浏览器可达地址：

```bash
# 本地
MINIO_PUBLIC_ENDPOINT=localhost:9000

# 远程 VM
MINIO_PUBLIC_ENDPOINT=172.24.188.165:9000
```

---

## 🔐 安全

| 层次 | 措施 |
|------|------|
| **HTTP API** | Bearer / X-API-Key / query token 三通道认证 |
| **gRPC** | Token metadata 拦截 (`x-mini-drop-grpc-token`) + 可选 TLS |
| **产物读取** | 沙箱限制在 `MINI_DROP_ARTIFACT_ROOT` 内，禁止路径穿越 |
| **预签名 URL** | 有效期可配（默认 1h），禁止签发 `tasks/` 外前缀 |
| **Agent 保护** | 拒绝自剖析（target_pid 与自身 PID 相同时拒绝） |
| **密钥管理** | `.env` 已 gitignore，模板 `.env.example` 仅含占位符 |
| **Nginx 安全头** | CSP / HSTS / X-Frame-Options / X-Content-Type-Options / Referrer-Policy |
| **速率限制** | nginx `limit_req` 30r/s + `limit_conn` 10 并发 |
| **Docker** | Agent `no-new-privileges:true` + seccomp 配置 |

**生产安全配置：**

```bash
# 生成随机密钥
MINI_DROP_API_KEY=$(openssl rand -hex 32)
MINI_DROP_GRPC_TOKEN=$(openssl rand -hex 32)

# 开启认证
MINI_DROP_API_AUTH_ENABLED=1
MINI_DROP_GRPC_AUTH_ENABLED=1

# 开启 gRPC TLS
AGENT_GRPC_SECURE=1
AGENT_GRPC_CA_CERT=/path/to/ca-cert.pem

# 关闭调试信息
MINI_DROP_ENV=production
```

---

## 🤖 AI Provider

兼容任意 OpenAI-style `/v1/chat/completions` 接口：

```bash
export MINI_DROP_AI_ENABLED=full
export MINI_DROP_AI_PROVIDER=deepseek
export MINI_DROP_AI_BASE_URL=https://api.deepseek.com
export MINI_DROP_AI_API_KEY=sk-xxxxxxxx
export MINI_DROP_AI_MODEL=deepseek-chat
```

**开关层级（粗→细）：**

```
MINI_DROP_AI_ENABLED=none            → nlp=off, rca=off, summarize=off
MINI_DROP_AI_ENABLED=nlp-only        → nlp=on,  rca=off, summarize=off
MINI_DROP_AI_ENABLED=rca-only        → nlp=off, rca=on,  summarize=off
MINI_DROP_AI_ENABLED=full (默认)     → nlp=on,  rca=on,  summarize=on

细粒度覆盖（优先级高于全局）：
MINI_DROP_NLP_ENABLED=true/false
MINI_DROP_RCA_ENABLED=true/false
MINI_DROP_SUMMARIZE_ENABLED=true/false
```

**降级行为：** AI 完全不可用时 → NLP 走关键词 fallback、RCA 走纯规则引擎报告、Summarize 走模板生成。核心采集/火焰图功能不受影响。

---

## 🛠 开发命令

项目提供两套等价入口，跨平台可用：

```bash
# ── 入口 A：Makefile (Linux / macOS / Git Bash) ──
make proto          # 编译 gRPC stub
make server         # 启动 Server
make agent          # 启动 Agent
make test           # 运行全部测试
make lint           # 编译级语法检查
make demo           # 一键演示（需 docker compose up -d 前提）
make deploy         # docker compose up -d
make deploy-down    # docker compose down

# ── 入口 B：dev.py (跨平台：Windows cmd/PowerShell 也可用) ──
python dev.py proto
python dev.py server
python dev.py agent
python dev.py test
python dev.py test -- -k "e2e"     # 按关键字筛选测试
python dev.py lint
python dev.py demo
python dev.py install              # pip install -e ".[dev]"
```

**完整开发流程：**

```bash
# 1. 安装依赖
python dev.py install

# 2. 编译 proto
python dev.py proto

# 3. 启动 Server + Agent（两个终端）
python dev.py server      # 终端 1 → FastAPI :8191 + gRPC :50051
python dev.py agent       # 终端 2 → 自动注册并心跳

# 4. 运行测试
python dev.py test

# 5. (可选) 启动前端开发服务器
npm --prefix web run dev  # Vite HMR :5173 → 代理到 localhost:8191
```

---

## 📦 仓库结构

```
mini-drop/
├── server/app/
│   ├── main.py                  # FastAPI 入口 (45+ 端点) + lifespan
│   ├── cli.py                   # CLI 入口 (28 条子命令, 全 JSON 输出)
│   ├── grpc_server.py           # gRPC server 启动 (后台线程)
│   ├── grpc_auth.py             # gRPC token 认证拦截器
│   ├── sql_repository.py        # SQLAlchemy 持久化 (SQLite/PostgreSQL 双后端)
│   ├── repository.py            # InMemoryRepository (线程安全)
│   ├── state_machine.py         # 6 状态任务状态机 + 迁移白名单
│   ├── database.py              # DB 引擎 + session factory (延迟创建)
│   ├── models.py                # 9 个 ORM 模型 (Agent/Task/Event/Audit/Artifact/Diagnosis/...)
│   ├── schemas.py               # Pydantic 请求/响应模型
│   ├── storage.py               # MinIO 客户端 (上传 + 预签名 URL)
│   ├── ai_provider.py           # AI 配置加载 + feature flag 判断
│   ├── event_bus.py             # 异步事件总线 (weakref 防泄漏, SSE + ChatOps 消息源)
│   ├── prometheus_metrics.py    # Prometheus 指标注册表 (Counter/Gauge/Histogram)
│   ├── logging_utils.py         # JSON 结构化日志
│   ├── common_utils.py          # env_bool + status_value 公共服务
│   ├── analyzer_runner.py       # Server 侧 Analyzer 降级执行
│   ├── _env.py                  # 自动加载 .env (区分 Docker/本地模式)
│   ├── grpc_services/           # 4 个 gRPC 服务实现
│   │   ├── init_service.py      # Agent 注册 + 配置下发 (MinIO 凭证)
│   │   ├── healthcheck_service.py  # 心跳 + 任务分发 (含 busy 标记)
│   │   ├── hotmethod_service.py # 采集结果上报 + UPLOADING→ANALYZING→DONE
│   │   └── control_service.py   # gRPC 控制面 (CreateTask / StatAgent)
│   ├── nlp/                     # 自然语言采集
│   │   ├── intent_parser.py     # function calling 解析 + 关键词 fallback
│   │   ├── process_resolver.py  # /proc 进程名 → PID 模糊匹配
│   │   ├── summarizer.py        # AI 结果总结 + 追问建议生成
│   │   └── tool_schemas.py      # create_profiling_task JSON Schema
│   ├── rca/                     # 智能归因 5 层引擎
│   │   ├── evidence.py          # ① 证据采集 (6 种数据源)
│   │   ├── candidates.py        # ② 规则引擎 (10 种匹配器, rules.json 外部化)
│   │   ├── calibrator.py        # ③ 五维置信度校准
│   │   ├── llm_client.py        # ④ LLM 调用 + 校验 + 自修复 (max 2 retries)
│   │   ├── prompt.py            # System prompt + 3 Few-Shot 样例 + 近因效应设计
│   │   ├── report.py            # 编排入口 (工具→证据→候选→校准→LLM→修复)
│   │   ├── repair.py            # ⑤ 修复计划 (safe_auto / confirm_required / manual_only)
│   │   ├── tools.py             # 诊断工具层 (火焰图摘要 / eBPF 摘要 / 基线对比)
│   │   ├── rules.json           # 6 条可扩展 RCA 规则
│   │   └── models.py            # Pydantic 数据模型 (EvidenceInput/CauseEntry/RepairPlan/...)
│   └── chatops/                 # IM 通知
│       ├── dispatcher.py        # EventBus → IM 消息格式化 → 异步发送
│       ├── base.py              # ChatopsMessage + BaseProvider 抽象
│       └── providers/           # 5 平台实现 (dingtalk/feishu/wecom/slack/qqbot)
├── agent/mini_drop_agent/
│   ├── main.py                  # 心跳循环 + 任务拉取 + worker 线程采集
│   ├── config.py                # Agent 配置 (含真实 IP 自动探测)
│   ├── connection.py            # gRPC 长连接 + TLS + token 认证 + 指数退避重试
│   ├── metrics.py               # /proc 进程资源采样器 (CPU/RSS/IO)
│   ├── logging_utils.py         # JSON 日志
│   ├── artifact_upload.py       # MinIO 上传 (可选, 默认本地路径)
│   └── collectors/
│       ├── base.py              # CollectorTask + CollectorResult + Collector(Protocol)
│       ├── perf.py              # perf record CPU 采样 + 本地 Analyzer 闭环
│       ├── ebpf.py              # bpftrace IO 延迟采集 + histogram 解析
│       ├── pyspy.py             # py-spy Python 采样 (--native fallback 降级)
│       ├── continuous.py        # 持续 Profiling (多窗口周期采样)
│       ├── java_async.py        # Java async-profiler
│       ├── pprof.py             # Go pprof
│       ├── memory.py            # /proc/PID/smaps 内存分析
│       ├── sys_metrics.py       # /proc 多维指标时间序列
│       └── scripts/             # bpftrace 脚本 (io_latency.bt)
├── analyzer/
│   ├── mini_drop_analyzer/
│   │   └── hotmethod_analyzer.py  # perf.data → 火焰图 JSON 树 + TopN + SVG + 规则建议
│   └── scripts/                    # stackcollapse-perf.pl + flamegraph.pl (Brendan Gregg)
├── web/
│   └── src/
│       ├── router.jsx              # React Router (6 页面)
│       ├── main.jsx                # 入口 (ConfigProvider + zh_CN)
│       ├── components/             # AppLayout, StatusTag, NLPTaskInput, FlamegraphViewer, TopNChart, ErrorAlert
│       ├── pages/                  # Dashboard, TaskResult, AuditLogs, DiagnosisHistory, AgentDetail, Settings
│       ├── hooks/                  # useSSE (EventSource 自动重连), usePolling (页面隐藏暂停)
│       ├── utils/                  # html.js, status.js
│       └── api/client.js           # axios 封装 + SSE EventSource
├── proto/                          # 5 个 gRPC 契约文件 (参考 DeepFlow message/ 模式)
│   ├── common.proto                # PidStats / CosConfig 通用结构
│   ├── init.proto                  # InitAgent (注册 + 配置下发)
│   ├── healthcheck.proto           # HealthCheck (心跳 + 任务拉取)
│   ├── hotmethod.proto             # Hotmethod (结果上报)
│   ├── control.proto               # Control (创建任务 / 查询 Agent)
│   └── compile.sh                  # 编译脚本 → server/app/generated/
├── deploy/
│   ├── dockerfiles/                # agent/server/web/web.prebuilt 四组 Dockerfile
│   ├── nginx/default.conf          # nginx 反向代理 + SSE + 速率限制 + 安全头
│   └── napcat/                     # QQ 机器人运行时
├── demo/                           # 演示负载脚本
│   ├── demo.sh                     # 一键端到端演示
│   ├── cpu_hotspot.py              # CPU 热点模拟进程
│   ├── e2e_final.sh / e2e_fix_round.sh  # 完整链路测试
│   └── test_runner.py / vm_test_targets.py / vm_deploy.sh  # VM 测试套件
├── tests/                          # 28 个测试文件 (>300 个用例)
│   ├── test_e2e.py                 # 4 个端到端 (正常路径/失败/离线/状态链)
│   ├── test_perf_collector.py      # perf 采集器单元测试
│   ├── test_grpc_services.py       # gRPC 服务测试
│   ├── test_rca.py / test_rca_enhanced.py  # RCA 引擎测试
│   └── test_*.py                   # 覆盖全部模块
├── docs/
│   ├── design_doc_final.md         # 完整设计文档
│   └── rca_eval_report.md          # 归因引擎评测报告 (4 场景)
├── docker-compose.yml              # 全栈部署 (PostgreSQL + MinIO + Server + Agent + Web)
├── docker-compose.local.yml        # 离线模式 (SQLite + 共享 volume)
├── docker-compose.prebuilt-web.yml # 跳过 Node 镜像拉取
├── Makefile                        # Linux/macOS 开发入口
├── dev.py                          # 跨平台开发入口
├── pyproject.toml                  # Python 项目配置
├── .env.example                    # 环境变量模板
└── .gitignore / .dockerignore      # 忽略规则
```

---

## 📐 设计原则

- **gRPC 契约优先** — proto 是 Server ↔ Agent 唯一契约来源，参考 DeepFlow `message/` 模式
- **采集器即插件** — 统一 `Collector(Protocol)` 接口，Server 不绑定具体工具
- **LLM 工具约束** — AI 只能调用预定义 tool schema，不做自由决策；输出必须过 Schema + 证据引用完整性校验
- **归因可追溯** — 每条 claim 带 `evidence_refs`，可回到原始证据字段
- **状态机驱动** — 基于 `ALLOWED_TRANSITIONS` 白名单，每步迁移必带 `reason`
- **降级友好** — AI 不可用时：NLP → 关键词匹配、RCA → 规则引擎降级报告、Summarize → 模板生成
- **CLI 脚本优先** — 所有命令默认 JSON 输出，退出码语义明确（0=成功, 1=失败, 2=阈值告警）
- **防御性编程** — 路径沙箱、参数 clamp、预签名白名单、拒绝自剖析、proto 字段 reserved
- **密钥不入仓库** — `.env` 已 gitignore，`.env.example` 仅含模板占位符
- **本地友好** — `_env.py` 自动加载 .env、区分 Docker/本地模式、Docker 专属变量本地自动跳过
