"""结构化诊断动作及可信命令预览渲染。"""

from __future__ import annotations

import shlex
from typing import Any

from server.app.diagnosis.schemas import ActionTarget, DiagnosisAction


DEFAULT_API_KEY_ENV = "MINI_DROP_API_KEY"
DEFAULT_SERVER_URL = "http://localhost:8191"


def render_action_argv(value: DiagnosisAction | dict[str, Any]) -> list[str]:
    action = value if isinstance(value, DiagnosisAction) else DiagnosisAction.model_validate(value)
    url = str(action.parameters.get("server_url", DEFAULT_SERVER_URL))
    key_env = str(action.parameters.get("api_key_env", DEFAULT_API_KEY_ENV))
    if action.action_type == "collect":
        return [
            "micro-drop", "collect", "--url", url, "--api-key-env", key_env,
            "--agent", str(action.target.agent_id), "--pid", str(action.target.pid),
            "--collector", str(action.collector_type),
            "--duration", str(int(action.parameters["duration_sec"])),
            "--sample-rate", str(int(action.parameters["sample_rate"])),
            "--watch",
        ]
    argv = action.parameters.get("argv")
    if isinstance(argv, list) and argv:
        return [str(item) for item in argv]
    if action.action_type == "inspect" and action.target.diagnosis_id:
        return [
            "micro-drop", "diagnosis-inspect", "--url", url,
            "--api-key-env", key_env, "--diagnosis-id", action.target.diagnosis_id,
        ]
    raise ValueError(f"Action 无法渲染: {action.action_id}")


def render_action_command(value: DiagnosisAction | dict[str, Any]) -> str:
    return shlex.join(render_action_argv(value))


def _rendered(action: DiagnosisAction) -> DiagnosisAction:
    return action.model_copy(update={"rendered_command": render_action_command(action)})


def inspect_session_action(
    diagnosis_id: str,
    evidence_refs: list[str],
) -> dict[str, Any]:
    return _with_legacy_fields(_rendered(DiagnosisAction(
        action_id="act_review_session",
        action_type="inspect",
        title="回看诊断证据链",
        target=ActionTarget(diagnosis_id=diagnosis_id),
        rendered_command="pending-render",
        parameters={"server_url": DEFAULT_SERVER_URL, "api_key_env": DEFAULT_API_KEY_ENV},
        comment="只读查询当前诊断会话，核对流水线、Finding、evidence_refs 和探针状态。",
        risk_level="R0",
        approval_policy="read_only",
        evidence_refs=evidence_refs,
        confidence_level="高",
    )))


def inspect_command_action(
    *,
    action_id: str,
    title: str,
    argv: list[str],
    comment: str,
    diagnosis_id: str,
    confidence_level: str = "高",
) -> dict[str, Any]:
    return _with_legacy_fields(_rendered(DiagnosisAction(
        action_id=action_id,
        action_type="inspect",
        title=title,
        target=ActionTarget(diagnosis_id=diagnosis_id),
        rendered_command="pending-render",
        parameters={"argv": argv},
        comment=comment,
        risk_level="R0",
        approval_policy="read_only",
        evidence_refs=[],
        confidence_level=confidence_level,
    )))


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
    requires_approval = risk_level == "R2"
    policy = "single_execution" if requires_approval else "auto_low_risk"
    action = _rendered(DiagnosisAction(
        action_id=action_id,
        action_type="collect",
        title=title,
        collector_type=collector_type,
        target=ActionTarget(**{
            key: target.get(key)
            for key in ("service_id", "instance_id", "host_id", "agent_id", "pid")
            if target.get(key) is not None
        }),
        parameters={"duration_sec": duration_sec, "sample_rate": sample_rate, "watch": True,
                    "server_url": DEFAULT_SERVER_URL, "api_key_env": DEFAULT_API_KEY_ENV},
        rendered_command="pending-render",
        comment=comment,
        risk_level=risk_level,
        approval_policy=policy,
        requires_approval=requires_approval,
        evidence_refs=list(dict.fromkeys(evidence_refs)),
        confidence_level=confidence_level,
    ))
    return _with_legacy_fields(action)


def _with_legacy_fields(action: DiagnosisAction) -> dict[str, Any]:
    """兼容旧前端字段；真实契约以 action_* 与 rendered_command 为准。"""
    result = action.model_dump(mode="json")
    result["command_id"] = action.action_id.replace("act_", "cmd_", 1)
    result["command"] = action.rendered_command
    result["confidence"] = {"高": 0.9, "中": 0.65, "低": 0.35, "不可判断": 0.0}[action.confidence_level]
    return result
