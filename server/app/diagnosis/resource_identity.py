"""统一资源身份图：tenant/cluster → service → instance → container → process → host。

每个节点保存稳定 ID、运行时 ID、版本、节点类型、发现来源、发现时间、有效时间、
置信等级。边至少覆盖 runs_on / contains / calls / connects_to / shares_host_with /
deployed_from / replaces。多来源合并按固定优先级：编排器事实 > Agent 进程发现 >
Trace/连接观测 > 用户补充 > 模型推测；模型推测只能生成待验证候选边。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from server.app.diagnosis.schemas import StrictModel

NODE_TYPE = Literal[
    "cluster", "service", "instance", "container", "cgroup", "process", "host",
]
SOURCE_PRIORITY = ["orchestrator", "agent_discovery", "trace", "user", "model"]
RELATIONS = {
    "runs_on", "contains", "calls", "connects_to",
    "shares_host_with", "deployed_from", "replaces",
}


class ResourceIdentity(StrictModel):
    stable_id: str = ...  # type: ignore[name-defined]  # pydantic 隐式 required
    runtime_id: str = ""
    version: str = ""
    node_type: NODE_TYPE = "process"
    source: str = "user"
    discovered_at: datetime = ...  # type: ignore[name-defined]
    valid_until: Optional[datetime] = None
    confidence: Literal["high", "medium", "low"] = "medium"

    def key(self) -> str:
        return self.stable_id


class ResourceEdge(StrictModel):
    source: str
    target: str
    relation: str
    confidence: Literal["high", "medium", "low"] = "medium"
    source_kind: str = "orchestrator"
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None


def _priority(source: str) -> int:
    try:
        return SOURCE_PRIORITY.index(source)
    except ValueError:
        return len(SOURCE_PRIORITY)


class ResourceIdentityGraph:
    """多来源合并的版本化资源身份图。"""

    def __init__(self) -> None:
        self._nodes: dict[str, ResourceIdentity] = {}
        self._edges: list[ResourceEdge] = []

    def nodes(self) -> list[ResourceIdentity]:
        return list(self._nodes.values())

    def edges(self) -> list[ResourceEdge]:
        return list(self._edges)

    def register(self, identity: ResourceIdentity) -> None:
        """按来源优先级合并：更高优先级来源覆盖低优先级身份。"""
        key = identity.key()
        existing = self._nodes.get(key)
        if existing is None or _priority(identity.source) < _priority(existing.source):
            self._nodes[key] = identity

    def add_edge(
        self,
        source: str,
        target: str,
        relation: str,
        *,
        confidence: str = "medium",
        source_kind: str = "orchestrator",
    ) -> None:
        if relation not in RELATIONS:
            raise ValueError(f"未注册的拓扑关系: {relation}")
        self._edges.append(ResourceEdge(
            source=source, target=target, relation=relation,
            confidence=confidence, source_kind=source_kind,
        ))

    def get(self, stable_id: str) -> Optional[ResourceIdentity]:
        return self._nodes.get(stable_id)

    def neighbors(self, stable_id: str) -> list[ResourceEdge]:
        return [
            edge for edge in self._edges
            if edge.source == stable_id or edge.target == stable_id
        ]

    def downstream(self, stable_id: str) -> list[str]:
        """按 calls/reads 等依赖边找一跳下游。"""
        return [
            edge.target for edge in self._edges
            if edge.source == stable_id
            and edge.relation in {"calls", "connects_to"}
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "resource-identity.v1",
            "nodes": [item.model_dump(mode="json") for item in self._nodes.values()],
            "edges": [item.model_dump(mode="json") for item in self._edges],
        }


def build_identity_graph(
    *,
    service_id: str | None,
    instances: list[dict[str, Any]],
    dependencies: list[dict[str, Any]],
    hosts: dict[str, str] | None = None,
) -> ResourceIdentityGraph:
    """从诊断作用域（实例 + 依赖边 + 宿主映射）构建身份图。

    发现来源标记为 orchestrator（来自请求上下文的编排器事实）。
    """
    graph = ResourceIdentityGraph()
    hosts = hosts or {}
    seen: set[str] = set()
    for item in instances:
        instance_id = item.get("instance_id")
        svc = item.get("service_id") or service_id
        if not instance_id or instance_id in seen:
            continue
        seen.add(instance_id)
        graph.register(ResourceIdentity(
            stable_id=svc, runtime_id=instance_id, node_type="service",
            source="orchestrator", discovered_at=_utcnow(), confidence="high",
        ))
        graph.register(ResourceIdentity(
            stable_id=instance_id, runtime_id=instance_id, node_type="instance",
            source="orchestrator", discovered_at=_utcnow(), confidence="high",
        ))
        graph.add_edge(instance_id, svc, "contains", confidence="high")
        host_id = item.get("host_id")
        if host_id:
            graph.register(ResourceIdentity(
                stable_id=host_id, runtime_id=host_id, node_type="host",
                source="orchestrator", discovered_at=_utcnow(), confidence="high",
            ))
            graph.add_edge(instance_id, host_id, "runs_on", confidence="high")
        container_id = item.get("container_id")
        if container_id:
            graph.register(ResourceIdentity(
                stable_id=container_id, runtime_id=container_id, node_type="container",
                source="orchestrator", discovered_at=_utcnow(), confidence="high",
            ))
            graph.add_edge(container_id, instance_id, "contains", confidence="high")
        pid = item.get("pid")
        if pid:
            graph.register(ResourceIdentity(
                stable_id=f"process:{instance_id}:{pid}", runtime_id=str(pid),
                node_type="process", source="orchestrator",
                discovered_at=_utcnow(), confidence="high",
            ))
            graph.add_edge(
                f"process:{instance_id}:{pid}", instance_id, "contains", confidence="high",
            )
    relation_map = {
        "CALLS": "calls",
        "READS_FROM": "calls",
        "WRITES_TO": "connects_to",
        "PUBLISHES_TO": "connects_to",
        "CONSUMES_FROM": "calls",
        "SHARES_DEPENDENCY": "shares_host_with",
    }
    for edge in dependencies:
        source = edge.get("source_service")
        target = edge.get("target_service")
        relation = relation_map.get(str(edge.get("relation") or ""), "connects_to")
        if source and target:
            graph.add_edge(source, target, relation)
    return graph


def merge_identity_graphs(primary: ResourceIdentityGraph, *others: ResourceIdentityGraph) -> ResourceIdentityGraph:
    """按来源优先级合并多份身份图（primary 优先）。"""
    merged = ResourceIdentityGraph()
    for identity in primary.nodes():
        merged.register(identity)
    for identity in (item for other in others for item in other.nodes()):
        merged.register(identity)
    for other in (primary, *others):
        for edge in other.edges():
            merged.add_edge(
                edge.source, edge.target, edge.relation,
                confidence=edge.confidence, source_kind=edge.source_kind,
            )
    return merged


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
