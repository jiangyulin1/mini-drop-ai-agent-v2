"""受控修复动作执行（Actuation Gateway 首个可验证实例）。

当前只开放两个可回滚、低影响的 Mini-Drop 自身维护动作：

- ``mini-drop.cleanup-expired-cache``：把过期诊断产物移入隔离区（移动而非删除，可回滚）
- ``mini-drop.restore-cache-quarantine``：从隔离区恢复缓存

安全前置条件（全部满足才能执行）：
1. Action 已注册且 ``implementation_status == "executable"``；
2. ``evaluate_action`` 决策不是 DENIED（人工发起视为 USER_APPROVAL）；
3. 必须先执行 dry-run 并持有 dry_run attempt_id，执行时校验其存在；
4. 目标路径必须位于受控缓存根目录内（path_scope 校验）；
5. 移动而非删除 → 可回滚；隔离区已有同名目录时跳过 → 幂等；
6. 所有读取、dry-run、执行、回滚都写审计日志。

任何不满足条件的请求都被拒绝，不猜测、不扩大范围。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

TASK_DIR_PATTERN = re.compile(r"^task_[A-Za-z0-9_.-]{6,128}$")
SWARM_SERVICE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,127}$")


def cache_root() -> Path:
    return Path(os.getenv("MINI_DROP_ARTIFACT_ROOT", "/tmp/mini-drop")).expanduser().resolve()


def quarantine_root() -> Path:
    return Path(os.getenv("MINI_DROP_QUARANTINE_ROOT", "/tmp/mini-drop-quarantine")).expanduser().resolve()


class ActuationError(Exception):
    """确定性拒绝原因，message 直接对用户展示。"""


# ── 执行状态 ─────────────────────────────────────────────────


class ActuationStage(str):
    CREATED = "CREATED"
    DRY_RUN_COMPLETED = "DRY_RUN_COMPLETED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"


@dataclass
class ActuationAttempt:
    attempt_id: str
    action_id: str
    stage: str = ActuationStage.CREATED
    created_at: float = field(default_factory=time.time)
    dry_run_items: list[dict[str, Any]] = field(default_factory=list)
    executed_items: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ── 目录扫描与路径安全 ───────────────────────────────────────


def _safe_resolve(base: Path, path: Path) -> Path:
    """校验 path 位于 base 之内并返回规范化路径，否则抛出 ActuationError。"""
    try:
        resolved = path.expanduser().resolve()
    except OSError as exc:
        raise ActuationError(f"路径无法解析: {path}") from exc
    if resolved != base and base not in resolved.parents:
        raise ActuationError(f"路径超出受控目录: {resolved}")
    return resolved


def _list_task_dirs(root: Path) -> list[Path]:
    """列出缓存根目录下符合任务目录命名规则的子目录。"""
    if not root.is_dir():
        return []
    result: list[Path] = []
    try:
        for entry in os.listdir(root):
            if not TASK_DIR_PATTERN.match(entry):
                continue
            candidate = root / entry
            if candidate.is_dir():
                result.append(candidate)
    except OSError:
        return []
    return result


def _dir_age_days(path: Path, now: float | None = None) -> float:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return 0.0
    return max(0.0, (now or time.time()) - mtime) / 86400.0


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


# ── 动作执行器 ───────────────────────────────────────────────


def cleanup_expired_cache_dry_run(parameters: dict[str, Any]) -> dict[str, Any]:
    """列出超过保留期的过期诊断产物（只读，不移动任何文件）。"""
    retention_days = max(1, min(int(parameters.get("retention_days", 7)), 365))
    root = cache_root()
    now = time.time()
    items: list[dict[str, Any]] = []
    for task_dir in _list_task_dirs(root):
        age_days = _dir_age_days(task_dir, now)
        if age_days < retention_days:
            continue
        size_bytes = _dir_size(task_dir)
        items.append({
            "task_id": task_dir.name,
            "path": str(task_dir),
            "size_bytes": size_bytes,
            "age_days": round(age_days, 1),
        })
    items.sort(key=lambda item: item["age_days"], reverse=True)
    total_bytes = sum(item["size_bytes"] for item in items)
    return {
        "retention_days": retention_days,
        "candidate_count": len(items),
        "total_bytes": total_bytes,
        "items": items,
        "quarantine_root": str(quarantine_root()),
    }


def cleanup_expired_cache_execute(attempt: ActuationAttempt) -> list[dict[str, Any]]:
    """把 dry-run 中列出的过期产物移入隔离区（移动而非删除）。"""
    root = cache_root()
    quarantine = quarantine_root()
    quarantine.mkdir(parents=True, exist_ok=True)
    executed: list[dict[str, Any]] = []
    for item in attempt.dry_run_items:
        source = _safe_resolve(root, Path(item["path"]))
        if not source.is_dir():
            continue  # 已被其他流程处理，幂等跳过
        target = _safe_resolve(quarantine, quarantine / source.name)
        if target.exists():
            target = _safe_resolve(quarantine, quarantine / f"{source.name}-{time.strftime('%Y%m%d%H%M%S')}")
        try:
            shutil.move(str(source), str(target))
        except OSError as exc:
            raise ActuationError(f"移动失败 {source.name}: {exc}") from exc
        executed.append({
            "task_id": source.name,
            "source": str(source),
            "quarantine_path": str(target),
            "size_bytes": item.get("size_bytes", 0),
        })
    return executed


def restore_cache_quarantine_dry_run(parameters: dict[str, Any]) -> dict[str, Any]:
    """列出隔离区中可恢复的缓存目录（只读）。"""
    quarantine = quarantine_root()
    items: list[dict[str, Any]] = []
    for entry in sorted(os.listdir(quarantine)) if quarantine.is_dir() else []:
        candidate = quarantine / entry
        if not candidate.is_dir():
            continue
        items.append({
            "task_id": entry,
            "path": str(candidate),
            "size_bytes": _dir_size(candidate),
            "age_days": round(_dir_age_days(candidate), 1),
        })
    return {"candidate_count": len(items), "items": items}


def restore_cache_quarantine_execute(attempt: ActuationAttempt) -> list[dict[str, Any]]:
    """把隔离区中的缓存目录移回缓存根目录（回滚）。"""
    root = cache_root()
    quarantine = quarantine_root()
    root.mkdir(parents=True, exist_ok=True)
    executed: list[dict[str, Any]] = []
    for item in attempt.dry_run_items:
        source = _safe_resolve(quarantine, Path(item["path"]))
        if not source.is_dir():
            continue
        target = _safe_resolve(root, root / source.name)
        if target.exists():
            target = _safe_resolve(root, root / f"{source.name}-{time.strftime('%Y%m%d%H%M%S')}")
        try:
            shutil.move(str(source), str(target))
        except OSError as exc:
            raise ActuationError(f"恢复失败 {source.name}: {exc}") from exc
        executed.append({
            "task_id": source.name,
            "source": str(source),
            "restored_path": str(target),
        })
    return executed


# ── Docker Swarm bounded recovery ─────────────────────────────


def _allowed_swarm_services() -> set[str]:
    return {
        item.strip()
        for item in os.getenv("MINI_DROP_AUTONOMY_SWARM_SERVICES", "").split(",")
        if item.strip()
    }


def _validate_swarm_service(value: Any) -> str:
    service = str(value or "")
    if not SWARM_SERVICE_PATTERN.fullmatch(service):
        raise ActuationError("service_name 非法")
    if service not in _allowed_swarm_services():
        raise ActuationError(f"服务 {service} 不在自主处置允许列表")
    return service


def _docker_json(args: list[str]) -> Any:
    docker = shutil.which("docker")
    if not docker:
        raise ActuationError("docker 命令不可用")
    try:
        result = subprocess.run(
            [docker, *args], capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ActuationError(f"Docker 只读检查失败: {exc}") from exc
    if result.returncode != 0:
        raise ActuationError(f"Docker 只读检查失败: {result.stderr.strip()[:300]}")
    try:
        import json
        return json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        raise ActuationError("Docker 返回了无效 JSON") from exc


def _docker_run(args: list[str], *, timeout: int = 90) -> str:
    docker = shutil.which("docker")
    if not docker:
        raise ActuationError("docker 命令不可用")
    try:
        result = subprocess.run(
            [docker, *args], capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ActuationError(f"Docker 动作失败: {exc}") from exc
    if result.returncode != 0:
        raise ActuationError(f"Docker 动作失败: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


def _inspect_swarm_service(service: str) -> dict[str, Any]:
    value = _docker_json(["service", "inspect", service])
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise ActuationError(f"无法唯一定位 Swarm 服务 {service}")
    return value[0]


def _swarm_preflight_item(service: str) -> dict[str, Any]:
    value = _inspect_swarm_service(service)
    spec = value.get("Spec") or {}
    labels = spec.get("Labels") or {}
    if str(labels.get("mini-drop.autonomy", "")).lower() != "true":
        raise ActuationError("服务缺少 mini-drop.autonomy=true 标签")
    if str(labels.get("mini-drop.stateless", "")).lower() != "true":
        raise ActuationError("服务缺少 mini-drop.stateless=true 标签，拒绝自动重启")
    replicas = int((((spec.get("Mode") or {}).get("Replicated") or {}).get("Replicas", 0)) or 0)
    if replicas < 1:
        raise ActuationError("服务没有期望副本，拒绝自动重启")
    return {
        "service_name": service,
        "service_id": value.get("ID"),
        "version_index": int((value.get("Version") or {}).get("Index", 0) or 0),
        "replicas": replicas,
        "labels": {
            "mini-drop.autonomy": labels.get("mini-drop.autonomy"),
            "mini-drop.stateless": labels.get("mini-drop.stateless"),
        },
    }


def swarm_restart_dry_run(parameters: dict[str, Any]) -> dict[str, Any]:
    service = _validate_swarm_service(parameters.get("service_name"))
    item = _swarm_preflight_item(service)
    return {"candidate_count": 1, "items": [item], "update_order": "start-first"}


def swarm_restart_execute(attempt: ActuationAttempt) -> list[dict[str, Any]]:
    executed: list[dict[str, Any]] = []
    for item in attempt.dry_run_items:
        service = _validate_swarm_service(item.get("service_name"))
        current = _swarm_preflight_item(service)
        if current["version_index"] != int(item.get("version_index", -1)):
            raise ActuationError("服务在 dry-run 后已被修改，拒绝执行（并发变更）")
        output = _docker_run([
            "service", "update", "--force", "--update-order", "start-first",
            "--label-add", f"mini-drop.last-actuation={attempt.attempt_id}", service,
        ])
        executed.append({
            "service_name": service,
            "previous_version_index": current["version_index"],
            "replicas": current["replicas"],
            "output": output[:500],
            "rollback_action_id": "swarm.rollback-service",
        })
    return executed


def swarm_rollback_dry_run(parameters: dict[str, Any]) -> dict[str, Any]:
    service = _validate_swarm_service(parameters.get("service_name"))
    item = _swarm_preflight_item(service)
    return {"candidate_count": 1, "items": [item]}


def swarm_rollback_execute(attempt: ActuationAttempt) -> list[dict[str, Any]]:
    executed: list[dict[str, Any]] = []
    for item in attempt.dry_run_items:
        service = _validate_swarm_service(item.get("service_name"))
        output = _docker_run(["service", "rollback", "--detach=false", service], timeout=120)
        executed.append({"service_name": service, "output": output[:500]})
    return executed


# ── 执行器注册表 ─────────────────────────────────────────────

Executor = Callable[[ActuationAttempt], list[dict[str, Any]]]

EXECUTORS: dict[str, dict[str, Any]] = {
    "mini-drop.cleanup-expired-cache": {
        "dry_run": cleanup_expired_cache_dry_run,
        "execute": cleanup_expired_cache_execute,
        "rollback_action_id": "mini-drop.restore-cache-quarantine",
    },
    "mini-drop.restore-cache-quarantine": {
        "dry_run": restore_cache_quarantine_dry_run,
        "execute": restore_cache_quarantine_execute,
    },
    "swarm.restart-stateless-service": {
        "dry_run": swarm_restart_dry_run,
        "execute": swarm_restart_execute,
        "rollback_action_id": "swarm.rollback-service",
    },
    "swarm.rollback-service": {
        "dry_run": swarm_rollback_dry_run,
        "execute": swarm_rollback_execute,
    },
}


def is_executable(action_id: str) -> bool:
    return action_id in EXECUTORS


# ── 执行编排 ─────────────────────────────────────────────────


class ActuationGateway:
    """管理 dry-run → execute → rollback 的单机执行边界。

    attempts 保存在内存中，仅用于本次会话内的二次校验（dry-run 必须先于
    执行）；所有审计信息写入调用方提供的 audit 回调。
    """

    def __init__(self, audit_callback: Optional[Callable[[dict[str, Any]], None]] = None):
        self._attempts: dict[str, ActuationAttempt] = {}
        self._audit = audit_callback

    def _audit_log(self, event: str, detail: dict[str, Any]) -> None:
        if self._audit is None:
            return
        self._audit({"event_type": event, **detail})

    def dry_run(self, action_id: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if not is_executable(action_id):
            raise ActuationError(f"动作 {action_id} 未开放执行（policy_only）")
        definition = EXECUTORS[action_id]
        result = definition["dry_run"](parameters or {})
        attempt = ActuationAttempt(
            attempt_id=f"act_{uuid.uuid4().hex[:12]}",
            action_id=action_id,
            stage=ActuationStage.DRY_RUN_COMPLETED,
            dry_run_items=result.get("items", []),
            metadata={"parameters": parameters or {}},
        )
        self._attempts[attempt.attempt_id] = attempt
        self._audit_log("ACTION_DRY_RUN", {
            "action_id": action_id,
            "attempt_id": attempt.attempt_id,
            "candidate_count": result.get("candidate_count", 0),
            "total_bytes": result.get("total_bytes", 0),
        })
        return {
            "attempt_id": attempt.attempt_id,
            "action_id": action_id,
            "stage": attempt.stage,
            "dry_run": result,
        }

    def execute(self, action_id: str, dry_run_attempt_id: str, environment: str = "production") -> dict[str, Any]:
        if not is_executable(action_id):
            raise ActuationError(f"动作 {action_id} 未开放执行（policy_only）")
        attempt = self._attempts.get(dry_run_attempt_id)
        if attempt is None:
            raise ActuationError("dry-run 不存在：必须先执行 dry-run 才能执行")
        if attempt.action_id != action_id:
            raise ActuationError("dry-run 与执行动作不匹配")
        if attempt.stage == ActuationStage.COMPLETED:
            # 幂等重放：同一 attempt 已完成则直接返回已执行结果，不重复执行
            return {
                "attempt_id": attempt.attempt_id,
                "action_id": action_id,
                "stage": attempt.stage,
                "executed": attempt.executed_items,
                "idempotent_replay": True,
            }
        if attempt.stage != ActuationStage.DRY_RUN_COMPLETED:
            raise ActuationError(f"dry-run 状态无效: {attempt.stage}")
        if not attempt.dry_run_items:
            raise ActuationError("dry-run 未发现可执行项，无需执行")
        definition = EXECUTORS[action_id]
        attempt.stage = ActuationStage.EXECUTING
        try:
            executed = definition["execute"](attempt)
        except ActuationError as exc:
            attempt.stage = ActuationStage.FAILED
            attempt.error = str(exc)
            self._audit_log("ACTION_EXECUTE_FAILED", {
                "action_id": action_id,
                "attempt_id": attempt.attempt_id,
                "error": str(exc),
            })
            raise
        attempt.executed_items = executed
        attempt.stage = ActuationStage.COMPLETED
        self._audit_log("ACTION_EXECUTED", {
            "action_id": action_id,
            "attempt_id": attempt.attempt_id,
            "executed_count": len(executed),
            "environment": environment,
        })
        return {
            "attempt_id": attempt.attempt_id,
            "action_id": action_id,
            "stage": attempt.stage,
            "executed": executed,
        }

    def get_attempt(self, attempt_id: str) -> Optional[ActuationAttempt]:
        return self._attempts.get(attempt_id)
