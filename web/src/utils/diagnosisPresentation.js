const PROFILE_COLLECTORS = new Set([
  "perf_cpu",
  "pyspy",
  "continuous_perf",
  "java_async",
  "go_pprof",
]);

export const TOOL_LABELS = {
  get_flamegraph_top: "CPU 热点摘要",
  get_ebpf_latency_summary: "I/O 延迟摘要",
  compare_baseline: "历史基线对比",
  inspect_task_events: "任务执行记录",
  check_agent_health: "Agent 健康状态",
};

export const TOOL_STATUS = {
  success: { label: "已获取", color: "green", severity: "available" },
  missing: { label: "应有但缺失", color: "red", severity: "missing" },
  not_applicable: { label: "不适用于本任务", color: "default", severity: "neutral" },
  optional_missing: { label: "未配置（可选）", color: "gold", severity: "neutral" },
  derivable: { label: "原始火焰图可用", color: "blue", severity: "available" },
};

export const CAUSE_LABELS = {
  python_userland_hotspot: "Python 用户态热点",
  cpu_hotspot_recursive: "CPU 计算热点",
  io_wait_high: "I/O 延迟异常",
  insufficient_data: "证据不足",
  agent_overhead: "采集 Agent 开销",
  target_pid_invalid: "目标进程不存在",
  collector_permission_denied: "采集权限不足",
  artifact_storage_unreachable: "对象存储上传链路异常",
};

export function causeLabel(causeId) {
  return CAUSE_LABELS[causeId] || causeId || "未命名候选";
}

export function evidenceRefLabel(ref) {
  const value = String(ref || "");
  if (value.startsWith("top_functions")) return "火焰图热点";
  if (value.includes("get_flamegraph_top")) return "CPU 热点摘要";
  if (value.includes("get_ebpf_latency_summary")) return "I/O 延迟摘要";
  if (value.includes("compare_baseline") || value.startsWith("baseline_diff")) return "历史基线";
  if (value.includes("inspect_task_events") || value.startsWith("failure_events")) return "任务执行记录";
  if (value.includes("check_agent_health") || value.startsWith("agent_stats")) return "Agent 健康状态";
  if (value.startsWith("sys_metrics")) return "系统指标";
  if (value.startsWith("task_metadata")) return "任务信息";
  return value;
}

export function effectiveToolStatus(tool, collectorType, artifactTypes = []) {
  if (tool?.status && tool.status !== "missing") return tool.status;
  const available = new Set(artifactTypes);
  if (tool?.tool_name === "get_flamegraph_top") {
    if (!PROFILE_COLLECTORS.has(collectorType)) return "not_applicable";
    if (available.has("flamegraph_svg")) return "derivable";
  }
  if (tool?.tool_name === "get_ebpf_latency_summary" && collectorType !== "ebpf_io") {
    return "not_applicable";
  }
  if (tool?.tool_name === "compare_baseline") return "optional_missing";
  return tool?.status || "missing";
}

export function toolResultSummary(tool, effectiveStatus) {
  const output = tool?.output || {};
  if (effectiveStatus === "not_applicable") {
    return "该证据类型不属于当前采集器，不影响本任务成功状态。";
  }
  if (effectiveStatus === "optional_missing") {
    return "当前没有历史基线；可在需要判断“是否较平时异常”时补充。";
  }
  if (effectiveStatus === "derivable") {
    return "任务已生成可用 SVG 火焰图；本次报告生成时尚未从 SVG 提取 TopN，重新归因即可使用。";
  }
  if (tool?.tool_name === "get_flamegraph_top") {
    const items = output.top_functions || [];
    const top = items[0];
    return top
      ? `获得 ${items.length} 个热点；最高为 ${top.name}（${Number(top.percent || 0).toFixed(1)}%）。`
      : "当前 Profile 任务没有获得可解析的热点函数。";
  }
  if (tool?.tool_name === "get_ebpf_latency_summary") {
    return output.total_samples > 0
      ? `获得 ${output.total_samples} 个延迟样本，主要分布在 ${output.dominant_bucket || "未知区间"}。`
      : "当前 eBPF I/O 任务没有获得延迟样本。";
  }
  if (tool?.tool_name === "inspect_task_events") {
    const failures = output.failure_reasons || [];
    return `记录 ${output.events?.length || 0} 个状态事件${failures.length ? `，其中 ${failures.length} 个失败事件` : "，未发现失败事件"}。`;
  }
  if (tool?.tool_name === "check_agent_health") {
    return `Agent ${output.agent_id || "-"} 当前为 ${output.status || "UNKNOWN"}，报告 ${output.capabilities?.length || 0} 项能力。`;
  }
  if (tool?.tool_name === "compare_baseline") {
    return Object.keys(output).length ? "已获得历史基线对比结果。" : "没有历史基线数据。";
  }
  return tool?.error_message || "已记录结构化工具结果。";
}

export function confidencePresentation(value) {
  const score = Number(value || 0);
  if (score >= 0.7) return { label: "支持较强", color: "green" };
  if (score >= 0.4) return { label: "需要复核", color: "gold" };
  return { label: "证据较弱", color: "red" };
}

export function repairLabel(value) {
  return {
    manual_only: "仅人工操作",
    safe_auto: "低风险自动动作",
    planned: "待处理",
    executed: "已执行",
    collect_more_evidence: "补充诊断证据",
    code_change_suggestion: "代码优化建议",
    create_followup_task: "创建复核任务",
    system_tuning_suggestion: "系统调优建议",
    permission_command_suggestion: "采集权限建议",
    storage_connectivity_check: "存储链路检查",
  }[value] || value || "-";
}
