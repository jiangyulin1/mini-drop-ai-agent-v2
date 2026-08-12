import { describe, expect, it } from "vitest";

import { confidenceGuide, findingSummaries, humanDiagnosis } from "./diagnosisHumanize";

describe("human diagnosis presentation", () => {
  it("explains OOM without assuming advanced knowledge", () => {
    const value = humanDiagnosis({ cluster_assessment: { classification: "process_oom" } });
    expect(value.title).toContain("内存耗尽");
    expect(value.meaning).toContain("OOM Kill");
    expect(value.impact).toContain("重启只能临时恢复");
  });

  it("separates evidence confidence from historical accuracy", () => {
    const value = confidenceGuide({ cluster_assessment: {
      confidence: 0.82,
      confidence_level: "高",
      confidence_factors: { source_independence: "high", scope_coverage: "medium" },
    } });
    expect(value.label).toBe("高置信");
    expect(value.explanation).toContain("82/100");
    expect(value.explanation).toContain("不等于历史诊断准确率");
  });

  it("removes duplicate cluster summaries when detailed findings exist", () => {
    const value = findingSummaries({ findings: [
      { finding_id: "detail", category: "memory", summary: "出现 2 次 OOM Kill", evidence_refs: ["e1"] },
      { finding_id: "cluster", category: "cluster", summary: "出现 2 次 OOM Kill", evidence_refs: ["e1"] },
    ] });
    expect(value).toHaveLength(1);
    expect(value[0].evidenceCount).toBe(1);
  });
});
