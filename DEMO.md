# Mini-Drop 现场演示指南

在两台机器上搭建：宿主机跑 Docker（Web/Server/数据库），虚拟机跑 Agent 和负载。全程约 20 分钟。也可以全部放在一台机器上（本地模式）。

---

## 演示拓扑

```
宿主机 (浏览器)                        虚拟机 (Ubuntu 22.04)
┌──────────────────────────┐            ┌──────────────────────────────┐
│                          │            │                              │
│  Docker Compose          │   gRPC     │  Agent                       │
│  ├ Web (nginx :80)       │◄──────────│  ├ perf CPU 火焰图             │
│  ├ Server (:8191 :50051) │            │  ├ eBPF IO 延迟               │
│  ├ PostgreSQL            │            │  ├ py-spy Python 采样         │
│  └ MinIO                 │            │  ├ memory_smaps 内存分析      │
│                          │            │  └ sys_metrics 系统指标       │
│  浏览器打开               │            │                              │
│  http://localhost         │            │  负载进程:                    │
│                          │            │  demo/vm_test_targets.py      │
└──────────────────────────┘            └──────────────────────────────┘
```

---

## 方式 A：纯本地演示（一台机器，无需 Docker）

最简单的演示方式。Server + Agent + 负载全部在同一台机器上运行。

```bash
# 1. 克隆仓库
git clone https://github.com/jiangyulin1/mini-drop.git
cd mini-drop

# 2. 安装依赖 + 编译 proto + 设置权限（首次运行，约 3 分钟）
bash demo/vm_deploy.sh

# 3. 运行演示（6 个场景，约 15 分钟）
bash demo/demo.sh

# 或者快速过场模式（每个场景 5 秒）
DEMO_QUICK=1 bash demo/demo.sh

# 只运行特定场景
DEMO_SCENES=cpu,python bash demo/demo.sh
```

可用场景标签：`cpu` `python` `memory` `sys` `io` `lock`

---

## 方式 B：Docker + 虚拟机双机演示（效果最完整）

### B-1. 宿主机：启动 Docker 服务

```bash
git clone https://github.com/jiangyulin1/mini-drop.git
cd mini-drop
docker compose up -d

# 验证
curl http://localhost/api/healthz
# 浏览器打开 http://localhost
```

### B-2. 虚拟机：部署 Agent 环境

```bash
# 1. 克隆仓库
git clone https://github.com/jiangyulin1/mini-drop.git
cd mini-drop

# 2. 一键安装所有依赖（约 3 分钟）
bash demo/vm_deploy.sh

# 此脚本自动完成:
#   - apt-get install python3 pip linux-tools bpftrace curl
#   - pip install -e ".[dev]"
#   - 编译 gRPC proto
#   - 设置 perf_event_paranoid=1
#   - 运行单元测试确认环境就绪
```

### B-3. 确认网络互通

```bash
# 在虚拟机上执行：
curl http://<宿主机IP>:8191/api/healthz
# 应返回 {"code":0,"message":"ok","data":{"service":"mini-drop-server","version":"0.1.0"}}
```

### B-4. 虚拟机：启动 Agent 并运行演示脚本

```bash
# 启动 Agent（指向宿主机 Server）
export AGENT_GRPC_ADDR=<宿主机IP>:50051
export AGENT_ID=agent_vm_demo
python3 -m agent.mini_drop_agent.main &
# 看到 "[agent] 注册成功 agent_id=agent_vm_demo" 表示连上
# 在宿主机浏览器 http://localhost 的 Agent 列表中应能看到 agent_vm_demo (ONLINE)

# 另开终端，运行演示脚本
bash demo/demo.sh
```

---

## 方式 C：Docker Compose 全栈（单机，包含 Web 界面）

```bash
git clone https://github.com/jiangyulin1/mini-drop.git
cd mini-drop
docker compose up -d

# 一键 docker demo（调用 API 创建任务 + 轮询 + 验证产物）
make demo
# 或: bash demo/demo.sh

# 浏览器打开 http://localhost 查看火焰图
```

---

## 演示脚本输出示例

运行 `bash demo/demo.sh` 后，终端会依次展示：

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
  ▸ 生成产物：
     flamegraph_json            flamegraph.json                4096 bytes
     flamegraph_svg             flamegraph.svg                28672 bytes
     top_json                   top.json                      2048 bytes

  TopN 热点函数:
  #1   68.5%  fib_hotspot
  #2   13.4%  sort_hotspot
  #3    8.1%  json_hotspot
  #4    3.2%  [unknown]
  #5    2.1%  __libc_start_main

  火焰图 SVG: 28672 bytes
  火焰图 JSON: 4096 bytes
  ✅ 场景1: CPU 火焰图采集 — 完成
```

每个场景耗时约 20-30 秒（15 秒采集 + 轮询）。

---

## 演示脚本详解

### `demo/demo.sh` — 主演示脚本

自动完成：检查依赖 → 设置权限 → 编译 proto → 启动 Server → 启动 Agent → 依次运行 6 个场景 → 展示产物 → 清理。

环境变量控制：

```bash
DEMO_QUICK=1                          # 快速模式（每个场景 5 秒）
DEMO_SCENES=cpu,python,memory         # 只运行指定场景
DEMO_SCENES=all                       # 运行所有场景（默认）
DEMO_SKIP_INSTALL=1                   # 跳过依赖安装
```

### `demo/vm_test_targets.py` — 负载场景生成器（15 种场景）

```bash
# 用法: python3 demo/vm_test_targets.py <场景> [持续秒数]

# CPU 场景
python3 demo/vm_test_targets.py cpu-fib 30       # 递归 Fibonacci — CPU 热点
python3 demo/vm_test_targets.py cpu-loop 20      # 空转循环 — 100% 单核
python3 demo/vm_test_targets.py cpu-sort 20      # 排序热点 — sorted() 开销

# Python 场景
python3 demo/vm_test_targets.py python-cpu 15    # 纯 Python 热点 — py-spy 采样
python3 demo/vm_test_targets.py python-multi 15  # Python 多线程 — GIL 竞争

# 内存场景
python3 demo/vm_test_targets.py memory-leak 20   # 持续分配内存 — RSS 增长
python3 demo/vm_test_targets.py memory-stable 15 # 稳定大内存 — RSS 恒定

# IO 场景
python3 demo/vm_test_targets.py io-write 15      # Python 顺序写入
python3 demo/vm_test_targets.py io-dd 15         # dd 块设备 IO

# FD / 线程 / 锁 / 网络场景
python3 demo/vm_test_targets.py fd-leak 15       # 持续打开 FD — FD 增长
python3 demo/vm_test_targets.py thread-spawn 15  # 持续创建线程 — 线程增长
python3 demo/vm_test_targets.py lock-contend 12  # 32 线程竞争锁 — 上下文切换高
python3 demo/vm_test_targets.py network-http 12  # HTTP 服务 — 网络流量
```

### `demo/test_runner.py` — 自动化 E2E 测试套件（16 个场景，含报告）

```bash
sudo python3 demo/test_runner.py                 # 全部 16 个场景
sudo python3 demo/test_runner.py --quick         # 快速模式
sudo python3 demo/test_runner.py --scene cpu-fib # 单场景
```

### `demo/vm_deploy.sh` — 一键环境部署

```bash
bash demo/vm_deploy.sh           # 完整安装 + 单元测试
bash demo/vm_deploy.sh unit      # 仅安装 + 单元测试
bash demo/vm_deploy.sh e2e       # 安装 + 端到端测试
```

### `demo/cpu_hotspot.py` — 简单热点进程（无需参数，持续运行）

```bash
python3 demo/cpu_hotspot.py &
# 输出: cpu_hotspot pid=12345
# 每 60 秒切换负载: fib → sort → json 编解码
```

---

## 手动采集示例（逐条命令，适合逐行讲解）

```bash
# 1. 启动一个负载进程
python3 demo/vm_test_targets.py cpu-fib 30 &
TARGET=$!
echo "目标 PID: $TARGET"

# 2. 创建 perf CPU 采集任务
curl -X POST http://localhost:8191/api/tasks \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"手动演示\",\"agent_id\":\"demo_agent\",\"target_pid\":$TARGET,\"collector_type\":\"perf_cpu\",\"sample_rate\":99,\"duration_sec\":15}"

# 3. 复制输出的 task_id，轮询状态
curl -s http://localhost:8191/api/tasks/<TASK_ID> | python3 -m json.tool

# 4. 查看产物
curl -s http://localhost:8191/api/tasks/<TASK_ID>/artifacts | python3 -m json.tool

# 5. 查看 TopN 热点内容
curl -s http://localhost:8191/api/tasks/<TASK_ID>/artifacts/top_json/content | python3 -m json.tool

# 6. 查看火焰图 SVG（在浏览器中打开）
curl -s http://localhost:8191/api/tasks/<TASK_ID>/artifacts/flamegraph_svg/content > /tmp/flamegraph.svg
firefox /tmp/flamegraph.svg

# 7. 触发 AI 诊断
curl -X POST http://localhost:8191/api/tasks/<TASK_ID>/diagnose

# 8. CLI 方式
micro-drop parse "CPU 飙高"
micro-drop summarize --top-json /tmp/mini-drop/<TASK_ID>/top.json
micro-drop diff-top --base /tmp/before.json --head /tmp/after.json --threshold 5
```

---

## 异常处理

| 症状 | 原因 | 解决 |
|------|------|------|
| `perf 命令不可用` | 未安装 linux-tools | `sudo apt-get install -y linux-tools-generic` |
| `perf_event_paranoid 权限不足` | paranoid ≥ 2 | `sudo sysctl -w kernel.perf_event_paranoid=1` |
| `bpftrace 命令不可用` | 未安装 | `sudo apt-get install -y bpftrace` |
| `py-spy 命令不可用` | 未安装 | `pip install py-spy` |
| 任务停在 PENDING | Agent 心跳未拉取 | 确认 `AGENT_ID` 与创建任务时一致 |
| 火焰图为空 | 采样时间太短/符号缺失 | 延长 duration_sec 到 30s |
| Server 端口冲突 | 8191 或 50051 被占用 | `fuser -k 8191/tcp; fuser -k 50051/tcp` |
| Agent 注册不上 | gRPC 连不上 | 检查防火墙、确认 `AGENT_GRPC_ADDR` 正确 |

---

## 演示后产物位置

```
/tmp/mini-drop/
├── task_20240617_143022_a1b2c3/
│   ├── perf.data           # perf 原始采样数据
│   ├── flamegraph.json     # d3-flame-graph JSON 树
│   ├── flamegraph.svg      # 火焰图 SVG（浏览器可直接打开）
│   ├── top.json            # TopN 热点函数
│   ├── suggestions.md      # 规则引擎建议
│   └── perf.script.txt     # perf script 原始输出
├── task_20240617_143145_d4e5f6/
│   ├── ebpf_metrics.json   # IO 延迟 histogram
│   └── io_latency.txt      # bpftrace 原始输出
├── task_20240617_143310_g7h8i9/
│   ├── sys_metrics.json    # CPU/线程/FD/网络/IO 时序
│   └── ...
└── task_20240617_143505_j0k1l2/
    ├── memory_profile.json # RSS 趋势 + 内存映射
    └── ...
```
