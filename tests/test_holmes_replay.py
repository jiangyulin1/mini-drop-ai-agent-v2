from scripts.run_holmes_replay import _normalize_final


def test_holmes_final_json_is_normalized() -> None:
    final = _normalize_final('{"status":"SUFFICIENT","certainty":"medium","summary":"ok","claims":[]}')
    assert final["status"] == "SUFFICIENT"
    assert final["certainty"] == "MEDIUM"


def test_holmes_unstructured_final_fails_closed() -> None:
    final = _normalize_final("I think it is CPU")
    assert final["status"] == "INSUFFICIENT_EVIDENCE"
    assert final["claims"] == []
