# Mini-Drop 完整演示体系

> 目标：在一台**干净 Ubuntu 22.04 机器**上，`git clone && docker compose up` 后 **10 分钟内**跑通端到端演示。
> 评审可逐条复制命令执行，全程约 15 分钟（含 Docker 拉取镜像时间）。

---

## 目录

- [1. 环境准备（~5 分钟）](#1-环境准备5-分钟)
- [2. 一键全栈 Docker 部署（~5 分钟）](#2-一键全栈-docker-部署5-分钟)
- [3. 端到端演示 6 场景（~5 分钟）](#3-端到端演示-6-场景5-分钟)
- [4. 手动逐条命令讲解（评审演示用）](#4-手动逐条命令讲解评审演示用)
- [5. 常见问题排查](#5-常见问题排查)
- [附录 A：演示脚本速查](#附录-a演示脚本速查)
- [附录 B：评审交付物检查清单](#附录-b评审交付物检查清单)

---

## 1. 环境准备（~5 分钟）

### 硬件要求

| 项 | 最低要求 | 推荐 |
|------|----------|------|
| CPU | 2 核 | 4 核（Docker 需同时运行 5 个容器 + 负载） |
| 内存 | 6 GB | 8 GB 以上 |
| 磁盘 | 10 GB | 20 GB（Docker 镜像约 3GB + 产物约 500MB） |
| Linux 内核 | 5.4+ | 5.15+（eBPF 需要 BPF 特性） |

### 操作系统

Ubuntu 22.04 LTS（其他发行版需自行适配 Docker 和权限配置）。

### 一键准备（全部命令复制执行）

```bash
# ── 1. 安装 Docker ──────────────────────────────────────
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
# ⚠️ 重要：登出后重新登录，或执行: newgrp docker

# ── 2. 安装 make、perf、bpftrace ─────────────────────────
sudo apt-get update
sudo apt-get install -y make linux-tools-$(uname -r) bpftrace

# ── 3. 安装 py-spy（用于 Python 采样演示）───────────────
pip3 install py-spy

# ── 4. 设置性能采样权限（永久生效）──────────────────────
echo 'kernel.perf_event_paranoid=1' | sudo tee /etc/sysctl.d/99-mini-drop.conf
sudo sysctl -p /etc/sysctl.d/99-mini-drop.conf

# ── 5. 验证关键依赖 ─────────────────────────────────────
docker --version
docker compose version
perf --version
bpftrace --version 2>/dev/null && echo "bpftrace OK" || echo "bpftrace not found"
```

---

## 2. 一键全栈 Docker 部署（~5 分钟）

```bash
# 1. 克隆项目
git clone https://github.com/jiangyulin1/mini-drop.git
cd mini-drop

# 2. 创建环境变量（默认值即可直接使用）
cp .env.example .env

# 3. 编译 gRPC stub（镜像构建时需要）
cd proto && bash compile.sh && cd ..

# 4. 启动全栈服务（5 个容器）
#    拉取镜像约 2-3 分钟，启动约 30 秒
docker compose up -d

# 5. 验证所有服务就绪
#    等待约 10 秒后执行：
curl http://localhost/api/healthz
#    应返回: {"code":0,"message":"ok","data":{"service":"mini-drop-server","version":"0.1.0"}}

#    浏览器打开 http://localhost
#    - 应看到 Mini-Drop Web 界面
#    - Agent 列表中应看到 agent_docker_demo 状态为 ONLINE

# 6. 查看服务日志
docker compose logs -f server    # Server 日志
docker compose logs -f agent     # Agent 日志
```

### 启动后的服务

| 服务 | 地址 | 说明 |
|------|------|------|
| Web 控制台 | http://localhost | React SPA 前端 |
| Swagger 文档 | http://localhost:8191/docs | API 交互式调试 |
| MinIO 管理 | http://localhost:9001 | 用户名/密码：mini_drop / mini_drop_secret |
| PostgreSQL | localhost:5432 | 数据库直连 |

---

## 3. 端到端演示 6 场景（~5 分钟）

### 3.1 一键演示（推荐）

```bash
# 全部 6 个场景，每个约 15 秒
bash demo/demo.sh

# 或者用 make
make demo
```

### 3.2 快速过场（5 秒/场景，适合时间紧张）

```bash
DEMO_QUICK=1 bash demo/demo.sh
```

### 3.3 选择特定场景

```bash
DEMO_SCENES=cpu,memory bash demo/demo.sh
# 可选标签: cpu python memory sys io lock
```

### 3.4 演示脚本输出预览

```
══════════════════════════════════════════════
  场景1: CPU 火焰图采集
══════════════════════════════════════════════

  ▸ 启动负载进程…
     PID=12345
  ▸ 创建采集任务…
     任务ID: task_20240617_143022_a1b2c3
  ▸ 等待采集完成…
     状态: DONE
  ▸ 产物列表：
     flamegraph_json            flamegraph.json        4096 bytes
     flamegraph_svg             flamegraph.svg        28672 bytes
     top_json                   top.json               2048 bytes

  TopN 热点函数:
  #1   68.5%  fib_hotspot
  #2   13.4%  sort_hotspot
  #3    8.1%  json_hotspot
```

---

## 4. 手动逐条命令讲解（评审演示用）

以下命令适合在评审时逐条执行、逐行讲解。每个场景都包含完整的"创建任务 → 轮询 → 查看产物"链路。

### 场景 A：CPU 火焰图（核心链路，3 分钟）

```bash
# 1. 启动 CPU 热点进程
python3 demo/cpu_hotspot.py &
# 记住输出的 PID，例如 12345
TARGET=12345

# 2. 创建 perf 采集任务
curl -X POST http://localhost/api/tasks \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"CPU演示\",\"agent_id\":\"agent_docker_demo\",\"target_pid\":$TARGET,\"collector_type\":\"perf_cpu\",\"sample_rate\":99,\"duration_sec\":15}"

# 3. 复制返回的 task_id，轮询状态
curl -s http://localhost/api/tasks/<TASK_ID> | python3 -m json.tool

# 4. 状态变化: PENDING → RUNNING → UPLOADING → ANALYZING → DONE

# 5. 查看产物列表
curl -s http://localhost/api/tasks/<TASK_ID>/artifacts | python3 -m json.tool

# 6. 查看 TopN 热点
curl -s http://localhost/api/tasks/<TASK_ID>/artifacts/top_json/content | python3 -m json.tool

# 7. 下载火焰图 SVG 到本地查看
curl -s http://localhost/api/tasks/<TASK_ID>/artifacts/flamegraph_svg/content > /tmp/flamegraph.svg
# 浏览器打开 /tmp/flamegraph.svg

# 8. 触发 AI 诊断
curl -X POST http://localhost/api/tasks/<TASK_ID>/diagnose | python3 -m json.tool
```

### 场景 B：eBPF IO 延迟观测（演示加分项，2 分钟）

```bash
# 1. 制造块设备 IO 压力
dd if=/dev/zero of=/tmp/io-test bs=4M count=512 oflag=direct &

# 2. 创建 eBPF 采集任务（target_pid=1 即可，探针是系统级的）
curl -X POST http://localhost/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"name":"eBPF IO演示","agent_id":"agent_docker_demo","target_pid":1,"collector_type":"ebpf_io","duration_sec":15}'

# 3. 查看 IO 延迟分布
curl -s http://localhost/api/tasks/<TASK_ID>/artifacts/ebpf_metrics/content | python3 -m json.tool
# 输出应包含: {"io_latency_us": {"[128, 256)": 50, "[256, 512)": 12, ...}, "total_samples": 62}
```

### 场景 C：自然语言采集（30 秒）

```bash
curl -X POST http://localhost/api/nlp/parse \
  -H "Content-Type: application/json" \
  -d '{"query":"mysqld CPU 飙高，帮我看看"}'
# 返回结构化参数：采集器 perf_cpu，时长 15s，采样率 99Hz
```

---

## 5. 常见问题排查

| 症状 | 原因 | 解决 |
|------|------|------|
| `docker compose up` 后 `localhost` 无法访问 | Docker 未启动或端口冲突 | `docker compose ps` 检查状态，`docker compose logs -f` 查看日志 |
| Agent 状态 OFFLINE | 心跳超时（默认 30 秒） | `docker compose restart agent` |
| Agent 容器重启循环 | gRPC 连接不上 Server | 先确认 Server healthy：`docker compose logs server` |
| 任务一直 PENDING | AGENT_ID 不匹配 | `bash demo/demo.sh` 会自动检测；或手动用 `agent_docker_demo` |
| perf 采集失败 | perf_event_paranoid ≥ 2 | `sudo sysctl -w kernel.perf_event_paranoid=1` |
| bpftrace 采集失败 | 缺少内核头文件 | `sudo apt-get install -y linux-headers-$(uname -r)` |
| py-spy 采集失败（Docker） | 容器权限不足 | 已在 docker-compose.yml 配置 privileged:true + SYS_PTRACE |
| 火焰图/产物为空 | 采样时间太短或符号缺失 | 延长 `duration_sec` 到 30s |
| 测试跑不过 | gRPC 版本不匹配 | `pip install grpcio>=1.80 grpcio-tools>=1.80` |
| `make: command not found` | 纯净系统未安装 make | `sudo apt-get install -y make` |
| `.env file not found` | 未复制模板 | `cp .env.example .env` |
| 前端白屏 | web 容器未就绪 | `docker compose logs web` 检查 nginx 是否启动 |
| MinIO 上传失败 | 磁盘空间不足 | `df -h` 检查可用空间，清理 `/tmp/mini-drop/*` |

---

## 附录 A：演示脚本速查

| 脚本 | 位置 | 用途 | 执行方式 |
|------|------|------|----------|
| 主演示 | `demo/demo.sh` | 6 场景端到端演示 | `bash demo/demo.sh` |
| 负载场景生成器 | `demo/vm_test_targets.py` | 15 种性能压测 | `python3 demo/vm_test_targets.py cpu-fib 30` |
| 简单热点进程 | `demo/cpu_hotspot.py` | 简易 CPU 热点 | `python3 demo/cpu_hotspot.py &` |
| 自动化 E2E 测试 | `demo/test_runner.py` | 16 场景测试+报告 | `sudo python3 demo/test_runner.py --quick` |
| 环境部署 | `demo/vm_deploy.sh` | 一键安装依赖+测试 | `bash demo/vm_deploy.sh` |
| 演示指南 | `DEMO.md` | 三种部署方式的命令指导 | — |

---

## 附录 B：评审交付物检查清单

| # | 交付物 | 位置 | 状态 |
|---|--------|------|------|
| 1 | Git 仓库链接，提交历史完整 | https://github.com/jiangyulin1/mini-drop (88 commits) | ✅ |
| 2 | `docker compose up` + `make demo` 一键跑通，README 写明硬件/内核/权限要求 | ✅ `README.md` 环境要求章节 |
| 3 | ≤ 10 页设计文档：架构图、状态机迁移图、关键决策、取舍说明、AI 协作、性能自证、"再有 7 天" | ✅ `docs/design_doc_final.md` |
| 4 | ≤ 15 分钟演示视频 | — | 需录制 |
| 5 | 智能归因评测报告 | ✅ `docs/rca_eval_report.md` |

### 基础能力检查

| # | 要求 | 验证方法 |
|---|------|----------|
| 1 | Web UI 指定 PID/时长/采样率 → Server → Agent | `curl -X POST /api/tasks` 并观察 Agent 日志 |
| 2 | Agent 采集并通知 | 任务最终状态为 DONE |
| 3 | Analyzer 转为火焰图 | 产物含 flamegraph_json / flamegraph_svg |
| 4 | 状态机 PENDING→→DONE/FAILED，每步带 reason | `GET /api/tasks/{id}/events` 查看迁移链 |
| 5 | Agent 5s 心跳，30s 离线，审计日志 | `docker compose stop agent` 30 秒后看 audit-logs |
| 6 | 单测覆盖，E2E 测试 | `python dev.py test` (225 tests) |

### 扩展能力检查

| # | 要求 | 验证方法 |
|---|------|----------|
| 1 | Continuous Profiling | `collector_type=continuous_perf` 创建任务 |
| 2a | eBPF 采集器真跑 | 场景 B，Web 上看到 IO 延迟 histogram |
| 2b | py-spy 用户态采集 | `collector_type=pyspy` 创建任务 |

### 加分项检查

| # | 要求 | 验证方法 |
|---|------|----------|
| 1 | 智能归因 | `POST /api/tasks/{id}/diagnose` 查看报告 |
| 2 | 自然语言采集 | `POST /api/nlp/parse` 查看结构化结果 |
