"""智能归因编排入口。

串联证据采集 → 候选生成 → 置信度校准 → LLM 推理 → 校验修复 → 反馈闭环。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from server.app.ai_provider import get_ai_settings
from server.app.rca.calibrator import calibrate, format_for_llm
from server.app.rca.candidates import generate_candidates
from server.app.rca.evidence import collect_evidence
from server.app.rca.llm_client import diagnose
from server.app.rca.models import (
    DiagnosisOutcome,
    DiagnosisReport,
    EvidenceInput,
    FeedbackPrior,
    RCAFeedback,
    ValidatedReport,
)
from server.app.rca.repair import build_repair_plan, execute_safe_actions
from server.app.rca.tools import run_rca_tools, tool_results_to_evidence


def run_diagnosis(
    task_id: str,
    task_record,
    top_functions: list[dict] | None = None,
    ebpf_metrics: dict | None = None,
    sys_metrics: dict | None = None,
    suggestions: list[str] | None = None,
    failure_events: list[str] | None = None,
    baseline_diff: dict | None = None,
    agent_stats: dict | None = None,
    feedback_priors: dict[str, FeedbackPrior] | None = None,
    model_name: str | None = None,
) -> ValidatedReport:
    """执行一次完整的智能归因。

    Args:
        task_id: 任务 ID。
        task_record: 任务记录。
        top_functions: TopN 热点函数。
        ebpf_metrics: eBPF IO 延迟分布。
        suggestions: 规则引擎建议。
        failure_events: 失败原因列表。
        baseline_diff: 历史基线差异。
        agent_stats: Agent 资源开销。
        feedback_priors: 历史反馈先验。
        model_name: LLM 模型，默认从环境变量读取。

    Returns:
        ValidatedReport。
    """
    return run_diagnosis_context(
        task_id=task_id,
        task_record=task_record,
        top_functions=top_functions,
        ebpf_metrics=ebpf_metrics,
        sys_metrics=sys_metrics,
        suggestions=suggestions,
        failure_events=failure_events,
        baseline_diff=baseline_diff,
        agent_stats=agent_stats,
        feedback_priors=feedback_priors,
        model_name=model_name,
    ).report


def run_diagnosis_context(
    task_id: str,
    task_record,
    top_functions: list[dict] | None = None,
    ebpf_metrics: dict | None = None,
    sys_metrics: dict | None = None,
    suggestions: list[str] | None = None,
    failure_events: list[str] | None = None,
    baseline_diff: dict | None = None,
    agent_stats: dict | None = None,
    feedback_priors: dict[str, FeedbackPrior] | None = None,
    model_name: str | None = None,
    task_events: list[dict] | None = None,
    agent_record=None,
    repo=None,
    auto_execute_safe: bool = True,
) -> DiagnosisOutcome:
    """执行带工具证据和修复计划的完整诊断。"""
    model = model_name or get_ai_settings().model

    tool_results = run_rca_tools(
        task_record=task_record,
        top_functions=top_functions,
        ebpf_metrics=ebpf_metrics,
        baseline_diff=baseline_diff,
        task_events=task_events,
        agent_record=agent_record,
    )

    # 1. 证据采集
    evidence = collect_evidence(
        task_id=task_id,
        task_record=task_record,
        top_functions=top_functions,
        ebpf_metrics=ebpf_metrics,
        sys_metrics=sys_metrics,
        suggestions=suggestions,
        failure_events=failure_events,
        baseline_diff=baseline_diff,
        agent_stats=agent_stats,
        tool_results=tool_results_to_evidence(tool_results),
    )

    # 2. 候选归因生成
    candidates = generate_candidates(evidence, feedback_priors)

    # 3. 置信度校准
    calibrated = calibrate(candidates, evidence, feedback_priors)
    candidates_json = format_for_llm(calibrated)

    # 4. LLM 推理（含校验 + 自修复）
    result = diagnose(task_id, evidence, candidates_json, model_name=model)

    repair_plan = build_repair_plan(task_id, result.report, evidence)
    if auto_execute_safe and repo is not None:
        repair_plan = execute_safe_actions(repair_plan, repo)

    return DiagnosisOutcome(
        report=result,
        tool_results=tool_results,
        repair_plan=repair_plan,
    )
