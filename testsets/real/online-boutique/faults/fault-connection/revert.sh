#!/usr/bin/env bash
# 连接耗尽注入回滚。
# 用法：./fault-connection/revert.sh <case_id>
set -euo pipefail
source "$(dirname "$0")/../common.sh"

CID="${1:?用法: revert.sh <case_id>}"
M="$(load_manifest "$CID")"
NODE="$(jget "$M" fault.target_node)"

node_run "$NODE" "test ! -f /tmp/conn_$CID.pid || kill \$(cat /tmp/conn_$CID.pid) 2>/dev/null || true; rm -f /tmp/conn_$CID.pid"
save_pid "$CID" ""
record_gt "$CID" end
echo "[connection] reverted: $CID"
