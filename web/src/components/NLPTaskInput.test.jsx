import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import NLPTaskInput from "./NLPTaskInput";
import { createTask, listAgents, listTaskKinds } from "../api/client";

vi.mock("../api/client", () => ({
  createTask: vi.fn(),
  listAgents: vi.fn(),
  listTaskKinds: vi.fn(),
}));

vi.mock("./AgentProcessPicker", () => ({
  default: ({ agentId, value, onChange }) => {
    const pid = agentId === "worker-c" ? 3303 : 2202;
    return (
      <div data-testid={`process-picker-${agentId || "none"}`}>
        <button type="button" onClick={() => onChange(pid)}>
          为 {agentId || "未选 Worker"} 确认 PID {pid}
        </button>
        <span>当前 PID：{value || "未选择"}</span>
      </div>
    );
  },
}));

const workers = [
  {
    id: "demo-worker",
    hostname: "历史演示节点",
    status: "ONLINE",
    capabilities: ["perf_cpu"],
  },
  {
    id: "worker-a",
    hostname: "离线节点 A",
    status: "OFFLINE",
    capabilities: ["perf_cpu"],
  },
  {
    id: "worker-b",
    hostname: "在线节点 B",
    status: "ONLINE",
    capabilities: ["perf_cpu"],
  },
  {
    id: "worker-c",
    hostname: "在线节点 C",
    status: "ONLINE",
    capabilities: ["perf_cpu"],
  },
];

describe("NLPTaskInput quick task creation", () => {
  afterEach(() => {
    cleanup();
  });

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
    listAgents.mockResolvedValue(workers.map((worker) => ({ ...worker })));
    listTaskKinds.mockResolvedValue([]);
    createTask.mockResolvedValue({ task_id: "task-created" });
  });

  it("creates a quick task with the preferred online capable Worker and its confirmed PID", async () => {
    const onTaskCreated = vi.fn();
    render(<NLPTaskInput onTaskCreated={onTaskCreated} />);

    const picker = await screen.findByTestId("process-picker-worker-b");
    fireEvent.click(screen.getByRole("button", { name: "为 worker-b 确认 PID 2202" }));
    expect(picker).toHaveTextContent("当前 PID：2202");

    fireEvent.click(screen.getByRole("button", { name: /开始采集/ }));

    await waitFor(() => expect(createTask).toHaveBeenCalledWith(expect.objectContaining({
      agent_id: "worker-b",
      target_pid: 2202,
      collector_type: "perf_cpu",
      options: { source: "web_quick_preset" },
    })));
    expect(onTaskCreated).toHaveBeenCalledWith("task-created");
  });

  it("does not expose a natural-language tab", async () => {
    render(<NLPTaskInput />);
    await screen.findByTestId("process-picker-worker-b");
    expect(screen.queryByRole("tab", { name: /自然语言/ })).not.toBeInTheDocument();
    expect(screen.queryByText("解析意图")).not.toBeInTheDocument();
  });
});
