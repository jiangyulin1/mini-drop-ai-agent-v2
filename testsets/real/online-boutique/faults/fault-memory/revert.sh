#!/usr/bin/env bash
# 内存泄漏注入回滚。
# 用法：./fault-memory/revert.sh <case_id>
set -euo pipefail
source "$(dirname "$0")/../common.sh"

CID="${1:?用法: revert.sh <case_id>}"
M="$(load_manifest "$CID")"
NODE="$(jget "$M" fault.target_node)"

node_run "$NODE" "test ! -f /tmp/leak_$CID.pid || kill \$(cat /tmp/leak_$CID.pid) 2>/dev/null || true; rm -f /tmp/leak_$CID.pid"
save_pid "$CID" ""
record_gt "$CID" end
echo "[memory] reverted: $CID"
