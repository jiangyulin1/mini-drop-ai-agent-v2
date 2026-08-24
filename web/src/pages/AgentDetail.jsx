import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import {
  Button,
  Card,
  Descriptions,
  Empty,
  Input,
  Skeleton,
  Table,
  Tag,
  Typography,
} from "antd";
import {
  ArrowLeftOutlined,
  CloudServerOutlined,
  ReloadOutlined,
  ApiOutlined,
  HddOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import { Link, useParams, useNavigate } from "react-router-dom";
import { listAgents, listTasks } from "../api/client";
import StatusTag from "../components/StatusTag";
import ErrorAlert from "../components/ErrorAlert";
import { COLORS } from "../theme";
import usePolling from "../hooks/usePolling";
import echarts from "../lib/echarts";
import { taskDisplayName } from "../utils/taskNames";
import styles from "./AgentDetail.module.css";
import { formatBeijingDateTime, formatBeijingTime } from "../utils/time";

function reported(value) {
  return value === null || value === undefined || value === "" ? "未上报" : value;
}

function metric(value, digits = 0) {
  const number = Number(value ?? 0);
  return Number.isFinite(number) ? number.toFixed(digits) : Number(0).toFixed(digits);
}

export default function AgentDetail() {
  const { agentId } = useParams();
  const navigate = useNavigate();

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [agent, setAgent] = useState(null);
  const [agentTasks, setAgentTasks] = useState([]);
  const [taskSearch, setTaskSearch] = useState("");
  const [cpuHistory, setCpuHistory] = useState([]);
  const [rssHistory, setRssHistory] = useState([]);
  const chartRef = useRef(null);
  const chartInst = useRef(null);
  const hasLoaded = useRef(false);

  const load = useCallback(async () => {
    setError("");
    if (hasLoaded.current) setRefreshing(true);
    try {
      const agents = (await listAgents()) || [];
      const found = agents.find((a) => a.id === agentId);
      setAgent(found || null);

      const tasks = await listTasks();
      const mine = (tasks || [])
        .filter((t) => t.agent_id === agentId)
        .sort(
          (a, b) =>
            new Date(b.created_at || 0).getTime() -
            new Date(a.created_at || 0).getTime()
        );
      setAgentTasks(mine);

      // 构建最近的指标历史（从 agent 的 latest_metrics 累计）
      if (found?.latest_metrics?.self) {
        const now = Date.now();
        const m = found.latest_metrics.self;
        setCpuHistory((prev) => {
          const next = [...prev, { ts: now, value: m.cpu_percent || 0 }];
          return next.slice(-60); // 最多保留 60 个点
        });
        setRssHistory((prev) => {
          const next = [...prev, { ts: now, value: m.rss_mb || 0 }];
          return next.slice(-60);
        });
      }
    } catch (err) {
      setError(err);
    } finally {
      hasLoaded.current = true;
      setLoading(false);
      setRefreshing(false);
    }
  }, [agentId]);

  useEffect(() => {
    hasLoaded.current = false;
    setLoading(true);
    setAgent(null);
    setAgentTasks([]);
    setCpuHistory([]);
    setRssHistory([]);
    void load();
  }, [load]);

  useEffect(() => {
    const refresh = () => { void load(); };
    window.addEventListener("mini-drop:refresh", refresh);
    return () => window.removeEventListener("mini-drop:refresh", refresh);
  }, [load]);

  // 每 10 秒自动刷新
  usePolling(load, { interval: 10000, enabled: agent?.status === "ONLINE" });

  // ── 渲染 ECharts 指标折线图 ──────────────────────────
  useEffect(() => {
    if (!chartRef.current || cpuHistory.length < 2) return;

    // 先销毁旧实例
    if (chartInst.current) {
      chartInst.current.dispose();
      chartInst.current = null;
    }

    const inst = echarts.init(chartRef.current);
    chartInst.current = inst;

    const cpuData = cpuHistory.map((p) => [
      formatBeijingTime(p.ts),
      p.value,
    ]);
    const rssData = rssHistory.map((p) => [
      formatBeijingTime(p.ts),
      p.value,
    ]);

    inst.setOption({
        tooltip: { trigger: "axis" },
        legend: { data: ["CPU %", "RSS MB"], bottom: 0 },
        grid: { left: 46, right: 42, top: 24, bottom: 52 },
        xAxis: { type: "category", boundaryGap: false, axisLabel: { hideOverlap: true } },
        yAxis: [
          { type: "value", name: "CPU %", max: 100 },
          { type: "value", name: "MB" },
        ],
        series: [
          {
            name: "CPU %",
            type: "line",
            data: cpuData,
            smooth: true,
            areaStyle: { opacity: 0.15 },
            itemStyle: { color: COLORS.primary },
          },
          {
            name: "RSS MB",
            type: "line",
            yAxisIndex: 1,
            data: rssData,
            smooth: true,
            areaStyle: { opacity: 0.1 },
            itemStyle: { color: COLORS.success },
          },
        ],
    });

    const onResize = () => inst.resize();
    window.addEventListener("resize", onResize);

    return () => {
      window.removeEventListener("resize", onResize);
      inst.dispose();
      if (chartInst.current === inst) chartInst.current = null;
    };
  }, [cpuHistory, rssHistory]);

  // 任务搜索过滤
  const filteredTasks = useMemo(() => {
    if (!taskSearch.trim()) return agentTasks;
    const q = taskSearch.toLowerCase();
    return agentTasks.filter(
      (t) =>
        (t.name || "").toLowerCase().includes(q) ||
        taskDisplayName(t).toLowerCase().includes(q) ||
        (t.id || "").toLowerCase().includes(q) ||
        (t.collector_type || "").toLowerCase().includes(q)
    );
  }, [agentTasks, taskSearch]);

  const taskColumns = [
    {
      title: "任务",
      dataIndex: "name",
      ellipsis: true,
      render: (_, record) => (
        <Link
          to={`/task/${encodeURIComponent(record.id)}`}
          title={record.name}
        >
          {taskDisplayName(record)}
        </Link>
      ),
    },
    {
      title: "采集器",
      dataIndex: "collector_type",
      width: 130,
      render: (v) => <Tag>{v}</Tag>,
    },
    {
      title: "PID",
      dataIndex: "target_pid",
      width: 80,
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 110,
      render: (v) => <StatusTag status={v} />,
    },
    {
      title: "时间",
      dataIndex: "created_at",
      width: 180,
      render: (v) => (v ? formatBeijingDateTime(v) : "-"),
    },
  ];

  if (loading) {
    return (
      <div className={styles.page}>
        <Skeleton.Input active size="small" />
        <div className={styles.summaryGrid}>
          <Card size="small"><Skeleton active paragraph={{ rows: 6 }} /></Card>
          <Card size="small"><Skeleton active paragraph={{ rows: 6 }} /></Card>
        </div>
        <Card size="small">
          <Skeleton active paragraph={{ rows: 5 }} />
        </Card>
      </div>
    );
  }

  if (!agent) {
    return (
      <div className={`${styles.page} ${styles.statePage}`}>
        {error ? (
          <>
            <ErrorAlert error={error} onRetry={load} />
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate("/agents")}>
              返回节点列表
            </Button>
          </>
        ) : (
          <Empty
            description={`Agent "${agentId}" 未找到`}
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          >
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate("/agents")}>
              返回节点列表
            </Button>
          </Empty>
        )}
      </div>
    );
  }

  const selfMetrics = agent.latest_metrics?.self;

  return (
    <div className={styles.page}>
      <header className={styles.pageHeader}>
        <div className={styles.headerMain}>
          <Button
            className={styles.backButton}
            icon={<ArrowLeftOutlined />}
            type="text"
            aria-label="返回节点列表"
            onClick={() => navigate("/agents")}
          >
            返回节点
          </Button>
          <span
            className={styles.nodeIcon}
            style={{ color: agent.status === "ONLINE" ? COLORS.success : COLORS.offline }}
          >
            <CloudServerOutlined />
          </span>
          <div className={styles.titleBlock}>
            <span>AGENT DETAIL</span>
            <Typography.Title level={3} title={agent.hostname || agent.id}>
              {agent.hostname || agent.id}
            </Typography.Title>
            <small title={agent.id}>{agent.id}</small>
          </div>
          <StatusTag status={agent.status} />
        </div>
        <Button icon={<ReloadOutlined />} loading={refreshing} aria-label="刷新 Agent 数据" onClick={load}>
          刷新
        </Button>
      </header>

      <ErrorAlert error={error} onClose={() => setError("")} />

      <div className={styles.summaryGrid}>
        <Card
          className={styles.sectionCard}
          title={<span className={styles.cardTitle}><HddOutlined style={{ color: COLORS.primary }} />节点信息</span>}
          size="small"
        >
          <Descriptions
            className={styles.descriptions}
            column={{ xs: 1, sm: 1, md: 2, lg: 2, xl: 2, xxl: 2 }}
            size="small"
            bordered
          >
            <Descriptions.Item label="Agent ID">
              <Typography.Text copyable>{agent.id}</Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="状态">
              <StatusTag status={agent.status} />
            </Descriptions.Item>
            <Descriptions.Item label="Hostname">{reported(agent.hostname)}</Descriptions.Item>
            <Descriptions.Item label="IP">{reported(agent.ip_addr)}</Descriptions.Item>
            <Descriptions.Item label="版本">{reported(agent.version)}</Descriptions.Item>
            <Descriptions.Item label="OS">{reported(agent.os_info)}</Descriptions.Item>
            <Descriptions.Item label="最后心跳">
              {agent.last_heartbeat_at
                ? formatBeijingDateTime(agent.last_heartbeat_at)
                : "未上报"}
            </Descriptions.Item>
            <Descriptions.Item label="注册时间">
              {agent.created_at
                ? formatBeijingDateTime(agent.created_at)
                : "未上报"}
            </Descriptions.Item>
          </Descriptions>

          <div className={styles.subsection}>
            <strong>采集能力</strong>
            {agent.capabilities?.length > 0 ? (
              <div className={styles.tagList}>
                {agent.capabilities.map((cap) => (
                  <Tag key={cap} color="blue">{cap}</Tag>
                ))}
              </div>
            ) : (
              <Typography.Text type="secondary">未上报采集能力</Typography.Text>
            )}
          </div>

          {selfMetrics && (
            <div className={styles.subsection}>
              <strong>Agent 进程开销</strong>
              <div className={styles.tagList}>
                <Tag color="blue">CPU {metric(selfMetrics.cpu_percent, 1)}%</Tag>
                <Tag color="green">RSS {metric(selfMetrics.rss_mb, 1)} MB</Tag>
                <Tag>IO R/W {metric(selfMetrics.read_kb_s)}/{metric(selfMetrics.write_kb_s)} KB/s</Tag>
                <Tag>子进程 {metric(selfMetrics.children_count)}</Tag>
              </div>
            </div>
          )}
        </Card>

        <Card
          className={styles.sectionCard}
          title={(
            <span className={styles.cardTitle}>
              <ApiOutlined style={{ color: COLORS.warning }} />
              Agent 进程趋势
              {cpuHistory.length > 0 && <Tag>过去 {cpuHistory.length} 个采样点</Tag>}
            </span>
          )}
          size="small"
        >
          {cpuHistory.length >= 2 ? (
            <div ref={chartRef} className={styles.chart} aria-label="Agent 进程 CPU 与 RSS 趋势图" />
          ) : (
            <Empty
              description="等待 Agent 进程指标数据…"
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          )}
        </Card>
      </div>

      <Card
        className={`${styles.sectionCard} ${styles.taskCard}`}
        title={<span className={styles.cardTitle}>历史任务<Tag>{filteredTasks.length}</Tag></span>}
        size="small"
        extra={
          <Input
            className={styles.taskSearch}
            size="small"
            placeholder="搜索任务…"
            prefix={<SearchOutlined />}
            allowClear
            aria-label="搜索 Agent 历史任务"
            value={taskSearch}
            onChange={(e) => setTaskSearch(e.target.value)}
          />
        }
      >
        <Table
          rowKey="id"
          columns={taskColumns}
          dataSource={filteredTasks}
          pagination={{ pageSize: 10, responsive: true, showSizeChanger: filteredTasks.length > 10, showTotal: (t) => `共 ${t} 条` }}
          size="small"
          scroll={{ x: 700 }}
          locale={{ emptyText: taskSearch ? "无匹配任务" : "该 Agent 暂无任务记录" }}
        />
      </Card>
    </div>
  );
}
