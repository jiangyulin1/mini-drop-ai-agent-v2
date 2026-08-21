import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import RuntimeConsole from "./RuntimeConsole";

const api = vi.hoisted(() => ({
  getAgentRuntimeConfig: vi.fn(),
  getAIConfig: vi.fn(),
  healthz: vi.fn(),
  listSystemControls: vi.fn(),
}));

vi.mock("../api/client", () => api);

const runtime = {
  runtime_type: "deterministic",
  runtime_version: "0.1.0",
  mode: "deterministic",
  ai_ready: false,
  ai_status: "NOT_CONFIGURED",
  flags: {
    pi_runtime_url: false,
    agent_auto_read_low: false,
    agent_mcp_enabled: false,
    agent_skills_enabled: false,
    agent_cluster_fanout_enabled: false,
    agent_max_active_cases: 1,
    agent_max_fanout_targets: 1,
    agent_skill_max_per_turn: 1,
  },
  tool_catalog: { tools: [] },
  runtime_policy_schema: { properties: {} },
};

describe("RuntimeConsole", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getAgentRuntimeConfig.mockResolvedValue(runtime);
    api.getAIConfig.mockResolvedValue({ has_api_key: false, enabled: "none", features: {} });
    api.healthz.mockResolvedValue({ version: "0.1.0" });
    api.listSystemControls.mockResolvedValue({ items: [] });
  });

  it("uses deployment-managed wording for an unavailable optional runtime", async () => {
    api.getAIConfig.mockRejectedValue(Object.assign(new Error("Not Found"), { status: 404 }));

    render(<MemoryRouter><RuntimeConsole /></MemoryRouter>);

    expect(await screen.findByText("AI 调查暂不可用")).toBeInTheDocument();
    expect(screen.getByText(/运行凭据由部署环境管理/)).toBeInTheDocument();
    expect(screen.queryByText(/AI Collector Runtime 未配置|Provider 是否配置|未配置/)).not.toBeInTheDocument();
    expect(screen.queryByText("Not Found")).not.toBeInTheDocument();
  });

  it("keeps an actionable runtime request failure", async () => {
    api.getAgentRuntimeConfig.mockRejectedValue(Object.assign(new Error("Runtime gateway timeout"), { status: 503 }));

    render(<MemoryRouter><RuntimeConsole /></MemoryRouter>);

    expect(await screen.findByText("Runtime gateway timeout")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重\s*试/ })).toBeInTheDocument();
  });
});
