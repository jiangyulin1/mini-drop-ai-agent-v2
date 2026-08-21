"""Low-overhead TCP topology snapshot collector.

The collector intentionally uses procfs instead of an always-on sidecar so the
current Agent can discover a seed process' live TCP peers without installing
extra software.  It is a point-in-time observation: completed short-lived
connections, application protocol semantics, NAT/LB backends and causality are
outside of this artifact's claims.

``scope=target`` (the default) only emits sockets owned by ``target_pid`` and
keeps both collection overhead and upload size small.  ``scope=host`` is an
explicit inventory mode used by a remote endpoint resolver.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone
from typing import Any

from agent.mini_drop_agent.collectors.base import CollectorResult, CollectorTask


SCHEMA_VERSION = "network_discovery.v1"
EVENT_SCHEMA_VERSION = "network-discovery-event.v1"
_SOCKET_LINK_RE = re.compile(r"^socket:\[(\d+)]$")
_NETNS_RE = re.compile(r"^net:\[(\d+)]$")
_CONTAINER_ID_RE = re.compile(r"(?<![0-9a-f])([0-9a-f]{32,64})(?![0-9a-f])")
_TCP_STATES = {
    "01": "ESTABLISHED",
    "02": "SYN_SENT",
    "03": "SYN_RECV",
    "04": "FIN_WAIT1",
    "05": "FIN_WAIT2",
    "06": "TIME_WAIT",
    "07": "CLOSE",
    "08": "CLOSE_WAIT",
    "09": "LAST_ACK",
    "0A": "LISTEN",
    "0B": "CLOSING",
    "0C": "NEW_SYN_RECV",
}


def _clk_tck() -> int:
    try:
        return int(os.sysconf("SC_CLK_TCK") or 100)
    except (AttributeError, OSError, ValueError):
        return 100


class NetworkDiscoveryCollector:
    """Associate procfs TCP socket inodes with stable process incarnations."""

    OUTPUT_BASE = "/tmp/mini-drop"

    def _output_base(self) -> str:
        return os.getenv("MINI_DROP_ARTIFACT_ROOT", "").strip() or self.OUTPUT_BASE
    PROC_ROOT = "/proc"
    CGROUP_ROOT = "/sys/fs/cgroup"
    DEFAULT_MAX_PROCESSES = 500
    DEFAULT_MAX_SOCKETS = 500
    DEFAULT_MAX_EVENTS = 500

    def collect(self, task: CollectorTask) -> CollectorResult:
        scope = str(task.options.get("scope") or "target").strip().lower()
        if scope not in {"target", "host"}:
            return CollectorResult(ok=False, reason="network_discovery scope 必须是 target 或 host")

        max_processes = self._bounded_int(
            task.options.get("max_processes"), self.DEFAULT_MAX_PROCESSES, 1, 2000,
        )
        max_sockets = self._bounded_int(
            task.options.get("max_sockets"), self.DEFAULT_MAX_SOCKETS, 1, 2000,
        )
        max_events = self._bounded_int(
            task.options.get("max_events"), self.DEFAULT_MAX_EVENTS, 1, 2000,
        )
        include_loopback = self._as_bool(task.options.get("include_loopback"), default=True)
        include_listeners = self._as_bool(task.options.get("include_listeners"), default=True)
        include_connections = self._as_bool(task.options.get("include_connections"), default=True)
        listener_ports = self._port_filter(
            task.options.get("listener_ports", task.options.get("listen_ports")),
        )
        if not include_listeners and not include_connections:
            return CollectorResult(
                ok=False,
                reason="network_discovery 至少需要 include_listeners/include_connections 之一",
            )
        agent_id = str(task.options.get("agent_id") or os.getenv("AGENT_ID", ""))[:128]
        agent_ip = str(task.options.get("agent_ip") or os.getenv("AGENT_IP_ADDR", ""))[:128]

        if not os.path.isdir(self.PROC_ROOT):
            if platform.system() == "Darwin" and shutil.which("lsof"):
                return self._collect_lsof(
                    task,
                    scope=scope,
                    max_processes=max_processes,
                    max_sockets=max_sockets,
                    max_events=max_events,
                    include_loopback=include_loopback,
                    include_listeners=include_listeners,
                    include_connections=include_connections,
                    listener_ports=listener_ports,
                    agent_id=agent_id,
                    agent_ip=agent_ip,
                )
            return CollectorResult(ok=False, reason="network_discovery 需要 Linux procfs")

        boot_id = self._read_text("sys/kernel/random/boot_id", max_bytes=128).strip()
        boot_time = self._read_boot_time()
        observed_ts = time.time()
        observed_at = datetime.fromtimestamp(observed_ts, tz=timezone.utc).isoformat()

        available_pids = self._list_pids()
        if scope == "target":
            if task.target_pid not in available_pids:
                return CollectorResult(
                    ok=False,
                    reason=f"目标进程 {task.target_pid} 不存在或 Agent 无权读取",
                )
            requested_pids = [task.target_pid]
            process_scan_truncated = False
        else:
            requested_pids = available_pids[:max_processes]
            process_scan_truncated = len(available_pids) > len(requested_pids)

        processes: dict[int, dict[str, Any]] = {}
        fd_access_failures = 0
        process_read_failures = 0
        for pid in requested_pids:
            process = self._read_process(pid, boot_id=boot_id, boot_time=boot_time)
            if process is None:
                process_read_failures += 1
                continue
            if not process.pop("_fd_accessible"):
                fd_access_failures += 1
            processes[pid] = process

        if scope == "target" and task.target_pid not in processes:
            return CollectorResult(
                ok=False,
                reason=f"目标进程 {task.target_pid} 在采集期间退出或无法读取",
            )

        inode_owners: dict[tuple[int, int], list[int]] = {}
        netns_members: dict[int, list[int]] = {}
        for pid, process in processes.items():
            netns = int(process["netns"] or 0)
            netns_members.setdefault(netns, []).append(pid)
            for inode in process["_socket_inodes"]:
                inode_owners.setdefault((netns, inode), []).append(pid)

        sockets: list[dict[str, Any]] = []
        socket_table_failures: list[str] = []
        tables_read = 0
        for netns, member_pids in sorted(netns_members.items()):
            for family, filename in ((socket.AF_INET, "tcp"), (socket.AF_INET6, "tcp6")):
                rows, read_pid = self._read_namespace_table(
                    member_pids, filename=filename, family=family, netns=netns,
                )
                if read_pid is None:
                    socket_table_failures.append(f"netns={netns}:{filename}")
                    continue
                tables_read += 1
                for row in rows:
                    row["owner_pids"] = sorted(inode_owners.get((netns, row["inode"]), []))
                    sockets.append(row)

        listener_exact, listener_wildcard = self._listener_indexes(sockets)
        for item in sockets:
            self._infer_direction(item, listener_exact, listener_wildcard)

        if scope == "target":
            sockets, peer_processes, peer_limit_reached = self._target_and_local_peer_sockets(
                sockets,
                target_pid=task.target_pid,
                target_netns=int(processes[task.target_pid]["netns"] or 0),
                available_pids=available_pids,
                max_processes=max_processes,
                boot_id=boot_id,
                boot_time=boot_time,
            )
            for pid, process in peer_processes.items():
                processes[pid] = process
            if peer_limit_reached:
                process_scan_truncated = True
        if listener_ports:
            # Endpoint-to-process resolution mode: avoid returning unrelated
            # host connections when only a listening port was requested.
            sockets = [
                item for item in sockets
                if item["state"] == "LISTEN" and item["local_port"] in listener_ports
            ]
        else:
            sockets = [
                item for item in sockets
                if (include_listeners or item["state"] != "LISTEN")
                and (include_connections or item["state"] == "LISTEN")
            ]
        if not include_loopback:
            sockets = [item for item in sockets if not self._is_loopback_only(item)]

        sockets.sort(key=self._socket_sort_key)
        unique_socket_count = len(sockets)
        socket_scan_truncated = len(sockets) > max_sockets
        sockets = sockets[:max_sockets]

        events: list[dict[str, Any]] = []
        event_sockets: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for item in sockets:
            owners: list[int | None] = item["owner_pids"] or ([None] if scope == "host" else [])
            for pid in owners:
                process = processes.get(pid) if pid is not None else None
                event = self._event(
                    item,
                    process=process,
                    shared_owner_pids=item["owner_pids"],
                    agent_id=agent_id,
                    boot_id=boot_id,
                    observed_at=observed_at,
                    source="procfs",
                )
                events.append(event)
                event_sockets.append((event, item))
                if len(events) >= max_events:
                    break
            if len(events) >= max_events:
                break
        event_scan_truncated = sum(max(1, len(item["owner_pids"])) for item in sockets) > len(events)

        per_process: dict[int, dict[str, Any]] = {}
        for event in events:
            event_process = event.get("process") or {}
            pid = event_process.get("pid")
            if not isinstance(pid, int):
                continue
            stats = per_process.setdefault(pid, {"listen_ports": set(), "connection_count": 0})
            if event["event_type"] == "tcp_listen":
                stats["listen_ports"].add(event["socket"]["local"]["port"])
            else:
                stats["connection_count"] += 1

        output_processes: list[dict[str, Any]] = []
        for pid, process in sorted(processes.items()):
            stats = per_process.get(pid)
            if scope == "host" and stats is None:
                continue
            public_process = {
                key: value for key, value in process.items() if not key.startswith("_")
            }
            public_process["listen_ports"] = sorted((stats or {}).get("listen_ports", set()))
            public_process["connection_count"] = int((stats or {}).get("connection_count", 0))
            output_processes.append(public_process)

        resolved_sockets = sum(1 for item in sockets if item["owner_pids"])
        partial_reasons: list[str] = []
        if process_scan_truncated:
            partial_reasons.append("process_limit_reached")
        if socket_scan_truncated:
            partial_reasons.append("socket_limit_reached")
        if event_scan_truncated:
            partial_reasons.append("event_limit_reached")
        if process_read_failures:
            partial_reasons.append("process_read_failures")
        if fd_access_failures:
            partial_reasons.append("fd_permission_or_race")
        if socket_table_failures:
            partial_reasons.append("socket_table_unavailable")
        if scope == "target" and fd_access_failures:
            partial_reasons.append("target_socket_ownership_unresolved")

        coverage_status = "partial" if partial_reasons else "complete"
        if not tables_read or (scope == "target" and fd_access_failures and not events):
            coverage_status = "insufficient"

        summary = {
            "scope": scope,
            "seed_pid": task.target_pid,
            "process_count": len(output_processes),
            "socket_count": len(sockets),
            "event_count": len(events),
            "listener_count": sum(1 for item in events if item["event_type"] == "tcp_listen"),
            "connection_count": sum(1 for item in events if item["event_type"] != "tcp_listen"),
            "established_count": sum(
                1 for _, socket_item in event_sockets if socket_item["state"] == "ESTABLISHED"
            ),
            "namespace_count": len(netns_members),
            "unresolved_socket_count": len(sockets) - resolved_sockets,
            "truncated": process_scan_truncated or socket_scan_truncated or event_scan_truncated,
        }
        listeners = [
            self._socket_projection(event, socket_item)
            for event, socket_item in event_sockets if event["event_type"] == "tcp_listen"
        ]
        connections = [
            self._socket_projection(event, socket_item)
            for event, socket_item in event_sockets if event["event_type"] != "tcp_listen"
        ]
        coverage = {
            "status": coverage_status,
            "partial": coverage_status != "complete",
            "reasons": list(dict.fromkeys(partial_reasons)),
            "available_process_count": len(available_pids),
            "requested_process_count": len(requested_pids),
            "inspected_process_count": len(processes),
            "process_read_failure_count": process_read_failures,
            "fd_access_failure_count": fd_access_failures,
            "socket_tables_read": tables_read,
            "socket_table_failures": socket_table_failures,
            "observed_socket_count_before_limit": unique_socket_count,
            "owner_resolution_ratio": round(resolved_sockets / len(sockets), 4) if sockets else 1.0,
        }
        output = {
            "schema_version": SCHEMA_VERSION,
            "event_schema_version": EVENT_SCHEMA_VERSION,
            "task_id": task.id,
            "capture_mode": "procfs_snapshot",
            "observed_at": observed_at,
            "observed_timestamp": observed_ts,
            "agent_id": agent_id or "unknown-agent",
            "boot_id": boot_id or "unknown-boot",
            "hostname": socket.gethostname(),
            "host_addresses": [agent_ip] if agent_ip else [],
            "dropped_events": 0,
            "clock_quality": "unknown",
            "agent": {
                "agent_id": agent_id or "unknown-agent",
                "hostname": socket.gethostname(),
                "ip_addr": agent_ip,
                "boot_id": boot_id or "unknown-boot",
            },
            "summary": summary,
            "coverage": coverage,
            "processes": output_processes,
            "listeners": listeners,
            "connections": connections,
            "events": events,
            "limitations": [
                "point_in_time_snapshot_misses_completed_short_connections",
                "tcp_communication_does_not_prove_root_cause",
                "nat_load_balancer_and_proxy_backends_are_not_uniquely_resolved",
                "application_protocol_and_request_outcomes_are_not_observed",
            ],
        }

        output_dir = os.path.join(self._output_base(), task.id)
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "network_discovery.json")
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(output, handle, ensure_ascii=False, indent=2)

        reason = (
            f"网络发现快照完成: {summary['process_count']} 个进程, "
            f"{summary['listener_count']} 个监听事件, {summary['connection_count']} 个连接事件"
        )
        if coverage_status != "complete":
            reason += f"（覆盖 {coverage_status}）"
        return CollectorResult(
            ok=True,
            reason=reason,
            artifacts=[{
                "artifact_type": "network_discovery",
                "filename": "network_discovery.json",
                "local_path": output_path,
                "content_type": "application/json",
                "size_bytes": os.path.getsize(output_path),
                "metadata": {
                    "schema_version": SCHEMA_VERSION,
                    "event_schema_version": EVENT_SCHEMA_VERSION,
                    "coverage_status": coverage_status,
                    **summary,
                },
            }],
        )

    def _collect_lsof(
        self,
        task: CollectorTask,
        *,
        scope: str,
        max_processes: int,
        max_sockets: int,
        max_events: int,
        include_loopback: bool,
        include_listeners: bool,
        include_connections: bool,
        listener_ports: set[int],
        agent_id: str,
        agent_ip: str,
    ) -> CollectorResult:
        """macOS read-only fallback used by local demos and smoke tests."""

        rows, command_errors = self._darwin_lsof_rows(
            target_pid=task.target_pid if scope == "target" else None,
        )
        if rows is None:
            return CollectorResult(
                ok=False,
                reason="network_discovery 无法执行 macOS lsof TCP 快照",
            )

        observed_ts = time.time()
        observed_at = datetime.fromtimestamp(observed_ts, tz=timezone.utc).isoformat()
        boot_id, boot_time = self._darwin_boot_identity()
        sockets = [self._lsof_socket_row(item) for item in rows]
        sockets = [item for item in sockets if item is not None]
        listener_exact, listener_wildcard = self._listener_indexes(sockets)
        for item in sockets:
            self._infer_direction(item, listener_exact, listener_wildcard)

        if scope == "target":
            target_sockets = [item for item in sockets if task.target_pid in item["owner_pids"]]
            if not target_sockets and not self._darwin_pid_exists(task.target_pid):
                return CollectorResult(
                    ok=False,
                    reason=f"目标进程 {task.target_pid} 不存在或 Agent 无权读取",
                )
            sockets = self._lsof_target_and_peer_listeners(
                target_sockets,
                sockets,
                target_pid=task.target_pid,
                max_processes=max_processes,
            )

        if listener_ports:
            sockets = [
                item for item in sockets
                if item["state"] == "LISTEN" and item["local_port"] in listener_ports
            ]
        else:
            sockets = [
                item for item in sockets
                if (include_listeners or item["state"] != "LISTEN")
                and (include_connections or item["state"] == "LISTEN")
            ]
        if not include_loopback:
            sockets = [item for item in sockets if not self._is_loopback_only(item)]

        sockets.sort(key=self._socket_sort_key)
        process_ids = sorted({pid for item in sockets for pid in item["owner_pids"]})
        process_limit_reached = len(process_ids) > max_processes
        allowed_pids = set(process_ids[:max_processes])
        if scope == "target":
            allowed_pids.add(task.target_pid)
        sockets = [item for item in sockets if allowed_pids.intersection(item["owner_pids"])]
        socket_count_before_limit = len(sockets)
        socket_limit_reached = len(sockets) > max_sockets
        sockets = sockets[:max_sockets]

        comm_by_pid = {
            int(item["pid"]): str(item.get("comm") or "")
            for item in rows if isinstance(item.get("pid"), int)
        }
        process_pids = {pid for item in sockets for pid in item["owner_pids"]}
        if scope == "target":
            process_pids.add(task.target_pid)
        processes = self._darwin_processes(
            sorted(process_pids),
            comm_by_pid=comm_by_pid,
            boot_id=boot_id,
        )

        events: list[dict[str, Any]] = []
        event_sockets: list[tuple[dict[str, Any], dict[str, Any]]] = []
        candidate_event_count = 0
        for item in sockets:
            owners = item["owner_pids"] or [None]
            candidate_event_count += len(owners)
            for pid in owners:
                event = self._event(
                    item,
                    process=processes.get(pid) if pid is not None else None,
                    shared_owner_pids=item["owner_pids"],
                    agent_id=agent_id,
                    boot_id=boot_id,
                    observed_at=observed_at,
                    source="lsof",
                )
                events.append(event)
                event_sockets.append((event, item))
                if len(events) >= max_events:
                    break
            if len(events) >= max_events:
                break
        event_limit_reached = candidate_event_count > len(events)

        per_process: dict[int, dict[str, Any]] = {}
        for event in events:
            event_process = event.get("process") or {}
            pid = event_process.get("pid")
            if not isinstance(pid, int):
                continue
            stats = per_process.setdefault(pid, {"listen_ports": set(), "connection_count": 0})
            if event["event_type"] == "tcp_listen":
                stats["listen_ports"].add(event["socket"]["local"]["port"])
            else:
                stats["connection_count"] += 1

        output_processes: list[dict[str, Any]] = []
        for pid, process in sorted(processes.items()):
            stats = per_process.get(pid)
            if scope == "host" and stats is None:
                continue
            public_process = dict(process)
            public_process["listen_ports"] = sorted((stats or {}).get("listen_ports", set()))
            public_process["connection_count"] = int((stats or {}).get("connection_count", 0))
            output_processes.append(public_process)

        listeners = [
            self._socket_projection(event, socket_item)
            for event, socket_item in event_sockets if event["event_type"] == "tcp_listen"
        ]
        connections = [
            self._socket_projection(event, socket_item)
            for event, socket_item in event_sockets if event["event_type"] != "tcp_listen"
        ]
        partial_reasons = [
            "platform_lsof_fallback",
            "network_namespace_unavailable",
            "socket_inode_unavailable",
            "cgroup_identity_unavailable",
        ]
        if command_errors:
            partial_reasons.append("lsof_partial_error")
        if process_limit_reached:
            partial_reasons.append("process_limit_reached")
        if socket_limit_reached:
            partial_reasons.append("socket_limit_reached")
        if event_limit_reached:
            partial_reasons.append("event_limit_reached")
        missing_start_times = sum(
            1 for process in processes.values() if process["start_time_epoch"] is None
        )
        if missing_start_times:
            partial_reasons.append("process_start_time_unavailable")

        summary = {
            "scope": scope,
            "seed_pid": task.target_pid,
            "process_count": len(output_processes),
            "socket_count": len(sockets),
            "event_count": len(events),
            "listener_count": len(listeners),
            "connection_count": len(connections),
            "established_count": sum(1 for item in connections if item["state"] == "ESTABLISHED"),
            "namespace_count": 0,
            "unresolved_socket_count": sum(1 for item in events if item["process"] is None),
            "truncated": process_limit_reached or socket_limit_reached or event_limit_reached,
        }
        coverage = {
            "status": "partial",
            "partial": True,
            "reasons": partial_reasons,
            "available_process_count": len(comm_by_pid),
            "requested_process_count": 1 if scope == "target" else len(comm_by_pid),
            "inspected_process_count": len(processes),
            "process_read_failure_count": missing_start_times,
            "fd_access_failure_count": 0,
            "socket_tables_read": 1,
            "socket_table_failures": command_errors,
            "observed_socket_count_before_limit": socket_count_before_limit,
            "owner_resolution_ratio": 1.0 if events else 1.0,
        }
        output = {
            "schema_version": SCHEMA_VERSION,
            "event_schema_version": EVENT_SCHEMA_VERSION,
            "task_id": task.id,
            "capture_mode": "lsof_snapshot",
            "observed_at": observed_at,
            "observed_timestamp": observed_ts,
            "agent_id": agent_id or "unknown-agent",
            "boot_id": boot_id or "unknown-boot",
            "hostname": socket.gethostname(),
            "host_addresses": [agent_ip] if agent_ip else [],
            "dropped_events": 0,
            "clock_quality": "unknown",
            "agent": {
                "agent_id": agent_id or "unknown-agent",
                "hostname": socket.gethostname(),
                "ip_addr": agent_ip,
                "boot_id": boot_id or "unknown-boot",
            },
            "summary": summary,
            "coverage": coverage,
            "processes": output_processes,
            "listeners": listeners,
            "connections": connections,
            "events": events,
            "limitations": [
                "point_in_time_snapshot_misses_completed_short_connections",
                "tcp_communication_does_not_prove_root_cause",
                "nat_load_balancer_and_proxy_backends_are_not_uniquely_resolved",
                "macos_lsof_does_not_expose_linux_netns_cgroup_or_socket_inode",
            ],
        }

        output_dir = os.path.join(self._output_base(), task.id)
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, "network_discovery.json")
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(output, handle, ensure_ascii=False, indent=2)
        return CollectorResult(
            ok=True,
            reason=(
                f"macOS 网络发现快照完成: {len(output_processes)} 个进程, "
                f"{len(listeners)} 个监听事件, {len(connections)} 个连接事件（覆盖 partial）"
            ),
            artifacts=[{
                "artifact_type": "network_discovery",
                "filename": "network_discovery.json",
                "local_path": output_path,
                "content_type": "application/json",
                "size_bytes": os.path.getsize(output_path),
                "metadata": {
                    "schema_version": SCHEMA_VERSION,
                    "event_schema_version": EVENT_SCHEMA_VERSION,
                    "coverage_status": "partial",
                    **summary,
                },
            }],
        )

    def _darwin_lsof_rows(
        self, *, target_pid: int | None,
    ) -> tuple[list[dict[str, Any]] | None, list[str]]:
        commands: list[list[str]]
        base = [shutil.which("lsof") or "/usr/sbin/lsof", "-nP"]
        if target_pid is None:
            commands = [base + ["-iTCP", "-FpcfntT"]]
        else:
            commands = [
                base + ["-a", "-p", str(target_pid), "-iTCP", "-FpcfntT"],
                base + ["-iTCP", "-sTCP:LISTEN", "-FpcfntT"],
            ]
        rows: list[dict[str, Any]] = []
        errors: list[str] = []
        successful = False
        for command in commands:
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                errors.append(str(exc)[:160])
                continue
            # lsof uses exit 1 for a valid query with no matching rows.
            if completed.returncode not in {0, 1}:
                errors.append((completed.stderr or f"exit={completed.returncode}")[:160])
                continue
            successful = True
            rows.extend(self._parse_lsof_fields(completed.stdout.splitlines()))
        if not successful:
            return None, errors
        deduplicated: dict[tuple[Any, ...], dict[str, Any]] = {}
        for row in rows:
            key = (row.get("pid"), row.get("fd"), row.get("family"), row.get("name"), row.get("state"))
            deduplicated[key] = row
        return list(deduplicated.values()), errors

    @staticmethod
    def _parse_lsof_fields(lines: list[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        current_pid: int | None = None
        current_comm = ""
        current_file: dict[str, Any] | None = None

        def finish_file() -> None:
            nonlocal current_file
            if current_file and current_file.get("name"):
                rows.append(current_file)
            current_file = None

        for raw in lines:
            if not raw:
                continue
            field, value = raw[0], raw[1:]
            if field == "p":
                finish_file()
                try:
                    current_pid = int(value)
                except ValueError:
                    current_pid = None
                current_comm = ""
            elif field == "c":
                current_comm = value[:256]
            elif field == "f":
                finish_file()
                current_file = {
                    "pid": current_pid,
                    "comm": current_comm,
                    "fd": value[:64],
                }
            elif current_file is not None and field == "t":
                current_file["family"] = value
            elif current_file is not None and field == "n":
                current_file["name"] = value
            elif current_file is not None and field == "T" and value.startswith("ST="):
                current_file["state"] = value[3:].upper()
        finish_file()
        return [row for row in rows if isinstance(row.get("pid"), int)]

    def _lsof_socket_row(self, row: dict[str, Any]) -> dict[str, Any] | None:
        name = str(row.get("name") or "")
        local_raw, separator, remote_raw = name.partition("->")
        family_name = str(row.get("family") or "")
        family = "ipv6" if family_name.lower() == "ipv6" else "ipv4"
        local = self._parse_lsof_endpoint(local_raw, family=family)
        remote = self._parse_lsof_endpoint(remote_raw, family=family) if separator else None
        if local is None:
            return None
        local_ip, local_port = local
        remote_ip, remote_port = remote or (("::" if family == "ipv6" else "0.0.0.0"), 0)
        return {
            "netns": 0,
            "family": family,
            "protocol": "tcp",
            "local_ip": local_ip,
            "local_port": local_port,
            "remote_ip": remote_ip,
            "remote_port": remote_port,
            "state": str(row.get("state") or "UNKNOWN").upper(),
            "tx_queue_bytes": None,
            "rx_queue_bytes": None,
            "uid": None,
            "inode": None,
            "fd": row.get("fd"),
            "owner_pids": [int(row["pid"])],
            "comm": str(row.get("comm") or ""),
        }

    @staticmethod
    def _parse_lsof_endpoint(value: str, *, family: str) -> tuple[str, int] | None:
        value = value.strip()
        if not value:
            return None
        if value.startswith("["):
            close = value.rfind("]:")
            if close < 0:
                return None
            host, port_text = value[1:close], value[close + 2:]
        else:
            host, separator, port_text = value.rpartition(":")
            if not separator:
                return None
        try:
            port = int(port_text)
        except ValueError:
            return None
        if host == "*":
            host = "::" if family == "ipv6" else "0.0.0.0"
        return host, port

    @staticmethod
    def _lsof_target_and_peer_listeners(
        target_sockets: list[dict[str, Any]],
        all_sockets: list[dict[str, Any]],
        *,
        target_pid: int,
        max_processes: int,
    ) -> list[dict[str, Any]]:
        peers: list[dict[str, Any]] = []
        peer_pids: set[int] = set()
        for connection in target_sockets:
            if connection["state"] == "LISTEN" or not connection["remote_port"]:
                continue
            for listener in all_sockets:
                if listener["state"] != "LISTEN" or target_pid in listener["owner_pids"]:
                    continue
                address_matches = listener["local_ip"] in {
                    connection["remote_ip"], "0.0.0.0", "::",
                }
                if (
                    listener["family"] == connection["family"]
                    and listener["local_port"] == connection["remote_port"]
                    and address_matches
                ):
                    pid = listener["owner_pids"][0]
                    if pid not in peer_pids and len(peer_pids) >= max(0, max_processes - 1):
                        continue
                    peer_pids.add(pid)
                    if listener not in peers:
                        peers.append(listener)
        return target_sockets + peers

    def _darwin_processes(
        self,
        pids: list[int],
        *,
        comm_by_pid: dict[int, str],
        boot_id: str,
    ) -> dict[int, dict[str, Any]]:
        details: dict[int, dict[str, Any]] = {}
        if pids:
            try:
                completed = subprocess.run(
                    [
                        "/bin/ps", "-p", ",".join(str(pid) for pid in pids),
                        "-o", "pid=", "-o", "lstart=", "-o", "uid=", "-o", "comm=",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                for line in completed.stdout.splitlines():
                    parts = line.split(maxsplit=7)
                    if len(parts) < 7:
                        continue
                    try:
                        pid = int(parts[0])
                        started = datetime.strptime(
                            " ".join(parts[1:6]), "%a %b %d %H:%M:%S %Y",
                        ).astimezone()
                        start_epoch = round(started.timestamp(), 6)
                        uid = int(parts[6])
                    except (TypeError, ValueError):
                        continue
                    details[pid] = {
                        "start_time_epoch": start_epoch,
                        "uid": uid,
                        "comm": parts[7] if len(parts) > 7 else comm_by_pid.get(pid, ""),
                    }
            except (OSError, subprocess.TimeoutExpired):
                pass

        result: dict[int, dict[str, Any]] = {}
        for pid in pids:
            detail = details.get(pid, {})
            start_epoch = detail.get("start_time_epoch")
            identity_material = f"{boot_id}:{pid}:{start_epoch}"
            comm = str(detail.get("comm") or comm_by_pid.get(pid) or f"pid-{pid}")[:1024]
            result[pid] = {
                "process_identity": "proc_" + hashlib.sha256(identity_material.encode()).hexdigest()[:24],
                "pid": pid,
                "comm": os.path.basename(comm) or f"pid-{pid}",
                "cmdline": comm,
                "uid": detail.get("uid"),
                "start_time_ticks": int(start_epoch * 100) if start_epoch is not None else None,
                "process_start_time": int(start_epoch * 100) if start_epoch is not None else None,
                "start_time_epoch": start_epoch,
                "executable": comm,
                "boot_id": boot_id,
                "netns": None,
                "cgroup_path": None,
                "cgroup_id": None,
                "container_id": None,
            }
        return result

    @staticmethod
    def _darwin_boot_identity() -> tuple[str, float | None]:
        boot_time = None
        try:
            completed = subprocess.run(
                ["/usr/sbin/sysctl", "-n", "kern.boottime"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            match = re.search(r"sec\s*=\s*(\d+)", completed.stdout)
            if match:
                boot_time = float(match.group(1))
        except (OSError, subprocess.TimeoutExpired):
            pass
        material = f"{socket.gethostname()}:{boot_time}"
        return "darwin_" + hashlib.sha256(material.encode()).hexdigest()[:24], boot_time

    @staticmethod
    def _darwin_pid_exists(pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except (ProcessLookupError, OSError):
            return False

    def _list_pids(self) -> list[int]:
        try:
            return sorted(int(name) for name in os.listdir(self.PROC_ROOT) if name.isdigit())
        except (FileNotFoundError, PermissionError, OSError):
            return []

    def _read_process(
        self, pid: int, *, boot_id: str, boot_time: float | None,
    ) -> dict[str, Any] | None:
        proc_dir = os.path.join(self.PROC_ROOT, str(pid))
        stat = self._read_text(f"{pid}/stat", max_bytes=65536)
        start_ticks = self._process_start_ticks(stat)
        if start_ticks is None:
            return None
        comm = self._read_text(f"{pid}/comm", max_bytes=4096).strip()
        cmdline = self._read_bytes(f"{pid}/cmdline", max_bytes=16384)
        cmdline_text = cmdline.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()[:1024]
        uid = self._read_uid(pid)
        netns = self._read_netns(pid)
        cgroup = self._read_cgroup(pid)
        socket_inodes, fd_accessible = self._read_socket_inodes(proc_dir)
        start_epoch = None
        if boot_time is not None:
            start_epoch = round(boot_time + (start_ticks / _clk_tck()), 6)
        identity_material = f"{boot_id}:{pid}:{start_ticks}"
        return {
            "process_identity": "proc_" + hashlib.sha256(identity_material.encode()).hexdigest()[:24],
            "pid": pid,
            "comm": comm or f"pid-{pid}",
            "cmdline": cmdline_text,
            "uid": uid,
            "start_time_ticks": start_ticks,
            "process_start_time": start_ticks,
            "start_time_epoch": start_epoch,
            "executable": cmdline_text or comm,
            "boot_id": boot_id,
            "netns": netns,
            "cgroup_path": cgroup["path"],
            "cgroup_id": cgroup["id"],
            "container_id": cgroup["container_id"],
            "_socket_inodes": socket_inodes,
            "_fd_accessible": fd_accessible,
        }

    def _read_namespace_table(
        self, member_pids: list[int], *, filename: str, family: int, netns: int,
    ) -> tuple[list[dict[str, Any]], int | None]:
        for pid in member_pids:
            path = os.path.join(self.PROC_ROOT, str(pid), "net", filename)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    return self._parse_tcp_lines(handle, family=family, netns=netns), pid
            except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
                continue
        return [], None

    @classmethod
    def _parse_tcp_lines(
        cls, lines: Any, *, family: int, netns: int,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for line in lines:
            fields = line.split()
            if not fields or fields[0] == "sl" or len(fields) < 10:
                continue
            try:
                local_ip, local_port = cls._decode_endpoint(fields[1], family)
                remote_ip, remote_port = cls._decode_endpoint(fields[2], family)
                tx_queue_hex, rx_queue_hex = fields[4].split(":", 1)
                inode = int(fields[9])
                uid = int(fields[7])
            except (IndexError, TypeError, ValueError, OSError):
                continue
            result.append({
                "netns": netns,
                "family": "ipv6" if family == socket.AF_INET6 else "ipv4",
                "protocol": "tcp",
                "local_ip": local_ip,
                "local_port": local_port,
                "remote_ip": remote_ip,
                "remote_port": remote_port,
                "state": _TCP_STATES.get(fields[3].upper(), f"UNKNOWN_{fields[3].upper()}"),
                "tx_queue_bytes": int(tx_queue_hex, 16),
                "rx_queue_bytes": int(rx_queue_hex, 16),
                "uid": uid,
                "inode": inode,
            })
        return result

    @staticmethod
    def _decode_endpoint(raw: str, family: int) -> tuple[str, int]:
        address_hex, port_hex = raw.split(":", 1)
        if family == socket.AF_INET:
            packed = bytes.fromhex(address_hex)[::-1]
        else:
            if len(address_hex) != 32:
                raise ValueError("invalid IPv6 procfs address")
            packed = b"".join(
                bytes.fromhex(address_hex[index:index + 8])[::-1]
                for index in range(0, 32, 8)
            )
        return socket.inet_ntop(family, packed), int(port_hex, 16)

    @staticmethod
    def _listener_indexes(
        sockets: list[dict[str, Any]],
    ) -> tuple[set[tuple[int, str, str, int]], set[tuple[int, str, int]]]:
        exact: set[tuple[int, str, str, int]] = set()
        wildcard: set[tuple[int, str, int]] = set()
        for item in sockets:
            if item["state"] != "LISTEN":
                continue
            key = (item["netns"], item["family"], item["local_ip"], item["local_port"])
            exact.add(key)
            if item["local_ip"] in {"0.0.0.0", "::"}:
                wildcard.add((item["netns"], item["family"], item["local_port"]))
        return exact, wildcard

    def _target_and_local_peer_sockets(
        self,
        sockets: list[dict[str, Any]],
        *,
        target_pid: int,
        target_netns: int,
        available_pids: list[int],
        max_processes: int,
        boot_id: str,
        boot_time: float | None,
    ) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]], bool]:
        """Keep a seed PID's sockets plus local listeners hit by its peers.

        The namespace socket table exposes the peer listener inode, but not its
        owner.  We therefore inspect bounded local fd directories and retain
        only processes owning those matched listener inodes.  This avoids
        leaking an unrelated host-wide process/socket inventory in target mode.
        """

        target_sockets = [item for item in sockets if target_pid in item["owner_pids"]]
        exact_listeners: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
        wildcard_listeners: dict[tuple[str, int], list[dict[str, Any]]] = {}
        for item in sockets:
            if item["state"] != "LISTEN" or item["netns"] != target_netns:
                continue
            exact_listeners.setdefault(
                (item["family"], item["local_ip"], item["local_port"]), [],
            ).append(item)
            if item["local_ip"] in {"0.0.0.0", "::"}:
                wildcard_listeners.setdefault(
                    (item["family"], item["local_port"]), [],
                ).append(item)

        peer_listener_rows: list[dict[str, Any]] = []
        for item in target_sockets:
            if item["state"] == "LISTEN" or not item["remote_port"]:
                continue
            matches = exact_listeners.get(
                (item["family"], item["remote_ip"], item["remote_port"]), [],
            ) or wildcard_listeners.get((item["family"], item["remote_port"]), [])
            for listener in matches:
                if listener not in peer_listener_rows:
                    peer_listener_rows.append(listener)

        peer_inodes = {item["inode"] for item in peer_listener_rows}
        if not peer_inodes:
            return target_sockets, {}, False

        peer_processes: dict[int, dict[str, Any]] = {}
        candidate_pids = available_pids[:max_processes]
        for pid in candidate_pids:
            if pid == target_pid or int(self._read_netns(pid) or 0) != target_netns:
                continue
            proc_dir = os.path.join(self.PROC_ROOT, str(pid))
            inodes, accessible = self._read_socket_inodes(proc_dir)
            matched = inodes & peer_inodes
            if not accessible or not matched:
                continue
            process = self._read_process(pid, boot_id=boot_id, boot_time=boot_time)
            if process is None:
                continue
            peer_processes[pid] = process
            for listener in peer_listener_rows:
                if listener["inode"] in matched and pid not in listener["owner_pids"]:
                    listener["owner_pids"].append(pid)
                    listener["owner_pids"].sort()

        resolved_peer_rows = [
            item for item in peer_listener_rows
            if any(pid != target_pid for pid in item["owner_pids"])
        ]
        return (
            target_sockets + resolved_peer_rows,
            peer_processes,
            len(available_pids) > len(candidate_pids),
        )

    @staticmethod
    def _infer_direction(
        item: dict[str, Any],
        listener_exact: set[tuple[int, str, str, int]],
        listener_wildcard: set[tuple[int, str, int]],
    ) -> None:
        state = item["state"]
        if state == "LISTEN":
            item.update({"role": "listener", "direction": None, "direction_confidence": 1.0})
            return
        if state in {"SYN_RECV", "NEW_SYN_RECV"}:
            item.update({
                "role": "connection", "direction": "inbound",
                "direction_confidence": 0.98, "direction_source": "tcp_state",
            })
            return
        if state == "SYN_SENT":
            item.update({
                "role": "connection", "direction": "outbound",
                "direction_confidence": 0.98, "direction_source": "tcp_state",
            })
            return
        exact_key = (item["netns"], item["family"], item["local_ip"], item["local_port"])
        wildcard_key = (item["netns"], item["family"], item["local_port"])
        if exact_key in listener_exact:
            direction, confidence = "inbound", 0.9
        elif wildcard_key in listener_wildcard:
            direction, confidence = "inbound", 0.82
        else:
            direction, confidence = "outbound", 0.7
        item.update({
            "role": "connection", "direction": direction,
            "direction_confidence": confidence,
            "direction_source": "local_listener_match",
        })

    def _event(
        self,
        item: dict[str, Any],
        *,
        process: dict[str, Any] | None,
        shared_owner_pids: list[int],
        agent_id: str,
        boot_id: str,
        observed_at: str,
        source: str,
    ) -> dict[str, Any]:
        agent_id = agent_id or "unknown-agent"
        boot_id = boot_id or "unknown-boot"
        process_identity = (process or {}).get("process_identity", "unresolved")
        identity_material = ":".join(str(value) for value in (
            boot_id, item["netns"], item["inode"], process_identity,
            item["local_ip"], item["local_port"], item["remote_ip"], item["remote_port"],
        ))
        process_projection = None
        if process is not None and process.get("start_time_ticks") is not None:
            process_projection = {
                "agent_id": agent_id,
                "boot_id": boot_id,
                "pid": process["pid"],
                "process_start_time": max(1, int(process["start_time_ticks"])),
                "cgroup_id": str(process.get("cgroup_id") or ""),
                "netns": str(process.get("netns") or ""),
                "executable": str(process.get("cmdline") or process.get("comm") or "")[:1024],
            }
        remote = None
        if item["state"] != "LISTEN":
            remote = {
                "address": item["remote_ip"],
                "port": item["remote_port"],
                "protocol": "tcp",
            }
        observation_point = {
            "outbound": "client",
            "inbound": "server",
        }.get(item.get("direction"), "host")
        return {
            "schema_version": EVENT_SCHEMA_VERSION,
            "event_id": "nde_" + hashlib.sha256(identity_material.encode()).hexdigest()[:32],
            "agent_id": agent_id,
            "boot_id": boot_id,
            "observed_at": observed_at,
            "event_type": "tcp_listen" if item["state"] == "LISTEN" else "tcp_snapshot",
            "process": process_projection,
            "socket": {
                "cookie": str(item.get("inode") or item.get("fd") or "")[:128],
                "local": {
                    "address": item["local_ip"],
                    "port": item["local_port"],
                    "protocol": "tcp",
                },
                "remote": remote,
                "result": "success" if item["state"] in {"ESTABLISHED", "LISTEN"} else "unknown",
                "observation_point": observation_point,
                "bytes_sent": 0,
                "bytes_received": 0,
            },
            "source": source,
            "evidence_refs": [],
        }

    @staticmethod
    def _socket_projection(
        event: dict[str, Any], socket_item: dict[str, Any],
    ) -> dict[str, Any]:
        process = event.get("process") or {}
        socket_value = event["socket"]
        local = socket_value["local"]
        remote = socket_value.get("remote")
        return {
            "event_id": event["event_id"],
            "observed_at": event["observed_at"],
            "source": event["source"],
            "pid": process.get("pid"),
            "process_start_time": process.get("process_start_time"),
            "executable": process.get("executable"),
            "netns": process.get("netns"),
            "protocol": "tcp",
            "endpoint": local,
            "local": local,
            "local_ip": local["address"],
            "local_port": local["port"],
            "remote": remote,
            "remote_ip": remote.get("address") if remote else None,
            "remote_port": remote.get("port") if remote else None,
            "state": socket_item["state"],
            "result": socket_value["result"],
            "observation_point": socket_value["observation_point"],
            "direction": socket_item["direction"],
            "direction_confidence": socket_item["direction_confidence"],
            "confidence": (
                0.95 if event["event_type"] == "tcp_listen"
                else socket_item["direction_confidence"]
            ),
            "direction_source": socket_item.get("direction_source"),
            "tx_queue_bytes": socket_item.get("tx_queue_bytes"),
            "rx_queue_bytes": socket_item.get("rx_queue_bytes"),
            "shared_owner_pids": socket_item.get("owner_pids", []),
        }

    def _read_socket_inodes(self, proc_dir: str) -> tuple[set[int], bool]:
        fd_dir = os.path.join(proc_dir, "fd")
        try:
            names = os.listdir(fd_dir)
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            return set(), False
        inodes: set[int] = set()
        for name in names:
            try:
                target = os.readlink(os.path.join(fd_dir, name))
            except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
                continue
            match = _SOCKET_LINK_RE.match(target)
            if match:
                inodes.add(int(match.group(1)))
        return inodes, True

    def _read_netns(self, pid: int) -> int | None:
        try:
            value = os.readlink(os.path.join(self.PROC_ROOT, str(pid), "ns", "net"))
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            return None
        match = _NETNS_RE.match(value)
        return int(match.group(1)) if match else None

    def _read_cgroup(self, pid: int) -> dict[str, Any]:
        raw = self._read_text(f"{pid}/cgroup", max_bytes=65536)
        paths: list[str] = []
        for line in raw.splitlines():
            parts = line.split(":", 2)
            if len(parts) == 3 and parts[2]:
                paths.append(parts[2])
        path = max(paths, key=len, default="")[:2048]
        container_match = _CONTAINER_ID_RE.findall(path.lower())
        container_id = container_match[-1] if container_match else None
        cgroup_id = None
        if path:
            candidate = os.path.normpath(os.path.join(self.CGROUP_ROOT, path.lstrip("/")))
            try:
                cgroup_id = os.stat(candidate).st_ino
            except (FileNotFoundError, PermissionError, OSError):
                pass
        return {"path": path or None, "id": cgroup_id, "container_id": container_id}

    def _read_uid(self, pid: int) -> int | None:
        raw = self._read_text(f"{pid}/status", max_bytes=65536)
        for line in raw.splitlines():
            if line.startswith("Uid:"):
                try:
                    return int(line.split()[1])
                except (IndexError, ValueError):
                    return None
        return None

    def _read_boot_time(self) -> float | None:
        for line in self._read_text("stat", max_bytes=1024 * 1024).splitlines():
            if line.startswith("btime "):
                try:
                    return float(line.split()[1])
                except (IndexError, ValueError):
                    return None
        return None

    @staticmethod
    def _process_start_ticks(raw: str) -> int | None:
        close = raw.rfind(")")
        if close < 0:
            return None
        fields = raw[close + 2:].split()
        try:
            return int(fields[19])  # field 22 overall; fields begins at stat field 3
        except (IndexError, ValueError):
            return None

    def _read_text(self, relative_path: str, *, max_bytes: int) -> str:
        return self._read_bytes(relative_path, max_bytes=max_bytes).decode("utf-8", errors="replace")

    def _read_bytes(self, relative_path: str, *, max_bytes: int) -> bytes:
        try:
            with open(os.path.join(self.PROC_ROOT, relative_path), "rb") as handle:
                return handle.read(max_bytes)
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            return b""

    @staticmethod
    def _socket_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
        return (
            not bool(item["owner_pids"]),
            item["state"] != "LISTEN",
            item["netns"], item["local_ip"], item["local_port"],
            item["remote_ip"], item["remote_port"], item["inode"],
        )

    @staticmethod
    def _is_loopback_only(item: dict[str, Any]) -> bool:
        try:
            local = ipaddress.ip_address(item["local_ip"])
            remote = ipaddress.ip_address(item["remote_ip"])
        except ValueError:
            return False
        if item["state"] == "LISTEN":
            return local.is_loopback
        return local.is_loopback and remote.is_loopback

    @staticmethod
    def _format_endpoint(ip: str, port: int) -> str:
        return f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"

    @staticmethod
    def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value if value is not None else default)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(parsed, maximum))

    @staticmethod
    def _port_filter(value: Any) -> set[int]:
        if value is None or value == "":
            return set()
        values = value if isinstance(value, (list, tuple, set)) else [value]
        result: set[int] = set()
        for item in list(values)[:32]:
            try:
                port = int(item)
            except (TypeError, ValueError):
                continue
            if 1 <= port <= 65535:
                result.add(port)
        return result

    @staticmethod
    def _as_bool(value: Any, *, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
