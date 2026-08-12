const CLASSIFICATIONS = {
  self_code_or_process_pressure: {
    title: "问题主要在当前服务进程内",
    meaning: "当前进程的 CPU、内存或代码执行行为出现明显异常，问题不像是由其他 Worker 统一造成。",
    impact: "该实例可能变慢、超时或被系统终止。处理前应先确认具体是 CPU 热点、内存增长还是其他进程压力。",
  },
  host_resource_contention: {
    title: "所在机器发生资源争用",
    meaning: "同一台 Worker 上的多个进程正在争用 CPU、磁盘或内存，最先告警的服务不一定是真正的来源。",
    impact: "同机服务可能一起变慢。只重启当前服务通常不能解决问题，应查看同机其他实例和宿主机负载。",
  },
  downstream_dependency: {
    title: "下游服务或依赖不可用",
    meaning: "当前服务本身不一定异常，但它调用的数据库、缓存、支付或其他服务出现超时、拒绝连接或资源压力。",
    impact: "应沿服务关系检查下游目标。盲目扩大到所有服务会增加采集噪声，因此系统只检查已登记的依赖。",
  },
  process_oom: {
    title: "进程发生内存耗尽（OOM）",
    meaning: "进程或容器达到内存限制，Linux 已记录 OOM 或 OOM Kill。OOM Kill 表示系统为了回收内存终止了进程。",
    impact: "服务可能突然重启或请求中断。重启只能临时恢复，还需要检查内存限制、对象增长、缓存和回收行为。",
  },
  filesystem_exhaustion: {
    title: "目标文件系统没有可写空间",
    meaning: "目标进程使用的磁盘或挂载点已没有可用字节，或者日志明确出现 ENOSPC（没有剩余空间）。",
    impact: "日志、临时文件、数据库写入和发布操作可能失败。必须确认具体挂载点，不能直接清理整台机器。",
  },
  network_degradation: {
    title: "网络链路出现丢包或超时",
    meaning: "目标连接出现明显的 TCP 重传或超时。常见原因包括丢包、抖动、局部断连和目标端响应过慢。",
    impact: "请求可能变慢、重试或失败。需要结合服务关系确认故障位于当前实例、下游服务还是中间网络。",
  },
  runtime_lock_contention: {
    title: "应用线程大量等待锁",
    meaning: "Java、Go 或 Python 进程中，大量线程在等待同一类锁或同步资源，真正执行工作的线程很少。",
    impact: "CPU 不一定很高，但请求会排队或停顿。重启可临时恢复，长期需要定位持锁代码和临界区。",
  },
  runtime_stall: {
    title: "应用进程仍在运行，但工作没有继续推进",
    meaning: "进程存在且没有退出，但线程、CPU 计数或请求处理长时间没有正常前进。",
    impact: "健康检查可能仍然存活，业务却已经不可用，需要业务请求验证而不能只看进程状态。",
  },
  compound_incident: {
    title: "同时存在多个故障原因",
    meaning: "至少两个不同故障域都得到有效证据支持，例如内存持续增长和锁竞争同时发生。",
    impact: "只处理其中一个原因可能只能部分恢复。系统会保留主因和贡献原因，并分别验证处理结果。",
  },
  same_host_noisy_neighbor: {
    title: "同机其他进程正在抢占资源",
    meaning: "目标服务与异常进程位于同一台 Worker，共享的 CPU、内存或磁盘受到挤压。",
    impact: "应先定位同机高负载实例，再决定限流、迁移或扩容，避免误改当前服务。",
  },
  insufficient_evidence: {
    title: "当前数据不足以判断根因",
    meaning: "已有数据不能安全地区分多个可能原因，或者关键采集缺失。系统会停止猜测并说明需要补充什么。",
    impact: "这不是“没有问题”，而是“目前不能可靠归因”。在补齐实例、时间窗或关键采集前不应自动修改服务。",
  },
  scope_unresolved: {
    title: "还没有确定要检查的实例",
    meaning: "系统只知道服务名称，但不知道它位于哪个 Worker、对应哪个容器或 PID。",
    impact: "如果没有明确范围，采集可能落到错误进程，因此系统不会猜测 PID 或扩散到无关服务。",
  },
};

const CONFIDENCE_LABEL = {
  "高": "高置信",
  "中": "中等置信",
  "低": "低置信",
  "不可判断": "无法判断",
};

export function humanDiagnosis(conclusion = {}) {
  const assessment = conclusion.cluster_assessment || {};
  const classification = assessment.classification || "insufficient_evidence";
  return CLASSIFICATIONS[classification] || {
    title: "已形成诊断结论",
    meaning: conclusion.summary || "请查看关键证据和技术详情。",
    impact: "请先核对证据，再决定是否执行处理动作。",
  };
}

export function confidenceGuide(conclusion = {}) {
  const assessment = conclusion.cluster_assessment || {};
  const rawLevel = assessment.confidence_level || conclusion.confidence_level || "不可判断";
  const numeric = Number(assessment.confidence);
  const score = Number.isFinite(numeric) ? Math.round(numeric * 100) : null;
  const factors = assessment.confidence_factors || {};
  const notes = [];
  if (factors.source_independence === "high") notes.push("至少两类相互独立的数据方向一致");
  else if (factors.source_independence === "medium") notes.push("已有交叉证据，但独立来源仍有限");
  else if (factors.source_independence) notes.push("主要依赖单一来源，需要交叉验证");
  if (factors.scope_coverage === "high") notes.push("目标实例覆盖较完整");
  else if (factors.scope_coverage === "medium") notes.push("只覆盖了部分实例或有限时间窗");
  else if (factors.scope_coverage) notes.push("目标覆盖不足");
  if (factors.discriminating_evidence === "high") notes.push("证据能区分该原因与常见替代原因");
  const prefix = score === null ? "" : `内部证据分为 ${score}/100。`;
  return {
    label: CONFIDENCE_LABEL[rawLevel] || rawLevel,
    explanation: `${prefix}${notes.join("；") || "当前置信度由证据完整性、一致性和区分能力共同计算。"}。该分数表示本次证据支持强度，不等于历史诊断准确率。`,
  };
}

export function findingSummaries(conclusion = {}, limit = 3) {
  const findings = conclusion.findings || [];
  const nonCluster = findings.filter((item) => item.category !== "cluster");
  const source = nonCluster.length ? nonCluster : findings;
  const seen = new Set();
  const result = [];
  for (const item of source) {
    const text = String(item.summary || "").trim();
    if (!text || seen.has(text)) continue;
    seen.add(text);
    result.push({
      id: item.finding_id || text,
      text,
      evidenceCount: (item.evidence_refs || []).length,
      missing: item.missing_evidence || [],
    });
    if (result.length >= limit) break;
  }
  return result;
}
