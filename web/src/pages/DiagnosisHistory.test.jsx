import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DiagnosisHistory from "./DiagnosisHistory";
import { listDiagnoses, listTasks } from "../api/client";

vi.mock("../api/client", () => ({
  listTasks: vi.fn(),
  listDiagnoses: vi.fn(),
}));

describe("DiagnosisHistory", () => {
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
    const nativeGetComputedStyle = window.getComputedStyle;
    vi.spyOn(window, "getComputedStyle").mockImplementation((element) => (
      nativeGetComputedStyle(element)
    ));
    listTasks.mockResolvedValue([
      {
        id: "task-1",
        name: "release baseline smoke linux-worker-1",
        agent_id: "linux-worker-1",
        target_pid: 1,
        collector_type: "sys_metrics",
      },
      { id: "task-2", name: "无诊断任务" },
    ]);
    listDiagnoses.mockResolvedValue([
      {
        id: "diag-1",
        run: {
          task_id: "task-1",
          status: "DONE",
          model_name: "test-model",
          summary: "synthetic summary",
          created_at: "2026-07-30T00:00:00Z",
        },
        report: {
          ranked_causes: [{ cause_id: "cpu", confidence: 0.8 }],
        },
        feedback: null,
      },
    ]);
  });

  it("loads all diagnosis history through one aggregate request", async () => {
    render(
      <MemoryRouter>
        <DiagnosisHistory />
      </MemoryRouter>,
    );

    expect(await screen.findByText("synthetic summary")).toBeInTheDocument();
    expect(screen.getByText("发布基线检查 · linux-worker-1")).toBeInTheDocument();
    await waitFor(() => {
      expect(listTasks).toHaveBeenCalledTimes(1);
      expect(listDiagnoses).toHaveBeenCalledTimes(1);
      expect(listDiagnoses).toHaveBeenCalledWith({ limit: 500 });
    });
  });
});
