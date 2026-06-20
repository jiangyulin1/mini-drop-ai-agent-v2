#!/bin/bash
# Mini-Drop 一键演示脚本
#
# 前提: docker compose up -d 已启动所有服务
# 用法: bash demo/demo.sh
#
# 流程:
#   1. 启动 CPU 热点演示进程
#   2. 通过 REST API 创建 perf 采集任务
#   3. 轮询任务状态直到完成
#   4. 验证产物（火焰图/热点/TopN）

set -euo pipefail

API="${MINI_DROP_API_URL:-http://localhost/api}"
AGENT_ID="${AGENT_ID:-agent_docker_demo}"
API_KEY="${MINI_DROP_API_KEY:-}"
AUTH_ARGS=()
if [ -n "$API_KEY" ]; then
  AUTH_ARGS=(-H "X-API-Key: $API_KEY")
fi

echo "=== Mini-Drop 端到端演示 ==="
echo ""

# 1. 启动演示进程
echo "[1/4] 启动 CPU 热点演示进程..."
python3 demo/cpu_hotspot.py &
DEMO_PID=$!
trap "kill $DEMO_PID 2>/dev/null || true" EXIT
sleep 1
echo "  演示进程 PID=$DEMO_PID"
echo ""

# 2. 创建采集任务
echo "[2/4] 创建 perf 采集任务..."
RESP=$(curl -s -X POST "$API/tasks" \
  -H "Content-Type: application/json" \
  "${AUTH_ARGS[@]}" \
  -d "{
    \"name\": \"demo perf profiling\",
    \"agent_id\": \"$AGENT_ID\",
    \"target_pid\": $DEMO_PID,
    \"collector_type\": \"perf_cpu\",
    \"sample_rate\": 99,
    \"duration_sec\": 15
  }")
TASK_ID=$(echo "$RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['task_id'])")
echo "  任务已创建: $TASK_ID"
echo ""

# 3. 轮询任务状态
echo "[3/4] 等待采集完成..."
for i in $(seq 1 30); do
  STATUS=$(curl -s "${AUTH_ARGS[@]}" "$API/tasks/$TASK_ID" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['status'])" 2>/dev/null || echo "PENDING")
  echo "  状态: $STATUS (${i}/30)"
  if [ "$STATUS" = "DONE" ] || [ "$STATUS" = "FAILED" ]; then
    break
  fi
  sleep 3
done
echo ""

# 4. 验证产物
echo "[4/4] 验证产物..."
ARTIFACTS=$(curl -s "${AUTH_ARGS[@]}" "$API/tasks/$TASK_ID/artifacts")
ARTIFACT_COUNT=$(echo "$ARTIFACTS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['data']))" 2>/dev/null || echo "0")
echo "  产物数量: $ARTIFACT_COUNT"

TASK_DETAIL=$(curl -s "${AUTH_ARGS[@]}" "$API/tasks/$TASK_ID")
STATUS_FINAL=$(echo "$TASK_DETAIL" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['status'])" 2>/dev/null || echo "UNKNOWN")
echo "  最终状态: $STATUS_FINAL"

EVENTS=$(curl -s "${AUTH_ARGS[@]}" "$API/tasks/$TASK_ID/events")
EVENT_COUNT=$(echo "$EVENTS" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['data']))" 2>/dev/null || echo "0")
echo "  状态事件数: $EVENT_COUNT"

echo ""
echo "=== 演示完成 ==="
echo "在浏览器打开 http://localhost 查看火焰图"
