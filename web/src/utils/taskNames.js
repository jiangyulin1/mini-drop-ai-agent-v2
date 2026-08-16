import { collectorMeta } from "./collectors";

const LEGACY_NAME_RULES = [
  {
    pattern: /^release baseline smoke/i,
    label: "发布基线检查",
    preferAgent: true,
  },
  {
    pattern: /^VM eBPF result-message validation/i,
    label: "虚拟机 eBPF 结果校验",
    preferAgent: true,
  },
];

const INTERNAL_TASK_SOURCES = new Set(["process_scan_api", "case_verification"]);

/**
 * v6: AI-generated data tasks are first-class user tasks.  Only explicitly
 * marked INTERNAL control-plane work is hidden from the two Task surfaces.
 */
export function isUserVisibleTask(task = {}) {
  if (task.visibility === "INTERNAL") return false;
  if (task.visibility === "USER_VISIBLE") return true;
  const options = task.request_params?.options || task.options || {};
  if (options.visibility === "INTERNAL") return false;
  // Legacy control-plane probes remain hidden; AI diagnosis steps are now visible.
  return !INTERNAL_TASK_SOURCES.has(options.source) && options.registered_probe !== true;
}

export function isUnreadableTaskName(value) {
  const name = String(value || "").trim();
  return !name
    || /[?？]{2,}/.test(name)
    || name.includes("\uFFFD")
    || /锟斤拷|烫烫烫/.test(name);
}

function targetSuffix(task, preferAgent = false) {
  if ((preferAgent || Number(task?.target_pid) === 1) && task?.agent_id) {
    return task.agent_id;
  }
  if (task?.target_pid) return `PID ${task.target_pid}`;
  if (task?.agent_id) return task.agent_id;
  return "";
}

function inferredBaseName(task) {
  const raw = String(task?.name || "");
  if (/pprof/i.test(raw) || task?.collector_type === "go_pprof") return "Go CPU 剖析";
  if (/ebpf|tracepoint/i.test(raw) || task?.collector_type === "ebpf_io") return "I/O 延迟采集";
  return `${collectorMeta(task?.collector_type).label}采集`;
}

export function taskDisplayInfo(task = {}) {
  const originalName = String(task.name || "").trim();
  const legacyRule = LEGACY_NAME_RULES.find((item) => item.pattern.test(originalName));
  if (legacyRule) {
    const suffix = targetSuffix(task, legacyRule.preferAgent);
    return {
      displayName: suffix ? `${legacyRule.label} · ${suffix}` : legacyRule.label,
      originalName,
      normalized: true,
    };
  }

  if (isUnreadableTaskName(originalName)) {
    const base = inferredBaseName(task);
    const suffix = targetSuffix(task);
    return {
      displayName: suffix ? `${base} · ${suffix}` : base,
      originalName,
      normalized: true,
    };
  }

  return {
    displayName: originalName || task.id || "未命名任务",
    originalName,
    normalized: false,
  };
}

export function taskDisplayName(task = {}) {
  return taskDisplayInfo(task).displayName;
}
