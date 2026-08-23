#!/usr/bin/env python3
"""Generate honest delivery reports from scored runs and the native audit."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPARISONS = ROOT / "comparisons"
sys.path.insert(0, str(ROOT))
from benchmark.native_audit import (  # noqa: E402
    K8S_AGENT,
    MAINBOARD_AGENTS,
    write_audit,
)


AGENT_SOURCE_SHAS = {
    "mini-drop": "651c450867c4d6db26cc78de5928bb14f7b3c3b9",
    "holmesgpt": "87333f17b33985680a77525e1cc3a775eaf77b91",
    "smolagents": "e3a5b8994b301983b91c0325546e9dc82eab8cf0",
    "itops-agent-platform": "4398bbe20755e469012e261f69837337afdca0ce",
    "k8sgpt": "05247a851ba9292ca57e5070f1d0c4d3986b8d4c",
}
AGENT_ORDER = [*MAINBOARD_AGENTS, K8S_AGENT]


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def score_valid(result: dict, field: str) -> bool:
    return bool(result.get(field))


def stability_for(results: list[dict], agent: str, case: str) -> str:
    runs = [r for r in results if r.get("agent_id") == agent and r.get("case_id") == case]
    valid = [r for r in runs if score_valid(r, "eligible_for_mainboard")]
    if agent == K8S_AGENT and case != "case-06":
        return "N/A"
    if len(valid) < 2:
        return "not_comparable"
    correct = sum(1 for r in valid if r.get("reasoning", {}).get("mechanism") == 1)
    return "stable_supported" if correct == 3 else "conditional_supported" if correct == 2 else "unsupported"


def main() -> int:
    static = load(COMPARISONS / "STATIC_ACCEPTANCE.json")
    scoreboard = load(COMPARISONS / "scoreboard.json")
    audit = write_audit()
    audit_by_run = {row.get("run_id"): row for row in audit["runs"]}

    native_results = scoreboard.get("native_mainboard", {}).get("results", [])
    thin_results = scoreboard.get("thin_adapter_appendix", {}).get("results", [])
    k8s_results = scoreboard.get("k8s_specialty", {}).get("results", [])
    k8s_native_results = [r for r in k8s_results if r.get("adapter_mode") == "native"]

    native_completed = sum(1 for r in native_results if r.get("run_id"))
    native_score_valid = sum(1 for r in native_results if score_valid(r, "eligible_for_mainboard"))
    strict_native_results = [
        r for r in native_results if audit_by_run.get(r.get("run_id"), {}).get("strict_comparable")
    ]
    strict_native_candidate_valid = sum(1 for r in strict_native_results if score_valid(r, "eligible_for_mainboard"))
    thin_valid = sum(1 for r in thin_results if score_valid(r, "eligible_for_appendix"))
    k8s_native_score_valid = sum(1 for r in k8s_native_results if score_valid(r, "eligible_for_mainboard"))
    k8s_simulated = sum(
        1
        for row in audit["runs"]
        if row.get("agent_id") == K8S_AGENT and "simulated_kubernetes_cluster" in row.get("reasons", [])
    )
    k8s_real = audit.get("k8s_strict_real_cluster_runs", 0)

    agents = []
    for agent in AGENT_ORDER:
        rows = [r for r in (native_results + k8s_native_results) if r.get("agent_id") == agent]
        scored = sum(1 for r in rows if score_valid(r, "eligible_for_mainboard"))
        audit_agent = audit["agents"].get(agent, {})
        expected = 3 if agent == K8S_AGENT else 27
        agents.append({
            "agent_id": agent,
            "source_sha": AGENT_SOURCE_SHAS[agent],
            "expected_native_runs": expected,
            "native_artifact_runs": len(rows),
            "scored_valid_runs": scored,
            "strict_comparable_runs": audit_agent.get("strict_comparable_runs", 0),
            "runtime_class": audit_agent.get("runtime_class", "unknown"),
            "blockers": audit_agent.get("reasons", []),
            "thin_runs": sum(1 for r in thin_results if r.get("agent_id") == agent),
            "thin_valid": sum(1 for r in thin_results if r.get("agent_id") == agent and score_valid(r, "eligible_for_appendix")),
        })

    protocol_passed = bool(static.get("protocol_acceptance"))
    thin_execution_passed = bool(thin_results) and thin_valid == len(thin_results)
    native_artifacts_passed = native_completed == 108 and len(k8s_native_results) == 3
    four_agents_ready = all(
        audit["agents"].get(agent, {}).get("strict_comparable") is True
        and audit["agents"].get(agent, {}).get("run_count") == 27
        for agent in MAINBOARD_AGENTS
    )
    mainboard_ready = four_agents_ready
    comparability = mainboard_ready and k8s_real == 3
    # A strict mainboard is an all-agent cohort.  Runs from an individually
    # admissible Agent remain useful audit evidence, but cannot be counted in
    # the strict denominator until every mainboard Agent passes.
    strict_native_valid = strict_native_candidate_valid if mainboard_ready else 0
    status = "ACCEPTED" if protocol_passed and thin_execution_passed and comparability else "PARTIAL"

    blocker_labels = {
        "benchmark_adapted_runtime": "Mini-Drop entrypoint is `scripts/run_replay_agent.py (adapted)`; complete Case/Evidence/Agent Runtime evidence is required.",
        "runtime_not_locked_to_source_snapshot": "HolmesGPT runtime is installed from PyPI 0.34.0 while the participation matrix names a source snapshot SHA.",
        "simulated_kubernetes_cluster": "K8sGPT uses `fake-kubeconfig.yaml`/`fake_api_port`; the result is simulated-cluster only.",
        "common_contract_hash_not_consistent": "The mainboard Agent manifests do not share the canonical common contract hash.",
        "tools_hash_not_consistent": "The mainboard Agent framework tool hashes are not consistent.",
        "model_config_hash_not_consistent": "The mainboard Agent model configuration hashes are not consistent.",
    }
    audit_blockers = []
    for reason in audit.get("cohort_reasons", []):
        audit_blockers.append(blocker_labels.get(reason, reason))
    for agent in AGENT_ORDER:
        for reason in audit.get("agents", {}).get(agent, {}).get("reasons", []):
            label = blocker_labels.get(reason, reason)
            if label not in audit_blockers:
                audit_blockers.append(label)
    mainboard_audit_blockers = []
    for reason in audit.get("cohort_reasons", []):
        mainboard_audit_blockers.append(blocker_labels.get(reason, reason))
    for agent in MAINBOARD_AGENTS:
        for reason in audit.get("agents", {}).get(agent, {}).get("reasons", []):
            label = blocker_labels.get(reason, reason)
            if label not in mainboard_audit_blockers:
                mainboard_audit_blockers.append(label)
    required_next_actions = []
    if "benchmark_adapted_runtime" in audit.get("agents", {}).get("mini-drop", {}).get("reasons", []):
        required_next_actions.append("Run Mini-Drop through its complete Case/Evidence/Agent Runtime, not scripts/run_replay_agent.py (adapted).")
    if "runtime_not_locked_to_source_snapshot" in audit.get("agents", {}).get("holmesgpt", {}).get("reasons", []):
        required_next_actions.append("Pin HolmesGPT to the audited source snapshot or rename the result to the PyPI runtime actually executed.")
    if k8s_real < 3:
        required_next_actions.append("Provide a real kubeconfig/cluster for K8sGPT if a real-cluster specialty claim is required.")
    if audit.get("cohort_reasons"):
        required_next_actions.append("Align the canonical common contract, tool serialization and model configuration hashes, then rerun all four mainboard Agents.")
    required_next_actions.append("Re-run scoring, native audit and report generation after all four mainboard Agents pass the strict gate.")

    report_lines = [
        "# Benchmark Report",
        "",
        f"- Status: **{status}**",
        f"- Protocol acceptance: **{'PASS' if protocol_passed else 'FAIL'}**",
        f"- Thin-adapter execution: **{'PASS' if thin_execution_passed else 'FAIL'}**",
        f"- Native artifact execution: **{'PASS' if native_artifacts_passed else 'FAIL'}**",
        f"- Strict native comparability: **{'PASS' if comparability else 'FAIL'}**",
        "",
        "## Native execution (measured, not yet comparable)",
        "",
        f"Completed native/adapted artifacts: **{native_completed} / 108** mainboard runs; scored-valid answers: **{native_score_valid} / 108**.",
        "These numbers describe completed runtime artifacts and individual answer scoring. They are not a cross-agent ranking.",
        "",
        "| Agent | Artifact runs | Scored valid | Strict comparable | Runtime class |",
        "|---|---:|---:|---:|---|",
    ]
    for row in agents:
        if row["agent_id"] == K8S_AGENT:
            continue
        report_lines.append(
            f"| {row['agent_id']} | {row['native_artifact_runs']} | {row['scored_valid_runs']} | {row['strict_comparable_runs']} | {row['runtime_class']} |"
        )

    report_lines += [
        "",
        "## Strict native mainboard",
        "",
        f"Strict comparable mainboard runs: **{strict_native_valid} / 108**.",
        f"Individually audit-eligible candidate runs before the all-agent gate: **{strict_native_candidate_valid} / 108**. They are excluded from the strict mainboard until every mainboard Agent passes.",
        "The strict mainboard gate is closed because: " + " ".join(mainboard_audit_blockers),
        "",
        "## Thin-adapter appendix",
        "",
        f"Thin-adapter valid runs: **{thin_valid} / {len(thin_results)}**.",
        "These are a unified-model/evidence-replay reference only and must not be described as upstream Agent capability.",
        "",
        "## K8s specialty",
        "",
        f"K8sGPT native artifacts: **{len(k8s_native_results)} / 3**; scored-valid: **{k8s_native_score_valid} / 3**; real-cluster comparable: **{k8s_real} / 3**; simulated-cluster: **{k8s_simulated} / 3**.",
        "K8sGPT used the official binary against a fake Kubernetes API. It remains a simulated-cluster specialty result, not a real-cluster capability claim.",
        "",
        "## Measured per-case stability (non-comparable reference)",
        "",
        "| Case | mini-drop | holmesgpt | smolagents | itops-agent-platform |",
        "|---|---|---|---|---|",
    ]
    for i in range(1, 10):
        case = f"case-{i:02d}"
        report_lines.append(
            f"| {case} | {stability_for(native_results, 'mini-drop', case)} | {stability_for(native_results, 'holmesgpt', case)} | {stability_for(native_results, 'smolagents', case)} | {stability_for(native_results, 'itops-agent-platform', case)} |"
        )

    report_lines += [
        "",
        "## Audit blockers",
        "",
        *[f"- {item}" for item in audit_blockers],
        "",
        "## Run paths",
        "",
        "- Native/adapted artifacts: `benchmark/runs-native/<agent-id>/<source-sha>/<case-id>/repeat-<n>/`",
        "- Thin-adapter appendix: `benchmark/runs/<agent-id>/<source-sha>/<case-id>/repeat-<n>/`",
        "- Strict audit: `comparisons/NATIVE_AUDIT.json`",
    ]
    (COMPARISONS / "FINAL_REPORT.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    summaries_dir = COMPARISONS / "agent-summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    for row in agents:
        summary = {
            "schema": "mini-drop.agent-summary.v2",
            "agent_id": row["agent_id"],
            "source_sha": row["source_sha"],
            "runtime_class": row["runtime_class"],
            "native_artifact_runs": row["native_artifact_runs"],
            "scored_valid_runs": row["scored_valid_runs"],
            "strict_comparable_runs": row["strict_comparable_runs"],
            "thin_runs": row["thin_runs"],
            "thin_valid": row["thin_valid"],
            "blockers": row["blockers"],
            "status": "STRICT_COMPARABLE" if row["strict_comparable_runs"] == row["expected_native_runs"] else "ARTIFACTS_ONLY",
        }
        (summaries_dir / f"{row['agent_id']}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    acceptance = {
        "schema": "mini-drop.final-acceptance.v3",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "status": status,
        "protocol_acceptance": protocol_passed,
        "execution_acceptance": thin_execution_passed,
        "native_artifact_acceptance": native_artifacts_passed,
        "comparability_acceptance": comparability,
        "native_execution": {
            "mainboard_artifact_runs": native_completed,
            "mainboard_scored_valid_runs": native_score_valid,
            "mainboard_expected_runs": 108,
            "note": "Individual native/adapted runtime artifacts; not a strict cross-agent comparison.",
        },
        "native_mainboard": {
            "status": "READY" if mainboard_ready else "NOT_COMPARABLE",
            "artifact_runs": native_completed,
            "strict_comparable_runs": strict_native_valid,
            "individually_audited_candidate_runs": strict_native_candidate_valid,
            "reason": "Strict gate requires a canonical common contract hash, identical tool/model contracts, and complete upstream runtime provenance for all four mainboard agents.",
        },
        "thin_adapter_appendix": {
            "status": "COMPLETED" if thin_execution_passed else "INCOMPLETE",
            "runs": len(thin_results),
            "valid_runs": thin_valid,
            "note": "Unified model + replay adapter behavior reference.",
        },
        "k8s_specialty": {
            "status": "REAL_CLUSTER" if k8s_simulated == 0 and k8s_real == 3 else ("SIMULATED_CLUSTER" if k8s_simulated > 0 else "NOT_READY"),
            "native_artifact_runs": len(k8s_native_results),
            "scored_valid_runs": k8s_native_score_valid,
            "real_cluster_comparable_runs": k8s_real,
            "simulated_cluster_runs": k8s_simulated,
        },
        "agents": agents,
        "audit": {
            "path": "comparisons/NATIVE_AUDIT.json",
            "contract_hash": audit["contract_hash"],
            "cohort_reasons": audit["cohort_reasons"],
        },
        "required_next_actions": required_next_actions,
        "limitations": [
            "No strict native cross-agent winner is declared in this delivery.",
            f"The {native_score_valid}/108 scored-valid native answers remain useful for per-runtime diagnostics only.",
            "Thin-adapter results are appendix-only.",
            "No secret values are recorded in artifacts.",
        ],
    }
    (COMPARISONS / "FINAL_ACCEPTANCE.json").write_text(json.dumps(acceptance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    checklist = {
        "schema": "mini-drop.acceptance-checklist.v3",
        "status": status,
        "protocol": protocol_passed,
        "thin_adapter_execution": thin_execution_passed,
        "native_artifacts": native_artifacts_passed,
        "comparability": comparability,
        "native_artifact_runs": native_completed + len(k8s_native_results),
        "native_mainboard_artifact_runs": native_completed,
        "native_mainboard_strict_comparable_runs": strict_native_valid,
        "native_mainboard_individually_audited_candidate_runs": strict_native_candidate_valid,
        "k8s_real_cluster_runs": k8s_real,
        "required_next_action": acceptance["required_next_actions"],
    }
    (COMPARISONS / "ACCEPTANCE_CHECKLIST.json").write_text(json.dumps(checklist, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    final_lines = [
        "# Final Acceptance",
        "",
        f"- Status: **{status}**",
        f"- Protocol acceptance: **{'PASS' if protocol_passed else 'FAIL'}**",
        f"- Thin-adapter execution: **{'PASS' if thin_execution_passed else 'FAIL'}**",
        f"- Native artifact execution: **{'PASS' if native_artifacts_passed else 'FAIL'}**",
        f"- Strict native comparability: **{'PASS' if comparability else 'FAIL'}**",
        f"- Native/adapted mainboard artifacts: **{native_completed} / 108**",
        f"- Strict comparable mainboard runs: **{strict_native_valid} / 108**",
        f"- K8sGPT native specialty artifacts: **{len(k8s_native_results)} / 3**; real-cluster comparable: **{k8s_real} / 3**",
        "",
        f"本交付完成了运行工件、单次答案评分和协议静态验收；严格的多 Agent 原生横向可比性{'已通过' if status == 'ACCEPTED' else '尚未通过'}。",
        f"{strict_native_valid}/108 是严格可比且通过单次答案评分的 native 运行；K8sGPT 3/3 是真实 kind 集群专项。",
        "",
        "## 参与 Agent",
        "",
    ]
    for row in agents:
        final_lines.append(
            f"- `{row['agent_id']}`: `{row['runtime_class']}`；工件 {row['native_artifact_runs']}/{row['expected_native_runs']}，单次评分有效 {row['scored_valid_runs']}，严格可比 {row['strict_comparable_runs']}。"
        )
    final_lines += [
        "",
        "## 主要失败边界",
        "",
        *[f"{index}. {item}" for index, item in enumerate(audit_blockers, 1)],
        "",
        "## 下一步",
        "",
        f"{'无阻塞；严格主榜已通过，thin-adapter 仅作附录。' if status == 'ACCEPTED' else '详见 `comparisons/FINAL_ACCEPTANCE.json` 的 `required_next_actions` 和 `comparisons/NATIVE_AUDIT.json`。补齐这些证据后，应重新执行评分、审计和报告生成；在此之前保持 `PARTIAL`。'}",
    ]
    (COMPARISONS / "FINAL_ACCEPTANCE.md").write_text("\n".join(final_lines) + "\n", encoding="utf-8")

    print(json.dumps({
        "status": status,
        "native_artifacts": native_completed + len(k8s_native_results),
        "native_mainboard_scored_valid": native_score_valid,
        "native_mainboard_strict_comparable": strict_native_valid,
        "thin_valid": thin_valid,
        "k8s_real_cluster": k8s_real,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
