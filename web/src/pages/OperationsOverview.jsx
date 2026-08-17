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
  Progress,
  Row,
  Skeleton,
  Space,
  Tag,
  Timeline,
  Tooltip,
  Typography,
} from "antd";
import {
  AlertOutlined,
  ArrowRightOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
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
} from "../api/client";
import ErrorAlert from "../components/ErrorAlert";
import useSSE from "../hooks/useSSE";
import { caseStatus, eventType, isActiveCase } from "../utils/opsMappings";
import styles from "./OperationsOverview.module.css";

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
  agent_cluster_fanout_enabled: "关闭时，同一诊断动作不会自动扩散到多个目标节点。",
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
  const seconds = Math.max(0, Math.floor((Date.now() - time.getTime()) / 1000));
  const relative = seconds < 60 ? `${seconds} 秒前` : seconds < 3600 ? `${Math.floor(seconds / 60)} 分钟前` : `${Math.floor(seconds / 3600)} 小时前`;
  return <time dateTime={time.toISOString()} title={time.toLocaleString()}>{relative}</time>;
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
    return { effectiveWorkers, online, activeCases, waitingApproval, failed, pending, running };
  }, [data]);

  if (loading) {
    return <div className={styles.page}><Skeleton active paragraph={{ rows: 16 }} /></div>;
  }

  const checks = data.health?.checks || {};
  const flags = data.runtime?.flags || {};
  const controlsPaused = data.controls.some((item) => item.enabled && /pause|stop|red/i.test(item.control_name || item.name || ""));
  const analyzerHealthy = checks.analyzer?.status === "ok";
  const runtimeHealthy = data.runtime?.ready === true;
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
          <Button type="primary" icon={<ExperimentOutlined />} onClick={() => navigate("/cases?new=1")}>新建故障调查</Button>
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
              <HealthCheck label={CHECK_LABELS.storage} healthy={checks.storage?.status === "ok"} />
              <HealthCheck label={CHECK_LABELS.analyzer} healthy={analyzerHealthy} detail={`${checks.analyzer?.workers_online ?? 0} worker`} />
              <HealthCheck label={CHECK_LABELS.runtime} healthy={runtimeHealthy} detail={`${data.runtime?.runtime_type || "—"} ${data.runtime?.runtime_version || ""}`} />
              <HealthCheck label={CHECK_LABELS.worker} healthy={derived.online.length > 0} detail={`${derived.online.length} 在线`} />
            </div>
            <Collapse ghost size="small" items={[{ key: "raw", label: "查看原始健康检查结果", children: <pre className={styles.raw}>{JSON.stringify(data.health, null, 2)}</pre> }]} />
          </Card>
        </Col>
        <Col xs={24} xl={9}>
          <Card className={styles.primaryCard} title={<Space><RobotOutlined />Agent 自治能力</Space>} extra={<Tag color={runtimeHealthy ? "purple" : "red"}>{data.runtime?.mode || "unknown"}</Tag>}>
            <div className={styles.runtimeIdentity}>
              <div><span>Runtime</span><strong>{data.runtime?.runtime_type || "—"}</strong></div>
              <div><span>版本</span><strong>{data.runtime?.runtime_version || flags.pi_runtime_version || "—"}</strong></div>
              <div><span>并发 Case 上限</span><strong>{flags.agent_max_active_cases ?? "—"}</strong></div>
            </div>
            <CapabilityFlag label="Auto READ_LOW" value={flags.agent_auto_read_low} help={FLAG_HELP.agent_auto_read_low} />
            <CapabilityFlag label="MCP" value={flags.agent_mcp_enabled} help={FLAG_HELP.agent_mcp_enabled} />
            <CapabilityFlag label="Skills" value={flags.agent_skills_enabled} help={FLAG_HELP.agent_skills_enabled} />
            <CapabilityFlag label="Cluster Fanout" value={flags.agent_cluster_fanout_enabled} help={FLAG_HELP.agent_cluster_fanout_enabled} />
          </Card>
        </Col>

        <Col xs={24} lg={14}>
          <Card title={<Space><FileSearchOutlined />正在调查</Space>} extra={<Button type="link" onClick={() => navigate("/cases")}>全部 Case <ArrowRightOutlined /></Button>}>
            {derived.activeCases.length === 0 ? (
              <Empty description="还没有故障调查。创建 Case 后，Agent 会基于目标范围和初始证据生成调查计划。">
                <Button type="primary" onClick={() => navigate("/cases?new=1")}>创建 Case</Button>
              </Empty>
            ) : (
              <div className={styles.caseList}>
                {derived.activeCases.slice(0, 6).map((item) => {
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
              </div>
            )}
          </Card>
        </Col>

        <Col xs={24} lg={10}>
          <Card title={<Space><AlertOutlined />需要我处理</Space>} extra={<Tag color={derived.waitingApproval.length ? "orange" : "default"}>{derived.waitingApproval.length + derived.effectiveWorkers.filter((a) => a.status !== "ONLINE").length}</Tag>}>
            <div className={styles.attentionList}>
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
            </div>
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
              <Button onClick={() => navigate("/runtime")}>Runtime 配置</Button>
            </div>
          </Card>
        </Col>
      </Row>
    </div>
  );
}
