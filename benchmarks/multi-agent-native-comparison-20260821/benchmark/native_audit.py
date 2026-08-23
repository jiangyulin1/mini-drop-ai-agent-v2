#!/usr/bin/env python3
"""Audit native-run artifacts for strict cross-agent comparability.

The benchmark score answers whether an individual answer can be scored.  This
module answers a different question: whether the run is admissible in a
strict, multi-agent native comparison.  It intentionally treats missing
provenance as a failure instead of inferring it from a file name or an
``adapter_mode`` flag.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "benchmark"
COMPARISONS = ROOT / "comparisons"
MAINBOARD_AGENTS = ("mini-drop", "holmesgpt", "smolagents", "itops-agent-platform")
K8S_AGENT = "k8sgpt"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_contract_hash() -> str:
    return digest(BENCHMARK / "agent-contract-v1.json")


def run_manifests(root: Path | None = None) -> list[Path]:
    return sorted((root or (BENCHMARK / "runs-native")).glob("*/*/*/repeat-*/manifest.json"))


def _read_optional(path: Path) -> dict[str, Any]:
    try:
        return load(path)
    except (OSError, json.JSONDecodeError):
        return {}


def _has_text(value: Any, needle: str) -> bool:
    return needle.lower() in str(value or "").lower()


def _common_contract_value(manifest: dict[str, Any], runtime: dict[str, Any], input_hashes: dict[str, Any]) -> Any:
    for source in (manifest, runtime, input_hashes):
        if source.get("common_contract_hash"):
            return source["common_contract_hash"]
    return None


def audit_run(manifest_path: Path, contract_hash: str | None = None) -> dict[str, Any]:
    """Return a deterministic audit record for one native run."""

    manifest = _read_optional(manifest_path)
    run_dir = manifest_path.parent
    runtime = _read_optional(run_dir / "native-runtime.json")
    input_hashes = _read_optional(run_dir / "input-hashes.json")
    agent = str(manifest.get("agent_id") or "unknown")
    reasons: list[str] = []
    contract_hash = contract_hash or canonical_contract_hash()

    if manifest.get("adapter_mode") != "native":
        reasons.append("adapter_mode_not_native")
    if manifest.get("native_runtime") is not True or runtime.get("native_runtime") is not True:
        reasons.append("native_runtime_flag_missing")
    if manifest.get("status") != "completed":
        reasons.append("run_not_completed")
    # Reject empty final answers that are not abstentions.
    try:
        answer = load(run_dir / "normalized-answer.json")
        conclusion = str(answer.get("conclusion") or "").strip()
        abstain = bool(answer.get("abstain"))
        if not conclusion and not abstain:
            reasons.append("empty_final_answer")
    except (OSError, json.JSONDecodeError):
        reasons.append("normalized_answer_missing")
    if not (run_dir / "native-trace.jsonl").is_file() or not (run_dir / "native-trace.jsonl").read_text(encoding="utf-8").strip():
        reasons.append("native_trace_missing_or_empty")
    if not manifest.get("native_trace_hash"):
        reasons.append("native_trace_hash_missing")
    else:
        trace_file = run_dir / "native-trace.jsonl"
        if trace_file.is_file():
            actual = digest(trace_file)
            if actual != manifest.get("native_trace_hash"):
                reasons.append("native_trace_hash_mismatch")
        else:
            reasons.append("native_trace_missing_or_empty")

    # ``source_sha`` is the v2 field used by the existing artifacts.  Accept
    # it as the pinned framework SHA while requiring the same value in the
    # runtime record; future runs should use framework_source_sha explicitly.
    manifest_sha = manifest.get("framework_source_sha") or manifest.get("source_sha")
    runtime_sha = runtime.get("framework_source_sha") or runtime.get("source_sha")
    if not manifest_sha or not runtime_sha or manifest_sha != runtime_sha:
        reasons.append("framework_source_provenance_missing_or_mismatch")
    if not runtime.get("dependency_lock_hash"):
        reasons.append("dependency_lock_hash_missing")

    common_contract = _common_contract_value(manifest, runtime, input_hashes)
    if common_contract != contract_hash:
        reasons.append("common_contract_hash_missing_or_mismatch")

    if agent == "mini-drop":
        if _has_text(manifest.get("framework_entrypoint"), "adapted") or _has_text(runtime.get("framework_entrypoint"), "adapted"):
            reasons.append("benchmark_adapted_runtime")
        sidecar_source = str(runtime.get("sidecar_source") or "")
        if "benchmark/work/pi_sidecar.py" in sidecar_source or "benchmark/work/pi_sidecar" in sidecar_source:
            reasons.append("unofficial_pi_sidecar")
        if "agent_runtime/pi-sidecar" not in sidecar_source:
            reasons.append("official_pi_sidecar_missing")
        if not runtime.get("sidecar_package_lock_hash"):
            reasons.append("sidecar_package_lock_hash_missing")
        # Require at least one tool call/result in trace for Mini-Drop
        trace_text = (run_dir / "native-trace.jsonl").read_text(encoding="utf-8", errors="replace") if (run_dir / "native-trace.jsonl").is_file() else ""
        if not any(k in trace_text.lower() for k in ("tool_call", "tool_call_id", "tool_call_result", "tool result", "tool_result", "tool_execution")):
            reasons.append("mini_drop_tool_trace_missing")
    elif agent == "holmesgpt":
        source_path = runtime.get("source_path")
        if _has_text(source_path, "pypi") or runtime.get("dependency_version"):
            reasons.append("runtime_not_locked_to_source_snapshot")
        if manifest.get("framework_entrypoint") != "ToolCallingLLM.call()":
            reasons.append("unexpected_holmes_entrypoint")
    elif agent == "smolagents":
        if manifest.get("framework_entrypoint") != "ToolCallingAgent.run()":
            reasons.append("unexpected_smolagents_entrypoint")
    elif agent == "itops-agent-platform":
        if runtime.get("http_backend") is not True or not runtime.get("backend_url"):
            reasons.append("headless_backend_provenance_missing")
        if runtime.get("process_id") is None and not runtime.get("container_id"):
            reasons.append("process_or_container_identifier_missing")
    elif agent == K8S_AGENT:
        if runtime.get("fake_api_port") or _has_text(runtime.get("kubeconfig"), "fake"):
            reasons.append("simulated_kubernetes_cluster")
        if runtime.get("real_cluster") is not True:
            reasons.append("real_cluster_missing")
        if not runtime.get("fault_injection_yaml_hash"):
            reasons.append("fault_injection_yaml_hash_missing")
        if not runtime.get("object_snapshot_paths"):
            reasons.append("k8s_object_snapshot_missing")
        if not runtime.get("mapping"):
            reasons.append("k8s_fault_mapping_missing")

    if case_id := str(manifest.get("case_id") or ""):
        if case_id in {"case-07", "case-08", "case-09"}:
            int_file = run_dir / "interventions.jsonl"
            int_text = int_file.read_text(encoding="utf-8", errors="replace") if int_file.is_file() else ""
            if not int_text.strip():
                reasons.append("intervention_trace_missing")
            elif "fallback" in int_text.lower():
                reasons.append("intervention_trace_fallback_only")
            try:
                for line in int_text.splitlines():
                    if not line.strip():
                        continue
                    ev = json.loads(line)
                    rr = ev.get("review_response") or {}
                    if isinstance(rr, dict) and (rr.get("http_error") or (isinstance(rr.get("code"), int) and rr.get("code") != 0)):
                        reasons.append("evidence_review_api_failed")
                        break
            except Exception:
                reasons.append("intervention_trace_unparseable")
            trace_text = (run_dir / "native-trace.jsonl").read_text(encoding="utf-8", errors="replace") if (run_dir / "native-trace.jsonl").is_file() else ""
            if "intervention" not in trace_text.lower():
                reasons.append("intervention_not_in_native_trace")

    return {
        "run_id": manifest.get("run_id"),
        "agent_id": agent,
        "case_id": manifest.get("case_id"),
        "repeat": manifest.get("repeat"),
        "case_public_hash": manifest.get("case_public_hash"),
        "manifest_path": str(manifest_path.relative_to(ROOT)),
        "adapter_mode": manifest.get("adapter_mode"),
        "native_runtime": manifest.get("native_runtime"),
        "framework_entrypoint": manifest.get("framework_entrypoint"),
        "framework_source_sha": manifest_sha,
        "runtime_source_sha": runtime_sha,
        "tools_hash": manifest.get("tools_hash"),
        "common_contract_hash": common_contract,
        "prompt_hash": manifest.get("prompt_hash"),
        "model_identifier": manifest.get("model_identifier"),
        "model_config_hash": manifest.get("model_config_hash"),
        "reasons": sorted(set(reasons)),
        "strict_comparable": not reasons,
    }


def _score_valid(run_dir: Path) -> bool:
    score = _read_optional(run_dir / "score.json")
    return bool(score.get("eligible_for_mainboard"))


def audit_workspace() -> dict[str, Any]:
    """Audit all native artifacts and apply cohort-level consistency gates."""

    contract_hash = canonical_contract_hash()
    records = [audit_run(path, contract_hash) for path in run_manifests()]
    mainboard = [r for r in records if r["agent_id"] in MAINBOARD_AGENTS]
    k8s = [r for r in records if r["agent_id"] == K8S_AGENT]

    cohort_reasons: list[str] = []
    for field, label in (("tools_hash", "tools_hash_not_consistent"), ("prompt_hash", "prompt_hash_not_consistent"), ("model_identifier", "model_identifier_not_consistent"), ("model_config_hash", "model_config_hash_not_consistent")):
        values = {r.get(field) for r in mainboard if r.get(field) is not None}
        if len(values) != 1:
            cohort_reasons.append(label)

    common_values = {r.get("common_contract_hash") for r in mainboard}
    if common_values != {contract_hash}:
        cohort_reasons.append("common_contract_hash_not_consistent")

    for case_id in sorted({str(r.get("case_id")) for r in mainboard}):
        values = {r.get("case_public_hash") for r in mainboard if r.get("case_id") == case_id}
        if len(values) != 1:
            cohort_reasons.append(f"case_public_hash_not_consistent:{case_id}")

    # Mark cohort failures on every mainboard run.  This is deliberately
    # separate from per-run score eligibility.
    for record in mainboard:
        record["cohort_reasons"] = list(cohort_reasons)
        if cohort_reasons:
            record["reasons"] = sorted(set(record["reasons"] + cohort_reasons))
            record["strict_comparable"] = False

    agents: dict[str, dict[str, Any]] = {}
    for agent in (*MAINBOARD_AGENTS, K8S_AGENT):
        rows = [r for r in records if r["agent_id"] == agent]
        measured_valid = 0
        for row in rows:
            measured_valid += int(_score_valid((ROOT / row["manifest_path"]).parent))
        reasons = sorted({reason for row in rows for reason in row["reasons"]})
        agents[agent] = {
            "agent_id": agent,
            "run_count": len(rows),
            "measured_score_valid_runs": measured_valid,
            "strict_comparable_runs": sum(1 for row in rows if row["strict_comparable"]),
            "strict_comparable": bool(rows) and all(row["strict_comparable"] for row in rows),
            "reasons": reasons,
            "runtime_class": (
                "simulated-cluster" if agent == K8S_AGENT and "simulated_kubernetes_cluster" in reasons
                else "native-adapted-runtime" if agent == "mini-drop" and "benchmark_adapted_runtime" in reasons
                else "pypi-runtime-source-mismatch" if agent == "holmesgpt" and "runtime_not_locked_to_source_snapshot" in reasons
                else "native-runtime"
            ),
        }

    return {
        "schema": "mini-drop.native-audit.v1",
        "contract_hash": contract_hash,
        "native_artifact_runs": len(records),
        "mainboard_artifact_runs": len(mainboard),
        "mainboard_strict_comparable_runs": sum(1 for row in mainboard if row["strict_comparable"]),
        "mainboard_measured_score_valid_runs": sum(1 for row in mainboard if _score_valid((ROOT / row["manifest_path"]).parent)),
        "k8s_artifact_runs": len(k8s),
        "k8s_strict_real_cluster_runs": sum(1 for row in k8s if row["strict_comparable"]),
        "cohort_reasons": sorted(set(cohort_reasons)),
        "agents": agents,
        "runs": records,
    }


def write_audit(path: Path | None = None) -> dict[str, Any]:
    payload = audit_workspace()
    output = path or (COMPARISONS / "NATIVE_AUDIT.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(write_audit(), ensure_ascii=False, indent=2))
