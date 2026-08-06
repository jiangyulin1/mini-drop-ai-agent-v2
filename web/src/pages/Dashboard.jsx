import { useEffect, useState, useMemo, useCallback } from "react";
import {
  Alert,
  Badge,
  Button,
  Card,
  Empty,
  Input,
  Modal,
  notification,
  Select,
  Skeleton,
  Space,
  Tag,
  Timeline,
  Typography,
  message,
} from "antd";
import {
  ReloadOutlined,
  CloudServerOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  SyncOutlined,
  ExperimentOutlined,
  DeleteOutlined,
  SearchOutlined,
  ExclamationCircleOutlined,
  EyeOutlined,
  ThunderboltOutlined,
  HddOutlined,
  WifiOutlined,
  RobotOutlined,
  ClusterOutlined,
  FileSearchOutlined,
} from "@ant-design/icons";
import { Link, useNavigate } from "react-router-dom";
import { listAgents, listTasks, deleteTask, getTask, getTaskEvents } from "../api/client";
import NLPTaskInput from "../components/NLPTaskInput";
import StatusTag from "../components/StatusTag";
import ErrorAlert from "../components/ErrorAlert";
import MultiAgentCollectionModal from "../components/MultiAgentCollectionModal";
import TaskVisualizationPreview from "../components/TaskVisualizationPreview";
import usePolling from "../hooks/usePolling";
import useSSE from "../hooks/useSSE";
import { COLORS, SPACING } from "../theme";
import { collectorMeta } from "../utils/collectors";
import { taskDisplayName } from "../utils/taskNames";

// ── 通知列表（最近 5 条 toast 通知）──────────────────────

const RECENT_KEYS = new Set();
const MAX_NOTIFICATIONS = 5;

function showEventNotification(eventType, data) {
  const key = `${eventType}-${data.task_id || data.agent_id || Date.now()}`;
  if (RECENT_KEYS.has(key)) return;
  RECENT_KEYS.add(key);
  if (RECENT_KEYS.size > MAX_NOTIFICATIONS) {
    const first = RECENT_KEYS.values().next().value;
    RECENT_KEYS.delete(first);
  }

  const messages = {
    task_changed: {
      title: `任务 ${data.task_id?.slice(0, 8)}…`,
      description: `${data.from_status || "?"} → ${data.to_status}`,
      icon: data.to_status === "DONE" ? <CheckCircleOutlined style={{ color: COLORS.success }} />
        : data.to_status === "FAILED" ? <CloseCircleOutlined style={{ color: COLORS.error }} />
        : <SyncOutlined spin style={{ color: COLORS.primary }} />,
    },
    agent_status: {
      title: `Agent ${data.agent_id}`,
      description: data.status === "ONLINE" ? "已上线" : "已离线",
      icon: data.status === "ONLINE"
        ? <CloudServerOutlined style={{ color: COLORS.success }} />
        : <CloudServerOutlined style={{ color: COLORS.error }} />,
    },
    diagnosis_complete: {
      title: `诊断 ${data.diagnosis_id?.slice(0, 8)}…`,
      description: data.status === "DONE" ? "诊断完成" : "诊断失败",
      icon: <ExperimentOutlined style={{ color: data.status === "DONE" ? COLORS.success : COLORS.error }} />,
    },
  };

  const cfg = messages[eventType];
  if (!cfg) return;

  notification.open({
    key,
    message: cfg.title,
    description: cfg.description,
    icon: cfg.icon,
    placement: "bottomRight",
    duration: 4,
    style: { borderRadius: 8 },
  });
}

// ── 任务详情（右侧内嵌）────────────────────────────────

function TaskDetailPanel({ taskId, onDeleted, onAnalyze }) {
  const [task, setTask] = useState(null);
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    setTask(null);
    setEvents([]);
    if (!taskId) {
      setLoading(false);
      return undefined;
    }
    Promise.allSettled([getTask(taskId), getTaskEvents(taskId)]).then(([taskRes, eventRes]) => {
      if (cancelled) return;
      if (taskRes.status === "fulfilled") setTask(taskRes.value);
      else setError(taskRes.reason?.message || "任务加载失败");
      if (eventRes.status === "fulfilled") setEvents(eventRes.value || []);
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, [taskId]);

  if (!taskId) {
    return (
      <div style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <Space direction="vertical" size={6}>
              <span>从左侧选择一个任务查看详情</span>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                可直接查看火焰图、TopN、I/O 延迟与状态时间线，无需进入 AI
              </Typography.Text>
            </Space>
          }
        />
      </div>
    );
  }

  if (loading) return <div style={{ padding: 24 }}><Skeleton active paragraph={{ rows: 8 }} /></div>;
  if (error || !task) return <Alert type="warning" showIcon message="任务加载失败" description={error} style={{ margin: 12 }} />;

  const meta = collectorMeta(task.collector_type);
  const statusEvents = [...events].reverse();
  return (
    <div style={{ padding: "2px 4px" }}>
      {/* 任务元信息 */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap", marginBottom: 12 }}>
        <Space direction="vertical" size={4} style={{ minWidth: 0 }}>
          <Space wrap>
            <Typography.Title level={5} style={{ margin: 0 }} ellipsis>{taskDisplayName(task)}</Typography.Title>
            <StatusTag status={task.status} />
            <Tag color={meta.color}>{meta.label}</Tag>
          </Space>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {task.id} · PID {task.target_pid} · {task.agent_id} · {new Date(task.created_at).toLocaleString()}
          </Typography.Text>
        </Space>
        <Space size={6}>
          <Button
            size="small"
            type="primary"
            icon={<RobotOutlined />}
            onClick={() => onAnalyze(task)}
          >
            交给 AI 分析
          </Button>
          <Link to={`/task/${task.id}`}>
            <Button size="small" icon={<EyeOutlined />}>打开完整结果</Button>
          </Link>
          <Button
            size="small"
            danger
            icon={<DeleteOutlined />}
            disabled={!["DONE", "FAILED"].includes(task.status)}
            onClick={() => onDeleted(task)}
          >
            删除
          </Button>
        </Space>
      </div>

      {/* 状态时间线 */}
      {statusEvents.length > 0 && (
        <Card size="small" title={<Space size={6}><FileSearchOutlined style={{ color: COLORS.primary }} />状态变化</Space>} style={{ marginBottom: 12 }}>
          <Timeline
            items={statusEvents.slice(0, 8).map((event) => ({
              color: event.to_status === "DONE" ? "green" : event.to_status === "FAILED" ? "red" : "blue",
              children: (
                <Space size={6}>
                  <Typography.Text style={{ fontSize: 12 }}>{event.from_status || "—"} → {event.to_status}</Typography.Text>
                  <Typography.Text type="secondary" style={{ fontSize: 11 }}>{new Date(event.created_at).toLocaleTimeString()}</Typography.Text>
                  {event.reason && <Typography.Text type="secondary" style={{ fontSize: 11 }} ellipsis>· {event.reason}</Typography.Text>}
                </Space>
              ),
            }))}
          />
        </Card>
      )}

      {/* 可视化预览 */}
      <TaskVisualizationPreview taskId={task.id} />
    </div>
  );
}

// ── 主页面 ─────────────────────────────────────────────

export default function Dashboard() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [tasks, setTasks] = useState([]);
  const [agents, setAgents] = useState([]);
  const [agentsLoaded, setAgentsLoaded] = useState(false);

  const [searchText, setSearchText] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [multiOpen, setMultiOpen] = useState(false);

  // ── 数据加载 ──────────────────────────────────────────

  const refresh = useCallback(async () => {
    setError("");
    try {
      const params = {};
      if (searchText.trim()) params.search = searchText.trim();
      params.sort_by = "created_at";
      params.sort_order = "desc";

      const [taskRes, agentRes] = await Promise.allSettled([
        listTasks(params),
        listAgents(),
      ]);
      if (taskRes.status === "fulfilled") setTasks(taskRes.value || []);
      if (agentRes.status === "fulfilled") {
        setAgents(agentRes.value || []);
        setAgentsLoaded(true);
      } else {
        setAgentsLoaded(false);
      }
      const failures = [taskRes, agentRes]
        .filter((item) => item.status === "rejected")
        .map((item) => item.reason?.message || "数据加载失败");
      if (failures.length) setError([...new Set(failures)].join("；"));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [searchText]);

  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    window.addEventListener("mini-drop:auth-changed", refresh);
    return () => window.removeEventListener("mini-drop:auth-changed", refresh);
  }, [refresh]);

  // ── 删除任务 ──────────────────────────────────────────

  const handleDeleteTask = useCallback((task) => {
    Modal.confirm({
      title: "确认删除任务？",
      icon: <ExclamationCircleOutlined />,
      content: (
        <div>
          <p>将删除以下任务及其火焰图、事件、诊断结果：</p>
          <p><strong>{taskDisplayName(task)}</strong></p>
          <p style={{ color: "#999", fontSize: 12 }}>
            PID: {task.target_pid} · {task.collector_type} · {new Date(task.created_at).toLocaleString()}
          </p>
          <p style={{ color: "#ff4d4f", fontSize: 12 }}>
            仅 DONE/FAILED 终态任务可删除，此操作不可撤销。
          </p>
        </div>
      ),
      okText: "确认删除",
      okType: "danger",
      cancelText: "取消",
      onOk: async () => {
        try {
          await deleteTask(task.id);
          notification.success({ message: "删除成功", description: `任务 ${taskDisplayName(task)} 已删除`, placement: "bottomRight", duration: 3 });
          if (selectedTaskId === task.id) setSelectedTaskId("");
          refresh();
        } catch (err) {
          notification.error({ message: "删除失败", description: err.message, placement: "bottomRight", duration: 5 });
        }
      },
    });
  }, [refresh, selectedTaskId]);

  // ── SSE 实时事件 ──────────────────────────────────────

  const { connected: sseConnected } = useSSE({
    onTaskChanged(data) {
      showEventNotification("task_changed", data);
      refresh();
    },
    onAgentStatus(data) {
      showEventNotification("agent_status", data);
      refresh();
    },
    onDiagnosisComplete(data) {
      showEventNotification("diagnosis_complete", data);
      refresh();
    },
  });

  const { isPolling } = usePolling(refresh, {
    interval: 10000,
    enabled: !loading && !sseConnected,
  });

  // ── 统计 ──────────────────────────────────────────────

  const stats = useMemo(() => {
    const doneCount = tasks.filter((t) => t.status === "DONE").length;
    const failedCount = tasks.filter((t) => t.status === "FAILED").length;
    const activeCount = tasks.filter((t) =>
      ["PENDING", "RUNNING", "UPLOADING", "ANALYZING"].includes(t.status)
    ).length;
    const onlineCount = agents.filter((a) => a.status === "ONLINE").length;
    const offlineCount = agents.filter((a) => a.status === "OFFLINE").length;
    const successRate = tasks.length > 0
      ? Math.round((doneCount / (doneCount + failedCount || 1)) * 100)
      : 100;
    return { total: tasks.length, doneCount, failedCount, activeCount, onlineCount, offlineCount, successRate };
  }, [tasks, agents]);

  // ── 筛选任务列表 ──────────────────────────────────────

  const filteredTasks = useMemo(() => {
    let items = tasks;
    if (statusFilter) items = items.filter((t) => t.status === statusFilter);
    return items;
  }, [tasks, statusFilter]);

  // ── 交给 AI 分析 ─────────────────────────────────────

  const handleAnalyze = useCallback((task) => {
    // 把采集上下文传给 AI 页：任务 ID 用于自动带入目标范围
    navigate(`/ai-diagnosis?fromTask=${task.id}`);
  }, [navigate]);

  if (loading) {
    return (
      <Space direction="vertical" size={SPACING.lg} style={{ width: "100%" }}>
        <Skeleton.Input active size="small" style={{ width: 160 }} />
        <RowPlaceholder />
        <Skeleton active paragraph={{ rows: 8 }} />
      </Space>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, height: "calc(100vh - 96px)" }}>
      {/* ── 顶部：标题 + 统计 + 操作 ───────────────────── */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <Space align="center">
          <HddOutlined style={{ fontSize: 20, color: COLORS.primary }} />
          <Typography.Title level={4} style={{ margin: 0 }}>采集与监控</Typography.Title>
          {sseConnected ? (
            <Tag icon={<WifiOutlined />} color="success">实时连接</Tag>
          ) : isPolling ? (
            <Tag icon={<SyncOutlined spin />} color="processing">10s 轮询</Tag>
          ) : null}
        </Space>
        <Space size="small" wrap>
          <Badge status="success" text={`${stats.onlineCount} 在线`} />
          <Badge status="default" text={`${stats.offlineCount} 离线`} />
          <Badge status="processing" text={`${stats.activeCount} 进行中`} />
          <Tag color={stats.successRate >= 80 ? "green" : "orange"}>成功率 {stats.successRate}%</Tag>
          <Button size="small" icon={<ClusterOutlined />} onClick={() => setMultiOpen(true)}>多机采集</Button>
          <Button size="small" icon={<ReloadOutlined />} onClick={refresh}>刷新</Button>
        </Space>
      </div>

      {/* ── 快速采集 ───────────────────────────────────── */}
      <NLPTaskInput
        onTaskCreated={(taskId) => {
          refresh();
          setSelectedTaskId(taskId);
        }}
      />

      <ErrorAlert error={error} onClose={() => setError("")} />

      {/* ── 主体：左 Agent+任务 / 右详情 ───────────────── */}
      <div style={{ display: "flex", gap: 12, flex: 1, minHeight: 0 }}>
        {/* 左栏 */}
        <div style={{ width: 360, minWidth: 300, display: "flex", flexDirection: "column", gap: 12, overflow: "auto" }}>
          {/* Agent 状态 */}
          <Card size="small" title={<Space size={6}><CloudServerOutlined style={{ color: COLORS.success }} />Agent 节点</Space>} styles={{ body: { padding: "8px 12px" } }}>
            {agentsLoaded && agents.length === 0 ? (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>暂无 Agent 注册</Typography.Text>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {agents.map((agent) => (
                  <div key={agent.id} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ width: 8, height: 8, borderRadius: "50%", background: agent.status === "ONLINE" ? COLORS.success : COLORS.offline, flexShrink: 0 }} />
                    <Space size={6} style={{ minWidth: 0, flex: 1 }}>
                      <Typography.Text strong style={{ fontSize: 12 }}>{agent.hostname || agent.id}</Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: 10 }}>{agent.ip_addr || agent.id}</Typography.Text>
                    </Space>
                    <Tag style={{ fontSize: 10, lineHeight: "16px", margin: 0 }}>{agent.status === "ONLINE" ? "在线" : "离线"}</Tag>
                  </div>
                ))}
              </div>
            )}
          </Card>

          {/* 任务列表 */}
          <Card
            size="small"
            title={<Space size={6}><ThunderboltOutlined style={{ color: COLORS.warning }} />任务列表 <Tag>{filteredTasks.length}</Tag></Space>}
            styles={{ body: { padding: "6px 8px" } }}
            extra={
              <Select
                size="small"
                style={{ width: 110 }}
                value={statusFilter}
                onChange={setStatusFilter}
                allowClear
                placeholder="全部状态"
                options={[
                  { value: "PENDING", label: "排队中" },
                  { value: "RUNNING", label: "采集中" },
                  { value: "UPLOADING", label: "上传中" },
                  { value: "ANALYZING", label: "分析中" },
                  { value: "DONE", label: "已完成" },
                  { value: "FAILED", label: "失败" },
                ]}
              />
            }
          >
            <Input
              size="small"
              placeholder="搜索任务名…"
              prefix={<SearchOutlined />}
              allowClear
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
              style={{ marginBottom: 8 }}
            />
            <div style={{ maxHeight: "calc(100vh - 460px)", overflow: "auto", display: "flex", flexDirection: "column", gap: 4 }}>
              {filteredTasks.length === 0 && (
                <Typography.Text type="secondary" style={{ fontSize: 12, padding: 8 }}>暂无任务</Typography.Text>
              )}
              {filteredTasks.slice(0, 200).map((task) => {
                const meta = collectorMeta(task.collector_type);
                const active = selectedTaskId === task.id;
                return (
                  <div
                    key={task.id}
                    role="button"
                    tabIndex={0}
                    onClick={() => setSelectedTaskId(task.id)}
                    onKeyDown={(e) => { if (e.key === "Enter") setSelectedTaskId(task.id); }}
                    style={{
                      padding: "8px 10px",
                      borderRadius: 8,
                      cursor: "pointer",
                      border: active ? `1px solid ${COLORS.primary}` : "1px solid transparent",
                      background: active ? "rgba(36,95,145,0.08)" : "transparent",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 6 }}>
                      <Typography.Text strong style={{ fontSize: 12 }} ellipsis>{taskDisplayName(task)}</Typography.Text>
                      <StatusTag status={task.status} />
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", marginTop: 3 }}>
                      <Typography.Text type="secondary" style={{ fontSize: 10 }}>{meta.label} · PID {task.target_pid}</Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: 10 }}>{new Date(task.created_at).toLocaleString()}</Typography.Text>
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>
        </div>

        {/* 右栏：任务详情 */}
        <div style={{ flex: 1, minWidth: 0, overflow: "auto", border: "1px solid #f0f0f0", borderRadius: 8, background: "#fff" }}>
          <TaskDetailPanel taskId={selectedTaskId} onDeleted={handleDeleteTask} onAnalyze={handleAnalyze} />
        </div>
      </div>

      <MultiAgentCollectionModal
        open={multiOpen}
        agents={agents}
        onClose={() => setMultiOpen(false)}
        onCreated={(collectionId) => {
          refresh();
          message.success(`采集会话 ${collectionId} 已创建，可在 AI 诊断中关联分析`);
        }}
      />
    </div>
  );
}

function RowPlaceholder() {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12 }}>
      {[1, 2, 3, 4].map((i) => <Skeleton active paragraph={{ rows: 1 }} key={i} />)}
    </div>
  );
}
