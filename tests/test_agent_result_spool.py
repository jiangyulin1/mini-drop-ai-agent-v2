from agent.mini_drop_agent.result_spool import ResultSpool


def test_result_spool_survives_reopen_and_acknowledges(tmp_path):
    spool = ResultSpool(str(tmp_path))
    spool.save(
        "task_001",
        True,
        "done",
        [{"artifact_type": "top_json", "sha256": "a" * 64}],
    )

    reopened = ResultSpool(str(tmp_path))
    pending = reopened.pending()

    assert pending[0]["task_id"] == "task_001"
    assert pending[0]["ok"] is True
    reopened.acknowledge("task_001")
    assert reopened.pending() == []


def test_result_spool_quarantines_invalid_json(tmp_path):
    (tmp_path / "broken.json").write_text("{not-json", encoding="utf-8")
    spool = ResultSpool(str(tmp_path))

    assert spool.pending() == []
    assert (tmp_path / "broken.corrupt").exists()


def test_result_spool_keeps_attempt_identity_and_cancel_outcome(tmp_path):
    spool = ResultSpool(str(tmp_path))
    spool.save(
        "task-1",
        False,
        "operator cancelled",
        [],
        attempt_id="attempt-2",
        cancelled=True,
        exit_code=-15,
        error_code="TASK_CANCELLED",
        traceparent="00-11111111111111111111111111111111-2222222222222222-01",
    )

    pending = spool.pending()
    assert pending[0]["attempt_id"] == "attempt-2"
    assert pending[0]["cancelled"] is True
    assert pending[0]["exit_code"] == -15
    assert pending[0]["error_code"] == "TASK_CANCELLED"
    assert pending[0]["traceparent"] == (
        "00-11111111111111111111111111111111-2222222222222222-01"
    )
    spool.acknowledge("task-1", "attempt-2")
    assert spool.pending() == []
