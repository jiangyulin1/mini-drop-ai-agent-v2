from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from server.app.diagnosis import intent as intent_module
from server.app.diagnosis.schemas import CreateDiagnosisRequest


def _request() -> CreateDiagnosisRequest:
    return CreateDiagnosisRequest.model_validate({
        "query": "service-a is slow",
        "context": {
            "service_id": "service-a",
            "environment": "production",
            "instances": [{
                "service_id": "service-a",
                "instance_id": "service-a-1",
                "host_id": "worker1",
                "agent_id": "linux-worker-1",
                "pid": 1234,
                "environment": "production",
            }],
        },
    })


def test_model_cannot_invent_a_stale_default_time_window():
    request = _request()
    parsed = intent_module._fallback_intent(request).model_dump(mode="json")
    parsed["time_range"] = {
        "start": "2025-01-01T00:00:00Z",
        "end": "2025-01-01T01:00:00Z",
        "source": "default_window",
    }
    parsed["diagnosis_mode"] = "HISTORICAL"
    response = mock.MagicMock(status_code=200)
    response.json.return_value = {
        "choices": [{
            "message": {
                "tool_calls": [{
                    "function": {"arguments": __import__("json").dumps(parsed)},
                }],
            },
        }],
    }

    with (
        mock.patch.object(intent_module, "is_feature_enabled", return_value=True),
        mock.patch.object(
            intent_module,
            "get_ai_settings",
            return_value=SimpleNamespace(provider="deepseek", model="test-model"),
        ),
        mock.patch.object(intent_module, "chat_completions", return_value=response),
    ):
        result = intent_module.parse_diagnosis_intent(request)

    now = datetime.now(timezone.utc)
    assert result.diagnosis_mode.value == "LIVE"
    assert result.time_range.source == "default_window"
    assert (now - result.time_range.end).total_seconds() < 5
