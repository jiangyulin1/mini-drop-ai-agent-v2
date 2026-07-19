#!/bin/bash
# Mini-Drop VM 部署 & 测试脚本
# 在 Ubuntu VM 上执行: bash demo/vm_deploy.sh
set -euo pipefail

echo "═══════════════════════════════════════════"
echo "  Mini-Drop VM Deployment & Test Suite"
echo "═══════════════════════════════════════════"

# ── 1. 安装系统依赖 ──────────────────────────────────
echo ""
echo "[1/5] Installing system dependencies…"
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3 python3-pip python3-venv \
    linux-tools-common linux-tools-$(uname -r) linux-cloud-tools-$(uname -r) \
    bpftrace curl perl 2>/dev/null || true

# 检查 perf
if ! command -v perf &>/dev/null; then
    echo "WARN: perf not found, trying linux-tools-generic…"
    sudo apt-get install -y -qq linux-tools-generic 2>/dev/null || true
fi

echo "perf:   $(command -v perf || echo 'NOT FOUND')"
echo "bpftrace: $(command -v bpftrace || echo 'NOT FOUND')"

# ── 2. 设置 Python 环境 ────────────────────────────────
echo ""
echo "[2/5] Setting up Python environment…"
python3 -m venv .venv 2>/dev/null || python3 -m virtualenv .venv 2>/dev/null || true
source .venv/bin/activate 2>/dev/null || true
python3 -m pip install --upgrade pip -q
pip install -e ".[dev]" -q 2>&1 | tail -3

# ── 3. 设置 perf_event_paranoid ─────────────────────────
echo ""
echo "[3/5] Configuring perf_event_paranoid…"
CURRENT=$(cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null || echo "unknown")
echo "  Current: $CURRENT"
if [ "$CURRENT" != "-1" ] && [ "$CURRENT" != "0" ] && [ "$CURRENT" != "1" ]; then
    echo "  Setting to 1 (allow user profiling)…"
    sudo sysctl -w kernel.perf_event_paranoid=1 || echo "  WARN: Cannot set (will run agent as root)"
fi

# ── 4. 编译 proto ──────────────────────────────────────
echo ""
echo "[4/5] Compiling protobuf stubs…"
(cd proto && bash compile.sh) || echo "WARN: proto compilation failed (may already be compiled)"

# ── 5. 运行测试 ────────────────────────────────────────
echo ""
echo "[5/5] Running Mini-Drop Test Suite…"
echo ""

RUN_MODE="${1:-full}"

if [ "$RUN_MODE" == "unit" ]; then
    echo "--- Unit Tests ---"
    python3 -m pytest tests/ -v --tb=short 2>&1 | tail -40

elif [ "$RUN_MODE" == "e2e" ]; then
    echo "--- End-to-End Scenario Tests ---"
    sudo python3 demo/test_runner.py

elif [ "$RUN_MODE" == "quick" ]; then
    echo "--- Quick Mode ---"
    sudo python3 demo/test_runner.py --quick

elif [ "$RUN_MODE" == "single" ]; then
    SCENE="${2:-cpu-fib}"
    echo "--- Single Scene: $SCENE ---"
    sudo python3 demo/test_runner.py --scene="$SCENE"

else
    echo "--- Full Suite (unit + e2e) ---"
    echo ""
    echo ">>> Phase 1: Unit Tests <<<"
    python3 -m pytest tests/ -v --tb=short
    echo ""
    echo ">>> Phase 2: End-to-End Tests <<<"
    sudo python3 demo/test_runner.py
fi

echo ""
echo "═══════════════════════════════════════════"
echo "  Test Complete"
echo "═══════════════════════════════════════════"
echo ""
echo "Artifacts:"
echo "  /tmp/mini-drop/         — 采集产物 (perf.data, flamegraph.svg, top.json, …)"
echo "  /tmp/mini-drop-test-results/report.json — 测试报告"
echo ""
echo "View results:"
echo "  cat /tmp/mini-drop-test-results/report.json | python3 -m json.tool"
echo "  ls -la /tmp/mini-drop/"
