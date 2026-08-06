"""三节点评测编排：注入故障 → 跑评测 → 清理 → 汇总（从开发机驱动）。"""
import argparse
import json
import os
import sys
import time

import paramiko

PASSWORD = os.getenv("MINI_DROP_VM_PASSWORD", "")
CONTROL = ("192.168.10.10", "control")
WORKER1 = ("192.168.10.11", "worker1")


def ssh(host_user, cmd, timeout=900):
    ip, user = host_user
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(ip, username=user, password=PASSWORD, timeout=15)
    _, out, err = c.exec_command(cmd, timeout=timeout)
    o = out.read().decode("utf-8", "replace")
    e = err.read().decode("utf-8", "replace")
    c.close()
    return o, e


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="catalog-cpu-hotspot,catalog-downstream-pg-down,catalog-host-io-contention,catalog-no-fault-baseline")
    parser.add_argument("--worker", default="linux-worker-1")
    args = parser.parse_args()
    cases = [x.strip() for x in args.cases.split(",") if x.strip()]
    worker = args.worker

    scores = []
    for case in cases:
        print(f"\n{'='*60}\n场景: {case}\n{'='*60}")
        # 1. 注入
        o, e = ssh(WORKER1, f"cd /home/worker1/mini-drop-active && bash benchmarks/github_cases/scripts/inject.sh {case} worker1 2>&1 | tail -3")
        print("[注入]", o.strip()[-150:])
        # 2. 跑评测（control 上）
        cmd = (
            "cd /home/control/mini-drop-active && "
            "export MINI_DROP_API_KEY=$(grep '^MINI_DROP_API_KEY=' deploy/env/control-native.env | cut -d= -f2-) && "
            f"timeout 540 .venv/bin/python benchmarks/github_cases/scripts/run_eval.py "
            f"--server https://127.0.0.1 --api-key \"$MINI_DROP_API_KEY\" "
            f"--worker {worker} --cases {case} --output-dir reports/eval/github-cases 2>&1"
        )
        o, e = ssh(CONTROL, cmd)
        print("[评测]\n", o[-1800:])
        if e.strip():
            print("[stderr]", e[:300])
        # 3. 清理
        o, e = ssh(WORKER1, f"cd /home/worker1/mini-drop-active && bash benchmarks/github_cases/scripts/inject.sh --clean {case} worker1 2>&1 | tail -1")
        print("[清理]", o.strip()[-100:])
        # 场景间隔：等待 sys_metrics 复用窗口过期（MINI_DROP_DIAGNOSIS_REUSE_MAX_AGE_SECONDS=120），
        # 避免跨场景复用上一故障的数据导致误判。
        print("[等待复用窗口过期 135s]...")
        time.sleep(135)
        # 4. 收集分数（每场景独立保存，避免 results.json 被后续场景覆盖）
        o, e = ssh(CONTROL, "cat /home/control/mini-drop-active/reports/eval/github-cases/results.json 2>/dev/null")
        try:
            data = json.loads(o)
            sc = data["scores"][0]
            scores.append({k: sc.get(k) for k in ("case_id", "root_location_match", "domain_cause_match", "evidence_refs_valid", "no_fault_false_positive", "actual_location", "actual_domain", "error")})
            print("[分数]", json.dumps(scores[-1], ensure_ascii=False))
            ssh(CONTROL, f"cp /home/control/mini-drop-active/reports/eval/github-cases/results.json /home/control/mini-drop-active/reports/eval/github-cases/results_{case}.json")
        except Exception as exc:
            print("[分数读取失败]", exc)
        time.sleep(3)

    print(f"\n{'='*60}\n汇总\n{'='*60}")
    for s in scores:
        print(json.dumps(s, ensure_ascii=False))
    with open("reports/eval/github-cases/all_scores.json", "w", encoding="utf-8") as f:
        json.dump(scores, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
