"""智能归因数据模型。

定义证据、候选原因、置信度校准和诊断报告的全部结构。
LLM 输出的 JSON 必须符合 DiagnosisReport 的 schema，
工程校验层通过 Pydantic 解析进行格式和引用完整性检查。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel, Field


# ── 输入侧 ──


class EvidenceInput(BaseModel):
    """归因输入的全部结构化证据（传给 LLM 前构造）。"""

    task_metadata: dict = Field(default_factory=dict)
    top_functions: list[dict] = Field(default_factory=list)
    ebpf_metrics: Optional[dict] = None
    sys_metrics: Optional[dict] = None
    baseline_diff: Optional[dict] = None
    agent_stats: Optional[dict] = None
    tool_results: list[dict] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    failure_events: list[str] = Field(default_factory=list)


class CandidateCause(BaseModel):
    """规则引擎生成的候选归因。"""

    candidate_id: str
    description: str
    evidence_refs: list[str] = Field(default_factory=list)
    rule_score: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_evidence: list[str] = Field(default_factory=list)


@dataclass
class CalibratedCause:
    """经过置信度校准器加权后的候选原因。"""

    candidate_id: str
    description: str
    evidence_refs: list[str]
    final_confidence: float
    rule_score: float
    evidence_quality: float
    baseline_support: float
    cross_collector_agreement: float
    feedback_prior: float
    missing_evidence: list[str] = field(default_factory=list)


class FeedbackPrior(BaseModel):
    """从 rca_feedback_weights 表查询到的历史反馈先验。"""

    candidate_id: str
    positive_count: int = 0
    negative_count: int = 0
    weight_delta: float = 0.0


# ── 输出侧 ──


class CauseEntry(BaseModel):
    """LLM 输出的单条归因结论。"""

    cause_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    claim: str
    evidence_refs: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    verification_steps: list[str] = Field(default_factory=list)


class DiagnosisReport(BaseModel):
    """LLM 输出的完整归因报告，schema 注入到 system prompt 中。

    工程校验层通过此 Pydantic 模型解析 LLM JSON 输出：
      - 字段类型不匹配 → 校验失败 → 触发自修复重试
      - evidence_refs 不存在于输入证据中 → 校验失败 → 触发修复
    """

    summary: str
    ranked_causes: list[CauseEntry]
    facts: list[str]
    not_enough_evidence: bool = False


class ValidatedReport(BaseModel):
    """校验通过并保存到数据库的报告。"""

    task_id: str
    model_name: str
    evidence_snapshot: dict
    report: DiagnosisReport
    validated: bool = True
    validation_issues: list[str] = Field(default_factory=list)
    retry_count: int = 0


class ToolResult(BaseModel):
    """一次 RCA 工具调用结果，作为可引用证据链的一部分。"""

    tool_name: str
    status: str
    evidence_ref: str
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None


class RepairAction(BaseModel):
    """单个修复动作。safe_auto 可自动执行，其余只生成建议。"""

    action_id: str
    action_type: str
    risk_level: str
    description: str
    command: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str = "planned"
    result: Optional[str] = None


class RepairPlan(BaseModel):
    """诊断后的修复计划。"""

    plan_id: str
    task_id: str
    cause_id: str
    risk_level: str
    actions: list[RepairAction] = Field(default_factory=list)
    requires_user_confirm: bool = True
    status: str = "planned"


class DiagnosisOutcome(BaseModel):
    """一次完整诊断的工程化输出。"""

    report: ValidatedReport
    tool_results: list[ToolResult] = Field(default_factory=list)
    repair_plan: Optional[RepairPlan] = None


class RCAFeedback(BaseModel):
    """用户对归因报告的反馈。"""

    task_id: str
    report_id: str
    predicted_cause_id: str
    predicted_confidence: float
    feedback_label: str  # correct / wrong / partial / unknown
    corrected_cause_id: Optional[str] = None
    feedback_note: Optional[str] = None
