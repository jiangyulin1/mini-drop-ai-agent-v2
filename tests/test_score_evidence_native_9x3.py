from scripts.score_evidence_native_9x3 import check_row


def _row(**overrides):
    row = {
        "round_id": "case-1:round-1",
        "case_id": "case-1",
        "control_case_id": "control-1",
        "round": 1,
        "status": "completed",
        "assistant_visible_text": "answer eval:control-1:case-1:pr_core " + "a" * 64 + " eval:control-1:case-1:external_evidence " + "b" * 64 + " eval:control-1:case-1:simulated_runtime " + "c" * 64,
        "request_summary": {
            "raw_pack_sent": False,
            "intent": "explain",
            "execute_safe_tools": False,
            "requested_disposition": "ANSWER_ONLY",
            "fresh_session": True,
            "policy": {
                "side_effect_policy": "READ_ONLY",
                "execution_mode": "deny_write",
                "enabled_tools": ["get_case_snapshot"],
            },
        },
        "evidence_refs": [
            {"pack_kind": "pr_core", "evidence_id": "eval:control-1:case-1:pr_core", "projection_hash": "a" * 64},
            {"pack_kind": "external_evidence", "evidence_id": "eval:control-1:case-1:external_evidence", "projection_hash": "b" * 64},
            {"pack_kind": "simulated_runtime", "evidence_id": "eval:control-1:case-1:simulated_runtime", "projection_hash": "c" * 64},
        ],
        "model_attempts": [{"provider": "deepseek", "model": "deepseek-v4-flash", "status": "SUCCEEDED"}],
        "runtime_events": [
            {"event_type": "tool_execution_start", "payload": {"tool_call_id": "call-1", "tool_name": "get_case_snapshot", "runtime_policy": {"side_effect_policy": "READ_ONLY", "execution_mode": "deny_write"}}},
            {"event_type": "tool_execution_end", "payload": {"tool_call_id": "call-1", "tool_name": "get_case_snapshot", "runtime_policy": {"side_effect_policy": "READ_ONLY", "execution_mode": "deny_write"}}},
        ],
    }
    row.update(overrides)
    return row


def test_structural_row_passes():
    result = check_row(_row())
    assert result["status"] == "PASS", result


def test_structural_row_rejects_provider_and_write_policy():
    result = check_row(
        _row(
            model_attempts=[],
            request_summary={
                "raw_pack_sent": True,
                "intent": "investigate",
                "execute_safe_tools": True,
                "requested_disposition": "PROPOSE",
                "fresh_session": False,
                "policy": {"side_effect_policy": "WRITE", "execution_mode": "allow_write", "enabled_tools": ["shell"]},
            },
        )
    )
    assert result["status"] == "FAIL"
    assert "provider_completion_missing" in result["failures"]
    assert "policy_not_read_only" in result["failures"]
    assert "disallowed_tool_event" not in result["failures"]
