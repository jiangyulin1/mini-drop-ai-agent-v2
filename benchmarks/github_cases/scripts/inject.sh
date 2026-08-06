#!/usr/bin/env bash
# 故障注入与恢复：按场景在 worker 上注入/清理故障（native 部署：Go 二进制 + apt PostgreSQL）。
# 用法：
#   bash inject.sh catalog-cpu-hotspot worker1          # 注入
#   bash inject.sh --clean catalog-cpu-hotspot worker1   # 清理
#
# 注意：这些命令只作用于评测服务与测试文件，绝不触碰生产配置。
# 故障场景统一附带低并发压测，确保故障通过真实请求显现（连接池/日志/延迟）。

set -euo pipefail

ACTION="${1:-}"
CASE_ID="${2:-}"
WORKER_NAME="${3:-worker1}"
EVAL_DIR="${MINI_DROP_EVAL_DIR:-/opt/mini-drop-eval}"
LOAD_ADDR="127.0.0.1:3550"

clean_load() {
  pkill -f "eval-load" >/dev/null 2>&1 || true
}

if [ "$ACTION" = "--clean" ]; then
  CASE_ID="$2"
  case "$CASE_ID" in
    catalog-cpu-hotspot)
      echo "==> 停止 gRPC 压测"
      clean_load
      ;;
    catalog-downstream-pg-down)
      echo "==> 停止压测并恢复 PostgreSQL 下游"
      clean_load
      sudo systemctl start postgresql >/dev/null 2>&1 || true
      ;;
    catalog-host-io-contention)
      echo "==> 停止压测与同机磁盘写压"
      clean_load
      pkill -f "io-storm" >/dev/null 2>&1 || true
      rm -f /tmp/io-storm
      ;;
    catalog-no-fault-baseline)
      echo "==> 无故障场景无需清理"
      ;;
    *)
      echo "未知场景: $CASE_ID" >&2
      exit 2
      ;;
  esac
  echo "==> 清理完成"
  exit 0
fi

CASE_ID="$1"
WORKER_NAME="$2"

case "$CASE_ID" in
  catalog-cpu-hotspot)
    echo "==> [$WORKER_NAME] 注入 CPU 热点：对 product-catalog gRPC 端点发起持续高并发请求"
    nohup "$EVAL_DIR/eval-load" -c 10 -z 120 -addr "$LOAD_ADDR" \
      >/tmp/mini-drop-eval-load.log 2>&1 &
    echo "    eval-load 已启动 (PID $!)"
    ;;
  catalog-downstream-pg-down)
    echo "==> [$WORKER_NAME] 注入下游故障：停止 PostgreSQL（低并发压测触发连接失败，不形成 CPU 热点）"
    sudo systemctl stop postgresql
    nohup "$EVAL_DIR/eval-load" -c 2 -z 120 -addr "$LOAD_ADDR" \
      >/tmp/mini-drop-eval-load.log 2>&1 &
    echo "    postgresql 已停止；eval-load 已启动 (PID $!)"
    ;;
  catalog-host-io-contention)
    echo "==> [$WORKER_NAME] 注入同机磁盘 I/O 争抢（循环覆盖写 /tmp/io-storm）+ 低并发压测"
    # SSD 顺序写太快（>6GB/s），一次性写会在评测前结束；用循环覆盖写保持持续 IO 压力
    truncate -s 20G /tmp/io-storm
    nohup bash -c 'while true; do dd if=/dev/zero of=/tmp/io-storm bs=1M count=20000 oflag=direct conv=notrunc 2>/dev/null; done' >/dev/null 2>&1 &
    nohup "$EVAL_DIR/eval-load" -c 2 -z 120 -addr "$LOAD_ADDR" \
      >/tmp/mini-drop-eval-load.log 2>&1 &
    echo "    io-storm 循环写已启动；eval-load 已启动"
    ;;
  catalog-no-fault-baseline)
    echo "==> [$WORKER_NAME] 无故障基线：不注入任何故障"
    ;;
  *)
    echo "未知场景: $CASE_ID" >&2
    exit 2
    ;;
esac

echo "==> 注入完成，等待 20 秒让故障稳定显现"
sleep 20
