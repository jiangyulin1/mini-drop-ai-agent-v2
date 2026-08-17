import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import AIDiagnosis from "./AIDiagnosis";
import {
  getCaseWorkspace,
  getCaseHypotheses,
  getCaseInvestigationPlan,
  getCaseCurrentUnderstanding,
  listAgents,
  listDiagnosisSessions,
  listIncidentCaseEvents,
  listIncidentCases,
  listTasks,
  listCaseProposals,
  listCaseRecoveryPlans,
  listCaseEvidenceReviews,
  listRegisteredActions,
  listTargetSessions,
} from "../api/client";

vi.mock("../api/client", () => ({
  appendIncidentCaseMessage: vi.fn(),
  approveDiagnosisProbe: vi.fn(),
  correctIncidentCase: vi.fn(),
  createIncidentCase: vi.fn(),
  createCaseRecoveryPlan: vi.fn(),
  createServiceChange: vi.fn(),
  createTargetSession: vi.fn(),
  createCaseEventSource: vi.fn(() => ({
    addEventListener: vi.fn(),
    close: vi.fn(),
    onopen: null,
    onerror: null,
  })),
  decideCaseRecoveryPlan: vi.fn(),
  dryRunCaseRecoveryPlan: vi.fn(),
  executeCaseRecoveryPlan: vi.fn(),
  createTask: vi.fn(),
  downloadTaskArtifact: vi.fn(),
  getDiagnosisSession: vi.fn(),
  getCaseCurrentUnderstanding: vi.fn(),
  getIncidentCase: vi.fn(),
  getCaseWorkspace: vi.fn(),
  getCaseHypotheses: vi.fn(),
  getCaseInvestigationPlan: vi.fn(),
  getTask: vi.fn(),
  getTaskArtifactContent: vi.fn(),
  getTaskArtifacts: vi.fn(),
  listAgents: vi.fn(),
  listDiagnosisSessions: vi.fn(),
  listIncidentCaseEvents: vi.fn(),
  listIncidentCases: vi.fn(),
  listCaseProposals: vi.fn(),
  listCaseRecoveryPlans: vi.fn(),
  listCaseEvidenceReviews: vi.fn(),
  listRegisteredActions: vi.fn(),
  listTargetSessions: vi.fn(),
  listTasks: vi.fn(),
  ensureEventSourceAuthCookie: vi.fn(() => Promise.resolve()),
  runAIValidation: vi.fn(),
  runIncidentCaseAgentTurn: vi.fn(),
  startIncidentCaseDiagnosis: vi.fn(),
  transitionIncidentCase: vi.fn(),
  verifyCaseRecoveryPlan: vi.fn(),
  rollbackCaseRecoveryPlan: vi.fn(),
}));

const CASE = {
  case_id: "case-service-x",
  title: "service-x CPU 飙高",
  problem_description: "service-x CPU 持续超过 90%",
  recovery_goal: "确认原因并给出安全处置建议",
  run_mode: "COLLABORATE",
  environment: "production",
  target_scope: { service_id: "service-x", instances: [], dependencies: [] },
  state: "NEEDS_SCOPE_CONFIRMATION",
  row_version: 0,
  scope_revision: 1,
  created_at: "2026-08-06T00:00:00Z",
  updated_at: "2026-08-06T00:01:00Z",
  summary: {
    need_you: { required: true, question: "请确认目标范围" },
  },
};

describe("AIDiagnosis workspace", () => {
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
    listAgents.mockResolvedValue([{
      id: "linux-worker-1",
      hostname: "worker1",
      ip_addr: "192.168.10.11",
      status: "ONLINE",
      capabilities: ["sys_metrics", "perf_cpu"],
    }]);
    listIncidentCases.mockResolvedValue({ items: [CASE], total: 1 });
    getCaseWorkspace.mockResolvedValue({
      case_projection_version: 1,
      revisions: { case_command: 1, control: 1, scope: 1, plan: 0 },
      case: CASE,
      engine: { state: "IDLE" },
      plan: {}, campaign: {}, executions: [], evidence: [], causal_graph: {},
      evidence_gaps: [], conclusion: null, recommendations: [], messages: [],
      last_event_seq: 0,
    });
    getCaseCurrentUnderstanding.mockResolvedValue({
      current_understanding: {
        understanding: "OTHER_UNKNOWN：尚无活跃候选解释",
        confirmed: [],
        missing: [],
      },
    });
    getCaseInvestigationPlan.mockResolvedValue({ plan_id: null, steps: [] });
    getCaseHypotheses.mockResolvedValue({ nodes: [], edges: [] });
    listCaseEvidenceReviews.mockResolvedValue({ items: [] });
    listCaseProposals.mockResolvedValue({ proposals: [] });
    listCaseRecoveryPlans.mockResolvedValue({ items: [] });
    listRegisteredActions.mockResolvedValue({ items: [] });
    listTargetSessions.mockResolvedValue([]);
    listIncidentCaseEvents.mockResolvedValue({ items: [], total: 0 });
    listDiagnosisSessions.mockResolvedValue([]);
    listTasks.mockResolvedValue([]);
  });

  it("shows one conversation, scope action, worker status and the data console", async () => {
    render(<MemoryRouter><AIDiagnosis /></MemoryRouter>);

    expect((await screen.findAllByText("service-x CPU 飙高")).length).toBeGreaterThan(0);
    expect(await screen.findByTestId("canonical-workspace")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /调查计划/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /因果链与结论/ })).toBeInTheDocument();
    expect(await screen.findByText("设置诊断范围")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看 Worker 状态" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /更多/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /服务检测/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /长期目标/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /范围与服务关系/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^诊断数据$/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /能力与准确率/ }));
    expect(await screen.findByText(/严格根因准确率 80%/)).toBeInTheDocument();
    expect(screen.getByText(/连续两次通过才判定恢复/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /关\s*闭/ }));
    expect(screen.getByRole("button", { name: /发送并分析/ })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "设置范围" }));
    expect(await screen.findByText("Worker 与目标进程")).toBeInTheDocument();
    expect(screen.getByLabelText("worker1 手动 PID")).toBeInTheDocument();
    expect(screen.getByText("服务关系")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /诊断数据台/ }));
    expect(await screen.findByRole("heading", { name: "诊断数据台" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /新建多机采集/ })).toBeInTheDocument();

    await waitFor(() => expect(getCaseWorkspace).toHaveBeenCalledWith(CASE.case_id));
  }, 15_000);

  it("opens change registration from the selected case", async () => {
    render(<MemoryRouter><AIDiagnosis /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "登记变更" }));
    expect(await screen.findByText("变更只作为待验证相关性，不会直接被当作根因。"))
      .toBeInTheDocument();
    expect(screen.getByLabelText("服务")).toHaveValue("service-x");
  });

  it("opens long-lived target creation", async () => {
    render(<MemoryRouter><AIDiagnosis /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "长期目标" }));
    expect(await screen.findByText(/长期目标会积累信号/)).toBeInTheDocument();
    expect(screen.getByLabelText("服务标识")).toBeInTheDocument();
  });
});
