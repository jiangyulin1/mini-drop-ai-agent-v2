"""Focused contracts for the low-bandwidth GitHub PR live runner."""

from scripts.run_github_pr_live_eval import (
    bind_pack_set_to_case,
    current_event_seq,
    hash_value,
    import_packs_once,
    pending_imports,
    redact_runtime_event,
    write_blocked_live_report,
)


def _pack_set():
    return {
        "pr_core": {
            "projection": {
                "schema": "mini-drop.github-pr.evaluation-projection.v1",
                "case_id": "grafana-123359",
                "pack_kind": "pr_core",
                "records": [{
                    "evidence_id": "ghpr:grafana-123359:pr_core:source",
                    "projection_hash": "source-projection-hash",
                    "field_path": "github.title",
                }],
            },
            "projection_hash": "old-hash",
            "projected_bytes": 1,
            "pack_bytes": 2,
            "synthetic": False,
            "evidence_id": "eval:grafana-123359:pr_core",
            "source_id": "github-pr:grafana-123359:pr_core",
            "source_ref": "github://grafana/grafana/pull/123359",
        },
    }


def test_bind_pack_set_scopes_id_and_hash_to_control_case():
    bound = bind_pack_set_to_case(
        _pack_set(), control_case_id="case-server-1", source_case_id="grafana-123359",
    )
    item = bound["pr_core"]
    assert item["evidence_id"] == "eval:case-server-1:grafana-123359:pr_core"
    assert item["projection"]["case_id"] == "case-server-1"
    assert item["projection"]["source_case_id"] == "grafana-123359"
    record = item["projection"]["records"][0]
    assert record["evidence_id"] == item["evidence_id"]
    assert record["source_evidence_id"] == "ghpr:grafana-123359:pr_core:source"
    assert record["source_projection_hash"] == "source-projection-hash"
    assert "projection_hash" not in record
    assert item["projection_hash"] == hash_value(item["projection"])


class _FakeClient:
    def __init__(self):
        self.calls = []

    def request(self, method, path, *, payload=None, phase=None, eval_import=False, **kwargs):
        self.calls.append({
            "method": method,
            "path": path,
            "payload": payload,
            "phase": phase,
            "eval_import": eval_import,
        })
        return {"data": {"evidence_id": payload["evidence_id"]}}


class _EventCursorClient:
    def __init__(self, items):
        self.items = items
        self.calls = []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return {"data": {"items": self.items}}


def test_current_event_seq_uses_high_water_mark_not_oldest_event():
    client = _EventCursorClient([
        {"case_event_seq": 4},
        {"case_event_seq": 17},
        {"case_event_seq": 9},
    ])
    assert current_event_seq(client, "case-1") == 17
    assert "latest=true" in client.calls[0][1]


def test_import_uses_rebound_id_and_reuses_receipt():
    client = _FakeClient()
    state = {"cases": {"grafana-123359": {}}, "rounds": {}}
    bound, imported = import_packs_once(
        client,
        "case-server-1",
        _pack_set(),
        state,
        "grafana-123359",
    )
    item = bound["pr_core"]
    assert imported["pr_core"]["evidence_id"] == item["evidence_id"]
    assert client.calls[0]["path"].endswith("/cases/case-server-1/evidence/import")
    assert client.calls[0]["payload"]["evidence_id"].startswith("eval:case-server-1:")
    assert client.calls[0]["payload"]["projection_hash"] == hash_value(
        client.calls[0]["payload"]["projection"]
    )

    # A second invocation with the same Case/hash must not upload again.
    import_packs_once(client, "case-server-1", _pack_set(), state, "grafana-123359")
    assert len(client.calls) == 1


def test_pending_imports_allows_revoked_token_after_receipts():
    pack_set = _pack_set()
    bound = bind_pack_set_to_case(
        pack_set, control_case_id="case-server-1", source_case_id="grafana-123359",
    )
    item = bound["pr_core"]
    state = {
        "cases": {
            "grafana-123359": {
                "control_case_id": "case-server-1",
                "imports": {
                    "pr_core": {
                        "evidence_id": item["evidence_id"],
                        "projection_hash": item["projection_hash"],
                    },
                },
            },
        },
        "rounds": {},
    }
    assert pending_imports({"grafana-123359": pack_set}, state) == []


def test_pending_imports_requires_token_for_missing_or_stale_receipt():
    pack_set = _pack_set()
    state = {
        "cases": {
            "grafana-123359": {
                "control_case_id": "case-server-1",
                "imports": {
                    "pr_core": {
                        "evidence_id": "eval:case-server-1:grafana-123359:pr_core",
                        "projection_hash": "stale-hash",
                    },
                },
            },
        },
        "rounds": {},
    }
    assert pending_imports({"grafana-123359": pack_set}, state) == ["grafana-123359:pr_core"]


def test_runtime_event_redacts_model_visible_context_but_keeps_audit_metadata():
    event = {
        "event_type": "message_start",
        "payload": {
            "message": {"role": "user", "content": "private case context"},
            "trigger_turn_id": "turn-1",
        },
    }
    sanitized = redact_runtime_event(event)
    payload = sanitized["payload"]
    assert payload["message"] == "[REDACTED_RUNTIME_MESSAGE]"
    assert payload["message_bytes"] > 0
    assert len(payload["message_sha256"]) == 64
    assert payload["trigger_turn_id"] == "turn-1"
    assert event["payload"]["message"]["content"] == "private case context"


def test_blocked_smoke_report_records_one_unscored_row(tmp_path):
    rows = write_blocked_live_report(
        tmp_path,
        [{"case_id": "grafana-123359"}],
        {"grafana-123359": _pack_set()},
        ["provider_key_missing"],
        rounds=1,
    )
    assert len(rows) == 1
    assert rows[0]["status"] == "blocked"
    assert rows[0]["manual_score"] is None
    assert rows[0]["request_summary"]["model_turn_sent"] is False
    summary = (tmp_path / "summary.json").read_text(encoding="utf-8")
    assert '"round_count": 1' in summary
    assert '"automatic_score": null' in summary
