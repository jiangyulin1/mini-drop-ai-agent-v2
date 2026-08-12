import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import NLPTaskInput from "./NLPTaskInput";
import { createTask, listAgents, listTaskKinds, nlpParse } from "../api/client";

vi.mock("../api/client", () => ({
  createTask: vi.fn(),
  listAgents: vi.fn(),
  listTaskKinds: vi.fn(),
  nlpParse: vi.fn(),
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

describe("NLPTaskInput Worker/PID binding", () => {
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
    nlpParse.mockResolvedValue({
      process_name: "mysqld",
      collector_type: "perf_cpu",
      duration_sec: 20,
      sample_rate: 99,
      reasoning: "CPU 热点需要采集调用栈",
      // This PID belongs to the server host and must never be reused for a Worker.
      candidate_pids: [{ pid: 9999, comm: "server-mysqld" }],
    });
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

  it("ignores server-host candidate PIDs and binds natural-language creation to the confirmed Worker PID", async () => {
    const onTaskCreated = vi.fn();
    render(<NLPTaskInput onTaskCreated={onTaskCreated} />);

    await screen.findByTestId("process-picker-worker-b");
    fireEvent.click(screen.getByRole("tab", { name: /自然语言/ }));
    const naturalPanel = screen.getByRole("tabpanel", { name: /自然语言/ });
    const queryInput = within(naturalPanel).getByPlaceholderText(/描述性能问题/);
    fireEvent.change(queryInput, { target: { value: "mysqld CPU 飙高，帮我看看" } });
    fireEvent.click(within(naturalPanel).getByRole("button", { name: "解析意图" }));

    expect(await within(naturalPanel).findByText("确认采集参数与执行目标")).toBeInTheDocument();
    expect(within(naturalPanel).getByTestId("process-picker-worker-b")).toBeInTheDocument();
    expect(within(naturalPanel).queryByText("9999")).not.toBeInTheDocument();

    fireEvent.click(within(naturalPanel).getByRole("button", { name: "为 worker-b 确认 PID 2202" }));
    fireEvent.click(within(naturalPanel).getByRole("button", { name: /开始采集/ }));

    await waitFor(() => expect(createTask).toHaveBeenCalledWith(expect.objectContaining({
      agent_id: "worker-b",
      target_pid: 2202,
      collector_type: "perf_cpu",
      duration_sec: 20,
      sample_rate: 99,
      options: { nlp_query: "mysqld CPU 飙高，帮我看看" },
    })));
    expect(createTask.mock.calls[0][0].target_pid).not.toBe(9999);
    expect(onTaskCreated).toHaveBeenCalledWith("task-created");
  });

  it("clears the confirmed PID when the natural-language Worker changes", async () => {
    render(<NLPTaskInput />);

    await screen.findByTestId("process-picker-worker-b");
    fireEvent.click(screen.getByRole("tab", { name: /自然语言/ }));
    const naturalPanel = screen.getByRole("tabpanel", { name: /自然语言/ });
    fireEvent.change(within(naturalPanel).getByPlaceholderText(/描述性能问题/), {
      target: { value: "分析 mysqld CPU" },
    });
    fireEvent.click(within(naturalPanel).getByRole("button", { name: "解析意图" }));

    await within(naturalPanel).findByTestId("process-picker-worker-b");
    fireEvent.click(within(naturalPanel).getByRole("button", { name: "为 worker-b 确认 PID 2202" }));
    expect(within(naturalPanel).getByRole("button", { name: /开始采集/ })).toBeEnabled();

    const workerSelect = within(naturalPanel).getByRole("combobox");
    fireEvent.mouseDown(workerSelect);
    fireEvent.click(await screen.findByText("在线节点 C · 在线"));

    expect(await within(naturalPanel).findByTestId("process-picker-worker-c")).toHaveTextContent("当前 PID：未选择");
    expect(within(naturalPanel).getByRole("button", { name: /开始采集/ })).toBeDisabled();
    expect(createTask).not.toHaveBeenCalled();
  });
});
