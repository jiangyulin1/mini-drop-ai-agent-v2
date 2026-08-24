import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Card,
  Drawer,
  Empty,
  Grid,
  Input,
  Modal,
  Select,
  Skeleton,
  Space,
  Tag,
  Timeline,
  Tooltip,
  Typography,
  message,
  notification,
} from "antd";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  CloudServerOutlined,
  ClusterOutlined,
  DeleteOutlined,
  ExclamationCircleOutlined,
  EyeOutlined,
  FileSearchOutlined,
  HddOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SearchOutlined,
  SyncOutlined,
  ThunderboltOutlined,
  WifiOutlined,
} from "@ant-design/icons";
import { formatBeijingDateTime, formatBeijingTime } from "../utils/time";
import { Link, useNavigate } from "react-router-dom";
import { deleteTask, getTask, getTaskEvents, listAgents, listTasks } from "../api/client";
import NLPTaskInput from "../components/NLPTaskInput";
import StatusTag from "../components/StatusTag";
import ErrorAlert from "../components/ErrorAlert";
import MultiAgentCollectionModal from "../components/MultiAgentCollectionModal";
import TaskVisualizationPreview from "../components/TaskVisualizationPreview";
import usePolling from "../hooks/usePolling";
import useSSE from "../hooks/useSSE";
import { COLORS, SPACING } from "../theme";
import { collectorMeta } from "../utils/collectors";
import { isUserVisibleTask, taskDisplayName } from "../utils/taskNames";
import styles from "./Dashboard.module.css";

const RECENT_KEYS = new Set();
const MAX_NOTIFICATIONS = 5;
const ACTIVE_STATUSES = new Set(["PENDING", "RUNNING", "UPLOADING", "ANALYZING"]);

function showEventNotification(eventType, data) {
  const key = [
    eventType,
    data.task_id || data.agent_id || data.diagnosis_id || Date.now(),
    data.to_status || data.status || "changed",
  ].join("-");
  if (RECENT_KEYS.has(key)) return;
  RECENT_KEYS.add(key);
  if (RECENT_KEYS.size > MAX_NOTIFICATIONS) {
    RECENT_KEYS.delete(RECENT_KEYS.values().next().value);
  }

  const messages = {
    task_changed: {
      title: `任务 ${data.task_id?.slice(0, 8)}…`,
      description: `${data.from_status || "?"} → ${data.to_status}`,
      icon: data.to_status === "DONE"
        ? <CheckCircleOutlined style={{ color: COLORS.success }} />
        : data.to_status === "FAILED"
          ? <CloseCircleOutlined style={{ color: COLORS.error }} />
          : <SyncOutlined spin style={{ color: COLORS.primary }} />,
    },
    agent_status: {
      title: `Worker ${data.agent_id}`,
      description: data.status === "ONLINE" ? "已上线" : "已离线",
      icon: <CloudServerOutlined style={{ color: data.status === "ONLINE" ? COLORS.success : COLORS.error }} />,
    },
    diagnosis_complete: {
      title: `诊断 ${data.diagnosis_id?.slice(0, 8)}…`,
      description: data.status === "DONE" ? "诊断完成" : "诊断失败",
      icon: <RobotOutlined style={{ color: data.status === "DONE" ? COLORS.success : COLORS.error }} />,
    },
  };

  const config = messages[eventType];
  if (!config) return;
  notification.open({
    key,
    message: config.title,
    description: config.description,
    icon: config.icon,
    placement: "bottomRight",
    duration: 4,
  });
}

function TaskDetailPanel({ taskId, taskRevision, onDeleted, onAnalyze }) {
  const [task, setTask] = useState(null);
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(Boolean(taskId));
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setError("");
    setTask(null);
    setEvents([]);
    if (!taskId) {
      setLoading(false);
      return undefined;
    }

    setLoading(true);
    Promise.allSettled([getTask(taskId), getTaskEvents(taskId)]).then(([taskResult, eventResult]) => {
      if (cancelled) return;
      if (taskResult.status === "fulfilled") setTask(taskResult.value);
      else setError(taskResult.reason?.message || "任务加载失败");
      if (eventResult.status === "fulfilled") setEvents(eventResult.value || []);
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, [taskId, taskRevision]);

  if (!taskId) {
    return (
      <div className={styles.detailEmpty}>
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <span className={styles.emptyDescription}>
              <strong>还没有可查看的任务</strong>
              <small>在上方创建采集，完成后可直接查看原始可视化，再决定是否交给 AI 诊断。</small>
            </span>
          }
        />
      </div>
    );
  }

  if (loading) return <div className={styles.detailLoading}><Skeleton active paragraph={{ rows: 8 }} /></div>;
  if (error || !task) return <Alert type="warning" showIcon message="任务加载失败" description={error} className={styles.detailAlert} />;

  const meta = collectorMeta(task.collector_type);
  const statusEvents = [...events].reverse();
  const analysisReady = task.status === "DONE";

  return (
    <div className={styles.detailContent}>
      <header className={styles.detailHeader}>
        <div className={styles.detailHeading}>
          <div className={styles.detailTitleRow}>
            <Typography.Title level={5} ellipsis>{taskDisplayName(task)}</Typography.Title>
            <StatusTag status={task.status} />
            <Tag color={meta.color}>{meta.label}</Tag>
          </div>
          <Typography.Text type="secondary" className={styles.detailMeta}>
            PID {task.target_pid} · {task.agent_id} · {formatBeijingDateTime(task.created_at)}
          </Typography.Text>
        </div>
        <div className={styles.detailActions}>
          <Tooltip title={analysisReady ? "用该任务的 Worker、PID 和证据引用创建诊断会话" : "采集完成后才能创建 AI 诊断"}>
            <span>
              <Button
                size="small"
                type="primary"
                icon={<RobotOutlined />}
                disabled={!analysisReady}
                onClick={() => onAnalyze(task)}
              >
                创建 AI 诊断
              </Button>
            </span>
          </Tooltip>
          <Link to={`/task/${task.id}`}>
            <Button size="small" icon={<EyeOutlined />}>完整结果</Button>
          </Link>
          <Tooltip title={["DONE", "FAILED", "CANCELLED"].includes(task.status) ? "删除任务及关联产物" : "进行中的任务不能删除"}>
            <span>
              <Button
                size="small"
                danger
                icon={<DeleteOutlined />}
                disabled={!(["DONE", "FAILED", "CANCELLED"].includes(task.status))}
                aria-label="删除任务"
                onClick={() => onDeleted(task)}
              />
            </span>
          </Tooltip>
        </div>
      </header>

      {statusEvents.length > 0 && (
        <Card
          size="small"
          className={styles.timelineCard}
          title={<Space size={6}><FileSearchOutlined />最近状态</Space>}
        >
          <Timeline
            items={statusEvents.slice(0, 6).map((event) => ({
              color: event.to_status === "DONE" ? "green" : event.to_status === "FAILED" ? "red" : "blue",
              children: (
                <div className={styles.timelineEvent}>
                  <span>{event.from_status || "—"} → {event.to_status}</span>
                  <time>{formatBeijingTime(event.created_at)}</time>
                  {event.reason && <small title={event.reason}>{event.reason}</small>}
                </div>
              ),
            }))}
          />
        </Card>
      )}

      <TaskVisualizationPreview taskId={task.id} revision={task.updated_at || task.status} />
    </div>
  );
}

function SummaryCard({ label, value, detail, tone = "default" }) {
  return (
    <div className={`${styles.summaryCard} ${styles[`summary_${tone}`] || ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}

export default function Dashboard() {
  const navigate = useNavigate();
  const screens = Grid.useBreakpoint();
  const isMobile = screens.md === false;
  const refreshSequenceRef = useRef(0);
  const detailPaneRef = useRef(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [tasks, setTasks] = useState([]);
  const [agents, setAgents] = useState([]);
  const [agentsLoaded, setAgentsLoaded] = useState(false);
  const [searchText, setSearchText] = useState("");
  const deferredSearch = useDeferredValue(searchText);
  const [statusFilter, setStatusFilter] = useState("");
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [multiOpen, setMultiOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);

  const refresh = useCallback(async ({ quiet = false } = {}) => {
    const requestId = ++refreshSequenceRef.current;
    if (quiet) setRefreshing(true);
    setError("");
    try {
      const [taskResult, agentResult] = await Promise.allSettled([
        listTasks({ sort_by: "created_at", sort_order: "desc", limit: 200 }),
        listAgents(),
      ]);
      if (requestId !== refreshSequenceRef.current) return;
      if (taskResult.status === "fulfilled") {
        const nextTasks = (taskResult.value || []).filter(isUserVisibleTask);
        setTasks(nextTasks);
        setSelectedTaskId((current) => (
          current && nextTasks.some((task) => task.id === current)
            ? current
            : nextTasks[0]?.id || ""
        ));
      }
      if (agentResult.status === "fulfilled") {
        setAgents((agentResult.value || []).filter((agent) => agent.id !== "demo-worker"));
        setAgentsLoaded(true);
      } else {
        setAgentsLoaded(false);
      }
      const failures = [taskResult, agentResult]
        .filter((item) => item.status === "rejected")
        .map((item) => item.reason?.message || "数据加载失败");
      if (failures.length) setError([...new Set(failures)].join("；"));
    } finally {
      if (requestId === refreshSequenceRef.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    const reloadAfterAuth = () => refresh();
    window.addEventListener("mini-drop:auth-changed", reloadAfterAuth);
    return () => window.removeEventListener("mini-drop:auth-changed", reloadAfterAuth);
  }, [refresh]);

  const handleDeleteTask = useCallback((task) => {
    Modal.confirm({
      title: "删除这个采集任务？",
      icon: <ExclamationCircleOutlined />,
      content: (
        <div>
          <p>任务、状态事件、诊断结果和采集产物将一起删除。</p>
          <p><strong>{taskDisplayName(task)}</strong></p>
          <p className={styles.dangerHint}>此操作不可撤销。</p>
        </div>
      ),
      okText: "确认删除",
      okType: "danger",
      cancelText: "取消",
      onOk: async () => {
        try {
          await deleteTask(task.id);
          notification.success({ message: "任务已删除", placement: "bottomRight" });
          await refresh({ quiet: true });
        } catch (deleteError) {
          notification.error({ message: "删除失败", description: deleteError.message, placement: "bottomRight" });
        }
      },
    });
  }, [refresh]);

  const { connected: sseConnected } = useSSE({
    onTaskChanged(data) {
      showEventNotification("task_changed", data);
      refresh({ quiet: true });
    },
    onAgentStatus(data) {
      showEventNotification("agent_status", data);
      refresh({ quiet: true });
    },
    onDiagnosisComplete(data) {
      showEventNotification("diagnosis_complete", data);
    },
  });

  const { isPolling } = usePolling(() => refresh({ quiet: true }), {
    interval: 10000,
    enabled: !loading && !sseConnected,
  });

  const stats = useMemo(() => {
    const doneCount = tasks.filter((task) => task.status === "DONE").length;
    const failedCount = tasks.filter((task) => task.status === "FAILED").length;
    const activeCount = tasks.filter((task) => ACTIVE_STATUSES.has(task.status)).length;
    const onlineCount = agents.filter((agent) => agent.status === "ONLINE").length;
    const terminalCount = doneCount + failedCount;
    return {
      doneCount,
      failedCount,
      activeCount,
      onlineCount,
      offlineCount: agents.length - onlineCount,
      successRate: terminalCount ? Math.round((doneCount / terminalCount) * 100) : null,
    };
  }, [agents, tasks]);

  const filteredTasks = useMemo(() => {
    const query = deferredSearch.trim().toLowerCase();
    return tasks.filter((task) => {
      if (statusFilter && task.status !== statusFilter) return false;
      if (!query) return true;
      const meta = collectorMeta(task.collector_type);
      return [
        taskDisplayName(task),
        task.id,
        task.agent_id,
        task.target_pid,
        task.status,
        meta.label,
      ].some((value) => String(value || "").toLowerCase().includes(query));
    });
  }, [deferredSearch, statusFilter, tasks]);

  useEffect(() => {
    setSelectedTaskId((current) => (
      current && filteredTasks.some((task) => task.id === current)
        ? current
        : filteredTasks[0]?.id || ""
    ));
  }, [filteredTasks]);

  const selectedTask = filteredTasks.find((task) => task.id === selectedTaskId);

  const focusDetail = useCallback(() => {
    if (!isMobile) return;
    requestAnimationFrame(() => {
      detailPaneRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      detailPaneRef.current?.focus({ preventScroll: true });
    });
  }, [isMobile]);

  const selectTask = useCallback((taskId) => {
    setSelectedTaskId(taskId);
    focusDetail();
  }, [focusDetail]);

  const handleTaskCreated = useCallback(async (taskId) => {
    setSelectedTaskId(taskId);
    setCreateOpen(false);
    await refresh({ quiet: true });
    focusDetail();
  }, [focusDetail, refresh]);

  if (loading) {
    return (
      <Space direction="vertical" size={SPACING.lg} className={styles.loadingPage}>
        <Skeleton.Input active size="small" className={styles.loadingTitle} />
        <div className={styles.summaryGrid}>{[1, 2, 3, 4].map((item) => <Skeleton active paragraph={{ rows: 1 }} key={item} />)}</div>
        <Skeleton active paragraph={{ rows: 10 }} />
      </Space>
    );
  }

  return (
    <div className={styles.page}>
      <header className={styles.pageHeader}>
        <div className={styles.pageHeaderCopy}>
          <span className={styles.eyebrow}>采集 · 分析 · 验证</span>
          <div className={styles.titleRow}>
            <HddOutlined />
            <Typography.Title level={1}>采集与监控</Typography.Title>
            {sseConnected ? (
              <Tag icon={<WifiOutlined />} color="success">实时更新</Tag>
            ) : isPolling ? (
              <Tag icon={<SyncOutlined spin />} color="processing">每 10 秒更新</Tag>
            ) : null}
          </div>
          <Typography.Paragraph>
            先选择 Worker 和目标进程完成一次采集；结果可独立查看，也可带入 AI 会话做跨节点归因。
          </Typography.Paragraph>
        </div>
        <div className={styles.pageActions}>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建采集</Button>
          <Button icon={<ClusterOutlined />} onClick={() => setMultiOpen(true)}>多机采集</Button>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={() => refresh({ quiet: true })}>刷新</Button>
        </div>
      </header>

      <section className={styles.summaryGrid} aria-label="运行概览">
        <SummaryCard
          label="Worker"
          value={`${stats.onlineCount}/${agents.length}`}
          detail={stats.offlineCount ? `${stats.offlineCount} 个离线` : "全部在线"}
          tone={stats.onlineCount ? "success" : "warning"}
        />
        <SummaryCard
          label="进行中"
          value={stats.activeCount}
          detail="排队、采集或分析"
          tone={stats.activeCount ? "primary" : "default"}
        />
        <SummaryCard
          label="已完成"
          value={stats.doneCount}
          detail={stats.successRate === null ? "暂无终态任务" : `终态成功率 ${stats.successRate}%`}
          tone="success"
        />
        <SummaryCard
          label="失败"
          value={stats.failedCount}
          detail={stats.failedCount ? "可打开任务查看原因" : "当前无失败任务"}
          tone={stats.failedCount ? "danger" : "default"}
        />
      </section>

      <ErrorAlert error={error} onClose={() => setError("")} />

      <section className={styles.workspaceGrid} aria-label="节点、任务和结果">
        <div className={styles.sideColumn}>
          <Card
            size="small"
            className={styles.agentCard}
            title={<Space size={7}><CloudServerOutlined />Worker 节点</Space>}
            extra={<Badge status={stats.onlineCount ? "success" : "default"} text={`${stats.onlineCount} 在线`} />}
          >
            {agentsLoaded && agents.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无 Worker 注册" />
            ) : (
              <div className={styles.agentList}>
                {agents.map((agent) => (
                  <Link className={styles.agentLink} to={`/agent/${encodeURIComponent(agent.id)}`} key={agent.id}>
                    <span className={`${styles.agentDot} ${agent.status === "ONLINE" ? styles.agentOnline : ""}`} />
                    <span className={styles.agentIdentity}>
                      <strong>{agent.hostname || agent.id}</strong>
                      <small>{agent.ip_addr || agent.id}</small>
                    </span>
                    <Tag color={agent.status === "ONLINE" ? "success" : "default"}>
                      {agent.status === "ONLINE" ? "在线" : "离线"}
                    </Tag>
                  </Link>
                ))}
              </div>
            )}
          </Card>

          <Card
            size="small"
            className={styles.taskCard}
            title={<Space size={7}><ThunderboltOutlined />采集任务 <Tag>{filteredTasks.length} / 最近最多 200 条</Tag></Space>}
          >
            <div className={styles.taskFilters}>
              <Input
                size="small"
                aria-label="搜索采集任务"
                placeholder="搜索名称、节点或 PID"
                prefix={<SearchOutlined />}
                allowClear
                value={searchText}
                onChange={(event) => setSearchText(event.target.value)}
              />
              <Select
                size="small"
                aria-label="按任务状态筛选"
                value={statusFilter || undefined}
                onChange={(value) => setStatusFilter(value || "")}
                allowClear
                placeholder="全部状态"
                options={[
                  { value: "PENDING", label: "排队中" },
                  { value: "RUNNING", label: "采集中" },
                  { value: "UPLOADING", label: "上传中" },
                  { value: "ANALYZING", label: "分析中" },
                  { value: "DONE", label: "已完成" },
                  { value: "FAILED", label: "失败" },
                  { value: "CANCELLED", label: "已取消" },
                ]}
              />
            </div>
            <div className={styles.taskList}>
              {filteredTasks.length === 0 ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={tasks.length ? "没有匹配的任务" : "还没有采集任务"} />
              ) : filteredTasks.map((task) => {
                const meta = collectorMeta(task.collector_type);
                const active = selectedTaskId === task.id;
                return (
                  <button
                    type="button"
                    className={`${styles.taskButton} ${active ? styles.taskButtonActive : ""}`}
                    key={task.id}
                    aria-pressed={active}
                    onClick={() => selectTask(task.id)}
                  >
                    <span className={styles.taskTitleRow}>
                      <strong>{taskDisplayName(task)}</strong>
                      <StatusTag status={task.status} />
                    </span>
                    <span className={styles.taskMetaRow}>
                      <small>{meta.label} · PID {task.target_pid}</small>
                      <time>{formatBeijingDateTime(task.created_at)}</time>
                    </span>
                  </button>
                );
              })}
            </div>
          </Card>
        </div>

        <div className={styles.detailPane} ref={detailPaneRef} tabIndex={-1}>
          <TaskDetailPanel
            taskId={selectedTask?.id || ""}
            taskRevision={selectedTask?.updated_at || selectedTask?.status || ""}
            onDeleted={handleDeleteTask}
            onAnalyze={(task) => navigate(`/cases?fromTask=${encodeURIComponent(task.id)}`)}
          />
        </div>
      </section>

      <Drawer
        title="新建采集任务"
        placement={isMobile ? "bottom" : "right"}
        height={isMobile ? "calc(100dvh - 56px)" : undefined}
        width={isMobile ? undefined : 620}
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        destroyOnHidden
        rootClassName={styles.createDrawer}
        styles={{ body: { padding: 12, background: "var(--app-background)" } }}
      >
        <NLPTaskInput onTaskCreated={handleTaskCreated} />
      </Drawer>

      <MultiAgentCollectionModal
        open={multiOpen}
        agents={agents}
        onClose={() => setMultiOpen(false)}
        onCreated={(collectionId) => {
          refresh({ quiet: true });
          message.success(`采集会话 ${collectionId} 已创建，可在 AI 诊断中关联分析`);
        }}
      />
    </div>
  );
}
