#!/usr/bin/env python3
"""Run the API-facing part of the Mini-Drop AI showcase.

The runner never approves R2 probes and never executes a rendered command. It
only creates bounded diagnosis sessions and checks the returned orchestration
state.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SHOWCASE_ROOT = Path(__file__).resolve().parent
REGISTERED_COLLECTORS = {"sys_metrics", "perf_cpu", "ebpf_io", "memory_smaps"}


def _request(base_url: str, method: str, path: str, api_key: str | None,
             payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if api_key:
        headers["X-API-Key"] = api_key
    request = Request(
        base_url.rstrip("/") + path,
        data=body,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {path}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Cannot connect to {base_url}: {exc.reason}") from exc


def _expand(value: Any, replacements: dict[str, Any]) -> Any:
    if isinstance(value, str):
        if value in replacements:
            return replacements[value]
        result = value
        for marker, replacement in replacements.items():
            if isinstance(replacement, str):
                result = result.replace(marker, replacement)
        return result
    if isinstance(value, list):
        return [_expand(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item, replacements) for key, item in value.items()}
    return value


def _discover_agent(base_url: str, api_key: str | None,
                    requested_agent_id: str | None) -> dict[str, Any]:
    response = _request(base_url, "GET", "/api/agents", api_key)
    agents = response.get("data", {}).get("items", [])
    if requested_agent_id:
        matches = [item for item in agents if item.get("id") == requested_agent_id]
        if not matches:
            raise RuntimeError(f"Agent {requested_agent_id!r} was not returned by /api/agents")
        return matches[0]
    online = [item for item in agents if str(item.get("status", "")).upper() == "ONLINE"]
    if online:
        return online[0]
    if agents:
        return agents[0]
    raise RuntimeError("No agent is registered; start an agent or pass --skip-target-cases")


def _evaluate_case(case: dict[str, Any], response: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    data = response.get("data", {})
    status = data.get("status")
    checks = {
        "status": status in case["expected_statuses"],
    }
    if case.get("expected_no_tasks"):
        checks["no_tasks"] = not data.get("child_task_ids")
    if case.get("expected_registered_probes_only"):
        collectors = {
            item.get("collector_type")
            for item in data.get("probes", [])
            if item.get("collector_type")
        }
        checks["registered_probes_only"] = collectors.issubset(REGISTERED_COLLECTORS)
        checks["no_r2_auto_approval"] = all(
            not item.get("approved")
            for item in data.get("probes", [])
            if item.get("risk_level") == "R2"
        )
    return all(checks.values()), {
        "diagnosis_id": data.get("diagnosis_id"),
        "status": status,
        "child_task_count": len(data.get("child_task_ids", [])),
        "probe_states": [
            {
                "probe_id": item.get("probe_id"),
                "collector_type": item.get("collector_type"),
                "risk_level": item.get("risk_level"),
                "status": item.get("status"),
            }
            for item in data.get("probes", [])
        ],
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-url", default=os.getenv("MINI_DROP_SERVER_URL", "http://localhost:8191"))
    parser.add_argument("--api-key", default=os.getenv("MINI_DROP_API_KEY") or None)
    parser.add_argument("--agent-id")
    parser.add_argument("--pid", type=int, help="PID of a safe demo target process")
    parser.add_argument("--service-id", default="ai-showcase-target")
    parser.add_argument("--instance-id", default="ai-showcase-target-1")
    parser.add_argument("--host-id")
    parser.add_argument(
        "--skip-target-cases",
        action="store_true",
        help="Only run the scope-only case; does not require an Agent or PID.",
    )
    parser.add_argument(
        "--validate-provider",
        action="store_true",
        help="Also call the configured external model validation suite (may consume API quota).",
    )
    parser.add_argument("--output-dir", type=Path, default=SHOWCASE_ROOT / "reports")
    args = parser.parse_args()

    cases = json.loads(
        (SHOWCASE_ROOT / "scenarios" / "live_api_cases.json").read_text(encoding="utf-8")
    )
    _request(args.server_url, "GET", "/api/healthz", args.api_key)

    instance = None
    if not args.skip_target_cases:
        if not args.pid:
            parser.error("--pid is required unless --skip-target-cases is used")
        agent = _discover_agent(args.server_url, args.api_key, args.agent_id)
        agent_id = agent.get("id")
        host_id = args.host_id or agent.get("host_id") or agent.get("hostname") or agent_id
        instance = {
            "service_id": args.service_id,
            "instance_id": args.instance_id,
            "host_id": host_id,
            "agent_id": agent_id,
            "pid": args.pid,
            "environment": "demo",
        }

    replacements = {
        "${SERVICE_ID}": args.service_id,
        "${INSTANCE}": instance,
    }
    results = []
    for case in cases:
        needs_target = "${INSTANCE}" in json.dumps(case)
        if needs_target and instance is None:
            results.append({
                "case_id": case["case_id"],
                "description": case["description"],
                "status": "SKIPPED",
                "passed": True,
                "actual": {"reason": "target cases disabled"},
            })
            continue
        payload = _expand(case["request"], replacements)
        try:
            response = _request(
                args.server_url,
                "POST",
                "/api/v1/diagnoses",
                args.api_key,
                payload,
            )
            passed, actual = _evaluate_case(case, response)
            status = "PASS" if passed else "FAIL"
        except Exception as exc:  # noqa: BLE001 - report every online case
            passed, status, actual = False, "FAIL", {"error": f"{type(exc).__name__}: {exc}"}
        results.append({
            "case_id": case["case_id"],
            "description": case["description"],
            "status": status,
            "passed": passed,
            "actual": actual,
        })

    if args.validate_provider:
        try:
            response = _request(
                args.server_url,
                "POST",
                "/api/ai-validation/runs",
                args.api_key,
                {},
            )
            validation = response.get("data", {})
            passed = validation.get("status") == "PASSED"
            actual = {
                "status": validation.get("status"),
                "provider": validation.get("provider"),
                "model": validation.get("model"),
                "passed_count": validation.get("passed_count"),
                "total_count": validation.get("total_count"),
                "checks": [
                    {
                        "check_id": item.get("check_id"),
                        "name": item.get("name"),
                        "status": item.get("status"),
                    }
                    for item in validation.get("checks", [])
                ],
                "security": validation.get("security"),
            }
        except Exception as exc:  # noqa: BLE001
            passed = False
            actual = {"error": f"{type(exc).__name__}: {exc}"}
        results.append({
            "case_id": "external_model_validation",
            "description": "验证 Provider、模型发现、对话、NLP、总结和 RCA 链路。",
            "status": "PASS" if passed else "FAIL",
            "passed": passed,
            "actual": actual,
        })

    failed = sum(not item["passed"] for item in results)
    report = {
        "suite": "mini-drop-ai-showcase-live-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "server_url": args.server_url,
        "summary": {
            "total": len(results),
            "passed_or_skipped": len(results) - failed,
            "failed": failed,
        },
        "results": results,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "live_showcase_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("# Mini-Drop 在线 AI 编排检查")
    print()
    print("| 用例 | 结果 | 诊断状态 |")
    print("|---|---|---|")
    for item in results:
        print(
            f"| {item['case_id']} | {item['status']} | "
            f"{item['actual'].get('status', item['actual'].get('reason', item['actual'].get('error', '')))} |"
        )
    print()
    print("安全说明：本脚本不会批准 R2 探针，也不会执行报告中的命令或修复动作。")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
