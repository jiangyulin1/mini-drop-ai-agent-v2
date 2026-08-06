#!/usr/bin/env bash
# 评测环境搭建：在 worker 节点部署 OpenTelemetry Demo checkoutservice + Redis。
# 用法（在对应 worker 上执行）：
#   bash setup.sh worker1
# 要求：Docker Engine 可用；能访问 GitHub（或使用离线镜像仓库）。

set -euo pipefail

WORKER_NAME="${1:-worker1}"
REPO_DIR="${MINI_DROP_EVAL_REPO:-/opt/otel-demo}"
OTEL_DEMO_TAG="${MINI_DROP_EVAL_OTEL_TAG:-v1.11.0}"
LOG_DIR="/var/log/mini-drop-eval"

mkdir -p "$LOG_DIR"

echo "==> [$WORKER_NAME] 安装评测依赖（hey 负载生成器）"
if ! command -v hey >/dev/null 2>&1; then
  # hey: https://github.com/rakyll/hey（Go 写的 HTTP 负载生成器，单二进制）
  if command -v go >/dev/null 2>&1; then
    go install github.com/rakyll/hey@latest
    export PATH="$PATH:$(go env GOPATH)/bin"
  else
    echo "WARN: 未找到 go，hey 将不可用；可用 wrk/ab 替代。"
  fi
fi

echo "==> [$WORKER_NAME] 拉取 OpenTelemetry Demo 源码（固定 tag）"
if [ ! -d "$REPO_DIR/.git" ]; then
  git clone --depth 1 --branch "$OTEL_DEMO_TAG" \
    https://github.com/open-telemetry/opentelemetry-demo.git "$REPO_DIR"
else
  echo "    源码已存在，跳过 clone"
fi

echo "==> [$WORKER_NAME] 构建 checkoutservice 镜像（本地构建，避免 Docker Hub 超时）"
# checkoutservice 是 Go 服务，Dockerfile 位于 src/checkoutservice/Dockerfile
cd "$REPO_DIR"
docker build -t mini-drop-eval/checkoutservice:"$OTEL_DEMO_TAG" \
  -f src/checkoutservice/Dockerfile src/checkoutservice

echo "==> [$WORKER_NAME] 启动 Redis 依赖"
docker rm -f mini-drop-eval-redis >/dev/null 2>&1 || true
docker run -d --name mini-drop-eval-redis --restart=unless-stopped \
  -p 6379:6379 redis:7-alpine

echo "==> [$WORKER_NAME] 启动 checkoutservice"
docker rm -f mini-drop-eval-checkout >/dev/null 2>&1 || true
docker run -d --name mini-drop-eval-checkout --restart=unless-stopped \
  -p 18080:8080 \
  -e REDIS_ADDR=127.0.0.1:6379 \
  -e CHECKOUT_PORT=8080 \
  --log-driver=json-file --log-opt max-size=50m --log-opt max-file=2 \
  mini-drop-eval/checkoutservice:"$OTEL_DEMO_TAG"

echo "==> [$WORKER_NAME] 等待服务就绪"
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:18080/health" >/dev/null 2>&1; then
    echo "    checkoutservice 就绪"
    break
  fi
  sleep 2
done

echo "==> [$WORKER_NAME] 健康检查"
docker ps --filter name=mini-drop-eval- --format '{{.Names}}\t{{.Status}}'
echo "    Redis: $(docker exec mini-drop-eval-redis redis-cli ping 2>/dev/null || echo '不可达')"
echo "==> 搭建完成。日志位置（供 log_scan 采集）:"
echo "    $(docker inspect --format '{{.LogPath}}' mini-drop-eval-checkout)"
