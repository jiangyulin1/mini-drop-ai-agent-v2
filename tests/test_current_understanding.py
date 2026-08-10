"""当前理解（current_understanding）确定性派生测试：程序从假设+证据推导，不依赖模型。"""

from server.app.diagnosis.current_understanding import derive_current_understanding


def _hypothesis(**overrides) -> dict:
    item = {
        "hypothesis_id": "hyp_downstream",
        "statement": "延迟由下游 db 锁等待传播",
        "root_entity": "order-db",
        "status": "ACTIVE",
        "supporting_evidence_refs": ["ev_db_1", "ev_proc_2"],
        "contradicting_evidence_refs": ["ev_baseline_3"],
        "missing_evidence": [
            {"description": "锁持有者与发布变更的时间关联", "domains": ["database"]},
            {"description": "宿主 CPU 基线", "domains": ["host"]},
        ],
    }
    item.update(overrides)
    return item


def _evidence(eid, source, domains, probe=None) -> dict:
    return {
        "evidence_id": eid,
        "source_type": source,
        "query_or_probe": probe or source,
        "observed_value": {"value": 1, "p95": 80},
        "data_quality": {"domains": domains},
    }


def test_derive_confirmed_contradictions_missing_and_next():
    hypotheses = [_hypothesis()]
    evidence = [
        _evidence("ev_db_1", "derived_artifact", ["database"], probe="mysql_lock"),
        _evidence("ev_proc_2", "derived_artifact", ["process"], probe="top_json"),
        _evidence("ev_baseline_3", "derived_artifact", ["host"], probe="sys_metrics"),
    ]
    cu = derive_current_understanding(
        target="order-svc",
        symptom="延迟升高",
        hypotheses=hypotheses,
        evidence=evidence,
        utcnow_str="2026-08-07T00:00:00Z",
    )
    assert cu.understanding == "延迟由下游 db 锁等待传播"
    assert len(cu.confirmed) == 2
    assert "ev_db_1" in cu.confirmed[0]
    assert "database" in cu.confirmed[0]
    assert cu.contradictions and "ev_baseline_3" in cu.contradictions[0]
    assert len(cu.missing) == 2
    assert cu.missing_domains == ["database", "host"]
    assert "mysql_lock" in cu.next  # 缺 database 域 → 建议 mysql_lock
    assert cu.candidate_gap_proposals[0]["evidence_domain"] == "database"
    assert cu.candidate_gap_proposals[0]["requires_approval"] is True
    assert cu.source == "programmatic"


def test_derive_rules_out_weak_hypotheses():
    hypotheses = [
        _hypothesis(status="RULED_OUT"),
        _hypothesis(hypothesis_id="hyp_other", statement="候选已排除", status="WEAKENED"),
    ]
    cu = derive_current_understanding(hypotheses=hypotheses)
    # 活跃候选为空 → OTHER_UNKNOWN
    assert "不可判断" in cu.understanding or "OTHER_UNKNOWN" in cu.understanding


def test_derive_no_missing_suggests_converge():
    hypotheses = [_hypothesis(missing_evidence=[], supporting_evidence_refs=[])]
    cu = derive_current_understanding(hypotheses=hypotheses)
    assert "收敛" in cu.next or "覆盖充分" in cu.next


def test_missing_support_reference_is_not_reported_as_confirmed():
    cu = derive_current_understanding(
        hypotheses=[_hypothesis(supporting_evidence_refs=["ev_missing"], missing_evidence=[])],
    )
    assert cu.confirmed == []
    assert cu.missing == ["支持证据引用不可用: ev_missing"]
