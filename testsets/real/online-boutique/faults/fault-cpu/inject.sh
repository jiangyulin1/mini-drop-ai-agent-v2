#!/usr/bin/env bash
# CPU 占核注入：对 target_service 所在容器跑 stress-ng 占核。
# 用法：./fault-cpu/inject.sh <case_id>
set -euo pipefail
source "$(dirname "$0")/../common.sh"

CID="${1:?用法: inject.sh <case_id>}"
M="$(load_manifest "$CID")"
NODE="$(jget "$M" fault.target_node)"
SVC="$(jget "$M" fault.target_service)"
DUR="$(jget "$M" fault.duration_sec)"
PCT="$(python3 -c "print(int($(jget "$M" fault.intensity)*100))")"
CONTAINER="$(container_of "$SVC")"
ROOT_LOCATION="$(jget "$M" expected.root_location)"

if [[ "$ROOT_LOCATION" == "same_host" ]]; then
  node_run "$NODE" "nohup stress-ng --cpu 1 --cpu-load $PCT --timeout ${DUR}s >/tmp/cpu_$CID.log 2>&1 & echo \$! >/tmp/cpu_$CID.pid"
  record_gt "$CID" start "{\"method\":\"stress-ng-host\",\"pid_file\":\"/tmp/cpu_$CID.pid\",\"cpu_load_pct\":$PCT}"
  echo "[cpu] injected: $NODE host ${PCT}% × ${DUR}s"
else
  node_run "$NODE" "docker exec -d $CONTAINER sh -c 'stress-ng --cpu 1 --cpu-load $PCT --timeout ${DUR}s >/tmp/cpu_$CID.log 2>&1 & echo \$! >/tmp/cpu_$CID.pid'"
  record_gt "$CID" start "{\"method\":\"stress-ng-container\",\"container\":\"$CONTAINER\",\"pid_file\":\"/tmp/cpu_$CID.pid\",\"cpu_load_pct\":$PCT}"
  echo "[cpu] injected: $NODE/$SVC container=$CONTAINER ${PCT}% × ${DUR}s"
fi
