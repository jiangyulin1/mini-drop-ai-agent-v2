#!/usr/bin/env python3
"""Build the deterministic 30-case fair replay suite.

The generated public files contain only incident symptoms and evidence
projections.  Root-cause expectations stay in private/oracles.json.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

CASES = [
    ("self_cpu_hotspot", "self", "cpu", "self_code_or_process_pressure", "A request handler is slower while traffic is stable; identify the bounded hot path.", ["cpu_profile_topn", "latency_context"], ["cpu hotspot", "self time", "bounded path"]),
    ("self_cpu_regression", "self", "cpu", "self_code_or_process_pressure", "CPU rises after a release and p99 increases only on one endpoint.", ["cpu_profile_topn", "release_comparison", "latency_context"], ["regression", "new symbol", "endpoint"]),
    ("memory_leak", "self", "memory", "memory_retention", "Resident memory grows across equal work units and does not return after collection.", ["rss_timeseries", "heap_retainers", "gc_context"], ["retained", "dominator", "growth"]),
    ("memory_capacity_boundary", "self", "memory", "retained_capacity_not_leak", "Memory remains high after a cache shrinks; decide whether this proves a leak.", ["rss_timeseries", "capacity_snapshot", "object_liveness"], ["capacity", "reachable", "not proof of leak"]),
    ("mutex_wait", "self", "runtime", "runtime_lock_contention", "Throughput falls as concurrency rises although CPU is available.", ["runtime_wait_profile", "throughput_context", "cpu_utilization"], ["mutex", "wait", "owner"]),
    ("pool_lock", "self", "runtime", "runtime_lock_contention", "Requests queue behind a shared connection pool during a burst.", ["pool_queue", "runtime_wait_profile", "request_latency"], ["pool", "queue", "critical section"]),
    ("network_latency", "downstream", "network", "network_degradation", "A service meets CPU targets but end-to-end latency follows a remote hop.", ["network_rtt", "span_edges", "request_latency"], ["rtt", "remote hop", "latency"]),
    ("network_loss", "downstream", "network", "network_degradation", "Retries and tail latency increase with packet loss on one route.", ["network_errors", "retry_rate", "route_health"], ["loss", "retransmit", "route"]),
    ("redis_saturation", "downstream", "database", "downstream_dependency", "Checkout latency rises with cache command wait and downstream saturation.", ["dependency_latency", "redis_queue", "client_errors"], ["redis", "downstream", "queue"]),
    ("payment_timeout", "downstream", "network", "downstream_dependency", "Payment calls time out while local request processing remains healthy.", ["dependency_latency", "timeout_logs", "local_cpu"], ["payment", "timeout", "remote"]),
    ("same_host_cpu", "same_host", "cpu", "same_host_noisy_neighbor", "The target slows while an unrelated process consumes host CPU.", ["host_cpu_by_process", "target_latency", "cgroup_cpu"], ["noisy neighbor", "host", "unrelated process"]),
    ("same_host_memory", "same_host", "memory", "host_resource_contention", "Page reclaim and target stalls coincide with another workload's memory spike.", ["host_memory", "major_faults", "target_rss"], ["reclaim", "host pressure", "neighbor"]),
    ("disk_io", "shared_resource", "io", "host_resource_contention", "Write latency rises during a host-wide storage queue spike.", ["device_latency", "io_queue", "target_write_rate"], ["device", "queue", "contention"]),
    ("fd_exhaustion", "self", "runtime", "fd_exhaustion", "New connections fail after file descriptor usage approaches the process limit.", ["fd_usage", "error_logs", "connection_rate"], ["file descriptor", "limit", "leak"]),
    ("gc_pause", "self", "runtime", "runtime_stall", "Tail latency has sawtooth pauses aligned with stop-the-world collection.", ["gc_pause", "latency_timeseries", "allocation_rate"], ["gc", "pause", "allocation"]),
    ("scheduler_jitter", "self", "runtime", "runtime_stall", "Periodic reconciliation misses its deadline without sustained CPU saturation.", ["scheduler_ticks", "deadline_misses", "run_queue"], ["periodic", "scheduler", "jitter"]),
    ("insufficient_evidence", "unknown", "unknown", "insufficient_evidence", "The incident is real but the available projections do not identify scope or mechanism.", ["quality_summary", "partial_timeline"], ["insufficient", "unknown", "missing"]),
    ("stale_evidence", "unknown", "unknown", "stale_evidence", "Evidence is outside the active window and cannot establish the current cause.", ["stale_metric", "current_symptom"], ["stale", "window", "cannot establish"]),
    ("conflicting_evidence", "unknown", "unknown", "conflicting_sources", "Two trusted sources disagree on whether the bottleneck is local or downstream.", ["source_a", "source_b", "quality_summary"], ["conflict", "disagree", "abstain"]),
    ("expert_hint_unverified", "self", "memory", "memory_retention", "An operator claims a cleanup fixed memory, but post-change retention evidence is available.", ["post_change_rss", "heap_retainers", "operator_note"], ["cleanup", "retained", "verify"]),
    ("scope_correction", "downstream", "network", "network_degradation", "The initial service scope is broad; a trace edge narrows the fault to a remote dependency.", ["scope_candidates", "span_edges", "dependency_latency"], ["scope", "remote", "edge"]),
    ("memory_lock_compound", "self", "runtime", "compound_incident", "A retention increase amplifies lock wait and causes a concurrency collapse.", ["heap_retainers", "runtime_wait_profile", "throughput_context"], ["retention", "lock", "amplifies"]),
    ("disk_network_compound", "self", "io", "compound_incident", "A full local spool adds I/O delay while retries increase network traffic.", ["filesystem_usage", "device_latency", "retry_rate"], ["spool", "disk", "retries"]),
    ("noisy_downstream_compound", "same_host", "cpu", "compound_incident", "A host neighbor consumes CPU and the downstream dependency also shows saturation.", ["host_cpu_by_process", "dependency_latency", "target_latency"], ["neighbor", "downstream", "two contributors"]),
    ("fd_downstream_compound", "self", "runtime", "compound_incident", "A connection leak exhausts descriptors and turns a downstream slowdown into local failures.", ["fd_usage", "dependency_latency", "error_logs"], ["descriptor", "downstream", "amplifier"]),
    ("cpu_gc_compound", "self", "cpu", "compound_incident", "A CPU-heavy serialization path increases allocation and produces periodic pauses.", ["cpu_profile_topn", "allocation_rate", "gc_pause"], ["serialization", "allocation", "pause"]),
    ("evidence_review_exclusion", "self", "cpu", "self_code_or_process_pressure", "One profile sample is mis-scoped; the remaining aligned metrics point to a local CPU path.", ["mis_scoped_profile", "aligned_profile", "latency_context"], ["scope", "aligned", "local"]),
    ("hypothesis_challenge", "downstream", "database", "downstream_dependency", "A plausible cache hypothesis is challenged by a dependency queue projection.", ["cache_health", "dependency_queue", "request_latency"], ["challenge", "queue", "dependency"]),
    ("plan_reprioritize", "self", "io", "filesystem_exhaustion", "The first remediation plan targets CPU, but capacity and write errors require storage triage.", ["filesystem_usage", "error_logs", "cpu_profile_topn"], ["capacity", "write error", "reprioritize"]),
    ("healthy_negative", "unknown", "unknown", "no_incident_confirmed", "All aligned signals are within baseline and no degradation is confirmed.", ["baseline_comparison", "quality_summary"], ["baseline", "healthy", "no incident"]),
]

INTERVENTION_TYPES = [
    "HYPOTHESIS_CHALLENGE", "PLAN_REPRIORITIZE", "EVIDENCE_REVIEW", "SCOPE_CORRECTION", "OPERATOR_HINT_UNVERIFIED",
]

def h(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()

def build():
    public_cases = []
    replay_cases = []
    oracles = []
    interventions = []
    for n, (family, location, domain, classification, symptom, kinds, keywords) in enumerate(CASES, 1):
        cid = f"case-{n:02d}"
        evidence = []
        for idx, kind in enumerate(kinds, 1):
            eid = f"ev-{n:02d}-{idx:02d}"
            projection = {
                "signal": kind,
                "window": "T0..T1",
                "sample_count": 120,
                "value": round(0.17 * n + idx * 0.031, 3),
                "alignment": "aligned" if family not in {"stale_evidence", "insufficient_evidence", "conflicting_evidence"} else ("stale" if family == "stale_evidence" else "partial"),
                "redacted_dimensions": ["service", "host", "request_id"],
                "observations": keywords,
            }
            if family == "conflicting_evidence" and idx == 2:
                projection["claim"] = "downstream"
                projection["conflicts_with"] = "ev-19-01"
            if family == "healthy_negative":
                projection["baseline_delta"] = 0.02
            item = {
                "evidence_id": eid,
                "kind": kind,
                "summary": f"Bounded {kind.replace('_', ' ')} projection for the active window.",
                "source_class": "SOURCE_DERIVED",
                "observed_at": "T1",
                "lifecycle": "ACTIVE",
                "trust": "TRUSTED",
                "query_capabilities": ["query_metrics" if "profile" not in kind and "log" not in kind else ("get_profile_topn" if "profile" in kind else "search_logs")],
                "projection": projection,
                "integrity_hash": h(projection),
            }
            evidence.append(item)
        public_cases.append({
            "schema": "mini-drop.public-case.v1",
            "case_id": cid,
            "suite": "expert-intervention-fair-30",
            "incident": {"symptom": symptom, "service_scope": {"service": "redacted-service", "environment": "production-like"}, "time_window": {"start": "T0", "end": "T1"}},
            "evidence_index": [{k: item[k] for k in ("evidence_id", "kind", "summary", "source_class", "observed_at", "integrity_hash", "lifecycle", "trust", "query_capabilities")} for item in evidence],
            "tracks": ["fair_same_data", "expert_intervention_tuning"],
            "budget": {"max_tool_calls": 16, "max_return_bytes": 65536},
        })
        replay_cases.append({"schema": "mini-drop.replay-pack.v1", "case_id": cid, "evidence": evidence, "limits": {"max_tool_calls": 16, "max_single_result_bytes": 65536, "max_total_result_bytes": 524288}})
        event_type = INTERVENTION_TYPES[(n - 1) % len(INTERVENTION_TYPES)]
        required_items = evidence[1:3] if event_type == "EVIDENCE_REVIEW" else evidence[:min(2, len(evidence))]
        oracles.append({
            "case_id": cid,
            "family": family,
            "accepted_answers": [{"root_location": location, "domain_type": domain, "classification": classification, "mechanism": " ".join(keywords), "mechanism_keywords": keywords, "required_evidence": [x["evidence_id"] for x in required_items]}],
            "abstention": {"allowed": location == "unknown", "required": location == "unknown"},
            "expert_track": {"must_re_read_evidence": True, "must_update_hypothesis": True, "event_type": INTERVENTION_TYPES[(n - 1) % len(INTERVENTION_TYPES)]},
        })
        event = {"event_id": f"i-{n:02d}-1", "type": event_type, "trigger": "after_first_supported_answer", "trust": "UNVERIFIED" if event_type == "OPERATOR_HINT_UNVERIFIED" else "TRUSTED", "content": f"Expert intervention for {cid}: re-evaluate the active evidence before the second answer."}
        if event_type == "EVIDENCE_REVIEW":
            event["evidence_id"] = f"ev-{n:02d}-01"
            event["decision"] = "EXCLUDED"
            event["reason"] = "scope or alignment review requires this item to be excluded"
        interventions.append({"schema": "mini-drop.intervention.v1", "case_id": cid, "trigger": {"event": "after_first_supported_answer", "type": "state_based"}, "events": [event], "expected_post_state": {"must": ["re_read_active_evidence", "revise_or_confirm_hypothesis"], "must_not": "blindly_accept_operator_claim"}})
        (ROOT / "cases/public" / f"{cid}.json").write_text(json.dumps(public_cases[-1], ensure_ascii=False, indent=2) + "\n")
        (ROOT / "cases/replay" / f"{cid}.json").write_text(json.dumps(replay_cases[-1], ensure_ascii=False, indent=2) + "\n")
        (ROOT / "interventions" / f"{cid}.json").write_text(json.dumps(interventions[-1], ensure_ascii=False, indent=2) + "\n")
    (ROOT / "private/oracles.json").write_text(json.dumps({"schema": "mini-drop.private-oracle-suite.v1", "cases": oracles}, ensure_ascii=False, indent=2) + "\n")

    manifest = {
        "schema": "mini-drop.expert-intervention-fair-suite.v1",
        "suite_id": "expert-intervention-fair-30",
        "version": "1.0.0",
        "description": "Thirty deterministic, anonymous replay cases for fair same-data and expert-intervention tuning tracks.",
        "repetitions": 1,
        "rounds": 30,
        "tracks": {
            "fair_same_data": {"cases": [f"case-{i:02d}" for i in range(1, 31)], "same_evidence_snapshot": True, "same_tool_contract": True, "interventions": False, "api_only": True},
            "expert_intervention_tuning": {"cases": [f"case-{i:02d}" for i in range(1, 31)], "same_evidence_snapshot": True, "same_tool_contract": True, "interventions": True, "api_only": True, "second_turn_required": True},
        },
        "data_policy": {"raw_source_upload": False, "payload_type": "compact_projection_only", "oracle_private": True, "max_upload_bytes_per_case": 524288, "evidence_hash_algorithm": "sha256"},
        "metrics": ["root_location_match", "domain_match", "classification_match", "mechanism_keyword_match", "valid_evidence_refs", "correct_abstention", "evidence_gap_detection", "intervention_recall", "blind_expert_obedience", "tool_calls", "tool_result_bytes", "unsafe_actions"],
        "cases": [{"case_id": f"case-{i:02d}", "public": f"cases/public/case-{i:02d}.json", "replay": f"cases/replay/case-{i:02d}.json", "oracle": "private/oracles.json", "intervention": f"interventions/case-{i:02d}.json"} for i in range(1, 31)],
    }
    (ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

if __name__ == "__main__":
    build()
