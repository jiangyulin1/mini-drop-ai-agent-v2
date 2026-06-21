import { useMemo } from "react";
import { Empty, Skeleton, Space, Tag, Typography } from "antd";
import ReactECharts from "echarts-for-react";
import { COLORS } from "../theme";

/**
 * 解析 "\[start, end)" 格式的 histogram key，返回数值区间中点。
 */
function parseRangeMidpoint(label) {
  const match = label.match(/\[(\d+)\s*,\s*(\d+)\)/);
  if (!match) return { min: 0, max: 0, mid: 0 };
  const min = parseInt(match[1], 10);
  const max = parseInt(match[2], 10);
  return { min, max, mid: (min + max) / 2 };
}

/**
 * eBPF IO 延迟分布 Histogram 图表。
 *
 * 数据格式: { io_latency_us: { "\[0, 4)": 1234, "\[4, 8)": 567, ... }, ... }
 * 将 bpftrace 输出的区间计数渲染为 ECharts 柱状图 + 累计分布表。
 *
 * @param {{ data: object | null, loading?: boolean, height?: number }} props
 */
export default function EBPFHistogram({ data, loading = false, height = 340 }) {
  const histogram = data?.io_latency_us || {};

  const chartData = useMemo(() => {
    const entries = Object.entries(histogram || {});
    if (entries.length === 0) return null;

    // 按区间中点排序
    entries.sort(
      (a, b) => parseRangeMidpoint(a[0]).mid - parseRangeMidpoint(b[0]).mid
    );

    const labels = entries.map(([key]) => key);
    const values = entries.map(([, v]) => v);
    const total = values.reduce((s, v) => s + v, 0);

    return { labels, values, total, entries };
  }, [histogram]);

  const option = useMemo(() => {
    if (!chartData) return null;
    const { labels, values } = chartData;

    return {
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        backgroundColor: "rgba(255,255,255,0.96)",
        borderColor: COLORS.border,
        borderWidth: 1,
        textStyle: { color: COLORS.textPrimary, fontSize: 12 },
        formatter(params) {
          const item = params[0];
          if (!item) return "";
          const idx = item.dataIndex;
          const label = labels[idx] || "";
          const count = values[idx] || 0;
          const pct =
            chartData.total > 0
              ? ((count / chartData.total) * 100).toFixed(1)
              : "0.0";
          return `
            <div style="font-family:monospace;font-size:12px;line-height:1.5">
              <strong>IO 延迟区间: ${label} μs</strong><br/>
              <span style="color:#888">请求数:</span> ${count.toLocaleString()}<br/>
              <span style="color:#888">占比:</span> ${pct}%
            </div>
          `;
        },
      },
      grid: { left: 8, right: 20, top: 12, bottom: 40, containLabel: true },
      xAxis: {
        type: "category",
        data: labels,
        axisLabel: {
          rotate: 45,
          fontSize: 10,
          color: COLORS.textSecondary,
          formatter: (v) => v.replace(/\s/g, ""),
        },
        name: "延迟区间 (μs)",
        nameTextStyle: { color: COLORS.textSecondary, fontSize: 11 },
        nameLocation: "middle",
        nameGap: 28,
      },
      yAxis: {
        type: "value",
        name: "请求数",
        nameTextStyle: { color: COLORS.textSecondary, fontSize: 11 },
        axisLabel: { fontSize: 11, formatter: (v) => v >= 1000 ? `${(v / 1000).toFixed(1)}k` : v },
        splitLine: { lineStyle: { color: COLORS.borderSecondary } },
      },
      series: [
        {
          type: "bar",
          data: values.map((v) => ({
            value: v,
            itemStyle: {
              // 低延迟 = 绿色，高延迟 = 红色
              color: new Array(values.length)
                .fill(null)
                .map((_, i) => {
                  const ratio = values.length > 1 ? i / (values.length - 1) : 0;
                  const r = Math.round(82 + ratio * 173);
                  const g = Math.round(196 - ratio * 176);
                  const b = Math.round(26 + ratio * 0);
                  return `rgb(${r},${g},${b})`;
                })[i],
              borderRadius: [0, 0, 0, 0],
            },
          })),
          barMaxWidth: 36,
        },
      ],
      animationDuration: 500,
      animationEasing: "cubicOut",
    };
  }, [chartData]);

  const summaryStats = useMemo(() => {
    if (!chartData) return null;
    const buckets = chartData.entries.map(([label, count]) => ({
      label,
      count,
      ...parseRangeMidpoint(label),
    }));
    const total = chartData.total;
    if (total === 0) return null;

    // P50 / P95 / P99 估算
    let cumulative = 0;
    let p50 = null, p95 = null, p99 = null, maxBucket = buckets[buckets.length - 1];
    for (const b of buckets) {
      cumulative += b.count;
      const pct = cumulative / total;
      if (p50 === null && pct >= 0.5) p50 = b.max;
      if (p95 === null && pct >= 0.95) p95 = b.max;
      if (p99 === null && pct >= 0.99) p99 = b.max;
    }
    return { total, p50, p95, p99, maxBucket };
  }, [chartData]);

  if (loading) {
    return <Skeleton.Input active block style={{ height, borderRadius: 8 }} />;
  }

  if (!chartData || chartData.total === 0) {
    return <Empty description="暂无 eBPF IO 延迟数据" />;
  }

  return (
    <div style={{ width: "100%" }}>
      {summaryStats && (
        <Space style={{ marginBottom: 8 }} wrap>
          <Typography.Text strong style={{ fontSize: 12 }}>
            IO 延迟统计:
          </Typography.Text>
          <Tag color="default">样本数 {summaryStats.total.toLocaleString()}</Tag>
          <Tag color="green">P50 ≤ {summaryStats.p50} μs</Tag>
          <Tag color="orange">P95 ≤ {summaryStats.p95} μs</Tag>
          <Tag color="red">P99 ≤ {summaryStats.p99} μs</Tag>
          {summaryStats.maxBucket && (
            <Tag color="volcano">最大区间: {summaryStats.maxBucket.label} μs</Tag>
          )}
        </Space>
      )}
      <ReactECharts
        option={option}
        style={{ height, width: "100%" }}
        notMerge
        lazyUpdate
        opts={{ renderer: "canvas" }}
      />
      <Typography.Text
        type="secondary"
        style={{ fontSize: 10, display: "block", textAlign: "center", marginTop: 4 }}
      >
        延迟越高颜色越红 — 绿色=正常 &nbsp;|&nbsp; 橙色=偏高 &nbsp;|&nbsp; 红色=异常抖动
      </Typography.Text>
    </div>
  );
}
