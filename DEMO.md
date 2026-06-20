# Mini-Drop 现场演示流程

在两台机器上搭建完整演示环境：宿主机运行 Web/Server/数据库，虚拟机运行 Agent 和真实负载。全程约 30 分钟。

---

## 演示拓扑

```
┌─ 宿主机 (你的新电脑, Windows/macOS/Linux) ─────────────────────────────┐
│                                                                          │
│   Docker Compose                                                        │
│   ├── Web (nginx :80)         ← 浏览器打开 http://localhost              │
│   ├── Server (FastAPI :8191)  ← REST API + gRPC :50051                  │
│   ├── PostgreSQL (:5432)      ← 任务/事件/审计/诊断数据                  │
│   └── MinIO (:9000)           ← 火焰图/产物对象存储                      │
│                                                                          │
│                           ↑ gRPC :50051                                  │
│                           │ (需确保 VM 能访问宿主机 IP)                    │
├───────────────────────────│──────────────────────────────────────────────┤
│                           ↓                                              │
│   ┌─ 虚拟机 (Ubuntu 22.04) ─────────────────────────────────────────┐   │
│   │                                                                    │   │
│   │   Agent (Python)                                                   │   │
│   │   ├── 心跳注册 + 任务拉取 (gRPC)                                    │   │
│   │   ├── perf CPU 火焰图采集                                           │   │
│   │   ├── eBPF IO 延迟采集                                              │   │
│   │   ├── py-spy Python 采样                                            │   │
│   │   ├── sys_metrics 多维指标                                          │   │
│   │   └── memory_smaps 内存分析                                         │   │
│   │                                                                    │   │
│   │   演示负载进程 (demo/*.py)                                           │   │
│   │   ├── cpu_hotspot.py   递归/排序/JSON 热点                          │   │
│   │   └── vm_test_targets.py  15 种负载场景                             │   │
│   │                                                                    │   │
│   └────────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 前置准备（现场开始前 10 分钟完成）

### 宿主机

```bash
# 1. 确认 Docker 可用
docker --version      # ≥ 20.10
docker compose version # ≥ v2

# 2. 克隆项目
git clone https://github.com/jiangyulin1/mini-drop.git
cd mini-drop

# 3. 拉取镜像并启动（提前做，避免现场等待拉取）
docker compose pull
docker compose up -d

# 4. 验证
curl http://localhost/api/healthz
# 应返回 {"code":0,"message":"ok","data":{"service":"mini-drop-server","version":"0.1.0"}}

# 5. 在浏览器打开 http://localhost，确认 Web 界面正常
```

### 虚拟机（Ubuntu 22.04）

```bash
# 1. 克隆项目
git clone https://github.com/jiangyulin1/mini-drop.git
cd mini-drop

# 2. 一键安装依赖 + 编译 proto + 运行单元测试
bash demo/vm_deploy.sh

# 若需手动步骤：
#   sudo apt-get install -y python3 python3-pip linux-tools-$(uname -r) bpftrace curl
#   pip install -e ".[dev]"
#   cd proto && bash compile.sh

# 3. 配置 perf 权限
echo 'kernel.perf_event_paranoid=1' | sudo tee /etc/sysctl.d/99-mini-drop.conf
sudo sysctl -p /etc/sysctl.d/99-mini-drop.conf

# 4. 确认依赖可用
which perf && perf --version
which bpftrace && bpftrace --version
which py-spy && py-spy --version
```

### 确认宿主机 ↔ 虚拟机网络互通

```bash
# 在虚拟机上执行——确认能访问宿主机的 gRPC 端口
# 假设宿主机 IP 为 192.168.x.x（用实际 IP 替换）
curl http://<宿主机IP>:8191/api/healthz
# 应返回 200
```

---

## 场景一：CPU 火焰图采集（5 分钟）— 第一个演示，效果最直观

> 目标：展示核心链路——创建任务 → Agent 采集 → 火焰图展示。

### 步骤

**1. 虚拟机：启动 Agent**

```bash
cd ~/mini-drop

# Agent 指向宿主机的 Server gRPC 端口
export AGENT_GRPC_ADDR=<宿主机IP>:50051
export AGENT_ID=agent_vm_demo

python3 -m agent.mini_drop_agent.main
# 看到 "[agent] 注册成功 agent_id=agent_vm_demo" 表示连上
```

**2. 虚拟机：启动 CPU 热点演示进程**

```bash
# 新开终端
cd ~/mini-drop
python3 demo/cpu_hotspot.py
# 输出: cpu_hotspot pid=12345
# 记住这个 PID
```

**3. 宿主机：浏览器操作**

打开 `http://localhost`：

- 页面右侧"Agent 列表"应能看到 `agent_vm_demo` 状态为 **ONLINE**（绿色）
- 在 NLP 输入框输入 "CPU 飙高，帮我看看进程 xxx"（xxx 换成实际 PID）
- 点击创建任务

**或者用 API 创建：**

```bash
curl -X POST http://localhost/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"name":"demo CPU 火焰图","agent_id":"agent_vm_demo","target_pid":<PID>,"collector_type":"perf_cpu","sample_rate":99,"duration_sec":15}'
```

**4. 等待 + 展示**

- 任务面板中任务状态依次变为 `PENDING → RUNNING → UPLOADING → ANALYZING → DONE`
- 点击任务进入详情页，展示 **d3 交互式火焰图**（点击放大、搜索高亮）
- 右侧 **TopN 柱状图** 应显示 `fib_hotspot` 占比 ~68%
- 点击"触发 AI 诊断"可查看归因报告

### 演示要点

> "刚才从宿主机浏览器创建了一个 CPU 采集任务，任务通过 gRPC 下发到虚拟机上的 Agent。Agent 用 perf 采集了 15 秒，本地生成了火焰图和 TopN 热点。产物通过 MinIO 回传，现在浏览器上看到的是交互式火焰图——可以点进去放大，搜索函数名。"

---

## 场景二：eBPF IO 延迟观测（5 分钟）

> 目标：展示 eBPF 内核探针能力，采集块设备 IO 延迟分布。

### 步骤

**1. 虚拟机：制造 IO 负载**

```bash
# 新开终端，制造磁盘写入压力
dd if=/dev/zero of=/tmp/mini-drop-io-test bs=4M count=512 oflag=direct
```

**2. 创建 eBPF 采集任务**

```bash
# 趁 dd 还没跑完，快速创建任务（target_pid 填任意有效 PID 即可，ebpf_io 是系统级探针）
curl -X POST http://localhost/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"name":"demo IO 延迟","agent_id":"agent_vm_demo","target_pid":1,"collector_type":"ebpf_io","duration_sec":15}'
```

**3. 查看结果**

- 任务完成后进入详情页，查看 `ebpf_metrics` 产物
- 展示 **IO 延迟 histogram**（柱状图）：大部分请求落在 `[128, 512)` μs 区间
- 对比：没有 IO 负载时的基线延迟接近 0

### 演示要点

> "eBPF 采集器通过 bpftrace 内核探针在不修改内核、不重启服务的情况下采集了块设备的 IO 延迟分布。可以看到写入压力下大部分 IO 请求的延迟在 128 到 512 微秒之间。同样的采集器可以扩展到调度延迟、网络、文件系统。"

---

## 场景三：自然语言采集（3 分钟）

> 目标：展示 NLP 意图解析 + /proc 进程发现 + 一键创建任务。

### 步骤

**1. 浏览器操作**

在 Dashboard 页面的 NLP 输入框中输入：

```
mysqld 最近 CPU 飙高，帮我看看
```

**2. 展示解析结果**

系统弹出确认对话框，展示：
- 解析的进程名：`mysqld`
- 选择的采集器：`perf_cpu`
- 采样参数：15s / 99Hz
- 候选 PID 列表（从虚拟机的 /proc 匹配到的实际进程）

**3. 确认创建**

点击确认，任务自动创建并进入采集流程。

### 演示要点

> "输入一句自然语言描述，系统通过 AI function calling 自动判断应该用哪种采集器、采多长时间。进程名到 PID 的匹配是在 Agent 所在机器的 /proc 里做的——AI 不知道机器上有什么进程，不会编造。如果没有配置 AI API Key，会自动降级为关键词匹配。"

---

## 场景四：多维系统指标采集（5 分钟）

> 目标：展示 sys_metrics 采集器对 CPU/线程/FD/网络/磁盘的全景监控。

### 步骤

**1. 虚拟机：启动线程泄漏场景**

```bash
cd ~/mini-drop
python3 demo/vm_test_targets.py thread-spawn 20 &
# 记住 PID
```

**2. 创建 sys_metrics 任务**

```bash
curl -X POST http://localhost/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"name":"demo 系统指标","agent_id":"agent_vm_demo","target_pid":<PID>,"collector_type":"sys_metrics","duration_sec":20}'
```

**3. 查看结果**

- 产物 `sys_metrics.json` 包含时间序列：线程数趋势（increasing）、FD 数、CPU sys%、网络吞吐、上下文切换速率
- 展示线程数随时间增长的曲线

### 演示要点

> "sys_metrics 采集器一次采集就覆盖了 CPU、内存、线程、文件描述符、磁盘 IO、网络流量六个维度。这里的线程数趋势是 increasing，符合我们启动的线程泄漏场景。如果是在生产环境排查"服务越来越慢"，这些指标能直接告诉你是线程膨胀还是 FD 泄漏。"

---

## 场景五：内存泄漏检测（3 分钟）

> 目标：展示 memory_smaps 采集器对进程内存的深度分析。

### 步骤

**1. 虚拟机：启动内存泄漏场景**

```bash
cd ~/mini-drop
python3 demo/vm_test_targets.py memory-leak 20 &
# 记住 PID
```

**2. 创建 memory_smaps 任务**

```bash
curl -X POST http://localhost/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"name":"demo 内存分析","agent_id":"agent_vm_demo","target_pid":<PID>,"collector_type":"memory_smaps","duration_sec":20}'
```

**3. 查看结果**

- 产物 `memory_profile.json` 展示 RSS 随时间增长的趋势
- 每隔 2 秒一次快照，可观测内存从初始 ~10MB 增长到 ~40MB+

### 演示要点

> "memory_smaps 不是简单的 RSS 读数，它读取了 /proc/PID/smaps 的详细内存映射。可以看到进程的内存每 2 秒增长约 2MB，且 PSS（按比例分摊的共享内存）和 RSS 同步增长，确认是物理内存泄漏而非虚拟地址空间膨胀。"

---

## 场景六（可选）：AI 智能归因（3 分钟）

> 目标：展示 5 层归因引擎的完整输出——从证据到修复计划。

### 步骤

**1. 在场景一的已完成任务上触发诊断**

在任务详情页点击"触发 AI 诊断"，或通过 API：

```bash
curl -X POST http://localhost/api/tasks/<TASK_ID>/diagnose
```

**2. 展示诊断报告**

- **summary**：一句话结论（"CPU 热点集中在 fib_hotspot 递归计算…"）
- **ranked_causes**：排名列表，每条带 `confidence` 和 `evidence_refs`
- **repair_plan**：自动生成的修复建议（如"创建 py-spy 二次采集验证"）

**3. 展示反馈闭环**

```bash
# 提交反馈——"这个归因是对的"
curl -X POST http://localhost/api/diagnoses/<DIAGNOSIS_ID>/feedback \
  -H "Content-Type: application/json" \
  -d '{"predicted_cause_id":"cpu_hotspot_recursive","feedback_label":"correct"}'
```

### 演示要点

> "如果没有配置 AI API Key，归因引擎会自动降级为规则引擎模式，仍然能给出基于关键词和阈值匹配的分析结论。有了 API Key，LLM 在 few-shot 样例约束下生成结构化报告，每条结论都引用具体证据字段，可追溯。用户反馈会修正后续归因的权重——点一次'正确'，下一次同样场景的置信度会更高。"

---

## 演示收尾

### 停止虚拟机上的负载

```bash
# 杀掉所有演示进程
pkill -f cpu_hotspot
pkill -f vm_test_targets
# 停止 Agent
pkill -f agent.mini_drop_agent.main
```

### 停止宿主机 Docker

```bash
cd ~/mini-drop
docker compose down
# 如需保留数据：docker compose stop
```

---

## 环境速查

| 组件 | 位置 | 关键命令 |
|------|------|----------|
| Web 界面 | 宿主机 `http://localhost` | 浏览器打开 |
| Swagger | 宿主机 `http://localhost:8191/docs` | API 交互式调试 |
| Server 日志 | 宿主机 `docker compose logs -f server` | 排查 Server 问题 |
| Agent 日志 | 虚拟机终端 stdout | JSON 格式，关注 `event` 字段 |
| 采集产物 | 虚拟机 `/tmp/mini-drop/` | `ls -la /tmp/mini-drop/` |
| MinIO 控制台 | 宿主机 `http://localhost:9001` | 用户名 `mini_drop`，密码 `mini_drop_secret` |

---

## 异常处理

| 症状 | 可能原因 | 解决 |
|------|----------|------|
| Agent 注册后 Web 上看不到 | 虚拟机→宿主机网络不通 | `curl http://<宿主机IP>:8191/api/healthz` 确认 |
| Agent 日志报 `UNAVAILABLE` | gRPC 端口未暴露 | 检查宿主机防火墙：`50051` 端口是否开放 |
| 采集任务停在 PENDING | Agent 心跳未拉取到任务 | 确认 `AGENT_ID` 与创建任务时的 `agent_id` 一致 |
| perf 采集失败、"权限不足" | perf_event_paranoid > 1 | `sudo sysctl -w kernel.perf_event_paranoid=1` |
| bpftrace 采集失败 | 缺少内核头文件 | `sudo apt-get install -y linux-headers-$(uname -r)` |
| 火焰图为空 | perf.data 太小或符号缺失 | 延长 duration_sec 到 30s，安装 debuginfo |
| Docker 服务未启动 | Docker Desktop 未运行 | 启动 Docker Desktop 后重试 |
| 端口冲突 | 80/8191/5432 被占用 | `docker compose down && docker compose up -d` |

---

## 自定义演示脚本

如果需要一键跑全部场景，可在虚拟机上执行项目自带的 E2E 套件：

```bash
cd ~/mini-drop
export AGENT_GRPC_ADDR=<宿主机IP>:50051
export AGENT_ID=agent_vm_demo

# 快速模式（每场景 5s）
sudo python3 demo/test_runner.py --quick

# 单场景
sudo python3 demo/test_runner.py --scene cpu-fib

# 完整套件（约 15 分钟，16 个场景）
sudo python3 demo/test_runner.py
```
