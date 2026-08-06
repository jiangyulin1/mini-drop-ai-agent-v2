"""生成三节点集群可视化 HTML 报告（单文件、无外部依赖）。"""
import json
import os
import ssl
import urllib.request
import datetime

import paramiko

NODES = [
    {"ip": "192.168.10.10", "user": "control", "label": "control", "role": "控制节点（Server / S3 / Nginx / 任务编排）"},
    {"ip": "192.168.10.11", "user": "worker1", "label": "worker1", "role": "采集节点（Agent + 被测负载）"},
    {"ip": "192.168.10.12", "user": "worker2", "label": "worker2", "role": "采集节点（Agent + 被测负载）"},
]
PASSWORD = os.getenv("MINI_DROP_VM_PASSWORD", "")

BASIC = r"""
echo "===HOSTNAME==="; hostname
echo "===KERNEL==="; uname -r
echo "===LOAD==="; cat /proc/loadavg | cut -d' ' -f1-3
echo "===MEM==="; free -m | awk '/Mem:/{print $2"|"$3"|"$4}'
echo "===DISK==="; df -h / | awk 'NR==2{print $2"|"$3"|"$5}'
echo "===SERVICES==="; systemctl is-active mini-drop-server mini-drop-s3 nginx mini-drop-agent 2>&1 | tr '\n' ','; echo
echo "===DOCKER==="; systemctl is-active docker 2>&1
echo "===UPTIME==="; uptime -s
echo "===ACTIVE==="; readlink -f /home/*/mini-drop-active 2>/dev/null | head -1
"""


def ssh_exec(ip: str, user: str, cmd: str) -> str:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(ip, username=user, password=PASSWORD, timeout=10)
    _, out, err = c.exec_command(cmd, timeout=30)
    text = out.read().decode("utf-8", "replace")
    c.close()
    return text


def parse(text: str) -> dict:
    d, key = {}, None
    for ln in text.splitlines():
        if ln.startswith("==="):
            key = ln.strip("=").strip()
            d[key] = []
        elif key:
            d[key].append(ln)
    return {k: "\n".join(v).strip() for k, v in d.items()}


def get_api(key: str, path: str):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(f"https://192.168.10.10{path}", headers={"X-API-Key": key})
    with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
        return json.loads(r.read().decode())


def main():
    nodes = []
    for node in NODES:
        raw = parse(ssh_exec(node["ip"], node["user"], BASIC))
        nodes.append({"ip": node["ip"], "label": node["label"], "role": node["role"], "data": raw})

    # API key
    key = ssh_exec("192.168.10.10", "control",
                   'grep -E "^MINI_DROP_API_KEY=" /home/control/mini-drop-active/deploy/env/control-native.env | cut -d= -f2-').strip()
    agents = get_api(key, "/api/agents")["data"]["items"]
    health = get_api(key, "/api/healthz")["data"]

    # 本地开发版 vs 集群版差异
    local = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    local_has = {
        "process_scan 采集器": os.path.exists(os.path.join(local, "agent/mini_drop_agent/collectors/process_scan.py")),
        "log_scan 采集器": os.path.exists(os.path.join(local, "agent/mini_drop_agent/collectors/log_scan.py")),
        "Actuation 动作网关": os.path.exists(os.path.join(local, "server/app/diagnosis/actuation.py")),
        "恢复验证 API": "verification" in open(os.path.join(local, "server/app/main.py"), encoding="utf-8").read(),
        "多轮引导 next_best_action": "next_best_action" in open(os.path.join(local, "server/app/diagnosis/orchestrator.py"), encoding="utf-8").read(),
        "两页前端（采集与监控）": os.path.exists(os.path.join(local, "web/src/pages/Dashboard.jsx")),
    }

    report = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "health": health,
        "agents": agents,
        "nodes": nodes,
        "local_has": local_has,
        "cluster_active": ssh_exec("192.168.10.10", "control", "readlink -f /home/control/mini-drop-active").strip(),
    }
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cluster_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("collected")


if __name__ == "__main__":
    main()
