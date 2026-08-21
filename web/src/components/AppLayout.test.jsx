import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AppLayout from "./AppLayout";
import {
  getAgentRuntimeConfig,
  getCurrentUser,
  healthz,
  listAgents,
  listIncidentCases,
  listSystemControls,
} from "../api/client";

vi.mock("../api/client", () => ({
  getAgentRuntimeConfig: vi.fn(),
  getCurrentUser: vi.fn(),
  healthz: vi.fn(),
  listAgents: vi.fn(),
  listIncidentCases: vi.fn(),
  listSystemControls: vi.fn(),
}));
vi.mock("../hooks/useSSE", () => ({
  default: () => ({ connected: true, connectionState: "connected", reconnect: vi.fn() }),
}));

function renderLayout() {
  render(
    <MemoryRouter>
      <Routes>
        <Route element={<AppLayout />}>
          <Route path="*" element={<div>页面内容</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe("AppLayout navigation", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.matchMedia = vi.fn().mockImplementation((query) => ({
      matches: query.includes("min-width"),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    healthz.mockResolvedValue({ healthy: true });
    getAgentRuntimeConfig.mockResolvedValue({ ai_ready: true, mode: "pi" });
    getCurrentUser.mockResolvedValue({ username: "operator", role: "admin" });
    listAgents.mockResolvedValue([{ id: "worker-1", status: "ONLINE" }]);
    listIncidentCases.mockResolvedValue({ items: [] });
    listSystemControls.mockResolvedValue({ items: [] });
  });

  it("keeps daily work in the primary navigation and management in one menu", async () => {
    renderLayout();

    const navigation = screen.getByRole("navigation", { name: "主导航" });
    expect(within(navigation).getAllByRole("menuitem").map((item) => item.textContent)).toEqual([
      "总览",
      "AI 调查",
      "任务与证据",
      "节点",
    ]);
    expect(screen.queryByText("系统说明")).not.toBeInTheDocument();
    expect(screen.queryByText("旧诊断记录")).not.toBeInTheDocument();
    expect(screen.queryByText("审计与安全")).not.toBeInTheDocument();

    expect(await screen.findByText("operator")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开管理菜单" }));

    await waitFor(() => {
      expect(screen.getByRole("menuitem", { name: /运行配置/ })).toBeInTheDocument();
      expect(screen.getByRole("menuitem", { name: /操作记录/ })).toBeInTheDocument();
      expect(screen.getByRole("menuitem", { name: /访问与存储/ })).toBeInTheDocument();
    });
  });
});
