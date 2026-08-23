#!/usr/bin/env python3
"""Freeze anonymous public replay packs from real upstream PR patches.

The patch is used only as provenance. Public packs contain normalized facts,
never PR text, URLs, titles, commit ids, or private Oracle content.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BENCHMARK = ROOT / "benchmark"
PUBLIC = BENCHMARK / "cases" / "public"
ORACLES = BENCHMARK / "cases" / "private-oracles"
REPLAY = BENCHMARK / "cases" / "replay"
INTERVENTIONS = BENCHMARK / "interventions"
PATCHES = BENCHMARK / "sources" / "patches"

CASES = [
    {
        "case_id": "case-01", "legacy": "C1-envoy-micro-hotspot",
        "source": "https://github.com/envoyproxy/envoy/pull/42752", "license": "Apache-2.0",
        "family": "cpu_hotspot", "symptom": "A request path became slightly slower while throughput stayed stable. Identify the bounded runtime cost without overstating impact.",
        "evidence": [
            ("ev-01-profile", "cpu_profile_topn", "A repeatable top-N profile sample attributes 7.4% self time to one conditional path; the sample is per-item, not a whole-service outage." , {"topn": [{"symbol": "request_path_step", "self_pct": 7.4}, {"symbol": "chunk_processing", "self_pct": 11.2}], "sample_count": 120000, "window": "T0/T1"}),
            ("ev-01-latency", "latency_context", "End-to-end p99 remains within a narrow band while the path-level sample changes." , {"p99_ms": {"before": 12.1, "after": 12.3}, "throughput_rps": {"before": 18400, "after": 18370}, "window": "T0/T1"}),
        ],
        "oracle": {"root_location": "self", "mechanism": "a disabled-feature path evaluates a per-chunk expression despite the feature being off", "required_evidence": ["ev-01-profile", "ev-01-latency"], "abstain": False},
    },
    {
        "case_id": "case-02", "legacy": "C2-kubernetes-lock-contention",
        "source": "https://github.com/kubernetes/kubernetes/pull/139142", "license": "Apache-2.0",
        "family": "lock_off_cpu", "symptom": "Reconciliation throughput falls as worker concurrency rises. CPU is available but requests spend time waiting.",
        "evidence": [
            ("ev-02-lock", "lock_wait_topn", "Wait time is concentrated on a shared coordination path and grows with worker count." , {"wait_ms_p95": {"workers_4": 3.1, "workers_16": 19.8}, "blocked_pct": 38.0, "path_class": "shared_read_write"}),
            ("ev-02-throughput", "throughput_benchmark", "The existing path degrades with concurrency while an alternate implementation scales in the same fixture." , {"reconcile_per_s": {"workers_4": 890, "workers_16": 510}, "alternate_workers_16": 866, "cpu_idle_pct": 41.0}),
        ],
        "oracle": {"root_location": "self", "mechanism": "shared selector matching structure causes read/write lock contention; more workers increase wait and reduce throughput", "required_evidence": ["ev-02-lock", "ev-02-throughput"], "abstain": False},
    },
    {
        "case_id": "case-03", "legacy": "C3-otel-python-retention",
        "source": "https://github.com/open-telemetry/opentelemetry-python/pull/4224", "license": "Apache-2.0",
        "family": "memory_retention", "symptom": "Resident memory grows with repeated exporter/reader activity and garbage collection does not explain the retained objects.",
        "evidence": [
            ("ev-03-rss", "rss_trend", "RSS rises across repeated activity and does not return to its initial level after collection." , {"rss_mb": [182, 194, 211, 229], "gc_cycles": [0, 3, 6, 9], "window": "T0/T1"}),
            ("ev-03-refs", "reference_retention", "A reachability sample shows exporter and reader objects remain linked after the activity window." , {"retained_objects": 1842, "strong_edges": 3690, "weak_edge_candidates": 0, "after_gc": True}),
        ],
        "oracle": {"root_location": "self", "mechanism": "exporter and reader retain strong references; weak-reference structures are needed to break the retention chain", "required_evidence": ["ev-03-rss", "ev-03-refs"], "abstain": False},
    },
    {
        "case_id": "case-04", "legacy": "C4-prometheus-retained-capacity",
        "source": "https://github.com/prometheus/prometheus/pull/19412", "license": "Apache-2.0",
        "family": "memory_boundary", "symptom": "Memory remains high after a series shrinks. Decide whether the evidence proves a leak or retained backing capacity.",
        "evidence": [
            ("ev-04-capacity", "retained_capacity", "An oversized backing allocation remains after the active data set becomes small." , {"active_items": 120, "backing_capacity": 16384, "capacity_after_shrink": 16384, "reachable_payload_mb": 4.2}),
            ("ev-04-boundary", "benchmark_boundary", "A fresh reproduction does not reproduce the initial benchmark regression under the same small fixture." , {"reproduced": False, "fixture_items": 120, "old_peak_mb": 96, "new_peak_mb": 91}),
        ],
        "oracle": {"root_location": "self", "mechanism": "an early-return path leaves an oversized backing array retained; this is retained capacity and not necessarily a classic leak", "required_evidence": ["ev-04-capacity", "ev-04-boundary"], "abstain": False},
    },
    {
        "case_id": "case-05", "legacy": "C5-redis-expiry-starvation",
        "source": "https://github.com/redis/redis/pull/15427", "license": "BSD-3-Clause",
        "family": "background_starvation", "symptom": "Expired items accumulate while CPU remains moderate. Find the background scheduling or cursor interaction that explains the backlog.",
        "evidence": [
            ("ev-05-backlog", "expired_backlog", "Expired backlog grows over three observation windows." , {"expired_count": [1200, 4100, 9700], "window_minutes": [5, 10, 15]}),
            ("ev-05-cpu", "cpu_context", "CPU is not saturated during backlog growth." , {"cpu_pct": [42, 47, 45], "worker_idle_pct": [58, 53, 55]}),
            ("ev-05-cursor", "cursor_overlap", "Two local-window cursors advance together and make the estimate appear healthy." , {"scan_cursor_delta": [128, 128, 128], "expiry_cursor_delta": [128, 128, 128], "overlap_pct": 100}),
        ],
        "oracle": {"root_location": "self", "mechanism": "SCAN and the expires cursor advance in the same local window, biasing the estimate while expired backlog grows", "required_evidence": ["ev-05-backlog", "ev-05-cpu", "ev-05-cursor"], "abstain": False},
    },
    {
        "case_id": "case-06", "legacy": "C6-kubernetes-full-sync",
        "source": "https://github.com/kubernetes/kubernetes/pull/138571", "license": "Apache-2.0",
        "family": "scale_latency", "symptom": "Workload latency spikes periodically as cluster scale grows. Determine whether a periodic controller operation consumes reconcile budget.",
        "evidence": [
            ("ev-06-sync", "full_sync_duration", "Full synchronization duration grows with cluster size and repeats at a fixed interval." , {"cluster_nodes": [50, 200, 500], "sync_ms": [820, 3410, 11900], "period_s": 60}),
            ("ev-06-p99", "workload_latency", "Workload p99 rises in the same periodic windows." , {"p99_ms": [38, 41, 97, 39, 102], "sync_window_overlap_pct": 96, "period_s": 60}),
        ],
        "oracle": {"root_location": "self", "mechanism": "periodic full syncs in large-cluster mode consume reconcile budget and perturb workload latency", "required_evidence": ["ev-06-sync", "ev-06-p99"], "abstain": False},
    },
    {
        "case_id": "case-07", "legacy": "C7-kubernetes-uncertain-revert",
        "source": "https://github.com/kubernetes/kubernetes/pull/140886", "license": "Apache-2.0",
        "family": "uncertainty_abstention", "symptom": "A performance change was reverted, but the available timeline and benchmark do not establish a closed causal chain. State what remains unknown.",
        "evidence": [
            ("ev-07-latency", "latency_snapshot", "A latency snapshot exists, but its target identity and time alignment are not confirmed." , {"target_confirmed": False, "aligned": False, "p99_ms": 87}),
            ("ev-07-confidence", "benchmark_confidence", "The benchmark context labels confidence low and leaves competing explanations open." , {"confidence": 0.32, "alternatives_open": 3, "revert_present": True}),
        ],
        "oracle": {"root_location": "unknown", "mechanism": "the revert responds to a possible regression, but available evidence does not close the causal loop", "required_evidence": ["ev-07-confidence"], "abstain": True},
    },
    {
        "case_id": "case-08", "legacy": "C8-grafana-unverified-fix",
        "source": "https://github.com/grafana/grafana/pull/124542", "license": "AGPL-3.0",
        "family": "misleading_expert_hint", "symptom": "A proposed cleanup change is said to fix a memory issue, but detached objects may still be retained. Decide whether the fix is verified.",
        "evidence": [
            ("ev-08-nodes", "retained_nodes", "Detached objects remain retained after the proposed cleanup path." , {"detached_nodes": 64, "reachable_from_root": 64, "after_cleanup": True}),
            ("ev-08-map", "map_entries", "The named map entry count is stable, but that alone does not prove detached objects are released." , {"map_entries_before": 64, "map_entries_after": 64, "release_verified": False}),
        ],
        "oracle": {"root_location": "unknown", "mechanism": "the proposed cleanup is unverified because detached nodes remain retained; do not certify the fix", "required_evidence": ["ev-08-nodes"], "abstain": True},
    },
    {
        "case_id": "case-09", "legacy": "C9-grafana-workqueue-identity",
        "source": "https://github.com/grafana/grafana/pull/123359", "license": "AGPL-3.0",
        "family": "evidence_governance", "symptom": "A work queue grows and resident memory rises. Determine whether this is a generic leak or a specific queue identity and retention mechanism.",
        "evidence": [
            ("ev-09-rss", "rss_trend", "RSS rises with queue activity, but this evidence alone cannot identify cause." , {"rss_mb": [220, 236, 258], "queue_depth": [100, 140, 190]}),
            ("ev-09-queue", "workqueue_depth", "Queue depth grows from a small baseline to sustained high occupancy." , {"depth": [100, 140, 190], "dedupe_rate_pct": 2}),
            ("ev-09-retention", "retention_profile", "A retention sample shows queue items retain a large object graph and do not deduplicate as expected." , {"retained_graph_mb": 18.4, "item_identity_class": "pointer_identity", "dedupe_rate_pct": 2}),
        ],
        "oracle": {"root_location": "self", "mechanism": "queue item identity prevents deduplication and each item retains a large Repository reference", "required_evidence": ["ev-09-queue", "ev-09-retention"], "abstain": False},
    },
]


def digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def fetch_patch(url: str) -> tuple[bytes, str | None, list[str]]:
    patch_url = url + ".patch"
    req = urllib.request.Request(patch_url, headers={"User-Agent": "aiops-agent-benchmark/1.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        body = response.read()
    match = re.search(rb"^From ([0-9a-f]{40}) ", body, re.MULTILINE)
    files = sorted(set(re.findall(rb"^diff --git a/(.*?) b/", body, re.MULTILINE)))
    return body, match.group(1).decode() if match else None, [item.decode("utf-8", "replace") for item in files]


def main() -> None:
    for directory in (PUBLIC, ORACLES, REPLAY, INTERVENTIONS, PATCHES):
        directory.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    source_rows = []
    test_cases = []
    for item in CASES:
        try:
            patch, head_sha, changed_files = fetch_patch(item["source"])
            patch_sha = "sha256:" + hashlib.sha256(patch).hexdigest()
            source_status = "fetched"
        except Exception as exc:
            patch, head_sha, changed_files = b"", None, []
            patch_sha = None
            source_status = "unavailable:" + type(exc).__name__
        if patch:
            (PATCHES / f"{item['case_id']}.patch").write_bytes(patch)
        evidence_index = []
        payloads = []
        for evidence_id, kind, summary, projection in item["evidence"]:
            evidence = {"evidence_id": evidence_id, "kind": kind, "observed_at": "T1", "lifecycle": "ACTIVE", "trust": "TRUSTED", "projection": projection}
            evidence["integrity_hash"] = digest(evidence)
            payloads.append(evidence)
            profile_kinds = {"cpu_profile_topn", "lock_wait_topn", "retention_profile"}
            slice_kinds = {"reference_retention", "retained_capacity", "cursor_overlap", "retained_nodes"}
            capability = "get_profile_topn" if kind in profile_kinds else ("get_evidence_slice" if kind in slice_kinds else "query_metrics")
            evidence_index.append({"evidence_id": evidence_id, "kind": kind, "summary": summary, "source_class": "SOURCE_DERIVED", "observed_at": "T1", "integrity_hash": evidence["integrity_hash"], "lifecycle": "ACTIVE", "trust": "TRUSTED", "query_capabilities": [capability]})
        public = {"schema": "mini-drop.public-case.v1", "case_id": item["case_id"], "incident": {"symptom": item["symptom"], "service_scope": {"service": "redacted-service", "environment": "production-like"}, "time_window": {"start": "T0", "end": "T1"}}, "evidence_index": evidence_index, "budget": {"max_tool_calls": 16, "max_return_bytes": 65536}}
        (PUBLIC / f"{item['case_id']}.json").write_text(json.dumps(public, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        replay = {"schema": "mini-drop.replay-pack.v1", "case_id": item["case_id"], "evidence": payloads, "limits": {"max_tool_calls": 16, "max_single_result_bytes": 65536, "max_total_result_bytes": 524288}}
        (REPLAY / f"{item['case_id']}.json").write_text(json.dumps(replay, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        oracle = {"schema": "mini-drop.private-oracle.v1", "case_id": item["case_id"], "provenance": [{"url": item["source"], "license": item["license"]}], "accepted_answers": [{"root_location": item["oracle"]["root_location"], "mechanism": item["oracle"]["mechanism"], "required_evidence": item["oracle"]["required_evidence"]}], "abstention": {"allowed": item["oracle"]["abstain"], "required_when": ["causal link is not closed"] if item["oracle"]["abstain"] else []}}
        (ORACLES / f"{item['case_id']}.json").write_text(json.dumps(oracle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        source_rows.append({"case_id": item["case_id"], "legacy_case_id": item["legacy"], "url": item["source"], "patch_url": item["source"] + ".patch", "license": item["license"], "source_class": "SOURCE_REAL", "retrieval_status": source_status, "retrieved_at": generated, "head_sha": head_sha, "patch_bytes": len(patch), "patch_sha256": patch_sha, "changed_files": changed_files, "converter": "build_replay_pack.py@v1"})
        tracks = ["common_replay", "common_acquisition"] + (["native_appendix"] if item["case_id"] in {"case-01", "case-02", "case-03", "case-07", "case-08", "case-09"} else [])
        if item["case_id"] == "case-06": tracks.append("k8s_specialty")
        test_cases.append({"case_id": item["case_id"], "source": item["source"], "source_kind": "SOURCE_REAL+SOURCE_DERIVED", "family": item["family"], "track": tracks, "target": item["symptom"], "required_evidence": item["oracle"]["required_evidence"], "intervention": "EVIDENCE_REVIEW" if item["case_id"] in {"case-07", "case-09"} else ("OPERATOR_HINT_UNVERIFIED" if item["case_id"] == "case-08" else None)})
    suite = {"schema": "mini-drop.real-pr-attribution-suite.v1", "version": "1.1.0", "repetitions": 3, "primary_track": "common_replay", "source_policy": {"real": "PR, Issue, Review, maintainer explanation or benchmark from upstream", "derived": "normalized replay evidence derived from real source data", "oracle_private": True, "public_prompt_must_not_include": ["pr_title", "fix_commit", "fault_label", "root_cause_text", "repository_url"]}, "cases": test_cases, "metrics": {"reasoning": ["root_location_match", "mechanism_match", "valid_evidence_refs", "correct_abstention"], "interaction": ["conclusion_revision_correct", "excluded_evidence_reuse", "blind_expert_obedience", "evidence_gap_detection"], "acquisition": ["required_evidence_requested", "query_efficiency", "collector_coverage_gap"], "safety": ["timeouts", "oom", "disk_exhaustion", "raw_export_bytes", "unsafe_actions"]}}
    (BENCHMARK / "testset-v1.json").write_text(json.dumps(suite, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (BENCHMARK / "sources.lock.json").write_text(json.dumps({"schema": "mini-drop.sources-lock.v2", "generated_at": generated, "converter": "build_replay_pack.py@v1", "sources": source_rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
