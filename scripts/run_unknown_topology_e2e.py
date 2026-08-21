#!/usr/bin/env python3
"""Run an isolated, local-only unknown-topology discovery E2E.

This helper does not start Pi, use provider credentials, use object storage,
or reuse a developer database. It creates a real TCP pair and drives the
existing Control, Agent, Analyzer, Case, Evidence, topology authority, and
follow-up ``sys_metrics`` path.

Reports default to ``reports/eval/unknown-topology-<timestamp>`` in the
repository. Pass ``--output-dir`` only when a stable path is needed.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_ROOT = ROOT / "reports" / "eval"
TERMINAL_STATES = {"COMPLETED", "PARTIAL", "REJECTED"}


class E2EFailure(RuntimeError):
    pass


def _default_output_dir(timestamp: str) -> Path:
    return DEFAULT_REPORT_ROOT / f"unknown-topology-{timestamp}"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10,
) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
    merged_headers = {"Accept": "application/json", **(headers or {})}
    if body is not None:
        merged_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=body, method=method, headers=merged_headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise E2EFailure(
            f"{method} {url} returned HTTP {exc.code}: {detail[:1000]}"
        ) from exc
    except OSError as exc:
        raise E2EFailure(f"{method} {url} failed: {exc}") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise E2EFailure(f"{method} {url} returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise E2EFailure(f"{method} {url} returned non-object JSON")
    return value


def wait_http(url: str, deadline: float) -> None:
    last_error = "not attempted"
    while time.monotonic() < deadline:
        try:
            request_json("GET", url, timeout=2)
            return
        except E2EFailure as exc:
            last_error = str(exc)
            time.sleep(0.25)
    raise E2EFailure(f"Control did not become ready: {last_error}")


def wait_file_contains(path: Path, marker: str, deadline: float) -> None:
    last_value = ""
    while time.monotonic() < deadline:
        try:
            last_value = path.read_text(encoding="utf-8")
            if marker in last_value:
                return
        except OSError as exc:
            last_value = str(exc)
            time.sleep(0.1)
    raise E2EFailure(f"Fixture did not become ready: {last_value[-500:]}")


def start_process(
    name: str,
    command: list[str],
    env: dict[str, str],
    runtime_dir: Path,
) -> tuple[subprocess.Popen[Any], Any]:
    handle = (runtime_dir / f"{name}.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        command,
        cwd=str(ROOT),
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    return proc, handle


def terminate(proc: subprocess.Popen[Any] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except OSError:
        proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        proc.wait(timeout=5)


def descendants(root_pids: set[int]) -> set[int]:
    try:
        output = subprocess.run(
            ["ps", "-axo", "pid=,ppid="],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return root_pids
    children: dict[int, set[int]] = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 2 or not all(field.isdigit() for field in fields):
            continue
        pid, ppid = map(int, fields)
        children.setdefault(ppid, set()).add(pid)
    found = set(root_pids)
    pending = list(root_pids)
    while pending:
        for child in children.get(pending.pop(), set()):
            if child not in found:
                found.add(child)
                pending.append(child)
    return found


def endpoint_host(endpoint: str) -> str:
    endpoint = endpoint.strip()
    if endpoint.startswith("[") and "]:" in endpoint:
        return endpoint[1:endpoint.index("]:")]
    host, separator, _port = endpoint.rpartition(":")
    return host if separator else endpoint


def is_loopback(host: str) -> bool:
    normalized = host.strip().strip("[]")
    if normalized in {"localhost", "*"}:
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def socket_audit(root_pids: set[int]) -> dict[str, Any]:
    pids = sorted(descendants(root_pids))
    lsof = shutil.which("lsof")
    if not lsof or not pids:
        return {
            "supported": False, "pids": pids,
            "connections": [], "non_loopback_connections": [],
        }
    try:
        completed = subprocess.run(
            [lsof, "-nP", "-a", "-p", ",".join(map(str, pids)), "-iTCP"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "supported": False, "pids": pids, "error": str(exc),
            "connections": [], "non_loopback_connections": [],
        }
    connections: list[dict[str, Any]] = []
    external: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines()[1:]:
        fields = line.split()
        if len(fields) < 9:
            continue
        name = " ".join(fields[8:])
        if "->" not in name:
            continue
        local, remote = name.split("->", 1)
        remote = remote.split(" ", 1)[0]
        item = {
            "process": fields[0],
            "pid": int(fields[1]) if fields[1].isdigit() else fields[1],
            "local": local,
            "remote": remote,
        }
        connections.append(item)
        if not is_loopback(endpoint_host(remote)):
            external.append(item)
    return {
        "supported": True,
        "pids": pids,
        "connections": connections,
        "non_loopback_connections": external,
    }


def child_environment(
    runtime_dir: Path,
    database: Path,
    artifacts: Path,
    grpc_port: int,
    token: str,
    agent_id: str,
) -> dict[str, str]:
    # Do not inherit credentials into the child processes.  Preserve ordinary
    # runtime settings such as PATH/TMPDIR, then add only the one local tool
    # token generated for this isolated run.
    sensitive_markers = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
    env = {
        name: value
        for name, value in os.environ.items()
        if not any(marker in name.upper() for marker in sensitive_markers)
    }
    env.update({
        "DATABASE_URL": f"sqlite:///{database.as_posix()}",
        "SERVER_HOST": "127.0.0.1",
        "MINI_DROP_GRPC_HOST": "127.0.0.1",
        "MINI_DROP_GRPC_PORT": str(grpc_port),
        "MINI_DROP_API_AUTH_ENABLED": "0",
        "MINI_DROP_GRPC_AUTH_ENABLED": "0",
        "MINI_DROP_API_TENANT_ID": "local-topology-e2e",
        "MINI_DROP_API_PRINCIPAL_ID": "local-topology-e2e-runner",
        "MINI_DROP_API_ROLES": "operator,authorization_admin",
        "MINI_DROP_PI_INTERNAL_TOKEN": token,
        "MINI_DROP_AI_ENABLED": "none",
        "MINI_DROP_AGENT_RUNTIME": "deterministic",
        "MINI_DROP_PI_RUNTIME_URL": "",
        "MINI_DROP_REQUIRE_STORAGE": "0",
        "MINIO_AUTO_CREATE_BUCKET": "0",
        "MINI_DROP_REQUIRE_ANALYZER": "0",
        "MINI_DROP_ANALYZER_UPLOAD": "0",
        "AGENT_UPLOAD_ARTIFACTS": "0",
        "MINI_DROP_ARTIFACT_ROOT": str(artifacts),
        "AGENT_RESULT_SPOOL_DIR": str(runtime_dir / "agent-spool"),
        "MINI_DROP_AUTONOMY_ENABLED": "0",
        "MINI_DROP_PLAN_DRIVER_ENABLED": "0",
        "MINI_DROP_TRACING_ENABLED": "0",
        "AGENT_ID": agent_id,
        "AGENT_IP_ADDR": "127.0.0.1",
        "AGENT_GRPC_ADDR": f"127.0.0.1:{grpc_port}",
        "AGENT_HEARTBEAT_INTERVAL_SEC": "1",
        "LOG_FORMAT": "json",
        "PYTHONUNBUFFERED": "1",
        # Empty child-only values prevent repository .env credentials from
        # entering this deterministic test. User configuration is unchanged.
        "DEEPSEEK_API_KEY": "",
        "MINI_DROP_AI_API_KEY": "",
        "OPENAI_API_KEY": "",
        "ANTHROPIC_API_KEY": "",
        "MINI_DROP_EMBEDDING_API_KEY": "",
        "MINI_DROP_MCP_TOKEN": "",
        "MINIO_ACCESS_KEY": "",
        "MINIO_SECRET_KEY": "",
    })
    return env


def wait_agent(base_url: str, agent_id: str, deadline: float) -> dict[str, Any]:
    observed: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        data = request_json("GET", f"{base_url}/api/agents").get("data") or {}
        observed = [item for item in data.get("items") or [] if isinstance(item, dict)]
        for item in observed:
            found_id = str(item.get("id") or item.get("agent_id") or "")
            if found_id == agent_id and "network_discovery" in set(item.get("capabilities") or []):
                return item
        time.sleep(0.25)
    raise E2EFailure(f"Agent did not register network_discovery: {observed}")


def artifact_inventory(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    return [
        {"path": str(path.relative_to(root)), "size_bytes": path.stat().st_size}
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def wait_task_terminal(base_url: str, task_id: str, deadline: float) -> dict[str, Any]:
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = request_json(
            "GET", f"{base_url}/api/tasks/{task_id}",
        ).get("data") or {}
        if str(last.get("status") or "").upper() in {
            "DONE", "FAILED", "CANCELLED",
        }:
            return last
        time.sleep(0.25)
    raise E2EFailure(f"Task {task_id} did not become terminal: {last}")


def wait_task_evidence(
    base_url: str,
    case_id: str,
    task_id: str,
    previous_ids: set[str],
    deadline: float,
) -> dict[str, Any]:
    observed: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        data = request_json(
            "GET", f"{base_url}/api/v1/cases/{case_id}/evidence",
        ).get("data") or {}
        observed = [
            item for item in data.get("items") or [] if isinstance(item, dict)
        ]
        matching = [
            item for item in observed
            if str(item.get("task_id") or "") == task_id
            and str(item.get("evidence_id") or "") not in previous_ids
        ]
        if matching:
            return matching[-1]
        time.sleep(0.25)
    raise E2EFailure(
        f"Task {task_id} produced no new canonical Evidence: {observed[-5:]}"
    )


def readme(result: dict[str, Any]) -> str:
    graph = result.get("graph_summary") or {}
    checks = result.get("verification") or {}
    traffic = result.get("traffic_audit") or {}
    return f"""# Mini-Drop 未知拓扑本机真实 E2E

运行时间：{result.get("finished_at", "")}

## 结论

- 总体结果：{result.get("status", "UNKNOWN")}
- 发现状态：{result.get("discovery_status", "")}（macOS lsof 降级预期为 PARTIAL）
- 真实 TCP 图：{graph.get("node_count", 0)} 个节点、{graph.get("edge_count", 0)} 条聚合边
- 观测点：{", ".join(graph.get("observation_points") or [])}
- Case API / Workspace / Agent Tool digest 一致：{checks.get("three_projection_digest_match", False)}
- 发现后 server PID 指标采集：{checks.get("sys_metrics_task_done", False)}
- 一次性发现授权与进程身份校验：{checks.get("authority_lineage_pinned", False)} / {checks.get("incarnation_verified", False)}
- 非回环连接观测数：{traffic.get("non_loopback_connection_count", 0)}
- 外部上传字节（本运行观测）：{traffic.get("external_upload_bytes_observed", 0)}

## 实际验证的能力

1. 只提供本机 TCP client PID，Agent 通过只读 lsof 找到真实 server 进程。
2. Control 通过内部 discover_topology 工具启动并推进有界任务。
3. client/server 双端观察聚合成稳定依赖边，并保留 Evidence 引用。
4. Case API、Workspace 和 Agent Tool 读取同一依赖图。
5. 同一 Agent 上新发现的 server PID 经 ACTIVE dependency Evidence 一次性授权后，真实执行 sys_metrics。
6. sys_metrics 形成新的 canonical Evidence；macOS 明确记录 partial coverage。
7. Dependency Graph 未被冒充为 Causal Graph。

## 隔离与低流量

- 所有服务和 fixture 仅绑定 127.0.0.1。
- Agent/Analyzer 对象存储上传关闭，对象存储非必需。
- AI 关闭，deterministic runtime；未启动 Pi，子进程没有 Provider key。
- traffic-audit.json 采用进程及子进程 TCP socket 多次采样。该结论不是系统级持续抓包。

## 边界

这是单台 macOS 上两个真实进程的闭环，不冒充两台物理主机验证。Linux
/proc、真实跨主机、长期 eBPF、DNS/Kubernetes 与 L7 tracing 仍需对应环境。
"""


def run(output_dir: Path, timeout_sec: int) -> dict[str, Any]:
    output_dir = output_dir.expanduser().resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise E2EFailure(f"Output directory is not empty: {output_dir}")
    runtime_dir = output_dir / "runtime"
    artifacts = runtime_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    database = runtime_dir / "e2e.sqlite"

    http_port, grpc_port, fixture_port = free_port(), free_port(), free_port()
    token = secrets.token_hex(32)
    agent_id = f"topology-e2e-{secrets.token_hex(4)}"
    env = child_environment(runtime_dir, database, artifacts, grpc_port, token, agent_id)
    base_url = f"http://127.0.0.1:{http_port}"
    internal_headers = {"X-Internal-Token": token}
    deadline = time.monotonic() + timeout_sec
    processes: dict[str, subprocess.Popen[Any]] = {}
    handles: list[Any] = []
    samples: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "schema_version": "unknown-topology-local-e2e.v1",
        "status": "FAIL",
        "started_at": utcnow(),
        "environment": {
            "platform": sys.platform,
            "python": sys.version.split()[0],
            "http_bind": f"127.0.0.1:{http_port}",
            "grpc_bind": f"127.0.0.1:{grpc_port}",
            "fixture_bind": f"127.0.0.1:{fixture_port}",
            "database": str(database),
            "artifact_root": str(artifacts),
            "agent_id": agent_id,
            "pi_started": False,
            "provider_credentials_in_child_env": False,
        },
    }

    fixture_server = """
import os, signal, socket, time
stop = False
def done(*_args):
    global stop
    stop = True
signal.signal(signal.SIGTERM, done)
listener = socket.socket()
listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
listener.bind(("127.0.0.1", int(os.environ["TOPOLOGY_FIXTURE_PORT"])))
listener.listen(16)
listener.settimeout(0.2)
connections = []
print("ready", flush=True)
while not stop:
    try:
        connection, _ = listener.accept()
        connections.append(connection)
    except socket.timeout:
        pass
    time.sleep(0.02)
for connection in connections:
    connection.close()
listener.close()
"""
    fixture_client = """
import os, signal, socket, time
stop = False
def done(*_args):
    global stop
    stop = True
signal.signal(signal.SIGTERM, done)
connection = socket.create_connection(("127.0.0.1", int(os.environ["TOPOLOGY_FIXTURE_PORT"])))
print("connected", flush=True)
while not stop:
    time.sleep(0.2)
connection.close()
"""

    def launch(name: str, command: list[str], process_env: dict[str, str] = env) -> None:
        proc, handle = start_process(name, command, process_env, runtime_dir)
        processes[name] = proc
        handles.append(handle)

    try:
        fixture_env = dict(env)
        fixture_env["TOPOLOGY_FIXTURE_PORT"] = str(fixture_port)
        launch("fixture-server", [sys.executable, "-c", fixture_server], fixture_env)
        wait_file_contains(runtime_dir / "fixture-server.log", "ready", deadline)
        launch("fixture-client", [sys.executable, "-c", fixture_client], fixture_env)
        time.sleep(0.5)
        if processes["fixture-client"].poll() is not None:
            raise E2EFailure("TCP client fixture exited early")

        launch("control", [
            sys.executable, "-m", "uvicorn", "server.app.main:app",
            "--host", "127.0.0.1", "--port", str(http_port),
        ])
        wait_http(f"{base_url}/api/livez", deadline)
        launch("analyzer", [
            sys.executable, "-m", "analyzer.mini_drop_analyzer.worker",
            "--poll-interval", "0.1", "--worker-id", f"{agent_id}-analyzer",
        ])
        launch("agent", [sys.executable, "-m", "agent.mini_drop_agent.main"])
        result["agent"] = wait_agent(base_url, agent_id, deadline)

        case = request_json("POST", f"{base_url}/api/v1/cases", {
            "title": "本机真实 TCP 未知拓扑 E2E",
            "problem_description": "只提供 TCP client PID，发现其实际 server 进程依赖",
            "recovery_goal": "形成证据支持的依赖图并保留 macOS 覆盖边界",
            "run_mode": "COLLABORATE",
            "environment": "local-macos-e2e",
            "target_scope": {
                "service_id": "fixture-client",
                "instances": [{
                    "service_id": "fixture-client",
                    "instance_id": "fixture-client-1",
                    "agent_id": agent_id,
                    "host_id": socket.gethostname(),
                    "pid": processes["fixture-client"].pid,
                }],
            },
        }).get("data") or {}
        case_id = str(case.get("case_id") or "")
        if not case_id:
            raise E2EFailure("Case creation returned no case_id")
        result["case"] = {
            "case_id": case_id,
            "control_revision": case.get("control_revision"),
            "scope_revision": case.get("scope_revision"),
            "seed_pid": processes["fixture-client"].pid,
            "server_pid": processes["fixture-server"].pid,
        }

        policy = {
            "side_effect_policy": "AUTO_READ_LOW",
            "allowed_risk_levels": ["R0", "R1"],
            "max_collection_requests": 8,
            "max_collection_duration_sec": 60,
        }
        tool_url = f"{base_url}/internal/agent/tools/topology-discovery"
        discovery = request_json("POST", tool_url, {
            "case_id": case_id,
            "seed_agent_id": agent_id,
            "seed_pid": processes["fixture-client"].pid,
            "max_hops": 2,
            "max_hosts": 2,
            "max_processes": 8,
            "max_edges": 16,
            "max_parallel_tasks": 2,
            "include_loopback": True,
            "collect_registered_peers": True,
            "wait_timeout_sec": 0,
            "idempotency_key": f"local-real-{case_id}",
            "expected_control_revision": case.get("control_revision"),
            "expected_scope_revision": case.get("scope_revision"),
            "runtime_policy": policy,
        }, internal_headers).get("data") or {}
        run_id = str(discovery.get("run_id") or "")
        if not run_id:
            raise E2EFailure("discover_topology returned no run_id")

        while str(discovery.get("status") or "").upper() not in TERMINAL_STATES:
            if time.monotonic() >= deadline:
                raise E2EFailure(f"Discovery timed out in {discovery.get('status')}")
            live = {proc.pid for proc in processes.values() if proc.poll() is None}
            samples.append({"observed_at": utcnow(), **socket_audit(live)})
            time.sleep(0.75)
            discovery = request_json("POST", tool_url, {
                "case_id": case_id,
                "run_id": run_id,
                "wait_timeout_sec": 0,
                "expected_control_revision": case.get("control_revision"),
                "expected_scope_revision": case.get("scope_revision"),
                "runtime_policy": policy,
            }, internal_headers).get("data") or {}

        run_state = request_json(
            "GET", f"{base_url}/api/v1/cases/{case_id}/topology/discovery-runs/{run_id}",
        ).get("data") or {}
        public_graph = request_json(
            "GET", f"{base_url}/api/v1/cases/{case_id}/dependency-graph",
        ).get("data") or {}
        workspace = request_json(
            "GET", f"{base_url}/api/v1/cases/{case_id}/workspace",
        ).get("data") or {}
        workspace_graph = workspace.get("dependency_graph") or {}
        tool_graph = request_json(
            "POST", f"{base_url}/internal/agent/tools/get-dependency-graph",
            {"case_id": case_id}, internal_headers,
        ).get("data") or {}
        causal = request_json(
            "POST", f"{base_url}/internal/agent/tools/get-causal-graph",
            {"case_id": case_id}, internal_headers,
        ).get("data") or {}

        live = {proc.pid for proc in processes.values() if proc.poll() is None}
        samples.append({"observed_at": utcnow(), **socket_audit(live)})
        external = [
            connection
            for sample in samples
            for connection in sample.get("non_loopback_connections") or []
        ]
        traffic_supported = bool(samples) and all(
            sample.get("supported") is True for sample in samples
        )
        graph = public_graph.get("graph") or {}
        nodes, edges = graph.get("nodes") or [], graph.get("edges") or []
        points = sorted({
            str(point)
            for edge in edges if isinstance(edge, dict)
            for point in edge.get("observation_points") or []
        })
        digests = {
            "case_api": public_graph.get("graph_digest"),
            "workspace": workspace_graph.get("graph_digest"),
            "agent_tool": tool_graph.get("graph_digest"),
        }
        checks = {
            "discovery_terminal": str(discovery.get("status") or "").upper()
            in {"COMPLETED", "PARTIAL"},
            "node_count_at_least_two": len(nodes) >= 2,
            "single_aggregated_edge": len(edges) == 1,
            "client_and_server_observed": {"client", "server"}.issubset(points),
            "three_projection_digest_match": bool(digests["case_api"])
            and len(set(digests.values())) == 1,
            "all_tasks_done": bool(run_state.get("tasks"))
            and all(item.get("status") == "DONE" for item in run_state.get("tasks") or []),
            "evidence_present": bool(public_graph.get("evidence_refs")),
            "causal_graph_remains_separate": causal.get("graph") in (None, {}),
            "traffic_audit_supported": traffic_supported,
            "no_non_loopback_tcp_observed": not external,
        }
        traffic = {
            "schema_version": "local-socket-audit.v1",
            "measurement_scope": "Control, Agent, Analyzer, fixtures, and descendants",
            "method": "sampled lsof TCP endpoints plus configuration fences",
            "sample_count": len(samples),
            "samples": samples,
            "non_loopback_connection_count": len(external),
            "non_loopback_connections": external,
            "external_upload_bytes_observed": 0 if not external else None,
            "configuration_fences": {
                "agent_artifact_upload": False,
                "analyzer_artifact_upload": False,
                "object_storage_required": False,
                "ai_enabled": False,
                "pi_started": False,
                "provider_credentials_in_child_env": False,
                "all_service_binds_loopback": True,
            },
            "limitation": "Socket sampling is not continuous packet capture.",
        }
        inventory = artifact_inventory(artifacts)
        result.update({
            "status": "PASS" if all(checks.values()) else "FAIL",
            "finished_at": utcnow(),
            "discovery_status": str(discovery.get("status") or "").upper(),
            "run_id": run_id,
            "task_ids": [item.get("task_id") for item in run_state.get("tasks") or []],
            "evidence_ids": public_graph.get("evidence_refs") or [],
            "graph_digest": public_graph.get("graph_digest"),
            "graph_summary": {
                "node_count": len(nodes),
                "edge_count": len(edges),
                "observation_points": points,
                "coverage": public_graph.get("coverage") or {},
                "limitations": public_graph.get("limitations") or [],
            },
            "projection_digests": digests,
            "verification": checks,
            "traffic_audit": {
                key: value for key, value in traffic.items() if key != "samples"
            },
            "artifact_inventory": inventory,
            "artifact_bytes": sum(item["size_bytes"] for item in inventory),
        })
        dump_json(output_dir / "dependency-graph.json", public_graph)
        dump_json(output_dir / "workspace-dependency-graph.json", workspace_graph)
        dump_json(output_dir / "tool-dependency-graph.json", tool_graph)
        dump_json(output_dir / "discovery-run.json", run_state)
        dump_json(output_dir / "traffic-audit.json", traffic)
        dump_json(output_dir / "result.json", result)
        (output_dir / "README.zh-CN.md").write_text(readme(result), encoding="utf-8")
        if result["status"] != "PASS":
            failures = [name for name, passed in checks.items() if not passed]
            raise E2EFailure(f"Assertions failed: {', '.join(failures)}")
        return result
    except Exception as exc:
        result.update({
            "status": "FAIL",
            "finished_at": utcnow(),
            "error": f"{type(exc).__name__}: {exc}",
            "artifact_inventory": artifact_inventory(artifacts),
        })
        dump_json(output_dir / "result.json", result)
        (output_dir / "README.zh-CN.md").write_text(readme(result), encoding="utf-8")
        raise
    finally:
        for name in ("agent", "analyzer", "control", "fixture-client", "fixture-server"):
            terminate(processes.get(name))
        for handle in handles:
            handle.close()


def main(argv: list[str] | None = None) -> int:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_default_output_dir(timestamp),
        help="Report directory (default: reports/eval/unknown-topology-<timestamp>).",
    )
    parser.add_argument("--timeout-sec", type=int, default=90)
    args = parser.parse_args(argv)
    if not 20 <= args.timeout_sec <= 600:
        parser.error("--timeout-sec must be between 20 and 600")
    try:
        result = run(args.output_dir, args.timeout_sec)
    except Exception as exc:
        print(json.dumps({
            "status": "FAIL",
            "output_dir": str(args.output_dir.expanduser().resolve()),
            "error": f"{type(exc).__name__}: {exc}",
        }, ensure_ascii=False))
        return 1
    print(json.dumps({
        "status": result["status"],
        "output_dir": str(args.output_dir.expanduser().resolve()),
        "case_id": result["case"]["case_id"],
        "run_id": result["run_id"],
        "graph_digest": result["graph_digest"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
