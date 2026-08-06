export const TERMINAL_DIAGNOSIS = new Set([
  "COMPLETED",
  "INSUFFICIENT_EVIDENCE",
  "PARTIAL_COMPLETED",
  "BUDGET_EXHAUSTED",
  "TOPOLOGY_UNAVAILABLE",
  "USER_CANCELED",
  "FAILED",
]);

export const TERMINAL_CASE = new Set(["RESOLVED", "STOPPED"]);

export const CASE_STATE_META = {
  NEEDS_SCOPE_CONFIRMATION: { label: "需要范围", color: "orange", tone: "waiting" },
  OPEN: { label: "待开始", color: "blue", tone: "busy" },
  INVESTIGATING: { label: "诊断中", color: "processing", tone: "busy" },
  WAITING_USER: { label: "等待确认", color: "orange", tone: "waiting" },
  RECOVERY_PLANNING: { label: "待处理", color: "purple", tone: "busy" },
  VERIFYING: { label: "验证中", color: "cyan", tone: "busy" },
  PAUSED: { label: "已暂停", color: "default", tone: "idle" },
  RESOLVED: { label: "已解决", color: "green", tone: "online" },
  INSUFFICIENT_EVIDENCE: { label: "证据不足", color: "gold", tone: "waiting" },
  STOPPED: { label: "已停止", color: "red", tone: "error" },
};

export const DIAGNOSIS_STATUS_META = {
  CREATED: { label: "已创建", color: "default" },
  UNDERSTANDING: { label: "理解问题", color: "blue" },
  NEEDS_SCOPE_CONFIRMATION: { label: "需要范围", color: "orange" },
  PLANNING: { label: "准备诊断", color: "blue" },
  ANALYZING_EXISTING_DATA: { label: "检查已有数据", color: "cyan" },
  WAITING_APPROVAL: { label: "等待确认", color: "orange" },
  COLLECTING: { label: "采集中", color: "processing" },
  ANALYZING: { label: "分析中", color: "cyan" },
  NEED_MORE_EVIDENCE: { label: "需要更多数据", color: "gold" },
  CONCLUDING: { label: "整理结论", color: "cyan" },
  COMPLETED: { label: "已完成", color: "green" },
  PARTIAL_COMPLETED: { label: "部分完成", color: "gold" },
  INSUFFICIENT_EVIDENCE: { label: "证据不足", color: "gold" },
  BUDGET_EXHAUSTED: { label: "预算已用完", color: "red" },
  TOPOLOGY_UNAVAILABLE: { label: "拓扑不可用", color: "orange" },
  USER_CANCELED: { label: "已取消", color: "default" },
  FAILED: { label: "失败", color: "red" },
};

export const PROBE_LABELS = {
  host_process_metrics: "系统与进程指标",
  process_cpu_profile: "CPU 火焰图",
  process_io_latency: "I/O 延迟",
  process_memory_map: "进程内存",
};

export const RELATION_OPTIONS = [
  { value: "CALLS", label: "调用" },
  { value: "READS_FROM", label: "读取" },
  { value: "WRITES_TO", label: "写入" },
  { value: "PUBLISHES_TO", label: "发布到" },
  { value: "CONSUMES_FROM", label: "消费自" },
  { value: "SHARES_DEPENDENCY", label: "共享依赖" },
];

export function formatTime(value, withDate = false) {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return withDate
    ? parsed.toLocaleString([], { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
    : parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export function itemId(item) {
  return item?.id || item?.task_id || "";
}

export function taskOptions(task) {
  return task?.request_params?.options || task?.options || {};
}

export function newCollectionId() {
  const suffix = globalThis.crypto?.randomUUID?.().replaceAll("-", "").slice(0, 8)
    || Math.random().toString(16).slice(2, 10);
  const stamp = new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14);
  return `collect_${stamp}_${suffix}`;
}

export function groupTasks(tasks) {
  const grouped = new Map();
  for (const task of tasks || []) {
    const options = taskOptions(task);
    const key = options.collection_session_id || `task_${itemId(task)}`;
    if (!grouped.has(key)) {
      grouped.set(key, {
        collectionId: key,
        caseId: options.case_id || "",
        serviceId: options.service_id || "",
        createdAt: task.created_at,
        tasks: [],
      });
    }
    grouped.get(key).tasks.push(task);
  }
  return [...grouped.values()].sort((a, b) => new Date(b.createdAt || 0) - new Date(a.createdAt || 0));
}

export function caseHasInstances(detail) {
  return Boolean(detail?.target_scope?.instances?.length);
}

export function buildInstancesFromTasks(tasks, agents, detail) {
  const serviceId = detail?.target_scope?.service_id || detail?.target_scope?.service_ids?.[0] || "unknown-service";
  const agentMap = new Map((agents || []).map((agent) => [agent.id, agent]));
  return (tasks || []).map((task, index) => {
    const agent = agentMap.get(task.agent_id);
    const pid = Number(task.target_pid);
    return {
      service_id: serviceId,
      instance_id: `${serviceId}-${task.agent_id}-${pid || index + 1}`.slice(0, 128),
      host_id: agent?.hostname || task.agent_id,
      agent_id: task.agent_id,
      pid,
      environment: detail?.environment || "production",
    };
  }).filter((item) => item.agent_id && item.pid > 0);
}

export function uniqueInstances(instances) {
  const result = new Map();
  for (const item of instances || []) {
    if (!item?.agent_id || !item?.pid) continue;
    result.set(`${item.agent_id}:${item.pid}`, item);
  }
  return [...result.values()];
}

export function shortTitle(problem) {
  const value = String(problem || "").trim().replace(/\s+/g, " ");
  return value.length > 30 ? `${value.slice(0, 30)}…` : value;
}

export function eventText(event) {
  const payload = event?.payload || {};
  switch (event?.event_type) {
    case "case_corrected": return "范围或目标已更新，旧结论不再使用。";
    case "diagnosis_started": return "已开始一轮诊断。";
    case "case_paused": return "诊断已暂停。";
    case "case_resumed": return "诊断已继续。";
    case "case_stopped": return "会话已停止。";
    default: return payload.reason || "";
  }
}

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
