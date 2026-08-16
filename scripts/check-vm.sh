#!/bin/bash
# 测试 Mini-Drop 集群从 Mac 的可达性
# 用法: ./scripts/check-vm.sh
set -u

# 优先用 mDNS 主机名，失败则回退到 IP（IP 会随 DHCP 变化，需按当前值改）
HOST="lenovo.local"
if ! nc -z -G 2 "$HOST" 22 2>/dev/null; then
  echo "! mDNS 主机名不可用，尝试 192.168.2.203"
  HOST="192.168.2.203"
fi

echo "== 探测 $HOST 关键端口 =="
for p in 22:Windows-SSH 2201:control 2202:worker1 2203:worker2 8443:Web界面; do
  port="${p%%:*}"
  name="${p##*:}"
  if nc -z -G 3 "$HOST" "$port" 2>/dev/null; then
    echo "✓ $port ($name) 已开放"
  else
    echo "✗ $port ($name) 不通"
  fi
done

echo ""
echo "连通后使用: ssh -F ssh/vm-config control / worker1 / worker2"
echo "Web 界面:   https://$HOST:8443/"
