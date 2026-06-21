import { useState } from "react";
import { Alert, Button, Card, Descriptions, Input, Select, Space, Tag, Typography, message } from "antd";
import { ThunderboltOutlined } from "@ant-design/icons";
import { nlpParse, createTask, listAgents } from "../api/client";

const COLLECTOR_LABELS = {
  perf_cpu: "perf CPU 火焰图",
  ebpf_io: "eBPF IO 延迟",
  pyspy: "py-spy Python 火焰图",
  continuous_perf: "持续周期采样",
  java_async: "Java async-profiler",
  go_pprof: "Go pprof 分析",
  memory_smaps: "内存分析 (smaps)",
  sys_metrics: "系统多维指标",
};

export default function NLPTaskInput({ onTaskCreated }) {
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [submitting, setSubmitting] = useState(false);

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
      const agents = await listAgents();
      if (!agents || agents.length === 0) {
        setError("暂无可用 Agent，请先启动 Agent 后再创建采集任务");
        return;
      }
      // 优先选择在线 + 具有对应采集器能力的 Agent
      const online = agents.filter((a) => a.status === "ONLINE");
      const capable = (online.length > 0 ? online : agents).filter((a) =>
        (a.capabilities || []).includes(result.collector_type)
      );
      const agent = capable[0] || online[0] || agents[0];
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
          <Typography.Text strong>自然语言采集</Typography.Text>
          <Tag color="orange">AI</Tag>
        </Space>
      }
      style={{ marginBottom: 16 }}
    >
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

      {error && <Alert type="error" message={error} showIcon style={{ marginTop: 12 }} />}

      {result && (
        <Card
          size="small"
          style={{ marginTop: 12, background: "#fafafa" }}
          title="确认采集参数"
          extra={<Button type="primary" size="small" loading={submitting} onClick={handleCreate}>确认创建</Button>}
        >
          <Descriptions column={2} size="small">
            <Descriptions.Item label="采集器">
              <Tag color="blue">{COLLECTOR_LABELS[result.collector_type] || result.collector_type}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="进程">{result.process_name}</Descriptions.Item>
            <Descriptions.Item label="采样时长">{result.duration_sec}s</Descriptions.Item>
            <Descriptions.Item label="采样率">{result.sample_rate} Hz</Descriptions.Item>
            {result.candidate_pids?.length > 0 && (
              <Descriptions.Item label="候选 PID">
                <Select
                  size="small"
                  style={{ width: 200 }}
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
    </Card>
  );
}
