#!/usr/bin/env bash
# 网络延迟注入回滚。
# 用法：./fault-delay/revert.sh <case_id>
set -euo pipefail
source "$(dirname "$0")/../common.sh"

CID="${1:?用法: revert.sh <case_id>}"
M="$(load_manifest "$CID")"
NODE="$(jget "$M" fault.target_node)"
IFACE="${FAULT_IFACE:?请显式设置隔离测试网卡 FAULT_IFACE}"

node_run "$NODE" "tc qdisc del dev $IFACE root 2>/dev/null || true"
save_pid "$CID" ""
record_gt "$CID" end
echo "[delay] reverted: $CID (iface=$IFACE)"
