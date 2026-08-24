import { useEffect, useState, useRef, useCallback } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Descriptions,
  Empty,
  message,
  Modal,
  Progress,
  Row,
  Select,
  Skeleton,
  Space,
  Spin,
  Table,
  Tag,
  Timeline,
  Tooltip,
  Typography,
} from "antd";
import {
  ArrowLeftOutlined,
  BarChartOutlined,
  CheckOutlined,
  CloseOutlined,
  DownloadOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  MinusOutlined,
  RedoOutlined,
  ReloadOutlined,
  RobotOutlined,
  StopOutlined,
} from "@ant-design/icons";
import { formatBeijingDateTime } from "../utils/time";
import { useParams, useNavigate } from "react-router-dom";
import {
  cancelTask,
  createIncidentCase,
  downloadTaskArtifact,
  getDiagnosis,
  getTask,
  getTaskAnalysisJobs,
  getTaskAttempts,
  getTaskArtifactContent,
  getTaskArtifacts,
  getTaskEvents,
  listTaskDiagnoses,
  retryTask,
  submitDiagnosisFeedback,
} from "../api/client";
import FlamegraphViewer from "../components/FlamegraphViewer";
import TopNChart from "../components/TopNChart";
import SandboxedArtifactFrame from "../components/SandboxedArtifactFrame";
import {
  EBPFHistogramChart,
  JavaFlameViewer,
  MemoryChart,
  SysMetricsView,
} from "../components/TaskArtifactViews";
import StatusTag from "../components/StatusTag";
import ErrorAlert from "../components/ErrorAlert";
import usePolling from "../hooks/usePolling";
import { isTaskActive } from "../utils/status";
import { taskDisplayInfo, taskDisplayName } from "../utils/taskNames";
import { collectorMeta } from "../utils/collectors";
import {
  isArtifactAvailable,
  unavailableVisualArtifacts,
} from "../utils/artifacts";
import {
  extractFlamegraphTreeFromSvg,
  extractTopFunctionsFromSvg,
} from "../utils/flamegraph";
import {
  TOOL_LABELS,
  TOOL_STATUS,
  causeLabel,
  confidencePresentation,
  effectiveToolStatus,
  evidenceRefLabel,
  repairLabel,
  toolResultSummary,
} from "../utils/diagnosisPresentation";
import { COLORS, FLAMEGRAPH, SPACING } from "../theme";
import styles from "./TaskResult.module.css";

export default function TaskResult() {
  const { taskId } = useParams();
  const navigate = useNavigate();
  const flameRef = useRef(null);
  const taskIdRef = useRef(taskId);
  const requestSequenceRef = useRef(0);
  const fullLoadInFlightRef = useRef(false);
  taskIdRef.current = taskId;

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [task, setTask] = useState(null);
  const [events, setEvents] = useState([]);
  const [artifacts, setArtifacts] = useState([]);
  const [attempts, setAttempts] = useState([]);
  const [analysisJobs, setAnalysisJobs] = useState([]);
  const [diagnoses, setDiagnoses] = useState([]);
  const [diagnosis, setDiagnosis] = useState(null);
  const [creatingCase, setCreatingCase] = useState(false);
  const [analysis, setAnalysis] = useState({
    top: [],
    topSource: "",
    svg: "",
    derivedTree: null,
    hasFlameJson: false,
  });
  const [analysisLoading, setAnalysisLoading] = useState(true);
  const [selectedContinuousIndex, setSelectedContinuousIndex] = useState(null);
  const [downloadingArtifact, setDownloadingArtifact] = useState("");
  const [recreating, setRecreating] = useState(false);
  const [cancelling, setCancelling] = useState(false);

  // ── 数据加载 ──────────────────────────────────────────

  const loadAll = useCallback(async () => {
    const requestedTaskId = taskId;
    const requestId = ++requestSequenceRef.current;
    const isCurrent = () => (
      requestSequenceRef.current === requestId && taskIdRef.current === requestedTaskId
    );
    fullLoadInFlightRef.current = true;
    setError("");
    try {
      const results = await Promise.allSettled([
        getTask(requestedTaskId),
        getTaskEvents(requestedTaskId),
        getTaskArtifacts(requestedTaskId),
        listTaskDiagnoses(requestedTaskId),
        getTaskAttempts(requestedTaskId),
        getTaskAnalysisJobs(requestedTaskId),
      ]);
      if (!isCurrent()) return;
      const [taskResp, eventResp, artifactResp, diagnosisList, attemptList, jobList] = results.map(
        (r) => (r.status === "fulfilled" ? r.value : null)
      );
      const failedNames = ["task", "events", "artifacts", "diagnoses", "attempts", "analysis-jobs"]
        .filter((_, i) => results[i].status === "rejected");
      if (failedNames.length > 0 && failedNames.length < results.length) {
        console.warn("部分数据加载失败:", failedNames.join(", "));
      }
      if (!taskResp) {
        setError("无法加载任务数据");
        setLoading(false);
        return;
      }
      setTask(taskResp);
      setEvents(eventResp || []);
      setArtifacts(artifactResp || []);
      setAttempts(attemptList || []);
      setAnalysisJobs(jobList || []);

      // 内联加载分析产物
      const resp = (artifactResp || []).filter(isArtifactAvailable);
      setAnalysisLoading(true);
      const hasTop = resp.some((item) => item.artifact_type === "top_json");
      const hasFlameJson = resp.some((item) => item.artifact_type === "flamegraph_json");
      const hasSvg = resp.some((item) => item.artifact_type === "flamegraph_svg");
      const hasJavaHtml = resp.some((item) => item.artifact_type === "java_flamegraph_html");
      const next = {
        top: [],
        topSource: "",
        svg: "",
        derivedTree: null,
        hasFlameJson: false,
        hasJavaHtml: false,
      };
      if (hasTop) {
        try {
          next.top = await getTaskArtifactContent(requestedTaskId, "top_json");
          next.topSource = "top_json";
        } catch {
          next.top = [];
        }
        if (!isCurrent()) return;
      }
      if (hasFlameJson) { next.hasFlameJson = true; }
      if (hasSvg && !hasFlameJson) {
        try {
          const c = await getTaskArtifactContent(requestedTaskId, "flamegraph_svg");
          next.svg = c.text || "";
          next.derivedTree = extractFlamegraphTreeFromSvg(next.svg);
          if (next.top.length === 0) {
            next.top = extractTopFunctionsFromSvg(next.svg);
            if (next.top.length > 0) next.topSource = "flamegraph_svg";
          }
        } catch {
          next.svg = "";
        }
        if (!isCurrent()) return;
      }
      if (hasJavaHtml) { next.hasJavaHtml = true; }
      setAnalysis(next);
      setAnalysisLoading(false);

      setDiagnoses(diagnosisList || []);
      if (diagnosisList?.[0]?.id) {
        try {
          const nextDiagnosis = await getDiagnosis(diagnosisList[0].id);
          if (isCurrent()) setDiagnosis(nextDiagnosis);
        } catch {
          if (isCurrent()) setDiagnosis(null);
        }
      } else {
        setDiagnosis(null);
      }
    } catch (err) {
      if (isCurrent()) setError(err.message);
    } finally {
      if (isCurrent()) {
        fullLoadInFlightRef.current = false;
        setLoading(false);
        setAnalysisLoading(false);
      }
    }
  }, [taskId]);

  useEffect(() => {
    requestSequenceRef.current += 1;
    fullLoadInFlightRef.current = false;
    setLoading(true);
    setAnalysisLoading(true);
    setTask(null);
    setEvents([]);
    setArtifacts([]);
    setAttempts([]);
    setAnalysisJobs([]);
    setDiagnoses([]);
    setDiagnosis(null);
    void loadAll();
    return () => {
      requestSequenceRef.current += 1;
      fullLoadInFlightRef.current = false;
    };
  }, [loadAll]);

  const refreshActiveTask = useCallback(async () => {
    if (fullLoadInFlightRef.current) return;
    const requestedTaskId = taskId;
    const requestId = ++requestSequenceRef.current;
    const [taskResult, eventResult] = await Promise.allSettled([
      getTask(requestedTaskId),
      getTaskEvents(requestedTaskId),
    ]);
    if (requestSequenceRef.current !== requestId || taskIdRef.current !== requestedTaskId) return;
    if (taskResult.status === "rejected") {
      setError(taskResult.reason?.message || "任务状态刷新失败");
      return;
    }
    const nextTask = taskResult.value;
    setTask(nextTask);
    if (eventResult.status === "fulfilled") setEvents(eventResult.value || []);
    if (!isTaskActive(nextTask?.status)) await loadAll();
  }, [loadAll, taskId]);

  // 任务活跃时每 5 秒自动刷新
  const isActive = isTaskActive(task?.status);
  const taskCollector = collectorMeta(task?.collector_type);
  usePolling(refreshActiveTask, { interval: 5000, enabled: isActive });

  // E9：一次性诊断入口收敛为「创建调查 Case」，把本 Task 作为初始证据交给
  // AI 持续调查（复用统一 ResourceRef + EvidenceAttachment 数据入口）。
  async function createInvestigationCase() {
    setCreatingCase(true);
    setError("");
    try {
      const current = task || {};
      const result = await createIncidentCase({
        title: `调查：${current.name || `Task ${taskId}`}`.slice(0, 256),
        problem_description: `基于 Task ${taskId} 的采集数据定位问题并形成可验证结论`,
        recovery_goal: "形成可验证结论与后续处置建议",
        run_mode: "COLLABORATE",
        environment: current.environment || "production",
        target_scope: {
          service_id: current.request_params?.service_id,
          instances: current.request_params?.instances || [],
        },
        initial_tasks: [taskId],
      });
      message.success("已创建调查 Case，进入持续调查工作台");
      navigate(`/cases?caseId=${encodeURIComponent(result.case_id)}`);
    } catch (err) {
      setError(err.message);
    } finally {
      setCreatingCase(false);
    }
  }

  async function sendFeedback(label, causeId) {
    if (!diagnosis?.run?.id) return;
    try {
      await submitDiagnosisFeedback(diagnosis.run.id, {
        predicted_cause_id: causeId || "insufficient_data",
        feedback_label: label,
      });
      message.success("反馈已记录");
    } catch (err) {
      setError(err.message);
    }
  }

  async function refreshDiagnosis() {
    if (!diagnosis?.run?.id) return;
    try {
      setDiagnosis(await getDiagnosis(diagnosis.run.id));
    } catch (err) {
      setError(err.message);
    }
  }

  // ── 产物提取 ──────────────────────────────────────────
  const report = diagnosis?.report?.report || {};
  const rankedCauses = diagnosis?.report?.ranked_causes || [];
  const repairPlan = diagnosis?.repair_plan;
  const toolResults = diagnosis?.tool_results || [];
  const topCause = rankedCauses[0];
  const availableArtifacts = artifacts.filter(isArtifactAvailable);
  const unavailableVisuals = unavailableVisualArtifacts(artifacts);
  const topArtifact = availableArtifacts.find((item) => item.artifact_type === "top_json");
  const flameArtifact = artifacts.find(
    (item) =>
      isArtifactAvailable(item) && (
        item.artifact_type === "flamegraph_svg" ||
        item.artifact_type === "flamegraph_json" ||
        item.artifact_type === "java_flamegraph_html"
      )
  );
  const javaHtmlArtifact = availableArtifacts.find((item) => item.artifact_type === "java_flamegraph_html");
  const memoryArtifact = availableArtifacts.find((item) => item.artifact_type === "memory_json");
  const pprofArtifact = availableArtifacts.find((item) => item.artifact_type === "pprof_raw");
  const sysMetricsArtifact = availableArtifacts.find((item) => item.artifact_type === "sys_metrics");
  const ebpfArtifact = availableArtifacts.find((item) => item.artifact_type === "ebpf_metrics");
  const suggestionArtifact = availableArtifacts.find(
    (item) => item.artifact_type === "suggestions_md"
  );
  const continuousSummary = availableArtifacts.find(
    (item) => item.artifact_type === "continuous_summary"
  );
  const continuousWindowArtifacts = artifacts.filter(
    (item) => item.artifact_type === "continuous_window"
  );
  const continuousWindows =
    continuousSummary?.metadata?.windows?.length > 0
      ? continuousSummary.metadata.windows
      : continuousWindowArtifacts
          .map((item, index) => ({
            window_index: item.metadata?.window_index ?? index,
            start_ts: item.metadata?.start_ts,
            end_ts: item.metadata?.end_ts,
            ok: isArtifactAvailable(item) && (item.metadata?.ok ?? true),
            reason: isArtifactAvailable(item)
              ? item.metadata?.reason || item.filename || item.object_key || ""
              : item.availability_reason || "产物不可用",
          }))
          .sort((a, b) => a.window_index - b.window_index);
  const continuousFlameArtifacts = availableArtifacts.filter(
    (item) => item.artifact_type === "continuous_flamegraph_json"
  );
  const hasFlameOrTop = Boolean(flameArtifact || topArtifact);
  const hasTopData = analysis.top.length > 0;
  const hasContinuousAnalysis = continuousFlameArtifacts.length > 0;
  const hasPrimaryAnalysis = Boolean(hasFlameOrTop || ebpfArtifact || hasContinuousAnalysis);
  const hasDedicatedVisualization = Boolean(
    sysMetricsArtifact || memoryArtifact || pprofArtifact,
  );
  const artifactTypes = artifacts.map((item) => item.artifact_type);
  const presentedToolResults = toolResults.map((item) => {
    const effectiveStatus = effectiveToolStatus(
      item,
      task?.collector_type,
      artifactTypes,
    );
    return {
      ...item,
      effectiveStatus,
      statusPresentation: TOOL_STATUS[effectiveStatus] || {
        label: effectiveStatus,
        color: "default",
        severity: "neutral",
      },
      readableSummary: toolResultSummary(item, effectiveStatus),
    };
  });
  const evidenceOverview = presentedToolResults.reduce(
    (result, item) => {
      const severity = item.statusPresentation.severity;
      if (severity === "available") result.available += 1;
      else if (severity === "missing") result.missing += 1;
      else result.neutral += 1;
      return result;
    },
    { available: 0, missing: 0, neutral: 0 },
  );
  const historicalFlamegraphMismatch =
    analysis.topSource === "flamegraph_svg" &&
    toolResults.some(
      (item) =>
        item.tool_name === "get_flamegraph_top" &&
        String(item.status || "").toLowerCase() === "missing",
    );

  useEffect(() => {
    if (selectedContinuousIndex === null && continuousWindows.length > 0) {
      setSelectedContinuousIndex(continuousWindows[0].window_index);
    }
  }, [continuousWindows, selectedContinuousIndex]);

  async function downloadArtifact(record) {
    const key = `${record.artifact_type}:${record.metadata?.window_index ?? ""}`;
    setDownloadingArtifact(key);
    try {
      const params = record.metadata?.window_index === undefined
        ? {}
        : { index: record.metadata.window_index };
      const { blob, filename } = await downloadTaskArtifact(taskId, record.artifact_type, params);
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      message.error(`下载失败：${err.message}`);
    } finally {
      setDownloadingArtifact("");
    }
  }

  function recreateTask() {
    if (!task) return;
    Modal.confirm({
      title: `重新采集 ${taskCollector.label}？`,
      content: `将在 ${task.agent_id} 上对 PID ${task.target_pid} 使用相同参数创建一个新任务，原任务和产物不会被修改。`,
      okText: "创建并打开",
      cancelText: "取消",
      onOk: async () => {
        setRecreating(true);
        try {
          const response = await retryTask(task.id, {
            name: `重采集：${taskDisplayName(task)}`,
          });
          message.success("新任务已创建");
          navigate(`/task/${response.task_id}`);
        } catch (err) {
          message.error(`重新采集失败：${err.message}`);
        } finally {
          setRecreating(false);
        }
      },
    });
  }

  function cancelCurrentTask() {
    if (!task || !isActive) return;
    Modal.confirm({
      title: "确认取消当前任务？",
      content: "任务会立即进入 CANCELLED 终态；Agent 的迟到结果将被安全忽略。",
      okText: "确认取消",
      okType: "danger",
      cancelText: "继续等待",
      onOk: async () => {
        setCancelling(true);
        try {
          await cancelTask(task.id);
          message.success("任务已取消");
          await loadAll();
        } catch (err) {
          message.error(`取消失败：${err.message}`);
        } finally {
          setCancelling(false);
        }
      },
    });
  }

  const artifactColumns = [
    {
      title: "类型",
      dataIndex: "artifact_type",
      width: 140,
      render: (value) => {
        const colors = {
          flamegraph_json: "blue",
          flamegraph_svg: "blue",
          java_flamegraph_html: "magenta",
          top_json: "green",
          suggestions_md: "orange",
          memory_json: "volcano",
          pprof_raw: "geekblue",
          ebpf_metrics: "green",
          ebpf_raw: "lime",
          raw: "default",
          continuous_window: "cyan",
          continuous_summary: "cyan",
          continuous_flamegraph_json: "cyan",
          continuous_flamegraph_svg: "cyan",
          continuous_top_json: "cyan",
          sys_metrics: "purple",
          java_profile_jfr: "magenta",
        };
        return <Tag color={colors[value] || "default"}>{value}</Tag>;
      },
    },
    {
      title: "文件",
      dataIndex: "filename",
      ellipsis: true,
      render: (value, record) =>
        value || record.object_key || record.local_path || "-",
    },
    {
      title: "大小",
      dataIndex: "size_bytes",
      width: 100,
      render: (v) => (v ? `${(v / 1024).toFixed(1)} KB` : "-"),
    },
    {
      title: "可用性",
      width: 120,
      render: (_, record) => {
        const available = isArtifactAvailable(record);
        const label = available ? "可用" : "文件缺失";
        const tag = <Tag color={available ? "green" : "red"}>{label}</Tag>;
        return record.availability_reason
          ? <Tooltip title={record.availability_reason}>{tag}</Tooltip>
          : tag;
      },
    },
    {
      title: "操作",
      width: 100,
      render: (_, record) => {
        const key = `${record.artifact_type}:${record.metadata?.window_index ?? ""}`;
        return (
          <Button
            type="link"
            size="small"
            icon={<DownloadOutlined />}
            loading={downloadingArtifact === key}
            disabled={!isArtifactAvailable(record)}
            title={!isArtifactAvailable(record) ? record.availability_reason || "产物文件不可用" : undefined}
            onClick={() => downloadArtifact(record)}
          >
            下载
          </Button>
        );
      },
    },
  ];

  // ── 加载骨架屏 ────────────────────────────────────────

  const FLAMEGRAPH_HEIGHT = FLAMEGRAPH.defaultHeight;

  if (loading) {
    return (
      <div className={`${styles.page} ${styles.loadingPage}`}>
        <Skeleton.Input active size="small" style={{ width: 200 }} />
        <div className={styles.skeletonCard}>
          <Skeleton active paragraph={{ rows: 4 }} />
        </div>
        <div className={styles.skeletonCard}>
          <Skeleton active paragraph={{ rows: 3 }} />
        </div>
        <div className={styles.skeletonCard}>
          <Skeleton.Input active block style={{ height: FLAMEGRAPH_HEIGHT, borderRadius: 8 }} />
        </div>
      </div>
    );
  }

  // ── 主渲染 ────────────────────────────────────────────

  return (
    <div className={styles.page}>
      {/* 页面标题 + 返回 + 自动刷新指示 */}
      <header className={styles.pageHeader}>
        <div className={styles.pageHeaderMain}>
          <Button
            icon={<ArrowLeftOutlined />}
            type="text"
            size="small"
            onClick={() => navigate("/tasks")}
          >
            返回任务面板
          </Button>
          <Typography.Title level={4} style={{ margin: 0 }}>
            任务详情
          </Typography.Title>
        </div>
        <div className={styles.headerActions}>
          {isActive && <Tag color="blue">自动刷新中（任务运行中）</Tag>}
          {isActive && (
            <Button
              size="small"
              danger
              icon={<StopOutlined />}
              loading={cancelling}
              onClick={cancelCurrentTask}
            >
              取消任务
            </Button>
          )}
          {task && (
            <Button
              size="small"
              type="primary"
              icon={<RobotOutlined />}
              loading={creatingCase}
              disabled={isActive}
              onClick={createInvestigationCase}
              title={isActive ? "任务完成后可将采集结果加入 AI 调查" : "以当前任务和产物创建持续调查"}
            >
              加入 AI 调查
            </Button>
          )}
          {task && (
            <Button
              size="small"
              icon={<RedoOutlined />}
              loading={recreating}
              disabled={isActive}
              onClick={recreateTask}
              title={isActive ? "当前任务结束后才能重新采集" : "使用相同参数创建新任务"}
            >
              使用相同参数重新采集
            </Button>
          )}
        </div>
      </header>

      <ErrorAlert error={error} style={{ marginBottom: 0 }} onClose={() => setError("")} />

      {/* 任务基本信息 */}
      {task && (
        <>
          <Card size="small">
            <Descriptions column={{ xs: 1, sm: 2, md: 2, lg: 4 }} size="small">
              <Descriptions.Item label="任务 ID">
                <Typography.Text copyable={{ text: task.id }} style={{ fontSize: 12 }}>
                  {task.id}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <StatusTag status={task.status} />
              </Descriptions.Item>
              <Descriptions.Item label="采集状态">
                <Tag>{task.collection_status || task.status}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="分析状态">
                <Tag>{task.analysis_status || "WAITING"}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="名称">
                <Space direction="vertical" size={0}>
                  <Typography.Text>{taskDisplayName(task)}</Typography.Text>
                  {taskDisplayInfo(task).normalized && (
                    <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                      原始名称存在编码异常，当前名称由采集类型和目标自动生成
                    </Typography.Text>
                  )}
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="Agent">{task.agent_id}</Descriptions.Item>
              <Descriptions.Item label="PID">{task.target_pid}</Descriptions.Item>
              <Descriptions.Item label="采集器">
                <Tag color={taskCollector.color}>{taskCollector.label}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="采样率">{task.sample_rate} Hz</Descriptions.Item>
              <Descriptions.Item label="采样时长">{task.duration_sec}s</Descriptions.Item>
            </Descriptions>
          </Card>
          {(attempts.length > 0 || analysisJobs.length > 0) && (
            <Card title="执行与分析记录" size="small">
              <Table
                size="small"
                pagination={false}
                rowKey={(row) => row.attempt_id}
                dataSource={attempts}
                columns={[
                  { title: "尝试", dataIndex: "attempt_no", width: 72 },
                  { title: "Attempt ID", dataIndex: "attempt_id", ellipsis: true },
                  { title: "Agent", dataIndex: "agent_id", ellipsis: true },
                  { title: "状态", dataIndex: "status", render: (value) => <Tag>{value}</Tag> },
                  { title: "结果", dataIndex: "result_message", ellipsis: true },
                ]}
              />
              {analysisJobs.length > 0 && (
                <Table
                  style={{ marginTop: SPACING.md }}
                  size="small"
                  pagination={false}
                  rowKey={(row) => row.analysis_job_id}
                  dataSource={analysisJobs}
                  columns={[
                    { title: "分析流水线", dataIndex: "pipeline" },
                    { title: "状态", dataIndex: "status", render: (value) => <Tag>{value}</Tag> },
                    { title: "重试", dataIndex: "retry_count", width: 72 },
                    { title: "Worker", dataIndex: "lease_owner", ellipsis: true },
                    { title: "错误", dataIndex: "error_message", ellipsis: true },
                  ]}
                />
              )}
            </Card>
          )}
          <Alert
            type={
              task.status === "FAILED"
                ? "error"
                : task.status === "CANCELLED"
                ? "warning"
                : taskCollector.flamegraph
                ? "success"
                : "info"
            }
            showIcon
            message={
              task.status === "CANCELLED"
                ? "任务已取消"
                : `预期可视化：${taskCollector.resultLabel}`
            }
            description={
              task.status === "FAILED"
                ? `任务失败原因：${task.status_reason || "未提供失败原因"}`
                : task.status === "CANCELLED"
                ? task.status_reason || "任务已由用户取消，迟到结果不会覆盖此状态。"
                : `${taskCollector.description}${task.status_reason ? ` 当前状态：${task.status_reason}` : ""}`
            }
            action={
              ["FAILED", "CANCELLED"].includes(task.status) ? (
                <Button size="small" icon={<RedoOutlined />} onClick={recreateTask}>
                  重新采集
                </Button>
              ) : null
            }
          />
        </>
      )}

      {/* 状态时间线 + 产物 并排 */}
      <Row gutter={SPACING.lg}>
        <Col xs={24} lg={12}>
          <Card title="状态时间线" size="small">
            {events.length > 0 ? (
              <Timeline
                items={events.map((event) => ({
                  color: event.to_status === "DONE"
                    ? "green"
                    : event.to_status === "FAILED"
                    ? "red"
                    : event.to_status === "CANCELLED"
                    ? "orange"
                    : "blue",
                  children: (
                    <Space direction="vertical" size={0}>
                      <Typography.Text strong>
                        <StatusTag status={event.to_status} />
                      </Typography.Text>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {event.reason}
                      </Typography.Text>
                    </Space>
                  ),
                }))}
              />
            ) : (
              <Empty description="暂无状态事件" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="产物" size="small">
            {artifacts.length > 0 ? (
              <Table
                rowKey={(record, index) => `${record.artifact_type || "artifact"}-${index}`}
                columns={artifactColumns}
                dataSource={artifacts}
                pagination={false}
                size="small"
                scroll={{ x: 400 }}
              />
            ) : (
              <Empty description="暂无分析产物" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>
        </Col>
      </Row>

      {/* 火焰图 + TopN 并排 ⭐ 核心区域 */}
      <Card
        title={
          <Space>
            <FileTextOutlined style={{ color: COLORS.primary }} />
            核心可视化 · {taskCollector.resultLabel}
            {isActive && <Spin size="small" />}
          </Space>
        }
        size="small"
      >
        {unavailableVisuals.length > 0 && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message="部分可视化产物不可用"
            description={unavailableVisuals.map((item) => (
              <div key={`${item.artifact_type}:${item.metadata?.window_index ?? ""}`}>
                {item.artifact_type}
                {item.metadata?.window_index !== undefined ? `（窗口 ${item.metadata.window_index}）` : ""}
                ：{item.availability_reason || "产物文件不存在"}
              </div>
            ))}
          />
        )}
        {hasPrimaryAnalysis ? (
          hasFlameOrTop ? (
            <Row gutter={SPACING.lg}>
              {/* 火焰图 */}
              <Col xs={24} lg={hasTopData ? 16 : 24}>
                <Typography.Text strong style={{ display: "block", marginBottom: 8 }}>
                  🔥 火焰图
                </Typography.Text>
                {analysis.hasFlameJson ? (
                  <FlamegraphViewer
                    ref={flameRef}
                    taskId={taskId}
                    height={FLAMEGRAPH_HEIGHT}
                  />
                ) : analysis.derivedTree ? (
                  <FlamegraphViewer
                    ref={flameRef}
                    data={analysis.derivedTree}
                    height={FLAMEGRAPH_HEIGHT}
                  />
                ) : analysis.hasJavaHtml ? (
                  <JavaFlameViewer taskId={taskId} artifact={javaHtmlArtifact} />
                ) : analysis.svg ? (
                  <Space direction="vertical" size={8} style={{ width: "100%" }}>
                    <Alert
                      type="info"
                      showIcon
                      message="交互式 SVG 火焰图"
                      description="点击帧可逐层放大；使用图内 Search 搜索函数，Reset Zoom 返回全局视图。脚本运行在无同源权限的隔离沙箱中。"
                    />
                    <SandboxedArtifactFrame
                      html={analysis.svg}
                      title="火焰图"
                      style={{
                        width: "100%",
                        height: FLAMEGRAPH_HEIGHT,
                        border: `1px solid ${COLORS.border}`,
                        borderRadius: 6,
                        background: "#fff",
                      }}
                    />
                  </Space>
                ) : analysisLoading ? (
                  <Skeleton.Input active block style={{ height: FLAMEGRAPH_HEIGHT, borderRadius: 8 }} />
                ) : (
                  <Empty description="暂无火焰图，请等待分析完成" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                )}
              </Col>

              {/* TopN 柱状图 */}
              {hasTopData && (
                <Col xs={24} lg={8}>
                  <Space style={{ display: "flex", marginBottom: 8 }}>
                    <Typography.Text strong>
                      📊 热点 Top {Math.min(analysis.top.length, 10)}
                    </Typography.Text>
                    {analysis.topSource === "flamegraph_svg" && (
                      <Tooltip title="由 SVG 中的采样标题解析，重新归因时同样会作为结构化 TopN 证据">
                        <Tag color="blue">从 SVG 提取</Tag>
                      </Tooltip>
                    )}
                  </Space>
                  {analysisLoading || analysis.top.length > 0 ? (
                    <>
                      <TopNChart
                        data={analysis.top.slice(0, 10)}
                        loading={analysisLoading}
                        height={FLAMEGRAPH_HEIGHT}
                        onBarClick={(funcName) => {
                          if (flameRef.current) {
                            flameRef.current.search(funcName);
                          }
                        }}
                      />
                      <Typography.Text
                        type="secondary"
                        style={{ fontSize: 11, display: "block", marginTop: 4, textAlign: "center" }}
                      >
                        {analysis.hasFlameJson
                          ? "点击柱状图 → 火焰图中高亮对应函数"
                          : "SVG 火焰图请使用图内 Search 定位函数"}
                      </Typography.Text>
                    </>
                  ) : (
                    <Empty description="暂无热点函数数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                  )}
                </Col>
              )}
            </Row>
          ) : ebpfArtifact ? (
            <div>
              <Typography.Text strong style={{ display: "block", marginBottom: 8 }}>
                eBPF IO 延迟分布
              </Typography.Text>
              <EBPFHistogramChart taskId={taskId} artifact={ebpfArtifact} />
              <Typography.Text type="secondary" style={{ fontSize: 12, display: "block", marginTop: 8 }}>
                当前任务类型为 eBPF IO 采集，结果以延迟直方图和原始 bpftrace 输出呈现，不会生成 CPU 火焰图。
              </Typography.Text>
            </div>
          ) : (
            <Empty
              description="连续采样任务已生成窗口化火焰图，请在下方“连续采样窗口”选择窗口查看"
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          )
        ) : (
          <Empty
            description={
              isActive
                ? "任务运行中，分析产物将在完成后生成…"
                : hasDedicatedVisualization
                ? `“${taskCollector.resultLabel}”已在下方专属卡片展示`
                : `未生成“${taskCollector.resultLabel}”${task?.status_reason ? `：${task.status_reason}` : ""}`
            }
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          >
            {isActive && <Spin />}
          </Empty>
        )}

        {/* 建议 */}
        {suggestionArtifact && (
          <div style={{ marginTop: 12 }}>
            <Tag color="orange" icon={<BarChartOutlined />}>
              建议已生成: {suggestionArtifact.filename || suggestionArtifact.local_path || suggestionArtifact.object_key}
            </Tag>
          </div>
        )}

        {/* 持续采样窗口 */}
        {continuousWindows.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <Space style={{ width: "100%", justifyContent: "space-between", marginBottom: 8 }}>
              <Typography.Text strong>连续采样窗口</Typography.Text>
              {continuousFlameArtifacts.length > 0 && (
                <Select
                  size="small"
                  style={{ width: 180 }}
                  value={selectedContinuousIndex}
                  onChange={setSelectedContinuousIndex}
                  options={continuousWindows.map((item) => ({
                    value: item.window_index,
                    label: `窗口 ${item.window_index}`,
                  }))}
                />
              )}
            </Space>
            <Table
              rowKey={(record) => record.window_index}
              dataSource={continuousWindows}
              pagination={false}
              size="small"
              style={{ marginTop: 8 }}
              scroll={{ x: 600 }}
              columns={[
                { title: "窗口", dataIndex: "window_index", width: 70 },
                {
                  title: "开始",
                  dataIndex: "start_ts",
                  width: 170,
                  render: (value) =>
                    value
                      ? formatBeijingDateTime(new Date(value * 1000))
                      : "-",
                },
                {
                  title: "结束",
                  dataIndex: "end_ts",
                  width: 170,
                  render: (value) =>
                    value
                      ? formatBeijingDateTime(new Date(value * 1000))
                      : "-",
                },
                {
                  title: "状态",
                  dataIndex: "ok",
                  width: 100,
                  render: (value) => (
                    <Tag color={value ? "green" : "red"}>
                      {value ? "OK" : "FAILED"}
                    </Tag>
                  ),
                },
                { title: "说明", dataIndex: "reason", ellipsis: true },
              ]}
            />
            {continuousFlameArtifacts.length > 0 && selectedContinuousIndex !== null && (
              <div style={{ marginTop: 12 }}>
                <Typography.Text type="secondary" style={{ display: "block", marginBottom: 8 }}>
                  当前窗口火焰图
                </Typography.Text>
                <FlamegraphViewer
                  taskId={taskId}
                  artifactType="continuous_flamegraph_json"
                  artifactIndex={selectedContinuousIndex}
                  height={FLAMEGRAPH_HEIGHT}
                />
              </div>
            )}
          </div>
        )}
      </Card>

      {/* eBPF IO 延迟分布 */}
      {ebpfArtifact && hasFlameOrTop && (
        <Card title="eBPF IO 延迟分布" size="small">
          <EBPFHistogramChart taskId={taskId} artifact={ebpfArtifact} />
        </Card>
      )}

      {/* 内存时间序列 */}
      {memoryArtifact && (
        <Card title="内存分析" size="small">
          <MemoryChart taskId={taskId} artifact={memoryArtifact} />
        </Card>
      )}

      {/* 系统多维指标 */}
      {sysMetricsArtifact && (
        <Card title="系统多维指标" size="small">
          <SysMetricsView taskId={taskId} artifact={sysMetricsArtifact} />
        </Card>
      )}

      {/* Go pprof 状态 */}
      {pprofArtifact && !flameArtifact && (
        <Card title="Go pprof 采集" size="small">
          <Alert
            type="info"
            message="pprof 数据已采集"
            description={`原始 pprof 数据 (${(pprofArtifact.size_bytes / 1024).toFixed(1)} KB) 已保存。使用 go tool pprof 查看或安装 go 后自动生成火焰图。`}
            showIcon
          />
        </Card>
      )}

      {/* 保留旧版规则归因，用于查看已有诊断记录和结构化证据。 */}
      <Card
        className={styles.diagnosisCard}
        title={
          <Space>
            <ExperimentOutlined style={{ color: COLORS.primary }} />
            兼容规则归因
          </Space>
        }
        size="small"
        extra={
          <Space wrap className={styles.diagnosisActions}>
            {diagnoses.length > 0 && <Tag>{diagnoses.length} 次诊断</Tag>}
            <Button
              icon={<RobotOutlined />}
              loading={creatingCase}
              disabled={isActive}
              onClick={createInvestigationCase}
              size="small"
            >
              加入 AI 调查
            </Button>
            <Tooltip title="刷新诊断报告">
              <Button
                aria-label="刷新诊断报告"
                icon={<ReloadOutlined />}
                size="small"
                onClick={refreshDiagnosis}
                disabled={!diagnosis?.run?.id}
              />
            </Tooltip>
          </Space>
        }
      >
        {!diagnosis ? (
          <Empty
            description="暂无兼容规则归因报告，可将当前证据加入 AI 持续调查"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        ) : (
          <Space direction="vertical" size={SPACING.lg} style={{ width: "100%" }}>
            <Descriptions column={{ xs: 1, sm: 2, md: 4 }} size="small">
              <Descriptions.Item label="诊断 ID">
                <Typography.Text copyable={{ text: diagnosis.run?.id }} style={{ fontSize: 12 }}>
                  {diagnosis.run?.id}
                </Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <StatusTag status={diagnosis.run?.status || "UNKNOWN"} />
              </Descriptions.Item>
              <Descriptions.Item label="模型">
                <Tag>{diagnosis.run?.model_name || "-"}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="校验">
                <Tag color={diagnosis.run?.validated ? "green" : "orange"}>
                  {diagnosis.run?.validated ? "通过" : "未通过"}
                </Tag>
              </Descriptions.Item>
            </Descriptions>

            <Alert
              type={historicalFlamegraphMismatch || report.not_enough_evidence ? "warning" : "info"}
              message={
                historicalFlamegraphMismatch
                  ? "历史诊断与当前证据不一致"
                  : report.not_enough_evidence
                  ? "证据不足：以下内容是待验证候选，不是已确认根因"
                  : "规则归因结论"
              }
              description={
                historicalFlamegraphMismatch ? (
                  <Space direction="vertical" size={4}>
                    <Typography.Text>
                      当前已从 flamegraph_svg 还原 {analysis.top.length} 条热点数据；
                      下方诊断生成于 SVG 解析能力启用前，其中“火焰图 TopN 缺失”属于历史结论。
                    </Typography.Text>
                    <Typography.Text type="secondary">
                      原始诊断摘要：{report.summary || diagnosis.run?.summary || "诊断完成"}
                    </Typography.Text>
                    <Typography.Text type="secondary">
                      重新运行诊断后，归因服务会使用当前热点证据生成新结论。
                    </Typography.Text>
                  </Space>
                ) : (
                  report.summary || diagnosis.run?.summary || "诊断完成"
                )
              }
              showIcon
            />

            <section className={styles.evidenceSummary}>
              <Typography.Text strong>本次归因实际使用了什么</Typography.Text>
              <Space direction="vertical" size={8} style={{ width: "100%", marginTop: 8 }}>
                <Space wrap>
                  <Tag color="green">已获取 {evidenceOverview.available}</Tag>
                  <Tag color={evidenceOverview.missing > 0 ? "red" : "default"}>
                    应有但缺失 {evidenceOverview.missing}
                  </Tag>
                  <Tag>不适用或可选 {evidenceOverview.neutral}</Tag>
                </Space>
                <Typography.Text type="secondary">
                  当前任务使用「{taskCollector.label}」。只有该采集器预期产生但没有产生的数据，
                  才会计为缺失；其他采集器的数据不会被当成任务失败。
                </Typography.Text>
              </Space>
            </section>

            {rankedCauses.length > 0 && (
              <Table
                rowKey={(record) => record.cause_id}
                dataSource={rankedCauses}
                pagination={false}
                size="small"
                scroll={{ x: 820 }}
                expandable={{
                  expandedRowRender: (record) => (
                    <Row gutter={[16, 8]}>
                      <Col xs={24} md={12}>
                        <Typography.Text strong>尚未确认</Typography.Text>
                        <ul style={{ margin: "6px 0 0", paddingLeft: 20 }}>
                          {(record.uncertainties || []).length > 0
                            ? record.uncertainties.map((item) => <li key={item}>{item}</li>)
                            : <li>无额外不确定项</li>}
                        </ul>
                      </Col>
                      <Col xs={24} md={12}>
                        <Typography.Text strong>建议如何验证</Typography.Text>
                        <ol style={{ margin: "6px 0 0", paddingLeft: 20 }}>
                          {(record.verification_steps || []).length > 0
                            ? record.verification_steps.map((item) => <li key={item}>{item}</li>)
                            : <li>重新采集相同场景并对比结果</li>}
                        </ol>
                      </Col>
                    </Row>
                  ),
                  rowExpandable: (record) => (
                    (record.uncertainties || []).length > 0
                    || (record.verification_steps || []).length > 0
                  ),
                }}
                columns={[
                  {
                    title: "候选原因",
                    dataIndex: "cause_id",
                    width: 190,
                    render: (value) => (
                      <Space direction="vertical" size={0}>
                        <Typography.Text strong>{causeLabel(value)}</Typography.Text>
                        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                          {value}
                        </Typography.Text>
                      </Space>
                    ),
                  },
                  {
                    title: "证据支持度",
                    dataIndex: "confidence",
                    width: 165,
                    render: (value) => {
                      const presentation = confidencePresentation(value);
                      return (
                        <Space direction="vertical" size={0} style={{ width: "100%" }}>
                          <Progress
                            percent={Math.round((value || 0) * 100)}
                            size="small"
                            strokeColor={
                              (value || 0) > 0.7
                                ? COLORS.success
                                : (value || 0) > 0.4
                                ? COLORS.warning
                                : COLORS.error
                            }
                          />
                          <Tag color={presentation.color} style={{ width: "fit-content" }}>
                            {presentation.label}
                          </Tag>
                        </Space>
                      );
                    },
                  },
                  {
                    title: "当前判断",
                    dataIndex: "claim",
                    render: (value) => (
                      <Typography.Paragraph
                        ellipsis={{ rows: 2, expandable: true, symbol: "展开" }}
                        style={{ marginBottom: 0 }}
                      >
                        {value}
                      </Typography.Paragraph>
                    ),
                  },
                  {
                    title: "依据",
                    dataIndex: "evidence_refs",
                    width: 210,
                    render: (refs = []) => (
                      <Space size={[2, 2]} wrap>
                        {refs.map((ref) => (
                          <Tooltip title={ref} key={ref}>
                            <Tag color="blue" style={{ fontSize: 10, margin: 0 }}>
                              {evidenceRefLabel(ref)}
                            </Tag>
                          </Tooltip>
                        ))}
                      </Space>
                    ),
                  },
                ]}
              />
            )}
            {rankedCauses.length === 0 && report.not_enough_evidence && (
              <Empty
                description="当前没有能够被证据支持的候选原因"
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
            )}

            <Space wrap>
              <Button
                aria-label="标记诊断为正确"
                icon={<CheckOutlined />}
                size="small"
                onClick={() => sendFeedback("correct", topCause?.cause_id)}
                disabled={!topCause}
              >
                正确
              </Button>
              <Button
                aria-label="标记诊断为部分正确"
                icon={<MinusOutlined />}
                size="small"
                onClick={() => sendFeedback("partial", topCause?.cause_id)}
                disabled={!topCause}
              >
                部分正确
              </Button>
              <Button
                aria-label="标记诊断为错误"
                icon={<CloseOutlined />}
                size="small"
                danger
                onClick={() => sendFeedback("wrong", topCause?.cause_id)}
                disabled={!topCause}
              >
                错误
              </Button>
            </Space>

            <Collapse
              ghost
              items={[
                {
                  key: "tools",
                  label: `结构化证据检查 (${toolResults.length})`,
                  children: presentedToolResults.length > 0 ? (
                    <Table
                      rowKey={(record, index) => `${record.tool_name}-${index}`}
                      dataSource={presentedToolResults}
                      pagination={false}
                      size="small"
                      scroll={{ x: 820 }}
                      columns={[
                        {
                          title: "检查项",
                          dataIndex: "tool_name",
                          width: 180,
                          render: (value) => (
                            <Space direction="vertical" size={0}>
                              <Typography.Text strong>
                                {TOOL_LABELS[value] || value}
                              </Typography.Text>
                              <Typography.Text type="secondary" style={{ fontSize: 11 }}>
                                {value}
                              </Typography.Text>
                            </Space>
                          ),
                        },
                        {
                          title: "状态",
                          dataIndex: "statusPresentation",
                          width: 145,
                          render: (value) => <Tag color={value.color}>{value.label}</Tag>,
                        },
                        {
                          title: "含义",
                          dataIndex: "readableSummary",
                          render: (value, record) => (
                            <Space direction="vertical" size={6} style={{ width: "100%" }}>
                              <Typography.Text>{value}</Typography.Text>
                              <Collapse
                                ghost
                                size="small"
                                items={[{
                                  key: "raw",
                                  label: "查看原始结构化结果",
                                  children: (
                                    <pre className={styles.rawDiagnosisResult}>
                                      {JSON.stringify({
                                        evidence_ref: record.evidence_ref,
                                        input: record.input,
                                        output: record.output,
                                        error_message: record.error_message,
                                      }, null, 2)}
                                    </pre>
                                  ),
                                }]}
                              />
                            </Space>
                          ),
                        },
                      ]}
                    />
                  ) : (
                    <Empty description="无工具调用记录" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                  ),
                },
                {
                  key: "repair",
                  label: "修复计划",
                  children: repairPlan ? (
                    <Space direction="vertical" style={{ width: "100%" }}>
                      <Alert
                        type="info"
                        showIcon
                        message="这是后续验证方案，不是系统已经执行的自动修复"
                        description="涉及重新采集或环境变更的动作需要用户确认；当前报告不会自行修改目标进程。"
                      />
                      <Space wrap>
                        <Tag
                          color={repairPlan.risk_level === "safe_auto" ? "green" : "orange"}
                        >
                          {repairLabel(repairPlan.risk_level)}
                        </Tag>
                        <Tag>{repairLabel(repairPlan.status)}</Tag>
                        {repairPlan.requires_user_confirm && (
                          <Tag color="orange">执行前需要人工确认</Tag>
                        )}
                      </Space>
                      <Table
                        rowKey="action_id"
                        dataSource={repairPlan.actions || []}
                        pagination={false}
                        size="small"
                        scroll={{ x: 600 }}
                        columns={[
                          {
                            title: "建议动作",
                            dataIndex: "action_type",
                            width: 180,
                            render: (value) => repairLabel(value),
                          },
                          {
                            title: "风险",
                            dataIndex: "risk_level",
                            width: 120,
                            render: (value) => <Tag>{repairLabel(value)}</Tag>,
                          },
                          {
                            title: "状态",
                            dataIndex: "status",
                            width: 100,
                            render: (value) => repairLabel(value),
                          },
                          {
                            title: "为什么需要",
                            dataIndex: "description",
                            render: (value) => (
                              <Typography.Paragraph
                                ellipsis={{ rows: 2, expandable: true, symbol: "展开" }}
                                style={{ marginBottom: 0 }}
                              >
                                {value}
                              </Typography.Paragraph>
                            ),
                          },
                          {
                            title: "执行结果",
                            dataIndex: "result",
                            width: 160,
                            render: (value) => value || "尚未执行",
                          },
                        ]}
                      />
                    </Space>
                  ) : (
                    <Empty description="暂无修复计划" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                  ),
                },
              ]}
            />
          </Space>
        )}
      </Card>

    </div>
  );
}
