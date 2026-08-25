"""Deterministic operator control shared by HTTP commands and chat turns.

The model runtime is deliberately not involved in this module.  It only
receives the resulting revision/focus after the Case authority has committed
the change.
"""

from __future__ import annotations

import re
from typing import Any

from server.app.agent_runtime.config import AgentRuntimeMode, runtime_mode
from server.app.agent_runtime.dispatcher import get_runtime
from server.app.agent_runtime.port import RuntimeSteer
from server.app.diagnosis.network_discovery import case_dependency_graph_snapshot


def parse_chat_control(message: str) -> dict[str, Any] | None:
    """Parse the intentionally small, deterministic chat control grammar."""
    text = " ".join(str(message or "").strip().split())
    lowered = text.lower()
    if any(token in lowered for token in ("暂停", "pause")):
        return {"kind": "COMMAND", "command": "PAUSE", "reason": text}
    if any(token in lowered for token in ("停止", "stop")):
        return {"kind": "COMMAND", "command": "STOP", "reason": text}
    if any(token in lowered for token in ("恢复", "继续运行", "resume")):
        return {"kind": "COMMAND", "command": "RESUME", "reason": text}

    correction = any(token in lowered for token in ("纠正", "不是", "范围不对", "时间不对"))
    focus_prefix = r"(?:改查|换查|切换到|切到|聚焦|关注|先查|调查) *"
    focus_match = re.search(
        focus_prefix + r"(?:pid|process) *[:#]? *(\d+)", text, flags=re.IGNORECASE,
    ) or re.search(
        focus_prefix + r"([A-Za-z0-9_.:/@-]{2,128})", text, flags=re.IGNORECASE,
    )
    if focus_match:
        ref = focus_match.group(1).rstrip("，。；;。")
        kind = "PROCESS" if re.search(r"(?:pid|进程|process)", lowered) or ref.isdigit() else "SERVICE"
        if ":" in ref and ("edge" in lowered or "依赖" in lowered or "链路" in lowered):
            kind = "DEPENDENCY_EDGE"
        return {
            "kind": "FOCUS",
            "focus_kind": kind,
            "focus_ref": ref,
            "reason": text,
            "correction": correction,
        }
    if correction:
        return {"kind": "CORRECT_CONTEXT", "reason": text}
    return None


def notify_runtime_abort(case_id: str, reason: str) -> dict[str, Any]:
    """Best-effort abort; durable Case state remains authoritative."""
    if runtime_mode() not in {AgentRuntimeMode.PI, AgentRuntimeMode.PI_SHADOW}:
        return {"status": "not_required", "runtime": runtime_mode().value}
    try:
        get_runtime().abort(case_id, reason)
        return {"status": "sent", "action": "abort"}
    except RuntimeError as exc:
        return {"status": "pending", "action": "abort", "error": str(exc)[:240]}


def notify_runtime_steer(
    case_id: str,
    *,
    instruction: str,
    reason_code: str,
    scope_revision: int,
    plan_revision: int = 0,
    focus: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if runtime_mode() not in {AgentRuntimeMode.PI, AgentRuntimeMode.PI_SHADOW}:
        return {"status": "not_required", "runtime": runtime_mode().value}
    try:
        get_runtime().steer(
            case_id,
            RuntimeSteer(
                case_id=case_id,
                instruction=instruction,
                reason_code=reason_code,
                scope_revision=scope_revision,
                plan_revision=plan_revision,
                focus=focus or {},
            ),
        )
        return {"status": "sent", "action": "steer"}
    except RuntimeError as exc:
        return {"status": "pending", "action": "steer", "error": str(exc)[:240]}


def focus_from_case(case: dict[str, Any]) -> dict[str, Any]:
    value = (case.get("target_scope") or {}).get("active_focus")
    return dict(value) if isinstance(value, dict) else {}


def apply_chat_control(
    repository: Any,
    case: dict[str, Any],
    *,
    tenant_id: str,
    actor_id: str,
    control: dict[str, Any],
) -> dict[str, Any]:
    """Commit a parsed chat control and return the new Case plus runtime data."""
    case_id = str(case["case_id"])
    kind = str(control.get("kind") or "")
    reason = str(control.get("reason") or "operator chat control")[:1000]
    if kind == "COMMAND":
        command = str(control.get("command") or "").upper()
        if command not in {"PAUSE", "RESUME", "STOP"}:
            raise ValueError("UNSUPPORTED_CHAT_COMMAND")
        updated = repository.transition_incident_case(
            case_id,
            tenant_id,
            actor_id=actor_id,
            action=command.lower(),
            reason=reason,
        )
        if hasattr(repository, "enqueue_domain_outbox"):
            repository.enqueue_domain_outbox(
                aggregate_type="case_command",
                aggregate_id=case_id,
                event_type=f"CONTROL_{command}",
                payload={"command": command, "reason": reason, "source": "chat"},
                dedupe_key=f"chat-control:{case_id}:{command}:{int((updated or case).get('case_command_revision') or 0)}",
            )
        repository.record_case_event(
            case_id,
            tenant_id,
            event_type="control.applied",
            payload={"command": command, "reason": reason, "source": "chat"},
            actor_id=actor_id,
        )
        runtime = notify_runtime_abort(case_id, reason) if command in {"PAUSE", "STOP"} else {"status": "resume_pending"}
        return {"command": command, "case": updated or case, "runtime": {"abort": runtime}}
    if kind == "CORRECT_CONTEXT":
        updated = repository.correct_incident_case(
            case_id,
            tenant_id,
            actor_id=actor_id,
            changes={"target_scope": case.get("target_scope") or {}},
            reason=reason,
            expected_row_version=case.get("row_version"),
        )
        updated = updated or case
        abort = notify_runtime_abort(case_id, reason)
        steer = notify_runtime_steer(
            case_id,
            instruction=f"用户纠正了当前上下文：{reason}。重新读取最新 Case Snapshot。",
            reason_code="CONTROL_CHANGED",
            scope_revision=int(updated.get("scope_revision") or 1),
        )
        return {"command": "CORRECT_CONTEXT", "case": updated, "runtime": {"abort": abort, "steer": steer}}
    if kind == "FOCUS":
        focus_kind = str(control.get("focus_kind") or "").upper()
        focus_ref = str(control.get("focus_ref") or "")
        graph = case_dependency_graph_snapshot(repository, case_id, tenant_id)
        nodes = graph.get("graph", {}).get("nodes") or []
        edges = graph.get("graph", {}).get("edges") or []
        scope = case.get("target_scope") or {}
        allowed = (
            focus_kind == "SERVICE" and (
                focus_ref in {str(scope.get(key) or "") for key in ("service_id", "service", "service_name")}
                or any(str(node.get("entity_id")) == focus_ref and node.get("entity_type") in {"service", "instance"} for node in nodes)
            )
            or focus_kind == "PROCESS" and any(str(node.get("entity_id")) == focus_ref and node.get("entity_type") == "process" for node in nodes)
            or focus_kind == "DEPENDENCY_EDGE" and any(str(edge.get("edge_id")) == focus_ref for edge in edges)
        )
        if not allowed:
            raise ValueError("FOCUS_TARGET_NOT_AUTHORIZED_BY_DISCOVERY")
        focus = {
            "focus_kind": focus_kind,
            "focus_ref": focus_ref,
            "reason": reason,
            "focus_revision": int(case.get("scope_revision") or 1) + 1,
            "graph_digest": graph.get("graph_digest"),
        }
        new_scope = dict(scope)
        new_scope["active_focus"] = focus
        updated = repository.correct_incident_case(
            case_id,
            tenant_id,
            actor_id=actor_id,
            changes={"target_scope": new_scope},
            reason=f"focus_changed:{focus_kind}:{focus_ref}:{reason}",
            expected_row_version=case.get("row_version"),
        ) or case
        focus["focus_revision"] = int(updated.get("scope_revision") or focus["focus_revision"])
        abort = notify_runtime_abort(case_id, reason)
        steer = notify_runtime_steer(
            case_id,
            instruction=f"切换调查焦点到 {focus_kind}:{focus_ref}。原因：{reason}",
            reason_code="FOCUS_CHANGED",
            scope_revision=int(updated.get("scope_revision") or 1),
            focus=focus,
        )
        return {"command": "FOCUS", "focus": focus, "case": updated, "runtime": {"abort": abort, "steer": steer}}
    raise ValueError("UNSUPPORTED_CHAT_CONTROL")
