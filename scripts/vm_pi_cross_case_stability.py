"""Cross-Case stability: same unresolved issue at different times.

Creates separate Cases with different timestamps in the problem text, confirms
the deterministic directive is identical, then asks the real Pi the same
question in each Case and compares final answer token overlap and required
next-action mentions.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.vm_agent_beta_smoke import control_json, ssh  # noqa: E402
from server.app.diagnosis.investigation_directive import build_directive, normalize_goal  # noqa: E402
from server.app.diagnosis.skill_registry import SKILL_REGISTRY  # noqa: E402

OUT = ROOT / "reports" / "implementation" / "vm-pi-cross-case-stability.json"


def wait_settled(case_id: str, timeout: int = 150):
    deadline = time.time() + timeout
    prev = -1
    stable = 0
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
        if seq == prev and seq > 0:
            stable += 1
            if stable >= 3:
                return seq
        else:
            stable = 0
        prev = seq
        time.sleep(4)
    raise TimeoutError("sidecar did not settle")


def latest_turn_end(case_id: str) -> dict:
    raw = ssh(
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
    )
    return json.loads(raw)


def normalize(text: str) -> set[str]:
    lowered = re.sub(r"[\s`*#>\-]+", " ", text.lower())
    ascii_terms = set(re.findall(r"[a-z0-9_.-]{2,}", lowered))
    grams = set()
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", lowered):
        grams.update(run[i:i + 2] for i in range(len(run) - 1))
        grams.update(char for char in run)
    return ascii_terms | grams


def jaccard(a, b):
    sa, sb = normalize(a), normalize(b)
    return len(sa & sb) / len(sa | sb) if (sa or sb) else 1.0


def main() -> int:
    issues = [
        "2026-08-01 10:00 checkout 服务延迟突然升高，尚未修复",
        "2026-08-09 22:30 checkout 服务延迟突然升高，尚未修复",
        "2026-08-15 04:05 checkout 服务延迟突然升高，尚未修复",
    ]
    skills = SKILL_REGISTRY.select_skills(
        goal=issues[0], target_scope={"service_id": "checkout"},
    )
    directives = [
        build_directive(goal=issue, target_scope={"service_id": "checkout"}, skill_context=skills)
        for issue in issues
    ]
    assert len({d.directive_key for d in directives}) == 1
    expected_next = directives[0].next_action

    question = "请给出定位 checkout 延迟的下一步诊断动作；不要创建任务，不要给出多个方向。"
    results = []
    for issue in issues:
        case = control_json("/api/v1/cases", method="POST", payload={
            "title": "cross-case-stability",
            "problem_description": issue,
            "recovery_goal": "定位根因",
            "run_mode": "COLLABORATE",
            "environment": "vm",
            "target_scope": {"service_id": "checkout"},
        })["data"]
        control_json(
            f"/api/v1/cases/{case['case_id']}/agent/turn", method="POST",
            payload={"message": question},
        )
        wait_settled(case["case_id"])
        time.sleep(5)
        latest = latest_turn_end(case["case_id"])
        results.append({"case_id": case["case_id"], **latest})
        print(f"{case['case_id']} tools={latest['tools']} text_len={len(latest['text'])}")
        print(latest["text"][:300].replace("\n", " "))

    pair_scores = [
        jaccard(results[i]["text"], results[j]["text"])
        for i in range(len(results)) for j in range(i + 1, len(results))
    ]
    tool_sets = [tuple(item["tools"]) for item in results]
    mention_next = [expected_next.split(" ", 1)[1] in item["text"] for item in results]
    report = {
        "suite": "vm-pi-cross-case-stability",
        "directive_key": directives[0].directive_key,
        "normalized_goals": [normalize_goal(issue) for issue in issues],
        "expected_next_action": expected_next,
        "pairwise_jaccard": pair_scores,
        "min_jaccard": min(pair_scores),
        "tool_sequence_identical": len(set(tool_sets)) == 1,
        "next_action_mentioned": mention_next,
        "runs": results,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    report["direction_consistent"] = (
        report["tool_sequence_identical"] and all(mention_next)
    )
    print(json.dumps({
        "directive_key": report["directive_key"],
        "expected_next_action": expected_next,
        "pairwise_jaccard": pair_scores,
        "min_jaccard": report["min_jaccard"],
        "tool_sequence_identical": report["tool_sequence_identical"],
        "next_action_mentioned": mention_next,
        "direction_consistent": report["direction_consistent"],
    }, ensure_ascii=False, indent=2))
    return 0 if report["direction_consistent"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
