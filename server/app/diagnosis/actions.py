"""结构化诊断动作及可信命令预览渲染。"""

from __future__ import annotations

import shlex
from typing import Any

from server.app.diagnosis.schemas import ActionTarget, DiagnosisAction


def inspect_session_action(
    diagnosis_id: str,
    evidence_refs: list[str],
) -> dict[str, Any]:
    command = shlex.join([
        "curl", "-s", f"http://localhost:8191/api/v1/diagnoses/{diagnosis_id}",
    ])
    return _with_legacy_fields(DiagnosisAction(
        action_id="act_review_session",
        action_type="inspect",
        title="回看诊断证据链",
        target=ActionTarget(diagnosis_id=diagnosis_id),
        rendered_command=command,
        comment="只读查询当前诊断会话，核对流水线、Finding、evidence_refs 和探针状态。",
        risk_level="R0",
        approval_policy="read_only",
        evidence_refs=evidence_refs,
        confidence_level="高",
    ))


def inspect_command_action(
    *,
    action_id: str,
    title: str,
    argv: list[str],
    comment: str,
    diagnosis_id: str,
    confidence_level: str = "高",
) -> dict[str, Any]:
    return _with_legacy_fields(DiagnosisAction(
        action_id=action_id,
        action_type="inspect",
        title=title,
        target=ActionTarget(diagnosis_id=diagnosis_id),
        rendered_command=shlex.join(argv),
        comment=comment,
        risk_level="R0",
        approval_policy="read_only",
        evidence_refs=[],
        confidence_level=confidence_level,
    ))


def collect_action(
    *,
    action_id: str,
    title: str,
    collector_type: str,
    target: dict[str, Any],
    duration_sec: int,
    sample_rate: int,
    comment: str,
    risk_level: str,
    evidence_refs: list[str],
    confidence_level: str,
) -> dict[str, Any]:
    command = shlex.join([
        "micro-drop", "collect",
        "--agent", str(target.get("agent_id", "")),
        "--pid", str(target.get("pid", "")),
        "--collector", collector_type,
        "--duration", str(duration_sec),
        "--sample-rate", str(sample_rate),
        "--watch",
    ])
    requires_approval = risk_level == "R2"
    policy = "single_execution" if requires_approval else "auto_low_risk"
    action = DiagnosisAction(
        action_id=action_id,
        action_type="collect",
        title=title,
        collector_type=collector_type,
        target=ActionTarget(**{
            key: target.get(key)
            for key in ("service_id", "instance_id", "host_id", "agent_id", "pid")
            if target.get(key) is not None
        }),
        parameters={"duration_sec": duration_sec, "sample_rate": sample_rate, "watch": True},
        rendered_command=command,
        comment=comment,
        risk_level=risk_level,
        approval_policy=policy,
        requires_approval=requires_approval,
        evidence_refs=list(dict.fromkeys(evidence_refs)),
        confidence_level=confidence_level,
    )
    return _with_legacy_fields(action)


def _with_legacy_fields(action: DiagnosisAction) -> dict[str, Any]:
    """兼容旧前端字段；真实契约以 action_* 与 rendered_command 为准。"""
    result = action.model_dump(mode="json")
    result["command_id"] = action.action_id.replace("act_", "cmd_", 1)
    result["command"] = action.rendered_command
    result["confidence"] = {"高": 0.9, "中": 0.65, "低": 0.35, "不可判断": 0.0}[action.confidence_level]
    return result
