#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-status}"
SOURCE_IP="${MINI_DROP_FAULT_SOURCE_IP:-192.168.10.12}"
MINIO_PORT="${MINI_DROP_FAULT_MINIO_PORT:-9000}"
COMMENT="mini-drop-demo-worker2-minio-link"
RECOVERY_UNIT="mini-drop-storage-fault-auto-recover"
SCRIPT_PATH="/home/control/mini-drop-demo/storage_link_fault.sh"
AUTO_RECOVER_MINUTES="${MINI_DROP_FAULT_AUTO_RECOVER_MINUTES:-120}"

RULE=(
  INPUT
  -s "$SOURCE_IP"
  -p tcp
  --dport "$MINIO_PORT"
  -m comment
  --comment "$COMMENT"
  -j REJECT
  --reject-with tcp-reset
)

if [[ "$EUID" -ne 0 ]]; then
  echo "[FAIL] 请通过 sudo 运行该脚本。" >&2
  exit 2
fi

has_rule() {
  iptables -w 5 -C "${RULE[@]}" >/dev/null 2>&1
}

remove_rule() {
  while has_rule; do
    iptables -w 5 -D "${RULE[@]}"
  done
}

case "$ACTION" in
  apply)
    if ! has_rule; then
      iptables -w 5 -I INPUT 1 "${RULE[@]:1}"
    fi
    systemctl stop "$RECOVERY_UNIT.timer" "$RECOVERY_UNIT.service" >/dev/null 2>&1 || true
    systemctl reset-failed "$RECOVERY_UNIT.timer" "$RECOVERY_UNIT.service" >/dev/null 2>&1 || true
    systemd-run \
      --quiet \
      --unit="$RECOVERY_UNIT" \
      --on-active="${AUTO_RECOVER_MINUTES}m" \
      --timer-property=AccuracySec=1s \
      "$SCRIPT_PATH" recover --timer
    echo "跨虚拟机异常已启用。"
    echo "真实根因：control 防火墙拒绝 worker2 ($SOURCE_IP) 到 MinIO TCP/$MINIO_PORT 的连接。"
    echo "影响：worker2 Agent 保持 ONLINE，但该节点的新采集证据无法上传，任务将在上传阶段失败。"
    echo "自动恢复：${AUTO_RECOVER_MINUTES} 分钟后移除规则。"
    ;;
  recover)
    remove_rule
    if [[ "${2:-}" != "--timer" ]]; then
      systemctl stop "$RECOVERY_UNIT.timer" "$RECOVERY_UNIT.service" >/dev/null 2>&1 || true
      systemctl reset-failed "$RECOVERY_UNIT.timer" "$RECOVERY_UNIT.service" >/dev/null 2>&1 || true
    fi
    echo "跨虚拟机 MinIO 上传链路已恢复。"
    ;;
  status)
    if has_rule; then
      echo "ACTIVE：worker2 ($SOURCE_IP) -> control MinIO TCP/$MINIO_PORT 被拒绝。"
      systemctl list-timers "$RECOVERY_UNIT.timer" --no-pager 2>/dev/null || true
      exit 0
    fi
    echo "INACTIVE：未发现演示故障规则。"
    exit 1
    ;;
  *)
    echo "用法：$0 {apply|status|recover}" >&2
    exit 2
    ;;
esac
