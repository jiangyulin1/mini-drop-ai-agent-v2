"""E3.5: 集群范围一等领域模型与确定性 Target Resolver。

计划 3.5/3.6/8.13：集群调查先冻结 Membership Snapshot，再按选择策略展开为
单目标 Task。Mini-Drop 是成员快照的唯一固化者；模型无权把一个单机步骤悄悄
扩大为整个集群，也无权在缺少稳定锚点时扇出。

本模块保持纯确定性（不依赖 repo / DB），便于单元验证退出条件：
- EnvironmentProfile：环境类型、平台、地域、集群、允许数据源、默认风险策略、
  时钟质量与容量预算；
- ClusterResource：稳定资源 ID、父子关系、标签投影、故障域、Owner、生命周期
  与进程身份（platform_uid / boot_id / container_id / cgroup_id / pid /
  process_start_time），避免 Pod 重建与 PID 复用后把旧 Evidence 归到新实例；
- MembershipSnapshot：某个时间点参与调查的 Agent/实例集合、能力版本、在线状态、
  拓扑版本与排除原因；
- SelectionStrategy：ALL_IN_SCOPE / REPRESENTATIVE / OUTLIERS /
  CHANGE_COHORT / CANARY_AND_CONTROL / DEPENDENCY_FRONTIER；
- TargetResolver.resolve_collection_targets：冻结成员 + 选择原因 + 预算/并发门禁；
- 覆盖率判定与结论层级（cluster-wide / fault-domain / node-local / workload /
  instance / process / dependency / insufficient_coverage）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import Field

from server.app.diagnosis.schemas import StrictModel

SELECTION_STRATEGIES = Literal[
    "ALL_IN_SCOPE",
    "REPRESENTATIVE",
    "OUTLIERS",
    "CHANGE_COHORT",
    "CANARY_AND_CONTROL",
    "DEPENDENCY_FRONTIER",
]
SELECTION_STRATEGY_VALUES = [
    "ALL_IN_SCOPE", "REPRESENTATIVE", "OUTLIERS",
    "CHANGE_COHORT", "CANARY_AND_CONTROL", "DEPENDENCY_FRONTIER",
]

# 结论层级：集群调查必须区分范围，不能拿两个成功节点代表整个集群。
CONCLUSION_LEVELS = Literal[
    "cluster-wide", "fault-domain", "node-local", "workload",
    "instance", "process", "dependency", "insufficient_coverage",
]

CLUSTER_RESOURCE_TYPES = Literal[
    "cluster", "workload", "instance", "host", "process",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── EnvironmentProfile ─────────────────────────────────────────────────


class CapacityBudget(StrictModel):
    """全局容量预算（计划 3.6）：同时限制多类资源，超出即拒绝展开。"""

    max_clusters: int = 1
    max_nodes: int = 64
    max_instances: int = 128
    max_fault_domains: int = 8
    max_topology_hops: int = 2
    max_parallel_tasks: int = 16
    per_fault_domain_parallelism: int = 4
    artifact_quota_mb: int = 512


class EnvironmentProfile(StrictModel):
    environment_id: str = Field(min_length=1, max_length=128)
    environment_type: str = "production"
    platform: str = "kubernetes"
    region: str = ""
    cluster: str = ""
    allowed_data_sources: list[str] = Field(default_factory=list)
    default_risk_policy: str = "READ_LOW"
    clock_quality: Literal["good", "unknown", "poor"] = "good"
    capacity: CapacityBudget = Field(default_factory=CapacityBudget)
    revision: int = 1

    def key(self) -> str:
        """范围按 tenant + environment + cluster 逐级收窄的稳定锚点。"""
        return f"{self.environment_id}/{self.cluster}".rstrip("/")


# ── ClusterResource ────────────────────────────────────────────────────


class ClusterResource(StrictModel):
    """稳定资源 ID + 进程身份，保证 Pod 重建 / PID 复用后的可追溯性。"""

    stable_id: str = Field(min_length=1, max_length=128)
    resource_type: CLUSTER_RESOURCE_TYPES = "instance"
    parent_id: Optional[str] = None
    labels: dict[str, str] = Field(default_factory=dict)
    fault_domain: str = "default"
    owner: str = ""
    lifecycle: Literal["active", "deleted", "replaced"] = "active"
    # 身份字段（能取则取，防漂移）
    platform_uid: str = ""
    agent_id: str = ""
    boot_id: str = ""
    container_id: str = ""
    cgroup_id: str = ""
    pid: int = 0
    process_start_time: Optional[datetime] = None
    version: str = ""

    def ref(self) -> str:
        return f"{self.resource_type}:{self.stable_id}"

    def matches(self, ref: str) -> bool:
        """ResourceRef 匹配：支持 ``instance:xxx`` 与裸 ``xxx``。"""
        if ":" in ref:
            _type, _id = ref.split(":", 1)
            if _type != self.resource_type:
                return False
            return _id == self.stable_id
        return ref == self.stable_id


# ── Membership ─────────────────────────────────────────────────────────


class MemberEntry(StrictModel):
    """一个参与调查的 Agent/实例；离线/能力不匹配必须带排除原因进入分母。"""

    agent_id: str = Field(min_length=1, max_length=128)
    hostname: str = ""
    ip_addr: str = ""
    instance_id: str = ""
    service_id: str = ""
    fault_domain: str = "default"
    version: str = ""
    capability_version: str = "0"
    online: bool = True
    exclusion_reason: str = ""
    # 目标进程身份（防 PID 复用 / Pod 漂移）
    pid: int = 0
    process_start_time: Optional[datetime] = None
    platform_uid: str = ""
    agent_version: str = "0.1.0"

    def usable(self) -> bool:
        return self.online and not self.exclusion_reason


class MembershipSnapshot(StrictModel):
    """某个时间点冻结的参与集合；调查期间成员变化不修改历史快照。"""

    snapshot_id: str = Field(min_length=1, max_length=128)
    captured_at: datetime = Field(default_factory=_utcnow)
    environment_id: str = ""
    cluster_id: str = ""
    topology_version: str = ""
    scope_revision: int = 1
    members: list[MemberEntry] = Field(default_factory=list)

    def eligible(self) -> list[MemberEntry]:
        return [member for member in self.members if member.usable()]

    def excluded(self) -> list[MemberEntry]:
        return [member for member in self.members if not member.usable()]

    def coverage_denominator(self) -> int:
        return max(1, len(self.members))

    def fault_domains(self) -> dict[str, list[MemberEntry]]:
        domains: dict[str, list[MemberEntry]] = {}
        for member in self.members:
            domains.setdefault(member.fault_domain, []).append(member)
        return domains


# ── Target Resolver ────────────────────────────────────────────────────


class ResolvedTarget(StrictModel):
    """Resolver 输出的冻结成员 + 选择原因；Supervisor 只从这里展开 Task。"""

    member: MemberEntry
    resource: ClusterResource
    selection_reason: str = "in_scope"


class TargetResolution(StrictModel):
    snapshot: MembershipSnapshot
    strategy: str
    targets: list[ResolvedTarget] = Field(default_factory=list)
    excluded: list[dict[str, Any]] = Field(default_factory=list)
    rejected: list[dict[str, Any]] = Field(default_factory=list)
    selection_notes: list[str] = Field(default_factory=list)


class TargetResolver:
    """确定性选择策略；所有策略都以冻结快照 + 预算为输入，绝不明文枚举。"""

    def __init__(self) -> None:
        pass

    # ── 选择策略 ──────────────────────────────────────────────────
    def resolve_collection_targets(
        self,
        snapshot: MembershipSnapshot,
        strategy: str,
        *,
        profile: EnvironmentProfile,
        target_refs: list[str] | None = None,
        metric_scores: dict[str, float] | None = None,
        canary_labels: set[str] | None = None,
        control_labels: set[str] | None = None,
        change_cohort_version: str = "",
        max_targets: int = 0,
    ) -> TargetResolution:
        """冻结成员快照并按策略选择目标。

        target_refs 存在时视为显式收窄（用户/Agent 明确点名，仍然必须在快照内）；
        否则按 strategy 从 eligible 成员中选择。任何越出快照的 ref 都被拒绝。
        """
        resolution = TargetResolution(
            snapshot=snapshot, strategy=strategy,
            excluded=[
                {"agent_id": m.agent_id, "reason": m.exclusion_reason or "OFFLINE"}
                for m in snapshot.excluded()
            ],
        )
        eligible = snapshot.eligible()
        budget = profile.capacity
        cap = max_targets if max_targets > 0 else budget.max_instances

        # 1) 成员枚举 ref 与作用域锚点 ref 分离。
        #    - 作用域锚点（cluster/workload/service/host/environment）只收窄逻辑
        #      范围，不枚举成员；防模型用任意主机名扩大范围。
        #    - 成员 ref（instance/process/agent）必须落在快照内，否则拒绝。
        member_refs: list[str] = []
        scope_refs: list[str] = []
        for ref in (target_refs or []):
            if ":" in ref and ref.split(":", 1)[0] in {
                "cluster", "workload", "service", "host", "environment",
            }:
                scope_refs.append(ref)
            else:
                member_refs.append(ref)

        # 2) 作用域锚点验证。
        for ref in scope_refs:
            _type, _id = ref.split(":", 1)
            if _type == "cluster" and _id not in {snapshot.cluster_id, ""}:
                resolution.rejected.append({"ref": ref, "reason": "SCOPE_MISMATCH"})
                return self._finalize(resolution, budget)
            if _type == "environment" and _id not in {snapshot.environment_id, ""}:
                resolution.rejected.append({"ref": ref, "reason": "SCOPE_MISMATCH"})
                return self._finalize(resolution, budget)
        if scope_refs:
            resolution.selection_notes.append(f"scope_anchors:{sorted(scope_refs)}")

        # 3) 显式成员 ref：必须在快照内，否则拒绝。
        if member_refs:
            by_agent = {m.agent_id: m for m in eligible}
            by_instance = {m.instance_id: m for m in eligible}
            for ref in member_refs:
                member = None
                if ":" in ref:
                    _type, _id = ref.split(":", 1)
                    member = by_instance.get(_id) if _type == "instance" else None
                if member is None and ref in by_agent:
                    member = by_agent[ref]
                if member is None:
                    resolution.rejected.append({"ref": ref, "reason": "NOT_IN_SNAPSHOT"})
                    continue
                if len(resolution.targets) >= cap:
                    resolution.rejected.append({"ref": ref, "reason": "BUDGET_EXCEEDED"})
                    continue
                resolution.targets.append(ResolvedTarget(
                    member=member,
                    resource=_resource_for_member(member),
                    selection_reason="explicit_ref",
                ))
            resolution.selection_notes.append(
                f"explicit_refs:{len(member_refs)} accepted:{len(resolution.targets)} rejected:{len(resolution.rejected)}"
            )
            return self._finalize(resolution, budget)

        # 4) 策略化选择。
        if strategy == "ALL_IN_SCOPE":
            picked = eligible[:cap]
        elif strategy == "REPRESENTATIVE":
            picked = self._representative(eligible, budget, cap)
        elif strategy == "OUTLIERS":
            picked = self._outliers(eligible, metric_scores, cap)
        elif strategy == "CHANGE_COHORT":
            picked = [
                m for m in eligible
                if m.version and m.version == change_cohort_version
            ][:cap]
            resolution.selection_notes.append(
                f"change_cohort:{change_cohort_version or 'unset'}"
            )
        elif strategy == "CANARY_AND_CONTROL":
            picked = self._canary_and_control(
                eligible, canary_labels or set(), control_labels or set(), cap,
            )
        elif strategy == "DEPENDENCY_FRONTIER":
            picked = eligible[:cap]  # 拓扑跳数由调用方以 target_refs 表达
        else:
            raise ValueError(f"UNKNOWN_STRATEGY:{strategy}")

        for member in picked:
            if len(resolution.targets) >= cap:
                resolution.rejected.append(
                    {"agent_id": member.agent_id, "reason": "BUDGET_EXCEEDED"}
                )
                continue
            resolution.targets.append(ResolvedTarget(
                member=member, resource=_resource_for_member(member),
                selection_reason=strategy.lower(),
            ))
        return self._finalize(resolution, budget)

    def _finalize(self, resolution: TargetResolution, budget: CapacityBudget) -> TargetResolution:
        domains = {t.member.fault_domain for t in resolution.targets}
        if len(domains) > budget.max_fault_domains:
            resolution.rejected.append(
                {"reason": "FAULT_DOMAIN_LIMIT", "domains": len(domains)}
            )
        if len(resolution.targets) > budget.max_parallel_tasks:
            resolution.selection_notes.append(
                "concurrency_capped_by_max_parallel_tasks"
            )
        return resolution

    # ── 各策略实现 ─────────────────────────────────────────────────

    def _representative(
        self,
        eligible: list[MemberEntry],
        budget: CapacityBudget,
        cap: int,
    ) -> list[MemberEntry]:
        """按故障域 + 版本分层取样：每故障域至少 1，余量按域规模比例分配。"""
        domains: dict[str, list[MemberEntry]] = {}
        for member in eligible:
            domains.setdefault(member.fault_domain, []).append(member)
        picked: list[MemberEntry] = []
        per_domain = max(1, budget.per_fault_domain_parallelism)
        for _domain, members in sorted(domains.items(), key=lambda kv: kv[0]):
            if len(picked) >= cap:
                break
            chosen = members[:per_domain]
            picked.extend(chosen)
        # 余量：各域轮询补齐，保持分层而非前 N。
        idx = 0
        while len(picked) < cap:
            domain_names = sorted(domains.keys())
            if not domain_names:
                break
            key = domain_names[idx % len(domain_names)]
            members = domains[key]
            advanced = False
            for member in members:
                if member in picked:
                    continue
                picked.append(member)
                advanced = True
                break
            if not advanced:
                break
            idx += 1
        return picked

    def _outliers(
        self,
        eligible: list[MemberEntry],
        metric_scores: dict[str, float] | None,
        cap: int,
    ) -> list[MemberEntry]:
        """根据已有指标选异常实例；无指标时按 FaultDomain 至少取一个并降级说明。"""
        scores = metric_scores or {}
        ranked = sorted(
            eligible,
            key=lambda m: scores.get(m.agent_id, scores.get(m.instance_id, 0.0)),
            reverse=True,
        )
        picked = ranked[:cap]
        if not metric_scores:
            # 无指标时不假装有异常排序：每故障域取一个代表。
            seen: set[str] = set()
            representative: list[MemberEntry] = []
            for member in eligible:
                if member.fault_domain in seen:
                    continue
                seen.add(member.fault_domain)
                representative.append(member)
            picked = representative[:cap]
        return picked

    def _canary_and_control(
        self,
        eligible: list[MemberEntry],
        canary_labels: set[str],
        control_labels: set[str],
        cap: int,
    ) -> list[MemberEntry]:
        """比较金丝雀与对照组：两组各取 cap//2，缺组时只取有组并记录缺组。"""
        canary = [m for m in eligible if m.instance_id in canary_labels or m.hostname in canary_labels]
        control = [m for m in eligible if m.instance_id in control_labels or m.hostname in control_labels]
        if not canary:
            return control[:cap]
        if not control:
            return canary[:cap]
        half = max(1, cap // 2)
        return canary[:half] + control[:half]


# ── 覆盖率与结论层级 ───────────────────────────────────────────────────


class CoverageReport(StrictModel):
    coverage: float = 0.0
    denominator: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    fault_domains_seen: list[str] = Field(default_factory=list)
    time_aligned: bool = True
    conclusion: CONCLUSION_LEVELS = "insufficient_coverage"
    notes: list[str] = Field(default_factory=list)


def classify_coverage(
    *,
    snapshot: MembershipSnapshot,
    succeeded_members: list[str],
    failed_members: list[str],
    time_aligned: bool,
) -> CoverageReport:
    """覆盖率判定：不足或时间不对齐只能输出 insufficient_coverage。"""
    denominator = snapshot.coverage_denominator()
    succeeded = len(set(succeeded_members))
    failed = len(set(failed_members))
    # 覆盖率 = 成功节点证据占比；失败/离线/未执行都不产生证据，不算覆盖。
    coverage = succeeded / denominator
    domains = sorted({
        m.fault_domain for m in snapshot.members
        if m.agent_id in set(succeeded_members) or m.agent_id in set(failed_members)
    })
    report = CoverageReport(
        coverage=coverage,
        denominator=denominator,
        succeeded=succeeded,
        failed=failed,
        skipped=denominator - succeeded - failed,
        fault_domains_seen=domains,
        time_aligned=time_aligned,
        conclusion=_pick_conclusion(snapshot, succeeded, failed, time_aligned),
    )
    if coverage < 0.6:
        report.notes.append("覆盖率低于 0.6，仅输出局部结论")
    if failed:
        report.notes.append(f"{failed} 个成员失败（未覆盖）")
    if not time_aligned:
        report.notes.append("时间窗未对齐，仅输出局部结论")
    return report


def _pick_conclusion(
    snapshot: MembershipSnapshot,
    succeeded: int,
    failed: int,
    time_aligned: bool,
) -> str:
    """把成功/失败分布映射到结论层级（计划 3.6）。

    覆盖率 <0.6 或时间不对齐时只能输出 insufficient_coverage，禁止用两个成功
    节点代表整个集群。
    """
    if succeeded == 0:
        return "insufficient_coverage"
    if not time_aligned:
        return "insufficient_coverage"
    total = snapshot.coverage_denominator()
    # 证据覆盖率 = 成功节点 / 总数；<0.6 证据不足只能输出 insufficient_coverage。
    if succeeded / total < 0.6:
        return "insufficient_coverage"
    if failed > 0:
        # 存在失败成员 → 异常分布需在故障域/节点级定位，不能宣称 cluster-wide。
        return "fault-domain" if len(snapshot.fault_domains()) > 1 else "node-local"
    return "cluster-wide"


def _resource_for_member(member: MemberEntry) -> ClusterResource:
    return ClusterResource(
        stable_id=member.instance_id or member.agent_id,
        resource_type="instance",
        parent_id=member.service_id or None,
        labels={"service": member.service_id} if member.service_id else {},
        fault_domain=member.fault_domain,
        agent_id=member.agent_id,
        pid=member.pid,
        process_start_time=member.process_start_time,
        platform_uid=member.platform_uid,
        version=member.version,
    )
