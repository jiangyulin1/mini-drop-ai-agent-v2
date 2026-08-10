#!/usr/bin/env bash
# 故障注入公共库。
# 用法：source "$(dirname "$0")/common.sh"
#
# 设计契约：
#   每个 fault-<type>/inject.sh <case_id> 读取 manifest → 在 target_node 注入故障 →
#   用 record_gt 自记录 ground truth（root_location/domain_cause/root_entity + 时间窗）。
#   每个 fault-<type>/revert.sh <case_id> 停止故障 → 补记 ended_at。
#   GT 写入 ../ground_truth/<case_id>.json，供评测 harness 使用。
#
# Linux 就绪：本机即 worker 时直接执行；跨节点时配置 NODE_SSH 走 ssh。
set -euo pipefail

TESTSETS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
CASES_DIR="$TESTSETS_ROOT/real/online-boutique/cases"
GT_DIR="$TESTSETS_ROOT/real/online-boutique/ground_truth"
RUN_DIR="$TESTSETS_ROOT/real/online-boutique/runs"
PID_DIR="$TESTSETS_ROOT/real/online-boutique/runs/pids"

mkdir -p "$GT_DIR" "$RUN_DIR" "$PID_DIR"

# ── 节点执行前缀 ─────────────────────────────────────────────
# 若 target_node 是本机，留空（直接执行）；跨节点改为：
#   export FAULT_NODE_SSH="ssh -o ConnectTimeout=5 root@<worker-ip>"
NODE_SSH="${FAULT_NODE_SSH:-}"

node_run() { # $1=node  $2=cmd
  if [[ -z "$NODE_SSH" ]]; then
    bash -c "$2"
  else
    $NODE_SSH "$2"
  fi
}

# ── JSON 取值（简化点分路径）────────────────────────────────
jget() { # $1=manifest $2=dot.path
  python3 - "$1" "$2" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
for k in sys.argv[2].split('.'):
    d=d[k]
print(d)
PY
}

load_manifest() { # $1=case_id -> 输出 manifest 绝对路径
  local m="$CASES_DIR/$1.json"
  [[ -f "$m" ]] || { echo "[ERR] manifest 不存在: $m" >&2; exit 2; }
  echo "$m"
}

# ── Ground Truth 自记录 ─────────────────────────────────────
# record_gt <case_id> start [extra_json]
# record_gt <case_id> end   [extra_json]
record_gt() {
  local cid="$1" ev="$2" extra="${3:-{}}"
  local m="$CASES_DIR/$cid.json" gtf="$GT_DIR/$cid.json"
  local now
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [[ "$ev" == "start" ]]; then
    python3 - "$cid" "$m" "$gtf" "$now" "$extra" <<'PY'
import json,sys
cid,m,gtf,now,extra=sys.argv[1:]
m=json.load(open(m)); extra=json.loads(extra)
gt={
  "case_id": cid,
  "fault": m["fault"],
  "ground_truth": {
    "root_location": m["expected"]["root_location"],
    "domain_cause": m["expected"]["domain_cause"],
    "root_entity": m["expected"].get("root_entity"),
  },
  "started_at": now,
  "ended_at": None,
  "extra": extra,
}
json.dump(gt, open(gtf,"w"), ensure_ascii=False, indent=2)
PY
  else
    python3 - "$gtf" "$now" "$extra" <<'PY'
import json,sys
p,now,extra=sys.argv[1:]
d=json.load(open(p)); d["ended_at"]=now
if extra!="{}": d["extra"].update(json.loads(extra))
json.dump(d, open(p,"w"), ensure_ascii=False, indent=2)
PY
  fi
  echo "[GT] $cid $ev -> $gtf"
}

# ── PID 记录（供 revert 定位）────────────────────────────────
save_pid() { echo "$2" > "$PID_DIR/$1.pid"; }
get_pid()  { cat "$PID_DIR/$1.pid" 2>/dev/null || true; }

# ── 目标解析 ────────────────────────────────────────────────
# manifest 里 fault.target_service 对容器注入时映射到 docker 容器名。
# 部署后需维护此映射（见 deploy/README）：服务名 -> 容器名。
container_of() { # $1=service  -> 容器名（默认同服务名）
  echo "${1:-}"
}

echo "[common] loaded. cases=$CASES_DIR  gt=$GT_DIR"
