import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Checkbox,
  InputNumber,
  Modal,
  Select,
  Space,
  Typography,
  message,
} from "antd";
import { createTask } from "../api/client";
import { COLLECTOR_OPTIONS, collectorMeta } from "../utils/collectors";
import { newCollectionId } from "../pages/ai-workspace/workspaceUtils";

/** 多机采集弹窗：在多个 Worker 上创建同一采集会话的任务。
 *
 * 属于“采集与监控”第一页的纯采集功能，不依赖 AI。创建的任务带
 * collection_session_id，可供 AI 会话后续关联分析。
 */
export default function MultiAgentCollectionModal({
  open,
  agents,
  onClose,
  onCreated,
  serviceId = "",
  caseId = "",
}) {
  const [collector, setCollector] = useState("sys_metrics");
  const [duration, setDuration] = useState(30);
  const [targets, setTargets] = useState([]);
  const [creating, setCreating] = useState(false);
  const initializedOpen = useRef(false);
  const pendingCollectionId = useRef("");

  useEffect(() => {
    if (!open) {
      initializedOpen.current = false;
      return;
    }
    if (!initializedOpen.current) {
      initializedOpen.current = true;
      pendingCollectionId.current = newCollectionId();
      setTargets(agents.map((agent) => ({
        agent,
        checked: agent.status === "ONLINE",
        pid: null,
      })));
      return;
    }
    setTargets((current) => {
      const drafts = new Map(current.map((item) => [item.agent.id, item]));
      return agents.map((agent) => {
        const draft = drafts.get(agent.id);
        return draft ? { ...draft, agent } : { agent, checked: false, pid: null };
      });
    });
  }, [agents, open]);

  function updateTarget(agentId, patch) {
    setTargets((items) => items.map((item) => (
      item.agent.id === agentId ? { ...item, ...patch } : item
    )));
  }

  async function createCollection() {
    const selectedTargets = targets.filter((item) => item.checked);
    if (!selectedTargets.length) return message.warning("请选择 Worker");
    if (selectedTargets.some((item) => !Number(item.pid))) return message.warning("请填写每个 Worker 的 PID");
    const unsupported = selectedTargets.find((item) => !(item.agent.capabilities || []).includes(collector));
    if (unsupported) return message.error(`${unsupported.agent.hostname || unsupported.agent.id} 不支持 ${collectorMeta(collector).label}`);
    const collectionId = pendingCollectionId.current || newCollectionId();
    pendingCollectionId.current = collectionId;
    const meta = collectorMeta(collector);
    const service = serviceId || "manual";
    setCreating(true);
    try {
      const results = await Promise.allSettled(selectedTargets.map((item) => createTask({
        name: `${meta.label} · ${service} · ${item.agent.hostname || item.agent.id}`,
        agent_id: item.agent.id,
        target_pid: Number(item.pid),
        collector_type: collector,
        sample_rate: meta.defaultSampleRate,
        duration_sec: duration,
        options: {
          source: "multi_agent_collection",
          collection_session_id: collectionId,
          case_id: caseId || "",
          service_id: service,
        },
      }, `collection-${collectionId}-${item.agent.id}-${collector}`)));
      const succeeded = results.filter((result) => result.status === "fulfilled").length;
      const failed = results.length - succeeded;
      if (!succeeded) {
        const reason = results.find((result) => result.status === "rejected")?.reason?.message || "请求失败";
        throw new Error(reason);
      }
      setCreating(false);
      onClose();
      if (failed) message.warning(`已创建 ${succeeded} 个任务，${failed} 个节点失败；已保留为部分采集批次`);
      else message.success(`已创建 ${succeeded} 个采集任务（批次 ${collectionId}）`);
      onCreated?.(collectionId);
    } catch (error) {
      message.error(`创建失败：${error.message}`);
      setCreating(false);
    }
  }

  return (
    <Modal
      title="新建多机采集"
      open={open}
      onCancel={onClose}
      onOk={createCollection}
      okText="创建采集"
      confirmLoading={creating}
      width={720}
      destroyOnHidden
    >
      <Alert type="info" showIcon message="每个 Worker 创建一个任务，使用同一个采集会话 ID，可同时分析多个节点。" style={{ marginBottom: 14 }} />
      <Space direction="vertical" size={14} style={{ width: "100%" }}>
        <div>
          <Typography.Text strong>采集器</Typography.Text>
          <Select value={collector} onChange={setCollector} options={COLLECTOR_OPTIONS} style={{ width: "100%", marginTop: 5 }} />
          <Typography.Text type="secondary">{collectorMeta(collector).description}</Typography.Text>
        </div>
        <div>
          <Typography.Text strong>时长</Typography.Text>
          <InputNumber min={1} max={120} value={duration} onChange={(value) => setDuration(value || 15)} addonAfter="秒" style={{ width: 180, marginLeft: 10 }} />
        </div>
        <div>
          <Typography.Text strong>Worker 与目标进程</Typography.Text>
          <div style={{ marginTop: 7, display: "flex", flexDirection: "column", gap: 8 }}>
            {targets.map((item) => (
              <div key={item.agent.id} style={{ display: "grid", gridTemplateColumns: "24px 1fr 150px", gap: 9, alignItems: "center", padding: "9px 10px", border: "1px solid #f0f0f0", borderRadius: 8 }}>
                <Checkbox checked={item.checked} disabled={item.agent.status !== "ONLINE"} onChange={(event) => updateTarget(item.agent.id, { checked: event.target.checked })} aria-label={`采集 ${item.agent.hostname || item.agent.id}`} />
                <div>
                  <div style={{ fontWeight: 650 }}>{item.agent.hostname || item.agent.id}</div>
                  <div style={{ color: "#999", fontSize: 10 }}>{item.agent.ip_addr || item.agent.id} · {item.agent.status === "ONLINE" ? "在线" : "离线"}</div>
                </div>
                <InputNumber min={1} max={4194304} placeholder="目标 PID" value={item.pid} disabled={!item.checked} onChange={(value) => updateTarget(item.agent.id, { pid: value })} style={{ width: "100%" }} aria-label={`${item.agent.hostname || item.agent.id} 采集 PID`} />
              </div>
            ))}
          </div>
        </div>
        <div style={{ marginTop: 10, padding: "9px 10px", borderRadius: 8, color: "#226556", background: "#eaf8f5", fontSize: 11 }}>
          只创建你确认的任务。需要审批的采集仍按后端策略处理。
        </div>
      </Space>
    </Modal>
  );
}
