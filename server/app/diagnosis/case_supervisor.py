"""Case Supervisor：后台常驻推进非终态事故 Case 的持久化调度器。

职责（对照实施方案 P2）：
- 用短租约保证同一 Case 只被一个 Control 副本推进；
- 用户命令（pause/resume/stop/correction）进入持久化命令队列，统一按序处理；
- Stop 优先级高于后台结果：处理 stop 后不再推进该 Case 的自治循环；
- 扫描非终态 Case，竞争租约后从最后一个已提交步骤继续；
- 处理命令后递增 scope_revision，使迟到结果按版本隔离。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from server.app.diagnosis.autonomous_agent import AutonomousIncidentAgent
from server.app.diagnosis.governance import is_red_button_active

_STOPPED_STATES = {"PAUSED", "STOPPED", "RESOLVED", "INSUFFICIENT_EVIDENCE"}


class CaseSupervisor:
    """包装 AutonomousIncidentAgent 的租约化后台推进器。"""

    def __init__(
        self,
        repo,
        agent: AutonomousIncidentAgent,
        orchestrator=None,
        *,
        lease_ttl_seconds: int = 120,
        owner_prefix: str = "case-supervisor",
    ):
        self.repo = repo
        self.agent = agent
        self.orchestrator = orchestrator
        self.lease_ttl = max(10, min(int(lease_ttl_seconds), 600))
        self._owner_prefix = f"{owner_prefix}-{uuid4().hex[:6]}"

    # ── 扫描与推进 ──────────────────────────────────────────────────

    def scan_and_advance(self, tenant_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """列出未被有效租约持有的非终态 Case 并逐个推进一步。"""
        advanced: list[dict[str, Any]] = []
        for case in self.repo.list_unleased_cases(tenant_id, limit=limit):
            result = self.advance_case(case["case_id"], tenant_id)
            advanced.append({"case_id": case["case_id"], **result})
        return advanced

    def advance_case(self, case_id: str, tenant_id: str) -> dict[str, Any]:
        """租约内推进一个 Case：先处理命令，再走一步自治循环。"""
        # P7：全局 Red Button 停止所有自治推进。
        if is_red_button_active(self.repo):
            return {"outcome": "RED_BUTTON_ACTIVE"}
        owner = f"{self._owner_prefix}:{uuid4().hex}"
        if not self.repo.acquire_case_lease(
            case_id, tenant_id, owner=owner, ttl_seconds=self.lease_ttl,
        ):
            return {"outcome": "LEASE_BUSY"}
        try:
            stopped = self._process_commands(case_id, tenant_id)
            if stopped:
                return {"outcome": "STOPPED_BY_COMMAND"}
            return self.agent.step(case_id, tenant_id)
        finally:
            self.repo.release_case_lease(case_id, tenant_id, owner)

    # ── 命令队列 ───────────────────────────────────────────────────

    def enqueue_command(
        self,
        case_id: str,
        tenant_id: str,
        *,
        command_type: str,
        idempotency_key: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """把用户命令写入持久化队列（幂等）。"""
        return self.repo.enqueue_case_command(
            case_id, tenant_id,
            command_type=command_type,
            idempotency_key=idempotency_key,
            payload=payload or {},
        )

    def _process_commands(self, case_id: str, tenant_id: str) -> bool:
        """按序应用待处理命令；返回是否因 stop 而应停止本轮推进。"""
        stopped = False
        for command in self.repo.list_pending_case_commands(case_id, tenant_id):
            ctype = str(command.get("command_type") or "")
            payload = command.get("payload") or {}
            reason = str(payload.get("reason") or "case supervisor command")
            actor = str(payload.get("actor_id") or "case-supervisor")
            try:
                if ctype == "stop":
                    self._apply_stop(case_id, tenant_id, reason, actor)
                    stopped = True
                elif ctype == "pause":
                    self._apply_pause(case_id, tenant_id, reason, actor)
                    stopped = True
                elif ctype == "resume":
                    self._apply_resume(case_id, tenant_id, reason, actor)
                elif ctype == "correction":
                    self._apply_correction(case_id, tenant_id, payload, actor)
            except ValueError:
                # 状态机拒绝的非法迁移按幂等处理：命令已消费，不再重放。
                pass
            self.repo.complete_case_command(command["command_id"])
        return stopped

    def _apply_stop(self, case_id: str, tenant_id: str, reason: str, actor: str) -> None:
        current = self.repo.get_incident_case(case_id, tenant_id)
        diagnosis_id = (current or {}).get("diagnosis_session_id")
        if diagnosis_id and self.orchestrator is not None:
            try:
                self.orchestrator.cancel(diagnosis_id, reason)
            except ValueError:
                pass
        self.repo.transition_incident_case(
            case_id, tenant_id, actor_id=actor, action="stop", reason=reason,
        )

    def _apply_pause(self, case_id: str, tenant_id: str, reason: str, actor: str) -> None:
        current = self.repo.get_incident_case(case_id, tenant_id)
        diagnosis_id = (current or {}).get("diagnosis_session_id")
        if diagnosis_id and self.orchestrator is not None:
            try:
                self.orchestrator.pause(diagnosis_id)
            except ValueError:
                pass
        self.repo.transition_incident_case(
            case_id, tenant_id, actor_id=actor, action="pause", reason=reason,
        )

    def _apply_resume(self, case_id: str, tenant_id: str, reason: str, actor: str) -> None:
        current = self.repo.get_incident_case(case_id, tenant_id)
        diagnosis_id = (current or {}).get("diagnosis_session_id")
        if diagnosis_id and self.orchestrator is not None:
            try:
                self.orchestrator.resume(diagnosis_id)
            except ValueError:
                pass
        self.repo.transition_incident_case(
            case_id, tenant_id, actor_id=actor, action="resume", reason=reason,
        )

    def _apply_correction(
        self, case_id: str, tenant_id: str, payload: dict[str, Any], actor: str,
    ) -> None:
        """用户修正目标/拓扑/时间窗：递增 scope_revision 使旧计划失效。"""
        self.repo.correct_incident_case(
            case_id, tenant_id, actor_id=actor,
            changes=payload.get("changes") or {},
            reason=str(payload.get("reason") or "scope correction"),
            expected_row_version=payload.get("expected_row_version"),
        )
