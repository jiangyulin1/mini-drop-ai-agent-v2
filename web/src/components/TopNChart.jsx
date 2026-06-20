import { useMemo } from "react";
import { Empty, Skeleton } from "antd";
import ReactECharts from "echarts-for-react";
import { COLORS } from "../theme";
import { escapeHtml } from "../utils/html";

/**
 * 热力渐变色：低占比冷色 → 高占比暖色。
 */
function heatColor(percent, maxPct) {
  if (maxPct <= 0) return COLORS.primary;
  const ratio = Math.min(percent / maxPct, 1);
  // 从浅蓝渐变到深红
  const r = Math.round(22 + ratio * 233);
  const g = Math.round(119 - ratio * 70);
  const b = Math.round(255 - ratio * 190);
  return `rgb(${r},${g},${b})`;
}

/**
 * TopN 热点函数横向柱状图。
 *
 * 使用 ECharts 渲染交互式柱状图：
 * - 点击柱状图触发 onBarClick 联动火焰图搜索
 * - hover 显示函数详情（名称、样本数、占比）
 * - 颜色按占比热力渐变
 *
 * @param {{
 *   data: Array<{name: string, samples: number, percent: number}>,
 *   loading?: boolean,
 *   onBarClick?: (funcName: string) => void,
 *   height?: number,
 * }} props
 */
export default function TopNChart({
  data,
  loading = false,
  onBarClick,
  height = 420,
}) {
  const names = useMemo(() => (data || []).map((d) => d.name).reverse(), [data]);
  const values = useMemo(() => (data || []).map((d) => d.percent).reverse(), [data]);
  const samples = useMemo(() => (data || []).map((d) => d.samples).reverse(), [data]);
  const maxPct = useMemo(() => Math.max(...values, 1), [values]);

  const option = useMemo(() => {
    if (!data || data.length === 0) return null;

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
          const name = names[idx] || "";
          const pct = values[idx] || 0;
          const cnt = samples[idx] || 0;
          return `
            <div style="font-family:monospace;max-width:360px;word-break:break-all">
              <strong>${escapeHtml(name)}</strong><br/>
              <span style="color:#888">样本数:</span> ${cnt.toLocaleString()}<br/>
              <span style="color:#888">占比:</span> ${pct}%
            </div>
          `;
        },
      },
      grid: {
        left: 4,
        right: 16,
        top: 8,
        bottom: 4,
        containLabel: true,
      },
      xAxis: {
        type: "value",
        name: "占比 (%)",
        nameTextStyle: { color: COLORS.textSecondary, fontSize: 11 },
        axisLabel: { formatter: "{value}%", fontSize: 11 },
        splitLine: { lineStyle: { color: COLORS.borderSecondary } },
      },
      yAxis: {
        type: "category",
        data: names,
        inverse: true,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: {
          fontSize: 11,
          fontFamily: "monospace",
          color: COLORS.textPrimary,
          width: 200,
          overflow: "truncate",
          ellipsis: "…",
        },
      },
      series: [
        {
          type: "bar",
          data: values.map((v, i) => ({
            value: v,
            itemStyle: {
              color: heatColor(v, maxPct),
              borderRadius: [0, 3, 3, 0],
            },
            emphasis: {
              itemStyle: {
                color: heatColor(v, maxPct),
                shadowBlur: 8,
                shadowColor: "rgba(0,0,0,0.15)",
              },
            },
          })),
          barMaxWidth: 28,
          label: {
            show: true,
            position: "right",
            formatter: "{c}%",
            fontSize: 10,
            color: COLORS.textSecondary,
          },
        },
      ],
      animationDuration: 600,
      animationEasing: "cubicOut",
    };
  }, [data, names, values, samples, maxPct]);

  const onEvents = useMemo(() => {
    if (!onBarClick) return {};
    return {
      click: (params) => {
        if (params.name && onBarClick) {
          onBarClick(params.name);
        }
      },
    };
  }, [onBarClick]);

  if (loading) {
    return <Skeleton.Input active block style={{ height, borderRadius: 8 }} />;
  }

  if (!data || data.length === 0) {
    return <Empty description="暂无 TopN 热点数据" />;
  }

  return (
    <div style={{ width: "100%" }}>
      <ReactECharts
        option={option}
        style={{ height, width: "100%" }}
        onEvents={onEvents}
        notMerge
        lazyUpdate
        opts={{ renderer: "canvas" }}
      />
    </div>
  );
}
