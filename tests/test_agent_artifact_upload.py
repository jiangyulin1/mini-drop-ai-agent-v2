import hashlib
from unittest import mock

from agent.mini_drop_agent import artifact_upload
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
        uploaded = maybe_upload_artifacts(
            "task1", [artifact], _config(), attempt_id="attempt1",
        )

    assert uploaded[0]["bucket"] == "mini-drop"
    assert uploaded[0]["object_key"] == "tasks/task1/attempts/attempt1/perf.data"
    assert uploaded[0]["size_bytes"] == 4
    assert uploaded[0]["sha256"] == hashlib.sha256(b"perf").hexdigest()
    mock_client.return_value.fput_object.assert_called_once()


def test_secure_minio_client_uses_configured_ca(monkeypatch, tmp_path):
    ca_file = tmp_path / "ca.crt"
    ca_file.write_text("test-ca", encoding="utf-8")
    config = AgentConfig(
        agent_id="agent-1",
        agent_ip_addr="127.0.0.1",
        server_grpc_addr="control:50051",
        minio_endpoint="https://objects.example.com:9443",
        minio_access_key="access",
        minio_secret_key="secret",
    )
    monkeypatch.setenv("MINIO_SECURE", "1")
    monkeypatch.setenv("MINIO_CA_CERT", str(ca_file))

    with (
        mock.patch("urllib3.PoolManager") as pool_manager,
        mock.patch("minio.Minio") as minio,
    ):
        artifact_upload._minio_client(config)

    pool_manager.assert_called_once_with(
        cert_reqs="CERT_REQUIRED",
        ca_certs=str(ca_file),
        retries=mock.ANY,
    )
    minio.assert_called_once_with(
        endpoint="objects.example.com:9443",
        access_key="access",
        secret_key="secret",
        secure=True,
        http_client=pool_manager.return_value,
    )
