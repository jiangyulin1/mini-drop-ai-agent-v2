import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import Settings from "./Settings";

const api = vi.hoisted(() => ({
  healthz: vi.fn(),
  getAIConfig: vi.fn(),
  getCurrentUser: vi.fn(),
  getStoredApiKey: vi.fn(() => ""),
  saveApiKey: vi.fn(),
}));

vi.mock("../api/client", () => api);
vi.mock("./AuditLogs", () => ({ default: () => <div>审计占位</div> }));
vi.mock("./DiagnosisHistory", () => ({ default: () => <div>历史占位</div> }));
vi.mock("../components/StorageMaintenance", () => ({ default: () => <div>存储维护占位</div> }));

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("Settings", () => {
  it("treats a missing AI configuration endpoint as a scoped disabled state", async () => {
    api.healthz.mockResolvedValue({
      healthy: true,
      service: "mini-drop-server",
      checks: { database: { status: "ok" }, storage: { status: "ok" } },
    });
    api.getCurrentUser.mockResolvedValue({ name: "operator" });
    api.getAIConfig.mockRejectedValue(Object.assign(new Error("Not Found"), { status: 404 }));

    render(<Settings />);

    expect(await screen.findByText("当前服务未启用 AI Provider")).toBeInTheDocument();
    expect(screen.queryByText("Not Found")).not.toBeInTheDocument();
    expect(screen.getByText("访问正常")).toBeInTheDocument();
  });
});
