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

exec python3 "$SCRIPT_DIR/presentation_check.py" \
  --server-url "${MINI_DROP_PRESENTATION_SERVER_URL:-https://127.0.0.1}" \
  --public-url "${MINI_DROP_PRESENTATION_PUBLIC_URL:-https://192.168.10.10}" \
  --insecure \
  "$@"
