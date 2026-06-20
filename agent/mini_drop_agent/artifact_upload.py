"""Optional Agent-side artifact upload to MinIO."""

from __future__ import annotations

import os

from agent.mini_drop_agent.config import AgentConfig


def maybe_upload_artifacts(task_id: str, artifacts: list[dict], config: AgentConfig) -> list[dict]:
    if not config.upload_artifacts:
        return artifacts
    client = _minio_client(config)
    result: list[dict] = []
    for artifact in artifacts:
        result.append(_upload_one(client, task_id, artifact, config))
    return result


def _minio_client(config: AgentConfig):
    from minio import Minio

    endpoint, inferred_secure = _normalize_endpoint(config.minio_endpoint)
    secure_raw = os.getenv("MINIO_SECURE", "").strip().lower()
    if secure_raw:
        secure = secure_raw in {"1", "true", "yes", "on"}
    else:
        secure = inferred_secure

    return Minio(
        endpoint=endpoint,
        access_key=config.minio_access_key,
        secret_key=config.minio_secret_key,
        secure=secure,
    )


def _normalize_endpoint(endpoint: str) -> tuple[str, bool]:
    endpoint = (endpoint or "").strip()
    if endpoint.startswith("https://"):
        return endpoint.removeprefix("https://"), True
    if endpoint.startswith("http://"):
        return endpoint.removeprefix("http://"), False
    return endpoint, False


def _upload_one(client, task_id: str, artifact: dict, config: AgentConfig) -> dict:
    item = dict(artifact)
    local_path = item.get("local_path")
    if not local_path or not os.path.isfile(local_path):
        return item

    filename = item.get("filename") or os.path.basename(local_path)
    object_key = item.get("object_key") or f"tasks/{task_id}/{filename}"
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
