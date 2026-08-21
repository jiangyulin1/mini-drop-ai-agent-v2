import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, Button, Card, Col, Empty, Progress, Row, Skeleton, Tag, Tooltip, Typography } from "antd";
import { ApiOutlined, CloudServerOutlined, DatabaseOutlined, GlobalOutlined, HddOutlined, ReloadOutlined, RobotOutlined } from "@ant-design/icons";
import { listAgents } from "../api/client";
import ErrorAlert from "../components/ErrorAlert";
import styles from "./AgentsOverview.module.css";

const CAPABILITY_GROUPS = [
  ["系统观察", ["sys_metrics", "process_scan", "runtime_snapshot", "log_scan", "connection_probe"]],
  ["性能分析", ["perf_cpu", "continuous_perf", "memory_smaps", "ebpf_io"]],
  ["语言运行时", ["pyspy", "go_pprof", "java_async"]],
  ["控制能力", ["swarm_actuation"]],
];

function relative(value) {
  if (!value) return "无心跳记录";
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return "心跳时间无效";
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (seconds < 60) return `${seconds} 秒前`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  return `${Math.floor(seconds / 3600)} 小时前`;
}

function ResourceMeter({ label, value, suffix = "%", max = 100 }) {
  const parsed = Number(value ?? 0);
  const number = Number.isFinite(parsed) ? parsed : 0;
  const percent = Math.min(100, Math.max(0, (number / max) * 100));
  return <div className={styles.meter}><span>{label}</span><Progress percent={percent} size="small" showInfo={false} /><strong>{number.toFixed(number % 1 ? 1 : 0)}{suffix}</strong></div>;
}

function WorkerCard({ agent, onOpen }) {
  const online = agent.status === "ONLINE";
  const metrics = agent.latest_metrics?.self || {};
  const capabilities = new Set(agent.capabilities || []);
  const isDemo = agent.id === "demo-worker";
  return (
    <Card className={`${styles.workerCard} ${!online ? styles.offlineCard : ""}`}>
      <header className={styles.workerHeader}>
        <span className={`${styles.workerIcon} ${online ? styles.online : ""}`}><RobotOutlined /></span>
        <div className={styles.workerIdentity}><Typography.Title level={4}>{agent.hostname || agent.id}</Typography.Title><Typography.Text copyable={{ text: agent.id }}>{agent.id}</Typography.Text></div>
        <Tag color={online ? "success" : "default"}>{online ? "ONLINE" : "OFFLINE"}</Tag>
      </header>
      {isDemo && <Alert type="info" showIcon message="历史演示注册，当前离线，不计入有效 Worker 数量" />}
      <div className={styles.identityGrid}>
        <span><small>IP</small><strong>{agent.ip_addr || "—"}</strong></span>
        <span><small>版本</small><strong>{agent.version || "—"}</strong></span>
        <span><small>最后心跳</small><Tooltip title={agent.last_heartbeat_at ? new Date(agent.last_heartbeat_at).toLocaleString() : "—"}><strong>{relative(agent.last_heartbeat_at)}</strong></Tooltip></span>
      </div>
      <div className={styles.resources} aria-label={`${agent.id} Agent 进程开销`}>
        <strong className={styles.sectionLabel}>Agent 进程开销</strong>
        <ResourceMeter label="CPU" value={metrics.cpu_percent} />
        <ResourceMeter label="RSS" value={metrics.rss_mb} suffix=" MB" max={512} />
        <ResourceMeter label="IO Write" value={metrics.write_kb_s} suffix=" KB/s" max={1024} />
      </div>
      <div className={styles.capabilities}>
        {CAPABILITY_GROUPS.map(([group, names]) => (
          <div key={group}><strong>{group}</strong><div>{names.map((name) => capabilities.has(name) ? <Tag color="blue" key={name}>{name}</Tag> : <Tooltip title="当前节点未声明该能力" key={name}><Tag className={styles.missing}>{name}</Tag></Tooltip>)}</div></div>
        ))}
      </div>
      <Button block disabled={isDemo} onClick={() => onOpen(agent.id)}>查看节点任务与完整资源</Button>
    </Card>
  );
}

export default function AgentsOverview() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const [agents, setAgents] = useState([]);
  const hasLoaded = useRef(false);
  const load = useCallback(async () => {
    setError(null);
    if (hasLoaded.current) setRefreshing(true);
    else setLoading(true);
    try { setAgents((await listAgents()) || []); } catch (nextError) { setError(nextError); } finally {
      hasLoaded.current = true;
      setLoading(false);
      setRefreshing(false);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    const refresh = () => { void load(); };
    window.addEventListener("mini-drop:refresh", refresh);
    return () => window.removeEventListener("mini-drop:refresh", refresh);
  }, [load]);
  const workers = useMemo(() => agents.filter((agent) => agent.id !== "demo-worker"), [agents]);
  const demo = agents.find((agent) => agent.id === "demo-worker");
  if (loading) return <div className={styles.page}><Skeleton active paragraph={{ rows: 15 }} /></div>;
  return (
    <div className={styles.page}>
      <header className={styles.pageHeader}><div><span>CONTROL & DISTRIBUTED WORKERS</span><Typography.Title level={2}>节点与 Agent</Typography.Title><Typography.Paragraph>能力未声明表示该节点不支持对应采集，不代表系统故障。</Typography.Paragraph></div><Button icon={<ReloadOutlined />} loading={refreshing} aria-label="刷新节点列表" onClick={load}>刷新</Button></header>
      <ErrorAlert error={error} onRetry={load} />
      <section className={styles.topology} aria-label={`控制服务与 ${workers.length} 个 Worker 的连接拓扑`}>
        <div className={styles.topologyHeader}><div><strong>连接拓扑</strong><small>{workers.length ? `${workers.length} 个已注册 Worker` : "尚无已注册 Worker"}</small></div><Tag>{workers.filter((agent) => agent.status === "ONLINE").length} ONLINE</Tag></div>
        <div className={styles.controlNode}><span><CloudServerOutlined /></span><div className={styles.controlIdentity}><strong>Control Plane</strong><small>当前控制服务</small></div><Tag color="blue">CONTROL PLANE</Tag><div className={styles.services}><Tag icon={<ApiOutlined />}>Server</Tag><Tag icon={<RobotOutlined />}>Pi Sidecar</Tag><Tag icon={<DatabaseOutlined />}>S3</Tag><Tag icon={<HddOutlined />}>Analyzer</Tag><Tag icon={<GlobalOutlined />}>Nginx</Tag></div></div>
        <div className={styles.topologyLine} />
        {workers.length ? <div className={styles.workerNodes}>{workers.map((agent) => <div key={agent.id}><span className={agent.status === "ONLINE" ? styles.nodeOnline : styles.nodeOffline} /><strong title={agent.hostname || agent.id}>{agent.hostname || agent.id}</strong><small title={agent.ip_addr || undefined}>{agent.ip_addr || "地址未上报"}</small><Tag color={agent.status === "ONLINE" ? "success" : "default"}>Mini-Drop Agent</Tag></div>)}</div> : <div className={styles.emptyTopology}>等待 Worker 注册并上报心跳</div>}
      </section>
      {workers.length === 0 ? <Empty description="当前没有可执行采集任务的 Worker。请检查 Agent 服务和心跳状态。" /> : <Row gutter={[16, 16]}>{workers.map((agent) => <Col xs={24} xl={12} key={agent.id}><WorkerCard agent={agent} onOpen={(id) => navigate(`/agent/${encodeURIComponent(id)}`)} /></Col>)}</Row>}
      {demo && <div className={styles.demo}><WorkerCard agent={demo} onOpen={() => {}} /></div>}
    </div>
  );
}
