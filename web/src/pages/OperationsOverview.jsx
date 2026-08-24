import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Collapse,
  Empty,
  Input,
  Progress,
  Row,
  Skeleton,
  Space,
  Switch,
  Tag,
  Timeline,
  Tooltip,
  Typography,
  message,
} from "antd";
import {
  AlertOutlined,
  ArrowRightOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  PauseCircleOutlined,
  ReloadOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import {
  getAgentRuntimeConfig,
  healthz,
  listAgents,
  listAuditLogs,
  listIncidentCases,
  listSystemControls,
  listTasks,
  setAgentCollectionMode,
} from "../api/client";
import ErrorAlert from "../components/ErrorAlert";
import useSSE from "../hooks/useSSE";
import { caseStatus, eventType, isActiveCase } from "../utils/opsMappings";
import styles from "./OperationsOverview.module.css";
import { formatBeijingDateTime, relativeBeijingTime } from "../utils/time";

const CHECK_LABELS = {
  server: "Server",
  database: "Database",
  storage: "Storage",
  analyzer: "Analyzer",
  runtime: "Pi Runtime",
  worker: "Worker",
};

const FLAG_HELP = {
  agent_auto_read_low: "关闭时，Agent 可以提出低风险采集计划，但不会在无人确认时自动创建任务。",
  agent_mcp_enabled: "关闭时，Agent 不会连接外部 MCP 数据源。",
  agent_skills_enabled: "关闭时，Runtime 仅使用内置白名单工具，不加载扩展 Skills。",
  agent_cluster_fanout_enabled: "开启时，声明为集群范围的诊断动作会按冻结的成员快照展开到多个 Worker；关闭只适合单节点兼容运行。",
};

function asItems(value) {
  return Array.isArray(value) ? value : value?.items || [];
}

function switchEnabled(value) {
  return value === true || value === 1 || value === "1" || value === "true" || value === "enabled";
}

function RelativeTime({ value }) {
  if (!value) return <span>—</span>;
  const time = new Date(value);
  const relative = relativeBeijingTime(value);
  return <time dateTime={time.toISOString()} title={formatBeijingDateTime(value)}>{relative}</time>;
}

function HealthCheck({ label, healthy, detail }) {
  return (
    <div className={styles.healthCheck}>
      <span className={healthy ? styles.healthOk : styles.healthBad} aria-hidden="true" />
      <div><strong>{label}</strong><small>{healthy ? "正常" : "需要检查"}{detail ? ` · ${detail}` : ""}</small></div>
      <Tag color={healthy ? "success" : "error"}>{healthy ? "HEALTHY" : "DEGRADED"}</Tag>
    </div>
  );
}

function dependencyHealthy(check) {
  return check?.status === "ok" || check?.status === "disabled";
}

function dependencyDetail(check, kind) {
  if (check?.status === "disabled") {
    return kind === "storage" ? "未启用：使用本地 Artifact 目录" : "未启用";
  }
  return check?.error_code || "";
}

function CapabilityFlag({ label, value, help }) {
  const enabled = switchEnabled(value);
  return (
    <Tooltip title={help} placement="topLeft">
      <div className={styles.capabilityRow} tabIndex={0}>
        <span>{label}</span>
        <Tag color={enabled ? "blue" : "default"}>{enabled ? "开启" : "关闭"}</Tag>
      </div>
    </Tooltip>
  );
}

export default function OperationsOverview() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const [activeCaseQuery, setActiveCaseQuery] = useState("");
  const [collectionUpdating, setCollectionUpdating] = useState("");
  const [data, setData] = useState({ health: null, runtime: null, controls: [], agents: [], cases: [], tasks: [], logs: [] });

  const load = useCallback(async ({ quiet = false } = {}) => {
    quiet ? setRefreshing(true) : setLoading(true);
    setError(null);
    const requests = await Promise.allSettled([
      healthz(),
      getAgentRuntimeConfig(),
      listSystemControls(),
      listAgents(),
      listIncidentCases({ limit: 100 }),
      listTasks({ limit: 200, sort_by: "created_at", sort_order: "desc" }),
      listAuditLogs(),
    ]);
    const [health, runtime, controls, agents, cases, tasks, logs] = requests;
    setData((current) => ({
      health: health.status === "fulfilled" ? health.value : current.health,
      runtime: runtime.status === "fulfilled" ? runtime.value : current.runtime,
      controls: controls.status === "fulfilled" ? asItems(controls.value) : current.controls,
      agents: agents.status === "fulfilled" ? asItems(agents.value) : current.agents,
      cases: cases.status === "fulfilled" ? asItems(cases.value) : current.cases,
      tasks: tasks.status === "fulfilled" ? asItems(tasks.value) : current.tasks,
      logs: logs.status === "fulfilled" ? asItems(logs.value) : current.logs,
    }));
    const failures = requests.filter((item) => item.status === "rejected").map((item) => item.reason);
    if (failures.length) setError(failures[0]);
    setLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => { void load(); }, [load]);
  const { connected, connectionState } = useSSE({
    onTaskChanged: () => load({ quiet: true }),
    onAgentStatus: () => load({ quiet: true }),
  });

  const derived = useMemo(() => {
    const effectiveWorkers = data.agents.filter((agent) => agent.id !== "demo-worker");
    const online = effectiveWorkers.filter((agent) => agent.status === "ONLINE");
    const activeCases = data.cases.filter((item) => isActiveCase(item.state));
    const waitingApproval = activeCases.filter((item) => item.state === "WAITING_APPROVAL" || item.summary?.need_you?.required);
    const failed = data.tasks.filter((task) => task.status === "FAILED");
    const pending = data.tasks.filter((task) => task.status === "PENDING").length;
    const running = data.tasks.filter((task) => ["RUNNING", "UPLOADING", "ANALYZING"].includes(task.status)).length;
    const normalizedQuery = activeCaseQuery.trim().toLowerCase();
    const searchable = (item) => [
      item.title,
      item.problem_description,
      item.current_finding?.statement,
      item.summary?.current_finding?.statement,
      item.environment,
      item.service_id,
      item.target_scope?.service_id,
      item.state,
      item.case_id,
    ].filter(Boolean).join(" ").toLowerCase();
    const filteredActiveCases = normalizedQuery ? activeCases.filter(searchable) : activeCases;
    return { effectiveWorkers, online, activeCases, filteredActiveCases, waitingApproval, failed, pending, running };
  }, [activeCaseQuery, data]);

  const toggleAgentCollection = useCallback(async (agent, enabled) => {
    setCollectionUpdating(agent.id);
    setError(null);
    try {
      const updated = await setAgentCollectionMode(agent.id, enabled);
      setData((current) => ({ ...current, agents: current.agents.map((item) => item.id === agent.id ? { ...item, ...updated, collection_enabled: enabled } : item) }));
      message.success(`${agent.hostname || agent.id} 已${enabled ? "恢复" : "暂停"}接收新采集任务`);
    } catch (nextError) {
      setError(nextError);
      message.error(nextError.message);
    } finally {
      setCollectionUpdating("");
    }
  }, []);

  if (loading) {
    return <div className={styles.page}><Skeleton active paragraph={{ rows: 16 }} /></div>;
  }

  const checks = data.health?.checks || {};
  const flags = data.runtime?.flags || {};
  const controlsPaused = data.controls.some((item) => item.enabled && /pause|stop|red/i.test(item.control_name || item.name || ""));
  const analyzerHealthy = checks.analyzer?.status === "ok";
  const runtimeHealthy = data.runtime?.ready === true;
  const aiReady = data.runtime?.ai_ready === true || (data.runtime?.mode === "pi" && runtimeHealthy);
  const overallHealthy = data.health?.healthy && runtimeHealthy && derived.online.length > 0;
  const activity = [...data.logs].sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0)).slice(0, 8);

  return (
    <div className={styles.page}>
      <header className={styles.hero}>
        <div>
          <span className={styles.eyebrow}>SUPERVISED AUTONOMY CONTROL PLANE</span>
          <Typography.Title level={1}>运行态势总览</Typography.Title>
          <Typography.Paragraph>用健康、调查、证据与安全边界判断现在是否需要人工介入。</Typography.Paragraph>
        </div>
        <Space wrap>
          <Tag color={connected ? "success" : connectionState === "reconnecting" ? "processing" : "warning"}>
            实时通道：{connected ? "已连接" : connectionState === "reconnecting" ? "正在重连" : "轮询降级"}
          </Tag>
          <Button icon={<ReloadOutlined />} loading={refreshing} onClick={() => load({ quiet: true })}>刷新</Button>
        </Space>
      </header>

      <ErrorAlert error={error} onRetry={() => load()} />

      <section className={styles.signalStrip} aria-label="关键运行指标">
        <div><Badge status={overallHealthy ? "success" : "warning"} /><span>系统</span><strong>{overallHealthy ? "运行正常" : "需要关注"}</strong></div>
        <div><CloudServerOutlined /><span>在线 Worker</span><strong>{derived.online.length} / {derived.effectiveWorkers.length}</strong></div>
        <div><RobotOutlined /><span>活跃 Case</span><strong>{derived.activeCases.length}</strong></div>
        <div><SafetyCertificateOutlined /><span>待审批</span><strong className={derived.waitingApproval.length ? styles.attention : ""}>{derived.waitingApproval.length}</strong></div>
        <div><PauseCircleOutlined /><span>全局控制</span><strong>{controlsPaused ? "已限制" : "未触发"}</strong></div>
      </section>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={15}>
          <Card className={styles.primaryCard} title={<Space><CheckCircleOutlined />系统健康</Space>} extra={<RelativeTime value={new Date().toISOString()} />}>
            <div className={styles.healthGrid}>
              <HealthCheck label={CHECK_LABELS.server} healthy={Boolean(data.health?.healthy)} detail={`v${data.health?.version || "—"}`} />
              <HealthCheck label={CHECK_LABELS.database} healthy={checks.database?.status === "ok"} />
              <HealthCheck label={CHECK_LABELS.storage} healthy={dependencyHealthy(checks.storage)} detail={dependencyDetail(checks.storage, "storage")} />
              <HealthCheck label={CHECK_LABELS.analyzer} healthy={analyzerHealthy || checks.analyzer?.status === "disabled"} detail={checks.analyzer?.status === "disabled" ? "未启用" : `${checks.analyzer?.workers_online ?? 0} worker`} />
              <HealthCheck label={CHECK_LABELS.runtime} healthy={runtimeHealthy} detail={`${data.runtime?.runtime_type || "—"} ${data.runtime?.runtime_version || ""}`} />
              <HealthCheck label={CHECK_LABELS.worker} healthy={derived.online.length > 0} detail={`${derived.online.length} 在线`} />
            </div>
            <Collapse ghost size="small" items={[{ key: "raw", label: "查看原始健康检查结果", children: <pre className={styles.raw}>{JSON.stringify(data.health, null, 2)}</pre> }]} />
          </Card>
        </Col>
        <Col xs={24} xl={9}>
          <Card className={styles.primaryCard} title={<Space><RobotOutlined />Agent 采集能力</Space>} extra={<Tag color={aiReady ? "purple" : "default"}>{aiReady ? "AI 已就绪" : "按部署配置"}</Tag>}>
            <div className={styles.runtimeIdentity}>
              <div><span>Runtime</span><strong>{data.runtime?.runtime_type || "—"}</strong></div>
              <div><span>版本</span><strong>{data.runtime?.runtime_version || flags.pi_runtime_version || "—"}</strong></div>
              <div><span>并发 Case 上限</span><strong>{flags.agent_max_active_cases ?? "—"}</strong></div>
            </div>
            <CapabilityFlag label="Auto READ_LOW" value={flags.agent_auto_read_low} help={FLAG_HELP.agent_auto_read_low} />
            <CapabilityFlag label="MCP" value={flags.agent_mcp_enabled} help={FLAG_HELP.agent_mcp_enabled} />
            <CapabilityFlag label="Skills" value={flags.agent_skills_enabled} help={FLAG_HELP.agent_skills_enabled} />
            <CapabilityFlag label="Cluster Fanout" value={flags.agent_cluster_fanout_enabled} help={FLAG_HELP.agent_cluster_fanout_enabled} />
            <div className={styles.workerDispatchList} aria-label="Worker 采集派发控制">
              <div className={styles.workerDispatchHeading}><strong>Worker 接收新采集</strong><small>暂停不会影响心跳和正在执行的任务</small></div>
              {derived.effectiveWorkers.length ? derived.effectiveWorkers.map((agent) => {
                const enabled = agent.collection_enabled !== false;
                return <div className={styles.workerDispatchRow} key={agent.id}>
                  <span className={styles.workerDispatchIdentity}><span className={agent.status === "ONLINE" ? styles.dispatchOnline : styles.dispatchOffline} /><strong>{agent.hostname || agent.id}</strong><small>{agent.status === "ONLINE" ? "在线" : "离线"} · {enabled ? "可派发" : "已暂停派发"}</small></span>
                  <Switch size="small" checked={enabled} loading={collectionUpdating === agent.id} disabled={agent.status !== "ONLINE" || collectionUpdating !== ""} onChange={(checked) => void toggleAgentCollection(agent, checked)} checkedChildren="开" unCheckedChildren="停" />
                </div>;
              }) : <Typography.Text type="secondary">尚未注册有效 Worker</Typography.Text>}
            </div>
          </Card>
        </Col>

        <Col xs={24} lg={14}>
          <Card title={<Space><FileSearchOutlined />正在调查 <Typography.Text type="secondary">· {derived.activeCases.length} 项</Typography.Text></Space>} extra={<Input allowClear size="small" prefix={<FileSearchOutlined />} value={activeCaseQuery} onChange={(event) => setActiveCaseQuery(event.target.value)} placeholder="快速检索 Case" style={{ width: 180 }} />}>
            {derived.activeCases.length === 0 ? (
              <Empty description="还没有故障调查。请进入 AI 调查页，在同一工作区描述问题并开始调查。">
                <Button type="link" onClick={() => navigate("/cases")}>进入 AI 调查 <ArrowRightOutlined /></Button>
              </Empty>
            ) : derived.filteredActiveCases.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的正在调查 Case" />
            ) : (
              <Collapse ghost defaultActiveKey={["active-cases"]} items={[{ key: "active-cases", label: `调查列表 · ${derived.filteredActiveCases.length} 项`, children: <div className={styles.caseListViewport}><div className={styles.caseList}>
                {derived.filteredActiveCases.slice(0, 20).map((item) => {
                  const status = caseStatus(item.state);
                  const finding = item.summary?.current_finding?.statement || item.problem_description;
                  return (
                    <button key={item.case_id} type="button" className={styles.caseRow} onClick={() => navigate(`/cases?caseId=${encodeURIComponent(item.case_id)}`)}>
                      <span className={styles.caseState}><Tag color={status.color}>{status.label}</Tag><small>{item.environment || "—"}</small></span>
                      <span className={styles.caseCopy}><strong>{item.title || item.problem_description}</strong><small>{finding || "等待 Agent 建立当前理解"}</small></span>
                      <span className={styles.caseProgress}><Progress percent={Math.round((item.agent_progress?.verification_progress || 0) * 100)} size="small" showInfo={false} /><small>{item.agent_progress?.phase_label || "等待推进"}</small></span>
                      <ArrowRightOutlined />
                    </button>
                  );
                })}
              </div></div> }]} />
            )}
          </Card>
        </Col>

        <Col xs={24} lg={10}>
          <Card title={<Space><AlertOutlined />需要我处理</Space>} extra={<Tag color={derived.waitingApproval.length ? "orange" : "default"}>{derived.waitingApproval.length + derived.effectiveWorkers.filter((a) => a.status !== "ONLINE").length}</Tag>}>
            <Collapse ghost defaultActiveKey={["attention"]} items={[{ key: "attention", label: "待处理事项", children: <div className={styles.attentionListViewport}><div className={styles.attentionList}>
              {derived.waitingApproval.map((item) => (
                <button type="button" key={item.case_id} onClick={() => navigate(`/cases?caseId=${encodeURIComponent(item.case_id)}`)}>
                  <SafetyCertificateOutlined /><span><strong>{item.title}</strong><small>{item.summary?.need_you?.question || "Agent 请求人工审批或补充信息"}</small></span><Tag color="orange">处理</Tag>
                </button>
              ))}
              {derived.effectiveWorkers.filter((agent) => agent.status !== "ONLINE").map((agent) => (
                <button type="button" key={agent.id} onClick={() => navigate(`/agent/${encodeURIComponent(agent.id)}`)}>
                  <CloudServerOutlined /><span><strong>{agent.hostname || agent.id} 已离线</strong><small>最后心跳 <RelativeTime value={agent.last_heartbeat_at} /></small></span><Tag>检查</Tag>
                </button>
              ))}
              {!derived.waitingApproval.length && derived.effectiveWorkers.every((agent) => agent.status === "ONLINE") && (
                <Alert type="success" showIcon message="当前没有需要人工立即处理的事项" />
              )}
            </div></div> }]} />
          </Card>
        </Col>

        <Col xs={24} lg={14}>
          <Card title={<Space><ClockCircleOutlined />最近活动</Space>}>
            {activity.length ? (
              <Timeline items={activity.map((item) => {
                const meta = eventType(item.event_type);
                return { color: meta.color === "default" ? "gray" : meta.color, children: <div className={styles.activity}><strong>{meta.label}</strong><span>{item.message || item.event_type}</span><RelativeTime value={item.created_at} /></div> };
              })} />
            ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无可展示的审计活动" />}
          </Card>
        </Col>
        <Col xs={24} lg={10}>
          <Card title={<Space><ThunderboltOutlined />任务运行态势</Space>}>
            <div className={styles.taskSummary}>
              <div><span>Pending</span><strong>{derived.pending}</strong></div>
              <div><span>Running</span><strong>{derived.running}</strong></div>
              <div><span>历史失败</span><strong>{derived.failed.length}</strong></div>
            </div>
            <Alert
              type={analyzerHealthy && derived.pending === 0 && derived.running === 0 ? "info" : "warning"}
              showIcon
              message={`存在 ${derived.failed.length} 条历史失败记录，当前 pending=${derived.pending}、running=${derived.running}，Analyzer ${analyzerHealthy ? "健康" : "需要检查"}。`}
              description="历史失败不会直接被解释为当前系统故障。"
            />
            <div className={styles.quickLinks}>
              <Button onClick={() => navigate("/tasks")}>查看任务与 Evidence</Button>
              <Button onClick={() => navigate("/agents")}>查看在线 Agent</Button>
              <Button onClick={() => navigate("/audit")}>操作记录</Button>
            </div>
          </Card>
        </Col>
      </Row>
    </div>
  );
}
