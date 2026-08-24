#!/usr/bin/env python3
"""Score structural gates for the Evidence-native 9x3 live report.

This scorer intentionally does not infer RCA quality from words in an answer.
It verifies only the durable chain required by the 9x3 contract: 27 completed
turns, real provider attempts, canonical Evidence references, auditable read-only
tool execution, and independent round identities. Mechanism and uncertainty
quality remain human/oracle-scored.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ALLOWED_TOOLS = {
    "get_case_snapshot",
    "list_case_evidence",
    "get_evidence_projection",
    "compare_evidence",
    "get_causal_graph",
    "get_evidence_gaps",
}
EXPECTED_PACK_KINDS = {"pr_core", "external_evidence", "simulated_runtime"}


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def check_row(row: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    request = row.get("request_summary") or {}
    refs = row.get("evidence_refs") or []
    attempts = row.get("model_attempts") or []
    events = row.get("runtime_events") or []
    if row.get("status") != "completed":
        failures.append("round_not_completed")
    if not row.get("assistant_visible_text"):
        failures.append("assistant_answer_missing")
    if request.get("raw_pack_sent") is not False:
        failures.append("raw_pack_sent_or_unrecorded")
    if request.get("intent") != "explain":
        failures.append("intent_not_explain")
    if request.get("execute_safe_tools") is not False:
        failures.append("safe_tool_execution_not_disabled")
    if request.get("requested_disposition") != "ANSWER_ONLY":
        failures.append("disposition_not_answer_only")
    if request.get("fresh_session") is not True:
        failures.append("fresh_session_not_applied")
    policy = request.get("policy") or {}
    if policy.get("side_effect_policy") != "READ_ONLY":
        failures.append("policy_not_read_only")
    if policy.get("execution_mode") != "deny_write":
        failures.append("policy_not_deny_write")
    if set(policy.get("enabled_tools") or []) - ALLOWED_TOOLS:
        failures.append("policy_contains_unknown_tool")
    if {str(item.get("pack_kind")) for item in refs} != EXPECTED_PACK_KINDS:
        failures.append("evidence_pack_kinds_incomplete")
    if len(refs) != 3 or len({str(item.get("evidence_id")) for item in refs}) != 3:
        failures.append("evidence_reference_count_or_uniqueness")
    case_id = str(row.get("control_case_id") or "")
    for item in refs:
        evidence_id = str(item.get("evidence_id") or "")
        projection_hash = str(item.get("projection_hash") or "")
        if not evidence_id.startswith(f"eval:{case_id}:"):
            failures.append("evidence_id_not_case_bound")
        if len(projection_hash) != 64 or any(ch not in "0123456789abcdef" for ch in projection_hash.lower()):
            failures.append("projection_hash_invalid")
        answer = str(row.get("assistant_visible_text") or "")
        if evidence_id not in answer:
            failures.append("answer_missing_canonical_evidence_id")
        if projection_hash not in answer:
            failures.append("answer_missing_full_projection_hash")
    successful_attempts = [
        item for item in attempts
        if item.get("provider") == "deepseek"
        and item.get("model") == "deepseek-v4-flash"
        and item.get("status") == "SUCCEEDED"
    ]
    if not successful_attempts:
        failures.append("provider_completion_missing")
    starts = {
        str(item.get("payload", {}).get("tool_call_id"))
        for item in events if item.get("event_type") == "tool_execution_start"
    }
    ends = {
        str(item.get("payload", {}).get("tool_call_id"))
        for item in events if item.get("event_type") == "tool_execution_end"
    }
    if not starts or starts != ends:
        failures.append("tool_event_pairing")
    for item in events:
        if item.get("event_type") not in {"tool_execution_start", "tool_execution_end"}:
            continue
        payload = item.get("payload") or {}
        tool_name = str(payload.get("tool_name") or "")
        if tool_name not in ALLOWED_TOOLS:
            failures.append("disallowed_tool_event")
        policy_payload = payload.get("runtime_policy") or {}
        if policy_payload.get("side_effect_policy") != "READ_ONLY":
            failures.append("tool_event_policy_not_read_only")
        if policy_payload.get("execution_mode") != "deny_write":
            failures.append("tool_event_policy_not_deny_write")
    return {
        "round_id": row.get("round_id"),
        "case_id": row.get("case_id"),
        "round": row.get("round"),
        "status": "PASS" if not failures else "FAIL",
        "failures": sorted(set(failures)),
        "provider_attempt_count": len(successful_attempts),
        "tool_start_count": len(starts),
        "tool_end_count": len(ends),
        "evidence_ref_count": len(refs),
        "answer_chars": len(str(row.get("assistant_visible_text") or "")),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = load_rows(args.results)
    checks = [check_row(row) for row in rows]
    keys = [str(row.get("round_id") or "") for row in rows]
    duplicate_round_ids = sorted({key for key in keys if key and keys.count(key) > 1})
    expected = 9 * 3
    summary = {
        "schema": "mini-drop.evidence-native-9x3.structural-score.v1",
        "expected_round_count": expected,
        "observed_round_count": len(rows),
        "duplicate_round_ids": duplicate_round_ids,
        "completed_rounds": sum(item["status"] == "PASS" for item in checks),
        "failed_rounds": sum(item["status"] == "FAIL" for item in checks),
        "structural_status": "PASS" if len(rows) == expected and not duplicate_round_ids and all(item["status"] == "PASS" for item in checks) else "FAIL",
        "quality_score": None,
        "quality_score_note": "RCA mechanism and uncertainty quality are intentionally not inferred by this scorer.",
        "rounds": checks,
    }
    output = args.output or args.results.with_name("structural-score.json")
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "structural_status": summary["structural_status"],
        "expected_round_count": expected,
        "observed_round_count": len(rows),
        "completed_rounds": summary["completed_rounds"],
        "failed_rounds": summary["failed_rounds"],
        "output": str(output),
    }, ensure_ascii=False))
    return 0 if summary["structural_status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
