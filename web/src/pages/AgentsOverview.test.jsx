import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AgentsOverview from "./AgentsOverview";

const api = vi.hoisted(() => ({
  listAgents: vi.fn(),
}));

vi.mock("../api/client", () => api);

const worker = {
  id: "worker-1",
  hostname: "checkout-worker-with-a-long-hostname",
  ip_addr: "10.0.0.21",
  version: "1.4.0",
  status: "ONLINE",
  last_heartbeat_at: "2026-08-21T00:00:00Z",
  capabilities: ["sys_metrics", "process_scan"],
  latest_metrics: {
    self: {
      cpu_percent: 2.5,
      rss_mb: 44.2,
      write_kb_s: 3,
    },
  },
};

describe("AgentsOverview", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.listAgents.mockResolvedValue([worker]);
  });

  it("uses current worker data in the topology without a fabricated control address", async () => {
    render(<MemoryRouter><AgentsOverview /></MemoryRouter>);

    expect(await screen.findByRole("region", {
      name: "控制服务与 1 个 Worker 的连接拓扑",
    })).toBeInTheDocument();
    expect(screen.getByText("当前控制服务")).toBeInTheDocument();
    expect(screen.getAllByText("10.0.0.21")).toHaveLength(2);
    expect(screen.getByText("Agent 进程开销")).toBeInTheDocument();
    expect(screen.queryByText("192.168.10.10")).not.toBeInTheDocument();
  });

  it("keeps current node content visible during a quiet refresh", async () => {
    let resolveRefresh;
    const refreshResult = new Promise((resolve) => {
      resolveRefresh = resolve;
    });
    api.listAgents
      .mockResolvedValueOnce([worker])
      .mockReturnValueOnce(refreshResult);

    render(<MemoryRouter><AgentsOverview /></MemoryRouter>);
    expect(await screen.findByRole("heading", { name: "checkout-worker-with-a-long-hostname" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "刷新节点列表" }));
    await waitFor(() => expect(api.listAgents).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("heading", { name: "checkout-worker-with-a-long-hostname" })).toBeInTheDocument();

    await act(async () => {
      resolveRefresh([{ ...worker, hostname: "checkout-worker-updated" }]);
      await refreshResult;
    });
    expect(await screen.findByRole("heading", { name: "checkout-worker-updated" })).toBeInTheDocument();
  });
});
