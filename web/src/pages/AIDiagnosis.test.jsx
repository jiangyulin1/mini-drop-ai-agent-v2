import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import AIDiagnosis from "./AIDiagnosis";
import {
  getIncidentCase,
  listAgents,
  listDiagnosisSessions,
  listIncidentCaseEvents,
  listIncidentCases,
  listTasks,
} from "../api/client";

vi.mock("../api/client", () => ({
  appendIncidentCaseMessage: vi.fn(),
  approveDiagnosisProbe: vi.fn(),
  correctIncidentCase: vi.fn(),
  createIncidentCase: vi.fn(),
  createTask: vi.fn(),
  downloadTaskArtifact: vi.fn(),
  getDiagnosisSession: vi.fn(),
  getIncidentCase: vi.fn(),
  getTask: vi.fn(),
  getTaskArtifactContent: vi.fn(),
  getTaskArtifacts: vi.fn(),
  listAgents: vi.fn(),
  listDiagnosisSessions: vi.fn(),
  listIncidentCaseEvents: vi.fn(),
  listIncidentCases: vi.fn(),
  listTasks: vi.fn(),
  runAIValidation: vi.fn(),
  startIncidentCaseDiagnosis: vi.fn(),
  transitionIncidentCase: vi.fn(),
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
    getIncidentCase.mockResolvedValue(CASE);
    listIncidentCaseEvents.mockResolvedValue({ items: [], total: 0 });
    listDiagnosisSessions.mockResolvedValue([]);
    listTasks.mockResolvedValue([]);
  });

  it("shows one conversation, scope action, worker status and the data console", async () => {
    render(<MemoryRouter><AIDiagnosis /></MemoryRouter>);

    expect((await screen.findAllByText("service-x CPU 飙高")).length).toBeGreaterThan(0);
    expect(await screen.findByText("设置诊断范围")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看 Worker 状态" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /发送并分析/ })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "设置范围" }));
    expect(await screen.findByText("Worker 与目标进程")).toBeInTheDocument();
    expect(screen.getByLabelText("worker1 手动 PID")).toBeInTheDocument();
    expect(screen.getByText("服务关系")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /诊断数据台/ }));
    expect(await screen.findByRole("heading", { name: "诊断数据台" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /新建多机采集/ })).toBeInTheDocument();

    await waitFor(() => expect(getIncidentCase).toHaveBeenCalledWith(CASE.case_id));
  });
});
