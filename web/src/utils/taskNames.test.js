import { describe, expect, it } from "vitest";
import { isUnreadableTaskName, taskDisplayInfo, taskDisplayName } from "./taskNames";

describe("task display names", () => {
  it("maps release smoke tasks to a readable Chinese name", () => {
    expect(taskDisplayName({
      name: "release baseline smoke linux-worker-2",
      agent_id: "linux-worker-2",
      target_pid: 1,
      collector_type: "sys_metrics",
    })).toBe("发布基线检查 · linux-worker-2");
  });

  it("infers an eBPF task name when the stored text is corrupted", () => {
    expect(taskDisplayName({
      name: "eBPF tracepoint ????",
      agent_id: "linux-worker-2",
      target_pid: 17278,
      collector_type: "ebpf_io",
    })).toBe("I/O 延迟采集 · PID 17278");
  });

  it("infers a Go profile task name when the stored text is corrupted", () => {
    expect(taskDisplayName({
      name: "????? pprof ????",
      agent_id: "linux-worker-1",
      target_pid: 7827,
      collector_type: "go_pprof",
    })).toBe("Go CPU 剖析 · PID 7827");
  });

  it("preserves a readable user supplied name", () => {
    const result = taskDisplayInfo({
      name: "订单服务 CPU 峰值排查",
      collector_type: "perf_cpu",
    });
    expect(result.displayName).toBe("订单服务 CPU 峰值排查");
    expect(result.normalized).toBe(false);
  });

  it("detects replacement markers", () => {
    expect(isUnreadableTaskName("VM???? ebpf")).toBe(true);
    expect(isUnreadableTaskName("正常任务")).toBe(false);
  });
});
