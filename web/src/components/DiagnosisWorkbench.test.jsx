import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DiagnosisWorkbench from "./DiagnosisWorkbench";

const nodeNames = [
  "understand_intent",
  "resolve_scope",
  "build_hypotheses",
  "plan_evidence",
  "risk_gate",
  "run_probes",
  "normalize_evidence",
  "analyze_evidence",
  "assess_cluster",
  "retrieve_knowledge",
  "generate_actions",
  "verify_report",
];

const detail = {
  diagnosis_id: "diag_replay_test",
  status: "COMPLETED",
  created_at: "2026-07-29T10:00:00Z",
  updated_at: "2026-07-29T10:01:00Z",
  normalized_intent: { analysis_strategy: "DECISION_TREE" },
  pipeline_nodes: nodeNames.map((node_name) => ({
    node_name,
    status: "COMPLETED",
    attempt: 1,
    started_at: "2026-07-29T10:00:00Z",
    finished_at: "2026-07-29T10:00:01Z",
    input_refs: [],
    output_refs: [],
    metrics: {},
  })),
  evidence: [{
    evidence_id: "ev_replay_test",
    diagnosis_id: "diag_replay_test",
    source_system: "mini_drop",
    source_type: "derived_artifact",
    target: { agent_id: "linux-worker-1", pid: 1 },
    event_time_range: {
      start: "2026-07-29T10:00:00Z",
      end: "2026-07-29T10:00:15Z",
    },
    data_quality: { completeness: "high" },
    observed_value: { cpu_percent: 80 },
  }],
  hypothesis_graph: { hypotheses: [], edges: [] },
  probes: [],
  coverage: [{ status: "COMPLETED" }],
  conclusion_versions: [{}],
  latest_conclusion: null,
};

describe("DiagnosisWorkbench", () => {
  beforeEach(() => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockImplementation((query) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
    const nativeGetComputedStyle = window.getComputedStyle;
    vi.spyOn(window, "getComputedStyle").mockImplementation((element) => (
      nativeGetComputedStyle(element)
    ));
  });

  it("shows decision evidence first and keeps internal pipeline details collapsed", () => {
    render(<DiagnosisWorkbench detail={detail} />);

    expect(screen.getByText("证据链 (1)")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /下一步/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/第 1\/12 步/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("内部过程记录（调试）"));
    expect(screen.getByText("第 1/12 步 · 以下内容来自本次真实会话快照")).toBeInTheDocument();
  });
});
