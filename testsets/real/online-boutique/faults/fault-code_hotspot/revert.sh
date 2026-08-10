#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/../common.sh"

CID="${1:?用法: revert.sh <case_id>}"
M="$(load_manifest "$CID")"
NODE="$(jget "$M" fault.target_node)"

node_run "$NODE" "test ! -f /tmp/hotspot_$CID.pid || kill \$(cat /tmp/hotspot_$CID.pid) 2>/dev/null || true; rm -f /tmp/hotspot_$CID.pid"
record_gt "$CID" end
echo "[code_hotspot] reverted: $CID"
