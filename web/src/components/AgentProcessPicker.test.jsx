import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AgentProcessPicker from "./AgentProcessPicker";
import { scanAgentProcesses } from "../api/client";

vi.mock("../api/client", () => ({
  scanAgentProcesses: vi.fn(),
}));

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const baseProps = {
  keyword: "mysqld",
  onKeywordChange: vi.fn(),
  value: null,
  onChange: vi.fn(),
};

describe("AgentProcessPicker", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("scans only the explicit Worker and binds its sole candidate PID", async () => {
    const onChange = vi.fn();
    scanAgentProcesses.mockResolvedValue({
      processes: [{ pid: 2202, comm: "mysqld", cmdline: "mysqld --defaults-file=/etc/my.cnf" }],
    });

    render(
      <AgentProcessPicker
        {...baseProps}
        agentId="worker-b"
        agentLabel="数据库节点 B"
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "在 Worker 上查找" }));

    await waitFor(() => expect(scanAgentProcesses).toHaveBeenCalledWith("worker-b", {
      query: "mysqld",
      timeoutSec: 15,
    }));
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(2202));
    expect(screen.getByText("确认该 Worker 上的目标进程")).toBeInTheDocument();
  });

  it("ignores a stale scan response after the selected Worker changes", async () => {
    const pendingScan = deferred();
    const onChange = vi.fn();
    scanAgentProcesses.mockReturnValueOnce(pendingScan.promise);

    const view = render(
      <AgentProcessPicker
        {...baseProps}
        agentId="worker-a"
        agentLabel="节点 A"
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "在 Worker 上查找" }));
    expect(scanAgentProcesses).toHaveBeenCalledWith("worker-a", {
      query: "mysqld",
      timeoutSec: 15,
    });

    view.rerender(
      <AgentProcessPicker
        {...baseProps}
        agentId="worker-b"
        agentLabel="节点 B"
        onChange={onChange}
      />,
    );

    await act(async () => {
      pendingScan.resolve({ processes: [{ pid: 1101, comm: "mysqld" }] });
      await pendingScan.promise;
    });

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.queryByText("确认该 Worker 上的目标进程")).not.toBeInTheDocument();
    expect(screen.getByText(/扫描只读取 节点 B 的进程列表/)).toBeInTheDocument();
  });
});
