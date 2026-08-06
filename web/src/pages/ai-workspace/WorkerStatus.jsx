import { Button, Empty, Popover, Tag } from "antd";
import { ClusterOutlined, ReloadOutlined } from "@ant-design/icons";
import styles from "../AIDiagnosis.module.css";

function heartbeatText(value) {
  if (!value) return "无心跳";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 1000));
  if (!Number.isFinite(seconds)) return "心跳未知";
  return seconds < 60 ? `${seconds} 秒前` : `${Math.round(seconds / 60)} 分钟前`;
}

export default function WorkerStatus({ agents, onRefresh, loading }) {
  const online = agents.filter((item) => item.status === "ONLINE");
  const content = (
    <div className={styles.workerPopover}>
      <div className={styles.workerSummary}>
        {online.length}/{agents.length} 个 Worker 在线。采集前仍会检查 Agent 能力。
      </div>
      {agents.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有注册的 Worker" /> : agents.map((agent) => (
        <div className={styles.workerRow} key={agent.id}>
          <div>
            <div className={styles.workerName}>{agent.hostname || agent.id}</div>
            <div className={styles.workerMeta}>{agent.ip_addr || agent.id} · {agent.version || "版本未知"}</div>
          </div>
          <Tag color={agent.status === "ONLINE" ? "green" : "default"}>
            {agent.status === "ONLINE" ? "在线" : "离线"}
          </Tag>
          <div>
            <div className={styles.capabilities} title={(agent.capabilities || []).join("、")}>
              {(agent.capabilities || []).join("、") || "未上报能力"}
            </div>
            <div className={styles.workerMeta}>{heartbeatText(agent.last_heartbeat_at)}</div>
          </div>
        </div>
      ))}
      <Button block size="small" icon={<ReloadOutlined />} loading={loading} onClick={onRefresh}>
        刷新状态
      </Button>
    </div>
  );

  return (
    <Popover trigger="click" placement="bottomRight" title="Worker 状态" content={content}>
      <button type="button" className={styles.workerButton} aria-label="查看 Worker 状态">
        <span className={`${styles.statusDot} ${online.length === agents.length && agents.length ? styles.statusOnline : styles.statusWaiting}`} />
        <ClusterOutlined />
        <span>{online.length}/{agents.length || 0} 在线</span>
      </button>
    </Popover>
  );
}
