"""未知拓扑发现的数据契约与确定性依赖图。

本模块只表达观测事实、身份声明和通信关系，不把依赖边提升为因果边。它刻意
不依赖数据库、HTTP 路由或 Agent 实现，便于 Agent snapshot、历史 Artifact 和
合成 replay 共用同一套严格契约。
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import Field, field_validator, model_validator

from server.app.diagnosis.schemas import StrictModel


PROTOCOLS = Literal["tcp", "udp"]
CONFIDENCE_LEVELS = Literal["high", "medium", "low"]


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} 必须包含时区")


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}-{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:24]}"


class NetworkEndpoint(StrictModel):
    """规范化的 L4 endpoint；IPv6 输出始终使用方括号。"""

    address: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=0, le=65535)
    protocol: PROTOCOLS = "tcp"

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        if value == "*":
            return value
        try:
            return ipaddress.ip_address(value).compressed
        except ValueError as exc:
            raise ValueError("address 必须是 IPv4、IPv6 或 *") from exc

    @classmethod
    def parse(cls, value: str, *, protocol: str = "tcp") -> "NetworkEndpoint":
        text = value.strip()
        if "://" in text:
            parsed_protocol, text = text.split("://", 1)
            protocol = parsed_protocol
        if text.startswith("["):
            closing = text.find("]")
            if closing <= 0 or closing + 1 >= len(text) or text[closing + 1] != ":":
                raise ValueError(f"INVALID_ENDPOINT:{value}")
            address = text[1:closing]
            port_text = text[closing + 2:]
        else:
            try:
                address, port_text = text.rsplit(":", 1)
            except ValueError as exc:
                raise ValueError(f"INVALID_ENDPOINT:{value}") from exc
        try:
            port = int(port_text)
        except ValueError as exc:
            raise ValueError(f"INVALID_ENDPOINT_PORT:{value}") from exc
        return cls(address=address, port=port, protocol=protocol)

    def canonical(self) -> str:
        address = self.address
        if ":" in address and address != "*":
            address = f"[{address}]"
        return f"{self.protocol}://{address}:{self.port}"

    def entity_id(self) -> str:
        return f"ip_endpoint:{self.canonical()}"

    def is_wildcard(self) -> bool:
        return self.address in {"*", "0.0.0.0", "::"}


class ProcessIncarnation(StrictModel):
    """防 PID 复用的进程身份，四元组缺一不可。"""

    agent_id: str = Field(min_length=1, max_length=128)
    boot_id: str = Field(min_length=1, max_length=128)
    pid: int = Field(gt=0, le=4194304)
    process_start_time: int = Field(gt=0)
    cgroup_id: str = Field(default="", max_length=256)
    netns: str = Field(default="", max_length=128)
    executable: str = Field(default="", max_length=1024)

    def stable_ref(self) -> str:
        return (
            f"process:{self.agent_id}:{self.boot_id}:"
            f"{self.pid}:{self.process_start_time}"
        )


class SocketObservation(StrictModel):
    cookie: str = Field(default="", max_length=128)
    local: NetworkEndpoint
    remote: Optional[NetworkEndpoint] = None
    result: Literal["success", "failure", "reset", "timeout", "unknown"] = "unknown"
    observation_point: Literal["client", "server", "host", "snapshot"] = "host"
    bytes_sent: int = Field(default=0, ge=0)
    bytes_received: int = Field(default=0, ge=0)


class DiscoveryEvent(StrictModel):
    """单条连接/监听观测；原始 payload 不属于该契约。"""

    schema_version: Literal["network-discovery-event.v1"] = "network-discovery-event.v1"
    event_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    boot_id: str = Field(min_length=1, max_length=128)
    observed_at: datetime
    event_type: Literal[
        "tcp_connect", "tcp_accept", "tcp_close", "tcp_listen", "tcp_snapshot",
    ]
    process: Optional[ProcessIncarnation] = None
    socket: SocketObservation
    source: Literal["ebpf", "sock_diag", "procfs", "lsof", "replay"] = "procfs"
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_event(self):
        _require_aware(self.observed_at, "observed_at")
        if self.process is not None:
            if self.process.agent_id != self.agent_id:
                raise ValueError("process.agent_id 必须与事件 agent_id 一致")
            if self.process.boot_id != self.boot_id:
                raise ValueError("process.boot_id 必须与事件 boot_id 一致")
        if self.socket.local.protocol != "tcp":
            raise ValueError("tcp_* 事件必须使用 tcp endpoint")
        if self.event_type != "tcp_listen" and self.socket.remote is None:
            raise ValueError(f"{self.event_type} 必须包含 remote endpoint")
        return self


class IdentityAssertion(StrictModel):
    """一个来源在明确时间范围内提出的身份映射声明。"""

    schema_version: Literal["identity-assertion.v1"] = "identity-assertion.v1"
    assertion_id: str = Field(min_length=1, max_length=128)
    subject: str = Field(min_length=1, max_length=512)
    predicate: Literal[
        "maps_to", "listens_on", "runs_on", "resolves_to",
        "backend_candidate", "owned_by",
    ]
    object: str = Field(min_length=1, max_length=512)
    source: Literal[
        "orchestrator", "agent_discovery", "trace", "dns", "user", "model",
    ] = "agent_discovery"
    confidence: float = Field(ge=0.0, le=1.0)
    valid_from: datetime
    valid_to: Optional[datetime] = None
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_window(self):
        _require_aware(self.valid_from, "valid_from")
        if self.valid_to is not None:
            _require_aware(self.valid_to, "valid_to")
            if self.valid_to < self.valid_from:
                raise ValueError("valid_to 不能早于 valid_from")
        return self

    @classmethod
    def create(
        cls,
        *,
        subject: str,
        predicate: str,
        object: str,
        source: str,
        confidence: float,
        valid_from: datetime,
        valid_to: Optional[datetime] = None,
        evidence_refs: Optional[list[str]] = None,
    ) -> "IdentityAssertion":
        payload = {
            "subject": subject,
            "predicate": predicate,
            "object": object,
            "source": source,
            "valid_from": valid_from.isoformat(),
            "valid_to": valid_to.isoformat() if valid_to else None,
        }
        return cls(
            assertion_id=_stable_id("ia", payload),
            subject=subject,
            predicate=predicate,
            object=object,
            source=source,
            confidence=confidence,
            valid_from=valid_from,
            valid_to=valid_to,
            evidence_refs=evidence_refs or [],
        )


class ObservationWindow(StrictModel):
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def validate_window(self):
        _require_aware(self.start, "window.start")
        _require_aware(self.end, "window.end")
        if self.end < self.start:
            raise ValueError("window.end 不能早于 window.start")
        return self

    def contains(self, timestamp: datetime) -> bool:
        return self.start <= timestamp <= self.end


class DependencyMetrics(StrictModel):
    connections: int = Field(default=0, ge=0)
    active_connections: int = Field(default=0, ge=0)
    failures: int = Field(default=0, ge=0)
    resets: int = Field(default=0, ge=0)
    timeouts: int = Field(default=0, ge=0)
    bytes_sent: int = Field(default=0, ge=0)
    bytes_received: int = Field(default=0, ge=0)
    connect_p95_ms: Optional[float] = Field(default=None, ge=0.0)
    rtt_p95_ms: Optional[float] = Field(default=None, ge=0.0)
    retransmissions: int = Field(default=0, ge=0)

    def failure_rate(self) -> float:
        if self.connections <= 0:
            return 0.0
        return min(1.0, (self.failures + self.resets + self.timeouts) / self.connections)


class DependencyNode(StrictModel):
    entity_id: str = Field(min_length=1, max_length=512)
    entity_type: Literal[
        "service", "instance", "process", "host", "agent", "ip_endpoint",
        "virtual_endpoint", "external_unmanaged_endpoint", "managed_host_endpoint",
    ]
    display_name: str = Field(default="", max_length=512)
    agent_id: str = Field(default="", max_length=128)
    endpoint: Optional[NetworkEndpoint] = None
    process: Optional[ProcessIncarnation] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def for_endpoint(cls, endpoint: NetworkEndpoint) -> "DependencyNode":
        return cls(
            entity_id=endpoint.entity_id(),
            entity_type="ip_endpoint",
            display_name=endpoint.canonical(),
            endpoint=endpoint,
        )


class DependencyEdge(StrictModel):
    """时间窗内聚合的通信边，不包含 causal role。"""

    schema_version: Literal["dependency-edge.v1"] = "dependency-edge.v1"
    edge_id: str = Field(min_length=1, max_length=128)
    source_entity: str = Field(min_length=1, max_length=512)
    target_entity: str = Field(min_length=1, max_length=512)
    relation: Literal["calls", "connects_to", "publishes_to", "consumes_from"] = "connects_to"
    protocol: PROTOCOLS = "tcp"
    destination_port: int = Field(ge=0, le=65535)
    source_endpoint: Optional[NetworkEndpoint] = None
    target_endpoint: Optional[NetworkEndpoint] = None
    window: ObservationWindow
    metrics: DependencyMetrics = Field(default_factory=DependencyMetrics)
    identity_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    direction_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    observation_points: list[Literal["client", "server", "host", "snapshot"]] = Field(
        default_factory=list,
    )
    # ``evidence_refs`` must point at canonical Case Evidence rows when an edge
    # is materialized into a Case.  The collector's event IDs are still useful
    # for replay/debugging, but they are not addressable through the Evidence
    # API, so keep them in a separate lineage field instead of mixing the two
    # identifier namespaces.
    evidence_refs: list[str] = Field(default_factory=list, max_length=128)
    event_refs: list[str] = Field(default_factory=list, max_length=256)

    @model_validator(mode="after")
    def validate_edge(self):
        if self.source_entity == self.target_entity:
            raise ValueError("dependency edge 不能是自环")
        if self.target_endpoint is not None and self.destination_port != self.target_endpoint.port:
            raise ValueError("destination_port 必须与 target_endpoint.port 一致")
        return self

    @classmethod
    def create(
        cls,
        *,
        source_entity: str,
        target_entity: str,
        relation: str,
        protocol: str,
        destination_port: int,
        window: ObservationWindow,
        source_endpoint: Optional[NetworkEndpoint] = None,
        target_endpoint: Optional[NetworkEndpoint] = None,
        metrics: Optional[DependencyMetrics] = None,
        identity_confidence: float = 0.5,
        direction_confidence: float = 1.0,
        observation_points: Optional[list[str]] = None,
        evidence_refs: Optional[list[str]] = None,
        event_refs: Optional[list[str]] = None,
    ) -> "DependencyEdge":
        payload = {
            "source_entity": source_entity,
            "target_entity": target_entity,
            "relation": relation,
            "protocol": protocol,
            "destination_port": destination_port,
            "window": [window.start.isoformat(), window.end.isoformat()],
        }
        return cls(
            edge_id=_stable_id("dep", payload),
            source_entity=source_entity,
            target_entity=target_entity,
            relation=relation,
            protocol=protocol,
            destination_port=destination_port,
            source_endpoint=source_endpoint,
            target_endpoint=target_endpoint,
            window=window,
            metrics=metrics or DependencyMetrics(),
            identity_confidence=identity_confidence,
            direction_confidence=direction_confidence,
            observation_points=observation_points or [],
            evidence_refs=evidence_refs or [],
            event_refs=event_refs or [],
        )


class DependencyGraph(StrictModel):
    """可重放、可取 digest 的版本化依赖图。"""

    schema_version: Literal["dependency-graph.v1"] = "dependency-graph.v1"
    nodes: list[DependencyNode] = Field(default_factory=list, max_length=2000)
    edges: list[DependencyEdge] = Field(default_factory=list, max_length=10000)
    identity_assertions: list[IdentityAssertion] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def validate_and_normalize(self):
        self.nodes = self._unique(self.nodes, "entity_id")
        self.edges = self._unique(self.edges, "edge_id")
        self.identity_assertions = self._unique(self.identity_assertions, "assertion_id")
        node_ids = {node.entity_id for node in self.nodes}
        missing = sorted({
            ref
            for edge in self.edges
            for ref in (edge.source_entity, edge.target_entity)
            if ref not in node_ids
        })
        if missing:
            raise ValueError(f"dependency graph 缺少 edge 引用节点: {missing}")
        self.nodes.sort(key=lambda item: item.entity_id)
        self.edges.sort(key=lambda item: item.edge_id)
        self.identity_assertions.sort(key=lambda item: item.assertion_id)
        return self

    @staticmethod
    def _unique(items: list[Any], key_name: str) -> list[Any]:
        unique: dict[str, Any] = {}
        for item in items:
            key = str(getattr(item, key_name))
            previous = unique.get(key)
            if previous is not None and previous != item:
                raise ValueError(f"重复 {key_name} 的内容不一致: {key}")
            unique[key] = item
        return list(unique.values())

    def node_map(self) -> dict[str, DependencyNode]:
        return {node.entity_id: node for node in self.nodes}

    def adjacent_edges(self, entity_id: str) -> list[DependencyEdge]:
        return [
            edge for edge in self.edges
            if edge.source_entity == entity_id or edge.target_entity == entity_id
        ]

    def canonical_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def digest(self) -> str:
        payload = json.dumps(
            self.canonical_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
