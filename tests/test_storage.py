"""Tests for MinIO storage helpers."""

from unittest import mock

import pytest

from server.app import storage as store


class TestEnsureBucket:
    def test_creates_bucket_when_missing(self):
        with mock.patch.object(store, "_client") as mock_client:
            mock_minio = mock_client.return_value
            mock_minio.bucket_exists.return_value = False

            store.ensure_bucket("test-bucket")

            mock_minio.bucket_exists.assert_called_once_with("test-bucket")
            mock_minio.make_bucket.assert_called_once_with("test-bucket")

    def test_skips_bucket_when_exists(self):
        with mock.patch.object(store, "_client") as mock_client:
            mock_minio = mock_client.return_value
            mock_minio.bucket_exists.return_value = True

            store.ensure_bucket("existing-bucket")

            mock_minio.make_bucket.assert_not_called()


class TestUploadFile:
    def test_upload_returns_size(self, tmp_path):
        f = tmp_path / "test.dat"
        f.write_text("x" * 100)

        with mock.patch.object(store, "_client") as mock_client:
            size = store.upload_file(str(f), "b", "key", "text/plain")
            assert size == 100
            mock_client.return_value.fput_object.assert_called_once()

    def test_upload_missing_file_raises(self, tmp_path):
        with mock.patch.object(store, "_client"):
            with pytest.raises(FileNotFoundError):
                store.upload_file("/nonexistent/path.dat", "b", "key")


class TestReadObjectBytes:
    def test_reads_and_closes_object(self):
        response = mock.MagicMock()
        response.read.return_value = b"hello"
        with mock.patch.object(store, "_client") as mock_client:
            mock_client.return_value.get_object.return_value = response
            assert store.read_object_bytes("b", "k") == b"hello"
        response.close.assert_called_once()
        response.release_conn.assert_called_once()

    def test_streams_and_closes_object(self):
        response = mock.MagicMock()
        response.read.side_effect = [b"hello", b" world", b""]
        with mock.patch.object(store, "_client") as mock_client:
            mock_client.return_value.get_object.return_value = response
            assert b"".join(store.stream_object("b", "k", chunk_size=5)) == b"hello world"
        response.close.assert_called_once()
        response.release_conn.assert_called_once()


class TestPresignedUrl:
    def test_returns_url_string(self):
        with mock.patch.object(store, "_client") as mock_client:
            mock_client.return_value.presigned_get_object.return_value = (
                "http://minio:9000/bucket/key?token=xyz"
            )
            url = store.presigned_get_url("bucket", "key", expires=1800)
            assert "minio" in url
            assert "token" in url

    def test_default_expires_one_hour(self):
        with mock.patch.object(store, "_client") as mock_client:
            store.presigned_get_url("b", "k")
            _, kwargs = mock_client.return_value.presigned_get_object.call_args
            assert kwargs["expires"].total_seconds() == 3600

    def test_rejects_invalid_expires(self):
        with pytest.raises(ValueError, match="expires must be between"):
            store.presigned_get_url("b", "k", expires=0)

    def test_uses_public_endpoint_for_presigned_url(self, monkeypatch):
        monkeypatch.setenv("MINIO_PUBLIC_ENDPOINT", "http://localhost:9000")
        monkeypatch.delenv("MINIO_PUBLIC_SECURE", raising=False)

        with mock.patch.object(store, "_client") as mock_client:
            mock_client.return_value.presigned_get_object.return_value = (
                "http://localhost:9000/bucket/key?token=xyz"
            )
            url = store.presigned_get_url("bucket", "key")

        assert url.startswith("http://localhost:9000")
        _, kwargs = mock_client.call_args
        assert kwargs["endpoint"] == "localhost:9000"
        assert kwargs["secure"] is False

    def test_https_public_endpoint_infers_secure_client(self, monkeypatch):
        monkeypatch.setenv("MINIO_PUBLIC_ENDPOINT", "https://objects.example.com")
        monkeypatch.delenv("MINIO_PUBLIC_SECURE", raising=False)

        with mock.patch.object(store, "_client") as mock_client:
            mock_client.return_value.presigned_get_object.return_value = (
                "https://objects.example.com/bucket/key?token=xyz"
            )
            store.presigned_get_url("bucket", "key")

        _, kwargs = mock_client.call_args
        assert kwargs["endpoint"] == "objects.example.com"
        assert kwargs["secure"] is True
