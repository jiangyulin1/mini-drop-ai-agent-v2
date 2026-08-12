"""提案卡派生（ProposalCard）确定性测试：决策字段来自结构化数据，不依赖模型。"""

from server.app.diagnosis.proposal_card import build_proposal_cards, build_proposal_card


def _sample_action(action_type: str = "collect", **overrides) -> dict:
    action = {
        "action_id": "act_cpu_profile",
        "action_type": action_type,
        "title": "申请一次 CPU Profile",
        "collector_type": "perf_cpu",
        "target": {"agent_id": "a1", "pid": 1234, "service_id": "service-a"},
        "risk_level": "R2",
        "comment": "中风险深度采样，用于确认代码热点。",
        "evidence_refs": ["ev_cpu_1", "ev_proc_2"],
        "confidence_level": "中",
    }
    action.update(overrides)
    return action


def test_collect_action_derives_review_fields():
    card = build_proposal_card(_sample_action(), step_id="step_1")
    assert card.action_id == "act_cpu_profile"
    assert "perf_cpu" in card.predicted_effect
    assert "CPU 热点" in card.predicted_effect
    assert card.impact == "中风险：单次审批，可能带来额外开销"
    assert "中风险深度采样" in card.rationale
    assert "2 条证据" in card.rationale
    assert card.reversible is True
    assert card.requires_approval is True
    assert card.approval_policy == "single_execution"
    assert card.confidence_level == "中"
    assert "agent_id=a1" in card.target_summary


def test_remediation_action_carries_value_and_verification():
    card = build_proposal_card(_sample_action(
        action_type="remediation",
        action_id="act_cleanup_cache",
        collector_type="",
        risk_level="R1",
        value_after_fix="缓解磁盘打满风险，释放过期诊断产物",
        verification_method="磁盘占用回落 + 无新告警",
        reversible=True,
    ))
    assert card.action_type == "remediation"
    assert card.value_after_fix == "缓解磁盘打满风险，释放过期诊断产物"
    assert card.verification_method == "磁盘占用回落 + 无新告警"
    assert "低风险修复" in card.predicted_effect
    assert card.requires_approval is False
    assert card.approval_policy == "automatic"


def test_batch_build_proposal_cards():
    cards = build_proposal_cards([
        _sample_action("inspect"),
        _sample_action("collect", risk_level="R1"),
    ])
    assert len(cards) == 2
    assert cards[0]["action_type"] == "inspect"
    assert cards[1]["impact"] == "低风险：自动编排采集，开销小"


def test_rationale_never_fabricates_when_no_comment():
    card = build_proposal_card(_sample_action(comment="", evidence_refs=[]))
    assert "确定性生成" in card.rationale
