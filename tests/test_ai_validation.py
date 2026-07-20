"""Drop AI validation suite tests."""

from unittest import mock

import pytest

from server.app import ai_validation


def _passing_result():
    return True, "ok", {"safe": True}


def test_validation_suite_returns_safe_aggregate(monkeypatch):
    monkeypatch.setenv("MINI_DROP_AI_API_KEY", "secret-must-not-leak")
    monkeypatch.setenv("MINI_DROP_AI_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("MINI_DROP_AI_ENABLED", "full")
    for name in (
        "_check_configuration", "_check_balance", "_check_model_discovery",
        "_check_chat_completion", "_check_nlp_intent", "_check_cluster_intent",
        "_check_summary", "_check_rca",
    ):
        monkeypatch.setattr(ai_validation, name, _passing_result)

    result = ai_validation.run_ai_validation_suite()

    assert result["status"] == "PASSED"
    assert result["passed_count"] == result["total_count"] == 8
    assert result["security"]["api_key_exposed"] is False
    assert "secret-must-not-leak" not in str(result)


def test_safe_check_hides_exception_message():
    def fail():
        raise RuntimeError("secret response body")

    result = ai_validation._safe_check("x", "name", "layer", fail)
    assert result["status"] == "FAIL"
    assert result["metrics"]["error_type"] == "RuntimeError"
    assert "secret response body" not in str(result)


def test_concurrent_validation_is_rejected():
    assert ai_validation._validation_lock.acquire(blocking=False)
    try:
        with pytest.raises(ai_validation.AIValidationBusy):
            ai_validation.run_ai_validation_suite()
    finally:
        ai_validation._validation_lock.release()
