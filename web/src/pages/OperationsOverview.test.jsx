import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import OperationsOverview from "./OperationsOverview";
import {
  getAgentRuntimeConfig,
  healthz,
  listAgents,
  listAuditLogs,
  listIncidentCases,
  listSystemControls,
  listTasks,
} from "../api/client";

vi.mock("../api/client", () => ({
  getAgentRuntimeConfig: vi.fn(),
  healthz: vi.fn(),
  listAgents: vi.fn(),
  listAuditLogs: vi.fn(),
  listIncidentCases: vi.fn(),
  listSystemControls: vi.fn(),
  listTasks: vi.fn(),
}));
vi.mock("../hooks/useSSE", () => ({ default: () => ({ connected: false, connectionState: "polling" }) }));

describe("OperationsOverview", () => {
  beforeEach(() => {
    healthz.mockResolvedValue({ healthy: true, version: "0.1.0", checks: { database: { status: "ok" }, storage: { status: "ok" }, analyzer: { status: "ok", workers_online: 1 } } });
    getAgentRuntimeConfig.mockResolvedValue({ runtime_type: "pi", runtime_version: "0.84.2", mode: "pi", ready: true, flags: { agent_auto_read_low: false, agent_mcp_enabled: false, agent_skills_enabled: "0", agent_cluster_fanout_enabled: false, agent_max_active_cases: 4 } });
    listAgents.mockResolvedValue([{ id: "linux-worker-1", status: "ONLINE" }, { id: "linux-worker-2", status: "ONLINE" }, { id: "demo-worker", status: "OFFLINE" }]);
    listIncidentCases.mockResolvedValue({ items: [{ case_id: "case-1", title: "checkout 延迟", state: "WAITING_APPROVAL", summary: { need_you: { required: true, question: "批准只读采集" } } }] });
    listTasks.mockResolvedValue(Array.from({ length: 8 }, (_, index) => ({ id: `failed-${index}`, status: "FAILED" })));
    listAuditLogs.mockResolvedValue([]);
    listSystemControls.mockResolvedValue({ items: [] });
  });

  it("separates historical failures from current analyzer health", async () => {
    render(<MemoryRouter><OperationsOverview /></MemoryRouter>);
    expect(await screen.findByText(/存在 8 条历史失败记录/)).toHaveTextContent("pending=0、running=0，Analyzer 健康");
    expect(screen.getByText("2 / 2")).toBeInTheDocument();
    expect(screen.getByText("批准只读采集")).toBeInTheDocument();
  });

  it("shows autonomy flags as server-projected disabled capabilities", async () => {
    render(<MemoryRouter><OperationsOverview /></MemoryRouter>);
    expect(await screen.findByText("Auto READ_LOW")).toBeInTheDocument();
    expect(screen.getAllByText("关闭").length).toBeGreaterThanOrEqual(4);
    expect(screen.getByText("0.84.2")).toBeInTheDocument();
  });
});
