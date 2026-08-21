import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => {
  const requestUse = vi.fn();
  const responseUse = vi.fn();
  const instance = {
    interceptors: {
      request: { use: requestUse },
      response: { use: responseUse },
    },
    request: vi.fn(),
  };
  return {
    instance,
    post: vi.fn(),
    requestUse,
    responseUse,
  };
});

vi.mock("axios", () => ({
  default: {
    create: vi.fn(() => mocks.instance),
    get: vi.fn(),
    post: mocks.post,
  },
}));

await import("./client");

describe("automatic web session", () => {
  const storage = {
    getItem: vi.fn(),
    setItem: vi.fn(),
    removeItem: vi.fn(),
    clear: vi.fn(),
  };

  beforeEach(() => {
    mocks.post.mockReset();
    mocks.instance.request.mockReset();
    Object.defineProperty(window, "localStorage", { value: storage, configurable: true });
    Object.values(storage).forEach((method) => method.mockReset());
  });

  it("bootstraps once after a 401 and retries without storing a key", async () => {
    const rejectResponse = mocks.responseUse.mock.calls[0][1];
    mocks.post.mockResolvedValue({ data: { code: 0 } });
    mocks.instance.request.mockResolvedValue({ retried: true });
    const config = { url: "/tasks", method: "get" };

    const result = await rejectResponse({
      config,
      response: { status: 401, data: { detail: "无效 API Key" }, headers: {} },
      message: "Request failed",
    });

    expect(result).toEqual({ retried: true });
    expect(mocks.post).toHaveBeenCalledWith(
      "/api/auth/bootstrap",
      {},
      expect.objectContaining({ withCredentials: true }),
    );
    expect(mocks.instance.request).toHaveBeenCalledWith(
      expect.objectContaining({ _miniDropBootstrapAttempt: true }),
    );
    expect(storage.setItem).not.toHaveBeenCalled();
  });
});
