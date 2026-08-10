#!/usr/bin/env bash
# 内存泄漏注入：对 target_service 注入"持续分配不释放"的进程。
# 用法：./fault-memory/inject.sh <case_id>
set -euo pipefail
source "$(dirname "$0")/../common.sh"

CID="${1:?用法: inject.sh <case_id>}"
M="$(load_manifest "$CID")"
NODE="$(jget "$M" fault.target_node)"
SVC="$(jget "$M" fault.target_service)"
DUR="$(jget "$M" fault.duration_sec)"
RATE_MB="$(python3 -c "print(int($(jget "$M" fault.intensity)*64))")"  # 速率 MB/min

# 版本化 fixture：每 5s 分配并保留内存，PID 作为诊断目标，避免误归因到业务服务。
node_run "$NODE" "python3 -c \"
import time,sys
buf=[]
while True:
    buf.append(bytearray(${RATE_MB}*1024*1024//12))
    time.sleep(5)
\" >/dev/null 2>&1 & echo \\$! > /tmp/leak_$CID.pid" &
INJ_PID=$!

save_pid "$CID" "$INJ_PID"
record_gt "$CID" start "{\"method\":\"alloc-loop\",\"rate_mb_per_min\":$RATE_MB}"
echo "[memory] injected: $NODE/$SVC leak-rate=${RATE_MB}MB/min × ${DUR}s"
