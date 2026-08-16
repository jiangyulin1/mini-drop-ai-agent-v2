"""Measure real Pi answer repeatability for the same question on one Case.

Uses the same Case and the same Turn text three times.  For each run it waits
until the sidecar settles, then extracts the final assistant text and tool
sequence from the persistent agent_runtime_events table.  Stability is
reported as token-set Jaccard and exact tool-sequence equality.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SSH_CONFIG = ROOT / "ssh" / "vm-config"
OUT = ROOT / "reports" / "implementation" / "vm-pi-repeatability.json"


def ssh(node: str, command: str, timeout: int = 180) -> str:
    proc = subprocess.run(
        ["ssh", "-F", str(SSH_CONFIG), "-o", "BatchMode=yes", node, command],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-800:])
    return proc.stdout


def control_json(path: str, *, method: str = "GET", payload: dict | None = None) -> dict:
    curl = "curl -sk -H \"X-API-Key: $MINI_DROP_API_KEY\""
    if method == "POST":
        curl += " -X POST -H 'Content-Type: application/json' -d '{}'".format(json.dumps(payload, ensure_ascii=False))
    return json.loads(ssh("control", f"source ~/mini-drop-active/deploy/env/control-native.env && {curl} 'https://127.0.0.1{path}'"))


def wait_settled(case_id: str, timeout: int = 150) -> int:
    deadline = time.time() + timeout
    previous = -1
    stable_for = 0
    while time.time() < deadline:
        state = json.loads(ssh(
            "control",
            "source ~/mini-drop-active/deploy/env/control-native.env && "
            f"curl -sS -H \"X-Internal-Token: $MINI_DROP_PI_INTERNAL_TOKEN\" "
            f"'http://127.0.0.1:8899/internal/runtime/v1/cases/{case_id}/state'",
        ))["data"]
        seq = state.get("last_event_seq") or 0
        if state.get("detail"):
            raise RuntimeError(state["detail"])
        if seq == previous and seq > 0:
            stable_for += 1
            if stable_for >= 3:
                return seq
        else:
            stable_for = 0
        previous = seq
        time.sleep(4)
    raise TimeoutError("sidecar did not settle")


def normalize(text: str) -> set[str]:
    lowered = re.sub(r"[\s`*#>\-]+", " ", text.lower())
    ascii_terms = set(re.findall(r"[a-z0-9_.-]{2,}", lowered))
    chinese_runs = re.findall(r"[\u4e00-\u9fff]{2,}", lowered)
    grams: set[str] = set()
    for run in chinese_runs:
        grams.update(run[i:i + 2] for i in range(len(run) - 1))
        grams.update(char for char in run)
    return ascii_terms | grams


def jaccard(a: str, b: str) -> float:
    sa, sb = normalize(a), normalize(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def main() -> int:
    case = control_json("/api/v1/cases", method="POST", payload={
        "title": "pi-repeatability",
        "problem_description": "checkout 服务延迟升高，请先只做解释，不要创建任务",
        "recovery_goal": "定位根因",
        "run_mode": "COLLABORATE",
        "environment": "vm",
        "target_scope": {"service_id": "checkout"},
    })["data"]
    case_id = case["case_id"]
    question = "请基于当前 Case 快照，说明定位 checkout 延迟的稳定诊断步骤；不要创建任何任务，只输出诊断方案。"
    results = []
    for run in range(1, 4):
        control_json(f"/api/v1/cases/{case_id}/agent/turn", method="POST", payload={"message": question})
        wait_settled(case_id)
        time.sleep(5)
        # Query the latest turn_end from DB through a remote python file is simpler than heredoc quoting.
        latest = json.loads(ssh(
            "control",
            "cd ~/mini-drop-release-cand-41f41a04f9-5d44e0e708 && "
            f"CASE={case_id} .venv/bin/python -c 'import sqlite3,sys,json,os; "
            "c=sqlite3.connect(\"/home/control/mini-drop/data/mini_drop.db\"); "
            "case=os.environ[\"CASE\"]; "
            "gen=c.execute(\"select max(runtime_generation) from agent_runtime_events where case_id=?\",(case,)).fetchone()[0]; "
            "rows=c.execute(\"select payload_json from agent_runtime_events where case_id=? and runtime_generation=? and event_type=\\\"turn_end\\\" order by event_seq desc limit 1\",(case,gen)).fetchall(); "
            "p=json.loads(rows[0][0]); m=json.loads(p.get(\"message\") or \"{}\"); "
            "text=\" \".join(x.get(\"text\",\"\") for x in m.get(\"content\",[]) if x.get(\"type\")==\"text\"); "
            "tools=[x.get(\"name\") for x in m.get(\"content\",[]) if x.get(\"type\")==\"toolCall\"]; "
            "print(json.dumps({\"text\":text,\"tools\":tools}))'",
        ))
        results.append(latest)
        print(f"run{run} tools={latest['tools']} text_len={len(latest['text'])}")
        print(latest["text"][:400].replace("\n", " "))
    pair_scores = []
    for i in range(len(results)):
        for j in range(i + 1, len(results)):
            pair_scores.append(jaccard(results[i]["text"], results[j]["text"]))
    tool_sets = [tuple(item["tools"]) for item in results]
    report = {
        "suite": "vm-pi-repeatability",
        "case_id": case_id,
        "question": question,
        "runs": results,
        "pairwise_jaccard": pair_scores,
        "min_jaccard": min(pair_scores) if pair_scores else None,
        "tool_sequence_identical": len(set(tool_sets)) == 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("case_id", "pairwise_jaccard", "tool_sequence_identical", "min_jaccard")}, ensure_ascii=False, indent=2))
    return 0 if report["tool_sequence_identical"] and report["min_jaccard"] is not None and report["min_jaccard"] >= 0.6 else 1


if __name__ == "__main__":
    raise SystemExit(main())
