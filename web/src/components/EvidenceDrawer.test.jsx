import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import EvidenceDrawer from "./EvidenceDrawer";
import {
  getCaseEvidence,
  getEvidenceChainImpact,
  previewCaseEvidence,
  previewCaseEvidenceReview,
  reviewCaseEvidence,
} from "../api/client";

vi.mock("../api/client", () => ({
  createCaseEvidenceAnalysis: vi.fn(),
  downloadCaseEvidence: vi.fn(),
  getCaseEvidence: vi.fn(),
  getEvidenceChainImpact: vi.fn(),
  previewCaseEvidence: vi.fn(),
  previewCaseEvidenceReview: vi.fn(),
  reviewCaseEvidence: vi.fn(),
}));

describe("EvidenceDrawer citation focus", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getCaseEvidence.mockResolvedValue({
      evidence_id: "ev-1",
      status: "ACTIVE",
      artifact_type: "process_scan",
      reviews: [],
      analyses: [],
    });
    getEvidenceChainImpact.mockResolvedValue({ chains: [] });
    previewCaseEvidence.mockResolvedValue({
      evidence_id: "ev-1",
      projection_hash: "projection-hash-1",
      content: { summary: "CPU hot", signals: { cpu: 92.4 } },
      truncated: false,
    });
    previewCaseEvidenceReview.mockResolvedValue({
      current_review_revision: 0,
      impact_token: "impact-token-1",
      assessment_result: {
        recommended_decision: "LOW_TRUST",
        derived_trust_score: 65,
        reasons: ["没有独立数据源交叉佐证"],
      },
      affected: { analysis_runs: 1, hypotheses: 2, conclusions: 1, recovery_plans: 1 },
      predicted_conclusion_state: "INSUFFICIENT_EVIDENCE",
      requires_approval: true,
    });
    reviewCaseEvidence.mockResolvedValue({ review_revision: 1 });
  });

  it("shows the exact cited field, projection and quoted value", async () => {
    render(<EvidenceDrawer
      open
      caseId="case-1"
      evidence={{ evidence_id: "ev-1", status: "ACTIVE" }}
      focusCitation={{
        evidence_id: "ev-1",
        projection_hash: "projection-hash-1",
        field_path: "summary",
        quote: "CPU hot",
        start: 0,
        end: 7,
      }}
      onClose={vi.fn()}
    />);

    const focus = await screen.findByTestId("evidence-citation-focus");
    expect(focus).toHaveTextContent("summary");
    expect(focus).toHaveTextContent("projection-hash-1");
    expect(focus).toHaveTextContent("CPU hot");
    expect(focus).toHaveTextContent("0–7");
    expect(focus).not.toHaveTextContent("投影已变更");
  });

  it("resolves a citation against its pinned historical projection", async () => {
    getCaseEvidence.mockResolvedValue({
      evidence_id: "ev-1",
      status: "ACTIVE",
      reviews: [],
      analyses: [],
      projections: [{
        projection_hash: "projection-hash-old",
        content: { signals: { cpu: 71.2 } },
      }, {
        projection_hash: "projection-hash-1",
        content: { signals: { cpu: 92.4 } },
      }],
    });

    render(<EvidenceDrawer
      open
      caseId="case-1"
      evidence={{ evidence_id: "ev-1", status: "ACTIVE" }}
      focusCitation={{
        evidence_id: "ev-1",
        projection_hash: "projection-hash-old",
        field_path: "signals.cpu",
      }}
      onClose={vi.fn()}
    />);

    const focus = await screen.findByTestId("evidence-citation-focus");
    expect(focus).toHaveTextContent("历史投影");
    expect(within(focus).getByText("71.2")).toBeInTheDocument();
    expect(focus).not.toHaveTextContent("92.4");
    expect(focus).not.toHaveTextContent("分析时固定的投影不可用");
  });

  it("warns without substituting the current value when the pinned projection is missing", async () => {
    getCaseEvidence.mockResolvedValue({
      evidence_id: "ev-1",
      status: "ACTIVE",
      reviews: [],
      analyses: [],
      projections: [{
        projection_hash: "projection-hash-1",
        content: { signals: { cpu: 92.4 } },
      }],
    });

    render(<EvidenceDrawer
      open
      caseId="case-1"
      evidence={{ evidence_id: "ev-1", status: "ACTIVE" }}
      focusCitation={{
        evidence_id: "ev-1",
        projection_hash: "projection-hash-missing",
        field_path: "signals.cpu",
      }}
      onClose={vi.fn()}
    />);

    const focus = await screen.findByTestId("evidence-citation-focus");
    expect(focus).toHaveTextContent("投影不可用");
    expect(focus).toHaveTextContent("分析时固定的投影不可用");
    expect(focus).not.toHaveTextContent("92.4");
  });

  it("previews governance impact before allowing exclusion", async () => {
    render(<EvidenceDrawer
      open
      caseId="case-1"
      evidence={{ evidence_id: "ev-1", status: "ACTIVE" }}
      onClose={vi.fn()}
    />);

    await screen.findByText("标记可信");
    fireEvent.click(screen.getByRole("button", { name: /排除/ }));
    await waitFor(() => expect(previewCaseEvidenceReview).toHaveBeenCalledWith(
      "case-1",
      "ev-1",
      expect.objectContaining({ decision: "EXCLUDED" }),
    ));
    expect(await screen.findByText(/建议：LOW_TRUST · 治理分 65/)).toBeInTheDocument();
    expect(screen.getByText(/影响：分析 1，假设 2，结论 1，恢复方案 1/)).toBeInTheDocument();
    expect(screen.getByText(/本次操作需要审批角色/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "确认审查" })).toBeEnabled();
  });
});
