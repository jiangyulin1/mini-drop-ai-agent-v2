import { useEffect, useRef, useState, useCallback, useImperativeHandle, forwardRef } from "react";
import { Alert, Button, Empty, Input, Skeleton, Space, Tag, Tooltip } from "antd";
import {
  SearchOutlined,
  ReloadOutlined,
  ZoomInOutlined,
  ZoomOutOutlined,
  ExpandOutlined,
} from "@ant-design/icons";
import * as d3 from "d3";
import { flamegraph } from "d3-flame-graph";
import "d3-flame-graph/dist/d3-flamegraph.css";
import { getTaskArtifactContent } from "../api/client";
import { escapeHtml } from "../utils/html";
import { COLORS, FLAMEGRAPH as FG } from "../theme";

/**
 * 为函数名生成稳定的颜色（基于名称哈希的 HSL 色相）。
 */
function nameColor(name) {
  if (!name || name === "root") return COLORS.textSecondary;
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  const hue = ((hash % 37) + 0) * (360 / 37); // 37 种色相均匀分布
  return d3.hsl(hue, 0.65, 0.6).toString();
}

/**
 * 生成 Tooltip HTML。
 */
function tooltipContent(d) {
  const name = d.data.name || "(unknown)";
  const value = d.data.value || 0;
  const pct = d.data.value && d.parent?.data?.value
    ? ((d.data.value / d.parent.data.value) * 100).toFixed(1)
    : (d.data.value ? ((d.data.value / d.root?.data?.value) * 100).toFixed(1) : "0.0");
  return `
    <div style="font-family:monospace;font-size:12px;line-height:1.6;max-width:420px;word-break:break-all">
      <strong>${escapeHtml(name)}</strong><br/>
      <span style="color:#888">样本数:</span> ${value.toLocaleString()}<br/>
      <span style="color:#888">占比:</span> ${pct}%
    </div>
  `;
}

function hasRenderableFlamegraph(tree) {
  if (!tree || !tree.name) return false;
  const value = Number(tree.value || 0);
  const children = Array.isArray(tree.children) ? tree.children : [];
  return value > 0 || children.length > 0;
}

function normalizeFlamegraphPayload(payload) {
  let value = payload;
  for (let i = 0; i < 3; i += 1) {
    if (typeof value === "string") {
      try {
        value = JSON.parse(value);
      } catch {
        return payload;
      }
      continue;
    }
    if (value && typeof value === "object" && value.code === 0 && value.data !== undefined) {
      value = value.data;
      continue;
    }
    break;
  }
  return value;
}

/**
 * 交互式火焰图查看器。
 *
 * 使用 d3-flame-graph 库渲染可交互火焰图：
 * - 点击放大某一帧
 * - hover 显示函数名、样本数、占比
 * - 搜索高亮
 * - 重置缩放
 *
 * @param {{ taskId: string, artifactType?: string, artifactIndex?: number }} props
 * @param {React.Ref} ref — 暴露 search(text) 方法供 TopNChart 联动
 */
const FlamegraphViewer = forwardRef(function FlamegraphViewer({
  taskId,
  artifactType = "flamegraph_json",
  artifactIndex = null,
}, ref) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const dataRef = useRef(null);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [hasData, setHasData] = useState(false);
  const [searchText, setSearchText] = useState("");

  // ── 向外暴露方法 ───────────────────────────────────────
  useImperativeHandle(ref, () => ({
    /**
     * 在火焰图中搜索并高亮匹配的函数帧。
     */
    search(text) {
      setSearchText(text || "");
      if (chartRef.current) {
        chartRef.current.search(text || "");
      }
    },
    /**
     * 重置火焰图缩放。
     */
    resetZoom() {
      if (chartRef.current) {
        chartRef.current.resetZoom();
        setSearchText("");
      }
    },
  }));

  // ── 加载数据 ───────────────────────────────────────────
  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = artifactIndex === null || artifactIndex === undefined ? {} : { index: artifactIndex };
      const tree = normalizeFlamegraphPayload(await getTaskArtifactContent(taskId, artifactType, params));
      if (hasRenderableFlamegraph(tree)) {
        dataRef.current = tree;
        setHasData(true);
      } else {
        dataRef.current = null;
        setHasData(false);
      }
    } catch (err) {
      setError(err.message || "无法加载火焰图数据");
      setHasData(false);
    } finally {
      setLoading(false);
    }
  }, [taskId, artifactType, artifactIndex]);

  useEffect(() => {
    load();
  }, [load]);

  // ── 渲染火焰图 ─────────────────────────────────────────
  const renderChart = useCallback(() => {
    if (!containerRef.current || !dataRef.current) return;

    // 清理旧图表
    if (chartRef.current) {
      d3.select(containerRef.current).selectAll("svg").remove();
    }

    const width = containerRef.current.clientWidth || 960;

    const chart = flamegraph()
      .width(width)
      .height(FG.defaultHeight)
      .cellHeight(FG.cellHeight)
      .transitionDuration(FG.transitionDuration)
      .transitionEase(d3.easeCubicOut)
      .sort(true)
      .title("")
      .selfValue(false)
      .inverted(false)
      .minFrameSize(1)
      .color((d) => nameColor(d.data.name))
      .tooltip((d) => tooltipContent(d));

    const selection = d3.select(containerRef.current)
      .datum(dataRef.current)
      .call(chart);

    chartRef.current = chart;

    // 如果已有搜索文本，立即应用
    if (searchText) {
      chart.search(searchText);
    }
  }, [searchText]);

  useEffect(() => {
    if (hasData) {
      // 延迟一帧确保 DOM 就绪
      const timer = requestAnimationFrame(renderChart);
      return () => cancelAnimationFrame(timer);
    }
  }, [hasData, renderChart]);

  // ── 响应式：窗口大小变化时重建图表 ──────────────────────
  useEffect(() => {
    if (!hasData) return;

    let timeoutId = null;
    const handleResize = () => {
      clearTimeout(timeoutId);
      timeoutId = setTimeout(() => {
        if (containerRef.current && dataRef.current) {
          // 重建以匹配新宽度
          d3.select(containerRef.current).selectAll("svg").remove();
          const width = containerRef.current.clientWidth || 960;
          const chart = flamegraph()
            .width(width)
            .height(FG.defaultHeight)
            .cellHeight(FG.cellHeight)
            .transitionDuration(FG.transitionDuration)
            .sort(true)
            .selfValue(false)
            .minFrameSize(1)
            .color((d) => nameColor(d.data.name))
            .tooltip((d) => tooltipContent(d));

          d3.select(containerRef.current)
            .datum(dataRef.current)
            .call(chart);

          chartRef.current = chart;
          if (searchText) chart.search(searchText);
        }
      }, 200);
    };

    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
      clearTimeout(timeoutId);
    };
  }, [hasData, searchText]);

  // ── 清理 ───────────────────────────────────────────────
  useEffect(() => {
    return () => {
      if (containerRef.current) {
        d3.select(containerRef.current).selectAll("svg").remove();
      }
    };
  }, []);

  // ── Render ─────────────────────────────────────────────
  if (loading) {
    return <Skeleton.Input active block style={{ height: FG.defaultHeight, borderRadius: 8 }} />;
  }

  if (error) {
    return (
      <Alert
        type="warning"
        message="火焰图数据加载失败"
        description={error}
        showIcon
        action={
          <Button size="small" onClick={load}>
            重试
          </Button>
        }
      />
    );
  }

  if (!hasData) {
    return <Empty description="火焰图采样为空，未发现可渲染的热点调用栈" />;
  }

  return (
    <div style={{ width: "100%" }}>
      {/* 工具栏 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: 8,
          marginBottom: 12,
          padding: "8px 12px",
          background: COLORS.background,
          borderRadius: 6,
          border: `1px solid ${COLORS.border}`,
        }}
      >
        <Space size="small" wrap>
          <Input.Search
            placeholder="搜索函数名…"
            allowClear
            size="small"
            style={{ width: 200 }}
            value={searchText}
            onChange={(e) => {
              const v = e.target.value;
              setSearchText(v);
              if (chartRef.current) {
                chartRef.current.search(v || "");
              }
            }}
            onSearch={(v) => {
              if (chartRef.current) chartRef.current.search(v || "");
            }}
            prefix={<SearchOutlined style={{ color: COLORS.textSecondary }} />}
          />
          <Tooltip title="搜索火焰图中的函数帧">
            <Tag style={{ margin: 0, cursor: "default" }}>搜索高亮</Tag>
          </Tooltip>
        </Space>
        <Space size="small">
          <Tooltip title="重置缩放">
            <Button
              size="small"
              icon={<ExpandOutlined />}
              onClick={() => {
                if (chartRef.current) {
                  chartRef.current.resetZoom();
                  setSearchText("");
                }
              }}
            >
              重置
            </Button>
          </Tooltip>
          <Tooltip title="重新加载数据">
            <Button size="small" icon={<ReloadOutlined />} onClick={load}>
              刷新
            </Button>
          </Tooltip>
        </Space>
      </div>

      {/* 火焰图容器 */}
      <div
        ref={containerRef}
        style={{
          width: "100%",
          minHeight: FG.defaultHeight,
          border: `1px solid ${COLORS.border}`,
          borderRadius: 6,
          overflow: "hidden",
          background: COLORS.cardBackground,
        }}
      />

      {/* 操作提示 */}
      <div
        style={{
          marginTop: 8,
          display: "flex",
          alignItems: "center",
          gap: 16,
          flexWrap: "wrap",
        }}
      >
        <Tag icon={<ZoomInOutlined />} color="blue">
          点击帧放大
        </Tag>
        <Tag icon={<ZoomOutOutlined />} color="green">
          右键返回上层
        </Tag>
        <Tag color="default">hover 查看详情</Tag>
      </div>
    </div>
  );
});

export default FlamegraphViewer;
