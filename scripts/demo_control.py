"""Mini-Drop 现场演示控制台（Windows 端运行）。
一键完成：环境健康检查 / 注入故障 / 清理故障 / 查看状态。

用法示例：
    python scripts/demo_control.py check          # 健康检查
    python scripts/demo_control.py inject cpu     # 注入 CPU 热点
    python scripts/demo_control.py inject pg      # 注入下游故障(停 PostgreSQL)
    python scripts/demo_control.py inject io      # 注入磁盘争抢
    python scripts/demo_control.py clean          # 清理全部故障并恢复
    python scripts/demo_control.py watch          # 实时查看诊断/任务状态
"""
import argparse
import json
import os
import shlex
import ssl
import sys
import time
import urllib.request

import paramiko

PASSWORD = os.getenv("MINI_DROP_VM_PASSWORD", "")
CONTROL = ("192.168.10.10", "control")
WORKER1 = ("192.168.10.11", "worker1")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def ssh(host_user, cmd, timeout=180):
    ip, user = host_user
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(ip, username=user, password=PASSWORD, timeout=15)
    _, out, err = c.exec_command(cmd, timeout=timeout)
    o = out.read().decode("utf-8", "replace")
    e = err.read().decode("utf-8", "replace")
    c.close()
    return o, e


def api_key():
    o, _ = ssh(CONTROL, "grep '^MINI_DROP_API_KEY=' /home/control/mini-drop-active/deploy/env/control-native.env | cut -d= -f2-")
    return o.strip()


def api(path, method="GET", body=None):
    key = api_key()
    req = urllib.request.Request(f"https://192.168.10.10{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("X-API-Key", key)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=30) as r:
            return json.loads(r.read().decode()).get("data")
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "detail": e.read().decode()[:200]}


def cmd_check():
    print("=" * 60)
    print("Mini-Drop 演示环境健康检查")
    print("=" * 60)
    o, _ = ssh(CONTROL, "systemctl is-active mini-drop-server mini-drop-analyzer mini-drop-s3 nginx")
    print("[control] server/analyzer/s3/nginx:", o.strip().replace("\n", " | "))
    o, _ = ssh(WORKER1, "systemctl is-active mini-drop-agent postgresql; pg_isready 2>&1; ss -tln | grep 3550 >/dev/null && echo 'product-catalog:3550 LISTEN' || echo 'product-catalog: DOWN'")
    print("[worker1 ] agent/pg:", o.strip().replace("\n", " | "))
    o, _ = ssh(("192.168.10.12", "worker2"), "systemctl is-active mini-drop-agent")
    print("[worker2 ] agent:", o.strip())
    h = api("/api/healthz")
    print("[API    ] healthy:", h.get("healthy"), "| db:", h.get("checks", {}).get("database", {}).get("status"),
          "| storage:", h.get("checks", {}).get("storage", {}).get("status"))
    agents = api("/api/agents")
    for a in (agents or {}).get("items", []):
        if a["id"].startswith("linux-worker"):
            print(f"[Agent  ] {a['id']}: {a['status']} | 能力 {len(a['capabilities'])} 种")
    o, _ = ssh(WORKER1, "pgrep -af 'eval-load|io-storm|fio' | grep -v grep || echo '无残留压测'")
    print("[残留   ]", o.strip())


def cmd_inject(scenario):
    if scenario not in {"cpu", "pg", "io"}:
        print("未知场景，可选 cpu / pg / io"); return
    case = {"cpu": "catalog-cpu-hotspot", "pg": "catalog-downstream-pg-down", "io": "catalog-host-io-contention"}[scenario]
    print(f"==> 注入故障：{case}")
    o, e = ssh(WORKER1, f"cd /home/worker1/mini-drop-active && bash benchmarks/github_cases/scripts/inject.sh {case} worker1 2>&1 | tail -2")
    print(o.strip())
    print("==> 注入完成，请到浏览器 https://192.168.10.10/ 的「AI 诊断」页新建 Case")


def cmd_clean():
    print("==> 清理全部故障并恢复")
    sudo_password = shlex.quote(PASSWORD)
    o, _ = ssh(WORKER1, f"pkill -f eval-load 2>/dev/null; pkill -f io-storm 2>/dev/null; pkill -f fio 2>/dev/null; rm -f /tmp/io-storm; printf '%s\\n' {sudo_password} | sudo -S systemctl start postgresql 2>&1 | tail -1; pg_isready")
    print("worker1:", o.strip()[-60:])
    o, _ = ssh(("192.168.10.12", "worker2"), "pkill -f eval-load 2>/dev/null; true")
    print("worker2: 已清理")
    print("==> 环境已恢复（product-catalog 正常运行，PostgreSQL 可用）")


def cmd_watch():
    print("==> 最近诊断状态（每 5 秒刷新，Ctrl+C 退出）")
    try:
        while True:
            diags = api("/api/v1/diagnoses?limit=5")
            items = diags if isinstance(diags, list) else (diags or {}).get("items", [])
            os_clear = "\033[2J\033[H"
            print(os_clear + "最近诊断：")
            for x in items:
                ni = x.get("normalized_intent") or {}
                c = x.get("latest_conclusion") or {}
                cls = (c.get("cluster_assessment") or {}).get("classification", "-")
                print(f"  {x.get('diagnosis_id','')[-16:]} | {ni.get('symptom','?'):22s} | {x.get('status','?'):24s} | {cls}")
            time.sleep(5)
    except KeyboardInterrupt:
        print("\n已停止")


def main():
    parser = argparse.ArgumentParser(description="Mini-Drop 演示控制台")
    parser.add_argument("action", choices=["check", "inject", "clean", "watch"])
    parser.add_argument("scenario", nargs="?", default="", help="inject 用：cpu / pg / io")
    args = parser.parse_args()
    {"check": cmd_check, "inject": lambda: cmd_inject(args.scenario), "clean": cmd_clean, "watch": cmd_watch}[args.action]()


if __name__ == "__main__":
    main()
