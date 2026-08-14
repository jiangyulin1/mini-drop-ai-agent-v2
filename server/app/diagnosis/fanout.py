"""E3.5: FanoutCollectionRun —— 逻辑 Step 到单目标 Task 的确定性展开。

计划 3.5/8.13：Mini-Drop 继续以单目标 Task 作为数据面最小执行单元。集群能力
位于 Task 之上：PlanStep（逻辑范围，不携带任意主机名）→ Target Resolver +
Membership Snapshot → FanoutCollectionRun → 多个原有单目标 Task → Coverage-aware
Evidence Aggregate。

本模块负责：
- 从 repo 注册的 Agent 构建 MembershipSnapshot（能力版本 / 在线状态 / 排除原因）；
- 冻结成员后为每个目标展开一个单目标 Task（幂等：按 diagnosis_step_id 复用）；
- 故障域并发、全局预算、取消传播、部分失败与迟到结果隔离；
- coverage-aware 聚合（覆盖率不足 / 时间不对齐 → insufficient_coverage）。
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from pydantic import Field

from server.app.diagnosis.cluster_scope import (
    CoverageReport,
    EnvironmentProfile,
    MemberEntry,
    MembershipSnapshot,
    TargetResolution,
    classify_coverage,
)
from server.app.diagnosis.schemas import StrictModel

FANOUT_STATUS = (
    "RUNNING", "PARTIAL", "COMPLETED", "CANCELLED", "FAILED",
)

TASK_STATUS_DONE = "DONE"
TASK_STATUS_CANCELLED = "CANCELLED"
TASK_STATUS_FAILED = "FAILED"


class FanoutCollectionRun(StrictModel):
    run_id: str
    case_id: str = ""
    tenant_id: str = ""
    plan_step_id: str = ""
    plan_revision: int = 0
    scope_revision: int = 1
    snapshot_id: str = ""
    strategy: str = "ALL_IN_SCOPE"
    collector_id: str = "sys_metrics"
    target_members: list[dict[str, Any]] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)
    # member agent_id → task_id
    member_task_map: dict[str, str] = Field(default_factory=dict)
    task_statuses: dict[str, str] = Field(default_factory=dict)
    status: str = "RUNNING"
    coverage: float = 0.0
    failed_count: int = 0
    quorum_met: bool = False
    aggregate: dict[str, Any] = Field(default_factory=dict)
    late_result_isolated: list[str] = Field(default_factory=list)
    # repo 返回的 created_at/updated_at 可能是 datetime，也可能是 JSON 字符串
    created_at: Optional[Any] = None
    updated_at: Optional[Any] = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class FanoutCollectionService:
    """集群 Step → 冻结成员 → 展开 Task → 聚合/取消/恢复。"""

    def __init__(self, repository: Any, *, profile: EnvironmentProfile | None = None):
        self._repo = repository
        self._profile = profile

    # ── Membership ──────────────────────────────────────────────────────

    def build_membership_snapshot(
        self,
        *,
        environment_id: str,
        cluster_id: str,
        scope_revision: int = 1,
        topology_version: str = "topology-v1",
    ) -> MembershipSnapshot:
        """从 repo 注册的 Agent 固化成员快照。

        离线 Agent 进入排除列表（coverage 分母），不直接剔除，防止用两个成功
        节点代表整个集群。能力版本来自 agent.version，在线状态来自 agent.status。
        """
        agents = self._repo.list_agents() if hasattr(self._repo, "list_agents") else \
            list(getattr(self._repo, "agents", {}).values())
        members: list[MemberEntry] = []
        for agent in agents:
            if isinstance(agent, dict):
                agent_id = str(agent.get("agent_id") or agent.get("id") or "")
                status = str(agent.get("status") or "ONLINE")
                caps = agent.get("capabilities") or []
                hostname = str(agent.get("hostname") or agent_id)
                ip_addr = str(agent.get("ip_addr") or "")
                version = str(agent.get("agent_version") or agent.get("version") or "0.1.0")
                capability_version = str(agent.get("capability_version") or "0")
                agent_version = str(agent.get("version") or "0.1.0")
                fault_domain = str(agent.get("fault_domain") or "default")
                pid = int(agent.get("pid") or 1)
                platform_uid = str(agent.get("platform_uid") or "")
                instance_id = str(agent.get("instance_id") or agent_id)
            else:
                agent_id = str(getattr(agent, "id", "") or getattr(agent, "agent_id", ""))
                status = str(getattr(agent, "status", "ONLINE") or "ONLINE")
                caps = getattr(agent, "capabilities", []) or []
                hostname = str(getattr(agent, "hostname", "") or agent_id)
                ip_addr = str(getattr(agent, "ip_addr", "") or "")
                version = str(getattr(agent, "version", "0.1.0") or "0.1.0")
                capability_version = "0"
                agent_version = version
                fault_domain = "default"
                pid = 1
                platform_uid = ""
                instance_id = agent_id
            if not agent_id:
                continue
            online = status == "ONLINE"
            service_ids = [
                str(c).split(":", 1)[1] for c in caps
                if str(c).startswith("service:")
            ]
            fault_zones = [
                str(c).split(":", 1)[1] for c in caps
                if str(c).startswith("fault_domain:")
            ]
            if fault_zones:
                fault_domain = fault_zones[0]
            members.append(MemberEntry(
                agent_id=agent_id,
                hostname=hostname,
                ip_addr=ip_addr,
                instance_id=instance_id,
                service_id=service_ids[0] if service_ids else "",
                fault_domain=fault_domain,
                version=version,
                capability_version=capability_version,
                online=online,
                exclusion_reason="" if online else "OFFLINE",
                agent_version=agent_version,
                pid=pid,
                platform_uid=platform_uid,
            ))
        return MembershipSnapshot(
            snapshot_id=f"snap-{uuid4().hex[:16]}",
            environment_id=environment_id,
            cluster_id=cluster_id,
            topology_version=topology_version,
            scope_revision=scope_revision,
            members=members,
        )

    # ── 展开 ────────────────────────────────────────────────────────────

    def create_fanout_run(
        self,
        *,
        case_id: str,
        tenant_id: str,
        step: dict[str, Any],
        profile: EnvironmentProfile,
        environment_id: str,
        cluster_id: str,
        snapshot: MembershipSnapshot,
        resolution: TargetResolution,
    ) -> FanoutCollectionRun:
        """冻结成员并展开为单目标 Task（幂等：diagnosis_step_id 唯一键）。"""
        run_id = f"fanout-{uuid4().hex[:16]}"
        collector_id = str(step.get("collector_id") or "sys_metrics")
        run = FanoutCollectionRun(
            run_id=run_id,
            case_id=case_id,
            tenant_id=tenant_id,
            plan_step_id=str(step.get("step_id") or ""),
            plan_revision=int(step.get("plan_revision") or 0),
            scope_revision=int(step.get("scope_revision") or snapshot.scope_revision),
            snapshot_id=snapshot.snapshot_id,
            strategy=resolution.strategy,
            collector_id=collector_id,
            status="RUNNING",
        )
        self._dispatch_tasks(run, step, resolution)
        return self._repo.create_fanout_run(case_id, tenant_id, run.to_dict())

    def _dispatch_tasks(
        self,
        run: FanoutCollectionRun,
        step: dict[str, Any],
        resolution: TargetResolution,
    ) -> None:
        for target in resolution.targets:
            member = target.member
            step_id = str(step.get("step_id") or "")
            task = self._dispatch_one(
                case_id=run.case_id,
                tenant_id=run.tenant_id,
                step_id=step_id,
                run_id=run.run_id,
                member=member,
                collector_id=run.collector_id,
                step_kind=str(step.get("kind") or "COLLECTION"),
            )
            task_id = task.id if not isinstance(task, dict) else task.get("id")
            run.member_task_map[member.agent_id] = str(task_id)
            run.task_ids.append(str(task_id))
            run.target_members.append(member.model_dump(mode="json"))
            run.task_statuses[str(task_id)] = "PENDING"

    def _dispatch_one(
        self,
        *,
        case_id: str,
        tenant_id: str,
        step_id: str,
        run_id: str,
        member: MemberEntry,
        collector_id: str,
        step_kind: str,
    ):
        """创建（或复用）单个 Task。

        幂等：``create_task`` 的 ``idempotency_key = fanout:<step_id>:<agent_id>``
        跨 Run 稳定（恢复重放同一逻辑 Step 复用既有 Task），由唯一索引兜底——
        一个逻辑 Step 展开多个 Task，不能用唯一索引的 ``diagnosis_step_id`` 列
        （那会碰撞）。agent 离线/能力不匹配时拒绝创建，其失败在聚合中体现为未覆盖。
        """
        agent = self._repo.agents.get(member.agent_id) if hasattr(self._repo, "agents") else None
        if agent is None or str(getattr(agent, "status", "") or "") != "ONLINE":
            raise ValueError(f"MEMBER_OFFLINE:{member.agent_id}")
        from server.app.schemas import CreateTaskRequest
        step_key = f"{step_id}:{member.agent_id}" if step_id else f"{run_id}:{member.agent_id}"
        task = self._repo.create_task(
            CreateTaskRequest(
                name=f"fanout:{collector_id}:{member.agent_id}"[:120],
                agent_id=member.agent_id,
                target_pid=member.pid or 1,
                collector_type=collector_id,
                sample_rate=11,
                duration_sec=15,
                options={
                    "source": "fanout_collection_run",
                    "plan_step_id": step_id,
                    "fanout_target": member.agent_id,
                    "case_id": case_id,
                    "tenant_id": tenant_id,
                    # 注意：不携带 fanout_run_id —— 恢复重放同一逻辑 Step 时 payload
                    # 必须与首次一致，幂等键 fanout:<step_id>:<agent_id> 才生效。
                },
            ),
            idempotency_key=f"fanout:{step_key}",
        )
        return task

    # ── 状态更新 / 取消 / 恢复 ──────────────────────────────────────────

    def update_task_outcome(
        self,
        run: FanoutCollectionRun,
        task_id: str,
        status: str,
        *,
        scope_revision: int,
    ) -> dict[str, Any]:
        """记录单个 Task 结果；迟到结果（scope_revision 不匹配）隔离。"""
        if scope_revision != run.scope_revision:
            run.late_result_isolated.append(task_id)
            return run.to_dict()
        run.task_statuses[task_id] = status
        updated = self._repo.update_fanout_run(run.case_id, run.tenant_id, run.run_id, {
            "task_statuses": run.task_statuses,
            "late_result_isolated": run.late_result_isolated,
        })
        return updated

    def cancel_run(self, run: FanoutCollectionRun) -> dict[str, Any]:
        """取消传播：RUNNING/未完成 Task 全部转 CANCELLED；已 DONE 不动。"""
        if run.status in {"CANCELLED", "COMPLETED"}:
            return run.to_dict()
        for task_id in run.task_ids:
            status = run.task_statuses.get(task_id, "PENDING")
            if status in {"DONE", "CANCELLED"}:
                continue
            try:
                self._repo.cancel_task(task_id, "fanout_cancelled")
                run.task_statuses[task_id] = "CANCELLED"
            except ValueError:
                pass
        run.status = "CANCELLED"
        return self._repo.update_fanout_run(run.case_id, run.tenant_id, run.run_id, {
            "status": run.status,
            "task_statuses": run.task_statuses,
        })

    def resume_run(self, run: FanoutCollectionRun) -> dict[str, Any]:
        """恢复：未终态 Task 重新进入 RUNNING；已 CANCELLED 的 run 转回 RUNNING。"""
        if run.status == "COMPLETED":
            return run.to_dict()
        if run.status == "CANCELLED":
            run.status = "RUNNING"
        for task_id in run.task_ids:
            if run.task_statuses.get(task_id) == "CANCELLED":
                run.task_statuses[task_id] = "PENDING"
        return self._repo.update_fanout_run(run.case_id, run.tenant_id, run.run_id, {
            "status": run.status,
            "task_statuses": run.task_statuses,
        })

    # ── 聚合 ────────────────────────────────────────────────────────────

    def aggregate(
        self,
        run: FanoutCollectionRun,
        snapshot: MembershipSnapshot,
        *,
        time_aligned: bool = True,
        artifact_signals: dict[str, dict[str, Any]] | None = None,
    ) -> CoverageReport:
        """coverage-aware Evidence 聚合；只以成功成员作结论来源。"""
        succeeded = [
            agent_id for agent_id, task_id in run.member_task_map.items()
            if run.task_statuses.get(task_id) in {TASK_STATUS_DONE}
        ]
        failed = [
            agent_id for agent_id, task_id in run.member_task_map.items()
            if run.task_statuses.get(task_id) in {TASK_STATUS_FAILED, TASK_STATUS_CANCELLED}
        ]
        report = classify_coverage(
            snapshot=snapshot,
            succeeded_members=succeeded,
            failed_members=failed,
            time_aligned=time_aligned,
        )
        run.coverage = report.coverage
        run.failed_count = report.failed
        run.quorum_met = report.coverage >= 0.6
        signals = artifact_signals or {}
        run.aggregate = {
            "coverage": report.coverage,
            "conclusion": report.conclusion,
            "fault_domains_seen": report.fault_domains_seen,
            "quorum_met": run.quorum_met,
            "succeeded": report.succeeded,
            "failed": report.failed,
            "signals": {
                agent_id: signals.get(agent_id, {})
                for agent_id in succeeded
            },
        }
        self._repo.update_fanout_run(run.case_id, run.tenant_id, run.run_id, {
            "coverage": run.coverage,
            "failed_count": run.failed_count,
            "quorum_met": run.quorum_met,
            "aggregate": run.aggregate,
            "status": "COMPLETED" if run.quorum_met else "PARTIAL",
        })
        return report
