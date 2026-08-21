import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Dashboard from "./Dashboard";
import {
  getTask,
  getTaskEvents,
  listAgents,
  listTasks,
} from "../api/client";

vi.mock("../api/client", () => ({
  deleteTask: vi.fn(),
  getTask: vi.fn(),
  getTaskEvents: vi.fn(),
  listAgents: vi.fn(),
  listTasks: vi.fn(),
}));
vi.mock("../hooks/usePolling", () => ({
  default: () => ({ isPolling: true }),
}));
vi.mock("../hooks/useSSE", () => ({
  default: () => ({ connected: false }),
}));
vi.mock("../components/NLPTaskInput", () => ({
  default: () => <div data-testid="task-creator">任务创建表单</div>,
}));
vi.mock("../components/MultiAgentCollectionModal", () => ({
  default: () => null,
}));
vi.mock("../components/TaskVisualizationPreview", () => ({
  default: ({ taskId }) => <div data-testid={`preview-${taskId}`} />,
}));

const TASKS = [
  {
    id: "task-done",
    name: "CPU 采集",
    agent_id: "worker-a",
    target_pid: 101,
    collector_type: "perf_cpu",
    status: "DONE",
    created_at: "2026-08-20T01:00:00Z",
    updated_at: "2026-08-20T01:01:00Z",
  },
  {
    id: "task-failed",
    name: "内存失败采集",
    agent_id: "worker-a",
    target_pid: 202,
    collector_type: "memory",
    status: "FAILED",
    created_at: "2026-08-20T00:00:00Z",
    updated_at: "2026-08-20T00:01:00Z",
  },
];

function LocationResult() {
  const location = useLocation();
  return <div data-testid="case-location">{location.pathname}{location.search}</div>;
}

function renderDashboard() {
  return render(
    <MemoryRouter initialEntries={["/tasks"]}>
      <Routes>
        <Route path="/tasks" element={<Dashboard />} />
        <Route path="/cases" element={<LocationResult />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Dashboard task workspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listTasks.mockResolvedValue(TASKS);
    listAgents.mockResolvedValue([
      { id: "worker-a", hostname: "生产节点", status: "ONLINE" },
      { id: "demo-worker", hostname: "演示节点", status: "OFFLINE" },
    ]);
    getTask.mockImplementation((taskId) => Promise.resolve(
      TASKS.find((task) => task.id === taskId),
    ));
    getTaskEvents.mockResolvedValue([]);
  });

  it("keeps creation behind the action and excludes the historical demo Worker", async () => {
    renderDashboard();

    expect(await screen.findByTestId("preview-task-done")).toBeInTheDocument();
    expect(screen.getByText("1/1")).toBeInTheDocument();
    expect(screen.queryByText("演示节点")).not.toBeInTheDocument();
    expect(screen.queryByTestId("task-creator")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /新建采集/ }));
    expect(await screen.findByTestId("task-creator")).toBeInTheDocument();
  });

  it("updates the detail selection when filtering and opens the canonical Case route", async () => {
    renderDashboard();

    expect(await screen.findByTestId("preview-task-done")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("textbox", { name: "搜索采集任务" }), {
      target: { value: "内存失败" },
    });
    expect(await screen.findByTestId("preview-task-failed")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("textbox", { name: "搜索采集任务" }), {
      target: { value: "CPU 采集" },
    });
    await waitFor(() => expect(screen.getByTestId("preview-task-done")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /创建 AI 诊断/ }));

    expect(await screen.findByTestId("case-location"))
      .toHaveTextContent("/cases?fromTask=task-done");
  });
});
