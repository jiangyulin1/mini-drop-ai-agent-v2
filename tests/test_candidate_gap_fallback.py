"""候选缺失兜底自主权测试：白名单内提案 + 强制审批，白名单外禁止。"""

from server.app.diagnosis.probe_registry import (
    COLLECTOR_WHITELIST,
    fallback_candidates_for_gap,
)


def test_mapped_domain_does_not_trigger_gap():
    proposals = fallback_candidates_for_gap(["host", "process"])
    assert proposals == []


def test_unmapped_domain_proposes_whitelisted_collector_with_approval():
    proposals = fallback_candidates_for_gap(["database"])
    assert len(proposals) == 1
    item = proposals[0]
    assert item["candidate_gap"] is True
    assert item["requires_approval"] is True
    assert item["approval_policy"] == "single_execution"
    assert item["collector_type"] in COLLECTOR_WHITELIST  # 永不出白名单


def test_existing_collectors_not_repeated():
    proposals = fallback_candidates_for_gap(
        ["database", "network"],
        existing_collectors=["log_scan", "sys_metrics"],
    )
    assert proposals == []


def test_unknown_domain_does_not_get_arbitrary_collector():
    assert fallback_candidates_for_gap(["gpu"]) == []


def test_whitelist_exhausted_no_more_proposals():
    proposals = fallback_candidates_for_gap(
        ["database", "network", "runtime"],
        existing_collectors=list(COLLECTOR_WHITELIST),
    )
    assert proposals == []
