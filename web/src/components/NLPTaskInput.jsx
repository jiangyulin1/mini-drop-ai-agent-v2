import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  InputNumber,
  Row,
  Select,
  Space,
  Tag,
  Typography,
  message,
} from "antd";
import {
  AimOutlined,
  FireOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { createTask, listAgents, listTaskKinds } from "../api/client";
import AgentProcessPicker from "./AgentProcessPicker";
import {
  COLLECTOR_OPTIONS,
  collectorMeta,
  collectorMetaFromTaskKind,
} from "../utils/collectors";

export default function NLPTaskInput({ onTaskCreated }) {
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [agents, setAgents] = useState([]);
  const [agentsLoading, setAgentsLoading] = useState(true);
  const [quickCollector, setQuickCollector] = useState("perf_cpu");
  const [quickAgentId, setQuickAgentId] = useState("");
  const [quickPid, setQuickPid] = useState(null);
  const [quickProcessQuery, setQuickProcessQuery] = useState("");
  const [quickDuration, setQuickDuration] = useState(
    collectorMeta("perf_cpu").defaultDuration,
  );
  const [taskKindMeta, setTaskKindMeta] = useState({});
  const submittingRef = useRef(false);

  const metaFor = (collectorType) =>
    taskKindMeta[collectorType] || collectorMeta(collectorType);

  const collectorOptions = useMemo(() => {
    const remote = Object.entries(taskKindMeta).map(([value, meta]) => ({
      value,
      label: `${meta.label} · ${meta.resultLabel}`,
    }));
    return remote.length > 0 ? remote : COLLECTOR_OPTIONS;
  }, [taskKindMeta]);

  const onlineAgents = useMemo(
    () => agents.filter((agent) => agent.status === "ONLINE"),
    [agents],
  );

  function selectCapableAgent(collectorType, items = agents) {
    return items.find((agent) =>
      agent.status === "ONLINE" &&
      (agent.capabilities || []).includes(collectorType)
    );
  }

  async function loadAgents() {
    setAgentsLoading(true);
    try {
      const items = await listAgents();
      setAgents(items || []);
      const preferred = selectCapableAgent(quickCollector, items || []);
      setQuickAgentId((current) =>
        (items || []).some((agent) => (
          agent.id === current
          && agent.status === "ONLINE"
          && (agent.capabilities || []).includes(quickCollector)
        ))
          ? current
          : preferred?.id || ""
      );
    } catch (err) {
      setError(err.message);
    } finally {
      setAgentsLoading(false);
    }
  }

  useEffect(() => {
    loadAgents();
    listTaskKinds()
      .then((items) => {
        const normalized = Object.fromEntries(
          (items || [])
            .map((kind) => [kind.key, collectorMetaFromTaskKind(kind)])
            .filter(([, meta]) => Boolean(meta)),
        );
        if (Object.keys(normalized).length > 0) setTaskKindMeta(normalized);
      })
      .catch(() => {
        // Older Servers do not expose metadata; keep the built-in safe fallback.
      });
  }, []);

  function changeQuickCollector(value) {
    const meta = metaFor(value);
    setQuickCollector(value);
    setQuickDuration(meta.defaultDuration);
    setQuickAgentId(selectCapableAgent(value)?.id || "");
    setQuickPid(null);
  }

  async function handleQuickCreate() {
    if (submittingRef.current) return;
    if (!quickAgentId) {
      setError("请选择一个在线 Agent");
      return;
    }
    if (!quickPid || quickPid <= 0) {
      setError("请输入有效的目标 PID");
      return;
    }
    if (!quickDuration || quickDuration <= 0) {
      setError("请输入有效的采样时长");
      return;
    }
    const agent = agents.find((item) => item.id === quickAgentId);
    if (!agent || agent.status !== "ONLINE") {
      setError("所选 Worker 已离线，请重新选择在线 Worker");
      return;
    }
    if (!(agent?.capabilities || []).includes(quickCollector)) {
      setError(`Agent ${quickAgentId} 不支持 ${metaFor(quickCollector).label}`);
      return;
    }

    const meta = metaFor(quickCollector);
    submittingRef.current = true;
    setSubmitting(true);
    setError("");
    try {
      const taskResp = await createTask({
        name: `${meta.label} · ${quickAgentId} · PID ${quickPid}`,
        agent_id: quickAgentId,
        target_pid: quickPid,
        collector_type: quickCollector,
        sample_rate: meta.defaultSampleRate,
        duration_sec: quickDuration,
        options: { source: "web_quick_preset" },
      });
      message.success(`采集任务已创建，完成后将自动展示${meta.resultLabel}`);
      onTaskCreated?.(taskResp.task_id);
    } catch (err) {
      setError(err.message);
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  return (
    <Card
      title={
        <Space>
          <ThunderboltOutlined style={{ color: "#faad14" }} />
          <Typography.Text strong>新建采集任务</Typography.Text>
          <Tag color="blue">可视化</Tag>
        </Space>
      }
    >
      {error && <Alert type="error" message={error} showIcon style={{ marginTop: 12 }} />}

      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Alert
                  type={metaFor(quickCollector).flamegraph ? "success" : "info"}
                  showIcon
                  message={`完成后展示：${metaFor(quickCollector).resultLabel}`}
                  description={metaFor(quickCollector).description}
                />
                <Row gutter={[12, 12]}>
                  <Col xs={24} lg={10}>
                    <Typography.Text type="secondary">采集预设</Typography.Text>
                    <Select
                      value={quickCollector}
                      options={collectorOptions}
                      onChange={changeQuickCollector}
                      style={{ width: "100%", marginTop: 4 }}
                    />
                  </Col>
                  <Col xs={24} md={14} lg={8}>
                    <Typography.Text type="secondary">目标 Worker</Typography.Text>
                    <Select
                      value={quickAgentId || undefined}
                      loading={agentsLoading}
                      placeholder="选择在线且支持该采集器的 Worker"
                      style={{ width: "100%", marginTop: 4 }}
                      onChange={(value) => {
                        setQuickAgentId(value);
                        setQuickPid(null);
                      }}
                      options={agents.map((agent) => ({
                        value: agent.id,
                        label: `${agent.hostname || agent.id} · ${agent.status}`,
                        disabled:
                          agent.status !== "ONLINE" ||
                          !(agent.capabilities || []).includes(quickCollector),
                      }))}
                    />
                  </Col>
                  <Col xs={24} md={10} lg={6}>
                    <Typography.Text type="secondary">采样时长（秒）</Typography.Text>
                    <InputNumber
                      min={metaFor(quickCollector).durationMin || 1}
                      max={metaFor(quickCollector).durationMax || 120}
                      value={quickDuration}
                      onChange={setQuickDuration}
                      style={{ width: "100%", marginTop: 4 }}
                    />
                  </Col>
                </Row>
                <div>
                  <Typography.Text type="secondary">目标进程</Typography.Text>
                  <div style={{ marginTop: 4 }}>
                    <AgentProcessPicker
                      agentId={quickAgentId}
                      agentLabel={agents.find((agent) => agent.id === quickAgentId)?.hostname || quickAgentId}
                      keyword={quickProcessQuery}
                      onKeywordChange={setQuickProcessQuery}
                      value={quickPid}
                      onChange={setQuickPid}
                      disabled={agentsLoading}
                    />
                  </div>
                </div>
                <Space wrap>
                  <Button
                    type="primary"
                    icon={<AimOutlined />}
                    loading={submitting}
                    disabled={!quickAgentId || !Number(quickPid)}
                    onClick={handleQuickCreate}
                  >
                    开始采集
                  </Button>
                  <Typography.Text type="secondary">
                    {onlineAgents.length} 个 Agent 在线；仅显示支持当前采集器的目标
                  </Typography.Text>
                </Space>
      </Space>
    </Card>
  );
}
