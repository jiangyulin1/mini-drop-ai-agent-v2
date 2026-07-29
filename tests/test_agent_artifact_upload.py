from unittest import mock
import hashlib

from agent.mini_drop_agent.artifact_upload import maybe_upload_artifacts
from agent.mini_drop_agent.config import AgentConfig


def _config(upload=True):
    return AgentConfig(
        agent_id="agent",
        server_grpc_addr="server:50051",
        agent_ip_addr="10.0.0.2",
        upload_artifacts=upload,
        minio_endpoint="minio:9000",
        minio_access_key="ak",
        minio_secret_key="sk",
        minio_bucket="mini-drop",
    )


def test_upload_disabled_keeps_artifacts(tmp_path):
    path = tmp_path / "perf.data"
    path.write_text("perf", encoding="utf-8")
    artifact = {"artifact_type": "raw", "local_path": str(path)}
    result = maybe_upload_artifacts("task1", [artifact], _config(upload=False))
    assert result[0]["size_bytes"] == 4
    assert result[0]["sha256"] == hashlib.sha256(b"perf").hexdigest()


def test_upload_adds_bucket_and_object_key(tmp_path):
    path = tmp_path / "perf.data"
    path.write_text("perf", encoding="utf-8")
    artifact = {
        "artifact_type": "raw",
        "filename": "perf.data",
        "local_path": str(path),
        "content_type": "application/octet-stream",
    }

    with mock.patch("agent.mini_drop_agent.artifact_upload._minio_client") as mock_client:
        uploaded = maybe_upload_artifacts("task1", [artifact], _config())

    assert uploaded[0]["bucket"] == "mini-drop"
    assert uploaded[0]["object_key"] == "tasks/task1/perf.data"
    assert uploaded[0]["size_bytes"] == 4
    assert uploaded[0]["sha256"] == hashlib.sha256(b"perf").hexdigest()
    mock_client.return_value.fput_object.assert_called_once()
