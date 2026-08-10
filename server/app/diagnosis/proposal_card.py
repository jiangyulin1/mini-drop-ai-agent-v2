"""提案卡：服务端派生的审批展示层（依据/推断作用/影响面/成本/可逆性/置信度）。

内核 `DiagnosisAction` / `ActionPolicyDecision` 不改。本模块把结构化动作派生为
可读的 `ProposalCard`，供审批界面展示（见 docs/ai_diagnosis_agent_design.md §2.2）。
`rationale` / `predicted_effect` 由确定性模板派生（不依赖模型）；模型富文本可在
后续通过 Communicator 角色接入（designer-scoring 层），决策字段始终来自结构化数据。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from server.app.diagnosis.schemas import StrictModel

RISK_LEVEL_TO_IMPACT = {
    "R0": "只读检查，无副作用",
    "R1": "低风险：自动编排采集，开销小",
    "R2": "中风险：单次审批，可能带来额外开销",
    "R3": "高风险：人工建议，不自动执行",
}

# 推断作用模板：按 action_type × collector_type 派生（确定性，不调用模型）
COLLECTOR_DESC = {
    "sys_metrics": "宿主/进程级系统指标（CPU/内存/线程/FD/网络/I/O 等待）",
    "perf_cpu": "CPU 热点与调用栈",
    "continuous_perf": "持续 CPU 采样窗口（可回补历史）",
    "ebpf_io": "块设备 I/O 延迟分布与宿主级争抢",
    "go_pprof": "Go CPU/堆 Profile",
    "pyspy": "Python 用户态 CPU 热点",
    "memray": "Python 内存分配热点",
    "java_async": "Java CPU 火焰图",
    "mysql_lock": "MySQL 锁等待与事务冲突",
    "memory_smaps": "进程内存明细",
}

EFFECT_TEMPLATES = {
    "inspect": "只读检查目标当前状态，确认症状是否存在并校准判断。",
    "collect": "对目标执行 {collector} 采样，将揭示：{collector_desc}。该证据进入统一证据集参与收敛判断。",
    "remediation": "对目标执行低风险修复动作，预期缓解/消除当前异常；执行后进入验证窗口。",
    "manual_remediation": "代码级/高风险修复建议，由人工执行，AI 不自动操作。",
}


class ProposalCard(StrictModel):
    step_id: str
    action_id: str
    action_type: Literal["inspect", "collect", "remediation", "manual_remediation"]
    target_summary: str
    rationale: str
    predicted_effect: str
    impact: str
    cost_breakdown: dict[str, float]
    reversible: bool = True
    requires_approval: bool = False
    approval_policy: str = "automatic"
    confidence_level: Literal["高", "中", "低", "不可判断"] = "不可判断"
    value_after_fix: str = ""
    verification_method: str = ""


def build_proposal_card(
    action: dict[str, Any],
    *,
    step_id: str = "",
    cost: Optional[dict[str, float]] = None,
) -> ProposalCard:
    """把一个结构化动作派生为可读提案卡。

    决策字段（impact/reversible/confidence/cost）全部来自动作的结构化字段，
    rationale/predicted_effect 由模板派生。绝不把模型自由文本当作决策依据。
    """
    action_type = action.get("action_type", "inspect")
    action_id = action.get("action_id", "")
    collector_type = action.get("collector_type", "")
    risk_level = action.get("risk_level", "R1")
    confidence_level = action.get("confidence_level", "不可判断")
    evidence_refs = action.get("evidence_refs") or []
    target = action.get("target") or {}
    target_summary = _target_summary(target)
    comment = action.get("comment") or ""

    collector_desc = COLLECTOR_DESC.get(collector_type, "目标采集器相关证据")
    predicted_effect = EFFECT_TEMPLATES.get(
        action_type, EFFECT_TEMPLATES["inspect"],
    ).format(collector=collector_type or "目标采集器", collector_desc=collector_desc)

    rationale = _build_rationale(comment, evidence_refs)
    impact = RISK_LEVEL_TO_IMPACT.get(risk_level, RISK_LEVEL_TO_IMPACT["R1"])
    reversible = action_type in {"inspect", "collect"} or action.get("reversible", True)
    cost_breakdown = cost or _default_cost(risk_level)

    return ProposalCard(
        step_id=step_id or action_id,
        action_id=action_id,
        action_type=action_type,
        target_summary=target_summary,
        rationale=rationale,
        predicted_effect=predicted_effect,
        impact=impact,
        cost_breakdown=cost_breakdown,
        reversible=reversible,
        requires_approval=bool(action.get("requires_approval", risk_level in {"R2", "R3"})),
        approval_policy=action.get(
            "approval_policy",
            "single_execution" if risk_level == "R2" else (
                "manual_only" if risk_level == "R3" else "automatic"
            ),
        ),
        confidence_level=confidence_level,
        value_after_fix=action.get("value_after_fix", ""),
        verification_method=action.get("verification_method", ""),
    )


def build_proposal_cards(
    actions: list[dict[str, Any]],
    *,
    step_id_prefix: str = "",
) -> list[dict[str, Any]]:
    """批量派生提案卡列表（供审批界面一次取回）。"""
    return [
        build_proposal_card(
            action,
            step_id=f"{step_id_prefix}{action.get('action_id', i)}",
        ).model_dump(mode="json")
        for i, action in enumerate(actions)
    ]


def _build_rationale(comment: str, evidence_refs: list[str]) -> str:
    parts: list[str] = []
    if comment:
        parts.append(comment)
    if evidence_refs:
        parts.append(f"关联 {len(evidence_refs)} 条证据（{', '.join(evidence_refs[:3])}）")
    if not parts:
        return "由诊断流程确定性生成，无模型自由文本。"
    return " ".join(parts)


def _target_summary(target: dict[str, Any]) -> str:
    bits: list[str] = []
    for key in ("service_id", "instance_id", "host_id", "agent_id", "pid"):
        value = target.get(key)
        if value is not None:
            bits.append(f"{key}={value}")
    return ", ".join(bits) if bits else "目标待定"


def _default_cost(risk_level: str) -> dict[str, float]:
    return {
        "latency": 1.0 if risk_level == "R0" else 15.0,
        "resource": 0.1 if risk_level in {"R0", "R1"} else 0.5,
        "monetary": 0.0,
        "risk": {"R0": 0.0, "R1": 0.1, "R2": 0.4, "R3": 0.9}.get(risk_level, 0.1),
        "approval_wait": 0.0 if risk_level in {"R0", "R1"} else 5.0,
    }
