#!/usr/bin/env bash
# Read-only environment checks. Does not inject or modify faults.
set -euo pipefail
source "$(dirname "$0")/../faults/common.sh"

CID="${1:?用法: preflight.sh <case_id>}"
M="$(load_manifest "$CID")"
TYPE="$(jget "$M" fault.type)"
NODE="$(jget "$M" fault.target_node)"
SVC="$(jget "$M" fault.target_service)"
ROOT_LOCATION="$(jget "$M" expected.root_location)"

[[ "$(uname -s)" == "Linux" ]] || { echo "[ERR] 故障注入只允许在隔离 Linux 测试环境运行" >&2; exit 2; }
command -v python3 >/dev/null
command -v curl >/dev/null
: "${TARGET_URL:?请设置 TARGET_URL}"

case "$TYPE" in
  cpu)
    if [[ "$ROOT_LOCATION" == "same_host" ]]; then
      node_run "$NODE" "command -v stress-ng >/dev/null"
    else
      node_run "$NODE" "command -v docker >/dev/null && docker inspect $(container_of "$SVC") >/dev/null"
    fi
    ;;
  delay)
    : "${FAULT_IFACE:?网络延迟案例必须显式设置 FAULT_IFACE，禁止默认修改网卡}"
    node_run "$NODE" "command -v tc >/dev/null && ip link show '$FAULT_IFACE' >/dev/null"
    ;;
  memory|connection|code_hotspot)
    node_run "$NODE" "command -v python3 >/dev/null"
    ;;
  *)
    echo "[ERR] 未支持的故障类型: $TYPE" >&2
    exit 2
    ;;
esac

curl --silent --show-error --fail --max-time 5 --output /dev/null "$TARGET_URL"
echo "[OK] preflight passed: $CID node=$NODE service=$SVC"
