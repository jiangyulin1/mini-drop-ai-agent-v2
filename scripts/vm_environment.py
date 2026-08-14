"""VM Environment Profile loader for Mini-Drop lab runners.

E0 gate: node addresses, users and Agent IDs come from an approved Environment
Profile (benchmarks/environments/*.json), never from hardcoded literals.  SSH
connections validate host keys against a known-hosts lockfile; unknown hosts
are rejected unless the operator explicitly records host keys first.

Password is read only from the MINI_DROP_VM_PASSWORD environment variable and
never placed in command lines, logs, reports, git diffs or model context.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import paramiko


@dataclass(frozen=True)
class VMNode:
    name: str
    ip: str
    user: str
    agent_id: str = ""


@dataclass(frozen=True)
class VMTopology:
    environment_id: str
    control: VMNode
    workers: dict[str, VMNode]

    def all_nodes(self) -> list[VMNode]:
        return [self.control, *self.workers.values()]

    def worker(self, name: str) -> VMNode:
        node = self.workers.get(name)
        if node is None:
            raise KeyError(f"unknown worker {name!r} in profile {self.environment_id}")
        return node


def load_topology(profile_path: str | Path) -> VMTopology:
    path = Path(profile_path)
    if not path.is_file():
        raise FileNotFoundError(f"environment profile not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    topology = raw.get("topology") or {}
    control_raw = topology.get("control") or {}
    if not control_raw.get("ip"):
        raise ValueError(f"profile {path} is missing topology.control.ip")
    control = VMNode(
        name=str(control_raw.get("host") or "control"),
        ip=str(control_raw["ip"]),
        user=str(control_raw.get("user") or "control"),
    )
    workers: dict[str, VMNode] = {}
    for worker in topology.get("workers") or []:
        ip = worker.get("ip")
        if not ip:
            continue
        node = VMNode(
            name=str(worker.get("host") or ip),
            ip=str(ip),
            user=str(worker.get("user") or str(worker.get("host") or "worker")),
            agent_id=str(worker.get("agent_id") or ""),
        )
        workers[node.name] = node
    if not workers:
        raise ValueError(f"profile {path} declares no workers")
    return VMTopology(
        environment_id=str(raw.get("environment_id") or "unknown"),
        control=control,
        workers=workers,
    )


def api_base_from_control(node: VMNode) -> str:
    """Control VM serves the public REST/HTTPS endpoint in this lab."""
    return f"https://{node.ip}"


def default_known_hosts() -> Path:
    return Path(__file__).resolve().parents[1] / "deploy" / "ssh" / "mini-drop_vm_known_hosts"


class SSHGate:
    """Paramiko client factory that fails closed on unknown host keys."""

    def __init__(self, password: str, known_hosts: Path | None = None):
        self.password = password
        self.known_hosts = Path(known_hosts) if known_hosts else default_known_hosts()

    def connect(self, node: VMNode, *, timeout: int = 15) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        if self.known_hosts.is_file():
            client.load_host_keys(str(self.known_hosts))
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            raise RuntimeError(
                f"no known-hosts file at {self.known_hosts}; refusing to accept an unknown host key.\n"
                "Record the current VM host keys once, then re-run:\n"
                f"  python scripts/run_ai_ops_v2_vm.py --record-host-keys --env-profile benchmarks/environments/hyperv_online_boutique_verified_vm.json"
            )
        client.connect(node.ip, username=node.user, password=self.password, timeout=timeout)
        return client


def record_host_keys(profile: VMTopology, password: str, known_hosts: Path) -> int:
    """One-time explicit capture of the current VM host keys into a lockfile."""
    known_hosts.parent.mkdir(parents=True, exist_ok=True)
    recorded: list[str] = []
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # 仅此一次性录入流程
    for node in profile.all_nodes():
        try:
            client.connect(node.ip, username=node.user, password=password, timeout=20)
            host_keys = client.get_host_keys()
            for host in host_keys:
                sub = host_keys[host]
                for key_type, key in sub.items():  # SubDict: key_type -> PKey
                    if hasattr(key, "get_base64"):
                        recorded.append(f"{host} {key.get_name()} {key.get_base64()}")
            client.close()
        except Exception as exc:  # noqa: BLE001
            print(f"  failed to connect {node.name} ({node.ip}): {exc}", file=sys.stderr)
    if not recorded:
        print("no host keys recorded; check VM reachability", file=sys.stderr)
        return 1
    lines = sorted(set(recorded))
    known_hosts.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"recorded {len(lines)} host-key entries to {known_hosts}")
    return 0
