#!/usr/bin/env bash
set -euo pipefail

: "${MINIO_ACCESS_KEY:?MINIO_ACCESS_KEY is required}"
: "${MINIO_SECRET_KEY:?MINIO_SECRET_KEY is required}"

export MINIO_ROOT_USER="${MINIO_ACCESS_KEY}"
export MINIO_ROOT_PASSWORD="${MINIO_SECRET_KEY}"

exec /home/control/mini-drop-bin/minio server \
  /home/control/mini-drop-storage \
  --address 192.168.10.10:9000 \
  --console-address 127.0.0.1:9001
