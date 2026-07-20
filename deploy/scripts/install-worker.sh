#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run as root: sudo $0 [repository-path]" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${1:-$SCRIPT_DIR/../..}" && pwd)"

command -v python3 >/dev/null 2>&1 || { echo "python3 is required" >&2; exit 1; }
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install --upgrade pip
"$ROOT/.venv/bin/pip" install -e "$ROOT[dev]"
(cd "$ROOT/proto" && bash compile.sh)

install -d -m 0750 /etc/mini-drop
if [[ ! -e /etc/mini-drop/worker.env ]]; then
  install -m 0600 "$ROOT/deploy/env/worker.env.example" /etc/mini-drop/worker.env
fi
escaped_root="${ROOT//|/\\|}"
sed "s|@MINI_DROP_ROOT@|$escaped_root|g" \
  "$ROOT/deploy/systemd/mini-drop-agent.service" > /etc/systemd/system/mini-drop-agent.service
systemctl daemon-reload

echo "installed unit: /etc/systemd/system/mini-drop-agent.service"
echo "next: edit /etc/mini-drop/worker.env, copy ca.crt to $ROOT/deploy/certs/, then run:"
echo "  systemctl enable --now mini-drop-agent"
