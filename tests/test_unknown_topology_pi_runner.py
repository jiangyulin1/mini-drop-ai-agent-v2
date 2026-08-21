import sqlite3

from scripts.run_unknown_topology_pi_e2e import (
    _assistant_message,
    _model_completion_audit,
    _runtime_event_audit,
    _sqlite_conclusion_count,
)


def test_assistant_message_decodes_pi_envelope_and_skips_user_reminder():
    events = [
        {
            "event_type": "turn_end",
            "payload": {
                "message": '{"role":"assistant","content":[{"type":"text","text":"真实拓扑结论"},{"type":"toolCall","name":"finish_investigation"}]}'
            },
        },
        {
            "event_type": "message_end",
            "payload": {
                "message": '{"role":"user","content":[{"type":"text","text":"terminal reminder"}]}'
            },
        },
    ]

    assert _assistant_message(events) == "真实拓扑结论"


def test_assistant_message_prefers_latest_visible_assistant_text():
    events = [
        {"event_type": "turn_end", "payload": {"visible_text": "first"}},
        {"event_type": "turn_end", "payload": {"visible_text": "verified final"}},
    ]

    assert _assistant_message(events) == "verified final"


def _completion_event(turn_id: str, seq: int) -> dict:
    return {
        "event_seq": seq,
        "event_type": "message_end",
        "payload": {
            "trigger_turn_id": turn_id,
            "visible_text": "answer",
            "has_tool_calls": False,
            "model_attempt": {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "status": "SUCCEEDED",
                "turn_id": turn_id,
                "input_tokens": 100,
                "output_tokens": 20,
                "response_hash": f"{seq:064x}",
            },
        },
    }


def test_model_completion_audit_keeps_terminal_reminder_separate():
    main_turn = "turn-case-1"
    reminder_turn = "turn-case-1-terminal-2"
    events = [
        _completion_event(main_turn, 1),
        {"event_type": "agent_start", "payload": {"trigger_turn_id": reminder_turn}},
        _completion_event(reminder_turn, 2),
        {
            "event_type": "tool_execution_end",
            "payload": {
                "trigger_turn_id": reminder_turn,
                "tool_name": "finish_investigation",
            },
        },
        {"event_type": "agent_settled", "payload": {"trigger_turn_id": reminder_turn}},
    ]

    audit = _model_completion_audit(events, main_turn)

    assert audit["main_turn_real_completion"] is True
    assert audit["main_turn_completion_count"] == 1
    assert audit["terminal_reminder_started"] is True
    assert audit["terminal_reminder_resolved"] is True
    assert audit["terminal_reminders"][0]["real_completion_count"] == 1
    assert audit["terminal_reminders"][0]["finish_investigation_completed"] is True


def test_model_completion_audit_rejects_unfinished_terminal_reminder():
    main_turn = "turn-case-1"
    reminder_turn = "turn-case-1-terminal-2"
    events = [
        _completion_event(main_turn, 1),
        {"event_type": "agent_start", "payload": {"trigger_turn_id": reminder_turn}},
        {"event_type": "message_start", "payload": {"trigger_turn_id": reminder_turn}},
    ]

    audit = _model_completion_audit(events, main_turn)

    assert audit["main_turn_real_completion"] is True
    assert audit["terminal_reminder_started"] is True
    assert audit["terminal_reminder_resolved"] is False


def test_runtime_event_audit_requires_contiguous_closed_lifecycle():
    turn_id = "turn-case-1"
    events = [
        {
            "event_seq": 1,
            "event_id": "evt-1",
            "idempotency_key": "key-1",
            "event_type": "agent_start",
            "payload": {"trigger_turn_id": turn_id},
        },
        {
            "event_seq": 2,
            "event_id": "evt-2",
            "idempotency_key": "key-2",
            "event_type": "turn_start",
            "payload": {"trigger_turn_id": turn_id},
        },
        {
            "event_seq": 3,
            "event_id": "evt-3",
            "idempotency_key": "key-3",
            "event_type": "turn_end",
            "payload": {"trigger_turn_id": turn_id},
        },
        {
            "event_seq": 4,
            "event_id": "evt-4",
            "idempotency_key": "key-4",
            "event_type": "agent_settled",
            "payload": {"trigger_turn_id": turn_id},
        },
    ]

    audit = _runtime_event_audit(events)

    assert audit["seq_contiguous"] is True
    assert audit["event_ids_unique"] is True
    assert audit["idempotency_keys_unique"] is True
    assert audit["no_unfinished_turns"] is True

    broken = _runtime_event_audit(events[:2] + events[3:])
    assert broken["seq_contiguous"] is False
    assert broken["no_unfinished_turns"] is False


def test_sqlite_conclusion_count_only_returns_row_count(tmp_path):
    database = tmp_path / "audit.sqlite"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE conclusion_revisions (conclusion_id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO conclusion_revisions VALUES ('conclusion-secret-free')")
        connection.commit()
    finally:
        connection.close()

    assert _sqlite_conclusion_count(database) == 1
