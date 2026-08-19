import json

import pytest

from server.app.diagnosis import evidence_projection
from server.app.diagnosis.evidence_projection import (
    build_evidence_projection,
    project_artifact,
)


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


def _process_scan_artifact() -> dict:
    """A process_scan artifact whose detail rows only exist in object storage."""
    return {
        "id": 53,
        "artifact_type": "process_scan",
        "bucket": "mini-drop",
        "object_key": "tasks/t1/attempts/a1/process_scan.json",
        "size_bytes": 47_106,
        # Upload-time metadata carries scalars only — no per-process rows.
        "metadata": {"schema_version": "process_scan.v1", "process_count": 119},
    }


def _stored_process_scan_body() -> dict:
    return {
        "schema_version": "process_scan.v1",
        "ncpu": 2,
        "process_count": 119,
        "processes": [
            {"pid": 34034, "comm": "python3", "cpu_percent": 103.3, "rss_mb": 10.3},
            *(
                {"pid": 1000 + index, "comm": f"proc-{index}", "cpu_percent": 0.0}
                for index in range(118)
            ),
        ],
    }


def test_project_artifact_reads_detail_rows_from_object_storage(monkeypatch):
    """Regression: projecting metadata alone dropped every detail row.

    The collector stores 119 processes (the top one burning 103% CPU) in the
    object body while metadata only keeps ``process_count``.  Projecting
    metadata produced ``top_items == []``, so the investigating model could
    never see the offending process and had to abstain.
    """
    calls: list[tuple[str, str]] = []

    def fake_read(bucket: str, object_key: str) -> bytes:
        calls.append((bucket, object_key))
        return json.dumps(_stored_process_scan_body()).encode("utf-8")

    monkeypatch.setattr("server.app.storage.read_object_bytes", fake_read)

    projection = project_artifact(_process_scan_artifact())
    top_items = projection["content"]["top_items"]

    assert calls == [("mini-drop", "tasks/t1/attempts/a1/process_scan.json")]
    assert len(top_items) == 10
    assert top_items[0]["pid"] == 34034
    assert top_items[0]["cpu_percent"] == 103.3
    assert projection["content"]["signals"]["process_count"] == 119.0


def test_project_artifact_falls_back_to_metadata_when_storage_unavailable(monkeypatch):
    """Storage outages degrade the projection instead of losing the evidence."""

    def failing_read(bucket: str, object_key: str) -> bytes:
        raise OSError("object storage unreachable")

    monkeypatch.setattr("server.app.storage.read_object_bytes", failing_read)

    projection = project_artifact(_process_scan_artifact())

    assert projection["content"]["top_items"] == []
    assert projection["content"]["signals"]["process_count"] == 119.0


def test_project_artifact_skips_oversized_objects(monkeypatch):
    """Bodies past the fetch ceiling stay reachable only through raw_ref."""

    def unexpected_read(bucket: str, object_key: str) -> bytes:
        raise AssertionError("oversized objects must not be pulled into memory")

    monkeypatch.setattr("server.app.storage.read_object_bytes", unexpected_read)

    artifact = _process_scan_artifact()
    artifact["size_bytes"] = evidence_projection.MAX_RAW_FETCH_BYTES + 1

    projection = project_artifact(artifact)

    assert projection["content"]["top_items"] == []


@pytest.mark.parametrize("body", ["[1, 2, 3]", "not-json"])
def test_project_artifact_rejects_non_object_bodies(monkeypatch, body):
    """A non-dict body is untrusted input, not a projection source."""

    monkeypatch.setattr(
        "server.app.storage.read_object_bytes",
        lambda bucket, object_key: body.encode("utf-8"),
    )

    projection = project_artifact(_process_scan_artifact())

    assert projection["content"]["top_items"] == []
    assert projection["content"]["signals"]["process_count"] == 119.0
