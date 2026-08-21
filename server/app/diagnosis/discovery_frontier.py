"""未知拓扑 L4 发现的确定性解析与有界 frontier。

该模块是 Control 侧的纯逻辑层。它只处理 Agent 上传的低基数 snapshot，既不
创建 Task，也不尝试登录未注册主机；实际采集仍须由现有 scope/Fanout 流程完成。
输入故意兼容 ``network_discovery.v1`` 的几种常见字段命名，方便逐步接入现有
Agent，而无需一次性改动 collector/artifact 协议。
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any, Iterable, Literal, Mapping, Optional

from pydantic import Field, model_validator

from server.app.diagnosis.dependency_graph import (
    DependencyEdge,
    DependencyGraph,
    DependencyMetrics,
    DependencyNode,
    DiscoveryEvent,
    IdentityAssertion,
    NetworkEndpoint,
    ObservationWindow,
    ProcessIncarnation,
)
from server.app.diagnosis.schemas import StrictModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_datetime(value: Any, fallback: Optional[datetime] = None) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, (int, float)):
        # Collector timestamps are normally Unix seconds. A small integer is more
        # likely to be a monotonic/start tick; retain it as a UTC-ish instant only
        # for deterministic replay rather than failing the complete snapshot.
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            pass
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                return datetime.fromtimestamp(float(text), tz=timezone.utc)
            except (ValueError, OverflowError, OSError):
                pass
    return fallback or _utcnow()


def _first(item: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return default


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _endpoint(value: Any, *, port: Any = None, protocol: str = "tcp") -> Optional[NetworkEndpoint]:
    if isinstance(value, NetworkEndpoint):
        return value
    if isinstance(value, Mapping):
        address = _first(value, "address", "ip", "host", "hostname", "addr")
        endpoint_port = _first(value, "port", "endpoint_port", default=port)
        endpoint_protocol = str(_first(value, "protocol", "proto", default=protocol) or protocol).lower()
        if address is None or endpoint_port is None:
            return None
        try:
            return NetworkEndpoint(address=str(address), port=int(endpoint_port), protocol=endpoint_protocol)
        except (TypeError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        try:
            if ":" in value or "://" in value:
                return NetworkEndpoint.parse(value, protocol=protocol)
            if port is not None:
                return NetworkEndpoint(address=value.strip(), port=int(port), protocol=protocol)
        except (TypeError, ValueError):
            return None
    return None


def _process_from_record(
    record: Mapping[str, Any], *, agent_id: str, boot_id: str, fallback_pid: Any = None,
) -> Optional[ProcessIncarnation]:
    pid = _first(record, "pid", "process_id", "tgid", default=fallback_pid)
    if pid is None:
        return None
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        return None
    if pid_i <= 0:
        return None
    # A PID by itself is not a stable identity: it can be reused after a
    # process exits.  Older code silently substituted ``1`` when the collector
    # did not provide a start time, which made unrelated incarnations collapse
    # into one graph node.  Keep the record unresolved instead and let the
    # caller expose the resulting coverage limitation.
    start = _first(
        record, "process_start_time", "start_time", "start_time_ticks",
        "start_ticks", "starttime", default=None,
    )
    if start is None:
        return None
    # A process_start_time from procfs may be a decimal string or an ISO time.
    if isinstance(start, datetime):
        start = int(start.timestamp() * 1_000_000)
    else:
        try:
            start = int(float(start))
        except (TypeError, ValueError):
            try:
                start = int(_as_datetime(start).timestamp() * 1_000_000)
            except (TypeError, ValueError, OverflowError, OSError):
                return None
    if int(start) <= 0:
        return None
    return ProcessIncarnation(
        agent_id=agent_id,
        boot_id=boot_id or "unknown-boot",
        pid=pid_i,
        # ``start`` was checked for a strictly positive value above; retain it
        # verbatim so the identity can never be silently collapsed to tick 1.
        process_start_time=int(start),
        cgroup_id=str(_first(record, "cgroup_id", "cgroup", default="") or ""),
        netns=str(_first(record, "netns", "netns_inode", "network_namespace", default="") or ""),
        executable=str(_first(record, "executable", "exe", "comm", "command", default="") or "")[:1024],
    )


class ListenerRecord(StrictModel):
    agent_id: str = Field(min_length=1, max_length=128)
    endpoint: NetworkEndpoint
    observed_at: datetime
    process: Optional[ProcessIncarnation] = None
    service_id: str = ""
    instance_id: str = ""
    host_id: str = ""
    source: Literal["agent_listener", "orchestrator", "replay", "snapshot"] = "agent_listener"
    confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    # A snapshot's ``observed_at`` is the time we saw the socket, not the time
    # the listener started.  Explicit validity bounds, when supplied by an
    # orchestrator, are the only safe temporal constraints for historical
    # resolution.  With no bounds the graph builder may use a later snapshot
    # to resolve an earlier connection within the same discovery run.
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None

    @model_validator(mode="after")
    def validate_record(self):
        if self.observed_at.tzinfo is None:
            self.observed_at = self.observed_at.replace(tzinfo=timezone.utc)
        if self.valid_from is not None and self.valid_from.tzinfo is None:
            self.valid_from = self.valid_from.replace(tzinfo=timezone.utc)
        if self.valid_to is not None and self.valid_to.tzinfo is None:
            self.valid_to = self.valid_to.replace(tzinfo=timezone.utc)
        if self.valid_from is not None and self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("listener.valid_to 不能早于 valid_from")
        return self


class ConnectionRecord(StrictModel):
    agent_id: str = Field(min_length=1, max_length=128)
    observed_at: datetime
    local: NetworkEndpoint
    remote: NetworkEndpoint
    process: Optional[ProcessIncarnation] = None
    result: Literal["success", "failure", "reset", "timeout", "unknown"] = "unknown"
    observation_point: Literal["client", "server", "host", "snapshot"] = "client"
    direction_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    bytes_sent: int = Field(default=0, ge=0)
    bytes_received: int = Field(default=0, ge=0)
    connect_latency_ms: Optional[float] = Field(default=None, ge=0.0)
    retransmissions: int = Field(default=0, ge=0)
    event_id: str = ""
    # A collector may already know the canonical Case Evidence row that owns
    # this observation.  Keep it separate from ``event_id``: event IDs are
    # local lineage identifiers and must never be presented as Case Evidence
    # citations to the Agent.
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_record(self):
        if self.observed_at.tzinfo is None:
            self.observed_at = self.observed_at.replace(tzinfo=timezone.utc)
        if self.local.protocol != self.remote.protocol:
            raise ValueError("local/remote protocol 必须一致")
        return self


class AgentNetworkInventory(StrictModel):
    """一个 Agent 的 network_discovery.v1 归一化快照。"""

    schema_version: str = "network_discovery.v1"
    agent_id: str = Field(min_length=1, max_length=128)
    boot_id: str = "unknown-boot"
    observed_at: datetime = Field(default_factory=_utcnow)
    host_id: str = ""
    host_addresses: list[str] = Field(default_factory=list, max_length=128)
    online: bool = True
    processes: list[ProcessIncarnation] = Field(default_factory=list, max_length=10000)
    listeners: list[ListenerRecord] = Field(default_factory=list, max_length=10000)
    connections: list[ConnectionRecord] = Field(default_factory=list, max_length=50000)
    dropped_events: int = Field(default=0, ge=0)
    clock_quality: Literal["good", "unknown", "poor"] = "unknown"
    coverage_status: Literal["complete", "partial", "unknown"] = "unknown"
    coverage_reasons: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize(self):
        if self.observed_at.tzinfo is None:
            self.observed_at = self.observed_at.replace(tzinfo=timezone.utc)
        self.host_addresses = sorted({str(item) for item in self.host_addresses if item})
        return self

    @classmethod
    def from_payload(cls, payload: Any, *, agent_id: str = "") -> "AgentNetworkInventory":
        """兼容 collector 的扁平、嵌套和单 event 形态。"""
        if isinstance(payload, cls):
            return payload
        if isinstance(payload, DiscoveryEvent):
            event = payload
            process = event.process
            connections: list[ConnectionRecord] = []
            if event.socket.remote is not None:
                connections.append(ConnectionRecord(
                    agent_id=event.agent_id,
                    observed_at=event.observed_at,
                    local=event.socket.local,
                    remote=event.socket.remote,
                    process=process,
                    result=event.socket.result,
                    observation_point=event.socket.observation_point,
                    bytes_sent=event.socket.bytes_sent,
                    bytes_received=event.socket.bytes_received,
                    event_id=event.event_id,
                    evidence_refs=list(event.evidence_refs),
                ))
            listeners = []
            if event.event_type == "tcp_listen":
                listeners.append(ListenerRecord(
                    agent_id=event.agent_id, endpoint=event.socket.local,
                    observed_at=event.observed_at, process=process, source="snapshot",
                ))
            return cls(
                agent_id=event.agent_id, boot_id=event.boot_id, observed_at=event.observed_at,
                processes=[process] if process else [], listeners=listeners, connections=connections,
            )
        if not isinstance(payload, Mapping):
            raise ValueError("network discovery payload 必须是 object")
        agent_block = payload.get("agent") if isinstance(payload.get("agent"), Mapping) else {}
        raw_agent = str(
            _first(payload, "agent_id", "source_agent_id", default=None)
            or _first(agent_block, "agent_id", "id", default=agent_id)
            or ""
        )
        if not raw_agent:
            raise ValueError("network discovery payload 缺少 agent_id")
        boot_id = str(
            _first(payload, "boot_id", "host_boot_id", default=None)
            or _first(agent_block, "boot_id", default="unknown-boot")
            or "unknown-boot"
        )
        observed_at = _as_datetime(_first(payload, "observed_at", "scanned_at", "captured_at"))
        host_id = str(
            _first(payload, "host_id", "hostname", "host", default=None)
            or _first(agent_block, "hostname", "host_id", default="")
            or ""
        )
        addresses = _first(payload, "host_addresses", "addresses", "ips", "host_ips", default=[])
        if isinstance(addresses, str):
            addresses = [addresses]
        addresses = list(addresses or [])
        agent_ip = _first(agent_block, "ip_addr", "ip", "address", default=None)
        if agent_ip:
            addresses.append(str(agent_ip))
        processes: list[ProcessIncarnation] = []
        process_by_pid: dict[int, ProcessIncarnation] = {}
        process_by_identity: dict[str, ProcessIncarnation] = {}
        identity_failures = 0
        raw_processes = _as_list(_first(payload, "processes", "process_inventory", default=[]))
        for raw in raw_processes:
            if not isinstance(raw, Mapping):
                continue
            process = _process_from_record(raw, agent_id=raw_agent, boot_id=boot_id)
            if process is not None:
                process_by_pid[process.pid] = process
                identity = str(_first(raw, "process_identity", "identity", default="") or "")
                if identity:
                    process_by_identity[identity] = process
                processes.append(process)
            elif _first(raw, "pid", "process_id", "tgid", default=None) is not None:
                identity_failures += 1

        listeners: list[ListenerRecord] = []
        raw_listeners = _as_list(_first(payload, "listeners", "listening_sockets", "listening", default=[]))
        for raw in raw_listeners:
            if not isinstance(raw, Mapping):
                continue
            protocol = str(_first(raw, "protocol", "proto", default="tcp") or "tcp").lower()
            endpoint = _endpoint(
                _first(raw, "endpoint", "address", "ip", "host", "local", default=None),
                port=_first(raw, "port", "local_port", default=None), protocol=protocol,
            )
            if endpoint is None:
                continue
            pid = _first(raw, "pid", "process_id", "tgid", default=None)
            process = process_by_pid.get(int(pid)) if str(pid or "").isdigit() else None
            if process is None:
                process = process_by_identity.get(str(_first(raw, "process_identity", default="") or ""))
            if process is None:
                process = _process_from_record(raw, agent_id=raw_agent, boot_id=boot_id)
                if process is not None and process not in processes:
                    processes.append(process)
                    process_by_pid[process.pid] = process
                elif pid is not None or _first(raw, "process_identity", default=None) not in (None, ""):
                    identity_failures += 1
            source = str(_first(raw, "source", default="agent_listener") or "agent_listener")
            if source not in {"agent_listener", "orchestrator", "replay", "snapshot"}:
                source = "snapshot"
            listeners.append(ListenerRecord(
                agent_id=raw_agent, endpoint=endpoint,
                observed_at=_as_datetime(_first(raw, "observed_at", "seen_at"), observed_at),
                process=process,
                service_id=str(_first(raw, "service_id", "service", "name", default="") or ""),
                instance_id=str(_first(raw, "instance_id", "instance", "pod_uid", default="") or ""),
                host_id=host_id,
                source=source,
                confidence=float(_first(raw, "confidence", default=0.8) or 0.8),
                valid_from=(
                    _as_datetime(_first(raw, "valid_from", "started_at"), None)
                    if _first(raw, "valid_from", "started_at", default=None) is not None else None
                ),
                valid_to=(
                    _as_datetime(_first(raw, "valid_to", "ended_at"), None)
                    if _first(raw, "valid_to", "ended_at", default=None) is not None else None
                ),
            ))

        connections: list[ConnectionRecord] = []
        raw_connections = _as_list(_first(payload, "connections", "sockets", "flows", "events", default=[]))
        for index, raw in enumerate(raw_connections):
            if isinstance(raw, DiscoveryEvent):
                if raw.socket.remote is None:
                    continue
                connections.append(ConnectionRecord(
                    agent_id=raw.agent_id, observed_at=raw.observed_at,
                    local=raw.socket.local, remote=raw.socket.remote, process=raw.process,
                    result=raw.socket.result, observation_point=raw.socket.observation_point,
                    bytes_sent=raw.socket.bytes_sent, bytes_received=raw.socket.bytes_received,
                    event_id=raw.event_id,
                    evidence_refs=list(raw.evidence_refs),
                ))
                continue
            if not isinstance(raw, Mapping):
                continue
            nested_socket = raw.get("socket") if isinstance(raw.get("socket"), Mapping) else {}
            local_raw = _first(raw, "local", "local_endpoint", "local_address", default=nested_socket.get("local"))
            remote_raw = _first(raw, "remote", "remote_endpoint", "remote_address", "peer", default=nested_socket.get("remote"))
            protocol = str(_first(raw, "protocol", "proto", default=nested_socket.get("protocol", "tcp")) or "tcp").lower()
            local = _endpoint(local_raw, port=_first(raw, "local_port", "src_port", default=None), protocol=protocol)
            remote = _endpoint(remote_raw, port=_first(raw, "remote_port", "dst_port", "peer_port", default=None), protocol=protocol)
            if local is None or remote is None:
                continue
            pid = _first(raw, "pid", "process_id", "tgid", default=None)
            process = process_by_pid.get(int(pid)) if str(pid or "").isdigit() else None
            if process is None:
                process = process_by_identity.get(str(_first(raw, "process_identity", default="") or ""))
            if process is None and isinstance(raw.get("process"), Mapping):
                process = _process_from_record(raw["process"], agent_id=raw_agent, boot_id=boot_id)
                if process is None:
                    identity_failures += 1
            # A pid/process_identity without a matching incarnation is an
            # unresolved identity, even when the connection itself is valid.
            # Count it as partial coverage instead of silently downgrading the
            # owner to a host endpoint that looks authoritative.
            if process is None and (
                pid is not None
                or _first(raw, "process_identity", default=None) not in (None, "")
            ):
                identity_failures += 1
            result = str(_first(raw, "result", "status", default=nested_socket.get("result", "unknown")) or "unknown").lower()
            state = str(_first(raw, "state", default=nested_socket.get("state", "")) or "").upper()
            if result == "unknown" and state in {
                "ESTABLISHED", "SYN_SENT", "SYN_RECV", "FIN_WAIT1", "FIN_WAIT2",
                "CLOSE_WAIT", "LAST_ACK", "CLOSING", "NEW_SYN_RECV",
            }:
                result = "success"
            if result not in {"success", "failure", "reset", "timeout", "unknown"}:
                result = "unknown"
            point = str(
                _first(raw, "observation_point", "point", "direction", default=None)
                or _first(nested_socket, "observation_point", "direction", default="client")
                or "client"
            ).lower()
            point = {"outbound": "client", "connect": "client", "inbound": "server", "accept": "server"}.get(point, point)
            if point not in {"client", "server", "host", "snapshot"}:
                point = "host"
            connections.append(ConnectionRecord(
                agent_id=raw_agent,
                observed_at=_as_datetime(_first(raw, "observed_at", "seen_at", "timestamp"), observed_at),
                local=local, remote=remote, process=process, result=result,
                observation_point=point,
                direction_confidence=float(
                    _first(raw, "direction_confidence", default=None)
                    or _first(nested_socket, "direction_confidence", default=0.7)
                    or 0.7
                ),
                bytes_sent=int(_first(raw, "bytes_sent", "sent_bytes", default=0) or 0),
                bytes_received=int(_first(raw, "bytes_received", "recv_bytes", default=0) or 0),
                connect_latency_ms=(
                    float(_first(raw, "connect_latency_ms", "latency_ms", default=0))
                    if _first(raw, "connect_latency_ms", "latency_ms", default=None) is not None else None
                ),
                retransmissions=int(_first(raw, "retransmissions", "retransmit", default=0) or 0),
                event_id=str(_first(raw, "event_id", "id", default=f"{raw_agent}-{index}") or f"{raw_agent}-{index}"),
                evidence_refs=[
                    str(item) for item in _as_list(
                        _first(
                            raw, "evidence_refs", "evidence_ids",
                            default=_first(nested_socket, "evidence_refs", "evidence_ids", default=[]),
                        ),
                    ) if item
                ],
            ))
        # Remove duplicate process records deterministically.
        process_map = {item.stable_ref(): item for item in processes}
        clock_quality = str(_first(payload, "clock_quality", default="unknown") or "unknown")
        if clock_quality not in {"good", "unknown", "poor"}:
            clock_quality = "unknown"
        online_value = _first(payload, "online", default=True)
        online = online_value if isinstance(online_value, bool) else str(online_value).lower() not in {"false", "0", "offline", "no"}
        coverage_block = payload.get("coverage") if isinstance(payload.get("coverage"), Mapping) else {}
        coverage_status = str(_first(coverage_block, "status", default="unknown") or "unknown").lower()
        if coverage_status == "insufficient":
            # The Agent collector uses this spelling when it cannot read any
            # socket table.  Keep the shared contract to three states while
            # retaining the stronger reason in the coverage block.
            coverage_status = "partial"
        elif coverage_status not in {"complete", "partial", "unknown"}:
            coverage_status = "partial" if coverage_block.get("partial") else "unknown"
        coverage_reasons = [str(item) for item in _as_list(coverage_block.get("reasons")) if item]
        if str(_first(coverage_block, "status", default="") or "").lower() == "insufficient":
            coverage_reasons.append("collector_reported_insufficient")
        limitations = [str(item) for item in _as_list(payload.get("limitations")) if item]
        if identity_failures:
            if coverage_status == "complete":
                coverage_status = "partial"
            coverage_reasons.append("process_start_time_missing_or_invalid")
            limitations.append("process_identity_unresolved_without_start_time")
        return cls(
            schema_version=str(_first(payload, "schema_version", default="network_discovery.v1") or "network_discovery.v1"),
            agent_id=raw_agent, boot_id=boot_id, observed_at=observed_at, host_id=host_id,
            host_addresses=addresses, online=online,
            processes=list(process_map.values()), listeners=list(listeners), connections=list(connections),
            dropped_events=int(_first(payload, "dropped_events", "event_drops", default=0) or 0),
            clock_quality=clock_quality,
            coverage_status=coverage_status,
            coverage_reasons=list(dict.fromkeys(coverage_reasons)),
            limitations=list(dict.fromkeys(limitations)),
        )


def coverage_requires_abstention(
    status: Any,
    *,
    reasons: Iterable[Any] = (),
    has_observations: bool = True,
) -> bool:
    """Return whether a snapshot is too uncertain for a positive conclusion.

    ``complete`` is the only affirmative coverage state.  ``partial`` and
    ``unknown`` are deliberately fail-closed so a sparse snapshot cannot look
    like a complete dependency graph.  Membership-only placeholder inventories
    are the one exception: they carry no observations and are explicitly
    marked ``membership_only_no_observation`` by the resolver.
    """
    normalized = str(status or "unknown").strip().lower()
    if normalized == "complete":
        return False
    reason_values = {
        str(item or "").strip().lower()
        for item in (reasons or ())
    }
    if (
        normalized == "unknown"
        and not has_observations
        and any(item.startswith("membership_only") for item in reason_values)
    ):
        return False
    return True


class EndpointResolution(StrictModel):
    endpoint: NetworkEndpoint
    status: Literal[
        "managed", "external_unmanaged_endpoint", "virtual_endpoint", "unresolved",
    ]
    node: DependencyNode
    confidence: float = Field(ge=0.0, le=1.0)
    candidates: list[str] = Field(default_factory=list)
    assertions: list[IdentityAssertion] = Field(default_factory=list)
    reason: str = ""


class VirtualEndpointRecord(StrictModel):
    endpoint: NetworkEndpoint
    entity_id: str = ""
    display_name: str = ""
    service_id: str = ""
    backend_candidates: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None

    @model_validator(mode="after")
    def validate_record(self):
        if not self.entity_id:
            self.entity_id = f"virtual_endpoint:{self.endpoint.canonical()}"
        if self.valid_from and self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("virtual endpoint valid_to 不能早于 valid_from")
        return self

    @classmethod
    def from_payload(cls, payload: Any) -> "VirtualEndpointRecord":
        if isinstance(payload, cls):
            return payload
        if not isinstance(payload, Mapping):
            raise ValueError("virtual endpoint 必须是 object")
        raw = payload.get("endpoint") or payload.get("vip") or payload.get("address")
        endpoint = _endpoint(raw, port=payload.get("port"), protocol=str(payload.get("protocol", "tcp")))
        if endpoint is None:
            raise ValueError("virtual endpoint 缺少有效 endpoint")
        backends = payload.get("backend_candidates") or payload.get("backends") or payload.get("targets") or []
        normalized_backends: list[str] = []
        for item in _as_list(backends):
            if isinstance(item, Mapping):
                item = item.get("entity_id") or item.get("id") or item.get("endpoint")
            if item:
                normalized_backends.append(str(item))
        return cls(
            endpoint=endpoint,
            entity_id=str(payload.get("entity_id") or payload.get("virtual_id") or ""),
            display_name=str(payload.get("display_name") or payload.get("service_id") or ""),
            service_id=str(payload.get("service_id") or payload.get("service") or ""),
            backend_candidates=normalized_backends,
            confidence=float(payload.get("confidence", 0.6) or 0.6),
            valid_from=_as_datetime(payload.get("valid_from"), None) if payload.get("valid_from") else None,
            valid_to=_as_datetime(payload.get("valid_to"), None) if payload.get("valid_to") else None,
        )


class EndpointResolver:
    """把 IP:Port 映射到已注册 Agent 监听进程，并保留不确定性。"""

    def __init__(
        self,
        inventories: Iterable[AgentNetworkInventory | Mapping[str, Any]] | Mapping[str, Any] = (),
        *,
        virtual_endpoints: Iterable[VirtualEndpointRecord | Mapping[str, Any]] = (),
    ) -> None:
        if isinstance(inventories, Mapping):
            # Accept {agent_id: payload} and a single payload object.
            if "agent_id" in inventories or "schema_version" in inventories:
                raw_inventories = [inventories]
            else:
                raw_inventories = [dict(value, agent_id=str(key)) if isinstance(value, Mapping) else value for key, value in inventories.items()]
        else:
            raw_inventories = list(inventories)
        self.inventories = [
            item if isinstance(item, AgentNetworkInventory) else AgentNetworkInventory.from_payload(item)
            for item in raw_inventories
        ]
        self.virtual_endpoints = [
            item if isinstance(item, VirtualEndpointRecord) else VirtualEndpointRecord.from_payload(item)
            for item in virtual_endpoints
        ]
        self._inventory_by_agent = {item.agent_id: item for item in self.inventories}
        self._listeners = [listener for inventory in self.inventories for listener in inventory.listeners]
        self._listeners.sort(key=lambda item: (item.endpoint.canonical(), item.agent_id, item.process.stable_ref() if item.process else ""))

    def resolve(
        self,
        endpoint: NetworkEndpoint | str,
        observed_at: Optional[datetime] = None,
        *,
        allow_late_observation: bool = False,
    ) -> EndpointResolution:
        """Resolve an endpoint against the frozen inventory.

        ``observed_at`` belongs to the connection being resolved.  A listener
        record's timestamp normally says when that listener snapshot was
        collected, not when the service began listening.  The strict default
        keeps the old point-in-time semantics for callers that need it; the
        discovery graph builder opts into ``allow_late_observation`` after all
        snapshots in one run are available, which is what makes a serial
        cross-Agent collection (seed first, remote listener second) resolve
        correctly.
        """
        target = endpoint if isinstance(endpoint, NetworkEndpoint) else NetworkEndpoint.parse(endpoint)
        when = observed_at or _utcnow()
        virtual_matches = [
            record for record in self.virtual_endpoints
            if self._endpoint_matches(record.endpoint, target)
            and self._in_window(when, record.valid_from, record.valid_to)
        ]
        if virtual_matches:
            record = sorted(virtual_matches, key=lambda item: (item.entity_id, -item.confidence))[0]
            node = DependencyNode(
                entity_id=record.entity_id,
                entity_type="virtual_endpoint",
                display_name=record.display_name or record.service_id or target.canonical(),
                endpoint=target,
                confidence=record.confidence,
                attributes={"service_id": record.service_id, "backend_candidates": record.backend_candidates},
            )
            assertion = IdentityAssertion.create(
                subject=target.entity_id(), predicate="maps_to", object=node.entity_id,
                source="orchestrator", confidence=record.confidence,
                valid_from=record.valid_from or when,
                valid_to=record.valid_to,
            )
            return EndpointResolution(
                endpoint=target, status="virtual_endpoint", node=node,
                confidence=record.confidence, candidates=record.backend_candidates,
                assertions=[assertion], reason="virtual_or_load_balancer_endpoint",
            )

        matches = [
            listener for listener in self._listeners
            if self._listener_matches(listener, target)
            and self._listener_in_window(
                listener, when, allow_late_observation=allow_late_observation,
            )
        ]
        if matches:
            # A wildcard listener is weaker than an exact address. Multiple distinct
            # listeners mean VIP/LB ambiguity; expose a virtual node instead of lying.
            exact = [item for item in matches if item.endpoint.address == target.address]
            candidates = exact or matches
            distinct = sorted({
                item.process.stable_ref() if item.process else f"agent:{item.agent_id}"
                for item in candidates
            })
            if len(distinct) > 1:
                node = DependencyNode(
                    entity_id=f"virtual_endpoint:{target.canonical()}",
                    entity_type="virtual_endpoint", display_name=target.canonical(), endpoint=target,
                    confidence=min(item.confidence for item in candidates) * 0.8,
                    attributes={"candidate_entities": distinct, "reason": "multiple_registered_listeners"},
                )
                assertions = [IdentityAssertion.create(
                    subject=target.entity_id(), predicate="backend_candidate", object=candidate,
                    source="agent_discovery", confidence=node.confidence,
                valid_from=min(item.valid_from or item.observed_at for item in candidates),
                ) for candidate in distinct]
                return EndpointResolution(
                    endpoint=target, status="virtual_endpoint", node=node,
                    confidence=node.confidence, candidates=distinct, assertions=assertions,
                    reason="multiple_registered_listeners",
                )
            listener = sorted(candidates, key=lambda item: (-item.confidence, item.agent_id))[0]
            entity_id = listener.process.stable_ref() if listener.process else (
                f"agent:{listener.agent_id}:endpoint:{listener.endpoint.canonical()}"
            )
            entity_type = "process" if listener.process else "managed_host_endpoint"
            node = DependencyNode(
                entity_id=entity_id, entity_type=entity_type,
                display_name=listener.service_id or listener.instance_id or target.canonical(),
                agent_id=listener.agent_id, endpoint=target, process=listener.process,
                confidence=listener.confidence,
                attributes={"service_id": listener.service_id, "instance_id": listener.instance_id, "host_id": listener.host_id},
            )
            assertion = IdentityAssertion.create(
                subject=target.entity_id(), predicate="listens_on", object=entity_id,
                source="agent_discovery", confidence=listener.confidence,
                valid_from=listener.valid_from or listener.observed_at,
                valid_to=listener.valid_to,
            )
            return EndpointResolution(
                endpoint=target, status="managed", node=node,
                confidence=listener.confidence, candidates=[entity_id],
                assertions=[assertion], reason="registered_agent_listener",
            )

        # A registered host with no matching listener is still distinguishable from
        # an unmanaged external host. Keep a host endpoint node for honest coverage.
        host_matches = [inventory for inventory in self.inventories if target.address in inventory.host_addresses]
        if host_matches:
            inventory = sorted(host_matches, key=lambda item: item.agent_id)[0]
            entity_id = f"agent:{inventory.agent_id}:endpoint:{target.canonical()}"
            node = DependencyNode(
                entity_id=entity_id, entity_type="managed_host_endpoint",
                display_name=target.canonical(), agent_id=inventory.agent_id,
                endpoint=target, confidence=0.35,
                attributes={"host_id": inventory.host_id, "reason": "registered_host_without_listener"},
            )
            assertion = IdentityAssertion.create(
                subject=target.entity_id(), predicate="maps_to", object=entity_id,
                source="agent_discovery", confidence=0.35, valid_from=inventory.observed_at,
            )
            return EndpointResolution(
                endpoint=target, status="unresolved", node=node, confidence=0.35,
                candidates=[entity_id], assertions=[assertion], reason="registered_host_without_listener",
            )

        node = DependencyNode(
            entity_id=f"external_unmanaged_endpoint:{target.canonical()}",
            entity_type="external_unmanaged_endpoint", display_name=target.canonical(),
            endpoint=target, confidence=0.2,
        )
        assertion = IdentityAssertion.create(
            subject=target.entity_id(), predicate="maps_to", object=node.entity_id,
            source="agent_discovery", confidence=0.2, valid_from=when,
        )
        return EndpointResolution(
            endpoint=target, status="external_unmanaged_endpoint", node=node,
            confidence=0.2, candidates=[], assertions=[assertion], reason="no_registered_agent_match",
        )

    @staticmethod
    def _endpoint_matches(listener: NetworkEndpoint, target: NetworkEndpoint) -> bool:
        if listener.protocol != target.protocol or listener.port != target.port:
            return False
        return listener.address == target.address or listener.is_wildcard()

    def _listener_matches(self, listener: ListenerRecord, target: NetworkEndpoint) -> bool:
        if listener.endpoint.protocol != target.protocol or listener.endpoint.port != target.port:
            return False
        inventory = self._inventory_by_agent.get(listener.agent_id)
        if inventory is None or not inventory.online:
            return False
        if listener.endpoint.address == target.address:
            return True
        # 0.0.0.0/:: means all addresses *of this host*, not every address in the
        # investigation. Treating it globally would map arbitrary public endpoints
        # to an unrelated local listener that happens to use the same port.
        return listener.endpoint.is_wildcard() and target.address in inventory.host_addresses

    @staticmethod
    def _in_window(when: datetime, start: Optional[datetime], end: Optional[datetime]) -> bool:
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if start is not None and start.tzinfo is not None and when < start:
            return False
        if end is not None and end.tzinfo is not None and when > end:
            return False
        return True

    @classmethod
    def _listener_in_window(
        cls,
        listener: ListenerRecord,
        when: datetime,
        *,
        allow_late_observation: bool,
    ) -> bool:
        # Explicit validity bounds always win.  Without a lower bound, a late
        # snapshot is admissible only in the aggregate/replay mode described
        # above; a strict resolver still treats it as future evidence.
        if not cls._in_window(when, listener.valid_from, listener.valid_to):
            return False
        if listener.valid_from is not None or allow_late_observation:
            return True
        return cls._in_window(when, listener.observed_at, None)


class FrontierBudget(StrictModel):
    max_hops: int = Field(default=2, ge=0, le=16)
    max_hosts: int = Field(default=12, ge=1, le=10000)
    max_processes: int = Field(default=40, ge=1, le=100000)
    max_edges: int = Field(default=200, ge=1, le=100000)
    max_parallel_tasks: int = Field(default=8, ge=1, le=10000)
    max_endpoint_candidates: int = Field(default=3, ge=1, le=100)


class FrontierTarget(StrictModel):
    entity_id: str
    agent_id: str = ""
    hop: int = Field(ge=0)
    reason: str = "managed_peer"
    confidence: float = Field(ge=0.0, le=1.0)
    endpoint: Optional[NetworkEndpoint] = None
    collectable: bool = False


class DiscoveryFrontierRun(StrictModel):
    schema_version: Literal["discovery-frontier.v1"] = "discovery-frontier.v1"
    run_id: str
    seed_entity: str
    budget: FrontierBudget
    targets: list[FrontierTarget] = Field(default_factory=list)
    visited_entities: list[str] = Field(default_factory=list)
    edge_ids: list[str] = Field(default_factory=list)
    stopped_reasons: list[str] = Field(default_factory=list)
    unresolved_endpoints: list[str] = Field(default_factory=list)
    external_endpoints: list[str] = Field(default_factory=list)
    virtual_endpoints: list[str] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)
    complete: bool = False


class DiscoveryGraphBuildResult(StrictModel):
    """Case/API 可直接保存的 discovery snapshot 纯逻辑产物。"""

    schema_version: Literal["unknown-topology-discovery.v1"] = "unknown-topology-discovery.v1"
    membership_snapshot_id: str = ""
    discovery_run_id: str
    seed_ref: str
    graph: DependencyGraph
    graph_digest: str
    coverage: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    unresolved_endpoints: list[str] = Field(default_factory=list)
    managed_frontier_targets: list[FrontierTarget] = Field(default_factory=list)
    frontier: DiscoveryFrontierRun


class DiscoveryFrontierEngine:
    """在已构建依赖图上执行有界 BFS；不创建采集 Task。"""

    def __init__(self, resolver: EndpointResolver):
        self.resolver = resolver

    def run(
        self,
        *,
        run_id: str,
        seed_entity: str,
        graph: DependencyGraph,
        budget: FrontierBudget | None = None,
        time_aligned: bool = True,
    ) -> DiscoveryFrontierRun:
        limit = budget or FrontierBudget()
        result = DiscoveryFrontierRun(run_id=run_id, seed_entity=seed_entity, budget=limit)
        if seed_entity not in graph.node_map():
            result.stopped_reasons.append("SEED_NOT_IN_GRAPH")
            result.coverage = {"conclusion": "insufficient_coverage", "reason": "seed_missing"}
            return result
        queue: deque[tuple[str, int]] = deque([(seed_entity, 0)])
        visited: set[str] = set()
        target_ids: set[str] = set()
        seed_node = graph.node_map()[seed_entity]
        scheduled_processes: set[str] = {
            seed_entity
        } if seed_node.entity_type in {"process", "instance"} else set()
        scheduled_hosts: set[str] = {seed_node.agent_id} if seed_node.agent_id else set()

        def stop(reason: str) -> None:
            if reason not in result.stopped_reasons:
                result.stopped_reasons.append(reason)

        def reserve(peer: str, peer_node: DependencyNode) -> bool:
            process_like = peer_node.entity_type in {"process", "instance"}
            if process_like and peer not in scheduled_processes:
                if len(scheduled_processes) >= limit.max_processes:
                    stop("MAX_PROCESSES")
                    return False
            host_key = peer_node.agent_id
            if host_key and host_key not in scheduled_hosts:
                if len(scheduled_hosts) >= limit.max_hosts:
                    stop("MAX_HOSTS")
                    return False
            if process_like:
                scheduled_processes.add(peer)
            if host_key:
                scheduled_hosts.add(host_key)
            return True

        while queue:
            entity, hop = queue.popleft()
            if entity in visited:
                continue
            visited.add(entity)
            if hop >= limit.max_hops:
                continue
            for edge in graph.adjacent_edges(entity):
                if edge.edge_id not in result.edge_ids and len(result.edge_ids) >= limit.max_edges:
                    stop("MAX_EDGES")
                    queue.clear()
                    break
                if edge.edge_id not in result.edge_ids:
                    result.edge_ids.append(edge.edge_id)
                peer = edge.target_entity if edge.source_entity == entity else edge.source_entity
                peer_node = graph.node_map().get(peer)
                if peer_node is None:
                    continue
                if peer_node.entity_type == "external_unmanaged_endpoint":
                    if peer not in result.external_endpoints:
                        result.external_endpoints.append(peer)
                    continue
                if peer_node.entity_type == "virtual_endpoint":
                    if peer not in result.virtual_endpoints:
                        result.virtual_endpoints.append(peer)
                next_hop = hop + 1
                if peer not in visited and reserve(peer, peer_node):
                    queue.append((peer, next_hop))
                elif peer not in visited:
                    continue
                if peer_node.agent_id and peer not in target_ids and peer != seed_entity:
                    if len([item for item in result.targets if item.collectable]) >= limit.max_parallel_tasks:
                        stop("MAX_PARALLEL_TASKS")
                    else:
                        target_ids.add(peer)
                        result.targets.append(FrontierTarget(
                            entity_id=peer, agent_id=peer_node.agent_id, hop=next_hop,
                            reason="managed_peer", confidence=peer_node.confidence,
                            endpoint=peer_node.endpoint, collectable=peer_node.entity_type in {
                                "process", "instance", "service", "managed_host_endpoint",
                            },
                        ))
        result.visited_entities = sorted(visited)
        result.unresolved_endpoints = sorted({
            node.entity_id for node in graph.nodes
            if node.entity_type == "managed_host_endpoint"
            and node.entity_id not in result.external_endpoints
        })
        # A graph may contain external/virtual peers by design; that is not a failure.
        # Coverage is insufficient when time alignment is false or when managed targets
        # are capped before all managed peers can be visited.
        managed_nodes = self._reachable_managed_nodes(graph, seed_entity, limit.max_hops)
        discovered_managed = {item.entity_id for item in result.targets if item.collectable}
        coverage = len(discovered_managed) / max(1, len(managed_nodes))
        result.coverage = {
            "managed_candidates": len(managed_nodes),
            "managed_discovered": len(discovered_managed),
            "managed_fraction": coverage,
            "external_count": len(result.external_endpoints),
            "virtual_count": len(result.virtual_endpoints),
            "time_aligned": time_aligned,
            "conclusion": "dependency" if time_aligned and coverage >= 0.6 and not result.stopped_reasons else "insufficient_coverage",
        }
        result.complete = not result.stopped_reasons
        return result

    @staticmethod
    def _reachable_managed_nodes(
        graph: DependencyGraph, seed_entity: str, max_hops: int,
    ) -> set[str]:
        nodes = graph.node_map()
        queue: deque[tuple[str, int]] = deque([(seed_entity, 0)])
        seen: set[str] = set()
        managed: set[str] = set()
        while queue:
            entity, hop = queue.popleft()
            if entity in seen:
                continue
            seen.add(entity)
            if hop >= max_hops:
                continue
            for edge in graph.adjacent_edges(entity):
                peer = edge.target_entity if edge.source_entity == entity else edge.source_entity
                if peer == seed_entity:
                    continue
                node = nodes.get(peer)
                if node is None:
                    continue
                if node.entity_type in {"process", "instance", "service", "managed_host_endpoint"}:
                    managed.add(peer)
                queue.append((peer, hop + 1))
        return managed


def _normalize_inventories(
    snapshots: Any,
    agent_inventories: Any = None,
) -> list[AgentNetworkInventory]:
    values: list[Any] = []
    for raw in (snapshots, agent_inventories):
        if raw is None:
            continue
        if isinstance(raw, Mapping):
            if "agent_id" in raw or "schema_version" in raw or "agent" in raw:
                values.append(raw)
            else:
                values.extend(
                    dict(value, agent_id=str(key)) if isinstance(value, Mapping) else value
                    for key, value in raw.items()
                )
        else:
            values.extend(_as_list(raw))
    inventories: list[AgentNetworkInventory] = []
    for item in values:
        if isinstance(item, AgentNetworkInventory):
            inventories.append(item)
        elif isinstance(item, DiscoveryEvent):
            inventories.append(AgentNetworkInventory.from_payload(item))
        elif isinstance(item, Mapping):
            # A list of event payloads is also accepted; grouping is intentionally
            # deterministic by (agent, boot), preserving snapshot semantics.
            if str(item.get("event_type", "")).startswith("tcp_") and item.get("socket"):
                try:
                    inventories.append(AgentNetworkInventory.from_payload(item))
                except ValueError:
                    continue
            else:
                try:
                    inventories.append(AgentNetworkInventory.from_payload(item))
                except ValueError:
                    continue
    return inventories


def _process_node(process: ProcessIncarnation, *, confidence: float = 0.9) -> DependencyNode:
    return DependencyNode(
        entity_id=process.stable_ref(), entity_type="process", display_name=process.executable,
        agent_id=process.agent_id, process=process, confidence=confidence,
    )


def build_dependency_graph(
    snapshots: Any,
    agent_inventories: Any = None,
    *,
    virtual_endpoints: Iterable[VirtualEndpointRecord | Mapping[str, Any]] = (),
    max_edges: int = 200,
    max_processes: int = 40,
    window: Optional[ObservationWindow] = None,
    evidence_ref: str | None = None,
) -> DependencyGraph:
    """从 network_discovery snapshot/event replay 构建确定性依赖图。

    ``snapshots`` 与 ``agent_inventories`` 二选一；保留两个参数是为了兼容调用方
    可能把原始事件和已归一化 inventory 分开传递的形态。输入过预算时按
    ``agent_id, pid, start_time`` 和 event 时间排序截断，而不是依赖上传顺序。
    """
    inventories = _normalize_inventories(snapshots, agent_inventories)
    # Merge same agent/boot snapshots (common when an Agent uploads short windows).
    grouped: dict[tuple[str, str], AgentNetworkInventory] = {}
    for inventory in inventories:
        key = (inventory.agent_id, inventory.boot_id)
        existing = grouped.get(key)
        if existing is None:
            grouped[key] = inventory
            continue
        process_map = {item.stable_ref(): item for item in existing.processes}
        process_map.update({item.stable_ref(): item for item in inventory.processes})
        listener_map = {
            (item.agent_id, item.endpoint.canonical(), item.process.stable_ref() if item.process else ""): item
            for item in existing.listeners
        }
        for item in inventory.listeners:
            listener_key = (
                item.agent_id,
                item.endpoint.canonical(),
                item.process.stable_ref() if item.process else "",
            )
            previous = listener_map.get(listener_key)
            if previous is None:
                listener_map[listener_key] = item
                continue
            # Repeated snapshots describe one continuing listener incarnation.
            # Keep its earliest observation so connections captured before a
            # later reconciliation pass still resolve to the owning process.
            preferred = item if item.confidence >= previous.confidence else previous
            listener_map[listener_key] = preferred.model_copy(update={
                "observed_at": min(previous.observed_at, item.observed_at),
                "confidence": max(previous.confidence, item.confidence),
                "service_id": preferred.service_id or previous.service_id or item.service_id,
                "instance_id": preferred.instance_id or previous.instance_id or item.instance_id,
                "host_id": preferred.host_id or previous.host_id or item.host_id,
            })
        event_map = {
            item.event_id or f"{item.local.canonical()}->{item.remote.canonical()}:{item.observed_at.isoformat()}": item
            for item in existing.connections
        }
        event_map.update({
            item.event_id or f"{item.local.canonical()}->{item.remote.canonical()}:{item.observed_at.isoformat()}": item
            for item in inventory.connections
        })
        existing.processes = list(process_map.values())
        existing.listeners = list(listener_map.values())
        existing.connections = list(event_map.values())
        existing.host_addresses = sorted(set(existing.host_addresses) | set(inventory.host_addresses))
        existing.dropped_events += inventory.dropped_events
        if inventory.observed_at > existing.observed_at:
            existing.observed_at = inventory.observed_at
    inventories = sorted(grouped.values(), key=lambda item: (item.agent_id, item.boot_id))
    resolver = EndpointResolver(inventories, virtual_endpoints=virtual_endpoints)
    nodes: dict[str, DependencyNode] = {}
    assertions: dict[str, IdentityAssertion] = {}
    process_records = sorted(
        [process for inventory in inventories for process in inventory.processes],
        key=lambda item: (item.agent_id, item.boot_id, item.pid, item.process_start_time),
    )
    allowed_processes = {item.stable_ref() for item in process_records[:max(1, max_processes)]}
    for process in process_records[:max(1, max_processes)]:
        nodes[process.stable_ref()] = _process_node(process)
    # Include listeners' processes even if process inventory omitted them, subject to cap.
    listener_records = sorted(resolver._listeners, key=lambda item: (item.agent_id, item.endpoint.canonical(), item.process.stable_ref() if item.process else ""))
    for listener in listener_records:
        if listener.process is not None and listener.process.stable_ref() not in nodes and len(nodes) < max(1, max_processes):
            nodes[listener.process.stable_ref()] = _process_node(listener.process, confidence=listener.confidence)
            allowed_processes.add(listener.process.stable_ref())
        if listener.process is not None and listener.process.stable_ref() in allowed_processes:
            listener_entity = listener.process.stable_ref()
            listener_node = nodes[listener_entity]
        else:
            listener_entity = f"managed_host_endpoint:{listener.agent_id}:{listener.endpoint.canonical()}"
            listener_node = nodes.setdefault(listener_entity, DependencyNode(
                entity_id=listener_entity, entity_type="managed_host_endpoint",
                display_name=listener.service_id or listener.endpoint.canonical(),
                agent_id=listener.agent_id, endpoint=listener.endpoint,
                confidence=min(listener.confidence, 0.55),
                attributes={"host_id": listener.host_id, "process_budget_limited": listener.process is not None},
            ))
        inventory = resolver._inventory_by_agent.get(listener.agent_id)
        concrete_endpoints = [listener.endpoint]
        if listener.endpoint.is_wildcard() and inventory is not None:
            concrete_endpoints = [
                NetworkEndpoint(
                    address=address, port=listener.endpoint.port,
                    protocol=listener.endpoint.protocol,
                )
                for address in inventory.host_addresses
            ] or [listener.endpoint]
        listening = set(listener_node.attributes.get("listening_endpoints") or [])
        listening.update(item.canonical() for item in concrete_endpoints)
        listener_node.attributes = {
            **listener_node.attributes,
            "listening_endpoints": sorted(listening),
            "service_id": listener.service_id,
            "instance_id": listener.instance_id,
            "host_id": listener.host_id,
        }
        for concrete in concrete_endpoints:
            assertion = IdentityAssertion.create(
                subject=concrete.entity_id(), predicate="listens_on", object=listener_entity,
                source="agent_discovery", confidence=listener.confidence,
                valid_from=listener.observed_at,
            )
            assertions[assertion.assertion_id] = assertion
    all_connections = sorted(
        [connection for inventory in inventories for connection in inventory.connections],
        key=lambda item: (item.observed_at, item.agent_id, item.process.stable_ref() if item.process else "", item.local.canonical(), item.remote.canonical(), item.event_id),
    )
    outbound_by_tuple: dict[tuple[str, str], list[ConnectionRecord]] = {}
    for item in all_connections:
        if item.observation_point == "client":
            outbound_by_tuple.setdefault(
                (item.local.canonical(), item.remote.canonical()), [],
            ).append(item)
    edge_accumulators: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    for connection in all_connections:
        if window is not None and not window.contains(connection.observed_at):
            continue
        owner_node: Optional[DependencyNode] = None
        if connection.process is not None and connection.process.stable_ref() in allowed_processes:
            owner_node = nodes.get(connection.process.stable_ref()) or _process_node(connection.process, confidence=0.7)
            nodes[owner_node.entity_id] = owner_node
        if owner_node is None:
            owner_node = DependencyNode(
                entity_id=f"managed_host_endpoint:{connection.agent_id}:{connection.local.canonical()}",
                entity_type="managed_host_endpoint", display_name=connection.local.canonical(),
                agent_id=connection.agent_id, endpoint=connection.local, confidence=0.45,
            )
            nodes[owner_node.entity_id] = owner_node

        inbound = connection.observation_point == "server"
        if connection.observation_point in {"host", "snapshot"}:
            inbound = any(
                listener.agent_id == connection.agent_id
                and resolver._listener_matches(listener, connection.local)
                for listener in listener_records
            )
        duplicate_observation = False
        identity_confidence = owner_node.confidence
        if inbound:
            matching_outbound = outbound_by_tuple.get(
                (connection.remote.canonical(), connection.local.canonical()), [],
            )
            matched = min(
                matching_outbound,
                key=lambda item: abs((item.observed_at - connection.observed_at).total_seconds()),
            ) if matching_outbound else None
            if matched is not None and matched.process is not None and matched.process.stable_ref() in allowed_processes:
                source_node = nodes.get(matched.process.stable_ref()) or _process_node(matched.process, confidence=0.7)
                nodes[source_node.entity_id] = source_node
                duplicate_observation = True
                identity_confidence = min(source_node.confidence, owner_node.confidence)
            else:
                resolution = resolver.resolve(
                    connection.remote, connection.observed_at,
                    allow_late_observation=True,
                )
                previous = nodes.get(resolution.node.entity_id)
                if previous is not None:
                    resolution.node.attributes = {**previous.attributes, **resolution.node.attributes}
                nodes[resolution.node.entity_id] = resolution.node
                for assertion in resolution.assertions:
                    assertions[assertion.assertion_id] = assertion
                source_node = resolution.node
                identity_confidence = min(resolution.confidence, owner_node.confidence)
            target_node = owner_node
            source_endpoint = connection.remote
            target_endpoint = connection.local
            destination_port = connection.local.port
        else:
            source_node = owner_node
            resolution = resolver.resolve(
                connection.remote, connection.observed_at,
                allow_late_observation=True,
            )
            previous = nodes.get(resolution.node.entity_id)
            if previous is not None:
                resolution.node.attributes = {**previous.attributes, **resolution.node.attributes}
            nodes[resolution.node.entity_id] = resolution.node
            for assertion in resolution.assertions:
                assertions[assertion.assertion_id] = assertion
            target_node = resolution.node
            source_endpoint = connection.local
            target_endpoint = connection.remote
            destination_port = connection.remote.port
            identity_confidence = min(resolution.confidence, owner_node.confidence)

        key = (source_node.entity_id, target_node.entity_id, connection.local.protocol, destination_port)
        if key not in edge_accumulators and len(edge_accumulators) >= max(1, max_edges):
            continue
        accumulator = edge_accumulators.setdefault(key, {
            "source_entity": source_node.entity_id,
            "target_entity": target_node.entity_id,
            "relation": "calls" if connection.observation_point in {"client", "server"} else "connects_to",
            "protocol": connection.local.protocol,
            "destination_port": destination_port,
            "source_endpoint": source_endpoint,
            "target_endpoint": target_endpoint,
            "first_seen": connection.observed_at,
            "last_seen": connection.observed_at,
            "connections": 0, "active_connections": 0, "failures": 0, "resets": 0,
            "timeouts": 0, "bytes_sent": 0, "bytes_received": 0,
            "latencies": [], "retransmissions": 0,
            "identity_confidence": identity_confidence,
            "direction_confidence": connection.direction_confidence,
            "observation_points": set(), "evidence_refs": set(), "event_refs": set(),
        })
        accumulator["first_seen"] = min(accumulator["first_seen"], connection.observed_at)
        accumulator["last_seen"] = max(accumulator["last_seen"], connection.observed_at)
        if not duplicate_observation:
            accumulator["connections"] += 1
            accumulator["active_connections"] += 1 if connection.result == "success" else 0
            if connection.result == "failure":
                accumulator["failures"] += 1
            elif connection.result == "reset":
                accumulator["resets"] += 1
            elif connection.result == "timeout":
                accumulator["timeouts"] += 1
            accumulator["bytes_sent"] += connection.bytes_sent
            accumulator["bytes_received"] += connection.bytes_received
            if connection.connect_latency_ms is not None:
                accumulator["latencies"].append(connection.connect_latency_ms)
            accumulator["retransmissions"] += connection.retransmissions
        accumulator["identity_confidence"] = min(accumulator["identity_confidence"], identity_confidence)
        accumulator["direction_confidence"] = min(
            accumulator["direction_confidence"], connection.direction_confidence,
        )
        accumulator["observation_points"].add(connection.observation_point)
        if connection.event_id:
            accumulator["event_refs"].add(connection.event_id)
        # ``evidence_ref`` is supplied by CaseEvidenceService when the
        # projection is materialized.  Replay callers can also attach refs to
        # individual events.  Never use an event ID as an Evidence citation:
        # the two namespaces have different ownership/lifecycle semantics.
        if evidence_ref:
            accumulator["evidence_refs"].add(str(evidence_ref))
        accumulator["evidence_refs"].update(
            str(item) for item in connection.evidence_refs if item
        )
    if window is None:
        timestamps = [connection.observed_at for connection in all_connections]
        window = ObservationWindow(
            start=min(timestamps) if timestamps else _utcnow(),
            end=max(timestamps) if timestamps else _utcnow(),
        )
    edges: list[DependencyEdge] = []
    for item in sorted(edge_accumulators.values(), key=lambda value: (value["source_entity"], value["target_entity"], value["destination_port"], value["protocol"])):
        latencies = sorted(item["latencies"])
        p95 = latencies[min(len(latencies) - 1, max(0, int(round((len(latencies) - 1) * 0.95))))] if latencies else None
        edge = DependencyEdge.create(
            source_entity=item["source_entity"], target_entity=item["target_entity"],
            relation=item["relation"], protocol=item["protocol"], destination_port=item["destination_port"],
            source_endpoint=item["source_endpoint"], target_endpoint=item["target_endpoint"],
            window=ObservationWindow(start=item["first_seen"], end=max(item["last_seen"], item["first_seen"])),
            metrics=DependencyMetrics(
                connections=item["connections"], active_connections=item["active_connections"],
                failures=item["failures"], resets=item["resets"], timeouts=item["timeouts"],
                bytes_sent=item["bytes_sent"], bytes_received=item["bytes_received"],
                connect_p95_ms=p95, retransmissions=item["retransmissions"],
            ),
            identity_confidence=item["identity_confidence"],
            direction_confidence=item["direction_confidence"],
            observation_points=sorted(item["observation_points"]),
            evidence_refs=sorted(item["evidence_refs"]),
            event_refs=sorted(item["event_refs"]),
        )
        edges.append(edge)
    return DependencyGraph(nodes=list(nodes.values()), edges=edges, identity_assertions=list(assertions.values()))


def _resolve_seed_ref(graph: DependencyGraph, seed_ref: Any) -> tuple[str, list[str]]:
    limitations: list[str] = []
    nodes = graph.node_map()
    if isinstance(seed_ref, Mapping):
        agent_id = str(_first(seed_ref, "agent_id", "agent", default="") or "")
        pid = _first(seed_ref, "pid", "process_id", default=None)
        boot_id = str(_first(seed_ref, "boot_id", default="") or "")
        start = _first(seed_ref, "process_start_time", "start_time", "start_time_ticks", default=None)
        candidates = [
            node for node in graph.nodes
            if node.process is not None
            and (not agent_id or node.process.agent_id == agent_id)
            and (pid is None or node.process.pid == int(pid))
            and (not boot_id or node.process.boot_id == boot_id)
            and (start is None or node.process.process_start_time == int(start))
        ]
        if len(candidates) == 1:
            return candidates[0].entity_id, limitations
        if len(candidates) > 1:
            limitations.append("seed_identity_ambiguous_requires_boot_id_and_start_time")
            return sorted(item.entity_id for item in candidates)[0], limitations
        return "", ["seed_not_found"]
    value = str(seed_ref or "")
    if value in nodes:
        return value, limitations
    if value:
        # Compatibility aliases: agent:pid, pid:<n>, or a bare pid. Ambiguous
        # matches are surfaced rather than silently claiming stable identity.
        agent_id = ""
        pid_text = value
        if value.startswith("pid:"):
            pid_text = value.split(":", 1)[1]
        elif value.count(":") == 1:
            agent_id, pid_text = value.split(":", 1)
        if pid_text.isdigit():
            candidates = [
                node for node in graph.nodes
                if node.process is not None and node.process.pid == int(pid_text)
                and (not agent_id or node.process.agent_id == agent_id)
            ]
            if candidates:
                if len(candidates) > 1:
                    limitations.append("seed_identity_ambiguous_requires_boot_id_and_start_time")
                return sorted(item.entity_id for item in candidates)[0], limitations
        return "", ["seed_not_found"]
    process_nodes = sorted(
        (node.entity_id for node in graph.nodes if node.entity_type == "process"),
    )
    if process_nodes:
        limitations.append("seed_defaulted_to_first_process")
        return process_nodes[0], limitations
    return "", ["seed_not_found"]


def build_discovery_snapshot_graph(
    snapshots: Any,
    agent_inventories: Any = None,
    *,
    seed_ref: Any,
    membership_snapshot_id: str = "",
    discovery_run_id: str = "discovery-replay",
    virtual_endpoints: Iterable[VirtualEndpointRecord | Mapping[str, Any]] = (),
    budget: Optional[FrontierBudget] = None,
    time_aligned: Optional[bool] = None,
) -> DiscoveryGraphBuildResult:
    """构建图、计算 digest，并产出可交给现有 Fanout 的 managed targets。

    这是 snapshot→graph 的最小公开闭环。``managed_frontier_targets`` 只是一份
    冻结候选，调用方仍需经过 Membership/TargetResolver、授权和 Fanout 才能采集。
    """
    inventories = _normalize_inventories(snapshots, agent_inventories)
    limit = budget or FrontierBudget()
    graph = build_dependency_graph(
        inventories, virtual_endpoints=virtual_endpoints,
        max_edges=limit.max_edges, max_processes=limit.max_processes,
    )
    resolved_seed, limitations = _resolve_seed_ref(graph, seed_ref)
    aligned = time_aligned if time_aligned is not None else all(
        inventory.clock_quality != "poor" for inventory in inventories
    )
    resolver = EndpointResolver(inventories, virtual_endpoints=virtual_endpoints)
    frontier = DiscoveryFrontierEngine(resolver).run(
        run_id=discovery_run_id,
        seed_entity=resolved_seed or str(seed_ref or ""),
        graph=graph,
        budget=limit,
        time_aligned=aligned,
    )
    external = sorted(
        node.entity_id for node in graph.nodes
        if node.entity_type == "external_unmanaged_endpoint"
    )
    virtual = sorted(
        node.entity_id for node in graph.nodes
        if node.entity_type == "virtual_endpoint"
    )
    unresolved = sorted(
        node.entity_id for node in graph.nodes
        if node.entity_type == "managed_host_endpoint"
    )
    if external:
        limitations.append("external_unmanaged_endpoints_not_collectable")
    if virtual:
        limitations.append("virtual_endpoints_require_orchestrator_or_trace_resolution")
    if unresolved:
        limitations.append("registered_hosts_without_listener_identity")
    if any(inventory.dropped_events > 0 for inventory in inventories):
        limitations.append("agent_reported_dropped_discovery_events")
    if any(inventory.coverage_status == "partial" for inventory in inventories):
        limitations.append("agent_snapshot_coverage_partial")
    if any(inventory.coverage_status == "unknown" for inventory in inventories):
        limitations.append("agent_snapshot_coverage_unknown")
    for inventory in inventories:
        limitations.extend(inventory.coverage_reasons)
        limitations.extend(inventory.limitations)
    if not aligned:
        limitations.append("agent_time_windows_not_aligned")
    if any(inventory.clock_quality == "unknown" for inventory in inventories):
        limitations.append("clock_quality_unknown")
    if len(graph.edges) >= limit.max_edges:
        limitations.append("edge_budget_reached")
    process_nodes = sum(1 for node in graph.nodes if node.entity_type == "process")
    if process_nodes >= limit.max_processes:
        limitations.append("process_budget_reached")
    limitations.extend(frontier.stopped_reasons)
    coverage = dict(frontier.coverage)
    coverage.update({
        "registered_agent_snapshots": len(inventories),
        "online_agent_snapshots": sum(1 for item in inventories if item.online),
        "agents_with_connections": sum(1 for item in inventories if item.connections),
        "unresolved_endpoint_count": len(unresolved),
        "dropped_events": sum(item.dropped_events for item in inventories),
        "partial_agent_snapshots": sum(
            1 for item in inventories if item.coverage_status == "partial"
        ),
        "unknown_agent_snapshots": sum(
            1 for item in inventories if item.coverage_status == "unknown"
        ),
    })
    coverage_blocked = (
        not resolved_seed
        or not aligned
        or any(inventory.dropped_events > 0 for inventory in inventories)
        or any(
            coverage_requires_abstention(
                inventory.coverage_status,
                reasons=inventory.coverage_reasons,
                has_observations=bool(
                    inventory.processes or inventory.listeners or inventory.connections
                ),
            )
            for inventory in inventories
        )
        or bool(frontier.stopped_reasons)
        or bool(external or virtual or unresolved)
    )
    if coverage_blocked:
        coverage["conclusion"] = "insufficient_coverage"
    return DiscoveryGraphBuildResult(
        membership_snapshot_id=membership_snapshot_id,
        discovery_run_id=discovery_run_id,
        seed_ref=resolved_seed or str(seed_ref or ""),
        graph=graph,
        graph_digest=graph.digest(),
        coverage=coverage,
        limitations=sorted(set(limitations)),
        unresolved_endpoints=sorted(set(external + virtual + unresolved)),
        managed_frontier_targets=frontier.targets,
        frontier=frontier,
    )
