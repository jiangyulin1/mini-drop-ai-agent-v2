#!/usr/bin/env bash
# Start a deterministic Python source-level hotspot fixture.
set -euo pipefail
source "$(dirname "$0")/../common.sh"

CID="${1:?用法: inject.sh <case_id>}"
M="$(load_manifest "$CID")"
NODE="$(jget "$M" fault.target_node)"
DUR="$(jget "$M" fault.duration_sec)"

node_run "$NODE" "nohup python3 -c '
import json,time
deadline=time.time()+${DUR}
payload=list(range(2000))
def inefficient_serialize_loop():
    while time.time()<deadline:
        json.dumps(payload, separators=(\",\", \":\"), sort_keys=True)
inefficient_serialize_loop()
' >/tmp/hotspot_$CID.log 2>&1 & echo \$! >/tmp/hotspot_$CID.pid"

record_gt "$CID" start '{"method":"python-fixture","symbol":"inefficient_serialize_loop","pid_file":"/tmp/hotspot_'"$CID"'.pid"}'
echo "[code_hotspot] injected: $NODE pid-file=/tmp/hotspot_$CID.pid"
