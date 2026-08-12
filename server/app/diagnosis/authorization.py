"""Registered information sources and deterministic authorization decisions.

The model never calls this module directly.  HTTP handlers and orchestrators
submit a structured request; this module evaluates it against registered
capabilities and durable grants.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
from fnmatch import fnmatchcase
from typing import Any, Optional

from pydantic import Field, model_validator

from server.app.diagnosis.schemas import StrictModel


class OperationClass(str, Enum):
    READ = "READ"
    COLLECT = "COLLECT"
    CHANGE = "CHANGE"
    DESTRUCTIVE = "DESTRUCTIVE"


class ImpactLevel(str, Enum):
    I0 = "I0"
    I1 = "I1"
    I2 = "I2"
    I3 = "I3"
    I4 = "I4"


class AuthorizationDecision(str, Enum):
    AUTO_GRANTED = "AUTO_GRANTED"
    AUTO_REVIEWED = "AUTO_REVIEWED"
    USER_APPROVAL = "USER_APPROVAL"
    CHANGE_APPROVAL = "CHANGE_APPROVAL"
    DENIED = "DENIED"


class GrantMode(str, Enum):
    SINGLE_USE = "single_use"
    CASE = "case"
    SESSION = "session"
    POLICY = "policy"


class SourceDefinition(StrictModel):
    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,127}$")
    name: str = Field(min_length=1, max_length=128)
    source_type: str = Field(min_length=1, max_length=64)
    operation_class: OperationClass = OperationClass.READ
    operations: list[str] = Field(min_length=1, max_length=32)
    resource_dimensions: list[str] = Field(default_factory=list, max_length=16)
    data_classes: list[str] = Field(default_factory=list, max_length=16)
    credential_ref: Optional[str] = Field(default=None, max_length=512)
    network_policy: list[str] = Field(default_factory=list, max_length=32)
    default_timeout_sec: int = Field(default=15, ge=1, le=300)
    max_result_bytes: int = Field(default=1_048_576, ge=1_024, le=100_000_000)
    enabled: bool = True
    version: str = Field(default="1.0", min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_operations(self):
        normalized = [item.strip() for item in self.operations]
        if len(set(normalized)) != len(normalized) or any(not item for item in normalized):
            raise ValueError("source operations 必须非空且不能重复")
        self.operations = normalized
        return self

    def public_dict(self) -> dict[str, Any]:
        """Return capability metadata without credential or network secrets."""
        value = self.model_dump(mode="json", exclude={"credential_ref"})
        value["credential_configured"] = bool(self.credential_ref)
        return value


class GrantConstraints(StrictModel):
    max_result_bytes: int = Field(default=1_048_576, ge=1_024, le=100_000_000)
    max_queries: int = Field(default=100, ge=1, le=100_000)
    allowed_time_range_minutes: int = Field(default=60, ge=1, le=43_200)


class CreateAuthorizationGrantRequest(StrictModel):
    principal_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    source_ids: list[str] = Field(min_length=1, max_length=64)
    operations: list[str] = Field(min_length=1, max_length=64)
    resource_scope: dict[str, list[str]] = Field(default_factory=dict)
    mode: GrantMode = GrantMode.SESSION
    case_id: Optional[str] = Field(default=None, max_length=128)
    valid_until: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc) + timedelta(hours=1),
    )
    uses_remaining: Optional[int] = Field(default=None, ge=1, le=100_000)
    constraints: GrantConstraints = Field(default_factory=GrantConstraints)
    created_by: str = Field(default="demo_admin", min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_grant(self):
        if self.valid_until.tzinfo is None:
            raise ValueError("valid_until 必须包含时区")
        if self.valid_until <= datetime.now(timezone.utc):
            raise ValueError("valid_until 必须晚于当前时间")
        if self.valid_until > datetime.now(timezone.utc) + timedelta(days=365):
            raise ValueError("授权有效期不能超过 365 天")
        if self.mode == GrantMode.SINGLE_USE:
            self.uses_remaining = 1
        if self.mode == GrantMode.CASE and not self.case_id:
            raise ValueError("case 授权必须绑定 case_id")
        for dimension, patterns in self.resource_scope.items():
            if not dimension or not patterns or any(not pattern.strip() for pattern in patterns):
                raise ValueError("resource_scope 必须包含有效维度和选择器")
        return self


class AuthorizationEvaluationRequest(StrictModel):
    principal_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=128)
    source_id: str = Field(min_length=1, max_length=128)
    operation: str = Field(min_length=1, max_length=128)
    resource: dict[str, str] = Field(default_factory=dict)
    case_id: Optional[str] = Field(default=None, max_length=128)
    requested_result_bytes: int = Field(default=1_048_576, ge=0, le=100_000_000)
    requested_time_range_minutes: int = Field(default=15, ge=1, le=43_200)


class PolicyDecision(StrictModel):
    decision: AuthorizationDecision
    operation_class: OperationClass
    impact_level: ImpactLevel
    source_id: str
    matched_grant_id: Optional[str] = None
    reason_codes: list[str] = Field(default_factory=list)
    effective_constraints: dict[str, Any] = Field(default_factory=dict)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SourceRegistry:
    """Immutable-at-runtime registry for signed deployment capabilities."""

    def __init__(self, definitions: list[SourceDefinition]):
        self._definitions = {item.source_id: item for item in definitions}
        if len(self._definitions) != len(definitions):
            raise ValueError("Source Registry 中 source_id 重复")

    def get(self, source_id: str) -> Optional[SourceDefinition]:
        return self._definitions.get(source_id)

    def list(self) -> list[SourceDefinition]:
        return sorted(self._definitions.values(), key=lambda item: item.source_id)


DEFAULT_SOURCE_REGISTRY = SourceRegistry([
    SourceDefinition(
        source_id="mini-drop-agent-metrics",
        name="Mini-Drop Agent Metrics",
        source_type="agent_metrics",
        operations=["metrics.read", "metrics.query_range"],
        resource_dimensions=["cluster_id", "service_id", "agent_id"],
        data_classes=["operational_metric"],
        credential_ref="internal://mini-drop/agent-metrics-reader",
        network_policy=[],
        max_result_bytes=2_000_000,
    ),
    SourceDefinition(
        source_id="mini-drop-diagnosis-evidence",
        name="Mini-Drop Diagnosis Evidence",
        source_type="evidence_store",
        operations=["evidence.read", "evidence.list"],
        resource_dimensions=["cluster_id", "service_id", "diagnosis_id"],
        data_classes=["operational_metric", "diagnostic_artifact"],
        credential_ref="internal://mini-drop/evidence-reader",
        network_policy=[],
        max_result_bytes=5_000_000,
    ),
    SourceDefinition(
        source_id="mini-drop-topology-context",
        name="Mini-Drop Topology Context",
        source_type="topology",
        operations=["topology.read"],
        resource_dimensions=["cluster_id", "service_id", "diagnosis_id"],
        data_classes=["service_topology"],
        credential_ref=None,
        network_policy=[],
    ),
    SourceDefinition(
        source_id="prometheus-metrics",
        name="Prometheus Metrics",
        source_type="prometheus",
        operations=["metrics.query_range"],
        resource_dimensions=["cluster_id", "service_id"],
        data_classes=["operational_metric"],
        credential_ref=None,
        network_policy=["prometheus"],
        max_result_bytes=2_000_000,
    ),
    SourceDefinition(
        source_id="log-template-query",
        name="Log Template Query",
        source_type="log_analysis",
        operations=["log.query"],
        resource_dimensions=["cluster_id", "service_id", "agent_id"],
        data_classes=["log_pattern"],
        credential_ref="internal://mini-drop/log-reader",
        network_policy=[],
        max_result_bytes=500_000,
    ),
    SourceDefinition(
        source_id="otel-traces",
        name="OpenTelemetry Traces",
        source_type="trace",
        operations=["traces.list", "traces.read"],
        resource_dimensions=["cluster_id", "service_id"],
        data_classes=["trace_span"],
        credential_ref=None,
        network_policy=["otel_collector"],
        max_result_bytes=2_000_000,
    ),
    SourceDefinition(
        source_id="runtime-profile-parser",
        name="Runtime Profile Parser",
        source_type="profile",
        operations=["profile.parse"],
        resource_dimensions=["cluster_id", "service_id", "agent_id"],
        data_classes=["profile_artifact"],
        credential_ref="internal://mini-drop/profile-reader",
        network_policy=[],
        max_result_bytes=2_000_000,
    ),
])


def evaluate_source_access(
    request: AuthorizationEvaluationRequest,
    grants: list[dict[str, Any]],
    *,
    registry: SourceRegistry = DEFAULT_SOURCE_REGISTRY,
    now: Optional[datetime] = None,
) -> PolicyDecision:
    """Evaluate read access without consuming or mutating a grant."""
    evaluated_at = now or datetime.now(timezone.utc)
    source = registry.get(request.source_id)
    if source is None:
        return _decision(request.source_id, AuthorizationDecision.DENIED, ["SOURCE_NOT_REGISTERED"], evaluated_at)
    if not source.enabled:
        return _decision(request.source_id, AuthorizationDecision.DENIED, ["SOURCE_DISABLED"], evaluated_at)
    if request.operation not in source.operations:
        return _decision(
            request.source_id,
            AuthorizationDecision.DENIED,
            ["OPERATION_NOT_REGISTERED"],
            evaluated_at,
            source.operation_class,
        )
    if request.requested_result_bytes > source.max_result_bytes:
        return _decision(
            request.source_id,
            AuthorizationDecision.DENIED,
            ["SOURCE_RESULT_BUDGET_EXCEEDED"],
            evaluated_at,
            source.operation_class,
        )

    mismatch_reasons: set[str] = set()
    for grant in grants:
        reason = _grant_mismatch_reason(request, grant, evaluated_at)
        if reason:
            mismatch_reasons.add(reason)
            continue
        constraints = dict(grant.get("constraints") or {})
        if request.requested_result_bytes > int(constraints.get("max_result_bytes", 0)):
            mismatch_reasons.add("GRANT_RESULT_BUDGET_EXCEEDED")
            continue
        if request.requested_time_range_minutes > int(constraints.get("allowed_time_range_minutes", 0)):
            mismatch_reasons.add("GRANT_TIME_RANGE_EXCEEDED")
            continue
        return PolicyDecision(
            decision=AuthorizationDecision.AUTO_GRANTED,
            operation_class=source.operation_class,
            impact_level=ImpactLevel.I0,
            source_id=request.source_id,
            matched_grant_id=grant["grant_id"],
            reason_codes=["EXPLICIT_GRANT_MATCHED"],
            effective_constraints=constraints,
            evaluated_at=evaluated_at,
        )

    reason_codes = ["NO_MATCHING_GRANT"]
    reason_codes.extend(sorted(mismatch_reasons))
    return _decision(
        request.source_id,
        AuthorizationDecision.USER_APPROVAL,
        reason_codes,
        evaluated_at,
        source.operation_class,
    )


def _grant_mismatch_reason(
    request: AuthorizationEvaluationRequest,
    grant: dict[str, Any],
    now: datetime,
) -> Optional[str]:
    if grant.get("status") == "EXHAUSTED":
        return "GRANT_EXHAUSTED"
    if grant.get("status") != "ACTIVE":
        return "GRANT_NOT_ACTIVE"
    expires_at = grant.get("valid_until")
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= now:
            return "GRANT_EXPIRED"
    if grant.get("principal_id") != request.principal_id:
        return "PRINCIPAL_MISMATCH"
    if grant.get("tenant_id") != request.tenant_id:
        return "TENANT_MISMATCH"
    if request.source_id not in set(grant.get("source_ids") or []):
        return "SOURCE_OUT_OF_SCOPE"
    if request.operation not in set(grant.get("operations") or []):
        return "OPERATION_OUT_OF_SCOPE"
    if grant.get("case_id") and grant.get("case_id") != request.case_id:
        return "CASE_MISMATCH"
    uses_remaining = grant.get("uses_remaining")
    if uses_remaining is not None and int(uses_remaining) <= 0:
        return "GRANT_EXHAUSTED"
    constraints = grant.get("constraints") or {}
    max_queries = int(constraints.get("max_queries", 0) or 0)
    if max_queries and int(grant.get("query_count", 0) or 0) >= max_queries:
        return "GRANT_QUERY_BUDGET_EXHAUSTED"
    for dimension, patterns in (grant.get("resource_scope") or {}).items():
        actual = request.resource.get(dimension)
        if actual is None or not any(fnmatchcase(actual, pattern) for pattern in patterns):
            return "RESOURCE_OUT_OF_SCOPE"
    return None


def _decision(
    source_id: str,
    decision: AuthorizationDecision,
    reason_codes: list[str],
    evaluated_at: datetime,
    operation_class: OperationClass = OperationClass.READ,
) -> PolicyDecision:
    return PolicyDecision(
        decision=decision,
        operation_class=operation_class,
        impact_level=ImpactLevel.I0,
        source_id=source_id,
        reason_codes=reason_codes,
        evaluated_at=evaluated_at,
    )
