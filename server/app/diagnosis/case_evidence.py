"""Canonical Case Evidence ingestion and query (G3).

Task/Collection artifacts are materialized into case_evidence rows with stable
IDs derived from Task + Artifact provenance.  Attachment and legacy
DiagnosisEvidence remain compatibility projections; conclusion validation reads
this store first.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from server.app.state_machine import now_utc
from server.app.diagnosis.evidence_projection import project_artifact


def stable_projection_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evidence_id_for_artifact(
    case_id: str,
    task_id: str,
    artifact: dict[str, Any],
) -> str:
    artifact_id = str(artifact.get("id") or "")
    identity = artifact.get("identity_key") or artifact.get("object_key") or artifact_id
    raw = f"{case_id}:{task_id}:{identity}:{artifact.get('artifact_type') or 'raw'}"
    return f"ev-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


class CaseEvidenceService:
    def __init__(self, repository: Any):
        self._repo = repository

    def materialize_task_artifacts(
        self,
        case_id: str,
        tenant_id: str,
        *,
        task_id: str,
        attachment_id: str | None = None,
        actor_id: str = "mini-drop-evidence-service",
    ) -> list[str]:
        """Materialize all structured Task artifacts as canonical Case Evidence."""
        artifacts = self._repo.artifacts.get(task_id, []) if getattr(self._repo, "artifacts", None) else []
        task = self._repo.tasks.get(task_id) if getattr(self._repo, "tasks", None) else None
        collector_id = str(getattr(task, "collector_type", "") or "")
        request_options = ((getattr(task, "request_params", None) or {}).get("options") or {})
        target_ref = f"task:{task_id}"
        evidence_ids: list[str] = []
        for artifact in artifacts:
            if not artifact.get("artifact_type"):
                continue
            metadata = artifact.get("metadata") or {}
            evidence_id = evidence_id_for_artifact(case_id, task_id, artifact)
            try:
                projection = project_artifact(artifact)
            except (TypeError, ValueError, OverflowError) as exc:
                self._record_parser_failure(
                    case_id, tenant_id, task_id, artifact, actor_id, exc,
                )
                continue
            self._repo.upsert_case_evidence(
                case_id=case_id,
                tenant_id=tenant_id,
                evidence_id=evidence_id,
                attachment_id=attachment_id,
                task_id=task_id,
                artifact_id=artifact.get("id"),
                artifact_type=str(artifact.get("artifact_type") or ""),
                collector_id=collector_id,
                source_type="task_artifact",
                source_id=str(artifact.get("source_id") or collector_id or task_id),
                source_channel="QUERY" if str(artifact.get("artifact_type") or "").startswith("query") else "COLLECTOR",
                target_ref=target_ref,
                resource_incarnation=artifact.get("resource_incarnation"),
                content_hash=str(artifact.get("sha256") or stable_projection_hash(metadata)),
                projection_hash=projection["projection_hash"],
                quality="COMPLETE",
                freshness=_freshness_from_metadata(metadata, artifact.get("created_at")),
                time_window=_time_window_from_metadata(metadata, artifact.get("created_at")),
                event_time_start=metadata.get("window_start") or metadata.get("started_at") or artifact.get("created_at"),
                event_time_end=metadata.get("window_end") or metadata.get("finished_at"),
                artifact_schema=artifact.get("artifact_type"),
                schema_version=artifact.get("schema_version") or "1",
                producer_version=artifact.get("producer_version"),
                raw_locator=str(artifact.get("identity_key") or artifact.get("object_key") or ""),
                size_bytes=int(artifact.get("size_bytes") or artifact.get("size") or 0),
                sha256=str(artifact.get("sha256") or stable_projection_hash(metadata)),
                completeness=str(metadata.get("completeness") or "COMPLETE"),
                trust_level=str(metadata.get("trust_level") or "INTERNAL"),
                investigation_run_id=request_options.get("investigation_run_id"),
                execution_unit_id=getattr(task, "execution_unit_id", None),
                source_call_id=request_options.get("source_call_id"),
                lineage={
                    "task_id": task_id,
                    "artifact_id": artifact.get("id"),
                    "attachment_id": attachment_id,
                    "collector_id": collector_id,
                },
                trace_id=str(getattr(task, "traceparent", "") or "") or None,
                actor_id=actor_id,
            )
            if hasattr(self._repo, "upsert_evidence_projection"):
                self._repo.upsert_evidence_projection(
                    evidence_id=evidence_id,
                    case_id=case_id,
                    tenant_id=tenant_id,
                    projection_kind=projection["projection_kind"],
                    content=projection["content"],
                    projection_schema=projection["projection_schema"],
                    projection_version=projection["projection_version"],
                    truncated=projection["truncated"],
                    source_bytes=projection["source_bytes"],
                    parser_version="deterministic.v1",
                )
            evidence_ids.append(evidence_id)
        return evidence_ids

    def _record_parser_failure(
        self,
        case_id: str,
        tenant_id: str,
        task_id: str,
        artifact: dict[str, Any],
        actor_id: str,
        error: Exception,
    ) -> None:
        """Persist malformed input as INVALID Evidence plus a visible Gap."""
        evidence_id = evidence_id_for_artifact(case_id, task_id, artifact)
        self._repo.upsert_case_evidence(
            case_id=case_id,
            tenant_id=tenant_id,
            evidence_id=evidence_id,
            attachment_id=None,
            task_id=task_id,
            artifact_id=artifact.get("id"),
            artifact_type=str(artifact.get("artifact_type") or "raw"),
            collector_id=None,
            source_type="task_artifact",
            source_id=str(artifact.get("source_id") or task_id),
            target_ref=f"task:{task_id}",
            content_hash=str(artifact.get("sha256") or ""),
            projection_hash=None,
            quality="INVALID",
            completeness="FAILED",
            trust_level="INTERNAL",
            raw_locator=str(artifact.get("identity_key") or artifact.get("object_key") or ""),
            size_bytes=int(artifact.get("size_bytes") or artifact.get("size") or 0),
            sha256=str(artifact.get("sha256") or "") or None,
            lineage={"task_id": task_id, "artifact_id": artifact.get("id")},
            actor_id=actor_id,
        )
        self._repo.add_evidence_review_revision(
            evidence_id=evidence_id,
            case_id=case_id,
            tenant_id=tenant_id,
            decision="INVALID",
            reason=f"projection_parser_failed:{type(error).__name__}",
            reviewed_by=actor_id,
        )
        if hasattr(self._repo, "add_evidence_gap"):
            self._repo.add_evidence_gap(
                case_id=case_id,
                tenant_id=tenant_id,
                blocked_claim="Artifact could not be projected",
                required_fact=f"valid projection for {artifact.get('artifact_type') or 'raw'}",
                attempted_execution=task_id,
                target=f"task:{task_id}",
                reason_code="PROJECTION_PARSE_FAILED",
                raw_error_ref=evidence_id,
                retryable=True,
                next_best_action="recollect with a supported artifact schema",
            )

    def list_evidence(
        self,
        case_id: str,
        tenant_id: str,
        *,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        return self._repo.list_case_evidence(
            case_id, tenant_id, status=status, limit=limit,
        )

    def get_evidence(self, case_id: str, tenant_id: str, evidence_id: str) -> dict[str, Any] | None:
        return self._repo.get_case_evidence(case_id, tenant_id, evidence_id)


def _freshness_from_metadata(metadata: dict[str, Any], created_at: Any) -> str:
    if not created_at:
        return "UNKNOWN"
    try:
        created = created_at
        if isinstance(created, str):
            created = datetime.fromisoformat(created.replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_sec = (now_utc() - created).total_seconds()
    except Exception:
        return "UNKNOWN"
    return "FRESH" if age_sec <= 3600 else "STALE"


def _time_window_from_metadata(metadata: dict[str, Any], created_at: Any) -> dict[str, Any]:
    window = {
        "start": metadata.get("window_start") or metadata.get("started_at"),
        "end": metadata.get("window_end") or metadata.get("finished_at"),
        "source": "artifact_metadata",
    }
    if not window["start"]:
        window["start"] = created_at.isoformat() if hasattr(created_at, "isoformat") else created_at
    return {key: value for key, value in window.items() if value}
