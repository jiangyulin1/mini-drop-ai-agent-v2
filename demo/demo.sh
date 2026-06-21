#!/bin/bash
# Mini-Drop 现场演示脚本
#
# 兼容两种运行模式：
#   模式 A：Docker 模式（推荐）
#     docker compose up -d
#     bash demo/demo.sh
#     → 自动检测 Server 已运行，不重复启动
#
#   模式 B：本地模式（无 Docker）
#     bash demo/demo.sh
#     → 自动启动 Server + Agent，跑完清理
#
# 环境变量：
#   DEMO_SCENES      要运行的场景，逗号分隔。默认 all
#   DEMO_QUICK       设为 1 则每个场景采 5 秒
#   DEMO_SKIP_INSTALL 设为 1 则跳过依赖安装
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

# ── 颜色 ──────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

banner() { echo -e "\n${BOLD}${BLUE}═══ $1 ═══${NC}\n"; }
step()   { echo -e "${CYAN}  ▸ $1${NC}"; }
ok()     { echo -e "  ${GREEN}✅ $1${NC}"; }
warn()   { echo -e "  ${YELLOW}⚠️  $1${NC}"; }
fail()   { echo -e "  ${RED}❌ $1${NC}"; }
info()   { echo -e "     $1"; }

API_BASE="${API_BASE:-http://localhost:8191}"

# ── 分离部署模式 ─────────────────────────────────────────────
# API 在 Windows 宿主机、Agent 在 Linux VM
# 用法: SPLIT_HOST=172.17.144.1 bash demo/demo.sh
if [ -n "${SPLIT_HOST:-}" ]; then
    API_BASE="http://${SPLIT_HOST}:8191"
    info "分离部署模式 → API_BASE=$API_BASE"
fi

ARTIFACT_ROOT="${MINI_DROP_ARTIFACT_ROOT:-/tmp/mini-drop}"

# ── 检测运行模式 ──────────────────────────────────────────
DOCKER_MODE=0
if curl -sf "$API_BASE/api/healthz" > /dev/null 2>&1; then
    DOCKER_MODE=1
    step "检测到 Server 已运行（端口 :8191 有响应）→ Docker 模式"
    # 自动检测在线 Agent ID（兼容 docker-compose 默认 agent_docker_demo 和自定义 .env）
    AGENT_ID=$(curl -sf "$API_BASE/api/agents" 2>/dev/null | \
        python3 -c "import sys,json;d=json.load(sys.stdin)['data'];print(next((a['id'] for a in d if a['status']=='ONLINE'), ''))" 2>/dev/null || echo "")
    if [ -n "$AGENT_ID" ]; then
        ok "Agent 在线: $AGENT_ID"
    else
        warn "无在线 Agent（Docker 模式需要 Agent 容器已启动并成功注册）"
    fi
else
    AGENT_ID="${AGENT_ID:-demo_agent}"
fi

# ── 清理 ──────────────────────────────────────────────────
cleanup() {
    echo ""
    if [ "$DOCKER_MODE" != "1" ]; then
        step "清理本地 Server/Agent 进程…"
        kill "$SERVER_PID" 2>/dev/null || true
        kill "$AGENT_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
        wait "$AGENT_PID" 2>/dev/null || true
    fi
    pkill -f "cpu_hotspot" 2>/dev/null || true
    pkill -f "vm_test_targets" 2>/dev/null || true
}
trap cleanup EXIT

# ── 步骤 0: 环境检查 ──────────────────────────────────────
banner "Mini-Drop 现场演示"

echo "运行模式: $([ "$DOCKER_MODE" = "1" ] && echo "Docker" || echo "本地")"
echo "项目目录: $PROJECT_DIR"
echo "产物目录: $ARTIFACT_ROOT"
echo ""

if [ "$DOCKER_MODE" != "1" ]; then
    step "检查系统依赖…"
    if ! command -v python3 &>/dev/null; then fail "python3 未安装"; exit 1; fi
    info "python3: $(python3 --version)"
    if command -v perf &>/dev/null; then info "perf: $(perf --version 2>&1 | head -1)"
    else warn "perf 未安装（CPU 火焰图场景将跳过）"; fi
    if command -v bpftrace &>/dev/null; then info "bpftrace: $(bpftrace --version 2>&1)"
    else warn "bpftrace 未安装（eBPF IO 场景将跳过）"; fi
    if command -v py-spy &>/dev/null; then info "py-spy: $(py-spy --version 2>&1)"
    else warn "py-spy 未安装（Python 采样场景将跳过）"; fi

    # 编译 proto
    if [ ! -f "server/app/generated/common_pb2.py" ]; then
        step "编译 gRPC proto…"
        cd proto && bash compile.sh 2>/dev/null || true && cd "$PROJECT_DIR"
    fi

    # 设置 perf 权限
    PARANOID=$(cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null || echo "3")
    if [ "$PARANOID" -gt 1 ] 2>/dev/null; then
        step "设置 perf_event_paranoid（$PARANOID → 1）…"
        sudo sysctl -w kernel.perf_event_paranoid=1 2>/dev/null || \
            warn "无法修改 perf_event_paranoid（perf 场景需要 root 运行）"
    fi
    echo ""

    # ── 启动 Server ─────────────────────────────────────────
    banner "1. 启动 Server"
    export MINI_DROP_API_AUTH_ENABLED=0
    export MINI_DROP_GRPC_AUTH_ENABLED=0
    export MINIO_AUTO_CREATE_BUCKET=0
    export MINI_DROP_ARTIFACT_ROOT="$ARTIFACT_ROOT"
    python3 -m server.app.main &
    SERVER_PID=$!
    step "等待 Server 就绪…"
    for i in $(seq 1 30); do
        if curl -sf "http://localhost:8191/api/healthz" > /dev/null 2>&1; then
            ok "Server 已启动 (PID=$SERVER_PID)"
            break
        fi
        sleep 1
    done
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then fail "Server 启动失败"; exit 1; fi

    # ── 启动 Agent ──────────────────────────────────────────
    banner "2. 启动 Agent"
    export AGENT_ID="$AGENT_ID"
    export AGENT_GRPC_ADDR="localhost:50051"
    export AGENT_UPLOAD_ARTIFACTS=0
    python3 -m agent.mini_drop_agent.main &
    AGENT_PID=$!
    step "等待 Agent 注册…"
    for i in $(seq 1 15); do
        AGENTS=$(curl -sf "$API_BASE/api/agents" 2>/dev/null || echo "")
        if echo "$AGENTS" | python3 -c "import sys,json;d=json.load(sys.stdin)['data'];exit(0 if any(a['status']=='ONLINE' for a in d) else 1)" 2>/dev/null; then
            ok "Agent 已注册 (PID=$AGENT_PID)"
            break
        fi
        sleep 1
    done
fi

# ── API 工具 ──────────────────────────────────────────────
api()    { curl -sf -X POST "$API_BASE/api/tasks" -H "Content-Type: application/json" -d "$1"; }
api_get(){ curl -sf "$API_BASE$1"; }

poll() {
    local task_id="$1" max_wait="${2:-120}"
    for i in $(seq 1 "$max_wait"); do
        local s
        s=$(api_get "/api/tasks/$task_id" | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['status'])" 2>/dev/null || echo "?")
        echo "$s"
        [ "$s" = "DONE" ] || [ "$s" = "FAILED" ] && return
        sleep 1
    done
    echo "TIMEOUT"
}

# ── 场景执行 ──────────────────────────────────────────────
run_scene() {
    local scene_name="$1" scene_id="$2" collector="$3" duration="$4" description="$5"

    # 检查采集器是否在 Agent 能力列表中（仅 Docker 模式）
    if [ "$DOCKER_MODE" = "1" ]; then
        local capabilities
        capabilities=$(api_get "/api/agents" | python3 -c "
import sys,json
for a in json.load(sys.stdin)['data']:
    print(','.join(a.get('capabilities',[])))
" 2>/dev/null || echo "")
        if ! echo "$capabilities" | grep -q "$collector"; then
            warn "Agent 能力列表不含 '$collector'，跳过"
            return
        fi
    fi

    echo ""
    banner "$scene_name"
    echo "  采集器: $collector  |  时长: ${duration}s  |  $description"

    # 1. 启动负载
    step "启动负载进程…"
    python3 demo/vm_test_targets.py "$scene_id" $((duration + 10)) &
    local target=$!; sleep 2
    if ! kill -0 "$target" 2>/dev/null; then fail "负载启动失败"; return 1; fi
    info "PID=$target"

    # 2. 创建任务
    step "创建采集任务…"
    local resp task_id
    resp=$(api "{\"name\":\"demo-$scene_id\",\"agent_id\":\"$AGENT_ID\",\"target_pid\":$target,\"collector_type\":\"$collector\",\"sample_rate\":99,\"duration_sec\":$duration}")
    task_id=$(echo "$resp" | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['task_id'])" 2>/dev/null || echo "")
    if [ -z "$task_id" ]; then fail "任务创建失败: ${resp:0:100}"; kill "$target" 2>/dev/null; return 1; fi
    info "任务ID: $task_id"

    # 3. 轮询
    step "等待采集完成…"
    local status
    status=$(poll "$task_id" $((duration + 60)) | tail -1)
    info "状态: $status"

    # 4. 查看产物
    step "产物列表："
    api_get "/api/tasks/$task_id/artifacts" | python3 -c "
import sys,json
items=json.load(sys.stdin).get('data',[])
if isinstance(items,dict): items=items.get('items',[])
for a in items:
    print(f\"  {a.get('artifact_type',''):28s} {a.get('filename',''):32s} {a.get('size_bytes',0)} bytes\")
" 2>/dev/null || echo "  (无产物)"

    # 5. 展示关键数据
    local task_dir="$ARTIFACT_ROOT/$task_id"
    case "$collector" in
        perf_cpu|continuous_perf)
            if [ -f "$task_dir/top.json" ]; then
                echo ""; info "TopN 热点函数:"
                python3 -c "import json;d=json.load(open('$task_dir/top.json'));[print(f'  #{i+1}  {item[\"percent\"]:5.1f}%  {item[\"name\"][:70]}') for i,item in enumerate(d[:5])]"
            fi ;;
        ebpf_io)
            if [ -f "$task_dir/ebpf_metrics.json" ]; then
                echo ""; info "IO 延迟分布 (histogram):"
                python3 -c "import json;d=json.load(open('$task_dir/ebpf_metrics.json'));h=d.get('io_latency_us',{});total=sum(h.values()) or 1;[print(f'  {k:20s} {v:5d} ({v/total*100:4.1f}%)') for k,v in sorted(h.items(),key=lambda x:int(x[0].split(',')[0].strip('[')))]" 2>/dev/null
            fi ;;
        memory_smaps)
            if [ -f "$task_dir/memory_profile.json" ]; then
                python3 -c "import json;d=json.load(open('$task_dir/memory_profile.json'));print(f'\n  初始 RSS: {d.get(\"first_rss_mb\",0)} MB → 最终: {d.get(\"last_rss_mb\",0)} MB  趋势: {d.get(\"trend\",\"?\")}  采样: {d.get(\"sample_count\",0)}')" 2>/dev/null
            fi ;;
        sys_metrics)
            if [ -f "$task_dir/sys_metrics.json" ]; then
                python3 -c "import json;d=json.load(open('$task_dir/sys_metrics.json'));s=d.get('summary',{});print(f'\n  线程数: {s.get(\"thread_count\",0)} ({s.get(\"thread_trend\",\"?\")})  FD: {s.get(\"fd_count\",0)}  CPU sys%: {s.get(\"avg_cpu_sys_pct\",0)}%  IO wait%: {s.get(\"avg_cpu_iowait_pct\",0)}%  ctx: {s.get(\"ctx_nonvoluntary_rate\",0)}/s')" 2>/dev/null
            fi ;;
    esac

    kill "$target" 2>/dev/null; wait "$target" 2>/dev/null || true
    ok "$scene_name — 完成"
}

# ── 选择场景 ──────────────────────────────────────────────
DUR=$([ "${DEMO_QUICK:-0}" = "1" ] && echo 5 || echo 15)
SCENES="${DEMO_SCENES:-all}"

banner "3. 演示场景"

# 场景 1: CPU 火焰图
if [ "$SCENES" = "all" ] || echo "$SCENES" | grep -q "cpu"; then
    run_scene "场景1: CPU 火焰图" "cpu-fib" "perf_cpu" "$DUR" "递归 Fibonacci → 火焰图 + TopN"
fi

# 场景 2: Python 采样
if [ "$SCENES" = "all" ] || echo "$SCENES" | grep -q "python"; then
    run_scene "场景2: Python 火焰图" "python-cpu" "pyspy" "$DUR" "py-spy → 混合栈火焰图"
fi

# 场景 3: 内存分析
if [ "$SCENES" = "all" ] || echo "$SCENES" | grep -q "memory"; then
    run_scene "场景3: 内存泄漏" "memory-leak" "memory_smaps" "$DUR" "RSS 增长趋势"
fi

# 场景 4: 系统多维指标
if [ "$SCENES" = "all" ] || echo "$SCENES" | grep -q "sys"; then
    run_scene "场景4: 系统指标" "thread-spawn" "sys_metrics" "$DUR" "线程/CPU/FD/网络/IO"
fi

# 场景 5: eBPF IO 延迟
if [ "$SCENES" = "all" ] || echo "$SCENES" | grep -q "io"; then
    run_scene "场景5: eBPF IO" "io-write" "ebpf_io" "$DUR" "bpftrace → IO 延迟分布"
fi

# 场景 6: 锁竞争
if [ "$SCENES" = "all" ] || echo "$SCENES" | grep -q "lock"; then
    run_scene "场景6: 锁竞争" "lock-contend" "sys_metrics" "$DUR" "32 线程竞争 → 上下文切换飙升"
fi

# ── 汇总 ──────────────────────────────────────────────────
echo ""
banner "演示完成"
if [ "$DOCKER_MODE" = "1" ]; then
    echo "  浏览器打开 http://localhost 查看火焰图"
else
    info "产物目录: $ARTIFACT_ROOT/"
    echo ""
    echo "  CLI 工具:"
    echo "    micro-drop summarize --top-json $ARTIFACT_ROOT/<task_id>/top.json"
    echo "    micro-drop diff-top --base before.json --head after.json --threshold 5"
fi
