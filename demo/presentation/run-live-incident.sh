#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${MINI_DROP_ENV_FILE:-/home/control/mini-drop-active/deploy/env/control-native.env}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[FAIL] 找不到服务环境文件：$ENV_FILE" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

exec /home/control/mini-drop-active/.venv/bin/python \
  "$SCRIPT_DIR/live_incident_demo.py" "$@"
