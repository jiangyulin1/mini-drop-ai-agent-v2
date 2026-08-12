"""受控下游端点连通性探针采集器（connection_probe）。

只读：探测目标服务声明的下游端点是否可达。不做任何修改性操作，不注入目标。
每个端点输出：TCP 连通性（可达/延迟/错误）、下游容器状态（running/paused/
exited/restarting，经 docker CLI 读取）。TCP 探测优先走调用方容器 netns
（nsenter），使 overlay 网络内的服务名可解析；失败时退回宿主机直连。

输出 schema_version=connection_probe.v1，artifact_type="connection_probe"：
{
  "schema_version": "connection_probe.v1",
  "task_id": "...",
  "scanned_at": <float>,
  "endpoints": [
    {
      "service": "paymentservice",
      "host": "paymentservice",
      "port": 50051,
      "reachable": true | false | null,
      "connect_latency_ms": 2.1 | null,
      "error": null | "connection refused",
      "container_state": "running" | "paused" | "exited" | "restarting" | "unknown" | null,
      "container_restarts": 0 | null
    }
  ],
  "summary": {
    "endpoint.reachable": true | false,
    "endpoint.container_state": "running",
    "endpoint.total": 1,
    "endpoint.unreachable_count": 0,
    "endpoint.downstream_service": "paymentservice"
  }
}

summary 中的 ``endpoint.*`` 标量会经 orchestrator._normalized_facts 进入扁平
facts，供 EvidenceContract 判定下游可达性契约是否满足。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from agent.mini_drop_agent.collectors.base import CollectorResult, CollectorTask


SCHEMA_VERSION = "connection_probe.v1"
CONNECT_TIMEOUT_SEC = 3.0
_CGROUP_DOCKER_RE = re.compile(r"(?:^|/)docker[/-]([0-9a-fA-F]{12,64})$")
_STATE_RANK = {"running": 0, "restarting": 1, "exited": 2, "paused": 3}


class ConnectionProbeCollector:
    OUTPUT_BASE = "/tmp/mini-drop"

    def collect(self, task: CollectorTask) -> CollectorResult:
        raw_endpoints = task.options.get("endpoints") or []
        if not isinstance(raw_endpoints, list) or not raw_endpoints:
            return CollectorResult(
                ok=False, reason="connection_probe 缺少 endpoints 参数",
            )
        output_dir = os.path.join(self.OUTPUT_BASE, task.id)
        os.makedirs(output_dir, exist_ok=True)

        probed: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_endpoints[:16]):
            if not isinstance(raw, dict):
                continue
            service = str(raw.get("service") or f"endpoint-{index}")
            host = str(raw.get("host") or service)
            port = raw.get("port")
            caller_pid = raw.get("caller_pid")
            protocol = str(raw.get("protocol") or "tcp")
            port = int(port) if port else None
            probed.append(self._probe_endpoint(
                service=service, host=host, port=port,
                protocol=protocol, caller_pid=caller_pid,
            ))

        summary = self._summarize(probed)
        output = {
            "schema_version": SCHEMA_VERSION,
            "task_id": task.id,
            "scanned_at": time.time(),
            "endpoints": probed,
            "summary": summary,
        }
        output_path = os.path.join(output_dir, "connection_probe.json")
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(output, handle, ensure_ascii=False, indent=2)
        reachable_count = sum(1 for item in probed if item["reachable"] is True)
        return CollectorResult(
            ok=True,
            reason=(
                f"连接探测完成: {len(probed)} 个端点, "
                f"{reachable_count} 可达, {summary.get('endpoint.unreachable_count', 0)} 不可达"
            ),
            artifacts=[{
                "artifact_type": "connection_probe",
                "filename": "connection_probe.json",
                "local_path": output_path,
                "content_type": "application/json",
                "size_bytes": os.path.getsize(output_path),
                "metadata": {"schema_version": SCHEMA_VERSION, **summary},
            }],
        )

    def _probe_endpoint(
        self, *, service: str, host: str, port: int | None,
        protocol: str, caller_pid: Any,
    ) -> dict[str, Any]:
        container_state, restarts = self._container_state(service)
        reachable: bool | None = None
        latency: float | None = None
        error: str | None = None
        if port is not None and port > 0:
            if protocol == "http":
                reachable, latency, error = self._http_check(host, port)
            else:
                # 先直连；服务名在 overlay 网络内解析不了时，再尝试调用方 netns。
                reachable, latency, error = self._tcp_check(host, port)
                if reachable is False and caller_pid:
                    via_netns = self._connect_via_netns(caller_pid, host, port)
                    if via_netns is not None:
                        reachable, latency, error = via_netns
        elif container_state in {"paused", "exited", "restarting"}:
            # 容器状态本身即可说明下游不可用，无需端口。
            reachable = False
            error = f"downstream container state={container_state}"
        return {
            "service": service,
            "host": host,
            "port": port,
            "protocol": protocol,
            "reachable": reachable,
            "connect_latency_ms": latency,
            "error": error,
            "container_state": container_state or "unknown",
            "container_restarts": restarts,
        }

    @staticmethod
    def _tcp_check(host: str, port: int) -> tuple[bool | None, float | None, str | None]:
        try:
            start = time.time()
            with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_SEC):
                latency = (time.time() - start) * 1000
                return True, round(latency, 1), None
        except socket.timeout:
            return False, None, "connect timeout"
        except OSError as exc:
            return False, None, str(exc)[:160]

    @staticmethod
    def _http_check(host: str, port: int) -> tuple[bool | None, float | None, str | None]:
        try:
            import http.client
            start = time.time()
            conn = http.client.HTTPConnection(host, port, timeout=CONNECT_TIMEOUT_SEC)
            conn.request("HEAD", "/")
            status = conn.getresponse().status
            latency = (time.time() - start) * 1000
            conn.close()
            return status < 500, round(latency, 1), None
        except (OSError, http.client.HTTPException, Exception) as exc:  # noqa: BLE001
            return False, None, str(exc)[:160]

    @staticmethod
    def _container_state(service: str) -> tuple[str | None, int | None]:
        """经 docker CLI 读取下游容器状态；不可用时返回 (None, None)。"""
        docker = shutil.which("docker")
        if not docker:
            return None, None
        try:
            ps = subprocess.run(
                [docker, "ps", "-q", "--filter", f"name={service}"],
                capture_output=True, text=True, timeout=10,
            )
            container_id = ps.stdout.strip().splitlines()[0] if ps.stdout.strip() else ""
            if not container_id:
                return None, None
            inspect = subprocess.run(
                [docker, "inspect", "-f", "{{.State.Status}} {{.RestartCount}}", container_id],
                capture_output=True, text=True, timeout=10,
            )
            parts = inspect.stdout.strip().split()
            if not parts:
                return None, None
            status = parts[0]
            restarts = int(parts[1]) if len(parts) > 1 else None
            return status, restarts
        except (OSError, subprocess.SubprocessError, ValueError):
            return None, None

    @classmethod
    def _connect_via_netns(
        cls, caller_pid: Any, host: str, port: int,
    ) -> tuple[bool | None, float | None, str | None]:
        """进入调用方容器 netns 后 TCP 探测服务名，使 overlay DNS 生效。"""
        if not (shutil.which("nsenter") and shutil.which("python3")):
            return None
        init_pid = cls._container_init_pid(int(caller_pid))
        if init_pid is None:
            return None
        script = (
            "import socket,time\n"
            f"s=socket.socket(); s.settimeout({CONNECT_TIMEOUT_SEC})\n"
            "t=time.time()\n"
            "try:\n"
            f" s.connect(('{host}', {port})); print('OK %d' % int((time.time()-t)*1000))\n"
            "except Exception as e:\n"
            " print('ERR ' + str(e)[:160])\n"
        )
        try:
            out = subprocess.run(
                ["nsenter", "-t", str(init_pid), "-n", "python3", "-c", script],
                capture_output=True, text=True, timeout=CONNECT_TIMEOUT_SEC + 5,
            )
            line = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else ""
            if line.startswith("OK"):
                return True, float(line.split()[1]), None
            if line.startswith("ERR"):
                return False, None, line[4:]
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        return None

    @staticmethod
    def _container_init_pid(pid: int) -> int | None:
        """由 /proc/<pid>/cgroup 定位 Docker 容器 init PID（宿主机权限下可用）。"""
        try:
            cgroup = Path(f"/proc/{pid}/cgroup").read_text(errors="replace")
        except (FileNotFoundError, PermissionError, OSError):
            return None
        docker = shutil.which("docker")
        if not docker:
            return None
        container_id = None
        for line in cgroup.splitlines():
            match = _CGROUP_DOCKER_RE.search(line)
            if match:
                container_id = match.group(1)
                break
        if not container_id:
            return None
        try:
            out = subprocess.run(
                [docker, "inspect", "-f", "{{.State.Pid}}", container_id],
                capture_output=True, text=True, timeout=10,
            )
            return int(out.stdout.strip()) if out.stdout.strip().isdigit() else None
        except (OSError, subprocess.SubprocessError, ValueError):
            return None

    @staticmethod
    def _summarize(endpoints: list[dict[str, Any]]) -> dict[str, Any]:
        verdicts = [item["reachable"] for item in endpoints if item["reachable"] is not None]
        states = [
            item["container_state"] for item in endpoints
            if item.get("container_state") and item["container_state"] != "unknown"
        ]
        summary: dict[str, Any] = {
            "endpoint.total": len(endpoints),
            "endpoint.unreachable_count": sum(
                1 for item in endpoints if item["reachable"] is False
            ),
            "endpoint.downstream_service": ",".join(sorted({
                str(item["service"]) for item in endpoints
            })),
        }
        if verdicts:
            summary["endpoint.reachable"] = all(verdicts)
        if states:
            summary["endpoint.container_state"] = max(
                states, key=lambda item: _STATE_RANK.get(item, 0),
            )
        return summary
