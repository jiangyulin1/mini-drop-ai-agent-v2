import { describe, expect, it } from "vitest";
import {
  caseStatus,
  evidenceTrust,
  isActiveCase,
  isVerifiedSuccess,
  planStatus,
  riskLevel,
} from "./opsMappings";

describe("AIOps semantic mappings", () => {
  it("uses verified language only for resolved cases", () => {
    expect(caseStatus("RESOLVED").label).toContain("完成验证");
    expect(isVerifiedSuccess("DONE")).toBe(false);
    expect(isVerifiedSuccess("VERIFICATION_PASSED")).toBe(true);
  });

  it("explains safety and trust states", () => {
    expect(riskLevel("WRITE_HIGH").description).toContain("人工审批");
    expect(evidenceTrust("EXCLUDED").description).toContain("Agent Prompt");
    expect(planStatus("SUPERSEDED").label).toContain("Revision");
  });

  it("classifies active cases conservatively", () => {
    expect(isActiveCase("WAITING_APPROVAL")).toBe(true);
    expect(isActiveCase("RESOLVED")).toBe(false);
  });
});
