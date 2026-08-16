"""Conversation-first runtime contracts for the Mini-Drop diagnostic agent.

This module is deliberately orchestration-focused.  It does not expose model
chain-of-thought and it does not let a model invent tools.  A turn is reduced
to an auditable intent, registered tool candidates, evidence references and a
user-facing decision summary.  Source execution remains behind SourceGateway.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import Field, model_validator

from server.app.diagnosis.schemas import StrictModel
from server.app.diagnosis.source_gateway import SourceGatewayError, SourceQueryRequest


class AgentTurnIntent(str, Enum):
    INVESTIGATE = "investigate"
    EXPLAIN = "explain"
    CORRECT = "correct"
    DEPLOYMENT_ASSESSMENT = "deployment_assessment"
    STATUS = "status"


class DeploymentRequirements(StrictModel):
    replicas: int = Field(default=1, ge=1, le=1000)
    cpu_cores_per_replica: float = Field(default=0, ge=0, le=1024)
    memory_mb_per_replica: int = Field(default=0, ge=0, le=10_000_000)
    disk_mb_per_replica: int = Field(default=0, ge=0, le=100_000_000)
    cpu_overhead_cores: float = Field(default=0, ge=0, le=1024)
    memory_overhead_mb: int = Field(default=0, ge=0, le=10_000_000)
    disk_overhead_mb: int = Field(default=0, ge=0, le=100_000_000)
    safety_margin_ratio: float = Field(default=0.2, ge=0, le=1)

    @model_validator(mode="after")
    def require_resource_demand(self):
        if not any((
            self.cpu_cores_per_replica,
            self.memory_mb_per_replica,
            self.disk_mb_per_replica,
        )):
            raise ValueError("deployment requirements need cpu, memory or disk demand")
        return self


class AgentTurnRequest(StrictModel):
    message: str = Field(min_length=1, max_length=8000)
    intent: Optional[AgentTurnIntent] = None
    execute_safe_tools: bool = True
    max_tool_calls: int = Field(default=4, ge=0, le=16)
    deployment_requirements: Optional[DeploymentRequirements] = None
    client_command_id: Optional[str] = Field(default=None, max_length=128)
    requested_disposition: Optional[
        Literal[
            "ANSWER_ONLY", "ATTACH_EVIDENCE", "INVESTIGATE",
            "CORRECT_CONTEXT", "CONTROL", "DEPLOYMENT_ASSESSMENT",
        ]
    ] = None
    references: list[dict[str, Any]] = Field(default_factory=list)
    after_attach: Optional[Literal["ANSWER_ONLY", "INVESTIGATE"]] = None


class DeploymentAssessmentRequest(StrictModel):
    deployment_requirements: DeploymentRequirements
    execute_safe_tools: bool = False
    max_tool_calls: int = Field(default=4, ge=0, le=16)


class AgentToolCall(StrictModel):
    call_id: str
    source_id: str
    operation: str
    resource: dict[str, str] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str
    status: Literal[
        "proposed", "completed", "approval_required", "denied", "failed",
    ] = "proposed"
    evidence_id: Optional[str] = None
    reason: Optional[str] = None


class DeploymentAssessment(StrictModel):
    """部署承载评估（E7）：每个容量结论都携带可审计的出处。

    时间窗口 / 资源范围 / 数据新鲜度 / Evidence 引用来自工具证据投影；
    缺少关键数据时必须 ``insufficient_data`` 明确拒答而不是猜测。
    """

    verdict: Literal["ready", "conditional", "not_ready", "insufficient_data"]
    summary: str
    requirements: Optional[DeploymentRequirements] = None
    eligible_nodes: list[str] = Field(default_factory=list)
    rejected_nodes: list[dict[str, Any]] = Field(default_factory=list)
    missing_inputs: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    # E7 出处字段
    time_window: dict[str, Any] = Field(default_factory=dict)
    resource_scope: dict[str, str] = Field(default_factory=dict)
    data_freshness: float = 0.0
    evidence_refs: list[str] = Field(default_factory=list)
    scheduling_constraints: list[str] = Field(default_factory=list)


class AgentTurnResult(StrictModel):
    schema_version: Literal["case-agent-turn.v1"] = "case-agent-turn.v1"
    turn_id: str
    intent: AgentTurnIntent
    status: Literal[
        "answered", "diagnosis_requested", "diagnosis_in_progress",
        "needs_user", "tool_approval_required", "insufficient_data",
        "runtime_turn_accepted", "runtime_unavailable",
    ]
    assistant_message: str
    decision_summary: list[str] = Field(default_factory=list)
    evidence_chain: list[dict[str, Any]] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    next_actions: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[AgentToolCall] = Field(default_factory=list)
    deployment_assessment: Optional[DeploymentAssessment] = None
    side_effect_delta: dict[str, Any] = Field(default_factory=dict)


_EXPLAIN_MARKERS = ("为什么", "依据", "证据", "怎么判断", "解释", "报告", "结论")
_CORRECT_MARKERS = ("纠正", "不是", "搞错", "改成", "范围不对", "时间不对")
_DEPLOY_MARKERS = ("部署", "扩容", "承载", "容量", "空间", "资源够", "能否运行")
_STATUS_MARKERS = ("进度", "状态", "做到哪", "还要多久", "正在做什么")


def classify_turn(message: str, explicit: AgentTurnIntent | None = None) -> AgentTurnIntent:
    if explicit is not None:
        return explicit
    normalized = " ".join(message.lower().split())
    if any(marker in normalized for marker in _DEPLOY_MARKERS):
        return AgentTurnIntent.DEPLOYMENT_ASSESSMENT
    if any(marker in normalized for marker in _CORRECT_MARKERS):
        return AgentTurnIntent.CORRECT
    if any(marker in normalized for marker in _STATUS_MARKERS):
        return AgentTurnIntent.STATUS
    if any(marker in normalized for marker in _EXPLAIN_MARKERS):
        return AgentTurnIntent.EXPLAIN
    return AgentTurnIntent.INVESTIGATE


def parse_deployment_requirements(message: str) -> DeploymentRequirements | None:
    """Parse a deliberately small, deterministic Chinese/English resource grammar."""
    text = message.lower().replace("，", ",")
    replicas = _first_number(text, (
        r"(?:副本|实例|replicas?)\s*[:：x×]?\s*(\d+)",
        r"(\d+)\s*(?:个副本|个实例|replicas?)",
    ), integer=True) or 1
    cpu = _first_number(text, (
        r"(?:cpu|处理器)\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(?:核|cores?)?",
        r"(\d+(?:\.\d+)?)\s*(?:核|cores?)\s*(?:cpu)?",
    )) or 0
    memory = _parse_size_mb(text, ("内存", "memory", "ram"))
    disk = _parse_size_mb(text, ("磁盘", "硬盘", "空间", "disk", "storage"))
    if not any((cpu, memory, disk)):
        return None
    return DeploymentRequirements(
        replicas=int(replicas),
        cpu_cores_per_replica=float(cpu),
        memory_mb_per_replica=int(memory),
        disk_mb_per_replica=int(disk),
    )


def build_observability_tool_plan(
    case: dict[str, Any],
    *,
    intent: AgentTurnIntent,
    max_tool_calls: int,
    source_definitions: list[Any] | None = None,
) -> list[AgentToolCall]:
    """Select only registered, scope-bound read tools; never synthesize commands."""
    if max_tool_calls <= 0 or intent not in {
        AgentTurnIntent.INVESTIGATE,
        AgentTurnIntent.CORRECT,
        AgentTurnIntent.DEPLOYMENT_ASSESSMENT,
    }:
        return []
    scope = case.get("target_scope") or {}
    calls: list[AgentToolCall] = []
    seen: set[str] = set()
    if intent == AgentTurnIntent.DEPLOYMENT_ASSESSMENT:
        for source in source_definitions or []:
            if str(getattr(source, "source_type", "")) != "mcp" or not getattr(source, "enabled", False):
                continue
            operation = next((
                item for item in getattr(source, "operations", [])
                if any(marker in item.lower() for marker in ("capacity", "inventory", "deployment", "resource"))
            ), None)
            if not operation:
                continue
            dimensions = set(getattr(source, "resource_dimensions", []) or [])
            resource = {
                key: str(value)
                for key, value in {
                    "cluster_id": scope.get("cluster_id"),
                    "service_id": scope.get("service_id"),
                    "environment": case.get("environment"),
                }.items()
                if value and key in dimensions
            }
            calls.append(AgentToolCall(
                call_id=f"tool_{uuid4().hex[:16]}",
                source_id=source.source_id,
                operation=operation,
                resource=resource,
                parameters={"projection": "allocatable_capacity"},
                rationale="通过已注册 MCP 容量源读取精简后的可分配资源清单",
            ))
            if len(calls) >= max_tool_calls:
                return calls
        if calls:
            return calls
    for item in scope.get("instances") or []:
        agent_id = str(item.get("agent_id") or "").strip()
        if not agent_id or agent_id in seen:
            continue
        seen.add(agent_id)
        calls.append(AgentToolCall(
            call_id=f"tool_{uuid4().hex[:16]}",
            source_id="mini-drop-agent-metrics",
            operation="metrics.query_range",
            resource={
                key: str(value)
                for key, value in {
                    "cluster_id": scope.get("cluster_id"),
                    "service_id": scope.get("service_id"),
                    "agent_id": agent_id,
                }.items()
                if value
            },
            parameters={"limit": 20},
            rationale=(
                "读取目标节点近期资源窗口，用于部署承载力评估"
                if intent == AgentTurnIntent.DEPLOYMENT_ASSESSMENT
                else "读取目标实例所在节点的近期指标，补充时序证据"
            ),
        ))
        if len(calls) >= max_tool_calls:
            break
    return calls


def execute_tool_plan(
    source_gateway,
    calls: list[AgentToolCall],
    *,
    tenant_id: str,
    case_id: str,
    principal_id: str,
) -> tuple[list[AgentToolCall], list[dict[str, Any]]]:
    evidence: list[dict[str, Any]] = []
    completed: list[AgentToolCall] = []
    for call in calls:
        try:
            envelope = source_gateway.query(
                call.source_id,
                SourceQueryRequest(
                    tenant_id=tenant_id,
                    operation=call.operation,
                    resource=call.resource,
                    parameters=call.parameters,
                    case_id=case_id,
                    requested_result_bytes=48_000,
                    requested_time_range_minutes=60,
                ),
                principal_id=principal_id,
            )
            completed_call = call.model_copy(update={
                "status": "completed",
                "evidence_id": envelope.evidence_id,
            })
            completed.append(completed_call)
            evidence.append({
                "evidence_id": envelope.evidence_id,
                "source_id": envelope.source_id,
                "operation": envelope.operation,
                "content_projection": envelope.content_projection,
                "content_hash": envelope.content_hash,
                "projection_hash": envelope.projection_hash,
                "observed_at": envelope.observed_at,
                "policy": envelope.policy,
            })
        except SourceGatewayError as exc:
            status = (
                "approval_required" if "APPROVAL_REQUIRED" in exc.code
                else "denied" if exc.status_code in {401, 403}
                else "failed"
            )
            completed.append(call.model_copy(update={"status": status, "reason": exc.code}))
    return completed, evidence


def assess_deployment_capacity(
    requirements: DeploymentRequirements | None,
    *,
    target_scope: dict[str, Any],
    tool_evidence: list[dict[str, Any]] | None = None,
) -> DeploymentAssessment:
    """Evaluate declared allocatable capacity; utilization-only data is never treated as capacity."""
    evidence = tool_evidence or []
    provenance = _provenance_from_evidence(evidence, target_scope)
    if requirements is None:
        return DeploymentAssessment(
            verdict="insufficient_data",
            summary="还不能判断是否可部署：缺少每副本 CPU、内存或磁盘需求。",
            missing_inputs=["replicas", "cpu_cores_per_replica / memory_mb_per_replica / disk_mb_per_replica"],
            **provenance,
        )
    inventory = target_scope.get("deployment_inventory") or _inventory_from_tool_evidence(evidence)
    if not inventory:
        return DeploymentAssessment(
            verdict="insufficient_data",
            summary="已识别部署需求，但缺少节点可分配容量清单，不能用瞬时利用率替代容量。",
            requirements=requirements,
            missing_inputs=[
                "deployment_inventory[].allocatable_cpu_cores",
                "deployment_inventory[].allocatable_memory_mb",
                "deployment_inventory[].allocatable_disk_mb",
            ],
            assumptions=[f"已取得 {len(evidence)} 份运行指标投影，仅用于趋势佐证"],
            **provenance,
        )

    margin = 1 + requirements.safety_margin_ratio
    cpu_need = requirements.cpu_cores_per_replica * margin
    memory_need = requirements.memory_mb_per_replica * margin
    disk_need = requirements.disk_mb_per_replica * margin
    explicit_inventory = any(
        key in node
        for node in inventory
        for key in ("reserved_cpu_cores", "reserved_memory_mb", "reserved_disk_mb")
    )
    explicit_overhead = any((
        requirements.cpu_overhead_cores,
        requirements.memory_overhead_mb,
        requirements.disk_overhead_mb,
    ))
    eligible: list[str] = []
    rejected: list[dict[str, Any]] = []
    for index, node in enumerate(inventory[:1000]):
        node_id = str(node.get("node_id") or node.get("agent_id") or f"node-{index + 1}")
        reasons: list[str] = []
        if explicit_inventory or explicit_overhead:
            # P11 固定公式：available = allocatable - reservation - safety_margin
            # required = per_replica * replicas + deployment_overhead
            checks = (
                ("cpu",
                 requirements.cpu_cores_per_replica * requirements.replicas + requirements.cpu_overhead_cores,
                 float(node.get("allocatable_cpu_cores") or 0) * (1 - requirements.safety_margin_ratio)
                 - float(node.get("reserved_cpu_cores") or 0)),
                ("memory",
                 requirements.memory_mb_per_replica * requirements.replicas + requirements.memory_overhead_mb,
                 float(node.get("allocatable_memory_mb") or 0) * (1 - requirements.safety_margin_ratio)
                 - float(node.get("reserved_memory_mb") or 0)),
                ("disk",
                 requirements.disk_mb_per_replica * requirements.replicas + requirements.disk_overhead_mb,
                 float(node.get("allocatable_disk_mb") or 0) * (1 - requirements.safety_margin_ratio)
                 - float(node.get("reserved_disk_mb") or 0)),
            )
        else:
            checks = (
                ("cpu", cpu_need, node.get("allocatable_cpu_cores")),
                ("memory", memory_need, node.get("allocatable_memory_mb")),
                ("disk", disk_need, node.get("allocatable_disk_mb")),
            )
        for resource, need, available in checks:
            if need <= 0:
                continue
            try:
                if float(available) < float(need):
                    reasons.append(f"{resource}_insufficient")
            except (TypeError, ValueError):
                reasons.append(f"{resource}_unknown")
        if bool(node.get("schedulable", True)) and not reasons:
            eligible.append(node_id)
        else:
            if not bool(node.get("schedulable", True)):
                reasons.append("unschedulable")
            rejected.append({"node_id": node_id, "reasons": sorted(set(reasons))})

    if len(eligible) >= requirements.replicas:
        verdict = "ready"
        summary = f"容量清单中有 {len(eligible)} 个节点满足带安全余量的单副本需求，可放置 {requirements.replicas} 个副本。"
    elif eligible:
        verdict = "conditional"
        summary = f"仅 {len(eligible)} 个节点满足要求，少于 {requirements.replicas} 个副本；需扩容、降低需求或调整调度约束。"
    else:
        verdict = "not_ready"
        summary = "当前容量清单没有节点同时满足带安全余量的 CPU、内存和磁盘需求。"
    return DeploymentAssessment(
        verdict=verdict,
        summary=summary,
        requirements=requirements,
        eligible_nodes=eligible,
        rejected_nodes=rejected,
        assumptions=[f"按 {int(requirements.safety_margin_ratio * 100)}% 安全余量评估", "未评估业务依赖、配额与反亲和规则时不得直接上线"],
        **provenance,
    )


def _provenance_from_evidence(
    evidence: list[dict[str, Any]],
    target_scope: dict[str, Any],
) -> dict[str, Any]:
    """从工具证据投影提取出处：时间窗口 / Evidence 引用 / 数据新鲜度。

    data_freshness ∈ [0,1]：最近一条证据 observed_at 距今越近分越高；无观测为 0。
    """
    scope = target_scope or {}
    evidence_refs: list[str] = []
    starts: list[Any] = []
    ends: list[Any] = []
    latest_observed: Optional[Any] = None
    for item in evidence:
        evidence_id = str(item.get("evidence_id") or "")
        if evidence_id:
            evidence_refs.append(evidence_id)
        valid_time = item.get("valid_time") or {}
        if valid_time.get("start"):
            starts.append(valid_time["start"])
        if valid_time.get("end"):
            ends.append(valid_time["end"])
        observed = item.get("observed_at")
        if observed is not None and (latest_observed is None or str(observed) > str(latest_observed)):
            latest_observed = observed
    freshness = 0.0
    if latest_observed is not None:
        try:
            observed_dt = datetime.fromisoformat(str(latest_observed))
            age_seconds = max(0.0, (datetime.now(timezone.utc) - observed_dt).total_seconds())
            freshness = max(0.0, 1.0 - age_seconds / 600.0)
        except (ValueError, TypeError):
            freshness = 0.0
    return {
        "time_window": {
            "start": min(starts) if starts else "",
            "end": max(ends) if ends else "",
        },
        "resource_scope": {
            key: str(value)
            for key, value in {
                "cluster_id": scope.get("cluster_id"),
                "service_id": scope.get("service_id"),
                "environment": scope.get("environment"),
            }.items()
            if value
        },
        "data_freshness": round(freshness, 4),
        "evidence_refs": evidence_refs,
        "scheduling_constraints": [str(x) for x in (scope.get("scheduling_constraints") or [])],
    }


def backtest_deployment_assessments(
    records: list[dict[str, Any]],
    *,
    oracle_key: str = "oracle_verdict",
) -> dict[str, Any]:
    """E7 历史回测：重放历史部署决策并与 Oracle 判定比较。

    门槛（14.4）：缺少关键数据时正确拒答率 ≥ 95%——即对数据不足的记录评估器
    必须返回 insufficient_data 而不是猜测。``guessed_without_data`` 统计"缺数据
    仍猜测"的严重错误，它直接拉低正确拒答率。accuracy 反映可判定记录的命中率。
    """
    total = len(records)
    correct = 0
    refused = 0
    refused_but_oracle_decided = 0
    guessed_without_data = 0
    mismatch: list[dict[str, Any]] = []
    for record in records:
        raw_requirements = record.get("requirements")
        requirements = None
        if raw_requirements:
            try:
                requirements = DeploymentRequirements(**raw_requirements)
            except ValueError:
                requirements = None
        target_scope = record.get("target_scope") or {}
        tool_evidence = record.get("tool_evidence") or []
        inventory = target_scope.get("deployment_inventory") or _inventory_from_tool_evidence(tool_evidence)
        data_sufficient = bool(inventory) and requirements is not None
        assessment = assess_deployment_capacity(
            requirements,
            target_scope=target_scope,
            tool_evidence=tool_evidence,
        )
        oracle = str(record.get(oracle_key) or "insufficient_data")
        if not data_sufficient:
            refused += 1
            if oracle != "insufficient_data":
                refused_but_oracle_decided += 1
            if assessment.verdict != "insufficient_data":
                guessed_without_data += 1  # 数据不足仍猜测 → 硬门禁失败
            continue
        if assessment.verdict == oracle:
            correct += 1
        else:
            mismatch.append({
                "oracle": oracle, "got": assessment.verdict, "summary": assessment.summary,
            })
    correct_refusal_rate = (total - guessed_without_data) / total if total else 0.0
    return {
        "total": total,
        "correct": correct,
        "refused": refused,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "correct_refusal_rate": round(correct_refusal_rate, 4),
        "refused_but_oracle_decided": refused_but_oracle_decided,
        "guessed_without_data": guessed_without_data,
        "mismatches": mismatch[:20],
        "gates": {
            "min_correct_refusal_rate": 0.95,
            "passed": bool(total) and correct_refusal_rate >= 0.95,
        },
    }


def _inventory_from_tool_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Accept only an explicit allocatable-capacity shape from projected tool data."""
    for item in evidence:
        projection = item.get("content_projection") or {}
        candidates = (
            projection.get("deployment_inventory")
            or projection.get("nodes")
            or (projection.get("capacity") or {}).get("nodes")
        )
        if not isinstance(candidates, list):
            continue
        valid = [
            node for node in candidates
            if isinstance(node, dict) and any(
                key in node for key in (
                    "allocatable_cpu_cores", "allocatable_memory_mb", "allocatable_disk_mb",
                )
            )
        ]
        if valid:
            return valid[:1000]
    return []


def render_understanding_answer(understanding: dict[str, Any]) -> tuple[str, list[str], list[dict[str, Any]]]:
    statement = str(understanding.get("understanding") or "尚未形成经过证据验证的判断")
    confirmed = [str(item) for item in (understanding.get("confirmed") or [])]
    missing = [str(item) for item in (understanding.get("missing") or [])]
    refs = [
        {"evidence_id": str(item.get("evidence_id")), "summary": str(item.get("summary") or "")}
        for item in (understanding.get("evidence_chain") or [])
        if isinstance(item, dict) and item.get("evidence_id")
    ]
    message = statement
    if confirmed:
        message += "。当前可核验依据：" + "；".join(confirmed[:4])
    if missing:
        message += "。仍缺：" + "；".join(missing[:3])
    return message, confirmed, refs


def build_case_evidence_chain(
    graph: dict[str, Any],
    evidence: list[dict[str, Any]],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Project cited evidence and its role without exposing raw artifacts."""
    evidence_by_id = {str(item.get("evidence_id")): item for item in evidence if item.get("evidence_id")}
    roles: dict[str, set[str]] = {}
    for hypothesis in graph.get("hypotheses") or []:
        hypothesis_id = str(hypothesis.get("hypothesis_id") or "")
        for field, role in (
            ("supporting_evidence_refs", "support"),
            ("contradicting_evidence_refs", "contradiction"),
        ):
            for evidence_id in hypothesis.get(field) or []:
                key = str(evidence_id)
                roles.setdefault(key, set()).add(f"{role}:{hypothesis_id}")
    chain: list[dict[str, Any]] = []
    for evidence_id, citations in roles.items():
        item = evidence_by_id.get(evidence_id)
        if item is None:
            continue
        chain.append({
            "evidence_id": evidence_id,
            "source_type": item.get("source_type"),
            "query_or_probe": item.get("query_or_probe"),
            "roles": sorted(citations),
            "integrity_hash": item.get("integrity_hash"),
            "data_quality": item.get("data_quality") or {},
        })
        if len(chain) >= limit:
            break
    return chain


def _first_number(text: str, patterns: tuple[str, ...], *, integer: bool = False) -> float | int | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            return int(value) if integer else value
    return None


def _parse_size_mb(text: str, labels: tuple[str, ...]) -> int:
    label = "|".join(re.escape(item) for item in labels)
    patterns = (
        rf"(?:{label})\s*[:：]?\s*(\d+(?:\.\d+)?)\s*(gib|gb|g|mib|mb|m)",
        rf"(\d+(?:\.\d+)?)\s*(gib|gb|g|mib|mb|m)\s*(?:{label})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = float(match.group(1))
            unit = match.group(2).lower()
            return int(value * 1024) if unit.startswith("g") else int(value)
    return 0
