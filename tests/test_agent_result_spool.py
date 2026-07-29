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
