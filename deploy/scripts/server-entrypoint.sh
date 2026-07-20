#!/bin/sh
set -eu

# bind mount 的私钥通常为 0600 且宿主机 UID 不确定。启动阶段复制到容器临时目录，
# 交给 mini-drop 用户后立即降权；应用进程本身始终不以 root 运行。
if [ "${MINI_DROP_GRPC_SECURE:-0}" = "1" ]; then
  cert_source="${MINI_DROP_GRPC_CERT_FILE:-}"
  key_source="${MINI_DROP_GRPC_KEY_FILE:-}"
  if [ -z "$cert_source" ] || [ -z "$key_source" ]; then
    echo "gRPC TLS enabled but certificate paths are missing" >&2
    exit 1
  fi
  install -d -o mini-drop -g mini-drop -m 0700 /tmp/mini-drop-tls
  install -o mini-drop -g mini-drop -m 0644 "$cert_source" /tmp/mini-drop-tls/server.crt
  install -o mini-drop -g mini-drop -m 0600 "$key_source" /tmp/mini-drop-tls/server.key
  export MINI_DROP_GRPC_CERT_FILE=/tmp/mini-drop-tls/server.crt
  export MINI_DROP_GRPC_KEY_FILE=/tmp/mini-drop-tls/server.key
fi

exec gosu mini-drop "$@"
