"""Optional Agent-side artifact upload to MinIO."""

from __future__ import annotations

import hashlib
import os

import urllib3

from agent.mini_drop_agent.config import AgentConfig


def maybe_upload_artifacts(
    task_id: str,
    artifacts: list[dict],
    config: AgentConfig,
    *,
    attempt_id: str = "",
) -> list[dict]:
    enriched = [_with_integrity(artifact) for artifact in artifacts]
    if not config.upload_artifacts:
        return enriched
    client = _minio_client(config)
    result: list[dict] = []
    for artifact in enriched:
        result.append(_upload_one(client, task_id, attempt_id, artifact, config))
    return result


def _minio_client(config: AgentConfig):
    from minio import Minio

    endpoint, inferred_secure = _normalize_endpoint(config.minio_endpoint)
    secure_raw = os.getenv("MINIO_SECURE", "").strip().lower()
    if secure_raw:
        secure = secure_raw in {"1", "true", "yes", "on"}
    else:
        secure = inferred_secure

    ca_cert = os.getenv("MINIO_CA_CERT", "").strip()
    http_client = None
    if secure and ca_cert:
        http_client = urllib3.PoolManager(
            cert_reqs="CERT_REQUIRED",
            ca_certs=ca_cert,
            retries=urllib3.Retry(total=0, connect=0, read=0, redirect=0),
        )

    return Minio(
        endpoint=endpoint,
        access_key=config.minio_access_key,
        secret_key=config.minio_secret_key,
        secure=secure,
        http_client=http_client,
    )


def _normalize_endpoint(endpoint: str) -> tuple[str, bool]:
    endpoint = (endpoint or "").strip()
    if endpoint.startswith("https://"):
        return endpoint.removeprefix("https://"), True
    if endpoint.startswith("http://"):
        return endpoint.removeprefix("http://"), False
    return endpoint, False


def _upload_one(
    client,
    task_id: str,
    attempt_id: str,
    artifact: dict,
    config: AgentConfig,
) -> dict:
    item = dict(artifact)
    local_path = item.get("local_path")
    if not local_path or not os.path.isfile(local_path):
        return item

    filename = item.get("filename") or os.path.basename(local_path)
    default_prefix = f"tasks/{task_id}"
    if attempt_id:
        default_prefix += f"/attempts/{attempt_id}"
    object_key = item.get("object_key") or f"{default_prefix}/{filename}"
    content_type = item.get("content_type") or "application/octet-stream"
    client.fput_object(
        bucket_name=config.minio_bucket,
        object_name=object_key,
        file_path=local_path,
        content_type=content_type,
    )
    item["bucket"] = config.minio_bucket
    item["object_key"] = object_key
    item["size_bytes"] = os.path.getsize(local_path)
    return item


def _with_integrity(artifact: dict) -> dict:
    item = dict(artifact)
    local_path = item.get("local_path")
    if not local_path or not os.path.isfile(local_path):
        return item
    digest = hashlib.sha256()
    with open(local_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    item["size_bytes"] = os.path.getsize(local_path)
    item["sha256"] = digest.hexdigest()
    return item
