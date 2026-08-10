#!/usr/bin/env bash
# 网络延迟注入：tc netem 对 target_node 出口/入口加延迟，制造下游网络假象。
# 用法：./fault-delay/inject.sh <case_id>
set -euo pipefail
source "$(dirname "$0")/../common.sh"

CID="${1:?用法: inject.sh <case_id>}"
M="$(load_manifest "$CID")"
NODE="$(jget "$M" fault.target_node)"
DUR="$(jget "$M" fault.duration_sec)"
DELAY_MS="$(python3 -c "print(int($(jget "$M" fault.intensity)*100))")"

# 必须显式确认隔离测试网卡；禁止默认修改 eth0/默认路由。
IFACE="${FAULT_IFACE:?请显式设置隔离测试网卡 FAULT_IFACE}"

node_run "$NODE" "tc qdisc replace dev $IFACE root netem delay ${DELAY_MS}ms 5ms distribution normal"
# 自动到期回滚：延迟注入是定时器，另起一个睡眠后清理
node_run "$NODE" "(sleep ${DUR}s && tc qdisc del dev $IFACE root 2>/dev/null || true) &" &
INJ_PID=$!

save_pid "$CID" "$INJ_PID"
record_gt "$CID" start "{\"method\":\"tc-netem\",\"iface\":\"$IFACE\",\"delay_ms\":$DELAY_MS}"
echo "[delay] injected: $NODE dev=$IFACE +${DELAY_MS}ms × ${DUR}s"
