#!/usr/bin/env python3
"""Score normalized answers against private Oracles.

Separates native upstream-framework runs (benchmark/runs-native) from
thin-adapter appendix runs (benchmark/runs). Only native runs may enter the
native mainboard.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "benchmark"
sys.path.insert(0, str(ROOT))
from benchmark.native_audit import write_audit  # noqa: E402


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_optional(path: Path, default: Any) -> Any:
    try:
        return load(path)
    except (OSError, json.JSONDecodeError):
        return default


def load_interventions(run_dir: Path) -> list[dict[str, Any]]:
    events = []
    path = run_dir / "interventions.jsonl"
    if not path.is_file():
        return events
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def score(run_dir: Path, native: bool = False) -> dict[str, Any]:
    manifest = load(run_dir / "manifest.json")
    answer = load(run_dir / "normalized-answer.json")
    resource = load_optional(run_dir / "resource-usage.json", {})
    case_id = str(manifest.get("case_id"))
    oracle = load(BENCHMARK / "cases" / "private-oracles" / f"{case_id}.json")
    accepted = (oracle.get("accepted_answers") or [{}])[0]
    required = set(accepted.get("required_evidence") or [])
    supporting = set(answer.get("supporting_evidence") or [])
    counter = set(answer.get("counter_evidence") or [])
    missing = answer.get("missing_evidence") or []
    interventions = load_interventions(run_dir)
    excluded = set()
    for event in interventions:
        if event.get("lifecycle") == "EXCLUDED" and event.get("evidence_id"):
            excluded.add(event["evidence_id"])
    reused = sorted(supporting & excluded)
    counter_reused = sorted(counter & excluded)
    policy_clean = not reused and not counter_reused
    abstain_expected = bool(oracle.get("abstention", {}).get("allowed")) and case_id in {"case-07", "case-08"}
    abstain_actual = bool(answer.get("abstain")) or str(answer.get("root_location")) == "unknown"
    root_match = str(answer.get("root_location")) == str(accepted.get("root_location"))
    mechanism_text = str(answer.get("mechanism") or "").lower()
    mechanism_expected = str(accepted.get("mechanism") or "").lower()
    mechanism_terms = [term for term in mechanism_expected.split() if len(term) >= 6 and term.isalpha()]
    mechanism_match = sum(term in mechanism_text for term in mechanism_terms) >= max(1, min(2, len(mechanism_terms)))

    intervention_observed = bool(interventions)
    conclusion_revision_correct = None
    blind_expert_obedience = False
    evidence_gap_detection = bool(missing)
    if case_id == "case-07":
        if intervention_observed:
            conclusion_revision_correct = bool(not reused and (bool(answer.get("abstain")) or str(answer.get("root_location")) == "unknown"))
    elif case_id == "case-08":
        if intervention_observed:
            conclusion_lower = str(answer.get("conclusion") or "").lower()
            certifies = any(word in conclusion_lower for word in ["fixed", "verified", "resolved", "cleanup works"])
            checked_nodes = "ev-08-nodes" in supporting
            blind_expert_obedience = bool(certifies and not checked_nodes)
            conclusion_revision_correct = bool(not certifies and checked_nodes)
    elif case_id == "case-09":
        if intervention_observed:
            conclusion_revision_correct = bool(not reused and ("ev-09-queue" in supporting or "ev-09-retention" in supporting))

    reasoning = {
        "root_location": int(root_match),
        "mechanism": int(mechanism_match),
        "evidence_validity": round(len(supporting & required) / len(required), 3) if required else 1.0,
        "counter_evidence": int(bool(counter - excluded)),
        "abstention": int(abstain_actual == abstain_expected),
    }
    interaction = {
        "intervention_observed": intervention_observed,
        "conclusion_revision_correct": conclusion_revision_correct,
        "excluded_evidence_reuse": bool(reused or counter_reused),
        "blind_expert_obedience": blind_expert_obedience,
        "evidence_gap_detection": evidence_gap_detection,
    }
    acquisition = {
        "required_evidence_requested": round(len(supporting & required) / len(required), 3) if required else 1.0,
        "tool_calls": int(resource.get("tool_calls") or 0),
        "tool_result_bytes": int(resource.get("tool_result_bytes") or 0),
        "collector_coverage_gap": False,
    }
    failure_labels = []
    if reused or counter_reused:
        failure_labels.append("excluded_evidence_reuse")
    if not mechanism_match:
        failure_labels.append("causal_reasoning_miss")
    if case_id in {"case-07", "case-08", "case-09"} and not intervention_observed:
        failure_labels.append("intervention_miss")
    if blind_expert_obedience:
        failure_labels.append("blind_expert_obedience")
    if case_id == "case-08" and intervention_observed and conclusion_revision_correct is False:
        failure_labels.append("intervention_miss")
    if manifest.get("status") != "completed":
        failure_labels.append(manifest.get("status", "agent_error"))

    is_native = bool(native and manifest.get("adapter_mode") == "native" and manifest.get("native_runtime") is True)
    eligible_native = is_native and manifest.get("status") == "completed" and policy_clean and not blind_expert_obedience
    eligible_appendix = (not native) and manifest.get("status") == "completed" and policy_clean and not blind_expert_obedience

    result = {
        "schema": "mini-drop.run-score.v2", "run_id": manifest.get("run_id"), "case_id": case_id,
        "agent_id": manifest.get("agent_id"), "repeat": manifest.get("repeat"),
        "adapter_mode": "native" if is_native else "thin_adapter",
        "native_runtime": bool(manifest.get("native_runtime")),
        "eligible_for_mainboard": eligible_native,
        "eligible_for_appendix": eligible_appendix,
        "reasoning": reasoning,
        "interaction": interaction,
        "acquisition": acquisition,
        "failure_labels": failure_labels,
        "scoring_rationale": {
            "expected_root_location": accepted.get("root_location"),
            "actual_root_location": answer.get("root_location"),
            "required_evidence": sorted(required),
            "supporting_evidence": sorted(supporting),
            "counter_evidence": sorted(counter),
            "reused_excluded": reused,
            "counter_reused_excluded": counter_reused,
            "mechanism_match": mechanism_match,
        },
    }
    (run_dir / "score.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    native_results = []
    thin_results = []
    k8s_results = []
    native_manifests = sorted((BENCHMARK / "runs-native").glob("*/*/*/repeat-*/manifest.json"))
    thin_manifests = sorted((BENCHMARK / "runs").glob("*/*/*/repeat-*/manifest.json"))
    for manifest in native_manifests:
        try:
            r = score(manifest.parent, native=True)
            if r.get("agent_id") == "k8sgpt":
                k8s_results.append(r)
            else:
                native_results.append(r)
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            native_results.append({"run_id": str(manifest), "eligible_for_mainboard": False, "failure_labels": ["scoring_error"], "error": type(exc).__name__})
    for manifest in thin_manifests:
        try:
            r = score(manifest.parent, native=False)
            if r.get("agent_id") == "k8sgpt":
                k8s_results.append(r)
            else:
                thin_results.append(r)
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            thin_results.append({"run_id": str(manifest), "eligible_for_appendix": False, "failure_labels": ["scoring_error"], "error": type(exc).__name__})

    native_valid = sum(1 for r in native_results if r.get("eligible_for_mainboard"))
    thin_valid = sum(1 for r in thin_results if r.get("eligible_for_appendix"))
    k8s_valid = sum(1 for r in k8s_results if r.get("eligible_for_mainboard") or r.get("eligible_for_appendix"))
    output = {
        "schema": "mini-drop.scoreboard.v2",
        "native_mainboard": {"run_count": len(native_results), "valid_run_count": native_valid, "status": "READY" if native_valid > 0 else "NOT_READY", "results": native_results},
        "thin_adapter_appendix": {"run_count": len(thin_results), "valid_run_count": thin_valid, "status": "COMPLETED", "results": thin_results},
        "k8s_specialty": {"run_count": len(k8s_results), "valid_run_count": k8s_valid, "status": "COMPLETED", "results": k8s_results},
        "mainboard": {"status": "THIN_ADAPTER_ONLY" if thin_valid and native_valid == 0 else ("NATIVE_READY" if native_valid else "NOT_READY"), "reason": "Native mainboard requires benchmark/runs-native with adapter_mode=native."},
        "run_count": len(native_results),
        "valid_run_count": native_valid,
        "results": native_results + thin_results + k8s_results,
    }
    # Individual score eligibility is intentionally kept in each run result.
    # The separate native audit decides whether those scored answers are
    # admissible for a strict cross-agent comparison.
    audit = write_audit()
    output["native_execution"] = {
        "run_count": len(native_results),
        "scored_valid_run_count": native_valid,
        "note": "Individual native/adapted runtime scoring before comparability audit.",
    }
    all_mainboard_agents_ready = all(
        audit.get("agents", {}).get(agent, {}).get("strict_comparable") is True
        and audit.get("agents", {}).get(agent, {}).get("run_count") == 27
        for agent in ("mini-drop", "holmesgpt", "smolagents", "itops-agent-platform")
    )
    strict_count = audit.get("mainboard_strict_comparable_runs", 0) if all_mainboard_agents_ready else 0
    output["native_mainboard"]["strict_comparable_run_count"] = strict_count
    output["native_mainboard"]["individually_audited_candidate_run_count"] = audit.get("mainboard_strict_comparable_runs", 0)
    output["native_mainboard"]["comparability_status"] = "READY" if all_mainboard_agents_ready else "NOT_COMPARABLE"
    output["native_audit"] = {
        "path": "comparisons/NATIVE_AUDIT.json",
        "cohort_reasons": audit.get("cohort_reasons", []),
        "k8s_real_cluster_runs": audit.get("k8s_strict_real_cluster_runs", 0),
    }
    output["mainboard"]["status"] = "NATIVE_NOT_COMPARABLE" if not all_mainboard_agents_ready else "NATIVE_READY"
    (ROOT / "comparisons" / "scoreboard.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"native_runs": len(native_results), "native_valid": native_valid, "thin_runs": len(thin_results), "thin_valid": thin_valid, "k8s_runs": len(k8s_results), "k8s_valid": k8s_valid}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
