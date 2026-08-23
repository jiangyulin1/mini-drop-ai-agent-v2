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
from server.app.diagnosis.collection_reuse import result_fingerprint
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
        stale_for_current_revision: bool = False,
    ) -> list[str]:
        """Materialize all structured Task artifacts as canonical Case Evidence."""
        artifacts = self._repo.artifacts.get(task_id, []) if getattr(self._repo, "artifacts", None) else []
        task = self._repo.tasks.get(task_id) if getattr(self._repo, "tasks", None) else None
        collector_id = str(getattr(task, "collector_type", "") or "")
        request_options = ((getattr(task, "request_params", None) or {}).get("options") or {})
        target_ref = f"task:{task_id}"
        probe_fingerprint_value = str(request_options.get("probe_fingerprint") or "")
        probe_key_value = str(request_options.get("probe_key") or "")
        membership_snapshot_id = str(
            request_options.get("membership_snapshot_id") or ""
        ) or None
        membership_snapshot = None
        if membership_snapshot_id and hasattr(self._repo, "get_membership_snapshot"):
            membership_snapshot = self._repo.get_membership_snapshot(
                case_id, tenant_id, membership_snapshot_id,
            )
        projection_context = {
            "membership_snapshot_id": membership_snapshot_id,
            "membership_snapshot": membership_snapshot,
            "discovery_run_id": request_options.get("discovery_run_id"),
            "discovery_seed_ref": request_options.get("discovery_seed_ref"),
            "scope_revision": request_options.get("scope_revision"),
            "target_ref": request_options.get("target_ref") or target_ref,
        }
        evidence_ids: list[str] = []
        for artifact in artifacts:
            if not artifact.get("artifact_type"):
                continue
            metadata = artifact.get("metadata") or {}
            evidence_id = evidence_id_for_artifact(case_id, task_id, artifact)
            try:
                # The projection is built before the Case Evidence row is
                # persisted, but its graph edges must still cite the canonical
                # ``ev-...`` identifier rather than a collector-local event
                # ID.  Pass the deterministic ID into the parser as lineage.
                artifact_projection_context = {
                    **projection_context,
                    "evidence_id": evidence_id,
                }
                projection = project_artifact(
                    artifact,
                    projection_context=artifact_projection_context,
                )
            except (TypeError, ValueError, OverflowError) as exc:
                self._record_parser_failure(
                    case_id, tenant_id, task_id, artifact, actor_id, exc,
                )
                continue
            concrete_result_fingerprint = ""
            if probe_fingerprint_value and projection.get("projection_hash"):
                concrete_result_fingerprint = result_fingerprint(
                    probe_fingerprint_value=probe_fingerprint_value,
                    projection_hash=str(projection["projection_hash"]),
                    content_hash=str(artifact.get("sha256") or ""),
                    artifact_schema=str(artifact.get("artifact_type") or ""),
                    parser_version=(
                        "deterministic-network-discovery.v1"
                        if projection["projection_kind"] in {"DEPENDENCY_GRAPH", "TOPOLOGY_GRAPH"}
                        else "deterministic.v1"
                    ),
                    completeness=str(metadata.get("completeness") or "COMPLETE"),
                )
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
                membership_snapshot_id=membership_snapshot_id,
                lineage={
                    "task_id": task_id,
                    "artifact_id": artifact.get("id"),
                    "attachment_id": attachment_id,
                    "collector_id": collector_id,
                    "probe_fingerprint": probe_fingerprint_value,
                    "probe_key": probe_key_value,
                    "result_fingerprint": concrete_result_fingerprint,
                    "reuse_policy": "EXACT_PROBE_AND_RESULT",
                    "discovery_run_id": request_options.get("discovery_run_id"),
                    "discovery_seed_ref": request_options.get("discovery_seed_ref"),
                    "discovery_parent_task_id": request_options.get("discovery_parent_task_id"),
                },
                trace_id=str(getattr(task, "traceparent", "") or "") or None,
                stale_for_current_revision=stale_for_current_revision,
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
                    parser_version=(
                        "deterministic-network-discovery.v1"
                        if projection["projection_kind"] in {"DEPENDENCY_GRAPH", "TOPOLOGY_GRAPH"}
                        else "deterministic.v1"
                    ),
                )
            evidence_ids.append(evidence_id)
        return evidence_ids

    def materialize_source_envelope(
        self,
        case_id: str,
        tenant_id: str,
        *,
        envelope: dict[str, Any],
        actor_id: str = "mini-drop-source-evidence-service",
    ) -> str:
        """Commit a SourceGateway/MCP EvidenceEnvelope to the canonical store."""
        envelope_case = str(envelope.get("case_id") or case_id)
        if envelope_case != case_id or str(envelope.get("tenant_id") or "") != tenant_id:
            raise ValueError("SOURCE_EVIDENCE_OWNERSHIP_CONFLICT")
        evidence_id = str(envelope.get("evidence_id") or "").strip()
        source_id = str(envelope.get("source_id") or "").strip()
        projection = envelope.get("content_projection") or {}
        if not evidence_id or not source_id or not isinstance(projection, dict):
            raise ValueError("INVALID_EVIDENCE_ENVELOPE")
        observed_at = envelope.get("observed_at")
        valid_time = envelope.get("valid_time") or {}
        redactions = envelope.get("redactions") or {}
        projection_hash = stable_projection_hash(projection)
        self._repo.upsert_case_evidence(
            case_id=case_id, tenant_id=tenant_id, evidence_id=evidence_id,
            attachment_id=None, task_id=None, artifact_id=None,
            artifact_type="source_envelope", collector_id=None,
            source_type="source_gateway", source_id=source_id, source_channel="MCP",
            target_ref=json.dumps(envelope.get("resource_scope") or {}, sort_keys=True),
            content_hash=str(envelope.get("content_hash") or projection_hash),
            projection_hash=projection_hash, quality="COMPLETE", freshness="FRESH",
            time_window={
                "start": valid_time.get("start") or observed_at,
                "end": valid_time.get("end") or observed_at,
                "source": "source_envelope",
            },
            event_time_start=valid_time.get("start") or observed_at,
            event_time_end=valid_time.get("end") or observed_at,
            artifact_schema="evidence-envelope.v1",
            schema_version=str(envelope.get("schema_version") or "evidence-envelope.v1"),
            producer_version=str(envelope.get("source_version") or ""),
            raw_locator=f"source:{source_id}:{envelope.get('query_fingerprint') or ''}",
            size_bytes=int(redactions.get("projected_bytes") or 0),
            sha256=str(envelope.get("content_hash") or projection_hash),
            completeness="COMPLETE", trust_level="AUTHORIZED_SOURCE",
            source_call_id=str(envelope.get("query_fingerprint") or "") or None,
            lineage={
                "source_id": source_id, "operation": envelope.get("operation"),
                "query_fingerprint": envelope.get("query_fingerprint"),
                "envelope_projection_hash": envelope.get("projection_hash"),
                "principal_id": envelope.get("principal_id"),
                "policy": envelope.get("policy") or {},
            },
            actor_id=actor_id,
        )
        self._repo.upsert_evidence_projection(
            evidence_id=evidence_id, case_id=case_id, tenant_id=tenant_id,
            projection_kind="source_projection", content=projection,
            projection_schema="source-envelope.projection.v1", projection_version=1,
            truncated=bool(redactions.get("truncated")),
            source_bytes=int(redactions.get("source_bytes") or redactions.get("projected_bytes") or 0),
            parser_version="source-gateway.v1",
        )
        self._repo.record_case_event(
            case_id, tenant_id, event_type="source_evidence_committed",
            payload={"evidence_id": evidence_id, "source_id": source_id}, actor_id=actor_id,
        )
        return evidence_id

    def materialize_evaluation_projection(
        self,
        case_id: str,
        tenant_id: str,
        *,
        evidence_id: str,
        pack_kind: str,
        source_id: str,
        source_ref: str,
        projection: dict[str, Any],
        content_hash: str | None = None,
        source_bytes: int = 0,
        synthetic: bool = False,
        observed_at: Any = None,
        actor_id: str = "mini-drop-evaluation-import",
    ) -> dict[str, Any]:
        """Materialize a bounded public-evaluation projection.

        This is deliberately separate from ``materialize_source_envelope``:
        GitHub replay data is neither an MCP response nor a live collector
        artifact.  The explicit ``EVALUATION``/``REPLAY`` provenance keeps the
        distinction visible to the UI, verifier, and manual evaluator.
        """
        case = self._repo.get_incident_case(case_id, tenant_id)
        if case is None:
            raise ValueError("CASE_NOT_FOUND")
        evidence_id = str(evidence_id or "").strip()
        pack_kind = str(pack_kind or "").strip()
        source_id = str(source_id or "").strip()
        source_ref = str(source_ref or "").strip()
        if not evidence_id or not pack_kind or not source_id or not source_ref:
            raise ValueError("INVALID_EVALUATION_PROJECTION_METADATA")
        if not isinstance(projection, dict):
            raise ValueError("INVALID_EVALUATION_PROJECTION")
        projection_hash = stable_projection_hash(projection)
        content_hash = str(content_hash or projection_hash)
        timestamp_dt = observed_at if isinstance(observed_at, datetime) else now_utc()
        timestamp = timestamp_dt.isoformat()
        trust_level = "SYNTHETIC_EVAL" if synthetic else "DEVELOPMENT_EVAL"
        row = self._repo.upsert_case_evidence(
            case_id=case_id,
            tenant_id=tenant_id,
            evidence_id=evidence_id,
            attachment_id=None,
            task_id=None,
            artifact_id=None,
            artifact_type=f"github_pr_{pack_kind}",
            collector_id=None,
            source_type="evaluation_pack",
            source_id=source_id,
            source_channel="EVALUATION",
            data_origin="REPLAY",
            target_ref=source_ref,
            content_hash=content_hash,
            projection_hash=projection_hash,
            quality="COMPLETE",
            freshness="HISTORICAL",
            time_window={"observed_at": timestamp, "source": "github_pr_eval"},
            event_time_start=timestamp_dt,
            event_time_end=timestamp_dt,
            artifact_schema="github-pr-eval.projection.v1",
            schema_version="github-pr-eval.projection.v1",
            producer_version="run_github_pr_attribution_eval.v1",
            raw_locator=source_ref,
            size_bytes=int(source_bytes or 0),
            sha256=content_hash,
            completeness="COMPLETE",
            trust_level=trust_level,
            lineage={
                "source_ref": source_ref,
                "pack_kind": pack_kind,
                "synthetic": bool(synthetic),
                "import_mode": "projection_only",
            },
            actor_id=actor_id,
        )
        projection_row = self._repo.upsert_evidence_projection(
            evidence_id=evidence_id,
            case_id=case_id,
            tenant_id=tenant_id,
            projection_kind="evaluation_projection",
            content=projection,
            projection_schema="github-pr-eval.projection.v1",
            projection_version=1,
            truncated=False,
            source_bytes=int(source_bytes or 0),
            parser_version="github-pr-eval.v1",
        )
        self._repo.record_case_event(
            case_id,
            tenant_id,
            event_type="evaluation_evidence_imported",
            payload={
                "evidence_id": evidence_id,
                "pack_kind": pack_kind,
                "projection_hash": projection_hash,
                "synthetic": bool(synthetic),
            },
            actor_id=actor_id,
        )
        return {
            "evidence": row,
            "projection": projection_row,
            "evidence_id": evidence_id,
            "projection_hash": projection_hash,
            "projected_bytes": int(projection_row.get("projected_bytes") or 0),
            "synthetic": bool(synthetic),
        }

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
