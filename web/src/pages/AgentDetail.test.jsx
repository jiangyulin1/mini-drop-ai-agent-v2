import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AgentDetail from "./AgentDetail";

const api = vi.hoisted(() => ({
  listAgents: vi.fn(),
  listTasks: vi.fn(),
}));

vi.mock("../api/client", () => api);
vi.mock("../hooks/usePolling", () => ({ default: () => ({ inFlight: false }) }));
vi.mock("../lib/echarts", () => ({
  default: {
    init: vi.fn(() => ({
      dispose: vi.fn(),
      resize: vi.fn(),
      setOption: vi.fn(),
    })),
  },
}));

const agent = {
  id: "worker/a",
  hostname: "checkout-worker",
  ip_addr: "10.0.0.31",
  version: "1.4.0",
  os_info: "Linux",
  status: "ONLINE",
  capabilities: ["sys_metrics"],
  latest_metrics: {
    self: {
      cpu_percent: 3.25,
      rss_mb: 51.2,
      read_kb_s: 2,
      write_kb_s: 4,
      children_count: 1,
    },
  },
};

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={[`/agent/${encodeURIComponent(agent.id)}`]}>
      <Routes>
        <Route path="/agent/:agentId" element={<AgentDetail />} />
        <Route path="/agents" element={<div>节点列表占位</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AgentDetail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listAgents.mockResolvedValue([agent]);
    api.listTasks.mockResolvedValue([]);
  });

  it("labels self metrics accurately and returns to the agents page", async () => {
    renderDetail();

    expect(await screen.findByRole("heading", { name: "checkout-worker" })).toBeInTheDocument();
    expect(screen.getByText("Agent 进程开销")).toBeInTheDocument();
    expect(screen.getByText(/Agent 进程趋势/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "返回节点列表" }));
    expect(await screen.findByText("节点列表占位")).toBeInTheDocument();
  });

  it("shows an initial request error instead of a false not-found state", async () => {
    api.listAgents.mockRejectedValue(new Error("节点接口不可用"));

    renderDetail();

    expect(await screen.findByText("节点接口不可用")).toBeInTheDocument();
    expect(screen.queryByText(/Agent .* 未找到/)).not.toBeInTheDocument();
    expect(api.listTasks).not.toHaveBeenCalled();
  });

  it("renders task navigation as a real link", async () => {
    api.listTasks.mockResolvedValue([{
      id: "task/42",
      name: "CPU 采样",
      agent_id: agent.id,
      status: "DONE",
      created_at: "2026-08-20T12:00:00Z",
    }]);

    renderDetail();

    const taskLink = await screen.findByRole("link", { name: "CPU 采样" });
    expect(taskLink).toHaveAttribute("href", "/task/task%2F42");
  });
});
