const entry = (label, color, description) => ({ label, color, description });

export const CASE_STATUS = {
  OPEN: entry("待开始", "blue", "Case 已创建，等待启动调查"),
  INVESTIGATING: entry("正在调查", "blue", "Agent 正在验证假设并收集证据"),
  WAITING_USER: entry("等待审查", "gold", "Agent 已形成结论，等待继续追问或选择恢复方案"),
  WAITING_EVIDENCE: entry("等待证据", "gold", "采集任务已下发，等待 Worker 返回证据"),
  WAITING_APPROVAL: entry("需要人工审批", "orange", "策略要求人工确认后才能继续"),
  NEEDS_SCOPE_CONFIRMATION: entry("等待范围确认", "orange", "目标范围不完整，Agent 不会猜测执行目标"),
  INSUFFICIENT_EVIDENCE: entry("当前证据不足", "gold", "尚不满足形成可靠结论的条件"),
  PAUSED: entry("已暂停", "default", "Agent 不会继续推进"),
  STOPPED: entry("已停止", "default", "Case 已停止，保留完整审计记录"),
  RESOLVED: entry("已解决并完成验证", "green", "恢复结论已通过服务端验证"),
  ESCALATED: entry("已升级人工", "orange", "自治边界已触发，需要人工接管"),
};

export const TASK_STATUS = {
  PENDING: entry("排队中", "default"),
  RUNNING: entry("采集中", "blue"),
  UPLOADING: entry("上传中", "cyan"),
  ANALYZING: entry("分析中", "purple"),
  DONE: entry("已完成", "green"),
  FAILED: entry("失败", "red"),
  CANCELLED: entry("已取消", "default"),
};

export const PLAN_STATUS = {
  PROPOSED: entry("待选择", "blue"),
  COLLECTING: entry("采集中", "blue"),
  EVIDENCE_READY: entry("Evidence 已就绪", "green"),
  RESOLVED: entry("已解决", "green"),
  DRAFT: entry("草拟中", "default"),
  QUEUED: entry("待执行", "blue"),
  DISPATCHING: entry("正在下发", "cyan"),
  RUNNING: entry("执行中", "blue"),
  WAITING_APPROVAL: entry("等待审批", "orange"),
  COMPLETED: entry("证据已提交", "green"),
  FAILED: entry("失败", "red"),
  BLOCKED: entry("受策略阻止", "orange"),
  SUPERSEDED: entry("已被新 Revision 替代", "default"),
  CANCELLED: entry("已取消", "default"),
  CANCEL_REQUESTED: entry("正在取消", "gold"),
  REMOVED_BY_USER: entry("已由用户移除", "default"),
  SKIPPED_REUSED: entry("复用已有证据", "purple"),
};

export const EVIDENCE_TRUST = {
  TRUSTED: entry("可信", "green", "可参与后续推理和结论投影"),
  VALID: entry("有效", "green", "已通过完整性检查，可参与推理"),
  LOW_TRUST: entry("低可信", "gold", "仍保留，但在推理中降低权重"),
  EXCLUDED: entry("已排除", "default", "已从后续 Agent Prompt 与结论投影中排除"),
  UNKNOWN: entry("待审查", "default", "尚未完成人工可信度审查"),
};

export const RISK_LEVEL = {
  READ_LOW: entry("低风险只读", "blue", "只读取白名单诊断信息，不修改目标状态"),
  READ_HIGH: entry("高开销只读", "gold", "可能产生可观测性能开销，需要明确目标"),
  WRITE_LOW: entry("低风险变更", "orange", "会修改运行状态，必须执行预检和验证"),
  WRITE_HIGH: entry("高风险变更", "red", "必须人工审批、验证并具备回滚方案"),
  DESTRUCTIVE: entry("危险操作", "red", "不可逆或影响范围较大，禁止弱确认"),
};

/** Server-side R0-R3 risk codes, rendered for operators rather than raw. */
export const RISK_CODE = {
  R0: entry("无风险", "blue", "只读取已有数据，不接触目标进程"),
  R1: entry("低风险", "blue", "短时只读采集，不修改目标状态"),
  R2: entry("中风险", "orange", "有可观测开销，仅授权执行一次"),
  R3: entry("高风险", "red", "会改变运行状态，必须人工审批并可回滚"),
};

export const EVENT_TYPE = {
  case_created: entry("Case 创建", "blue"),
  user_message: entry("用户消息", "blue"),
  assistant_message: entry("Agent 理解", "purple"),
  agent_turn_started: entry("Agent Turn", "purple"),
  agent_turn_completed: entry("Agent Turn 完成", "purple"),
  plan_updated: entry("调查计划更新", "blue"),
  tool_called: entry("工具调用", "cyan"),
  task_created: entry("采集任务创建", "cyan"),
  evidence_committed: entry("Evidence 到达", "green"),
  evidence_reviewed: entry("Evidence 审查", "gold"),
  hypothesis_updated: entry("假设更新", "purple"),
  approval_requested: entry("审批请求", "orange"),
  recovery_verified: entry("恢复验证", "green"),
  recovery_failed: entry("验证失败", "red"),
};

export function mappingOf(group, value, fallback = "未知状态") {
  const key = String(value || "UNKNOWN").toUpperCase();
  return group[key] || group[String(value || "")] || entry(value || fallback, "default");
}

export function caseStatus(value) {
  return mappingOf(CASE_STATUS, value, "状态未知");
}

export function taskStatus(value) {
  return mappingOf(TASK_STATUS, value, "状态未知");
}

export function planStatus(value) {
  return mappingOf(PLAN_STATUS, value, "状态未知");
}

export function evidenceTrust(value) {
  return mappingOf(EVIDENCE_TRUST, value, "待审查");
}

export function riskLevel(value) {
  return mappingOf(RISK_LEVEL, value, "风险未声明");
}

export function riskCode(value) {
  return mappingOf(RISK_CODE, value, "风险未声明");
}

export function eventType(value) {
  return EVENT_TYPE[String(value || "").toLowerCase()] || entry(value || "系统事件", "default");
}

export function isActiveCase(value) {
  return !new Set(["STOPPED", "RESOLVED"]).has(String(value || "").toUpperCase());
}

export function isVerifiedSuccess(value) {
  return new Set(["RESOLVED", "VERIFIED", "VERIFICATION_PASSED"]).has(String(value || "").toUpperCase());
}
