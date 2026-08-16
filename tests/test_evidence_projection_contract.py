from server.app.diagnosis.evidence_projection import build_evidence_projection


def test_projection_is_deterministic_versioned_and_bounded():
    metadata = {
        "cpu_percent": 91.234567,
        "window_start": "2026-08-16T10:00:00Z",
        "window_end": "2026-08-16T10:00:15Z",
        "samples": [{"timestamp": index, "cpu_percent": index} for index in range(100)],
        "logs": [{"message": f"line-{index}"} for index in range(100)],
    }
    first = build_evidence_projection("sys_metrics", metadata, source_bytes=900_000)
    second = build_evidence_projection("sys_metrics", metadata, source_bytes=900_000)
    assert first["projection_hash"] == second["projection_hash"]
    assert first["projection_schema"] == "evidence-projection.v1"
    assert first["projection_version"] == 1
    assert first["truncated"] is True
    assert len(first["content"]["samples"]) == 20
    assert len(first["content"]["log_events"]) == 12
    assert first["content"]["signals"]["cpu_percent"] == 91.2346
