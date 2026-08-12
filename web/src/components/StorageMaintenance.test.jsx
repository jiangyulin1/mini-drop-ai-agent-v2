import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import StorageMaintenance from "./StorageMaintenance";

const api = vi.hoisted(() => ({
  dryRunAction: vi.fn(),
  executeAction: vi.fn(),
  rollbackAction: vi.fn(),
}));

vi.mock("../api/client", () => api);

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

describe("StorageMaintenance", () => {
  it("invalidates an approved dry-run when retention changes", async () => {
    api.dryRunAction.mockResolvedValue({
      attempt_id: "act-old",
      dry_run: {
        retention_days: 7,
        candidate_count: 1,
        total_bytes: 1024,
        items: [{ task_id: "task-1", size_bytes: 1024, age_days: 9, path: "/cache/task-1" }],
      },
    });

    render(<StorageMaintenance />);

    expect(screen.getByRole("button", { name: /批准并执行/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /恢复全局隔离区/ })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /查看可清理项/ }));
    await waitFor(() => expect(api.dryRunAction).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByRole("button", { name: /批准并执行/ })).toBeEnabled());
    expect(screen.getByText("task-1")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("spinbutton"), { target: { value: "30" } });

    expect(screen.getByRole("button", { name: /批准并执行/ })).toBeDisabled();
    expect(screen.queryByText("task-1")).not.toBeInTheDocument();
  });
});
