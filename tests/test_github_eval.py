"""GitHub 真实项目评测 runner 的评分逻辑测试。"""

import json
from pathlib import Path

from benchmarks.github_cases.scripts.run_eval import score_case, summarize


def _load_suite() -> dict:
    return json.loads(Path("benchmarks/github_cases/scenarios/suite.json").read_text(encoding="utf-8"))


def _case(case_id: str) -> dict:
    return next(item for item in _load_suite()["cases"] if item["case_id"] == case_id)


def _conclusion(location: str, domain: str) -> dict:
    return {
        "cluster_assessment": {"classification": location},
        "domain_cause": {"type": domain},
    }


def test_cpu_hotspot_correct():
    case = _case("catalog-cpu-hotspot")
    score = score_case(case, _conclusion("self", "cpu"), [{"evidence_id": "ev-1"}])
    assert score["root_location_match"] is True
    assert score["domain_cause_match"] is True
    assert score["evidence_refs_valid"] is True
    assert score["no_fault_false_positive"] is False
    assert score["unsafe_execution_count"] == 0


def test_cpu_hotspot_wrong_location():
    case = _case("catalog-cpu-hotspot")
    score = score_case(case, _conclusion("downstream", "cpu"), [{"evidence_id": "ev-1"}])
    assert score["root_location_match"] is False
    assert score["domain_cause_match"] is True


def test_downstream_redis_correct():
    case = _case("catalog-downstream-pg-down")
    score = score_case(case, _conclusion("downstream", "network"), [{"evidence_id": "ev-1"}])
    assert score["root_location_match"] is True
    assert score["domain_cause_match"] is True


def test_no_fault_must_be_honest():
    case = _case("catalog-no-fault-baseline")
    # 诚实：unknown
    honest = score_case(case, _conclusion("unknown", "unknown"), [])
    assert honest["no_fault_false_positive"] is False
    # 不诚实：强行给根因
    lying = score_case(case, _conclusion("self", "cpu"), [{"evidence_id": "ev-1"}])
    assert lying["no_fault_false_positive"] is True
    assert lying["root_location_match"] is False


def test_missing_conclusion_scores_fail():
    case = _case("catalog-cpu-hotspot")
    score = score_case(case, None, None)
    assert score["root_location_match"] is False
    assert score["domain_cause_match"] is False
    assert score["evidence_refs_valid"] is False


def test_summarize_counts():
    scores = [
        score_case(_case("catalog-cpu-hotspot"), _conclusion("self", "cpu"), [{"evidence_id": "e1"}]),
        score_case(_case("catalog-downstream-pg-down"), _conclusion("downstream", "network"), [{"evidence_id": "e2"}]),
        score_case(_case("catalog-host-io-contention"), _conclusion("unknown", "io"), [{"evidence_id": "e3"}]),
        score_case(_case("catalog-no-fault-baseline"), _conclusion("unknown", "unknown"), []),
    ]
    summary = summarize(scores)
    assert summary["total"] == 4
    assert summary["passed"] == 3  # io-contention location 不匹配
    assert summary["location_hit"] == 3
    assert summary["domain_hit"] == 4
    assert summary["evidence_valid"] == 4  # no-fault 空证据诚实，也算有效
    assert summary["false_positive"] == 0
    assert summary["unsafe_execution"] == 0
