"""三节点集群健康巡检：采集所有节点状态并生成可视化 HTML 报告。"""
import json
import os
import paramiko

NODES = [
    {"ip": "192.168.10.10", "user": "control", "label": "control 控制节点"},
    {"ip": "192.168.10.11", "user": "worker1", "label": "worker1 采集节点"},
    {"ip": "192.168.10.12", "user": "worker2", "label": "worker2 采集节点"},
]
PASSWORD = os.getenv("MINI_DROP_VM_PASSWORD", "")

BASIC = r"""
echo "===HOSTNAME==="; hostname
echo "===KERNEL==="; uname -r
echo "===UPTIME==="; uptime -p; uptime | awk -F'load average:' '{print "load:"$2}'
echo "===CPU==="; nproc; grep "model name" /proc/cpuinfo | head -1
echo "===MEM==="; free -m | awk '/Mem:/{print $2" "$3" "$4" "$7}'
echo "===DISK==="; df -h / | tail -1
echo "===SERVICES==="; systemctl is-active mini-drop-server mini-drop-s3 nginx mini-drop-agent 2>&1 | tr '\n' ' '; echo
echo "===DOCKER==="; systemctl is-active docker 2>&1
echo "===AGENT_DIR==="; ls -d /tmp/mini-drop-agent-results 2>/dev/null && ls /tmp/mini-drop-agent-results | wc -l
echo "===RELEASE==="; ls -d /home/*/mini-drop-release-* 2>/dev/null | head -2
echo "===PERF==="; perf --version 2>/dev/null | head -1
echo "===BPFTRACE==="; bpftrace --version 2>/dev/null | head -1
echo "===PYSPY==="; ls /home/*/.venv*/bin/py-spy 2>/dev/null | head -1
echo "===IP==="; ip -br address show | grep -v "^lo" | tr '\n' ' '; echo
"""


def run(client: paramiko.SSHClient, cmd: str) -> str:
    _, stdout, stderr = client.exec_command(cmd, timeout=30)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    return out + (f"\n[stderr] {err}" if err.strip() else "")


def parse(text: str) -> dict:
    d = {}
    lines = text.splitlines()
    key = None
    for ln in lines:
        if ln.startswith("==="):
            key = ln.strip("=").strip()
            d[key] = []
        elif key:
            d[key].append(ln)
    return {k: "\n".join(v).strip() for k, v in d.items()}


results = {}
for node in NODES:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        c.connect(node["ip"], username=node["user"], password=PASSWORD, timeout=10)
        raw = run(c, BASIC)
        results[node["ip"]] = {"node": node, "raw": parse(raw)}
    except Exception as e:
        results[node["ip"]] = {"node": node, "error": str(e)}
    finally:
        c.close()

with open("cluster_check.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print("done")
