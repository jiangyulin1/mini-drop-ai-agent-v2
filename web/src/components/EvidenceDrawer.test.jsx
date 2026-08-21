import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import EvidenceDrawer from "./EvidenceDrawer";
import { getCaseEvidence, previewCaseEvidence } from "../api/client";

vi.mock("../api/client", () => ({
  createCaseEvidenceAnalysis: vi.fn(),
  downloadCaseEvidence: vi.fn(),
  getCaseEvidence: vi.fn(),
  previewCaseEvidence: vi.fn(),
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
    previewCaseEvidence.mockResolvedValue({
      evidence_id: "ev-1",
      projection_hash: "projection-hash-1",
      content: { summary: "CPU hot", signals: { cpu: 92.4 } },
      truncated: false,
    });
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
});
