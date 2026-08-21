import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TaskResult from "./TaskResult";

const api = vi.hoisted(() => ({
  cancelTask: vi.fn(),
  createIncidentCase: vi.fn(),
  downloadTaskArtifact: vi.fn(),
  getDiagnosis: vi.fn(),
  getTask: vi.fn(),
  getTaskAnalysisJobs: vi.fn(),
  getTaskAttempts: vi.fn(),
  getTaskArtifactContent: vi.fn(),
  getTaskArtifacts: vi.fn(),
  getTaskEvents: vi.fn(),
  listTaskDiagnoses: vi.fn(),
  retryTask: vi.fn(),
  submitDiagnosisFeedback: vi.fn(),
}));

vi.mock("../api/client", () => api);
vi.mock("../hooks/usePolling", () => ({ default: () => ({ inFlight: false }) }));
vi.mock("../components/FlamegraphViewer", () => ({ default: () => null }));
vi.mock("../components/TopNChart", () => ({ default: () => null }));
vi.mock("../components/SandboxedArtifactFrame", () => ({ default: () => null }));
vi.mock("../components/TaskArtifactViews", () => ({
  EBPFHistogramChart: () => null,
  JavaFlameViewer: () => null,
  MemoryChart: () => null,
  SysMetricsView: () => null,
}));

const task = {
  id: "task-compat-1",
  name: "线上 CPU 采集",
  agent_id: "worker-1",
  target_pid: 4242,
  collector_type: "perf_cpu",
  status: "DONE",
  collection_status: "DONE",
  analysis_status: "DONE",
  sample_rate: 99,
  duration_sec: 30,
};

const diagnosis = {
  run: {
    id: "diag-compat-1",
    status: "DONE",
    model_name: "compat-rules-v1",
    validated: true,
    summary: "兼容规则归因完成",
  },
  report: {
    report: {
      summary: "CPU 热点证据支持当前候选原因",
      not_enough_evidence: false,
    },
    ranked_causes: [
      {
        cause_id: "cpu_hotspot_recursive",
        confidence: 0.82,
        claim: "递归计算占据主要 CPU 采样",
        evidence_refs: ["get_flamegraph_top:top_functions"],
        uncertainties: ["仍需与历史基线对比"],
        verification_steps: ["在同一负载下重新采集"],
      },
    ],
  },
  tool_results: [
    {
      tool_name: "get_flamegraph_top",
      status: "success",
      evidence_ref: "top_functions",
      input: { limit: 10 },
      output: {
        top_functions: [{ name: "recursive_work", percent: 82 }],
      },
    },
  ],
  repair_plan: {
    risk_level: "manual_only",
    status: "planned",
    requires_user_confirm: true,
    actions: [
      {
        action_id: "action-1",
        action_type: "code_change_suggestion",
        risk_level: "manual_only",
        status: "planned",
        description: "检查递归终止条件并复核调用路径",
        result: null,
      },
    ],
  },
};

function renderTaskResult() {
  return render(
    <MemoryRouter initialEntries={[`/task/${task.id}`]}>
      <Routes>
        <Route path="/task/:taskId" element={<TaskResult />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("TaskResult compatibility diagnosis", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getTask.mockResolvedValue(task);
    api.getTaskEvents.mockResolvedValue([]);
    api.getTaskArtifacts.mockResolvedValue([]);
    api.getTaskAttempts.mockResolvedValue([]);
    api.getTaskAnalysisJobs.mockResolvedValue([]);
    api.listTaskDiagnoses.mockResolvedValue([{ id: diagnosis.run.id }]);
    api.getDiagnosis.mockResolvedValue(diagnosis);
    api.submitDiagnosisFeedback.mockResolvedValue({ ok: true });
  });

  it("loads the latest diagnosis and keeps refresh and feedback available", async () => {
    renderTaskResult();

    expect(await screen.findByText("CPU 热点证据支持当前候选原因")).toBeInTheDocument();
    expect(screen.getByText("兼容规则归因")).toBeInTheDocument();
    expect(screen.getByText("1 次诊断")).toBeInTheDocument();
    expect(screen.getByText("CPU 计算热点")).toBeInTheDocument();
    expect(screen.getByText("结构化证据检查 (1)")).toBeInTheDocument();
    expect(screen.getByText("修复计划")).toBeInTheDocument();
    expect(api.listTaskDiagnoses).toHaveBeenCalledWith(task.id);
    expect(api.getDiagnosis).toHaveBeenCalledWith(diagnosis.run.id);

    fireEvent.click(screen.getByRole("button", { name: "刷新诊断报告" }));
    await waitFor(() => expect(api.getDiagnosis).toHaveBeenCalledTimes(2));

    fireEvent.click(screen.getByRole("button", { name: "标记诊断为正确" }));
    await waitFor(() => expect(api.submitDiagnosisFeedback).toHaveBeenCalledWith(
      diagnosis.run.id,
      {
        predicted_cause_id: "cpu_hotspot_recursive",
        feedback_label: "correct",
      },
    ));
  });
});
