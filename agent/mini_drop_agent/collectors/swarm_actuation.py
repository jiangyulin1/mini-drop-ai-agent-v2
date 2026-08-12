"""Strict Docker Swarm actuation executor for an explicitly enabled manager Agent."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Any

from agent.mini_drop_agent.collectors.base import CollectorResult, CollectorTask


SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,127}$")


class SwarmActuationCollector:
    OUTPUT_BASE = "/tmp/mini-drop"
    OPERATIONS = {"preflight_restart", "restart", "preflight_rollback", "rollback"}

    def collect(self, task: CollectorTask) -> CollectorResult:
        if os.getenv("MINI_DROP_AGENT_ACTUATION_ENABLED", "0").lower() not in {"1", "true", "yes", "on"}:
            return CollectorResult(ok=False, reason="该 Agent 未启用受控处置")
        operation = str(task.options.get("operation") or "")
        service = str(task.options.get("service_name") or "")
        if operation not in self.OPERATIONS:
            return CollectorResult(ok=False, reason="未注册的 Swarm 操作")
        if not SERVICE_RE.fullmatch(service):
            return CollectorResult(ok=False, reason="service_name 非法")
        allowed = {item.strip() for item in os.getenv("MINI_DROP_AGENT_SWARM_SERVICES", "").split(",") if item.strip()}
        if service not in allowed:
            return CollectorResult(ok=False, reason=f"服务 {service} 不在 Agent 处置允许列表")
        try:
            before = self._inspect(service)
            self._validate_labels(before)
            expected = task.options.get("expected_version_index")
            if operation in {"restart", "rollback"} and expected is not None:
                current_version = int((before.get("Version") or {}).get("Index", 0) or 0)
                if current_version != int(expected):
                    return CollectorResult(ok=False, reason="服务在 dry-run 后已变化，拒绝并发执行")
            command_output = ""
            if operation == "restart":
                command_output = self._run([
                    "service", "update", "--force", "--update-order", "start-first",
                    "--label-add", f"mini-drop.last-actuation={task.id}", service,
                ], timeout=120)
            elif operation == "rollback":
                command_output = self._run(["service", "rollback", "--detach=false", service], timeout=120)
            after = self._inspect(service)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            return CollectorResult(ok=False, reason=f"Swarm 处置失败: {str(exc)[:300]}")

        item = self._summary(service, before)
        result = {
            "schema_version": "swarm_actuation.v1",
            "task_id": task.id,
            "operation": operation,
            "service_name": service,
            "preflight": item,
            "before_version_index": int((before.get("Version") or {}).get("Index", 0) or 0),
            "after_version_index": int((after.get("Version") or {}).get("Index", 0) or 0),
            "command_output": command_output[:1000],
        }
        output_dir = os.path.join(self.OUTPUT_BASE, task.id)
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "swarm_actuation.json")
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
        return CollectorResult(
            ok=True,
            reason=f"Swarm {operation} 完成: {service}",
            artifacts=[{
                "artifact_type": "actuation_result",
                "filename": "swarm_actuation.json",
                "local_path": output_path,
                "content_type": "application/json",
                "size_bytes": os.path.getsize(output_path),
                "metadata": {"operation": operation, "service_name": service},
            }],
        )

    @classmethod
    def _inspect(cls, service: str) -> dict[str, Any]:
        value = json.loads(cls._run(["service", "inspect", service], timeout=20))
        if not isinstance(value, list) or len(value) != 1:
            raise ValueError("无法唯一定位 Swarm 服务")
        return value[0]

    @staticmethod
    def _validate_labels(value: dict[str, Any]) -> None:
        labels = ((value.get("Spec") or {}).get("Labels") or {})
        if str(labels.get("mini-drop.autonomy", "")).lower() != "true":
            raise ValueError("服务缺少 mini-drop.autonomy=true 标签")
        if str(labels.get("mini-drop.stateless", "")).lower() != "true":
            raise ValueError("服务缺少 mini-drop.stateless=true 标签")

    @staticmethod
    def _summary(service: str, value: dict[str, Any]) -> dict[str, Any]:
        spec = value.get("Spec") or {}
        return {
            "service_name": service,
            "service_id": value.get("ID"),
            "version_index": int((value.get("Version") or {}).get("Index", 0) or 0),
            "replicas": int((((spec.get("Mode") or {}).get("Replicated") or {}).get("Replicas", 0)) or 0),
        }

    @staticmethod
    def _run(args: list[str], *, timeout: int) -> str:
        docker = shutil.which("docker")
        if not docker:
            raise OSError("docker 命令不可用")
        result = subprocess.run([docker, *args], capture_output=True, text=True, timeout=timeout, check=False)
        if result.returncode != 0:
            raise OSError(result.stderr.strip()[:300])
        return result.stdout.strip()
