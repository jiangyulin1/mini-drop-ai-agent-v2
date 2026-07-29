export const COLLECTOR_META = {
  perf_cpu: {
    label: "CPU 火焰图",
    resultLabel: "交互式 CPU 火焰图 + TopN 热点",
    description: "使用 perf 采样目标进程调用栈，适合定位 CPU 热点、锁竞争和异常调用路径。",
    color: "blue",
    defaultDuration: 15,
    defaultSampleRate: 99,
    flamegraph: true,
  },
  pyspy: {
    label: "Python 火焰图",
    resultLabel: "Python 调用栈火焰图",
    description: "使用 py-spy 采样 Python 进程，无需修改应用代码。",
    color: "purple",
    defaultDuration: 15,
    defaultSampleRate: 99,
    flamegraph: true,
  },
  continuous_perf: {
    label: "持续火焰图",
    resultLabel: "按窗口切分的火焰图 + 趋势",
    description: "周期采集多个时间窗口，适合观察热点随时间变化。",
    color: "cyan",
    defaultDuration: 60,
    defaultSampleRate: 49,
    flamegraph: true,
  },
  java_async: {
    label: "Java 火焰图",
    resultLabel: "async-profiler Java 火焰图",
    description: "采集 JVM 进程的 CPU 调用栈并生成可浏览的 HTML 火焰图。",
    color: "magenta",
    defaultDuration: 15,
    defaultSampleRate: 99,
    flamegraph: true,
  },
  go_pprof: {
    label: "Go pprof",
    resultLabel: "Go pprof 数据（环境支持时生成火焰图）",
    description: "抓取 Go pprof CPU Profile，可下载原始数据并在工具链可用时生成火焰图。",
    color: "geekblue",
    defaultDuration: 15,
    defaultSampleRate: 99,
    flamegraph: true,
  },
  ebpf_io: {
    label: "I/O 延迟图",
    resultLabel: "eBPF I/O 延迟直方图",
    description: "使用 eBPF/bpftrace 观察块设备延迟分布；该采集不会生成 CPU 火焰图。",
    color: "green",
    defaultDuration: 15,
    defaultSampleRate: 11,
    flamegraph: false,
  },
  memory_smaps: {
    label: "内存趋势",
    resultLabel: "RSS / PSS / Swap 趋势图",
    description: "采样进程 smaps，适合定位内存增长、Swap 和疑似泄漏。",
    color: "orange",
    defaultDuration: 15,
    defaultSampleRate: 11,
    flamegraph: false,
  },
  sys_metrics: {
    label: "系统指标",
    resultLabel: "CPU / 负载 / 线程 / FD / 网络多维图",
    description: "低开销采集主机和进程指标；该采集不会生成调用栈火焰图。",
    color: "gold",
    defaultDuration: 15,
    defaultSampleRate: 11,
    flamegraph: false,
  },
};

export const COLLECTOR_OPTIONS = Object.entries(COLLECTOR_META).map(([value, meta]) => ({
  value,
  label: `${meta.label} · ${meta.resultLabel}`,
}));

export function collectorMeta(collectorType) {
  return COLLECTOR_META[collectorType] || {
    label: collectorType || "未知采集器",
    resultLabel: "任务产物",
    description: "任务完成后可在结果页查看采集产物。",
    color: "default",
    defaultDuration: 15,
    defaultSampleRate: 99,
    flamegraph: false,
  };
}

/** Convert server TaskKind metadata to the stable view model used by pages. */
export function collectorMetaFromTaskKind(kind) {
  if (!kind?.key) return null;
  const fallback = collectorMeta(kind.key);
  return {
    ...fallback,
    label: kind.display_name || fallback.label,
    resultLabel: kind.result_label || fallback.resultLabel,
    description: kind.description || fallback.description,
    color: kind.presentation?.color || fallback.color,
    flamegraph: kind.presentation?.flamegraph ?? fallback.flamegraph,
    defaultDuration:
      kind.defaults?.duration_sec ?? fallback.defaultDuration,
    defaultSampleRate:
      kind.defaults?.sample_rate ?? fallback.defaultSampleRate,
    durationMin:
      kind.parameter_schema?.duration_sec?.minimum ?? 1,
    durationMax:
      kind.parameter_schema?.duration_sec?.maximum ?? 120,
    sampleRateMin:
      kind.parameter_schema?.sample_rate?.minimum ?? 1,
    sampleRateMax:
      kind.parameter_schema?.sample_rate?.maximum ?? 999,
    permissionRequirements: kind.permission_requirements || [],
  };
}
