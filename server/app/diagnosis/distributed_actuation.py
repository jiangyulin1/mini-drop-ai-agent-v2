"""Dispatch registered Swarm recovery actions to a manager Agent."""

from __future__ import annotations

import json
import hashlib
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from server.app import storage
from server.app.common_utils import status_value
from server.app.diagnosis.actuation import ActuationError, ActuationGateway
from server.app.schemas import CreateTaskRequest


SWARM_ACTIONS = {"swarm.restart-stateless-service", "swarm.rollback-service"}


class DistributedActuationGateway:
    def __init__(
        self,
        repo,
        local: ActuationGateway,
        audit_callback: Callable[[dict[str, Any]], None] | None = None,
        *,
        lease_store: Callable[[str, str], bool] | None = None,
        release_lease: Callable[[str, str], None] | None = None,
    ):
        self.repo = repo
        self.local = local
        self.audit = audit_callback
        self._attempts: dict[str, dict[str, Any]] = {}
        # 动作级租约：同一 operation_key 只允许一个执行者，防止并发重复处置。
        # 默认用进程内锁表；生产可注入基于 DB 的租约实现。
        self._action_leases: dict[str, str] = {}
        self._lease_store = lease_store
        self._release_lease = release_lease

    def _acquire_action_lease(self, operation_key: str, owner: str) -> bool:
        if self._lease_store is not None:
            return self._lease_store(operation_key, owner)
        if self._action_leases.get(operation_key) not in (None, owner):
            return False
        self._action_leases[operation_key] = owner
        return True

    def _release_action_lease(self, operation_key: str, owner: str) -> None:
        if self._release_lease is not None:
            self._release_lease(operation_key, owner)
            return
        if self._action_leases.get(operation_key) == owner:
            self._action_leases.pop(operation_key, None)

    def dry_run(self, action_id: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if action_id not in SWARM_ACTIONS or not parameters.get("manager_agent_id"):
            return self.local.dry_run(action_id, parameters)
        operation = "preflight_restart" if action_id == "swarm.restart-stateless-service" else "preflight_rollback"
        result = self._dispatch(parameters, operation)
        item = result.get("preflight") or {}
        attempt_id = f"act_{uuid.uuid4().hex[:12]}"
        self._attempts[attempt_id] = {
            "action_id": action_id,
            "parameters": dict(parameters),
            "version_index": item.get("version_index"),
            "stage": "DRY_RUN_COMPLETED",
        }
        self._audit("ACTION_DRY_RUN", action_id, attempt_id, parameters)
        return {
            "attempt_id": attempt_id,
            "action_id": action_id,
            "stage": "DRY_RUN_COMPLETED",
            "dry_run": {"candidate_count": 1, "items": [item], "remote_task_id": result.get("task_id")},
        }

    def execute(self, action_id: str, dry_run_attempt_id: str, environment: str = "production") -> dict[str, Any]:
        if action_id not in SWARM_ACTIONS:
            return self.local.execute(action_id, dry_run_attempt_id, environment)
        attempt = self._attempts.get(dry_run_attempt_id)
        if not attempt or attempt.get("action_id") != action_id:
            raise ActuationError("远程 Swarm dry-run 不存在或动作不匹配")
        if attempt.get("stage") == "COMPLETED":
            return dict(attempt["result"], idempotent_replay=True)
        operation = "restart" if action_id == "swarm.restart-stateless-service" else "rollback"
        parameters = dict(attempt["parameters"])
        parameters["expected_version_index"] = attempt.get("version_index")
        operation_key = str(parameters.get("operation_key") or dry_run_attempt_id)
        owner = f"exec-{uuid.uuid4().hex[:8]}"
        if not self._acquire_action_lease(operation_key, owner):
            raise ActuationError("同一动作已有并发执行，拒绝重复下发")
        try:
            value = self._dispatch(parameters, operation)
        except ActuationError:
            attempt["stage"] = "FAILED"
            self._release_action_lease(operation_key, owner)
            raise
        self._release_action_lease(operation_key, owner)
        result = {
            "attempt_id": dry_run_attempt_id,
            "action_id": action_id,
            "stage": "COMPLETED",
            "executed": [{
                "service_name": value.get("service_name"),
                "before_version_index": value.get("before_version_index"),
                "after_version_index": value.get("after_version_index"),
                "remote_task_id": value.get("task_id"),
            }],
        }
        attempt.update({"stage": "COMPLETED", "result": result})
        self._audit("ACTION_EXECUTED", action_id, dry_run_attempt_id, parameters)
        return result

    def get_attempt(self, attempt_id: str):
        return self.local.get_attempt(attempt_id)

    def restore_dry_run_attempt(
        self,
        *,
        attempt_id: str,
        action_id: str,
        items: list[dict[str, Any]],
        parameters: dict[str, Any],
    ):
        return self.local.restore_dry_run_attempt(
            attempt_id=attempt_id,
            action_id=action_id,
            items=items,
            parameters=parameters,
        )

    def _dispatch(self, parameters: dict[str, Any], operation: str) -> dict[str, Any]:
        agent_id = str(parameters.get("manager_agent_id") or "")
        service_name = str(parameters.get("service_name") or "")
        agent = self.repo.agents.get(agent_id)
        if agent is None or status_value(agent.status) != "ONLINE":
            raise ActuationError(f"Swarm manager Agent {agent_id} 不在线")
        if "swarm_actuation" not in set(agent.capabilities or []):
            raise ActuationError(f"Agent {agent_id} 未启用 swarm_actuation")
        try:
            task = self.repo.create_task(
                CreateTaskRequest(
                    name=f"actuation:{operation}:{service_name}"[:120],
                    agent_id=agent_id,
                    target_pid=1,
                    collector_type="swarm_actuation",
                    sample_rate=1,
                    duration_sec=5,
                    options={
                        "source": "autonomous_actuation_gateway",
                        "operation": operation,
                        "service_name": service_name,
                        **({"expected_version_index": int(parameters["expected_version_index"])}
                           if parameters.get("expected_version_index") is not None else {}),
                    },
                ),
                idempotency_key=_task_idempotency_key(
                    operation,
                    service_name,
                    str(parameters.get("operation_key") or uuid.uuid4().hex),
                ),
            )
        except ValueError as exc:
            raise ActuationError(str(exc)) from exc
        deadline = time.time() + 90
        task_status = "PENDING"
        while time.time() < deadline:
            current = self.repo.tasks.get(task.id)
            if current is None:
                raise ActuationError("远程处置任务丢失")
            task_status = status_value(current.status)
            if task_status in {"DONE", "FAILED", "CANCELLED"}:
                break
            time.sleep(0.5)
        if task_status != "DONE":
            current = self.repo.tasks.get(task.id)
            reason = getattr(current, "status_reason", "") if current else ""
            raise ActuationError(f"远程处置未完成（{task_status}）: {reason}")
        value = self._read_result(task.id)
        if value is None:
            raise ActuationError("远程处置缺少结构化结果")
        value["task_id"] = task.id
        return value

    def _read_result(self, task_id: str) -> dict[str, Any] | None:
        for artifact in self.repo.artifacts.get(task_id, []):
            if artifact.get("artifact_type") != "actuation_result":
                continue
            metadata = artifact.get("metadata") or {}
            if isinstance(metadata.get("data"), dict):
                return metadata["data"]
            local_path = artifact.get("local_path")
            if local_path:
                path = Path(local_path)
                if path.is_file() and path.stat().st_size <= 1024 * 1024:
                    return json.loads(path.read_text(encoding="utf-8"))
            object_key = artifact.get("object_key")
            if object_key:
                raw = storage.read_object_bytes(artifact.get("bucket", "mini-drop"), object_key)
                if len(raw) <= 1024 * 1024:
                    return json.loads(raw.decode("utf-8"))
        return None

    def _audit(self, event: str, action_id: str, attempt_id: str, parameters: dict[str, Any]) -> None:
        if self.audit:
            self.audit({
                "event_type": event,
                "action_id": action_id,
                "attempt_id": attempt_id,
                "manager_agent_id": parameters.get("manager_agent_id"),
                "service_name": parameters.get("service_name"),
            })


def _task_idempotency_key(operation: str, service_name: str, operation_key: str) -> str:
    """Bounded, non-secret key shared by every retry of one logical action."""
    digest = hashlib.sha256(
        f"{operation}\0{service_name}\0{operation_key}".encode("utf-8")
    ).hexdigest()[:32]
    return f"actuation-{operation}-{digest}"
