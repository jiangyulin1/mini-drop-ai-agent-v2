"""Artifact identity, availability, integrity, and evidence-link helpers."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from server.app import storage


_TASK_ARTIFACT_REF = re.compile(r"^task:([^:]+)(?::artifact:([^:]+))?$")


def artifact_identity(task_id: str, artifact: dict[str, Any]) -> str:
    """Build a stable public identity without exposing the database row id."""

    window_index = (artifact.get("metadata") or {}).get("window_index", "")
    material = "\0".join([
        task_id,
        str(artifact.get("artifact_type") or ""),
        str(artifact.get("object_key") or ""),
        str(artifact.get("filename") or ""),
        str(window_index),
    ])
    return f"art_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def parse_task_artifact_ref(value: Any) -> tuple[str, str] | None:
    match = _TASK_ARTIFACT_REF.match(str(value or ""))
    if not match:
        return None
    return match.group(1), match.group(2) or ""


def _allowed_local_path(value: Any) -> Path | None:
    if not value:
        return None
    root_value = os.getenv("MINI_DROP_ARTIFACT_ROOT", "").strip()
    if not root_value:
        return None
    root = Path(root_value).expanduser().resolve()
    candidate = Path(str(value)).expanduser().resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return candidate


def _retention_state(created_at: Any) -> tuple[str, str | None]:
    try:
        retention_days = int(os.getenv("MINI_DROP_ARTIFACT_RETENTION_DAYS", "30"))
    except ValueError:
        retention_days = 30
    if retention_days <= 0 or not created_at:
        return "unbounded", None
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            return "unknown", None
    if not isinstance(created_at, datetime):
        return "unknown", None
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    expires_at = created_at.astimezone(timezone.utc).timestamp() + retention_days * 86400
    expires = datetime.fromtimestamp(expires_at, tz=timezone.utc)
    state = "expired" if expires <= datetime.now(timezone.utc) else "active"
    return state, expires.isoformat().replace("+00:00", "Z")


def inspect_artifact(
    task_id: str,
    artifact: dict[str, Any],
    *,
    check_availability: bool = True,
    verify_hash: bool = False,
) -> dict[str, Any]:
    """Return API-safe metadata with live availability and integrity state."""

    result = dict(artifact)
    result["artifact_id"] = artifact_identity(task_id, artifact)
    result["task_id"] = task_id
    retention_state, expires_at = _retention_state(artifact.get("created_at"))
    result["retention_state"] = retention_state
    result["expires_at"] = expires_at
    result["availability"] = "unknown"
    result["availability_reason"] = "尚未执行在线检查"
    result["actual_size_bytes"] = None
    result["integrity_status"] = "not_checked"
    if not check_availability:
        return result

    result["availability"] = "missing"
    result["availability_reason"] = "没有可访问的本地路径或对象存储键"

    local_path = _allowed_local_path(artifact.get("local_path"))
    if local_path is not None and local_path.is_file():
        result["availability"] = "available"
        result["availability_reason"] = "local"
        result["actual_size_bytes"] = local_path.stat().st_size
        expected_size = artifact.get("size_bytes")
        if expected_size and result["actual_size_bytes"] != expected_size:
            result["integrity_status"] = "mismatch"
            result["availability_reason"] = "本地文件大小与登记值不一致"
        if verify_hash and artifact.get("sha256"):
            actual_hash = hashlib.sha256(local_path.read_bytes()).hexdigest()
            result["actual_sha256"] = actual_hash
            result["integrity_status"] = "verified" if actual_hash == artifact["sha256"] else "mismatch"
        return result

    bucket = artifact.get("bucket") or os.getenv("MINIO_BUCKET", "mini-drop")
    object_key = artifact.get("object_key") or ""
    if not object_key:
        return result
    try:
        size = storage.object_size(bucket, object_key)
    except Exception as exc:
        result["availability"] = "unavailable"
        result["availability_reason"] = f"对象存储检查失败：{type(exc).__name__}"
        return result
    if size is None:
        result["availability_reason"] = "对象存储文件已不存在"
        return result

    result["availability"] = "available"
    result["availability_reason"] = "object_storage"
    result["actual_size_bytes"] = size
    expected_size = artifact.get("size_bytes")
    if expected_size and size != expected_size:
        result["integrity_status"] = "mismatch"
        result["availability_reason"] = "对象大小与登记值不一致"
    if verify_hash and artifact.get("sha256"):
        content = storage.read_object_bytes(bucket, object_key)
        actual_hash = hashlib.sha256(content).hexdigest()
        result["actual_sha256"] = actual_hash
        result["integrity_status"] = "verified" if actual_hash == artifact["sha256"] else "mismatch"
    return result


def evidence_artifact_links(
    evidence: dict[str, Any],
    artifacts_by_task: dict[str, list[dict[str, Any]]],
    *,
    verify: bool = False,
) -> list[dict[str, Any]]:
    """Resolve legacy evidence references into typed, exact artifact links."""

    targets = []
    for ref_name in ("raw_artifact_ref", "derived_artifact_ref"):
        target = parse_task_artifact_ref(evidence.get(ref_name))
        if target is not None and target not in targets:
            targets.append(target)
    if not targets:
        return []
    links: list[dict[str, Any]] = []
    seen: set[str] = set()
    for task_id, artifact_type in targets:
        candidates = artifacts_by_task.get(task_id, [])
        if artifact_type:
            candidates = [
                item
                for item in candidates
                if item.get("artifact_type") == artifact_type
            ]
        for item in candidates:
            descriptor = inspect_artifact(
                task_id,
                item,
                check_availability=verify,
                verify_hash=verify,
            )
            if descriptor["artifact_id"] in seen:
                continue
            seen.add(descriptor["artifact_id"])
            links.append(descriptor)
    return links


def read_artifact_bytes(artifact: dict[str, Any]) -> bytes:
    local_path = _allowed_local_path(artifact.get("local_path"))
    if local_path is not None and local_path.is_file():
        return local_path.read_bytes()
    bucket = artifact.get("bucket") or os.getenv("MINIO_BUCKET", "mini-drop")
    object_key = artifact.get("object_key") or ""
    if not object_key or storage.object_size(bucket, object_key) is None:
        raise FileNotFoundError(object_key or "missing artifact key")
    return storage.read_object_bytes(bucket, object_key)
