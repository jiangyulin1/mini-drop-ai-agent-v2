"""P7 生产治理控制：全局 Red Button、影子模式、Capability Key 轮换纪元。

- Red Button：全局停止所有自治执行（Case Supervisor 在推进前检查）；
- 影子模式：Case 只诊断不执行恢复动作（自治 Agent 在动作前检查）；
- Capability Key 纪元：轮换后所有旧 Capability Key 失效，强制重新签发。
"""

from __future__ import annotations

from typing import Any

RED_BUTTON = "red_button"
CAPABILITY_EPOCH = "capability_key_epoch"


def is_red_button_active(repo) -> bool:
    control = repo.get_system_control(RED_BUTTON)
    return bool(control and control.get("enabled"))


def is_shadow_mode(case: dict[str, Any]) -> bool:
    """Case 是否处于影子模式：诊断/验证照常，但跳过恢复动作执行。"""
    policy = (case.get("target_scope") or {}).get("autonomy_policy") or {}
    if isinstance(policy, dict) and policy.get("shadow"):
        return True
    return bool((case.get("target_scope") or {}).get("shadow_mode"))


def current_capability_epoch(repo) -> int:
    control = repo.get_system_control(CAPABILITY_EPOCH)
    if not control:
        return 0
    try:
        return int((control.get("value") or {}).get("epoch", 0) or 0)
    except (TypeError, ValueError):
        return 0


def issue_capability_key(repo, *, principal_id: str, source_ids: list[str]) -> dict[str, Any]:
    """签发绑定当前轮换纪元的 Capability Key（轮换后旧 Key 失效）。"""
    epoch = current_capability_epoch(repo)
    return {
        "principal_id": principal_id,
        "source_ids": source_ids,
        "capability_epoch": epoch,
        "issued_at": epoch > 0,
    }
