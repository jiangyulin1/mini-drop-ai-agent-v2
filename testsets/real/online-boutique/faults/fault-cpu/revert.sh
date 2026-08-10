#!/usr/bin/env bash
# CPU 注入回滚：终止 stress-ng，补记 GT 结束时间。
# 用法：./fault-cpu/revert.sh <case_id>
set -euo pipefail
source "$(dirname "$0")/../common.sh"

CID="${1:?用法: revert.sh <case_id>}"
M="$(load_manifest "$CID")"
NODE="$(jget "$M" fault.target_node)"
SVC="$(jget "$M" fault.target_service)"
ROOT_LOCATION="$(jget "$M" expected.root_location)"

if [[ "$ROOT_LOCATION" == "same_host" ]]; then
  node_run "$NODE" "test ! -f /tmp/cpu_$CID.pid || kill \$(cat /tmp/cpu_$CID.pid) 2>/dev/null || true; rm -f /tmp/cpu_$CID.pid"
else
  CONTAINER="$(container_of "$SVC")"
  node_run "$NODE" "docker exec $CONTAINER sh -c 'test ! -f /tmp/cpu_$CID.pid || kill \$(cat /tmp/cpu_$CID.pid) 2>/dev/null || true; rm -f /tmp/cpu_$CID.pid'"
fi
save_pid "$CID" ""
record_gt "$CID" end
echo "[cpu] reverted: $CID"
