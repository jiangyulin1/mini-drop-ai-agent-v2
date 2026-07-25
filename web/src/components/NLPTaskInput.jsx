import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import {
  AimOutlined,
  FireOutlined,
  ThunderboltOutlined,
} from "@ant-design/icons";
import { nlpParse, createTask, listAgents } from "../api/client";
import {
  COLLECTOR_OPTIONS,
  collectorMeta,
} from "../utils/collectors";

export default function NLPTaskInput({ onTaskCreated }) {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const [agents, setAgents] = useState([]);
  const [agentsLoading, setAgentsLoading] = useState(true);
  const [quickCollector, setQuickCollector] = useState("perf_cpu");
  const [quickAgentId, setQuickAgentId] = useState("");
  const [quickPid, setQuickPid] = useState(null);
  const [quickDuration, setQuickDuration] = useState(
    collectorMeta("perf_cpu").defaultDuration,
  );

  const onlineAgents = useMemo(
    () => agents.filter((agent) => agent.status === "ONLINE"),
    [agents],
  );

  function selectCapableAgent(collectorType, items = agents) {
    const online = items.filter((agent) => agent.status === "ONLINE");
    const candidates = online.length > 0 ? online : items;
    return candidates.find((agent) =>
      (agent.capabilities || []).includes(collectorType)
    ) || candidates[0];
  }

  async function loadAgents() {
    setAgentsLoading(true);
    try {
      const items = await listAgents();
      setAgents(items || []);
      const preferred = selectCapableAgent(quickCollector, items || []);
      setQuickAgentId((current) =>
        (items || []).some((agent) => agent.id === current)
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
  }, []);

  function changeQuickCollector(value) {
    const meta = collectorMeta(value);
    setQuickCollector(value);
    setQuickDuration(meta.defaultDuration);
    setQuickAgentId(selectCapableAgent(value)?.id || "");
  }

  async function handleQuickCreate() {
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
    if (!(agent?.capabilities || []).includes(quickCollector)) {
      setError(`Agent ${quickAgentId} 不支持 ${collectorMeta(quickCollector).label}`);
      return;
    }

    const meta = collectorMeta(quickCollector);
    setSubmitting(true);
    setError("");
    try {
      const taskResp = await createTask({
        name: `${meta.label}: PID ${quickPid}`,
        agent_id: quickAgentId,
        target_pid: quickPid,
        collector_type: quickCollector,
        sample_rate: meta.defaultSampleRate,
        duration_sec: quickDuration,
        options: { source: "web_quick_preset" },
      });
      message.success(`任务已创建，正在打开 ${meta.resultLabel}`);
      onTaskCreated?.(taskResp.task_id);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleParse() {
    if (!query.trim()) return;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const data = await nlpParse(query.trim());
      setResult(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate() {
    if (!result) return;
    const pid = result.selected_pid || result.candidate_pids?.[0]?.pid;
    if (!pid) {
      setError("请从候选列表中选择一个目标 PID");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const availableAgents = agents.length > 0 ? agents : await listAgents();
      if (!availableAgents || availableAgents.length === 0) {
        setError("暂无可用 Agent，请先启动 Agent 后再创建采集任务");
        return;
      }
      // 优先选择在线 + 具有对应采集器能力的 Agent
      const online = availableAgents.filter((a) => a.status === "ONLINE");
      const capable = (online.length > 0 ? online : availableAgents).filter((a) =>
        (a.capabilities || []).includes(result.collector_type)
      );
      const agent = capable[0] || online[0] || availableAgents[0];
      if (!agent?.id) {
        setError("暂无可用 Agent，请先启动 Agent 后再创建采集任务");
        return;
      }
      const taskResp = await createTask({
        name: `NLP: ${result.process_name}`,
        agent_id: agent.id,
        target_pid: pid,
        collector_type: result.collector_type,
        sample_rate: result.sample_rate,
        duration_sec: result.duration_sec,
        options: { nlp_query: query.trim() },
      });
      setResult(null);
      setQuery("");
      message.success(`任务已创建 → Agent: ${agent.id}`);
      onTaskCreated?.(taskResp.task_id);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card
      title={
        <Space>
          <ThunderboltOutlined style={{ color: "#faad14" }} />
          <Typography.Text strong>新建性能采集</Typography.Text>
          <Tag color="blue">可视化</Tag>
        </Space>
      }
      style={{ marginBottom: 16 }}
    >
      {error && <Alert type="error" message={error} showIcon style={{ marginTop: 12 }} />}

      <Tabs
        defaultActiveKey="quick"
        items={[
          {
            key: "quick",
            label: <Space><FireOutlined />快速可视化</Space>,
            children: (
              <Space direction="vertical" size={12} style={{ width: "100%" }}>
                <Alert
                  type={collectorMeta(quickCollector).flamegraph ? "success" : "info"}
                  showIcon
                  message={`完成后展示：${collectorMeta(quickCollector).resultLabel}`}
                  description={collectorMeta(quickCollector).description}
                />
                <Row gutter={[12, 12]}>
                  <Col xs={24} lg={9}>
                    <Typography.Text type="secondary">采集预设</Typography.Text>
                    <Select
                      value={quickCollector}
                      options={COLLECTOR_OPTIONS}
                      onChange={changeQuickCollector}
                      style={{ width: "100%", marginTop: 4 }}
                    />
                  </Col>
                  <Col xs={24} md={12} lg={7}>
                    <Typography.Text type="secondary">目标 Agent</Typography.Text>
                    <Select
                      value={quickAgentId || undefined}
                      loading={agentsLoading}
                      placeholder="选择在线 Agent"
                      style={{ width: "100%", marginTop: 4 }}
                      onChange={setQuickAgentId}
                      options={agents.map((agent) => ({
                        value: agent.id,
                        label: `${agent.hostname || agent.id} · ${agent.status}`,
                        disabled:
                          agent.status !== "ONLINE" ||
                          !(agent.capabilities || []).includes(quickCollector),
                      }))}
                    />
                  </Col>
                  <Col xs={12} md={6} lg={4}>
                    <Typography.Text type="secondary">目标 PID</Typography.Text>
                    <InputNumber
                      min={1}
                      value={quickPid}
                      placeholder="例如 1234"
                      onChange={setQuickPid}
                      style={{ width: "100%", marginTop: 4 }}
                    />
                  </Col>
                  <Col xs={12} md={6} lg={4}>
                    <Typography.Text type="secondary">采样时长（秒）</Typography.Text>
                    <InputNumber
                      min={1}
                      max={300}
                      value={quickDuration}
                      onChange={setQuickDuration}
                      style={{ width: "100%", marginTop: 4 }}
                    />
                  </Col>
                </Row>
                <Space wrap>
                  <Button
                    type="primary"
                    icon={<AimOutlined />}
                    loading={submitting}
                    onClick={handleQuickCreate}
                  >
                    创建并查看结果
                  </Button>
                  <Typography.Text type="secondary">
                    {onlineAgents.length} 个 Agent 在线；仅显示支持当前采集器的目标
                  </Typography.Text>
                </Space>
              </Space>
            ),
          },
          {
            key: "nlp",
            label: <Space><ThunderboltOutlined />自然语言</Space>,
            children: (
              <>
                <Input.Search
                  placeholder="描述性能问题，例如：mysqld CPU 飙高，帮我看看"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onSearch={handleParse}
                  loading={loading}
                  enterButton="解析意图"
                  size="large"
                  maxLength={200}
                />
                {result && (
                  <Card
                    size="small"
                    style={{ marginTop: 12, background: "#fafafa" }}
                    title="确认采集参数"
                    extra={<Button type="primary" size="small" loading={submitting} onClick={handleCreate}>确认创建并查看</Button>}
                  >
                    <Descriptions column={2} size="small">
                      <Descriptions.Item label="采集器">
                        <Tag color={collectorMeta(result.collector_type).color}>
                          {collectorMeta(result.collector_type).label}
                        </Tag>
                      </Descriptions.Item>
                      <Descriptions.Item label="预期结果">
                        {collectorMeta(result.collector_type).resultLabel}
                      </Descriptions.Item>
                      <Descriptions.Item label="进程">{result.process_name}</Descriptions.Item>
                      <Descriptions.Item label="采样时长">{result.duration_sec}s</Descriptions.Item>
                      <Descriptions.Item label="采样率">{result.sample_rate} Hz</Descriptions.Item>
                      {result.candidate_pids?.length > 0 && (
                        <Descriptions.Item label="候选 PID">
                          <Select
                            size="small"
                            style={{ width: 240 }}
                            defaultValue={result.candidate_pids[0].pid}
                            onChange={(val) => setResult({ ...result, selected_pid: val })}
                            options={result.candidate_pids.map((c) => ({
                              label: `${c.pid} (${c.comm}${c.cmdline ? " " + c.cmdline.slice(0, 40) : ""})`,
                              value: c.pid,
                            }))}
                          />
                        </Descriptions.Item>
                      )}
                    </Descriptions>
                    <Typography.Paragraph type="secondary" style={{ margin: "8px 0 0", fontSize: 12 }}>
                      {result.reasoning}
                    </Typography.Paragraph>
                  </Card>
                )}
              </>
            ),
          },
        ]}
      />
    </Card>
  );
}
