#!/usr/bin/env bash
# 单个故障案例的 E2E 编排：preflight → baseline → inject → revert → recovery → 校验 GT。
# 用法：./run-fault.sh <case_id> [--preflight|--no-verify]
#
# 说明：注入后脚本会 sleep duration 秒；期间由 mini-drop 完成捕获与诊断。
# 时序建议：mini-drop 先开始"捕获窗口"，再执行本脚本，保证 before/during/after 对齐。
set -euo pipefail
source "$(dirname "$0")/common.sh"

CID="${1:?用法: run-fault.sh <case_id>}"
M="$(load_manifest "$CID")"
DUR="$(jget "$M" fault.duration_sec)"
TYPE="$(jget "$M" fault.type)"
FAULT_DIR="$(dirname "$0")/fault-$TYPE"
VERIFY="${2:-verify}"
BASELINE="$(jget "$M" performance_requirements.baseline_duration_sec)"
RECOVERY="$(jget "$M" performance_requirements.recovery_timeout_sec)"
PREFLIGHT="$(dirname "$0")/../scripts/preflight.sh"
WORKLOAD="$(dirname "$0")/../$(jget "$M" trigger.workload_script)"

[[ -d "$FAULT_DIR" ]] || { echo "[ERR] 无对应故障目录: $FAULT_DIR" >&2; exit 2; }
"$PREFLIGHT" "$CID"
if [[ "$VERIFY" == "--preflight" ]]; then
  exit 0
fi

INJECTED=0
WORKLOAD_PID=""
cleanup() {
  if [[ "$INJECTED" == "1" ]]; then
    "$FAULT_DIR/revert.sh" "$CID" || true
  fi
  if [[ -n "$WORKLOAD_PID" ]]; then
    kill "$WORKLOAD_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "== 开始 $CID (type=$TYPE, duration=${DUR}s) =="
WORKLOAD_DURATION_SEC="$((BASELINE + DUR + RECOVERY))" "$WORKLOAD" &
WORKLOAD_PID=$!
echo "== 基线 ${BASELINE}s =="
sleep "$BASELINE"
"$FAULT_DIR/inject.sh" "$CID"
INJECTED=1
echo "== 等待 ${DUR}s（捕获窗口进行中）=="
sleep "$DUR"
"$FAULT_DIR/revert.sh" "$CID"
INJECTED=0
echo "== 恢复验证 ${RECOVERY}s =="
sleep "$RECOVERY"
wait "$WORKLOAD_PID"
WORKLOAD_PID=""

if [[ "$VERIFY" != "--no-verify" ]]; then
  GT="$GT_DIR/$CID.json"
  python3 - "$GT" <<'PY'
import json,sys
p=sys.argv[1]
d=json.load(open(p))
if not d.get("ended_at"):
    sys.exit(f"[ERR] GT 未补记结束时间: {p}")
print(f"[OK] GT 完整: {p}")
print(f"  root_location={d['ground_truth']['root_location']} "
      f"domain_cause={d['ground_truth']['domain_cause']} root_entity={d['ground_truth']['root_entity']}")
PY
fi
trap - EXIT INT TERM
echo "== 完成 $CID =="
