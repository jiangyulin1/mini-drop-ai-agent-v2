"""E4: PlanDriver —— Pi 规划，Mini-Drop 执行低风险调查。

计划 8.10/12/13：Supervisor 自动调度 READ_LOW 步骤，Task 完成自动唤醒调查
（不依赖模型轮询）。DeterministicRuntime 随时可回退；集群 Step 按 Membership
Snapshot + 选择策略走 Fanout，模型无权枚举或扩大节点。

本驱动：
- dispatch_case_ready_steps：把当前 Plan Revision 中 QUEUED/WAITING_APPROVAL 的
  READ_LOW 步骤展开为单目标 Task（普通 Step）或 FanoutCollectionRun（集群 Step）；
- on_task_done：Task 完成唤醒 —— 标记 Step COMPLETED / 更新 Fanout Run，然后链式
  调度下一批就绪步骤（连续补证）；
- 重复采集去重：当前 Plan 中已有同 collector + 同目标 COMPLETED Step 时，新 Step
  标记 SKIPPED_REUSED，不重复下发；
- 中断：用户取消/转向经 Case Command 队列与 Step 状态机生效，驱动遵守终态。
"""

from __future__ import annotations

from typing import Any, Optional

from mini_drop_contracts import get_collector_spec
from server.app.agent_runtime.config import agent_cluster_fanout_enabled
from server.app.agent_runtime.policy import RuntimePolicy, resolve_runtime_policy
from server.app.diagnosis.cluster_scope import EnvironmentProfile, MembershipSnapshot, TargetResolver
from server.app.diagnosis.fanout import FanoutCollectionRun, FanoutCollectionService
from server.app.diagnosis.investigation_plan import SCHEDULABLE

DONE_STATUSES = {"DONE"}
TERMINAL_CASE_STATES = {"PAUSED", "STOPPED", "RESOLVED", "INSUFFICIENT_EVIDENCE"}


def is_cluster_step(step: dict[str, Any]) -> bool:
    """集群 Step：声明了选择策略，或 target_refs 含集群/工作负载级锚点。"""
    if step.get("selection_strategy"):
        return True
    anchors = [ref for ref in (step.get("target_refs") or []) if ":" in ref and ref.split(":", 1)[0] in {
        "cluster", "workload", "service", "host", "environment",
    }]
    return bool(anchors)


class PlanDriver:
    """低风险调查的确定性调度器；一个 Case/Step 只有一个有效推进者。"""

    def __init__(
        self,
        repository: Any,
        investigation_plan_service: Any,
        fanout_service: FanoutCollectionService,
        target_resolver: TargetResolver,
        collection_supervisor: Any,
    ):
        self._repo = repository
        self._plan_service = investigation_plan_service
        self._fanout = fanout_service
        self._resolver = target_resolver
        self._collection_supervisor = collection_supervisor

    # ── 主入口 ─────────────────────────────────────────────────────────

    def dispatch_case_ready_steps(
        self, case_id: str, tenant_id: str, *,
        principal_id: str = "mini-drop-plan-driver",
        runtime_policy: RuntimePolicy | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """调度当前 Plan 中所有就绪的 READ_LOW 步骤（幂等，可在每 tick 调用）。"""
        case = self._repo.get_incident_case(case_id, tenant_id)
        if case is None:
            return {"outcome": "CASE_NOT_FOUND"}
        if case.get("state") in TERMINAL_CASE_STATES:
            return {"outcome": "TERMINAL", "state": case.get("state")}
        plan = self._plan_service.read_plan(case_id, tenant_id)
        if plan is None:
            return {"outcome": "NO_PLAN"}
        policy = resolve_runtime_policy(runtime_policy) if runtime_policy is not None else None
        if policy is not None and policy.execution_mode != "normal":
            return {
                "outcome": "POLICY_BLOCKED",
                "execution_mode": policy.execution_mode,
                "reason": "NATIVE_PLAN_DISPATCH_REQUIRES_NORMAL_EXECUTION_MODE",
            }
        plan_revision = int(plan.get("plan_revision") or 0)
        scope_revision = int(case.get("scope_revision") or 1)

        dispatched: list[dict[str, Any]] = []
        reused: list[str] = []
        for step in plan.get("steps") or []:
            if step.get("status") not in SCHEDULABLE:
                continue
            if policy is not None and "R1" not in policy.allowed_risk_levels:
                continue
            if step.get("risk") != "READ_LOW":
                continue  # E4 只自动调度低风险；其余等待审批
            step_id = str(step.get("step_id") or "")
            if self._dedup_reusable(case_id, tenant_id, plan, step):
                try:
                    self._plan_service.transition_step(
                        case_id, tenant_id, step_id, "SKIPPED_REUSED", actor_id=principal_id,
                    )
                except ValueError:
                    pass
                reused.append(step_id)
                continue
            outcome = self._dispatch_step(
                case_id, tenant_id, step, plan_revision, scope_revision, principal_id,
            )
            dispatched.append(outcome)
        return {
            "outcome": "DISPATCHED",
            "plan_revision": plan_revision,
            "scope_revision": scope_revision,
            "dispatched": dispatched,
            "reused": reused,
        }

    # ── 单步调度 ───────────────────────────────────────────────────────

    def _dispatch_step(
        self,
        case_id: str,
        tenant_id: str,
        step: dict[str, Any],
        plan_revision: int,
        scope_revision: int,
        principal_id: str,
    ) -> dict[str, Any]:
        step_id = str(step.get("step_id") or "")
        try:
            schedulable = self._plan_service.verify_schedulable(
                case_id, tenant_id, step_id,
                plan_revision=plan_revision, scope_revision=scope_revision,
            )
        except ValueError as exc:
            return {"step_id": step_id, "status": "REJECTED", "reason": str(exc)}
        if is_cluster_step(schedulable):
            if not agent_cluster_fanout_enabled():
                return {
                    "step_id": step_id,
                    "status": "CLUSTER_FANOUT_DISABLED",
                    "reason": "MINI_DROP_AGENT_CLUSTER_FANOUT_ENABLED=0",
                    "kind": "cluster",
                }
            return self._dispatch_cluster_step(
                case_id, tenant_id, schedulable, plan_revision, scope_revision, principal_id,
            )
        return self._dispatch_single_step(
            case_id, tenant_id, schedulable, plan_revision, scope_revision,
        )

    def _dispatch_single_step(
        self,
        case_id: str,
        tenant_id: str,
        step: dict[str, Any],
        plan_revision: int,
        scope_revision: int,
    ) -> dict[str, Any]:
        step_id = str(step.get("step_id") or "")
        collector_id = str(step.get("collector_id") or "sys_metrics")
        existing = self._repo.get_task_by_diagnosis_step_id(step_id)
        if existing is not None:
            self._mark_running(case_id, tenant_id, step_id)
            return {"step_id": step_id, "status": "RUNNING", "task_id": existing.id,
                    "kind": "single", "reused_task": True}
        # 目标解析：从 Step 的 target_refs 取一个 agent/instance 目标。
        target = self._resolve_single_target(case_id, tenant_id, step)
        if target is None:
            return {"step_id": step_id, "status": "NO_TARGET", "reason": "TARGET_UNAVAILABLE"}
        spec = get_collector_spec(collector_id)
        if spec is None:
            self._block_step(case_id, tenant_id, step_id)
            return {"step_id": step_id, "status": "REJECTED", "reason": "COLLECTOR_NOT_REGISTERED"}
        requested_goal = str(step.get("expected_information") or "").strip()
        information_goal = requested_goal if requested_goal in spec.information_goals else spec.information_goals[0]
        case = self._repo.get_incident_case(case_id, tenant_id) or {}
        binding = self._repo.get_agent_runtime_binding(case_id, tenant_id) or {}
        result = self._collection_supervisor.propose_and_dispatch(
            case_id=case_id, tenant_id=tenant_id, collector_id=collector_id,
            target_selector={"agent_id": target["agent_id"], "target_pid": target["pid"]},
            parameters={"target_pid": target["pid"]}, information_goal=information_goal,
            reason_summary=str(step.get("purpose") or requested_goal or information_goal),
            runtime_generation=int(binding.get("runtime_generation") or 1),
            expected_control_revision=int(case.get("control_revision") or 1),
            expected_scope_revision=scope_revision,
            idempotency_key=f"plan-step:{step_id}", allowed_risk_levels={"R0", "R1"},
            plan_step_id=step_id, plan_revision=plan_revision,
        )
        task = result.get("task")
        proposal = result.get("proposal") or {}
        request = result.get("collection_request") or {}
        if task is None:
            self._block_step(case_id, tenant_id, step_id)
            errors = (proposal.get("validation_result") or {}).get("errors") or []
            return {
                "step_id": step_id, "status": "REJECTED",
                "reason": ",".join(errors) or "COLLECTION_NOT_DISPATCHED",
                "proposal_id": proposal.get("proposal_id"), "kind": "single",
            }
        self._mark_running(case_id, tenant_id, step_id)
        return {
            "step_id": step_id, "status": "RUNNING", "task_id": task.id, "kind": "single",
            "proposal_id": proposal.get("proposal_id"),
            "collection_request_id": request.get("collection_request_id"),
        }


    def _mark_running(self, case_id: str, tenant_id: str, step_id: str) -> None:
        """QUEUED → DISPATCHING → RUNNING：任务已创建且在途。"""
        for to_status in ("DISPATCHING", "RUNNING"):
            try:
                self._plan_service.transition_step(
                    case_id, tenant_id, step_id, to_status, actor_id="mini-drop-plan-driver",
                )
            except ValueError:
                # DISPATCHING → RUNNING 只在 QUEUED→DISPATCHING 已发生时执行
                continue

    def _block_step(self, case_id: str, tenant_id: str, step_id: str) -> None:
        try:
            self._plan_service.transition_step(
                case_id, tenant_id, step_id, "BLOCKED", actor_id="mini-drop-plan-driver",
            )
        except ValueError:
            pass

    def _resolve_single_target(self, case_id: str, tenant_id: str,
                               step: dict[str, Any]) -> Optional[dict[str, Any]]:
        """从 Step target_refs 解析单目标；缺省时用 Case 首个在线实例。"""
        case = self._repo.get_incident_case(case_id, tenant_id) or {}
        instances = (case.get("target_scope") or {}).get("instances") or []
        for ref in step.get("target_refs") or []:
            if ":" in ref and ref.split(":", 1)[0] not in {
                "cluster", "workload", "service", "host", "environment",
            }:
                _type, _id = ref.split(":", 1)
                for item in instances:
                    if str(item.get("instance_id") or "") == _id or str(item.get("agent_id") or "") == _id:
                        return self._instance_target(item)
        for item in instances:
            return self._instance_target(item)
        # Process-level collection must never guess PID 1 on an arbitrary
        # online Agent. Establish an explicit Case target or discovery
        # authority first; otherwise the step remains NO_TARGET.
        return None

    def _instance_target(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "agent_id": str(item.get("agent_id") or ""),
            "pid": int(item.get("pid") or 1),
        }

    def _dispatch_cluster_step(
        self,
        case_id: str,
        tenant_id: str,
        step: dict[str, Any],
        plan_revision: int,
        scope_revision: int,
        principal_id: str,
    ) -> dict[str, Any]:
        step_id = str(step.get("step_id") or "")
        case = self._repo.get_incident_case(case_id, tenant_id) or {}
        target_scope = case.get("target_scope") or {}
        cluster_id = str(target_scope.get("cluster_id") or "")
        profile = EnvironmentProfile(
            environment_id=str(target_scope.get("environment_id") or "env-default"),
            cluster=cluster_id or "cluster-default",
        )
        snapshot = self._fanout.build_membership_snapshot(
            environment_id=profile.environment_id,
            cluster_id=cluster_id,
            scope_revision=scope_revision,
        )
        resolution = self._resolver.resolve_collection_targets(
            snapshot,
            str(step.get("selection_strategy") or "REPRESENTATIVE"),
            profile=profile,
            target_refs=step.get("target_refs") or None,
        )
        if not resolution.targets:
            return {"step_id": step_id, "status": "NO_TARGET", "reason": "NO_ELIGIBLE_MEMBERS"}
        self._repo.create_membership_snapshot(case_id, tenant_id, snapshot.model_dump(mode="json"))
        step_ctx = dict(step)
        step_ctx["plan_revision"] = plan_revision
        step_ctx["scope_revision"] = scope_revision
        run = self._fanout.create_fanout_run(
            case_id=case_id, tenant_id=tenant_id, step=step_ctx,
            profile=profile, environment_id=profile.environment_id,
            cluster_id=cluster_id, snapshot=snapshot, resolution=resolution,
        )
        self._mark_running(case_id, tenant_id, step_id)
        return {"step_id": step_id, "status": "RUNNING", "run_id": run["run_id"],
                "kind": "cluster", "targets": len(resolution.targets)}

    # ── 去重 ───────────────────────────────────────────────────────────

    def _dedup_reusable(
        self, case_id: str, tenant_id: str, plan: dict[str, Any], step: dict[str, Any],
    ) -> bool:
        """先查重再补采：已有同 collector 证据则标记 SKIPPED_REUSED。

        两条复用依据（DoD #3）：
        1. 当前 Plan 中已有同 collector + 目标重叠的 COMPLETED Step；
        2. 该 Case 已有 DONE 的同类采集任务（跨 Plan 修订 / 数据驱动入口）。
        """
        collector_id = step.get("collector_id")
        if not collector_id:
            return False
        target_set = set(step.get("target_refs") or [])
        for other in plan.get("steps") or []:
            if other.get("step_id") == step.get("step_id"):
                continue
            if other.get("status") != "COMPLETED":
                continue
            if other.get("collector_id") != collector_id:
                continue
            other_targets = set(other.get("target_refs") or [])
            if target_set and other_targets and target_set & other_targets:
                return True
            if not target_set and not other_targets:
                return True
        if self._has_reusable_task(case_id, tenant_id, collector_id, step=step):
            return True
        return False

    def _has_reusable_task(
        self, case_id: str, tenant_id: str, collector_id: str,
        *, step: dict[str, Any] | None = None,
    ) -> bool:
        """Case 内是否存在已完成且证据未被排除的同类采集任务。

        覆盖两种来源：
        1. 本驱动/扇出下发的 Task（options.case_id == case_id）；
        2. 数据驱动入口的 initial_tasks（已纳入 Case 初始证据）。
        """
        case = self._repo.get_incident_case(case_id, tenant_id) or {}
        initial_ids = set(case.get("initial_task_ids") or [])
        tasks = getattr(self._repo, "tasks", {})
        requested_targets = set((step or {}).get("target_refs") or [])
        requested_goal = str((step or {}).get("expected_information") or "").strip()
        for task in (tasks.values() if isinstance(tasks, dict) else tasks):
            status = str(getattr(task, "status", "") or "")
            if status != "DONE":
                continue
            if getattr(task, "collector_type", "") != collector_id:
                continue
            task_id = str(getattr(task, "id", "") or "")
            if task_id in initial_ids:
                return True
            options = (getattr(task, "request_params", None) or {}).get("options") or {}
            if str(options.get("case_id") or "") != case_id:
                continue
            task_target = str(options.get("target_ref") or "")
            if requested_targets and task_target and not requested_targets.intersection({task_target}):
                continue
            if requested_goal and str(options.get("information_goal") or "") not in {"", requested_goal}:
                continue
            return True
        return False

    # ── Task 完成唤醒 ──────────────────────────────────────────────────

    def on_task_done(
        self, case_id: str, tenant_id: str, task_id: str, *,
        status: str = "DONE", principal_id: str = "mini-drop-plan-driver",
    ) -> dict[str, Any]:
        """Task 完成唤醒：标记 Step / 更新 Fanout Run，然后链式调度下一批。"""
        task = self._repo.tasks.get(task_id)
        if task is None:
            return {"outcome": "TASK_NOT_FOUND"}
        options = (task.request_params or {}).get("options") or {}
        step_id = str(options.get("plan_step_id") or options.get("diagnosis_step_id") or "")
        if not step_id:
            return {"outcome": "NOT_PLAN_STEP", "task_id": task_id}
        # 集群 Fanout Task：更新 Run，不直接完成 Step（Step 由 Run 聚合结果决定）
        run = self._find_fanout_run_for_task(case_id, tenant_id, task_id)
        if run is not None:
            run_obj = FanoutCollectionRun(**run)
            updated = self._fanout.update_task_outcome(
                run_obj, task_id, status,
                scope_revision=int(run.get("scope_revision") or 1),
            )
            # v6 6.3: aggregation is automatic as soon as every ExecutionUnit/Task
            # has a terminal result.  There is no manual /aggregate dependency.
            run_after = FanoutCollectionRun(**updated)
            task_ids = [str(item) for item in (run_after.task_ids or [])]
            terminal = (
                len(task_ids) > 0
                and all(
                    (run_after.task_statuses or {}).get(tid) in {"DONE", "FAILED", "CANCELLED"}
                    for tid in task_ids
                )
            )
            if terminal:
                snapshot = self._repo.get_membership_snapshot(
                    case_id, tenant_id, run_after.snapshot_id,
                ) if hasattr(self._repo, "get_membership_snapshot") else None
                if snapshot:
                    try:
                        membership = MembershipSnapshot(
                            snapshot_id=str(snapshot.get("snapshot_id") or ""),
                            captured_at=snapshot.get("captured_at") or snapshot.get("created_at"),
                            environment_id=str(snapshot.get("environment_id") or ""),
                            cluster_id=str(snapshot.get("cluster_id") or ""),
                            topology_version=str(snapshot.get("topology_version") or ""),
                            scope_revision=int(snapshot.get("scope_revision") or 1),
                            members=snapshot.get("members") or [],
                        )
                    except Exception:
                        membership = None
                    if membership is not None:
                        self._fanout.aggregate(
                            run_after,
                            membership,
                            time_aligned=True,
                        )
                    refreshed = self._repo.get_fanout_run(
                        case_id, tenant_id, run_after.run_id,
                    )
                    if refreshed:
                        updated = refreshed
            return self._after_fanout_task(case_id, tenant_id, updated, step_id, principal_id)
        target_status = {"DONE": "COMPLETED", "FAILED": "FAILED", "CANCELLED": "CANCELLED"}.get(status)
        if target_status:
            try:
                self._plan_service.transition_step(
                    case_id, tenant_id, step_id, target_status, actor_id=principal_id,
                )
            except ValueError as exc:
                return {"outcome": "STEP_TRANSITION_REJECTED", "reason": str(exc)}
        return self.dispatch_case_ready_steps(case_id, tenant_id)

    def _find_fanout_run_for_task(self, case_id: str, tenant_id: str,
                                  task_id: str) -> Optional[dict[str, Any]]:
        for run in self._repo.list_fanout_runs(case_id, tenant_id):
            if task_id in run.get("task_ids") or []:
                return run
        return None

    def _after_fanout_task(self, case_id: str, tenant_id: str, updated: dict[str, Any],
                           step_id: str, principal_id: str) -> dict[str, Any]:
        """Fanout Run 聚合完成时把 Step 置 COMPLETED，否则继续等待。"""
        status = updated.get("status")
        if status in {"COMPLETED", "PARTIAL"}:
            try:
                self._plan_service.transition_step(
                    case_id, tenant_id, step_id, "COMPLETED", actor_id=principal_id,
                )
            except ValueError:
                pass
        return self.dispatch_case_ready_steps(case_id, tenant_id)
