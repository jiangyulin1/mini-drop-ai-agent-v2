#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 SERVER_RELEASE_DIR WEB_RELEASE_DIR" >&2
  exit 2
fi

server_release="$(readlink -f "$1")"
web_release="$(readlink -f "$2")"
server_link="/home/control/mini-drop-active"
web_link="/var/www/mini-drop-active"

[[ -f "${server_release}/server/app/main.py" ]]
[[ -x "${server_release}/.venv/bin/python" ]]
[[ -f "${server_release}/deploy/env/control-native.env" ]]
[[ -f "${server_release}/deploy/systemd/mini-drop-analyzer.service" ]]
[[ -f "${web_release}/index.html" ]]
[[ ! -e "${server_link}" || -L "${server_link}" ]]
[[ ! -e "${web_link}" || -L "${web_link}" ]]

# Validate access as the actual Nginx worker user.  A readable file below a
# non-traversable parent (for example /home/control with mode 0750) still
# produces an Nginx 500 after the symlink switch.
nginx_user="${MINI_DROP_NGINX_USER:-www-data}"
if id "${nginx_user}" >/dev/null 2>&1; then
  runuser -u "${nginx_user}" -- test -r "${web_release}/index.html"
fi

"${server_release}/.venv/bin/python" -m compileall -q "${server_release}/server"
(
  cd "${server_release}"
  "${server_release}/.venv/bin/python" -c "import server.app.main"
)

previous_server="$(readlink -f "${server_link}" 2>/dev/null || true)"
previous_web="$(readlink -f "${web_link}" 2>/dev/null || true)"

install -m 0644 \
  "${server_release}/deploy/systemd/mini-drop-analyzer.service" \
  /etc/systemd/system/mini-drop-analyzer.service
systemctl daemon-reload
systemctl enable mini-drop-analyzer.service >/dev/null

rollback() {
  if [[ -n "${previous_server}" && -d "${previous_server}" ]]; then
    ln -sfn "${previous_server}" "${server_link}"
  fi
  if [[ -n "${previous_web}" && -d "${previous_web}" ]]; then
    ln -sfn "${previous_web}" "${web_link}"
  fi
  systemctl restart mini-drop-server.service || true
  systemctl restart mini-drop-analyzer.service || true
  nginx -s reload || true
}
trap rollback ERR

ln -sfn "${server_release}" "${server_link}"
ln -sfn "${web_release}" "${web_link}"
systemctl restart mini-drop-server.service
systemctl restart mini-drop-analyzer.service
nginx -t
nginx -s reload

set -a
# shellcheck disable=SC1091
source "${server_release}/deploy/env/control-native.env"
set +a
healthy=0
for _ in $(seq 1 30); do
  if curl --fail --silent --show-error --insecure \
    -H "X-API-Key: ${MINI_DROP_API_KEY}" \
    https://127.0.0.1/api/healthz >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 1
done
[[ "${healthy}" -eq 1 ]]

trap - ERR
echo "activated server=${server_release} web=${web_release}"
