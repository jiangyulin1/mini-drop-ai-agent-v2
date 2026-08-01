#!/usr/bin/env bash
set -euo pipefail

base_url="${MINI_DROP_BASE_URL:-https://127.0.0.1}"
env_file="${MINI_DROP_ENV_FILE:-/home/control/mini-drop-active/deploy/env/control-native.env}"

if [[ -r "${env_file}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${env_file}"
  set +a
fi

: "${MINI_DROP_API_KEY:?MINI_DROP_API_KEY is required}"

response_file="$(mktemp)"
trap 'rm -f "${response_file}"' EXIT

curl --fail --silent --show-error --insecure \
  -H "X-API-Key: ${MINI_DROP_API_KEY}" \
  -o "${response_file}" \
  "${base_url}/api/storage/reconciliation?limit=${MINI_DROP_RECONCILE_LIMIT:-5000}&verify_hash=${MINI_DROP_VERIFY_HASH:-false}"

"${MINI_DROP_PYTHON:-/home/control/mini-drop-active/.venv/bin/python}" - \
  "${response_file}" "${MINI_DROP_RECONCILE_FAIL_ON_MISSING:-0}" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
summary = payload["data"]["summary"]
print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
fatal = summary["unavailable"] > 0 or summary["integrity_mismatch"] > 0
if sys.argv[2] == "1":
    fatal = fatal or summary["missing"] > 0
raise SystemExit(1 if fatal else 0)
PY
