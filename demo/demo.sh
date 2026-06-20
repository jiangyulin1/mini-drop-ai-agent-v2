#!/bin/bash
# Mini-Drop 现场演示脚本
#
# 在新机器上 clone 仓库后，只需要运行这一条命令：
#   bash demo/demo.sh
#
# 脚本自动完成：
#   1. 检查依赖 (perf / bpftrace / py-spy / python)
#   2. 设置 perf_event_paranoid（如需要）
#   3. 启动 Server + Agent（本地模式）
#   4. 依次运行 6 个演示场景
#   5. 每个场景展示核心产物
#   6. 清理并输出汇总
#
# 环境变量（可选）：
#   DEMO_SCENES      要运行的场景，逗号分隔。默认 all
#   DEMO_QUICK       设为 1 则每个场景只采 5 秒（快速过场）
#   DEMO_SKIP_INSTALL 设为 1 则跳过依赖安装步骤
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# ── 颜色 ──────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

banner()  { echo -e "\n${BOLD}${BLUE}═══ $1 ═══${NC}\n"; }
step()    { echo -e "${CYAN}  ▸ $1${NC}"; }
ok()      { echo -e "  ${GREEN}✅ $1${NC}"; }
warn()    { echo -e "  ${YELLOW}⚠️  $1${NC}"; }
fail()    { echo -e "  ${RED}❌ $1${NC}"; }
info()    { echo -e "     $1"; }

API_BASE="${MINI_DROP_API_URL:-http://localhost:8191}"
AGENT_ID="${AGENT_ID:-demo_agent}"
GRPC_PORT=50051
ARTIFACT_ROOT="${MINI_DROP_ARTIFACT_ROOT:-/tmp/mini-drop}"
DEMO_RESULTS=""

# ── 清理 ──────────────────────────────────────────────────
cleanup() {
    echo ""
    step "清理演示进程…"
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
    kill "$AGENT_PID" 2>/dev/null || true
    wait "$AGENT_PID" 2>/dev/null || true
    pkill -f "cpu_hotspot" 2>/dev/null || true
    pkill -f "vm_test_targets" 2>/dev/null || true
    fuser -k "$GRPC_PORT/tcp" 2>/dev/null || true
    fuser -k "8191/tcp" 2>/dev/null || true
    echo ""
}

trap cleanup EXIT

# ── 步骤 0: 检查环境 ──────────────────────────────────────
banner "Mini-Drop 现场演示"

echo "项目目录: $PROJECT_DIR"
echo "产物目录: $ARTIFACT_ROOT"
echo ""

step "检查系统依赖…"
MISSING=""

if ! command -v python3 &>/dev/null; then
    fail "python3 未安装"
    MISSING=1
else
    info "python3: $(python3 --version)"
fi

if command -v perf &>/dev/null; then
    info "perf:    $(perf --version 2>&1 | head -1)"
else
    warn "perf 未安装 — CPU 火焰图场景将无法运行"
    MISSING=1
fi

if command -v bpftrace &>/dev/null; then
    info "bpftrace: $(bpftrace --version 2>&1)"
else
    warn "bpftrace 未安装 — eBPF IO 场景将无法运行"
fi

if command -v py-spy &>/dev/null; then
    info "py-spy:  $(py-spy --version 2>&1)"
else
    warn "py-spy 未安装 — Python 采样场景将无法运行"
fi

if [ -n "$MISSING" ] && [ "${DEMO_SKIP_INSTALL:-0}" != "1" ]; then
    echo ""
    step "尝试安装缺失依赖…"
    sudo apt-get update -qq 2>/dev/null || true
    sudo apt-get install -y -qq \
        python3 python3-pip \
        linux-tools-common linux-tools-generic \
        bpftrace \
        2>/dev/null || true
    pip install -e ".[dev]" -q 2>/dev/null || true
    echo ""
fi

# 编译 proto（幂等）
if [ ! -f "$PROJECT_DIR/server/app/generated/common_pb2.py" ]; then
    step "编译 gRPC proto…"
    cd "$PROJECT_DIR/proto" && bash compile.sh 2>/dev/null || true
    cd "$PROJECT_DIR"
fi

# 设置 perf 权限
PARANOID=$(cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null || echo "3")
if [ "$PARANOID" -gt 1 ] 2>/dev/null; then
    step "降低 perf_event_paranoid（当前 $PARANOID → 1）…"
    sudo sysctl -w kernel.perf_event_paranoid=1 2>/dev/null || \
        warn "无法修改 perf_event_paranoid（perf CPU 场景需要 root 运行）"
fi
echo ""

# ── 步骤 1: 启动 Server ───────────────────────────────────
banner "1. 启动 Server"

export MINI_DROP_API_AUTH_ENABLED=0
export MINI_DROP_GRPC_AUTH_ENABLED=0
export MINIO_AUTO_CREATE_BUCKET=0
export MINI_DROP_ARTIFACT_ROOT="$ARTIFACT_ROOT"

python3 -m server.app.main &
SERVER_PID=$!

step "等待 Server 就绪…"
for i in $(seq 1 30); do
    if curl -s "http://localhost:8191/api/healthz" > /dev/null 2>&1; then
        ok "Server 已启动 (PID=$SERVER_PID, :8191 + :$GRPC_PORT)"
        break
    fi
    sleep 1
done
if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    fail "Server 启动失败"
    exit 1
fi

# ── 步骤 2: 启动 Agent ────────────────────────────────────
banner "2. 启动 Agent"

export AGENT_ID="$AGENT_ID"
export AGENT_GRPC_ADDR="localhost:$GRPC_PORT"
export AGENT_UPLOAD_ARTIFACTS=0

python3 -m agent.mini_drop_agent.main &
AGENT_PID=$!

step "等待 Agent 注册…"
for i in $(seq 1 15); do
    AGENTS=$(curl -s "$API_BASE/api/agents" 2>/dev/null || echo "")
    if echo "$AGENTS" | python3 -c "import sys,json; d=json.load(sys.stdin)['data']; print(any(a['status']=='ONLINE' for a in d))" 2>/dev/null | grep -q "True"; then
        ok "Agent 已注册 (PID=$AGENT_PID, agent_id=$AGENT_ID)"
        break
    fi
    sleep 1
done

# ── API 工具函数 ──────────────────────────────────────────
api() {
    curl -s -X POST "$API_BASE/api/tasks" \
        -H "Content-Type: application/json" \
        -d "$1"
}

poll() {
    local task_id="$1" max_wait="${2:-120}"
    for i in $(seq 1 "$max_wait"); do
        local status
        status=$(curl -s "$API_BASE/api/tasks/$task_id" | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['status'])" 2>/dev/null || echo "?")
        if [ "$status" = "DONE" ] || [ "$status" = "FAILED" ]; then
            echo "$status"
            return
        fi
        sleep 1
    done
    echo "TIMEOUT"
}

get_artifacts() {
    local task_id="$1"
    curl -s "$API_BASE/api/tasks/$task_id/artifacts" | python3 -c "
import sys, json
data = json.load(sys.stdin)['data']
items = data if isinstance(data, list) else data.get('items', [])
for a in items:
    print(f\"  {a['artifact_type']:28s} {a.get('filename',''):32s} {a.get('size_bytes',0)} bytes\")
" 2>/dev/null || echo "  (无法获取产物列表)"
}

# ── 场景定义 ──────────────────────────────────────────────

run_scene() {
    local scene_name="$1"
    local scene_id="$2"
    local collector="$3"
    local duration="$4"
    local description="$5"

    echo ""
    banner "$scene_name"
    echo "  采集器: $collector"
    echo "  时长:   ${duration}s"
    echo "  场景:   $description"
    echo ""

    # 1. 启动负载进程
    step "启动负载进程…"
    python3 demo/vm_test_targets.py "$scene_id" $((duration + 10)) &
    local target=$!
    sleep 2
    if ! kill -0 "$target" 2>/dev/null; then
        fail "负载进程启动失败"
        return 1
    fi
    info "PID=$target"

    # 2. 创建采集任务
    step "创建采集任务…"
    local resp task_id
    resp=$(api "{\"name\":\"demo-$scene_id\",\"agent_id\":\"$AGENT_ID\",\"target_pid\":$target,\"collector_type\":\"$collector\",\"sample_rate\":99,\"duration_sec\":$duration}")
    task_id=$(echo "$resp" | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['task_id'])" 2>/dev/null || echo "")
    if [ -z "$task_id" ]; then
        fail "任务创建失败: $resp"
        kill "$target" 2>/dev/null || true
        return 1
    fi
    info "任务ID: $task_id"

    # 3. 轮询状态
    step "等待采集完成…"
    local status
    status=$(poll "$task_id" $((duration + 60)))
    info "状态: $status"

    # 4. 查看产物
    step "生成产物："
    get_artifacts "$task_id"

    # 5. 展示关键数据
    local task_dir="$ARTIFACT_ROOT/$task_id"
    case "$collector" in
        perf_cpu|continuous_perf)
            if [ -f "$task_dir/top.json" ]; then
                echo ""
                info "TopN 热点函数:"
                python3 -c "
import json
d=json.load(open('$task_dir/top.json'))
for i, item in enumerate(d[:5]):
    print(f'  #{i+1}  {item[\"percent\"]:5.1f}%  {item[\"name\"][:70]}')
"
            fi
            if [ -f "$task_dir/flamegraph.svg" ]; then
                info "火焰图 SVG: $(stat -c%s "$task_dir/flamegraph.svg" 2>/dev/null || wc -c < "$task_dir/flamegraph.svg") bytes"
            fi
            if [ -f "$task_dir/flamegraph.json" ]; then
                info "火焰图 JSON: $(stat -c%s "$task_dir/flamegraph.json" 2>/dev/null || wc -c < "$task_dir/flamegraph.json") bytes"
            fi
            ;;
        pyspy)
            if [ -f "$task_dir/flamegraph.svg" ]; then
                info "py-spy 火焰图 SVG: $(stat -c%s "$task_dir/flamegraph.svg" 2>/dev/null || wc -c < "$task_dir/flamegraph.svg") bytes"
            fi
            ;;
        ebpf_io)
            if [ -f "$task_dir/ebpf_metrics.json" ]; then
                echo ""
                info "IO 延迟分布 (histogram):"
                python3 -c "
import json
d=json.load(open('$task_dir/ebpf_metrics.json'))
h=d.get('io_latency_us',{})
total=sum(h.values())
for k in sorted(h.keys(), key=lambda x: int(x.split(',')[0].strip('[')) if x.split(',')[0].strip('[').replace('K','000').replace('M','000000').isdigit() else 0):
    v=h[k]
    bar='█'*min(50, int(v/max(1,total)*50))
    print(f'  {k:20s} {v:5d} ({v/max(1,total)*100:4.1f}%) {bar}')
"
            fi
            ;;
        memory_smaps)
            if [ -f "$task_dir/memory_profile.json" ]; then
                echo ""
                info "内存分析:"
                python3 -c "
import json
d=json.load(open('$task_dir/memory_profile.json'))
print(f'  初始 RSS: {d.get(\"first_rss_mb\",0)} MB')
print(f'  最终 RSS: {d.get(\"last_rss_mb\",0)} MB')
print(f'  趋势:     {d.get(\"trend\",\"?\")}')
print(f'  采样数:   {d.get(\"sample_count\",0)}')
"
            fi
            ;;
        sys_metrics)
            if [ -f "$task_dir/sys_metrics.json" ]; then
                echo ""
                info "系统指标摘要:"
                python3 -c "
import json
d=json.load(open('$task_dir/sys_metrics.json'))
s=d.get('summary',{})
print(f'  线程数:     {s.get(\"thread_count\",0)} (趋势: {s.get(\"thread_trend\",\"?\")})')
print(f'  文件描述符: {s.get(\"fd_count\",0)} (趋势: {s.get(\"fd_trend\",\"?\")})')
print(f'  CPU sys%:   {s.get(\"avg_cpu_sys_pct\",0)}%')
print(f'  CPU user%:  {s.get(\"avg_cpu_user_pct\",0)}%')
print(f'  IO wait%:   {s.get(\"avg_cpu_iowait_pct\",0)}%')
print(f'  上下文切换: {s.get(\"ctx_nonvoluntary_rate\",0)}/s')
print(f'  网络 RX:    {s.get(\"net_rx_kbps\",0)} KB/s')
print(f'  网络 TX:    {s.get(\"net_tx_kbps\",0)} KB/s')
print(f'  当前 RSS:   {s.get(\"vmrss_mb\",0)} MB')
"
            fi
            ;;
    esac

    # 6. 清理本场景的负载进程
    kill "$target" 2>/dev/null || true
    wait "$target" 2>/dev/null || true

    ok "$scene_name — 完成"
    DEMO_RESULTS="$DEMO_RESULTS  ✅ $scene_name ($collector)\n"
}

# ── 确定要运行的场景 ──────────────────────────────────────
if [ "${DEMO_QUICK:-0}" = "1" ]; then
    DUR=5
    QUICK_MODE="(快速模式: ${DUR}s/场景)"
else
    DUR=15
    QUICK_MODE=""
fi

SCENES="${DEMO_SCENES:-all}"

# ── 执行 ──────────────────────────────────────────────────
banner "3. 开始演示场景 $QUICK_MODE"

# 场景 1: CPU 火焰图 — 最直观、放第一个
if [ "$SCENES" = "all" ] || echo "$SCENES" | grep -q "cpu"; then
    run_scene \
        "场景1: CPU 火焰图采集" \
        "cpu-fib" \
        "perf_cpu" \
        "$DUR" \
        "递归 Fibonacci 造成 CPU 热点 → d3 交互式火焰图 + TopN"
fi

# 场景 2: Python 采样
if [ "$SCENES" = "all" ] || echo "$SCENES" | grep -q "python"; then
    run_scene \
        "场景2: Python 用户态火焰图" \
        "python-cpu" \
        "pyspy" \
        "$DUR" \
        "py-spy 对 Python 进程采样 → --native 混合栈火焰图"
fi

# 场景 3: 内存分析
if [ "$SCENES" = "all" ] || echo "$SCENES" | grep -q "memory"; then
    run_scene \
        "场景3: 内存泄漏检测" \
        "memory-leak" \
        "memory_smaps" \
        "$DUR" \
        "持续分配内存不释放 → /proc/PID/smaps 采样 → RSS 增长趋势"
fi

# 场景 4: 系统多维指标
if [ "$SCENES" = "all" ] || echo "$SCENES" | grep -q "sys"; then
    run_scene \
        "场景4: 系统多维指标采集" \
        "thread-spawn" \
        "sys_metrics" \
        "$DUR" \
        "持续创建线程 → CPU/线程/FD/网络/IO 六维指标时序"
fi

# 场景 5: eBPF IO 延迟
if [ "$SCENES" = "all" ] || echo "$SCENES" | grep -q "io"; then
    run_scene \
        "场景5: eBPF IO 延迟观测" \
        "io-write" \
        "ebpf_io" \
        "$DUR" \
        "bpftrace 内核探针 → 块设备 IO 延迟 histogram"
fi

# 场景 6: 锁竞争 (上下文切换)
if [ "$SCENES" = "all" ] || echo "$SCENES" | grep -q "lock"; then
    run_scene \
        "场景6: 锁竞争与上下文切换" \
        "lock-contend" \
        "sys_metrics" \
        "$DUR" \
        "32 线程竞争同一把锁 → 上下文切换速率飙升"
fi

# ── 汇总 ──────────────────────────────────────────────────
echo ""
banner "演示完成"
echo "  产物目录: $ARTIFACT_ROOT/"
ls -la "$ARTIFACT_ROOT/" 2>/dev/null | head -20 || echo "  (空)"

echo ""
echo "  查看具体产物:"
echo "    ls -la $ARTIFACT_ROOT/<task_id>/"
echo "    cat $ARTIFACT_ROOT/<task_id>/top.json        # TopN 热点"
echo "    cat $ARTIFACT_ROOT/<task_id>/ebpf_metrics.json  # eBPF IO 分布"
echo "    cat $ARTIFACT_ROOT/<task_id>/sys_metrics.json   # 系统指标"
echo "    cat $ARTIFACT_ROOT/<task_id>/memory_profile.json # 内存分析"
echo ""
echo "  火焰图 SVG 可在浏览器直接打开:"
echo "    firefox $ARTIFACT_ROOT/<task_id>/flamegraph.svg"
echo ""
echo "  使用 CLI 工具查看 AI 诊断:"
echo "    micro-drop summarize --top-json $ARTIFACT_ROOT/<task_id>/top.json"
echo "    micro-drop diagnose-local --evidence $ARTIFACT_ROOT/<task_id>/evidence.json"
echo ""
echo "══════════════════════════════════════════════════════════"
echo "  演示脚本来源: demo/demo.sh"
echo "  负载场景来源: demo/vm_test_targets.py (15 种场景)"
echo "  完整 E2E:     sudo python3 demo/test_runner.py"
echo "══════════════════════════════════════════════════════════"
