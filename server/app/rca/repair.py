"""RCA 自动修复计划。

自动执行只覆盖 safe_auto 动作，例如创建二次采集任务。
高风险命令只作为建议进入计划，由用户审查后手工执行。
"""

from __future__ import annotations

from uuid import uuid4

from server.app.rca.models import DiagnosisReport, EvidenceInput, RepairAction, RepairPlan
from server.app.schemas import CreateTaskRequest


SAFE_AUTO = "safe_auto"
CONFIRM_REQUIRED = "confirm_required"
MANUAL_ONLY = "manual_only"


def build_repair_plan(task_id: str, report: DiagnosisReport, evidence: EvidenceInput) -> RepairPlan:
    cause = report.ranked_causes[0] if report.ranked_causes else None
    cause_id = cause.cause_id if cause else "insufficient_data"
    actions: list[RepairAction] = []

    collector_type = evidence.task_metadata.get("collector_type", "unknown")
    agent_id = evidence.task_metadata.get("agent_id")
    target_pid = evidence.task_metadata.get("target_pid")

    if cause_id.startswith("cpu_hotspot") and collector_type != "pyspy" and agent_id and target_pid:
        actions.append(RepairAction(
            action_id=f"action_{uuid4().hex[:8]}",
            action_type="create_followup_task",
            risk_level=SAFE_AUTO,
            description="创建 py-spy 二次采集任务，验证热点是否位于 Python 用户态代码。",
            payload={
                "name": f"followup_pyspy_{task_id}",
                "agent_id": agent_id,
                "target_pid": target_pid,
                "collector_type": "pyspy",
                "sample_rate": 99,
                "duration_sec": max(int(evidence.task_metadata.get("duration_sec", 15)), 15),
                "options": {"source_diagnosis_task_id": task_id},
            },
        ))
        actions.append(RepairAction(
            action_id=f"action_{uuid4().hex[:8]}",
            action_type="code_change_suggestion",
            risk_level=MANUAL_ONLY,
            description="热点函数疑似计算密集，建议人工审查算法复杂度、缓存策略或循环边界。",
        ))

    if cause_id == "python_userland_hotspot" or (
        cause_id.startswith("cpu_hotspot") and collector_type == "pyspy"
    ):
        top_name = (
            evidence.top_functions[0].get("name", "最高占比 Python 函数")
            if evidence.top_functions
            else "最高占比 Python 函数"
        )
        actions.append(RepairAction(
            action_id=f"action_{uuid4().hex[:8]}",
            action_type="code_change_suggestion",
            risk_level=MANUAL_ONLY,
            description=(
                f"优先审查热点 {top_name} 的算法复杂度、重复序列化、循环边界和缓存策略；"
                "修改后使用相同 py-spy 参数重新采样对比。"
            ),
        ))

    if cause_id == "io_wait_high" and agent_id and target_pid:
        # 与 cpu_hotspot 分支一致的守卫：缺 agent_id/target_pid 时不自动
        # 建采集任务（避免 target_pid 落到 1 对 PID 1 发起采集）。
        actions.append(RepairAction(
            action_id=f"action_{uuid4().hex[:8]}",
            action_type="create_followup_task",
            risk_level=SAFE_AUTO,
            description="创建 eBPF IO 二次采集任务，复核块设备延迟分布。",
            payload={
                "name": f"followup_ebpf_{task_id}",
                "agent_id": agent_id,
                "target_pid": target_pid,
                "collector_type": "ebpf_io",
                "sample_rate": 99,
                "duration_sec": max(int(evidence.task_metadata.get("duration_sec", 15)), 15),
                "options": {"source_diagnosis_task_id": task_id},
            },
        ))
        actions.append(RepairAction(
            action_id=f"action_{uuid4().hex[:8]}",
            action_type="system_tuning_suggestion",
            risk_level=MANUAL_ONLY,
            description="建议人工检查磁盘队列、IO 调度器、容器限速和底层存储吞吐。",
        ))

    if cause_id == "collector_permission_denied":
        actions.append(RepairAction(
            action_id=f"action_{uuid4().hex[:8]}",
            action_type="permission_command_suggestion",
            risk_level=CONFIRM_REQUIRED,
            description="采集权限不足。需用户确认后调整 perf/eBPF 权限或以具备权限的用户运行 Agent。",
            command="sudo sysctl kernel.perf_event_paranoid=1",
        ))

    if cause_id == "artifact_storage_unreachable":
        actions.append(RepairAction(
            action_id=f"action_{uuid4().hex[:8]}",
            action_type="storage_connectivity_check",
            risk_level=MANUAL_ONLY,
            description=(
                "Agent 已完成本地采集，但证据上传失败。建议依次检查 Agent 到 MinIO/S3 "
                "endpoint 的 TCP 连通性、防火墙规则、TLS 配置、bucket 和访问凭据；"
                "恢复链路后使用原参数重新采集。"
            ),
        ))

    if not actions:
        evidence_hint = {
            "pyspy": "延长或重复 py-spy 采样，并在相同负载下补充历史基线",
            "perf_cpu": "延长 CPU Profile 采样，并在相同负载下补充历史基线",
            "ebpf_io": "重复 eBPF I/O 延迟采集，并结合磁盘队列和历史基线",
            "sys_metrics": "延长系统指标窗口，并根据异常领域选择 CPU Profile 或 eBPF I/O",
        }.get(collector_type, "补充与当前症状直接相关的采集结果和历史基线")
        actions.append(RepairAction(
            action_id=f"action_{uuid4().hex[:8]}",
            action_type="collect_more_evidence",
            risk_level=MANUAL_ONLY,
            description=f"当前证据不足，建议{evidence_hint}后重试；无需补采与症状无关的采集器。",
        ))

    risk_order = {SAFE_AUTO: 0, CONFIRM_REQUIRED: 1, MANUAL_ONLY: 2}
    plan_risk = max((item.risk_level for item in actions), key=lambda level: risk_order[level])
    return RepairPlan(
        plan_id=f"repair_{uuid4().hex[:10]}",
        task_id=task_id,
        cause_id=cause_id,
        risk_level=plan_risk,
        actions=actions,
        requires_user_confirm=any(item.risk_level != SAFE_AUTO for item in actions),
    )


def execute_safe_actions(plan: RepairPlan, repo) -> RepairPlan:
    """执行 safe_auto 动作，其余动作保持 planned。"""
    for action in plan.actions:
        if action.risk_level != SAFE_AUTO:
            continue
        if action.action_type != "create_followup_task":
            continue
        try:
            task = repo.create_task(CreateTaskRequest(**action.payload))
            action.status = "executed"
            action.result = f"已创建二次采集任务 {task.id}"
        except Exception as exc:
            action.status = "failed"
            action.result = str(exc)

    if any(action.status == "failed" for action in plan.actions if action.risk_level == SAFE_AUTO):
        plan.status = "partial_failed"
    elif any(action.status == "executed" for action in plan.actions):
        plan.status = "safe_actions_executed"
    return plan
