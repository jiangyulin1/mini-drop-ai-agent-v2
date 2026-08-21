import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import Settings from "./Settings";

const api = vi.hoisted(() => ({
  healthz: vi.fn(),
  getAIConfig: vi.fn(),
  getCurrentUser: vi.fn(),
  getStoredApiKey: vi.fn(),
  saveApiKey: vi.fn(),
}));

vi.mock("../api/client", () => api);
vi.mock("./AuditLogs", () => ({ default: () => <div>审计占位</div> }));
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

beforeEach(() => {
  api.healthz.mockResolvedValue({
    healthy: true,
    service: "mini-drop-server",
    checks: { database: { status: "ok" }, storage: { status: "ok" } },
  });
  api.getAIConfig.mockResolvedValue({ enabled: "none", features: {} });
  api.getCurrentUser.mockResolvedValue({ name: "operator" });
  api.getStoredApiKey.mockReturnValue("");
  api.saveApiKey.mockResolvedValue(undefined);
});

describe("Settings", () => {
  it("treats a missing AI configuration endpoint as a scoped disabled state", async () => {
    api.getAIConfig.mockRejectedValue(Object.assign(new Error("Not Found"), { status: 404 }));

    render(<Settings />);

    expect(await screen.findByText("AI 服务由部署环境管理")).toBeInTheDocument();
    expect(screen.queryByText("Not Found")).not.toBeInTheDocument();
    expect(screen.getByText("访问设置")).toBeInTheDocument();
    expect(screen.getByText("访问正常")).toBeInTheDocument();
    expect(api.getCurrentUser).toHaveBeenCalledTimes(1);
  });

  it("keeps a useful request error while masking credential details", async () => {
    api.getAIConfig.mockRejectedValue(Object.assign(new Error("upstream timeout"), { status: 503 }));

    render(<Settings />);

    expect(await screen.findByText("upstream timeout")).toBeInTheDocument();

    cleanup();
    api.getAIConfig.mockRejectedValue(Object.assign(new Error("API Key missing"), { status: 500 }));
    render(<Settings />);

    expect(await screen.findByText("AI 服务请求失败（HTTP 500），请刷新重试或联系部署管理员。")).toBeInTheDocument();
    expect(screen.queryByText("API Key missing")).not.toBeInTheDocument();
  });

  it("saves a candidate API key and refreshes the authentication state", async () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent");
    render(<Settings />);

    const input = await screen.findByPlaceholderText("输入访问凭据（留空清除）");
    fireEvent.change(input, { target: { value: "  candidate-key  " } });
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => {
      expect(api.saveApiKey).toHaveBeenCalledWith("candidate-key");
      expect(api.getCurrentUser).toHaveBeenCalledTimes(2);
    });
    expect(dispatchSpy).toHaveBeenCalledWith(
      expect.objectContaining({ type: "mini-drop:auth-changed" }),
    );
    await waitFor(() => {
      expect(screen.getByPlaceholderText("输入访问凭据（留空清除）")).toHaveValue("");
    });
    dispatchSpy.mockRestore();
  });

  it("clears the active browser credential", async () => {
    render(<Settings />);

    await screen.findByText("访问正常");
    fireEvent.click(screen.getByRole("button", { name: "清除浏览器认证" }));

    await waitFor(() => {
      expect(api.saveApiKey).toHaveBeenCalledWith("");
      expect(api.getCurrentUser).toHaveBeenCalledTimes(2);
    });
  });
});
