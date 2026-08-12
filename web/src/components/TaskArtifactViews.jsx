import { useEffect, useRef, useState } from "react";
import { Empty, Skeleton, Space, Tag } from "antd";
import { getTaskArtifactContent } from "../api/client";
import echarts from "../lib/echarts";
import { prepareAsyncProfilerHtml } from "../utils/artifacts";
import EBPFHistogram from "./EBPFHistogram";
import SandboxedArtifactFrame from "./SandboxedArtifactFrame";

function useArtifactContent(taskId, artifactType, fallback = null) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getTaskArtifactContent(taskId, artifactType)
      .then((content) => {
        if (!cancelled) setData(content || fallback);
      })
      .catch(() => {
        // Artifact metadata is persisted with the task and is often enough to
        // render summary views even when the object-store content endpoint is
        // temporarily unavailable.
        if (!cancelled) setData(fallback);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [artifactType, fallback, taskId]);

  return { data, loading };
}

export function JavaFlameViewer({ taskId }) {
  const { data, loading } = useArtifactContent(taskId, "java_flamegraph_html");
  const html = data ? prepareAsyncProfilerHtml(data) : "";

  if (loading) return <Skeleton.Input active block style={{ height: 400, borderRadius: 8 }} />;
  if (!html) return <Empty description="无法加载 Java 火焰图" image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  return (
    <SandboxedArtifactFrame
      html={html}
      title="Java 火焰图"
      style={{ width: "100%", height: 420, border: "none", borderRadius: 6 }}
    />
  );
}

function useEChart(data, configure) {
  const chartRef = useRef(null);

  useEffect(() => {
    if (!data || !chartRef.current) return undefined;
    const instance = echarts.init(chartRef.current);
    instance.setOption(configure(data));
    const resize = () => instance.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      instance.dispose();
    };
  }, [configure, data]);

  return chartRef;
}

const systemMetricsOptions = (data) => {
  const s = data.summary;
  return {
    title: { text: "System Metrics Dashboard", left: "center", textStyle: { fontSize: 13 } },
    tooltip: {},
    grid: [
      { left: "8%", top: "8%", width: "20%", height: "38%" },
      { left: "36%", top: "8%", width: "20%", height: "38%" },
      { left: "64%", top: "8%", width: "20%", height: "38%" },
      { left: "8%", top: "54%", width: "38%", height: "38%" },
      { left: "54%", top: "54%", width: "38%", height: "38%" },
    ],
    xAxis: [
      { gridIndex: 0, data: ["User", "Sys", "Iowait"], axisLabel: { fontSize: 10 } },
      { gridIndex: 1, data: ["1m", "5m", "15m"], axisLabel: { fontSize: 10 } },
      { gridIndex: 2, data: ["Threads", "FD"], axisLabel: { fontSize: 10 } },
      { gridIndex: 3, data: ["Rx KB/s", "Tx KB/s"], axisLabel: { fontSize: 10 } },
      { gridIndex: 4, data: ["RSS MB", "RSS Peak"], axisLabel: { fontSize: 10 } },
    ],
    yAxis: [
      { gridIndex: 0, name: "%", axisLabel: { fontSize: 9 } },
      { gridIndex: 1, axisLabel: { fontSize: 9 } },
      { gridIndex: 2, axisLabel: { fontSize: 9 } },
      { gridIndex: 3, axisLabel: { fontSize: 9 } },
      { gridIndex: 4, axisLabel: { fontSize: 9 } },
    ],
    series: [
      { type: "bar", xAxisIndex: 0, yAxisIndex: 0, data: [
        { value: s.avg_cpu_user_pct, itemStyle: { color: "#5470c6" } },
        { value: s.avg_cpu_sys_pct, itemStyle: { color: "#fac858" } },
        { value: s.avg_cpu_iowait_pct, itemStyle: { color: "#ee6666" } },
      ] },
      { type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: [
        { value: s.load1m || 0, itemStyle: { color: "#91cc75" } },
        { value: s.load5m || 0, itemStyle: { color: "#73c0de" } },
        { value: s.load15m || 0, itemStyle: { color: "#a0a7e6" } },
      ] },
      { type: "bar", xAxisIndex: 2, yAxisIndex: 2, data: [
        { value: s.thread_count, itemStyle: { color: s.thread_trend === "increasing" ? "#ee6666" : "#73c0de" } },
        { value: s.fd_count, itemStyle: { color: s.fd_trend === "increasing" ? "#ee6666" : "#fac858" } },
      ] },
      { type: "bar", xAxisIndex: 3, yAxisIndex: 3, data: [
        { value: s.net_rx_kbps, itemStyle: { color: "#5470c6" } },
        { value: s.net_tx_kbps, itemStyle: { color: "#91cc75" } },
      ] },
      { type: "bar", xAxisIndex: 4, yAxisIndex: 4, data: [
        { value: s.vmrss_mb, itemStyle: { color: "#fc8452" } },
        { value: s.vmrss_mb_max, itemStyle: { color: "#9a60b4" } },
      ] },
    ],
  };
};

export function SysMetricsView({ taskId, artifact }) {
  const fallback = artifact?.metadata || null;
  const { data, loading } = useArtifactContent(taskId, "sys_metrics", fallback);
  const chartRef = useEChart(data?.summary ? data : null, systemMetricsOptions);

  if (loading) return <Skeleton.Input active block style={{ height: 400, borderRadius: 8 }} />;
  if (!data?.summary) return <Empty description="无系统指标数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  const s = data.summary;
  const trends = {
    fd: { increasing: ["red", "FD ↑"], decreasing: ["green", "FD ↓"], stable: ["blue", "FD →"] },
    thread: { increasing: ["red", "线程 ↑"], decreasing: ["green", "线程 ↓"], stable: ["blue", "线程 →"] },
  };
  return (
    <div>
      <Space style={{ marginBottom: 8 }} wrap>
        <Tag>样本: {data.sample_count}</Tag><Tag>CPU sys: {s.avg_cpu_sys_pct}%</Tag>
        <Tag>iowait: {s.avg_cpu_iowait_pct}%</Tag>
        <Tag color={trends.thread[s.thread_trend]?.[0] || "default"}>{trends.thread[s.thread_trend]?.[1] || s.thread_trend}: {s.thread_count}</Tag>
        <Tag color={trends.fd[s.fd_trend]?.[0] || "default"}>{trends.fd[s.fd_trend]?.[1] || s.fd_trend}: {s.fd_count}</Tag>
        <Tag>ctx/s: {s.ctx_nonvoluntary_rate}/s</Tag>
      </Space>
      <div ref={chartRef} style={{ width: "100%", height: 420 }} />
    </div>
  );
}

const memoryOptions = (data) => {
  const times = data.samples.map((sample) => new Date(sample.ts * 1000).toLocaleTimeString());
  const series = [{ name: "RSS", type: "line", data: data.samples.map((sample) => sample.rss_mb ?? 0), smooth: true, areaStyle: { opacity: 0.15 } }];
  if (data.samples.some((sample) => sample.pss_mb != null)) series.push({ name: "PSS", type: "line", data: data.samples.map((sample) => sample.pss_mb ?? 0), smooth: true });
  if (data.samples.some((sample) => sample.swap_mb > 0)) series.push({ name: "Swap", type: "line", data: data.samples.map((sample) => sample.swap_mb ?? 0), smooth: true, lineStyle: { type: "dashed" } });
  return {
    tooltip: { trigger: "axis" }, legend: { data: series.map((item) => item.name), bottom: 0 },
    grid: { left: 50, right: 20, top: 20, bottom: 30 },
    xAxis: { type: "category", data: times, boundaryGap: false },
    yAxis: { type: "value", name: "MB" }, series,
  };
};

export function MemoryChart({ taskId, artifact }) {
  const fallback = artifact?.metadata || null;
  const { data, loading } = useArtifactContent(taskId, "memory_json", fallback);
  const chartRef = useEChart(data?.samples?.length ? data : null, memoryOptions);

  if (loading) return <Skeleton.Input active block style={{ height: 300, borderRadius: 8 }} />;
  if (!data?.samples?.length) return <Empty description="无内存数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  const trendTag = { increasing: ["red", "增长 ↑"], decreasing: ["green", "下降 ↓"], stable: ["blue", "稳定 →"] }[data.trend] || ["default", data.trend];
  return (
    <div>
      <Space style={{ marginBottom: 8 }}>
        <Tag>样本数: {data.sample_count}</Tag><Tag>RSS: {data.first_rss_mb} → {data.last_rss_mb} MB</Tag>
        <Tag>峰值: {data.peak_rss_mb} MB</Tag><Tag color={trendTag[0]}>趋势: {trendTag[1]}</Tag>
      </Space>
      <div ref={chartRef} style={{ width: "100%", height: 300 }} />
    </div>
  );
}

export function EBPFHistogramChart({ taskId }) {
  const { data, loading } = useArtifactContent(taskId, "ebpf_metrics");
  return <EBPFHistogram data={data} loading={loading} />;
}
