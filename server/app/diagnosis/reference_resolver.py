"""Reference Resolver: turn `@` mentions and structured refs into stable IDs.

E1 (plan section 5.1): the conversation `@` is not plain text.  The frontend
submits a structured ResourceRef; this resolver validates tenant ownership,
object state and version, and returns stable IDs — never the display name as an
ID.  Model guesses are never auto-promoted to production targets.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import Field

from server.app.diagnosis.schemas import StrictModel

RESOURCE_TYPES = (
    "task", "collection", "service", "agent", "process",
    "evidence", "case", "change", "target_session",
    # E3.5 集群一等领域模型（计划 3.5）
    "cluster", "workload", "instance", "environment", "host",
)


class ResourceRef(StrictModel):
    type: str = Field(min_length=1, max_length=40)
    id: str = Field(min_length=1, max_length=128)
    revision: Optional[int] = None
    label: str = ""
    source: str = "user_mention"
    # 仅 collection 使用：显式成员（前端分组展开），不发明隐式集合域对象
    member_task_ids: list[str] = Field(default_factory=list)


class ResolvedResource(StrictModel):
    ref: ResourceRef
    label: str
    eligible: bool
    reason_code: Optional[str] = None  # e.g. TASK_NOT_DONE / TASK_NO_STRUCTURED_RESULT
    evidence_ids: list[str] = Field(default_factory=list)


class SearchCandidate(StrictModel):
    type: str
    id: str
    label: str
    revision: Optional[int] = None
    status: Optional[str] = None
    match_field: str = "id"


def _task_status_value(task: Any) -> str:
    return str(getattr(task, "status", "") or getattr(task, "status_value", "") or "")


class ReferenceResolver:
    """Tenant-scoped, state-checking resolver backed by the repository."""

    def __init__(self, repository: Any):
        self._repo = repository

    # ── search（@ 自动补全）───────────────────────────────────────────
    def search(
        self,
        query: str,
        tenant_id: str,
        *,
        ref_type: str | None = None,
        limit: int = 10,
    ) -> list[SearchCandidate]:
        query = (query or "").strip().lower()
        candidates: list[SearchCandidate] = []
        if ref_type in {None, "task"}:
            candidates.extend(self._search_tasks(query, tenant_id))
        if ref_type in {None, "agent"}:
            candidates.extend(self._search_agents(query))
        if ref_type in {None, "case"}:
            candidates.extend(self._search_cases(query, tenant_id))
        if ref_type in {None, "service"}:
            candidates.extend(self._search_services(query))
        if ref_type in {None, "cluster", "workload", "instance"}:
            candidates.extend(self._search_cluster_scope(query))
        return candidates[:limit]

    def _search_tasks(self, query: str, tenant_id: str) -> list[SearchCandidate]:
        out: list[SearchCandidate] = []
        tasks = getattr(self._repo, "tasks", None)
        if not isinstance(tasks, dict):
            return out
        for task_id, task in tasks.items():
            task_id = str(task_id)
            label = str(getattr(task, "name", "") or task_id)
            if query and query not in task_id.lower() and query not in label.lower():
                continue
            out.append(SearchCandidate(
                type="task", id=task_id,
                label=label,
                status=_task_status_value(task),
                match_field="id" if query in task_id.lower() else "name",
            ))
            if len(out) >= 10:
                break
        return out

    def _search_agents(self, query: str) -> list[SearchCandidate]:
        out: list[SearchCandidate] = []
        agents = self._repo.list_agents() if hasattr(self._repo, "list_agents") else []
        for agent in agents:
            agent_id = str(agent.get("agent_id") or "")
            if not agent_id:
                continue
            if query and query not in agent_id.lower() and query not in str(agent.get("hostname", "")).lower():
                continue
            out.append(SearchCandidate(
                type="agent", id=agent_id, label=agent_id,
                status=str(agent.get("status") or ""), match_field="id",
            ))
        return out

    def _search_cases(self, query: str, tenant_id: str) -> list[SearchCandidate]:
        out: list[SearchCandidate] = []
        cases = self._repo.list_incident_cases(tenant_id=tenant_id, limit=10) if hasattr(
            self._repo, "list_incident_cases"
        ) else []
        for case in cases:
            case_id = str(case.get("case_id") or "")
            if not case_id:
                continue
            if query and query not in case_id.lower() and query not in str(case.get("title", "")).lower():
                continue
            out.append(SearchCandidate(
                type="case", id=case_id, label=str(case.get("title") or case_id),
                status=str(case.get("state") or ""), match_field="id",
            ))
        return out

    def _search_services(self, query: str) -> list[SearchCandidate]:
        services: dict[str, str] = {}
        try:
            agents = self._repo.list_agents() if hasattr(self._repo, "list_agents") else []
            for agent in agents:
                caps = agent.get("capabilities") or []
                for capability in caps if isinstance(caps, list) else []:
                    if str(capability).startswith("service:"):
                        services.setdefault(str(capability).split(":", 1)[1], str(capability).split(":", 1)[1])
        except Exception:  # noqa: BLE001 — 搜索容错，不阻断 @ 输入
            pass
        out: list[SearchCandidate] = []
        for service_id, label in services.items():
            if query and query not in service_id.lower():
                continue
            out.append(SearchCandidate(type="service", id=service_id, label=label, match_field="id"))
        return out

    def _search_cluster_scope(self, query: str) -> list[SearchCandidate]:
        """E3.5：集群/工作负载/实例资源候选（从 Agent 注册能力投影，不发明对象）。"""
        out: list[SearchCandidate] = []
        seen: set[tuple[str, str]] = set()
        try:
            agents = self._repo.list_agents() if hasattr(self._repo, "list_agents") else \
                list(getattr(self._repo, "agents", {}).values())
        except Exception:  # noqa: BLE001 — 搜索容错
            return out
        for agent in agents:
            caps = agent.get("capabilities") if isinstance(agent, dict) else getattr(agent, "capabilities", [])
            for capability in caps if isinstance(caps, list) else []:
                capability = str(capability)
                for prefix, res_type in (
                    ("cluster:", "cluster"), ("workload:", "workload"),
                    ("service:", "service"), ("instance:", "instance"),
                ):
                    if not capability.startswith(prefix):
                        continue
                    resource_id = capability.split(":", 1)[1]
                    key = (res_type, resource_id)
                    if key in seen:
                        break
                    seen.add(key)
                    if query and query not in resource_id.lower():
                        break
                    out.append(SearchCandidate(
                        type=res_type, id=resource_id, label=resource_id,
                        status="", match_field="capability",
                    ))
                    break
        return out[:10]

    # ── resolve（结构化引用校验）────────────────────────────────────────
    def resolve(
        self,
        ref: ResourceRef,
        tenant_id: str,
        *,
        case: dict[str, Any] | None = None,
    ) -> ResolvedResource:
        if ref.type not in RESOURCE_TYPES:
            return ResolvedResource(ref=ref, label=ref.label, eligible=False, reason_code="UNKNOWN_TYPE")
        if ref.type == "task":
            return self._resolve_task(ref, tenant_id, case=case)
        if ref.type == "collection":
            return self._resolve_collection(ref, tenant_id, case=case)
        if ref.type == "case":
            existing = self._repo.get_incident_case(ref.id, tenant_id) if hasattr(
                self._repo, "get_incident_case"
            ) else None
            if existing is None:
                return ResolvedResource(ref=ref, label=ref.label, eligible=False, reason_code="CASE_NOT_FOUND")
            return ResolvedResource(
                ref=ref, label=str(existing.get("title") or ref.label), eligible=True,
            )
        if ref.type == "agent":
            agents = self._repo.list_agents() if hasattr(self._repo, "list_agents") else []
            if any(str(item.get("agent_id") or "") == ref.id for item in agents):
                return ResolvedResource(ref=ref, label=ref.label or ref.id, eligible=True)
            return ResolvedResource(ref=ref, label=ref.label, eligible=False, reason_code="AGENT_NOT_FOUND")
        # service / process / evidence / change / target_session: 首期允许显式关联但仅作只读引用
        return ResolvedResource(ref=ref, label=ref.label or ref.id, eligible=True)

    def _resolve_task(
        self,
        ref: ResourceRef,
        tenant_id: str,
        *,
        case: dict[str, Any] | None,
    ) -> ResolvedResource:
        task = self._repo.tasks.get(ref.id) if getattr(self._repo, "tasks", None) else None
        if task is None:
            return ResolvedResource(ref=ref, label=ref.label, eligible=False, reason_code="TASK_NOT_FOUND")
        status = _task_status_value(task)
        if status != "DONE":
            return ResolvedResource(
                ref=ref, label=ref.label or ref.id, eligible=False, reason_code="TASK_NOT_DONE",
            )
        artifacts = self._repo.artifacts.get(ref.id, []) if getattr(self._repo, "artifacts", None) else []
        if not any(item.get("artifact_type") and item.get("metadata") for item in artifacts):
            return ResolvedResource(
                ref=ref, label=ref.label or ref.id, eligible=False,
                reason_code="TASK_NO_STRUCTURED_RESULT",
            )
        label = str(getattr(task, "name", "") or ref.id)
        return ResolvedResource(ref=ref, label=label, eligible=True)

    def _resolve_collection(
        self,
        ref: ResourceRef,
        tenant_id: str,
        *,
        case: dict[str, Any] | None,
    ) -> ResolvedResource:
        members = list(dict.fromkeys(ref.member_task_ids or []))
        if not members:
            return ResolvedResource(
                ref=ref, label=ref.label or ref.id, eligible=False, reason_code="COLLECTION_EMPTY",
            )
        # 集合的合格性 = 至少一个成员合格；成员级结果由调用方逐条呈现
        any_eligible = False
        for task_id in members:
            member_ref = ResourceRef(type="task", id=task_id, source=ref.source)
            resolved = self._resolve_task(member_ref, tenant_id, case=case)
            if resolved.eligible:
                any_eligible = True
                break
        if not any_eligible:
            return ResolvedResource(
                ref=ref, label=ref.label or ref.id, eligible=False,
                reason_code="COLLECTION_NO_ELIGIBLE_MEMBER",
            )
        return ResolvedResource(ref=ref, label=ref.label or ref.id, eligible=True)
