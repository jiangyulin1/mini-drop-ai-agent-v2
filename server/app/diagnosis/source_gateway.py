"""Policy-enforced gateway for AI-readable system information."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

from pydantic import Field, model_validator

from server.app.ai_context import ContextBudget, optimize_evidence_context
from server.app.capability_tokens import (
    CapabilityTokenError,
    canonical_hash,
    issue_capability_token,
    token_fingerprint,
    verify_capability_token,
)
from server.app.diagnosis.authorization import (
    AuthorizationDecision,
    AuthorizationEvaluationRequest,
    DEFAULT_SOURCE_REGISTRY,
    evaluate_source_access,
)
from server.app.diagnosis.schemas import StrictModel


class SourceQueryRequest(StrictModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    operation: str = Field(min_length=1, max_length=128)
    resource: dict[str, str] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    case_id: Optional[str] = Field(default=None, max_length=128)
    requested_result_bytes: int = Field(default=24_000, ge=1_024, le=5_000_000)
    requested_time_range_minutes: int = Field(default=15, ge=1, le=43_200)

    @model_validator(mode="after")
    def validate_payload_size(self):
        size = len(json.dumps(self.parameters, ensure_ascii=False, default=str))
        if size > 20_000:
            raise ValueError("source query parameters 不能超过 20000 字符")
        if len(self.parameters) > 32 or len(self.resource) > 16:
            raise ValueError("source query 字段数量超限")
        return self


class EvidenceEnvelope(StrictModel):
    schema_version: str = "evidence-envelope.v1"
    evidence_id: str
    source_id: str
    source_version: str
    principal_id: str
    tenant_id: str
    case_id: Optional[str] = None
    resource_scope: dict[str, str]
    operation: str
    query_fingerprint: str
    observed_at: datetime
    valid_time: dict[str, Any] = Field(default_factory=dict)
    data_class: str
    content_hash: str
    projection_hash: str
    content_projection: dict[str, Any]
    redactions: dict[str, int] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)


class SourceGatewayError(RuntimeError):
    def __init__(self, code: str, status_code: int = 400):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class SourceConnector(Protocol):
    source_id: str

    def execute(self, request: SourceQueryRequest) -> dict[str, Any]: ...


class AgentMetricsConnector:
    source_id = "mini-drop-agent-metrics"

    def __init__(self, repo):
        self.repo = repo

    def execute(self, request: SourceQueryRequest) -> dict[str, Any]:
        agent_id = request.resource.get("agent_id", "")
        if not agent_id:
            raise SourceGatewayError("AGENT_ID_REQUIRED", 400)
        agent = self.repo.agents.get(agent_id)
        if agent is None:
            raise SourceGatewayError("AGENT_NOT_FOUND", 404)
        if request.operation == "metrics.read":
            return {
                "agent": {
                    "agent_id": agent.id,
                    "hostname": agent.hostname,
                    "status": agent.status,
                    "capabilities": agent.capabilities or [],
                },
                "metrics": dict(self.repo.agent_metrics.get(agent_id) or {}),
            }
        if request.operation == "metrics.query_range":
            limit = _bounded_int(request.parameters.get("limit", 50), 1, 100)
            return {
                "agent_id": agent_id,
                "samples": self.repo.get_agent_metric_history(agent_id, limit=limit),
            }
        raise SourceGatewayError("OPERATION_NOT_IMPLEMENTED", 400)


class DiagnosisEvidenceConnector:
    source_id = "mini-drop-diagnosis-evidence"

    def __init__(self, orchestrator):
        self.orchestrator = orchestrator

    def execute(self, request: SourceQueryRequest) -> dict[str, Any]:
        diagnosis_id = request.resource.get("diagnosis_id", "")
        if not diagnosis_id:
            raise SourceGatewayError("DIAGNOSIS_ID_REQUIRED", 400)
        if request.case_id and request.case_id != diagnosis_id:
            raise SourceGatewayError("CASE_SCOPE_MISMATCH", 403)
        session = self.orchestrator.store.get_session(diagnosis_id)
        if session is None:
            raise SourceGatewayError("DIAGNOSIS_NOT_FOUND", 404)
        evidence = self.orchestrator.store.list_evidence(diagnosis_id)
        service_id = request.resource.get("service_id")
        if service_id:
            evidence = [
                item for item in evidence
                if (item.get("target") or {}).get("service_id") == service_id
            ]
        if request.operation == "evidence.read":
            evidence_id = str(request.parameters.get("evidence_id", ""))
            if not evidence_id:
                raise SourceGatewayError("EVIDENCE_ID_REQUIRED", 400)
            item = next((row for row in evidence if row.get("evidence_id") == evidence_id), None)
            if item is None:
                raise SourceGatewayError("EVIDENCE_NOT_FOUND", 404)
            return {"diagnosis_id": diagnosis_id, "evidence": item}
        if request.operation == "evidence.list":
            limit = _bounded_int(request.parameters.get("limit", 20), 1, 100)
            return {"diagnosis_id": diagnosis_id, "evidence": evidence[:limit]}
        raise SourceGatewayError("OPERATION_NOT_IMPLEMENTED", 400)


class TopologyContextConnector:
    source_id = "mini-drop-topology-context"

    def __init__(self, orchestrator):
        self.orchestrator = orchestrator

    def execute(self, request: SourceQueryRequest) -> dict[str, Any]:
        diagnosis_id = request.resource.get("diagnosis_id", "")
        if not diagnosis_id:
            raise SourceGatewayError("DIAGNOSIS_ID_REQUIRED", 400)
        session = self.orchestrator.store.get_session(diagnosis_id)
        if session is None:
            raise SourceGatewayError("DIAGNOSIS_NOT_FOUND", 404)
        topology = self.orchestrator.store.get_topology(session.get("topology_snapshot_id"))
        if topology is None:
            raise SourceGatewayError("TOPOLOGY_NOT_FOUND", 404)
        return {"diagnosis_id": diagnosis_id, "topology": topology}


class SourceGateway:
    def __init__(self, repo, orchestrator):
        self.repo = repo
        self.orchestrator = orchestrator
        self._connectors: dict[str, SourceConnector] = {
            "mini-drop-agent-metrics": AgentMetricsConnector(repo),
            "mini-drop-diagnosis-evidence": DiagnosisEvidenceConnector(orchestrator),
            "mini-drop-topology-context": TopologyContextConnector(orchestrator),
        }

    def query(
        self,
        source_id: str,
        request: SourceQueryRequest,
        *,
        principal_id: str,
    ) -> EvidenceEnvelope:
        if not _source_access_enabled():
            self.repo.record_source_access_denied(
                principal_id=principal_id,
                tenant_id=request.tenant_id,
                source_id=source_id,
                operation=request.operation,
                reason_codes=["GLOBAL_SOURCE_ACCESS_DISABLED"],
            )
            raise SourceGatewayError("GLOBAL_SOURCE_ACCESS_DISABLED", 503)
        if source_id not in self._connectors:
            raise SourceGatewayError("SOURCE_CONNECTOR_NOT_AVAILABLE", 503)
        evaluation = AuthorizationEvaluationRequest(
            principal_id=principal_id,
            tenant_id=request.tenant_id,
            source_id=source_id,
            operation=request.operation,
            resource=request.resource,
            case_id=request.case_id,
            requested_result_bytes=request.requested_result_bytes,
            requested_time_range_minutes=request.requested_time_range_minutes,
        )
        grants = self.repo.list_authorization_grants(
            principal_id=principal_id,
            tenant_id=request.tenant_id,
            include_inactive=True,
        )
        decision = evaluate_source_access(evaluation, grants)
        if decision.decision != AuthorizationDecision.AUTO_GRANTED or not decision.matched_grant_id:
            self.repo.record_source_access_denied(
                principal_id=principal_id,
                tenant_id=request.tenant_id,
                source_id=source_id,
                operation=request.operation,
                reason_codes=decision.reason_codes,
            )
            code = "SOURCE_APPROVAL_REQUIRED" if decision.decision == AuthorizationDecision.USER_APPROVAL else "SOURCE_ACCESS_DENIED"
            raise SourceGatewayError(f"{code}:{','.join(decision.reason_codes)}", 403)

        max_result_bytes = min(
            request.requested_result_bytes,
            int(decision.effective_constraints.get("max_result_bytes", request.requested_result_bytes)),
        )
        try:
            token = issue_capability_token(
                principal_id=principal_id,
                tenant_id=request.tenant_id,
                grant_id=decision.matched_grant_id,
                case_id=request.case_id,
                capability_type="source",
                capability_id=source_id,
                operation=request.operation,
                resource=request.resource,
                parameters=request.parameters,
                max_result_bytes=max_result_bytes,
            )
            claims = verify_capability_token(
                token,
                principal_id=principal_id,
                tenant_id=request.tenant_id,
                capability_type="source",
                capability_id=source_id,
                operation=request.operation,
                resource=request.resource,
                parameters=request.parameters,
            )
        except CapabilityTokenError as exc:
            raise SourceGatewayError(str(exc), 503) from exc

        raw = self._connectors[source_id].execute(request)
        raw_size = len(json.dumps(raw, ensure_ascii=False, default=str).encode("utf-8"))
        source = DEFAULT_SOURCE_REGISTRY.get(source_id)
        if source is None:
            raise SourceGatewayError("SOURCE_NOT_REGISTERED", 500)
        projection = optimize_evidence_context(
            raw,
            budget=ContextBudget(
                max_chars=max(1_024, max_result_bytes),
                max_items_per_list=20,
                max_string_chars=800,
                max_depth=7,
            ),
        )
        projected_size = len(json.dumps(
            projection.payload, ensure_ascii=False, separators=(",", ":"), default=str,
        ).encode("utf-8"))
        if projected_size > claims.max_result_bytes:
            raise SourceGatewayError("SOURCE_RESULT_BUDGET_EXCEEDED", 413)

        query_fingerprint = canonical_hash({
            "source_id": source_id,
            "operation": request.operation,
            "resource": request.resource,
            "parameters": request.parameters,
        })
        content_hash = canonical_hash(raw)
        projection_hash = canonical_hash(projection.payload)
        capability_fingerprint = token_fingerprint(token)

        try:
            self.repo.consume_authorization_grant(
                claims.grant_id,
                principal_id=principal_id,
                tenant_id=request.tenant_id,
                capability_jti=claims.jti,
                capability_token_fingerprint=capability_fingerprint,
                source_id=source_id,
                operation=request.operation,
                query_fingerprint=query_fingerprint,
                content_hash=content_hash,
                projection_hash=projection_hash,
                result_bytes=projected_size,
            )
        except ValueError as exc:
            raise SourceGatewayError(str(exc), 409) from exc

        now = datetime.now(timezone.utc)
        return EvidenceEnvelope(
            evidence_id=f"source:{query_fingerprint[:24]}:{claims.jti[-8:]}",
            source_id=source_id,
            source_version=source.version,
            principal_id=principal_id,
            tenant_id=request.tenant_id,
            case_id=request.case_id,
            resource_scope=request.resource,
            operation=request.operation,
            query_fingerprint=query_fingerprint,
            observed_at=now,
            valid_time={
                key: request.parameters[key]
                for key in ("start", "end")
                if key in request.parameters
            },
            data_class=source.data_classes[0] if source.data_classes else "unknown",
            content_hash=content_hash,
            projection_hash=projection_hash,
            content_projection=projection.payload,
            redactions={
                "fields": projection.stats.redacted_fields,
                "truncated_strings": projection.stats.truncated_strings,
                "raw_bytes": raw_size,
                "projected_bytes": projected_size,
            },
            policy={
                "decision": decision.decision.value,
                "grant_id": decision.matched_grant_id,
                "capability_token_fingerprint": capability_fingerprint,
                "capability_jti": claims.jti,
                "expires_at": claims.expires_at,
            },
        )


def _bounded_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SourceGatewayError("INVALID_INTEGER_PARAMETER", 400) from exc
    return min(max(parsed, minimum), maximum)


def _source_access_enabled() -> bool:
    configured = os.getenv("MINI_DROP_AI_SOURCE_ACCESS_ENABLED")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes", "on"}
    auth_enabled = os.getenv("MINI_DROP_API_AUTH_ENABLED", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }
    return not auth_enabled
