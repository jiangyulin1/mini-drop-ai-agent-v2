import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  EBPFHistogramChart,
  JavaFlameViewer,
  MemoryChart,
  SysMetricsView,
} from "./TaskArtifactViews";
import { getTaskArtifactContent } from "../api/client";
import echarts from "../lib/echarts";

vi.mock("../api/client", () => ({
  getTaskArtifactContent: vi.fn(),
}));

vi.mock("../lib/echarts", () => ({
  default: {
    init: vi.fn(),
  },
}));

vi.mock("./EBPFHistogram", () => ({
  default: ({ data, loading }) => (
    <div data-testid="ebpf-view">{loading ? "loading" : JSON.stringify(data)}</div>
  ),
}));

vi.mock("./SandboxedArtifactFrame", () => ({
  default: ({ html, title }) => <div title={title}>{html}</div>,
}));

describe("task artifact views", () => {
  let chart;

  beforeEach(() => {
    vi.clearAllMocks();
    chart = { setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() };
    echarts.init.mockReturnValue(chart);
  });

  it("loads and prepares an async-profiler HTML artifact", async () => {
    getTaskArtifactContent.mockResolvedValue({
      text: '<html><head></head><body><canvas id="canvas"></canvas></body></html>',
    });

    render(<JavaFlameViewer taskId="task-java" />);

    const frame = await screen.findByTitle("Java 火焰图");
    expect(frame.innerHTML).toContain("#canvas{width:100vw!important");
    expect(getTaskArtifactContent).toHaveBeenCalledWith(
      "task-java",
      "java_flamegraph_html",
    );
  });

  it("renders system metrics and disposes its chart", async () => {
    getTaskArtifactContent.mockResolvedValue({
      sample_count: 3,
      summary: {
        avg_cpu_user_pct: 11,
        avg_cpu_sys_pct: 22,
        avg_cpu_iowait_pct: 4,
        load1m: 1,
        load5m: 2,
        load15m: 3,
        thread_count: 8,
        thread_trend: "stable",
        fd_count: 12,
        fd_trend: "increasing",
        net_rx_kbps: 5,
        net_tx_kbps: 6,
        vmrss_mb: 100,
        vmrss_mb_max: 120,
        ctx_nonvoluntary_rate: 7,
      },
    });

    const { unmount } = render(
      <SysMetricsView taskId="task-sys" artifact={{ metadata: null }} />,
    );

    expect(await screen.findByText("CPU sys: 22%")).toBeInTheDocument();
    await waitFor(() => expect(chart.setOption).toHaveBeenCalledOnce());
    expect(chart.setOption.mock.calls[0][0].series).toHaveLength(5);

    unmount();
    expect(chart.dispose).toHaveBeenCalledOnce();
  });

  it("falls back to persisted memory metadata when content loading fails", async () => {
    getTaskArtifactContent.mockRejectedValue(new Error("object store unavailable"));
    const metadata = {
      sample_count: 2,
      first_rss_mb: 10,
      last_rss_mb: 20,
      peak_rss_mb: 24,
      trend: "increasing",
      samples: [
        { ts: 1, rss_mb: 10, pss_mb: 8, swap_mb: 0 },
        { ts: 2, rss_mb: 20, pss_mb: 16, swap_mb: 1 },
      ],
    };

    render(<MemoryChart taskId="task-memory" artifact={{ metadata }} />);

    expect(await screen.findByText("RSS: 10 → 20 MB")).toBeInTheDocument();
    await waitFor(() => expect(chart.setOption).toHaveBeenCalledOnce());
    expect(chart.setOption.mock.calls[0][0].legend.data).toEqual(["RSS", "PSS", "Swap"]);
  });

  it("passes eBPF content and loading state to the histogram", async () => {
    getTaskArtifactContent.mockResolvedValue({ io_latency_us: { "[8,16)": 4 } });

    render(<EBPFHistogramChart taskId="task-ebpf" />);

    expect(screen.getByTestId("ebpf-view")).toHaveTextContent("loading");
    await waitFor(() => {
      expect(screen.getByTestId("ebpf-view")).toHaveTextContent('"[8,16)":4');
    });
    expect(getTaskArtifactContent).toHaveBeenCalledWith("task-ebpf", "ebpf_metrics");
  });
});
