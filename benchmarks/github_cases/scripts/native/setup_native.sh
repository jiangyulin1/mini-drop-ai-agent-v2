#!/usr/bin/env bash
# 评测环境搭建（native 变体）：worker 节点部署 otel-demo product-catalog + PostgreSQL。
# 适用：Docker Hub / Go module proxy 不可达、仅 GitHub 可达的受限网络集群。
#
# 前置（在开发机上完成，产物已上传）：
#   1. 本地交叉编译（GOPROXY=https://goproxy.cn）：
#      GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -buildvcs=false -o product-catalog ./src/product-catalog
#      GOOS=linux GOARCH=amd64 CGO_ENABLED=0 go build -buildvcs=false -o eval-load ./src/product-catalog/cmd/eval-load
#   2. scp product-catalog eval-load init.sql -> worker:/opt/mini-drop-eval/
#
# 用法（在 worker 上执行）：
#   bash setup_native.sh

set -euo pipefail

EVAL_DIR="${MINI_DROP_EVAL_DIR:-/opt/mini-drop-eval}"
LOG_DIR="/var/log/mini-drop-eval"

sudo mkdir -p "$EVAL_DIR" "$LOG_DIR"
sudo chown "$USER" "$EVAL_DIR" "$LOG_DIR"

echo "==> 安装 PostgreSQL"
sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql
sudo systemctl enable --now postgresql

echo "==> 初始化数据库（用户 otelu / 库 otel / 表 catalog.products）"
sudo -u postgres psql -c "CREATE USER otelu WITH PASSWORD 'otelp';" 2>/dev/null || true
sudo -u postgres createdb -O otelu otel 2>/dev/null || true
sudo -u postgres psql -d otel -f "$EVAL_DIR/init.sql" >/dev/null

echo "==> 启动 product-catalog（gRPC :3550，DB=postgres://otelu:otelp@127.0.0.1:5432/otel）"
pkill -f './prod.*catalog' >/dev/null 2>&1 || true
sleep 1
cd "$EVAL_DIR"
setsid nohup env \
  DB_CONNECTION_STRING='postgres://otelu:otelp@127.0.0.1:5432/otel?sslmode=disable' \
  PRODUCT_CATALOG_PORT=3550 \
  ./product-catalog </dev/null >> "$LOG_DIR/product-catalog.log" 2>&1 &
sleep 4

echo "==> 健康检查"
ss -tlnp | grep 3550 || true
"$EVAL_DIR/eval-load" -c 4 -z 5 -addr 127.0.0.1:3550 | tail -1
echo "==> 搭建完成。日志位置（供 log_scan 采集）: $LOG_DIR/product-catalog.log"
