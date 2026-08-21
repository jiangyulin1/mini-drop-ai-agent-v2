#!/usr/bin/env python3
"""Run one isolated, real Pi/DeepSeek unknown-topology E2E.

The existing developer Control/Pi processes are deliberately not reused.  A
second loopback-only Control and Pi sidecar are started with a fresh SQLite
database and a generated internal token.  The configured DeepSeek key is read
only into the sidecar child environment; it is never written to an artifact,
printed, or included in the report.

This is an acceptance runner, not a synthetic scorer.  It records the actual
tool sequence, durable Evidence wakeup, dependency projections, model usage,
and low-bandwidth socket observations so a reviewer can judge the result.

Reports default to ``reports/eval/unknown-topology-pi-<timestamp>``. On a
successful run, raw runtime state (SQLite, process logs, event spool and
artifacts) is removed after scrubbed logs are written at the report root.
Failures retain runtime state for diagnosis; use ``--keep-runtime`` to retain
it after success as well.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# When invoked as ``python scripts/<runner>.py`` Python puts ``scripts/``
# (rather than the repository root) on sys.path.  Add the root before importing
# the deterministic runner's small, side-effect-free helpers.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_unknown_topology_e2e import (
    E2EFailure,
    artifact_inventory,
    dump_json,
    free_port,
    request_json,
    socket_audit,
    start_process,
    terminate,
    wait_file_contains,
    wait_http,
)


SIDEcar_ROOT = ROOT / "agent_runtime" / "pi-sidecar"
DEFAULT_REPORT_ROOT = ROOT / "reports" / "eval"


def _default_output_dir(timestamp: str) -> Path:
    return DEFAULT_REPORT_ROOT / f"unknown-topology-pi-{timestamp}"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _credential_from_file(path: Path) -> str:
    """Read one allowlisted credential without ever logging its value."""
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            if raw.startswith("DEEPSEEK_API_KEY:"):
                value = raw.split(":", 1)[1].strip().strip("\"'")
                if value:
                    return value
    except OSError:
        pass
    return ""


def _base_environment(
    runtime_dir: Path,
    database: Path,
    artifacts: Path,
    grpc_port: int,
    agent_id: str,
) -> dict[str, str]:
    # Never inherit ambient credentials into Control/Agent/fixture children.
    sensitive = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
    env = {
        key: value for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in sensitive)
    }
    env.update({
        "DATABASE_URL": f"sqlite:///{database.as_posix()}",
        "SERVER_HOST": "127.0.0.1",
        "MINI_DROP_GRPC_HOST": "127.0.0.1",
        "MINI_DROP_GRPC_PORT": str(grpc_port),
        "MINI_DROP_API_AUTH_ENABLED": "0",
        "MINI_DROP_GRPC_AUTH_ENABLED": "0",
        "MINI_DROP_API_TENANT_ID": "real-topology-e2e",
        "MINI_DROP_API_PRINCIPAL_ID": "real-topology-e2e-runner",
        "MINI_DROP_API_ROLES": "operator,authorization_admin",
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
        "MINI_DROP_EMBEDDING_PROVIDER": "lexical",
        "AGENT_ID": agent_id,
        "AGENT_IP_ADDR": "127.0.0.1",
        "AGENT_GRPC_ADDR": f"127.0.0.1:{grpc_port}",
        "AGENT_HEARTBEAT_INTERVAL_SEC": "1",
        "LOG_FORMAT": "json",
        "PYTHONUNBUFFERED": "1",
        # Explicitly blank other providers in all non-Pi children.
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


def _nettop_bytes(pids: set[int]) -> dict[str, Any]:
    """Take one aggregate macOS byte sample; no packet payload is captured."""
    nettop = shutil.which("nettop")
    if not nettop or not pids:
        return {"supported": False, "bytes_in": 0, "bytes_out": 0}
    try:
        command = [nettop, "-n", "-P", "-L", "1", "-x", "-J", "bytes_in,bytes_out"]
        for pid in sorted(pids):
            command.extend(["-p", str(pid)])
        completed = subprocess.run(command, capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"supported": False, "bytes_in": 0, "bytes_out": 0, "error": str(exc)}
    total_in = total_out = 0
    for line in completed.stdout.splitlines()[1:]:
        fields = [item.strip() for item in line.split(",")]
        if len(fields) < 3:
            continue
        try:
            total_in += max(0, int(fields[-2]))
            total_out += max(0, int(fields[-1]))
        except ValueError:
            continue
    return {
        "supported": completed.returncode == 0,
        "bytes_in": total_in,
        "bytes_out": total_out,
    }


def _scrub_log(path: Path, output: Path, secrets_to_hide: list[str]) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for secret in secrets_to_hide:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    # Avoid accidentally persisting a prompt or unbounded SDK diagnostic.
    output.write_text(text[-12000:], encoding="utf-8")


def _apply_runtime_retention(
    runtime_dir: Path,
    *,
    succeeded: bool,
    keep_runtime: bool,
) -> dict[str, Any]:
    if succeeded and not keep_runtime:
        if runtime_dir.exists():
            shutil.rmtree(runtime_dir)
        return {
            "keep_requested": False,
            "retained": False,
            "reason": "successful_run_cleanup",
        }
    return {
        "keep_requested": keep_runtime,
        "retained": runtime_dir.exists(),
        "reason": "explicit_keep" if keep_runtime else "failed_run_diagnostics",
    }


def _assistant_message(events: list[dict[str, Any]]) -> str:
    def text_from(value: Any) -> str:
        if isinstance(value, str):
            candidate = value.strip()
            if not candidate:
                return ""
            # Pi stores the assistant envelope as a JSON string in
            # ``message``.  Decode it before falling back to plain text.
            if candidate.startswith(("{", "[")):
                try:
                    decoded = json.loads(candidate)
                except (TypeError, ValueError):
                    decoded = None
                if decoded is not None:
                    extracted = text_from(decoded)
                    if extracted:
                        return extracted
                    # A successfully decoded non-assistant envelope (for
                    # example the automatic terminal reminder) is not visible
                    # assistant text.  Never fall back to its raw JSON.
                    return ""
            return candidate
        if isinstance(value, dict):
            role = str(value.get("role") or "").strip().lower()
            if role and role != "assistant":
                return ""
            for key in ("visible_text", "text", "content", "message"):
                extracted = text_from(value.get(key))
                if extracted:
                    return extracted
            return ""
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                if isinstance(item, dict) and item.get("type") in {"toolCall", "toolResult", "thinking"}:
                    continue
                extracted = text_from(item)
                if extracted:
                    parts.append(extracted)
            return "\n".join(parts)
        return ""

    for event in reversed(events):
        payload = event.get("payload") or {}
        if event.get("event_type") in {"turn_end", "assistant.message", "message_end"}:
            text = text_from(payload.get("visible_text") or payload.get("content")
                             or payload.get("text") or payload.get("message"))
            if text:
                return text[:12000]
    return ""


def _tool_sequence(events: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for event in events:
        payload = event.get("payload") or {}
        name = payload.get("tool_name") or payload.get("toolName")
        if name and str(name) not in names:
            names.append(str(name))
    return names


def _model_attempts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    attempts = []
    for event in events:
        payload = event.get("payload") or {}
        attempt = payload.get("model_attempt")
        if isinstance(attempt, dict):
            # Keep only non-sensitive, auditable usage fields.
            record = {
                key: attempt.get(key)
                for key in (
                    "provider", "model", "status", "latency_ms", "input_tokens",
                    "output_tokens", "cache_read_tokens", "cache_write_tokens",
                    "retry_count", "turn_id", "prompt_version", "config_fingerprint",
                    "response_hash", "model_attempt_id",
                )
                if key in attempt
            }
            record.update({
                "event_seq": event.get("event_seq"),
                "event_type": event.get("event_type"),
                "trigger_turn_id": payload.get("trigger_turn_id"),
            })
            attempts.append(record)
    return attempts


def _model_completion_audit(
    events: list[dict[str, Any]],
    main_turn_id: str,
) -> dict[str, Any]:
    """Separate the accepted Turn's provider calls from terminal reminders."""

    def turn_id_for(event: dict[str, Any]) -> str:
        payload = event.get("payload") or {}
        attempt = payload.get("model_attempt") or {}
        return str(attempt.get("turn_id") or payload.get("trigger_turn_id") or "")

    def is_real_completion(event: dict[str, Any]) -> bool:
        payload = event.get("payload") or {}
        attempt = payload.get("model_attempt")
        if not isinstance(attempt, dict):
            return False
        response_hash = str(attempt.get("response_hash") or "")
        output_tokens = attempt.get("output_tokens")
        try:
            has_output = int(output_tokens or 0) > 0
        except (TypeError, ValueError):
            has_output = False
        return (
            event.get("event_type") in {"message_end", "turn_end"}
            and attempt.get("provider") == "deepseek"
            and attempt.get("model") == "deepseek-v4-flash"
            and attempt.get("status") == "SUCCEEDED"
            and bool(re.fullmatch(r"[0-9a-f]{64}", response_hash))
            and has_output
        )

    completions = [event for event in events if is_real_completion(event)]
    main_completions = [
        event for event in completions if turn_id_for(event) == main_turn_id
    ]
    reminder_ids = sorted({
        turn_id_for(event)
        for event in events
        if "-terminal-" in turn_id_for(event)
    })
    reminder_runs: list[dict[str, Any]] = []
    for reminder_id in reminder_ids:
        items = [event for event in events if turn_id_for(event) == reminder_id]
        event_types = [str(event.get("event_type") or "") for event in items]
        completed_tools = [
            str((event.get("payload") or {}).get("tool_name") or "")
            for event in items
            if event.get("event_type") == "tool_execution_end"
        ]
        reminder_completions = [event for event in completions if turn_id_for(event) == reminder_id]
        settled = "agent_settled" in event_types or (
            "agent_end" in event_types and "turn_end" in event_types
        )
        finish_completed = "finish_investigation" in completed_tools
        reminder_runs.append({
            "turn_id": reminder_id,
            "event_count": len(items),
            "event_types": event_types,
            "real_completion_count": len(reminder_completions),
            "finish_investigation_completed": finish_completed,
            "settled": settled,
            "resolved": bool(reminder_completions) and finish_completed and settled,
        })
    return {
        "main_turn_id": main_turn_id,
        "main_turn_real_completion": bool(main_completions),
        "main_turn_completion_count": len(main_completions),
        "terminal_reminder_started": bool(reminder_runs),
        "terminal_reminder_resolved": all(
            item["resolved"] for item in reminder_runs
        ),
        "terminal_reminders": reminder_runs,
    }


def _runtime_event_audit(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Check durable cursor integrity and lifecycle closure without payload output."""
    ordered = sorted(events, key=lambda item: int(item.get("event_seq") or 0))
    seqs = [int(item.get("event_seq") or 0) for item in ordered]
    expected = list(range(1, (seqs[-1] if seqs else 0) + 1))
    event_ids = [str(item.get("event_id") or "") for item in ordered]
    idempotency_keys = [str(item.get("idempotency_key") or "") for item in ordered]
    turn_ids = sorted({
        str((item.get("payload") or {}).get("trigger_turn_id") or "")
        for item in ordered
        if (item.get("payload") or {}).get("trigger_turn_id")
    })
    unfinished_turn_ids: list[str] = []
    turn_lifecycle: list[dict[str, Any]] = []
    for turn_id in turn_ids:
        items = [
            item for item in ordered
            if str((item.get("payload") or {}).get("trigger_turn_id") or "") == turn_id
        ]
        types = [str(item.get("event_type") or "") for item in items]
        turn_starts, turn_ends = types.count("turn_start"), types.count("turn_end")
        agent_starts = types.count("agent_start")
        agent_closed = types.count("agent_end") + types.count("agent_settled")
        unfinished = turn_starts > turn_ends or agent_starts > agent_closed
        if unfinished:
            unfinished_turn_ids.append(turn_id)
        turn_lifecycle.append({
            "turn_id": turn_id,
            "turn_start_count": turn_starts,
            "turn_end_count": turn_ends,
            "agent_start_count": agent_starts,
            "agent_close_count": agent_closed,
            "unfinished": unfinished,
        })
    return {
        "event_count": len(ordered),
        "first_seq": seqs[0] if seqs else None,
        "last_seq": seqs[-1] if seqs else None,
        "seq_contiguous": bool(seqs) and seqs == expected,
        "event_ids_unique": (
            bool(event_ids)
            and all(event_ids)
            and len(event_ids) == len(set(event_ids))
        ),
        "idempotency_keys_unique": (
            bool(idempotency_keys)
            and all(idempotency_keys)
            and len(idempotency_keys) == len(set(idempotency_keys))
        ),
        "unfinished_turn_ids": unfinished_turn_ids,
        "no_unfinished_turns": not unfinished_turn_ids,
        "turn_lifecycle": turn_lifecycle,
    }


def _read_runtime_events(
    base_url: str,
    case_id: str,
    headers: dict[str, str],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    after_seq = 0
    while True:
        data = request_json(
            "GET",
            f"{base_url}/internal/runtime/v1/cases/{case_id}/events"
            f"?after_seq={after_seq}&limit=1000",
            headers=headers,
        ).get("data") or {}
        page = [item for item in (data.get("items") or []) if isinstance(item, dict)]
        if not page:
            break
        items.extend(page)
        next_seq = max(int(item.get("event_seq") or 0) for item in page)
        if next_seq <= after_seq:
            raise E2EFailure("Runtime event cursor did not advance")
        after_seq = next_seq
        if len(page) < 1000:
            break
    return items


def _sqlite_conclusion_count(database: Path) -> int:
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5)
    try:
        row = connection.execute("SELECT COUNT(*) FROM conclusion_revisions").fetchone()
        return int(row[0] if row else 0)
    finally:
        connection.close()


def _read_events(base_url: str, case_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
    data = request_json(
        "GET",
        f"{base_url}/api/v1/cases/{case_id}/events?after_seq={after_seq}&limit=200",
    ).get("data") or {}
    return [item for item in (data.get("items") or []) if isinstance(item, dict)]


def run(
    output_dir: Path,
    timeout_sec: int,
    poll_interval_sec: int,
    credential_file: Path,
    *,
    keep_runtime: bool = False,
) -> dict[str, Any]:
    output_dir = output_dir.expanduser().resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise E2EFailure(f"Output directory is not empty: {output_dir}")
    runtime_dir = output_dir / "runtime"
    artifacts = runtime_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    database = runtime_dir / "e2e.sqlite"
    http_port, grpc_port, sidecar_port, fixture_port = (
        free_port(), free_port(), free_port(), free_port()
    )
    internal_token = secrets.token_hex(32)
    agent_id = f"real-topology-e2e-{secrets.token_hex(4)}"
    key = os.getenv("DEEPSEEK_API_KEY", "") or _credential_from_file(credential_file)
    if not key:
        raise E2EFailure("Configured DeepSeek credential was not found; no model request was sent")

    base_env = _base_environment(runtime_dir, database, artifacts, grpc_port, agent_id)
    control_env = dict(base_env)
    control_env.update({
        "MINI_DROP_AGENT_RUNTIME": "pi",
        "MINI_DROP_PI_RUNTIME_URL": f"http://127.0.0.1:{sidecar_port}",
        "MINI_DROP_PI_INTERNAL_TOKEN": internal_token,
        "MINI_DROP_WAKEUP_INTERVAL_SEC": "2",
        "MINI_DROP_OUTBOX_RELAY_INTERVAL_SEC": "1",
        "MINI_DROP_WAKEUP_QUIET_SEC": "1",
    })
    agent_env = dict(base_env)
    sidecar_env = dict(base_env)
    sidecar_env.update({
        "DEEPSEEK_API_KEY": key,
        "MINI_DROP_PI_MODEL_PROVIDER": "deepseek",
        "MINI_DROP_PI_MODEL": "deepseek-v4-flash",
        "MINI_DROP_PI_THINKING_LEVEL": "low",
        "MINI_DROP_PI_CONTEXT_MAX_CHARS": "8000",
        "MINI_DROP_PI_RESET_SESSION_PER_TURN": "1",
        "MINI_DROP_PI_EVENT_SPOOL_PATH": str(runtime_dir / "pi-events.jsonl"),
        "PI_OFFLINE": "1",
        "PI_TELEMETRY": "0",
        "MINI_DROP_PI_INTERNAL_TOKEN": internal_token,
        "MINI_DROP_PI_SIDECAR_HOST": "127.0.0.1",
        "MINI_DROP_PI_SIDECAR_PORT": str(sidecar_port),
        "MINI_DROP_PI_INTERNAL_BASE": f"http://127.0.0.1:{http_port}",
    })
    base_url = f"http://127.0.0.1:{http_port}"
    internal_headers = {"X-Internal-Token": internal_token}
    deadline = time.monotonic() + timeout_sec
    processes: dict[str, subprocess.Popen[Any]] = {}
    handles: list[Any] = []
    traffic_samples: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "schema_version": "unknown-topology-real-pi-e2e.v1",
        "status": "FAIL",
        "started_at": utcnow(),
        "environment": {
            "platform": sys.platform,
            "python": sys.version.split()[0],
            "http_bind": f"127.0.0.1:{http_port}",
            "grpc_bind": f"127.0.0.1:{grpc_port}",
            "sidecar_bind": f"127.0.0.1:{sidecar_port}",
            "fixture_bind": f"127.0.0.1:{fixture_port}",
            "database": str(database),
            "artifact_root": str(artifacts),
            "agent_id": agent_id,
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "pi_started": True,
            "credential_value_recorded": False,
            "credential_source": "configured_0600_file_or_inherited_process_env",
            "embedding_provider": "lexical",
            "agent_artifact_upload": False,
            "analyzer_artifact_upload": False,
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

    def launch(name: str, command: list[str], env: dict[str, str]) -> None:
        proc, handle = start_process(name, command, env, runtime_dir)
        processes[name] = proc
        handles.append(handle)

    try:
        fixture_env = dict(base_env)
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
        ], control_env)
        wait_http(f"{base_url}/api/livez", deadline)

        # The analyzer is part of the real durable Task -> Evidence path. It
        # never uploads artifacts in this run, but without it a completed
        # collector remains ANALYZING and the graph cannot be materialized.
        launch("analyzer", [
            sys.executable, "-m", "analyzer.mini_drop_analyzer.worker",
            "--poll-interval", "0.1", "--worker-id", f"{agent_id}-analyzer",
        ], base_env)

        launch("sidecar", [str(shutil.which("node") or "node"), str(SIDEcar_ROOT / "src" / "server.mjs")], sidecar_env)
        wait_http(f"http://127.0.0.1:{sidecar_port}/internal/runtime/v1/health", deadline)

        launch("agent", [sys.executable, "-m", "agent.mini_drop_agent.main"], agent_env)
        # Agent registration is visible through the normal API and confirms
        # that the real collector path is available before the Turn starts.
        observed_agents: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            data = request_json("GET", f"{base_url}/api/agents").get("data") or {}
            observed_agents = [item for item in (data.get("items") or []) if isinstance(item, dict)]
            match = next((item for item in observed_agents if str(item.get("id") or item.get("agent_id") or "") == agent_id), None)
            if match and "network_discovery" in set(match.get("capabilities") or []):
                result["agent"] = match
                break
            time.sleep(0.5)
        else:
            raise E2EFailure(f"Real Agent did not register network_discovery: {observed_agents}")

        case = request_json("POST", f"{base_url}/api/v1/cases", {
            "title": "真实 DeepSeek 未知拓扑调查",
            "problem_description": "只提供 TCP client PID，使用受控 Agent 工具发现真实上下游",
            "recovery_goal": "输出带 Evidence 引用和覆盖边界的依赖图；不把依赖当作因果",
            "run_mode": "COLLABORATE",
            "environment": "local-macos-real-pi",
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

        turn_payload = {
            "message": (
                "只做一次真实未知拓扑调查。你只有这个 Case 提供的 client PID；"
                "必须先读取 Case Snapshot，再调用 discover_topology 找到 client 的真实上下游。"
                "fixture client 与 server 都在 127.0.0.1，请在 discover_topology 的首次调用中显式设置 include_loopback=true；"
                "完成后读取 dependency graph，从图中取得新发现 server 进程的精确 agent_id、pid 和 entity_id。"
                "随后必须只调用一次 propose_collection，用 network_discovery 对这个 server PID 做 target scope、"
                "include_loopback=true 的低开销复核；必须携带 discover_topology 返回的原始 discovery_run_id、"
                "ACTIVE evidence_ids、当前 control/scope revision，information_goal 使用目录中的"
                "“从未知 PID 发现一跳 TCP 上下游”。不要对原始 client PID 重复采集，也不要调用其他采集器。"
                "采集调度后等待 Evidence 自动唤醒；读取新 Evidence，再通过 finish_investigation 提交终态。"
                "最终明确 client/server 双端观测、Evidence 引用和 macOS 覆盖限制；不要把通信依赖写成因果根因。"
            ),
            "intent": "investigate",
            "requested_disposition": "INVESTIGATE",
            "execute_safe_tools": True,
            "client_command_id": "real-topology-e2e-1",
            "runtime_policy": {
                "side_effect_policy": "AUTO_READ_LOW",
                "allowed_risk_levels": ["R0", "R1"],
                "max_collection_requests": 5,
                "max_collection_duration_sec": 70,
            },
            "runtime_options": {
                "fresh_session": True,
                "reasoning_effort": "low",
                "prompt_variant": "evidence_strict",
                "max_tokens": 768,
            },
        }
        accepted = request_json("POST", f"{base_url}/api/v1/cases/{case_id}/agent/turn", turn_payload).get("data") or {}
        result["turn_acceptance"] = {
            key: accepted.get(key)
            for key in ("turn_id", "accepted", "mode", "detail", "status")
            if key in accepted
        }
        turn_id = str(accepted.get("turn_id") or "")
        if not turn_id:
            raise E2EFailure(f"Agent turn was not accepted: {accepted}")

        last_seq = 0
        event_batches: list[dict[str, Any]] = []
        terminal_seen = False
        verified_conclusion: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            live = {proc.pid for proc in processes.values() if proc.poll() is None}
            socket_sample = socket_audit(live)
            socket_sample["process_bytes"] = _nettop_bytes(live)
            socket_sample["observed_at"] = utcnow()
            traffic_samples.append(socket_sample)
            state = request_json("GET", f"{base_url}/api/v1/cases/{case_id}/agent/runtime-state").get("data") or {}
            events = _read_events(base_url, case_id, last_seq)
            if events:
                last_seq = max(last_seq, max(int(item.get("case_event_seq") or 0) for item in events))
                event_batches.append({"observed_at": utcnow(), "events": events})
            topology = request_json(
                "GET", f"{base_url}/api/v1/cases/{case_id}/topology/discovery-runs/{result.get('discovery_run_id', '')}",
            ) if result.get("discovery_run_id") else None
            # Discover the run ID from the first persisted tool event if the
            # model has already called the tool.
            if not result.get("discovery_run_id"):
                for batch in event_batches:
                    for item in batch.get("events") or []:
                        payload = item.get("payload") or {}
                        text_blob = json.dumps(payload, ensure_ascii=False)
                        match = re.search(r"discovery-[0-9a-f]{20}", text_blob)
                        if match:
                            result["discovery_run_id"] = match.group(0)
                            break
                    if result.get("discovery_run_id"):
                        break
            if result.get("discovery_run_id"):
                try:
                    topology = request_json(
                        "GET", f"{base_url}/api/v1/cases/{case_id}/topology/discovery-runs/{result['discovery_run_id']}",
                    ).get("data") or {}
                    if topology.get("status") in {"COMPLETED", "PARTIAL"}:
                        terminal_seen = True
                except E2EFailure:
                    topology = None
            runtime_turns = state.get("turns") or []
            done_turn = next((item for item in runtime_turns if item.get("turn_id") == turn_id and item.get("status") in {"COMPLETED", "FAILED"}), None)
            # A plain assistant answer is not an accepted investigation
            # terminal.  Keep the isolated runtime alive until the server has
            # persisted a verifier-owned Conclusion (normally via
            # finish_investigation).  This prevents a correct-looking Markdown
            # answer from being scored as a complete Evidence-native loop.
            if terminal_seen:
                verified_conclusion = (
                    request_json(
                        "GET", f"{base_url}/api/v1/cases/{case_id}/conclusions",
                    ).get("data") or {}
                ).get("conclusion")
            live_completion_audit = _model_completion_audit(
                state.get("events") or [], turn_id,
            )
            live_runtime_audit = _runtime_event_audit(state.get("events") or [])
            if (
                done_turn
                and verified_conclusion
                and live_completion_audit["main_turn_real_completion"]
                and live_completion_audit["terminal_reminder_resolved"]
                and live_runtime_audit["no_unfinished_turns"]
            ):
                break
            time.sleep(max(2, poll_interval_sec))

        # Fetch the durable projections once after the low-frequency loop.
        state = request_json("GET", f"{base_url}/api/v1/cases/{case_id}/agent/runtime-state").get("data") or {}
        all_events = _read_events(base_url, case_id, 0)
        public_graph = request_json("GET", f"{base_url}/api/v1/cases/{case_id}/dependency-graph").get("data") or {}
        workspace = request_json("GET", f"{base_url}/api/v1/cases/{case_id}/workspace").get("data") or {}
        conclusion = (
            request_json("GET", f"{base_url}/api/v1/cases/{case_id}/conclusions").get("data") or {}
        ).get("conclusion") or verified_conclusion
        workspace_graph = workspace.get("dependency_graph") or {}
        tool_graph = request_json("POST", f"{base_url}/internal/agent/tools/get-dependency-graph", {"case_id": case_id}, internal_headers).get("data") or {}
        causal = request_json("POST", f"{base_url}/internal/agent/tools/get-causal-graph", {"case_id": case_id}, internal_headers).get("data") or {}
        topology = None
        if result.get("discovery_run_id"):
            topology = request_json("GET", f"{base_url}/api/v1/cases/{case_id}/topology/discovery-runs/{result['discovery_run_id']}").get("data") or {}
        graph = public_graph.get("graph") or {}
        nodes, edges = graph.get("nodes") or [], graph.get("edges") or []
        points = sorted({str(point) for edge in edges if isinstance(edge, dict) for point in edge.get("observation_points") or []})
        digests = {
            "case_api": public_graph.get("graph_digest"),
            "workspace": workspace_graph.get("graph_digest"),
            "agent_tool": tool_graph.get("graph_digest"),
        }
        runtime_events = _read_runtime_events(base_url, case_id, internal_headers)
        tool_sequence = _tool_sequence(runtime_events)
        attempts = _model_attempts(runtime_events)
        completion_audit = _model_completion_audit(runtime_events, turn_id)
        runtime_event_audit = _runtime_event_audit(runtime_events)
        conclusion_count = _sqlite_conclusion_count(database)
        evidence_refs = public_graph.get("evidence_refs") or []
        followup_items: list[dict[str, Any]] = []
        for request_item in workspace.get("collection_requests") or []:
            task_id = str(request_item.get("task_id") or "")
            if not task_id:
                continue
            task = request_json("GET", f"{base_url}/api/tasks/{task_id}").get("data") or {}
            options = ((task.get("request_params") or {}).get("options") or {})
            if options.get("discovery_followup_authority") is not True:
                continue
            task_artifacts = request_json(
                "GET", f"{base_url}/api/tasks/{task_id}/artifacts?verify=false",
            ).get("data") or []
            task_evidence = [
                str(item.get("evidence_id") or "")
                for item in workspace.get("evidence") or []
                if str(item.get("task_id") or "") == task_id
                and str(item.get("status") or "") == "ACTIVE"
            ]
            followup_items.append({
                "collection_request_id": request_item.get("collection_request_id"),
                "task_id": task_id,
                "task_status": task.get("status"),
                "collector_type": task.get("collector_type"),
                "agent_id": task.get("agent_id"),
                "target_pid": task.get("target_pid"),
                "discovery_run_id": options.get("discovery_run_id"),
                "membership_snapshot_id": options.get("membership_snapshot_id"),
                "authority_evidence_refs": options.get("discovery_authority_evidence_refs") or [],
                "expected_entity_id": options.get("expected_entity_id"),
                "target_incarnation_validation": sorted({
                    str((item.get("metadata") or {}).get("target_incarnation_validation") or "")
                    for item in task_artifacts
                    if (item.get("metadata") or {}).get("target_incarnation_validation")
                }),
                "evidence_ids": sorted(item for item in task_evidence if item),
            })
        server_node = next((
            item for item in nodes
            if str(item.get("entity_type") or "") == "process"
            and int((item.get("process") or {}).get("pid") or 0)
            == int(processes["fixture-server"].pid)
        ), None)
        expected_server_entity_id = str((server_node or {}).get("entity_id") or "")
        expected_membership_snapshot_id = str(
            ((topology or {}).get("started") or {}).get("membership_snapshot_id") or ""
        )
        followup = followup_items[0] if len(followup_items) == 1 else {}
        checks = {
            "turn_accepted_by_pi": (
                accepted.get("status") == "runtime_turn_accepted"
                and any("runtime_mode=pi" in str(item) for item in accepted.get("decision_summary") or [])
            ),
            "deepseek_completion_recorded": completion_audit["main_turn_real_completion"],
            "terminal_reminder_resolved": completion_audit["terminal_reminder_resolved"],
            "runtime_event_seq_contiguous": runtime_event_audit["seq_contiguous"],
            "runtime_event_identity_unique": (
                runtime_event_audit["event_ids_unique"]
                and runtime_event_audit["idempotency_keys_unique"]
            ),
            "no_unfinished_runtime_turns": runtime_event_audit["no_unfinished_turns"],
            "sqlite_conclusion_persisted": conclusion_count >= 1,
            "topology_tool_called": "discover_topology" in tool_sequence,
            "topology_terminal": bool(topology and topology.get("status") in {"COMPLETED", "PARTIAL"}),
            "discovered_followup_tool_called": "propose_collection" in tool_sequence,
            "single_discovered_followup_collection": len(followup_items) == 1,
            "discovered_followup_task_completed": (
                followup.get("task_status") == "DONE"
                and followup.get("collector_type") == "network_discovery"
                and followup.get("agent_id") == agent_id
                and int(followup.get("target_pid") or 0) == int(processes["fixture-server"].pid)
            ),
            "discovered_followup_authority_bound": bool(followup) and (
                followup.get("discovery_run_id") == result.get("discovery_run_id")
                and followup.get("membership_snapshot_id") == expected_membership_snapshot_id
                and followup.get("expected_entity_id") == expected_server_entity_id
                and bool(followup.get("authority_evidence_refs"))
            ),
            "discovered_followup_incarnation_checked": bool(
                set(followup.get("target_incarnation_validation") or [])
                & {"verified", "limited"}
            ),
            "discovered_followup_evidence_persisted": bool(
                followup.get("evidence_ids")
            ),
            "verified_terminal_conclusion": bool(conclusion) and "finish_investigation" in tool_sequence,
            "dependency_graph_has_two_nodes": len(nodes) >= 2,
            "dependency_graph_has_edge": len(edges) >= 1,
            "client_and_server_observed": {"client", "server"}.issubset(points),
            "evidence_present": bool(evidence_refs),
            "three_projection_digest_match": bool(digests["case_api"]) and len(set(digests.values())) == 1,
            "causal_graph_separate": causal.get("graph") in (None, {}),
            # The real Pi sidecar is expected to open one external TCP
            # connection to DeepSeek.  Local Control/Agent/fixture processes
            # must remain loopback-only; an external socket from any other
            # process is a boundary failure.
            "only_sidecar_provider_external": all(
                int(conn.get("pid") or -1) == int(processes["sidecar"].pid)
                for sample in traffic_samples
                for conn in sample.get("non_loopback_connections") or []
            ),
        }
        process_bytes = [_nettop_bytes({proc.pid for proc in processes.values() if proc.poll() is None}) for _ in range(1)]
        traffic = {
            "schema_version": "real-pi-low-bandwidth-audit.v1",
            "poll_interval_sec": poll_interval_sec,
            "sample_count": len(traffic_samples),
            "socket_samples": traffic_samples,
            "non_loopback_connection_count": sum(len(sample.get("non_loopback_connections") or []) for sample in traffic_samples),
            "provider_prompt_upload": "present_and_expected",
            "local_artifact_upload": "disabled",
            "byte_measurement": {
                "method": "macOS nettop per-process aggregate samples",
                "final_process_sample": process_bytes[0] if process_bytes else {},
                "limitation": "nettop is sampled aggregate accounting, not packet capture; provider-side bytes are not independently metered here",
            },
        }
        result.update({
            "status": "PASS" if all(checks.values()) else "FAIL",
            "finished_at": utcnow(),
            "runtime_state": {
                "binding": state.get("binding"),
                "turns": state.get("turns") or [],
                "event_count": len(runtime_events),
            },
            "tool_sequence": tool_sequence,
            "model_attempts": attempts,
            "model_completion_audit": completion_audit,
            "runtime_event_audit": runtime_event_audit,
            "persistence_audit": {
                "sqlite_conclusion_revision_count": conclusion_count,
            },
            "discovered_followup_audit": {
                "expected_server_entity_id": expected_server_entity_id,
                "expected_membership_snapshot_id": expected_membership_snapshot_id,
                "items": followup_items,
            },
            "assistant_message": str((conclusion or {}).get("report_text") or "").strip()
            or _assistant_message(runtime_events),
            "conclusion": conclusion,
            "topology_run": topology,
            "graph_digest": public_graph.get("graph_digest"),
            "projection_digests": digests,
            "graph_summary": {
                "node_count": len(nodes),
                "edge_count": len(edges),
                "observation_points": points,
                "coverage": public_graph.get("coverage") or {},
                "limitations": public_graph.get("limitations") or [],
            },
            "evidence_ids": evidence_refs,
            "verification": checks,
            "traffic_audit": traffic,
            "event_count": len(all_events),
            "artifact_inventory": artifact_inventory(artifacts),
        })
        dump_json(output_dir / "result.json", result)
        dump_json(output_dir / "runtime-state.json", state)
        dump_json(output_dir / "runtime-events.json", {"items": runtime_events})
        dump_json(output_dir / "traffic-audit.json", traffic)
        dump_json(output_dir / "dependency-graph.json", public_graph)
        dump_json(output_dir / "workspace-dependency-graph.json", workspace_graph)
        dump_json(output_dir / "tool-dependency-graph.json", tool_graph)
        dump_json(output_dir / "causal-graph.json", causal)
        dump_json(output_dir / "discovered-followup.json", {
            "expected_server_entity_id": expected_server_entity_id,
            "expected_membership_snapshot_id": expected_membership_snapshot_id,
            "items": followup_items,
        })
        if topology is not None:
            dump_json(output_dir / "topology-run.json", topology)
        if result["status"] != "PASS":
            failures = [name for name, passed in checks.items() if not passed]
            raise E2EFailure("Assertions failed: " + ", ".join(failures))
        return result
    except Exception as exc:
        result.update({"status": "FAIL", "finished_at": utcnow(), "error": f"{type(exc).__name__}: {exc}"})
        dump_json(output_dir / "result.json", result)
        raise
    finally:
        for name in ("agent", "analyzer", "sidecar", "control", "fixture-client", "fixture-server"):
            terminate(processes.get(name))
        for handle in handles:
            handle.close()
        logs_dir = output_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        for name in processes:
            _scrub_log(runtime_dir / f"{name}.log", logs_dir / f"{name}.log", [key, internal_token])
        try:
            retention = _apply_runtime_retention(
                runtime_dir,
                succeeded=result.get("status") == "PASS",
                keep_runtime=keep_runtime,
            )
        except OSError as exc:
            result.update({
                "status": "FAIL",
                "error": f"runtime cleanup failed: {type(exc).__name__}: {exc}",
                "runtime_retention": {
                    "keep_requested": keep_runtime,
                    "retained": runtime_dir.exists(),
                    "reason": "cleanup_failed",
                },
            })
            dump_json(output_dir / "result.json", result)
            raise E2EFailure(result["error"]) from exc
        result["runtime_retention"] = retention
        dump_json(output_dir / "result.json", result)


def main(argv: list[str] | None = None) -> int:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=_default_output_dir(timestamp),
        help="Report directory (default: reports/eval/unknown-topology-pi-<timestamp>).",
    )
    parser.add_argument("--timeout-sec", type=int, default=240)
    parser.add_argument("--poll-interval-sec", type=int, default=5)
    parser.add_argument("--credential-file", type=Path, default=Path.home() / ".dsh" / ".credentials.yaml")
    parser.add_argument(
        "--keep-runtime",
        action="store_true",
        help="Retain SQLite, raw logs, event spool and artifacts after a successful run.",
    )
    args = parser.parse_args(argv)
    if not 60 <= args.timeout_sec <= 900:
        parser.error("--timeout-sec must be between 60 and 900")
    if not 2 <= args.poll_interval_sec <= 15:
        parser.error("--poll-interval-sec must be between 2 and 15")
    try:
        result = run(
            args.output_dir,
            args.timeout_sec,
            args.poll_interval_sec,
            args.credential_file,
            keep_runtime=args.keep_runtime,
        )
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "output_dir": str(args.output_dir.expanduser().resolve()), "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 1
    print(json.dumps({
        "status": result["status"],
        "output_dir": str(args.output_dir.expanduser().resolve()),
        "case_id": result.get("case", {}).get("case_id"),
        "turn_id": (result.get("turn_acceptance") or {}).get("turn_id"),
        "graph_digest": result.get("graph_digest"),
        "tool_sequence": result.get("tool_sequence"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
