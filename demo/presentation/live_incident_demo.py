#!/usr/bin/env python3
"""Run a bounded service incident, collect evidence, diagnose it, and recover."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import ssl
import statistics
import subprocess
import sys
import time
from typing import Any
from urllib.request import Request, urlopen


HERE = Path(__file__).resolve().parent
ACTIVE_ROOT = Path("/home/control/mini-drop-active")
CONTROL_ENV = ACTIVE_ROOT / "deploy/env/control-native.env"
SERVICE_UNIT = "mini-drop-incident-demo.service"
AGENT_UNIT = "mini-drop-incident-agent.service"
DEFAULT_AGENT_ID = "demo-worker"


class ApiClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.context = ssl._create_unverified_context()

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json", "X-API-Key": self.api_key}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        with urlopen(request, timeout=130, context=self.context) as response:
            decoded = json.loads(response.read().decode("utf-8"))
        return decoded.get("data", decoded) if isinstance(decoded, dict) else decoded

    def get_bytes(self, path: str) -> bytes:
        request = Request(
            self.base_url + path,
            headers={"X-API-Key": self.api_key},
        )
        with urlopen(request, timeout=30, context=self.context) as response:
            return response.read()


def service_json(port: int, path: str) -> dict[str, Any]:
    with urlopen(f"http://127.0.0.1:{port}{path}", timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def measure_latency(port: int, count: int = 3) -> dict[str, float]:
    samples = []
    reported = []
    for _ in range(count):
        started = time.perf_counter()
        payload = service_json(port, "/work")
        samples.append((time.perf_counter() - started) * 1000)
        reported.append(float(payload.get("latency_ms", 0)))
    return {
        "median_client_ms": round(statistics.median(samples), 2),
        "median_service_ms": round(statistics.median(reported), 2),
    }


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True, capture_output=True)


def _stop_units() -> None:
    _run(["systemctl", "stop", SERVICE_UNIT, AGENT_UNIT], check=False)
    _run(["systemctl", "reset-failed", SERVICE_UNIT, AGENT_UNIT], check=False)


def _start_units(agent_id: str, port: int) -> None:
    _stop_units()
    python = str(ACTIVE_ROOT / ".venv/bin/python")
    service_script = str(HERE / "incident_service.py")
    tools_bin = "/home/control/mini-drop-demo-tools/bin"

    _run(
        [
            "systemd-run",
            f"--unit={SERVICE_UNIT.removesuffix('.service')}",
            "--collect",
            "--property=CPUQuota=80%",
            "--property=MemoryMax=192M",
            "--property=RuntimeMaxSec=15min",
            "--property=PrivateTmp=true",
            python,
            service_script,
            "--port",
            str(port),
        ]
    )

    agent_command = (
        f"set -a; source {CONTROL_ENV}; set +a; "
        f"export PATH={tools_bin}:$PATH; "
        f"export AGENT_ID={agent_id}; "
        "export AGENT_IP_ADDR=127.0.0.1; "
        "export AGENT_GRPC_ADDR=127.0.0.1:50051; "
        "export AGENT_HEARTBEAT_INTERVAL_SEC=1; "
        "export AGENT_UPLOAD_ARTIFACTS=1; "
        "export AGENT_GRPC_SECURE=1; "
        "export AGENT_GRPC_CA_CERT=$MINI_DROP_GRPC_CERT_FILE; "
        "export AGENT_GRPC_TLS_SERVER_NAME=127.0.0.1; "
        "export MINIO_SECURE=0; "
        f"exec {python} -m agent.mini_drop_agent.main"
    )
    _run(
        [
            "systemd-run",
            f"--unit={AGENT_UNIT.removesuffix('.service')}",
            "--collect",
            "--property=RuntimeMaxSec=20min",
            "--property=WorkingDirectory=/home/control/mini-drop-active",
            "/bin/bash",
            "-lc",
            agent_command,
        ]
    )


def _wait_for_service(port: int, timeout: int = 20) -> dict[str, Any]:
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        try:
            return service_json(port, "/health")
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            time.sleep(0.5)
    raise RuntimeError(f"演示服务未就绪：{last_error}")


def _wait_for_agent(client: ApiClient, agent_id: str, timeout: int = 30) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        agents = client.request("GET", "/api/agents")
        items = agents.get("items", []) if isinstance(agents, dict) else agents
        match = next((item for item in items if item.get("id") == agent_id), None)
        if match and str(match.get("status", "")).upper() == "ONLINE":
            return match
        time.sleep(1)
    raise RuntimeError(f"临时 Agent {agent_id} 未上线")


def _main_pid(unit: str) -> int:
    result = _run(["systemctl", "show", unit, "--property=MainPID", "--value"])
    pid = int(result.stdout.strip() or "0")
    if pid <= 0:
        raise RuntimeError(f"{unit} 没有有效 MainPID")
    return pid


def _wait_for_task(client: ApiClient, task_id: str, timeout: int = 100) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = client.request("GET", f"/api/tasks/{task_id}")
        if task.get("status") in {"DONE", "FAILED", "CANCELLED"}:
            return task
        time.sleep(1)
    raise RuntimeError(f"采集任务 {task_id} 超时")


def validate_diagnosis(detail: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    run = detail.get("run") or {}
    report = (detail.get("report") or {}).get("report") or {}
    tools = {
        item.get("tool_name"): item.get("status")
        for item in detail.get("tool_results", [])
    }
    causes = report.get("ranked_causes") or []
    checks = {
        "status_done": run.get("status") == "DONE",
        "validated": bool(run.get("validated")),
        "has_summary": bool(report.get("summary")),
        "enough_evidence": not report.get("not_enough_evidence", True),
        "flamegraph_used": tools.get("get_flamegraph_top") == "success",
        "has_ranked_cause": bool(causes),
    }
    return all(checks.values()), {
        "checks": checks,
        "summary": report.get("summary") or run.get("summary"),
        "causes": [
            {
                "cause_id": item.get("cause_id"),
                "confidence": item.get("confidence"),
                "description": item.get("description"),
            }
            for item in causes
        ],
        "tool_statuses": tools,
    }


def _latest_diagnosis_id(client: ApiClient, task_id: str) -> str:
    runs = client.request("GET", f"/api/tasks/{task_id}/diagnoses")
    items = runs.get("items", []) if isinstance(runs, dict) else runs
    if not items:
        raise RuntimeError("诊断接口没有返回诊断记录")
    return items[0].get("id") or items[0].get("diagnosis_id")


def run_demo(args: argparse.Namespace) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise RuntimeError("请通过 sudo 运行完整故障演示")
    api_key = args.api_key or os.getenv("MINI_DROP_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 MINI_DROP_API_KEY")
    client = ApiClient(args.server_url, api_key)
    failed = True
    leave_anomaly_running = False
    _start_units(args.agent_id, args.port)
    try:
        _wait_for_service(args.port)
        _wait_for_agent(client, args.agent_id)
        service_pid = _main_pid(SERVICE_UNIT)
        baseline = measure_latency(args.port)

        print("[1/5] 基线服务正常，准备注入 CPU/序列化异常", flush=True)
        print(
            f"      基线服务延迟：{baseline['median_service_ms']} ms",
            flush=True,
        )
        service_json(args.port, "/mode/anomaly")
        time.sleep(2)
        anomaly_metrics = service_json(args.port, "/metrics")
        if anomaly_metrics.get("mode") != "anomaly" or anomaly_metrics.get("cpu_hotspot_cycles", 0) <= 0:
            raise RuntimeError("故障注入没有产生热点循环")
        anomaly = measure_latency(args.port)
        print(
            f"      异常服务延迟：{anomaly['median_service_ms']} ms；"
            f"热点循环：{anomaly_metrics.get('cpu_hotspot_cycles')}",
            flush=True,
        )

        print("[2/5] 异常已生效，创建 py-spy 采集任务", flush=True)
        created = client.request(
            "POST",
            "/api/tasks",
            {
                "name": "现场演示｜checkout 服务 CPU 与 JSON 序列化异常",
                "agent_id": args.agent_id,
                "target_pid": service_pid,
                "collector_type": "pyspy",
                "sample_rate": args.sample_rate,
                "duration_sec": args.duration,
            },
        )
        task_id = created.get("task_id") or created.get("id")
        if not task_id:
            raise RuntimeError(f"任务创建失败：{created}")
        print(
            f"      实时任务页面：{args.public_url.rstrip('/')}/task/{task_id}",
            flush=True,
        )
        task = _wait_for_task(client, task_id, timeout=args.duration + 90)
        if task.get("status") != "DONE":
            raise RuntimeError(f"采集失败：{task.get('status_reason') or task.get('status')}")
        artifacts = client.request("GET", f"/api/tasks/{task_id}/artifacts")
        items = artifacts.get("items", []) if isinstance(artifacts, dict) else artifacts
        types = {item.get("artifact_type") for item in items}
        if "flamegraph_svg" not in types:
            raise RuntimeError(f"采集完成但缺少 flamegraph_svg：{sorted(types)}")
        svg = client.get_bytes(f"/api/tasks/{task_id}/artifacts/flamegraph_svg/download")
        if b"<svg" not in svg:
            raise RuntimeError("火焰图下载内容无效")

        if args.command == "prepare":
            result = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "task_id": task_id,
                "service_pid": service_pid,
                "agent_id": args.agent_id,
                "baseline": baseline,
                "anomaly": anomaly,
                "anomaly_cycles": anomaly_metrics.get("cpu_hotspot_cycles"),
                "artifact_types": sorted(types),
                "mode": "anomaly",
                "task_url": f"{args.public_url.rstrip('/')}/task/{task_id}",
                "recover_command": "/home/control/mini-drop-demo/run-live-incident.sh recover",
                "cleanup_command": "sudo /home/control/mini-drop-demo/run-live-incident.sh cleanup",
            }
            (HERE / "latest-session.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            failed = False
            leave_anomaly_running = True
            print("[3/3] 异常证据已准备完成；AI 尚未运行", flush=True)
            return result

        print("[3/5] 真实火焰图已上传，调用 AI 智能归因", flush=True)
        diagnosis_response = client.request("POST", f"/api/tasks/{task_id}/diagnose")
        diagnosis_id = diagnosis_response.get("diagnosis_id") or _latest_diagnosis_id(client, task_id)
        diagnosis = client.request("GET", f"/api/diagnoses/{diagnosis_id}")
        diagnosis_ok, diagnosis_summary = validate_diagnosis(diagnosis)
        if not diagnosis_ok:
            raise RuntimeError(f"AI 诊断校验失败：{diagnosis_summary['checks']}")

        print("[4/5] AI 归因完成，恢复服务到正常模式", flush=True)
        service_json(args.port, "/mode/normal")
        time.sleep(1)
        recovery_state = service_json(args.port, "/health")
        recovered = measure_latency(args.port)
        if recovery_state.get("mode") != "normal":
            raise RuntimeError("服务未恢复到 normal 模式")
        print(
            f"      恢复后服务延迟：{recovered['median_service_ms']} ms",
            flush=True,
        )

        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "task_id": task_id,
            "diagnosis_id": diagnosis_id,
            "service_pid": service_pid,
            "agent_id": args.agent_id,
            "baseline": baseline,
            "anomaly": anomaly,
            "recovered": recovered,
            "anomaly_cycles": anomaly_metrics.get("cpu_hotspot_cycles"),
            "artifact_types": sorted(types),
            "diagnosis": diagnosis_summary,
            "task_url": f"{args.public_url.rstrip('/')}/task/{task_id}",
            "diagnosis_url": f"{args.public_url.rstrip('/')}/task/{task_id}",
            "cleanup_command": "/home/control/mini-drop-demo/run-live-incident.sh cleanup",
        }
        (HERE / "latest-incident.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        failed = False
        print("[5/5] 服务恢复完成，演示闭环通过", flush=True)
        return result
    finally:
        if not leave_anomaly_running:
            try:
                service_json(args.port, "/mode/normal")
            except Exception:
                pass
        if failed:
            _stop_units()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        choices=["prepare", "run", "recover", "status", "cleanup"],
        default="run",
    )
    parser.add_argument("--server-url", default="https://127.0.0.1")
    parser.add_argument("--public-url", default="https://192.168.10.10")
    parser.add_argument("--api-key", default=os.getenv("MINI_DROP_API_KEY"))
    parser.add_argument("--agent-id", default=DEFAULT_AGENT_ID)
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--duration", type=int, default=15)
    parser.add_argument("--sample-rate", type=int, default=49)
    args = parser.parse_args()

    if args.command == "cleanup":
        if os.geteuid() != 0:
            parser.error("cleanup 需要 sudo")
        _stop_units()
        print("演示服务和临时 Agent 已停止。")
        return 0
    if args.command == "status":
        result = _run(
            ["systemctl", "is-active", SERVICE_UNIT, AGENT_UNIT],
            check=False,
        )
        print(result.stdout.strip() or "inactive")
        return 0 if result.returncode == 0 else 1
    if args.command == "recover":
        try:
            service_json(args.port, "/mode/normal")
            time.sleep(1)
            recovered = measure_latency(args.port)
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] 服务恢复失败：{exc}", file=sys.stderr)
            return 2
        print("服务已恢复到 normal 模式。")
        print(f"恢复后服务延迟：{recovered['median_service_ms']} ms")
        return 0

    try:
        result = run_demo(args)
    except Exception as exc:  # noqa: BLE001 - return a concise stage failure
        print(f"[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.command == "prepare":
        print()
        print("异常状态已保留，下一步请模拟用户使用 AI：")
        print(f"1. 打开任务页面：{result['task_url']}")
        print("2. 点击页面右上方蓝色“运行诊断”按钮")
        print("3. 等待智能归因结果出现并展开证据检查")
        print(f"4. 展示完成后恢复：{result['recover_command']}")
        print(f"5. 最后清理：{result['cleanup_command']}")
        return 0

    print()
    print("诊断摘要：")
    print(result["diagnosis"]["summary"])
    print()
    print(f"任务页面：{result['task_url']}")
    print(f"任务 ID：{result['task_id']}")
    print(f"诊断 ID：{result['diagnosis_id']}")
    print(f"恢复后延迟：{result['recovered']['median_service_ms']} ms")
    print(f"演示结束后清理：sudo {result['cleanup_command']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
