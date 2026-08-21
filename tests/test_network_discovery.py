"""Agent-side unknown-topology TCP snapshot tests."""

from __future__ import annotations

import json
import os
import platform
import socket
from pathlib import Path

import pytest

from agent.mini_drop_agent.collectors.base import CollectorTask
from agent.mini_drop_agent.collectors.network_discovery import (
    EVENT_SCHEMA_VERSION,
    SCHEMA_VERSION,
    NetworkDiscoveryCollector,
)


def _stat_line(pid: int, comm: str, start_ticks: int) -> str:
    # Content after ')' begins at proc stat field 3. starttime is field 22,
    # therefore index 19 in the collector's suffix parser.
    suffix = ["S"] + ["0"] * 18 + [str(start_ticks)] + ["0"] * 5
    return f"{pid} ({comm}) " + " ".join(suffix)


def _tcp_row(
    slot: int,
    local: str,
    remote: str,
    state: str,
    inode: int,
    *,
    uid: int = 1000,
) -> str:
    return (
        f"{slot}: {local} {remote} {state} 00000000:00000000 "
        f"00:00000000 00000000 {uid} 0 {inode} 1 0000000000000000"
    )


@pytest.fixture(name="fake_proc")
def fake_proc_fixture(tmp_path: Path) -> tuple[Path, Path]:
    proc = tmp_path / "proc"
    cgroup = tmp_path / "sys" / "fs" / "cgroup"
    (proc / "sys" / "kernel" / "random").mkdir(parents=True)
    (proc / "sys" / "kernel" / "random" / "boot_id").write_text(
        "boot-test-001\n", encoding="utf-8",
    )
    (proc / "stat").write_text("cpu  1 2 3 4\nbtime 1700000000\n", encoding="utf-8")

    processes = {
        100: {
            "comm": "checkout-client",
            "start": 12345,
            "cgroup": "/system.slice/checkout.service",
            "inodes": [111],
        },
        200: {
            "comm": "payment-server",
            "start": 23456,
            "cgroup": "/system.slice/payment.service",
            "inodes": [222, 333],
        },
    }
    tcp = "\n".join([
        "sl local_address rem_address st tx_queue:rx_queue tr tm->when retrnsmt uid timeout inode",
        # checkout-client:127.0.0.1:50000 -> payment-server:127.0.0.1:8080
        _tcp_row(0, "0100007F:C350", "0100007F:1F90", "01", 111),
        _tcp_row(1, "0100007F:1F90", "00000000:0000", "0A", 222),
        _tcp_row(2, "0100007F:1F90", "0100007F:C350", "01", 333),
    ]) + "\n"
    tcp6 = "sl local_address rem_address st tx_queue:rx_queue tr tm->when retrnsmt uid timeout inode\n"

    for pid, value in processes.items():
        base = proc / str(pid)
        (base / "fd").mkdir(parents=True)
        (base / "net").mkdir()
        (base / "ns").mkdir()
        (base / "comm").write_text(value["comm"] + "\n", encoding="utf-8")
        (base / "cmdline").write_bytes(f"/usr/bin/{value['comm']}\x00--serve".encode())
        (base / "stat").write_text(
            _stat_line(pid, value["comm"], value["start"]), encoding="utf-8",
        )
        (base / "status").write_text("Uid:\t1000\t1000\t1000\t1000\n", encoding="utf-8")
        (base / "cgroup").write_text(f"0::{value['cgroup']}\n", encoding="utf-8")
        os.symlink("net:[4026533000]", base / "ns" / "net")
        for fd, inode in enumerate(value["inodes"], start=3):
            os.symlink(f"socket:[{inode}]", base / "fd" / str(fd))
        (base / "net" / "tcp").write_text(tcp, encoding="utf-8")
        (base / "net" / "tcp6").write_text(tcp6, encoding="utf-8")
        (cgroup / value["cgroup"].lstrip("/")).mkdir(parents=True)
    return proc, cgroup


def _collector(fake_proc: tuple[Path, Path], tmp_path: Path) -> NetworkDiscoveryCollector:
    proc, cgroup = fake_proc
    collector = NetworkDiscoveryCollector()
    collector.PROC_ROOT = str(proc)
    collector.CGROUP_ROOT = str(cgroup)
    collector.OUTPUT_BASE = str(tmp_path / "out")
    return collector


def _task(task_id: str, *, target_pid: int = 100, **options) -> CollectorTask:
    return CollectorTask(
        id=task_id,
        collector_type="network_discovery",
        target_pid=target_pid,
        sample_rate=1,
        duration_sec=2,
        options={"agent_id": "worker-a", "agent_ip": "10.0.0.10", **options},
    )


def test_target_snapshot_keeps_seed_and_matched_local_peer_listener(fake_proc, tmp_path):
    collector = _collector(fake_proc, tmp_path)
    result = collector.collect(_task("network-target"))

    assert result.ok, result.reason
    assert result.artifacts[0]["artifact_type"] == "network_discovery"
    payload = json.loads(Path(result.artifacts[0]["local_path"]).read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["event_schema_version"] == EVENT_SCHEMA_VERSION
    assert payload["capture_mode"] == "procfs_snapshot"
    assert payload["agent_id"] == "worker-a"
    assert payload["coverage"]["status"] == "complete"

    # scope=target does not expose the host inventory, but it retains the
    # one local server listener actually reached by the seed connection.
    assert {item["pid"] for item in payload["processes"]} == {100, 200}
    assert len(payload["connections"]) == 1
    connection = payload["connections"][0]
    assert connection["pid"] == 100
    assert connection["local_port"] == 50000
    assert connection["remote_port"] == 8080
    assert connection["direction"] == "outbound"
    assert connection["observation_point"] == "client"

    assert len(payload["listeners"]) == 1
    listener = payload["listeners"][0]
    assert listener["pid"] == 200
    assert listener["endpoint"] == {
        "address": "127.0.0.1", "port": 8080, "protocol": "tcp",
    }
    assert payload["summary"]["process_count"] == 2
    assert payload["summary"]["listener_count"] == 1
    assert payload["summary"]["connection_count"] == 1


def test_canonical_events_validate_against_shared_discovery_contract(fake_proc, tmp_path):
    from server.app.diagnosis.dependency_graph import DiscoveryEvent
    from server.app.diagnosis.discovery_frontier import AgentNetworkInventory

    collector = _collector(fake_proc, tmp_path)
    result = collector.collect(_task("network-contract"))
    payload = json.loads(Path(result.artifacts[0]["local_path"]).read_text(encoding="utf-8"))

    events = [DiscoveryEvent.model_validate(value) for value in payload["events"]]
    assert {item.event_type for item in events} == {"tcp_listen", "tcp_snapshot"}
    assert all(item.source == "procfs" for item in events)
    assert all(item.process is not None for item in events)
    assert all(item.process.agent_id == "worker-a" for item in events if item.process)
    inventory = AgentNetworkInventory.from_payload(payload)
    assert {item.pid for item in inventory.processes} == {100, 200}
    assert len(inventory.listeners) == 1
    assert len(inventory.connections) == 1


def test_host_listener_port_filter_returns_only_requested_listener(fake_proc, tmp_path):
    collector = _collector(fake_proc, tmp_path)
    result = collector.collect(_task(
        "network-listener-resolution",
        scope="host",
        listener_ports=[8080],
        max_processes=10,
        max_sockets=10,
        max_events=10,
    ))
    payload = json.loads(Path(result.artifacts[0]["local_path"]).read_text(encoding="utf-8"))

    assert payload["connections"] == []
    assert len(payload["listeners"]) == 1
    assert payload["listeners"][0]["pid"] == 200
    assert payload["listeners"][0]["local_port"] == 8080
    assert [item["pid"] for item in payload["processes"]] == [200]


def test_limits_are_enforced_and_reported(fake_proc, tmp_path):
    collector = _collector(fake_proc, tmp_path)
    result = collector.collect(_task(
        "network-limits", scope="host", max_processes=2, max_sockets=3, max_events=1,
    ))
    payload = json.loads(Path(result.artifacts[0]["local_path"]).read_text(encoding="utf-8"))

    assert len(payload["events"]) == 1
    assert payload["coverage"]["status"] == "partial"
    assert "event_limit_reached" in payload["coverage"]["reasons"]
    assert payload["summary"]["truncated"] is True


def test_parse_procfs_ipv4_and_ipv6_endpoints():
    assert NetworkDiscoveryCollector._decode_endpoint("0100007F:1F90", socket.AF_INET) == (
        "127.0.0.1", 8080,
    )
    assert NetworkDiscoveryCollector._decode_endpoint(
        "00000000000000000000000001000000:01BB", socket.AF_INET6,
    ) == ("::1", 443)


def test_lsof_field_parser_and_socket_projection():
    rows = NetworkDiscoveryCollector._parse_lsof_fields([
        "p101", "cclient", "f9", "tIPv4",
        "n127.0.0.1:50000->127.0.0.1:8080", "TST=ESTABLISHED",
        "p202", "cserver", "f7", "tIPv6", "n*:8080", "TST=LISTEN",
    ])
    assert len(rows) == 2
    collector = NetworkDiscoveryCollector()
    connection = collector._lsof_socket_row(rows[0])
    listener = collector._lsof_socket_row(rows[1])
    assert connection is not None and connection["remote_port"] == 8080
    assert connection["family"] == "ipv4"
    assert listener is not None and listener["local_ip"] == "::"
    assert listener["state"] == "LISTEN"


def test_artifact_root_environment_overrides_legacy_output_base(monkeypatch, tmp_path):
    collector = NetworkDiscoveryCollector()
    collector.OUTPUT_BASE = str(tmp_path / "legacy")
    configured = tmp_path / "configured"
    monkeypatch.setenv("MINI_DROP_ARTIFACT_ROOT", str(configured))

    assert collector._output_base() == str(configured)


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS lsof fallback only")
def test_macos_lsof_fallback_real_listener_smoke(tmp_path):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    collector = NetworkDiscoveryCollector()
    collector.OUTPUT_BASE = str(tmp_path)
    try:
        result = collector.collect(_task(
            "network-macos-live",
            target_pid=os.getpid(),
            listener_ports=[listener.getsockname()[1]],
            max_sockets=10,
            max_events=10,
        ))
        assert result.ok, result.reason
        payload = json.loads(Path(result.artifacts[0]["local_path"]).read_text(encoding="utf-8"))
        assert payload["capture_mode"] == "lsof_snapshot"
        assert payload["coverage"]["status"] == "partial"
        assert payload["listeners"][0]["local_port"] == listener.getsockname()[1]
    finally:
        listener.close()
