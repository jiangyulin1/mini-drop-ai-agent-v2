from scripts.run_smolagents_replay import _normalize_final


def test_normalize_structured_final_answer() -> None:
    value = _normalize_final({"status": "SUFFICIENT", "certainty": "high", "summary": "ok", "claims": []})
    assert value == {"status": "SUFFICIENT", "certainty": "HIGH", "summary": "ok", "claims": []}


def test_normalize_invalid_final_answer_fails_closed() -> None:
    value = _normalize_final("not-json")
    assert value["status"] == "INSUFFICIENT_EVIDENCE"
    assert value["certainty"] == "LOW"
