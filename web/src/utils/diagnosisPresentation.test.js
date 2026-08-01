import { describe, expect, it } from "vitest";

import {
  causeLabel,
  effectiveToolStatus,
  evidenceRefLabel,
  repairLabel,
  toolResultSummary,
} from "./diagnosisPresentation";

describe("diagnosis presentation", () => {
  it("does not report eBPF as missing for a py-spy task", () => {
    const tool = {
      tool_name: "get_ebpf_latency_summary",
      status: "missing",
      output: { total_samples: 0 },
    };
    const status = effectiveToolStatus(tool, "pyspy", ["flamegraph_svg"]);
    expect(status).toBe("not_applicable");
    expect(toolResultSummary(tool, status)).toContain("不属于当前采集器");
  });

  it("explains that an existing SVG can be reprocessed into TopN", () => {
    const tool = { tool_name: "get_flamegraph_top", status: "missing", output: {} };
    const status = effectiveToolStatus(tool, "pyspy", ["flamegraph_svg"]);
    expect(status).toBe("derivable");
    expect(toolResultSummary(tool, status)).toContain("重新归因");
  });

  it("translates technical evidence paths", () => {
    expect(evidenceRefLabel("tool_results.get_flamegraph_top")).toBe("CPU 热点摘要");
    expect(evidenceRefLabel("top_functions[0].name")).toBe("火焰图热点");
  });

  it("translates artifact storage connectivity diagnoses", () => {
    expect(causeLabel("artifact_storage_unreachable")).toBe("对象存储上传链路异常");
    expect(repairLabel("storage_connectivity_check")).toBe("存储链路检查");
  });
});
