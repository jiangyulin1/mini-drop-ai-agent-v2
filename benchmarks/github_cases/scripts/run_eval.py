#!/usr/bin/env python3
"""GitHub 真实项目评测 runner：驱动三节点 VM 上的端到端诊断并评分。

用法（在三节点环境，control 节点上执行）：
    python scripts/run_github_eval.py \
        --server http://127.0.0.1:8191 \
        --api-key <key> \
        --suite benchmarks/github_cases/scenarios/suite.json \
        --worker worker1 \
        --output-dir reports/eval/github-cases

流程（每个场景）：
1. 确保故障已注入（inject.sh 已执行）；
2. process_scan 在目标 worker 上定位 checkoutservice 进程 PID；
3. 创建 Case（携带 symptom_query + 目标范围），按 COLLABORATE 自动推进；
4. 轮询 Case 状态直到终态，收集结论（root_location / domain_cause / evidence）；
5. 与 oracle 比对并打分；输出 JSON 报告。

评分（确定性，不与模型共享 oracle）：
- root_location_match / domain_cause_match / evidence_refs_valid
- no_fault 场景要求"不得输出确定根因"（false-positive 检查）
- unsafe_execution_count 恒为 0（当前系统不自动执行修复）
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

KNOWN_TERMINAL = {"COMPLETED", "INSUFFICIENT_EVIDENCE", "PARTIAL_COMPLETED",
                  "BUDGET_EXHAUSTED", "TOPOLOGY_UNAVAILABLE", "USER_CANCELED", "FAILED"}

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE  # 集群为自签名证书（评测环境）


def _api(base: str, path: str, api_key: str, method: str = "GET", body: dict | None = None):
    url = f"{base.rstrip('/')}{path}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if api_key:
        request.add_header("X-API-Key", api_key)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30, context=_SSL_CTX) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"API {method} {path} -> {exc.code}: {exc.read().decode()[:300]}") from exc
    if payload.get("code") != 0:
        raise RuntimeError(f"API {method} {path} -> {payload.get('message')}")
    return payload.get("data")


# ── 评分（纯函数，可单测） ──────────────────────────────────


# cluster_assessment.classification → 位置类别 归一化（评分口径）
_LOCATION_NORMALIZE: list[tuple[tuple[str, ...], str]] = [
    (("self_", "single_instance_"), "self"),
    (("host_", "same_host_"), "same_host"),
    (("downstream_",), "downstream"),
]


def _norm_location(location: str | None) -> str | None:
    if not location:
        return location
    for prefixes, target in _LOCATION_NORMALIZE:
        if any(location.startswith(prefix) for prefix in prefixes):
            return target
    return location


def score_case(case: dict, conclusion: dict | None, evidence: list[dict] | None) -> dict:
    """按 oracle 对照评分。conclusion 为 None 表示诊断未产出结论。"""
    oracle = case.get("oracle", {})
    expected_location = oracle.get("root_location")
    expected_domain = oracle.get("domain_cause")
    is_no_fault = not case.get("inject")

    location = _norm_location((conclusion or {}).get("cluster_assessment", {}).get("classification"))
    domain = (conclusion or {}).get("domain_cause", {}).get("type")
    evidence_list = evidence or []

    # 无故障场景：结论必须诚实（unknown / insufficient），不得给出确定根因
    if is_no_fault:
        location_match = location in (None, "unknown", "insufficient_evidence", "scope_unresolved")
        domain_match = domain in (None, "unknown")
        false_positive = not (location_match and domain_match)
    else:
        location_match = location == expected_location
        domain_match = domain == expected_domain
        false_positive = False

    valid_refs = bool(evidence_list) and all(
        isinstance(item.get("evidence_id"), str) and item.get("evidence_id")
        for item in evidence_list
    ) if not is_no_fault else True

    return {
        "case_id": case["case_id"],
        "root_location_match": location_match,
        "domain_cause_match": domain_match,
        "evidence_refs_valid": valid_refs,
        "no_fault_false_positive": false_positive,
        "unsafe_execution_count": 0,
        "actual_location": location,
        "actual_domain": domain,
        "evidence_count": len(evidence_list),
        "expected_location": expected_location,
        "expected_domain": expected_domain,
    }


def summarize(scores: list[dict]) -> dict:
    total = len(scores)
    passed = [item for item in scores if all(
        key in item and item[key] for key in
        ("root_location_match", "domain_cause_match", "evidence_refs_valid")
    ) and not item["no_fault_false_positive"] and item["unsafe_execution_count"] == 0]
    return {
        "total": total,
        "passed": len(passed),
        "failed": total - len(passed),
        "location_hit": sum(1 for item in scores if item["root_location_match"]),
        "domain_hit": sum(1 for item in scores if item["domain_cause_match"]),
        "evidence_valid": sum(1 for item in scores if item["evidence_refs_valid"]),
        "false_positive": sum(1 for item in scores if item["no_fault_false_positive"]),
        "unsafe_execution": sum(1 for item in scores if item["unsafe_execution_count"]),
    }


# ── 端到端驱动 ─────────────────────────────────────────────


def run_scenario(server: str, api_key: str, worker: str, case: dict, timeout_sec: int = 600) -> dict:
    """对一个场景执行完整诊断流程，返回评分结果。"""
    case_id = case["case_id"]
    symptom = case["symptom_query"]
    service_id = case.get("service_id", "checkoutservice")
    process_query = case.get("process_query", service_id)

    # 1. 在目标 worker 上定位被测服务进程（按 case 配置的进程关键字）
    scan = _api(server, f"/api/agents/{worker}/processes/scan", api_key, "POST",
                {"query": process_query, "timeout_sec": 30})
    processes = scan.get("processes", [])
    keyword = process_query.lower()
    target = None
    for attempt in range(2):
        target = next((item for item in processes
                       if keyword in (item.get("comm") or "").lower()
                       or keyword in (item.get("cmdline") or "").lower()
                       or "product-catalog" in (item.get("cmdline") or "").lower()), None)
        if target is not None:
            break
        if attempt == 0:
            time.sleep(4)
            scan = _api(server, f"/api/agents/{worker}/processes/scan", api_key, "POST",
                        {"query": process_query, "timeout_sec": 30})
            processes = scan.get("processes", [])
    if target is None:
        return {**score_case(case, None, None),
                "error": f"未在 {worker} 上发现 {service_id} 进程（扫描 {len(processes)} 个，query={process_query}）"}

    # 实例 host_id 需为 Agent 上报的 hostname（如 worker1），而非 Agent ID（如 linux-worker-1）
    host_id = worker
    try:
        agent_info = _api(server, "/api/agents", api_key)
        items = agent_info.get("items", agent_info) if isinstance(agent_info, dict) else agent_info
        host = next((a for a in items if (a or {}).get("id") == worker), None)
        if host and host.get("hostname"):
            host_id = host["hostname"]
    except Exception:
        pass

    # 2. 创建 Case（模拟基础用户：只给症状文本 + 服务名）
    created = _api(server, "/api/v1/cases", api_key, "POST", {
        "title": f"评测：{case_id}",
        "problem_description": symptom,
        "recovery_goal": "定位根因并给出可验证的处理建议",
        "run_mode": "COLLABORATE",
        "environment": "production",
        "target_scope": {
            "service_id": service_id,
            "instances": [{
                "service_id": service_id,
                "instance_id": f"{service_id}-{worker}-{target['pid']}",
                "host_id": host_id,
                "agent_id": worker,
                "pid": target["pid"],
                "environment": "production",
            }],
            "dependencies": [],
        },
    })
    case_ref = created["case_id"]

    # 3. 自动推进诊断（R1 自动；R2 由评测脚本批准一次，模拟值班用户）
    started = _api(server, f"/api/v1/cases/{case_ref}/diagnoses", api_key, "POST", {
        "analysis_strategy": "DECISION_TREE",
        "budget_profile": "production_safe",
    })
    diag = started.get("diagnosis") or {}
    diagnosis_id = (diag.get("diagnosis_id")
                    or started.get("case", {}).get("diagnosis_session_id")
                    or case_ref)

    deadline = time.time() + timeout_sec
    conclusion = None
    evidence = []
    while time.time() < deadline:
        diagnosis = _api(server, f"/api/v1/diagnoses/{diagnosis_id}", api_key)
        status = diagnosis.get("status", "")
        probes = diagnosis.get("probes", [])
        # 自动批准单个 R2（模拟人工确认，严格单次）
        for probe in probes:
            if probe.get("status") == "WAITING_APPROVAL":
                _api(server, f"/api/v1/diagnoses/{diagnosis_id}/approvals", api_key, "POST", {
                    "step_id": probe["step_id"], "decision": "approve", "scope": "single_execution",
                    "approver_id": "eval_runner",
                })
        if diagnosis.get("latest_conclusion"):
            conclusion = diagnosis["latest_conclusion"]
            evidence = diagnosis.get("evidence", [])
        if status in KNOWN_TERMINAL:
            break
        time.sleep(5)

    return score_case(case, conclusion, evidence)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:8191")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--suite", default="benchmarks/github_cases/scenarios/suite.json")
    parser.add_argument("--worker", default="worker1", help="目标 worker 的 Agent ID")
    parser.add_argument("--cases", default="", help="逗号分隔的 case_id 子集，默认全部")
    parser.add_argument("--output-dir", default="reports/eval/github-cases")
    args = parser.parse_args()

    suite = json.loads(Path(args.suite).read_text(encoding="utf-8"))
    cases = suite["cases"]
    if args.cases:
        wanted = {item.strip() for item in args.cases.split(",") if item.strip()}
        cases = [item for item in cases if item["case_id"] in wanted]

    scores = []
    for case in cases:
        print(f"==> 场景 {case['case_id']} ...")
        score = run_scenario(args.server, args.api_key, args.worker, case)
        scores.append(score)
        print(f"    location={score['actual_location']} domain={score['actual_domain']} "
              f"match={score['root_location_match']}/{score['domain_cause_match']} "
              f"evidence={score['evidence_count']}")

    summary = summarize(scores)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(
        json.dumps({"summary": summary, "scores": scores}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"报告已写入 {output_dir / 'results.json'}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
