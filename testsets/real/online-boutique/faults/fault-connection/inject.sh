#!/usr/bin/env bash
# 连接/句柄耗尽注入：对 target_service 持续建立并持有 TCP 连接，直至接近 ulimit。
# 用法：./fault-connection/inject.sh <case_id>
set -euo pipefail
source "$(dirname "$0")/../common.sh"

CID="${1:?用法: inject.sh <case_id>}"
M="$(load_manifest "$CID")"
NODE="$(jget "$M" fault.target_node)"
SVC="$(jget "$M" fault.target_service)"

# 用 socketpair 在同一 fixture 进程中确定性增长 socket/fd；诊断目标就是该 PID。
node_run "$NODE" "python3 -c \"
import socket,time
held=[]
while True:
    try:
        left,right=socket.socketpair(); held.extend((left,right))
    except OSError:
        time.sleep(1)
    time.sleep(0.05)
\" >/dev/null 2>&1 & echo \\$! > /tmp/conn_$CID.pid" &
INJ_PID=$!

save_pid "$CID" "$INJ_PID"
record_gt "$CID" start "{\"method\":\"socketpair-fixture\",\"pid_file\":\"/tmp/conn_$CID.pid\"}"
echo "[connection] injected: $NODE/$SVC pid-file=/tmp/conn_$CID.pid"
