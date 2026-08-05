#!/usr/bin/env python3
"""Read-only preflight for the prepared Mini-Drop teacher demonstration."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import ssl
import sys
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen


HERE = Path(__file__).resolve().parent
DEFAULT_MANIFEST = HERE / "showcase_manifest.json"


class HttpClient:
    def __init__(self, base_url: str, api_key: str, *, insecure: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.context = ssl._create_unverified_context() if insecure else ssl.create_default_context()

    def _request(self, path: str) -> bytes:
        request = Request(
            self.base_url + path,
            headers={"Accept": "application/json", "X-API-Key": self.api_key},
        )
        with urlopen(request, timeout=12, context=self.context) as response:
            return response.read()

    def get_json(self, path: str) -> Any:
        return json.loads(self._request(path).decode("utf-8"))

    def get_bytes(self, path: str) -> bytes:
        return self._request(path)


def _data(payload: Any) -> Any:
    if isinstance(payload, dict) and payload.get("code") == 0 and "data" in payload:
        return payload["data"]
    return payload


def _items(payload: Any) -> list[dict[str, Any]]:
    value = _data(payload)
    if isinstance(value, dict):
        value = value.get("items", [])
    return value if isinstance(value, list) else []


def _result(name: str, passed: bool, detail: str, **extra: Any) -> dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail, **extra}


def run_checks(manifest: dict[str, Any], client: Any) -> dict[str, Any]:
    """Validate the prepared environment without creating tasks or diagnoses."""
    checks: list[dict[str, Any]] = []

    health = _data(client.get_json("/api/healthz"))
    healthy = bool(health.get("healthy")) if isinstance(health, dict) else False
    checks.append(_result("服务健康", healthy, "数据库与对象存储正常" if healthy else "健康检查失败"))

    page = client.get_bytes("/")
    page_ok = b"Mini-Drop" in page
    checks.append(_result("前端入口", page_ok, "首页可访问" if page_ok else "首页内容异常"))

    agents = _items(client.get_json("/api/agents"))
    online_agents = [item for item in agents if str(item.get("status", "")).upper() == "ONLINE"]
    minimum = int(manifest.get("minimum_online_agents", 1))
    checks.append(
        _result(
            "Agent 在线",
            len(online_agents) >= minimum,
            f"{len(online_agents)} 台在线，要求至少 {minimum} 台",
            agents=[item.get("id") for item in online_agents],
        )
    )

    presentation_urls: list[dict[str, str]] = []
    for task in manifest.get("tasks", []):
        task_id = task["task_id"]
        task_data = _data(client.get_json(f"/api/tasks/{quote(task_id)}"))
        status = str(task_data.get("status", "")) if isinstance(task_data, dict) else ""
        artifacts = _items(client.get_json(f"/api/tasks/{quote(task_id)}/artifacts"))
        artifact_types = {item.get("artifact_type") for item in artifacts}
        expected = set(task.get("expected_artifacts", []))
        missing = sorted(expected - artifact_types)
        task_ok = status == "DONE" and not missing

        download_issues = []
        for artifact_type in sorted(expected):
            content = client.get_bytes(
                f"/api/tasks/{quote(task_id)}/artifacts/{quote(artifact_type)}/download"
            )
            if not content.strip():
                download_issues.append(f"{artifact_type} 内容为空")
        task_ok = task_ok and not download_issues
        detail = f"状态 {status}；产物 {', '.join(sorted(artifact_types)) or '无'}"
        if missing:
            detail += f"；缺少 {', '.join(missing)}"
        if download_issues:
            detail += "；" + "；".join(download_issues)
        checks.append(_result(task["title"], task_ok, detail, task_id=task_id))
        presentation_urls.append(
            {
                "title": task["title"],
                "path": f"/task/{task_id}",
                "talking_point": task.get("talking_point", ""),
            }
        )

        diagnosis_config = task.get("diagnosis") or {}
        if diagnosis_config.get("required"):
            runs = _items(client.get_json(f"/api/tasks/{quote(task_id)}/diagnoses"))
            latest = runs[0] if runs else {}
            diagnosis_id = latest.get("id") or latest.get("diagnosis_id")
            diagnosis = (
                _data(client.get_json(f"/api/diagnoses/{quote(diagnosis_id)}"))
                if diagnosis_id
                else {}
            )
            run = diagnosis.get("run") or {}
            report = (diagnosis.get("report") or {}).get("report") or {}
            tool_statuses = {
                item.get("tool_name"): item.get("status")
                for item in diagnosis.get("tool_results", [])
            }
            expected_tools = diagnosis_config.get("expected_tool_statuses", {})
            tool_mismatches = {
                name: {"expected": expected_status, "actual": tool_statuses.get(name)}
                for name, expected_status in expected_tools.items()
                if tool_statuses.get(name) != expected_status
            }
            diagnosis_ok = (
                run.get("status") == diagnosis_config.get("expected_status", "DONE")
                and bool(run.get("validated"))
                and not report.get("not_enough_evidence", True)
                and bool(report.get("summary"))
                and not tool_mismatches
            )
            checks.append(
                _result(
                    "智能归因结果",
                    diagnosis_ok,
                    (
                        f"{diagnosis_id}；已校验；"
                        f"{len(report.get('ranked_causes', []))} 个候选原因"
                        if diagnosis_ok
                        else f"{diagnosis_id or '无诊断'}；工具状态不一致 {tool_mismatches}"
                    ),
                    diagnosis_id=diagnosis_id,
                )
            )

    failed = [item for item in checks if not item["passed"]]
    return {
        "title": manifest.get("title", "Mini-Drop 演示"),
        "passed": not failed,
        "summary": {"total": len(checks), "passed": len(checks) - len(failed), "failed": len(failed)},
        "checks": checks,
        "presentation_urls": presentation_urls,
    }


def _print_report(report: dict[str, Any], base_url: str) -> None:
    print(f"# {report['title']}：现场预检")
    print()
    for item in report["checks"]:
        mark = "PASS" if item["passed"] else "FAIL"
        print(f"[{mark}] {item['name']} — {item['detail']}")
    print()
    print("演示顺序：")
    print(f"1. 任务面板与 Agent 状态 — {base_url.rstrip('/')}/")
    for index, item in enumerate(report["presentation_urls"], start=2):
        print(f"{index}. {item['title']} — {base_url.rstrip('/')}{item['path']}")
        if item["talking_point"]:
            print(f"   {item['talking_point']}")
    summary = report["summary"]
    print()
    print(f"结果：{summary['passed']}/{summary['total']} 通过。")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--server-url",
        default=os.getenv("MINI_DROP_SERVER_URL", "https://192.168.10.10"),
    )
    parser.add_argument(
        "--public-url",
        default=os.getenv("MINI_DROP_PRESENTATION_PUBLIC_URL"),
        help="报告中展示给浏览器使用的地址；默认与 --server-url 相同",
    )
    parser.add_argument("--api-key", default=os.getenv("MINI_DROP_API_KEY"))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--insecure", action="store_true", help="允许演示环境的自签名 HTTPS 证书")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    args = parser.parse_args()
    if not args.api_key:
        parser.error("请通过 MINI_DROP_API_KEY 或 --api-key 提供 API Key")

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    try:
        report = run_checks(
            manifest,
            HttpClient(args.server_url, args.api_key, insecure=args.insecure),
        )
    except Exception as exc:  # noqa: BLE001 - preflight must report a concise failure
        print(f"[FAIL] 预检无法完成：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report, args.public_url or args.server_url)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
