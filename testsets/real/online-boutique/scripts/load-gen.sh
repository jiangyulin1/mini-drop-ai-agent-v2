#!/usr/bin/env bash
# Minimal deterministic HTTP load generator for the VM testset.
set -euo pipefail

TARGET_URL="${TARGET_URL:?请设置 TARGET_URL，例如 http://worker1:8080/}"
QPS="${QPS:-10}"
DURATION_SEC="${WORKLOAD_DURATION_SEC:-60}"

[[ "$QPS" =~ ^[1-9][0-9]*$ ]] || { echo "[ERR] QPS 必须为正整数" >&2; exit 2; }
[[ "$DURATION_SEC" =~ ^[1-9][0-9]*$ ]] || { echo "[ERR] WORKLOAD_DURATION_SEC 必须为正整数" >&2; exit 2; }

python3 - "$TARGET_URL" "$QPS" "$DURATION_SEC" <<'PY'
import subprocess
import sys
import time

url, qps, duration = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
deadline = time.monotonic() + duration
interval = 1.0 / qps
sent = ok = 0
while time.monotonic() < deadline:
    started = time.monotonic()
    result = subprocess.run(
        ["curl", "--silent", "--show-error", "--output", "/dev/null",
         "--max-time", "5", "--write-out", "%{http_code}", url],
        capture_output=True,
        text=True,
    )
    sent += 1
    if result.returncode == 0 and result.stdout.startswith(("2", "3")):
        ok += 1
    time.sleep(max(0.0, interval - (time.monotonic() - started)))
print(f"[workload] sent={sent} success={ok} failed={sent-ok}")
PY
