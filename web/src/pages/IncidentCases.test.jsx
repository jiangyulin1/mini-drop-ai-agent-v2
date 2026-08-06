import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import IncidentCases from "./IncidentCases";
import {
  getIncidentCase,
  getCaseHypotheses,
  listCaseIterations,
  listCaseContextPackets,
  listCaseModelAttempts,
  listIncidentCaseEvents,
  listIncidentCases,
  startIncidentCaseDiagnosis,
} from "../api/client";

vi.mock("../api/client", () => ({
  appendIncidentCaseMessage: vi.fn(),
  correctIncidentCase: vi.fn(),
  createIncidentCase: vi.fn(),
  getIncidentCase: vi.fn(),
  getCaseHypotheses: vi.fn(),
  listCaseIterations: vi.fn(),
  listCaseContextPackets: vi.fn(),
  listCaseModelAttempts: vi.fn(),
  listIncidentCaseEvents: vi.fn(),
  listIncidentCases: vi.fn(),
  startIncidentCaseDiagnosis: vi.fn(),
  transitionIncidentCase: vi.fn(),
}));

const CASE = {
  case_id: "case-1",
  title: "checkout 延迟事故",
  problem_description: "checkout 延迟显著升高",
  recovery_goal: "p95 恢复至 300ms",
  run_mode: "COLLABORATE",
  environment: "production",
  target_scope: { service_id: "checkout" },
  time_range: {},
  state: "OPEN",
  row_version: 0,
  scope_revision: 1,
  updated_at: "2026-08-05T12:00:00Z",
  summary: {
    impact: { status: "unknown", message: "影响待确认" },
    current_finding: { status: "unknown", statement: "尚无判断" },
    what_ai_is_doing: { status: "ready", message: "等待调查" },
    need_you: { required: false, question: "" },
    recovery: { status: "not_started", goal: "p95 恢复至 300ms" },
  },
};

describe("IncidentCases", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockImplementation(() => ({
        matches: false,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    });
    listIncidentCases.mockResolvedValue({ items: [CASE], total: 1 });
    getIncidentCase.mockResolvedValue(CASE);
    listIncidentCaseEvents.mockResolvedValue({ items: [], total: 0 });
    listCaseContextPackets.mockResolvedValue({ items: [], total: 0 });
    listCaseModelAttempts.mockResolvedValue({ items: [], total: 0 });
    getCaseHypotheses.mockResolvedValue({ hypotheses: [], edges: [] });
    listCaseIterations.mockResolvedValue({ items: [], total: 0 });
  });

  it("prioritizes the current finding and lazy-loads technical panels", async () => {
    const view = render(<IncidentCases />);

    expect((await screen.findAllByText("checkout 延迟事故")).length).toBeGreaterThan(0);
    expect(await screen.findByText("当前判断")).toBeInTheDocument();
    expect(screen.getByText("影响")).toBeInTheDocument();
    expect(screen.getByText("AI 当前动作")).toBeInTheDocument();
    expect(screen.getByText("恢复目标")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /开始调查/ })).toBeInTheDocument();
    expect(listCaseContextPackets).not.toHaveBeenCalled();
    expect(listCaseModelAttempts).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("tab", { name: "技术审计" }));
    await waitFor(() => expect(listCaseContextPackets).toHaveBeenCalledWith("case-1", { limit: 50 }));
    expect(listCaseModelAttempts).toHaveBeenCalledWith("case-1", { limit: 50 });

    await waitFor(() => expect(getIncidentCase).toHaveBeenCalledWith("case-1"));
    view.unmount();
  });

  it("starts a diagnosis with the current row version", async () => {
    startIncidentCaseDiagnosis.mockResolvedValue({});
    const view = render(<IncidentCases />);

    fireEvent.click(await screen.findByRole("button", { name: /开始调查/ }));
    await waitFor(() => expect(startIncidentCaseDiagnosis).toHaveBeenCalledWith("case-1", {
      expected_row_version: 0,
    }));
    view.unmount();
  });
});
