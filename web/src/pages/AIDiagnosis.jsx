import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Row,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Timeline,
  Typography,
  message,
} from "antd";
import {
  CheckOutlined,
  CloseOutlined,
  ReloadOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import {
  approveDiagnosisProbe,
  createDiagnosisSession,
  getDiagnosisSession,
  listAgents,
  listDiagnosisSessions,
} from "../api/client";

const TERMINAL = new Set([
  "COMPLETED",
  "INSUFFICIENT_EVIDENCE",
  "PARTIAL_COMPLETED",
  "BUDGET_EXHAUSTED",
  "TOPOLOGY_UNAVAILABLE",
  "USER_CANCELED",
  "FAILED",
]);

const STATUS_COLORS = {
  COMPLETED: "green",
  PARTIAL_COMPLETED: "orange",
  INSUFFICIENT_EVIDENCE: "gold",
  FAILED: "red",
  BUDGET_EXHAUSTED: "red",
  WAITING_APPROVAL: "purple",
  COLLECTING: "blue",
  ANALYZING: "cyan",
  NEEDS_SCOPE_CONFIRMATION: "orange",
};

function Status({ value }) {
  return <Tag color={STATUS_COLORS[value] || "default"}>{value || "UNKNOWN"}</Tag>;
}

export default function AIDiagnosis() {
  const [form] = Form.useForm();
  const [agents, setAgents] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function refreshSessions() {
    try {
      setSessions(await listDiagnosisSessions({ limit: 50 }));
    } catch (err) {
      setError(err.message);
    }
  }

  useEffect(() => {
    Promise.all([listAgents(), listDiagnosisSessions({ limit: 50 })])
      .then(([agentItems, sessionItems]) => {
        setAgents(agentItems);
        setSessions(sessionItems);
        const first = agentItems.find((item) => item.status === "ONLINE") || agentItems[0];
        if (first) form.setFieldsValue({ agent_id: first.id, host_id: first.hostname || first.id });
      })
      .catch((err) => setError(err.message));
  }, [form]);

  useEffect(() => {
    if (!selected?.diagnosis_id || TERMINAL.has(selected.status)) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const detail = await getDiagnosisSession(selected.diagnosis_id);
        setSelected(detail);
        refreshSessions();
      } catch (err) {
        setError(err.message);
      }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [selected?.diagnosis_id, selected?.status]);

  async function submit(values) {
    setLoading(true);
    setError("");
    try {
      const instanceId = values.instance_id || `${values.service_id}-1`;
      const detail = await createDiagnosisSession({
        query: values.query,
        context: {
          service_id: values.service_id,
          environment: values.environment,
          instances: [{
            service_id: values.service_id,
            instance_id: instanceId,
            host_id: values.host_id,
            agent_id: values.agent_id,
            pid: values.pid,
            environment: values.environment,
          }],
          dependencies: [],
        },
        budget_profile: values.budget_profile,
      });
      setSelected(detail);
      await refreshSessions();
      message.success("诊断会话已创建；系统将先复用已有证据并运行低风险探针");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function openSession(id) {
    setLoading(true);
    setError("");
    try {
      setSelected(await getDiagnosisSession(id));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function decideProbe(stepId, decision) {
    if (!selected) return;
    setLoading(true);
    try {
      const detail = await approveDiagnosisProbe(selected.diagnosis_id, {
        step_id: stepId,
        decision,
        scope: "single_execution",
        approver_id: "demo_user",
      });
      setSelected(detail);
      message.success(decision === "approve" ? "已批准本次探针" : "已拒绝本次探针");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  const agentOptions = agents.map((agent) => ({
    value: agent.id,
    label: `${agent.hostname || agent.id} · ${agent.status}`,
    disabled: agent.status !== "ONLINE",
  }));

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Space>
        <RobotOutlined style={{ fontSize: 22, color: "#722ed1" }} />
        <Typography.Title level={4} style={{ margin: 0 }}>AI 集群诊断</Typography.Title>
        <Tag color="purple">证据驱动</Tag>
      </Space>

      <Alert
        type="info"
        showIcon
        message="诊断智能体只可选择已注册探针；R2 深度采样必须逐次审批，R3 变更仅生成建议。"
      />
      {error && <Alert type="error" showIcon closable message={error} onClose={() => setError("")} />}

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={14}>
          <Card title="发起诊断" extra={<SafetyCertificateOutlined style={{ color: "#52c41a" }} />}>
            <Form
              form={form}
              layout="vertical"
              initialValues={{ environment: "production", budget_profile: "production_safe" }}
              onFinish={submit}
            >
              <Form.Item name="query" label="问题描述" rules={[{ required: true, min: 3 }]}>
                <Input.TextArea rows={3} maxLength={2000} showCount placeholder="例如：service-a 从十点开始变慢，检查自身、同机服务和一跳下游" />
              </Form.Item>
              <Row gutter={12}>
                <Col xs={24} md={12}>
                  <Form.Item name="service_id" label="服务 ID" rules={[{ required: true }]}>
                    <Input placeholder="service-a" />
                  </Form.Item>
                </Col>
                <Col xs={24} md={12}>
                  <Form.Item name="instance_id" label="实例 ID（可选）">
                    <Input placeholder="默认使用 service-id-1" />
                  </Form.Item>
                </Col>
                <Col xs={24} md={12}>
                  <Form.Item name="agent_id" label="目标 Agent" rules={[{ required: true }]}>
                    <Select options={agentOptions} placeholder="选择在线 Agent" />
                  </Form.Item>
                </Col>
                <Col xs={24} md={12}>
                  <Form.Item name="pid" label="目标 PID" rules={[{ required: true }]}>
                    <InputNumber min={1} max={4194304} style={{ width: "100%" }} />
                  </Form.Item>
                </Col>
                <Col xs={24} md={12}>
                  <Form.Item name="host_id" label="宿主机 ID" rules={[{ required: true }]}>
                    <Input />
                  </Form.Item>
                </Col>
                <Col xs={12} md={6}>
                  <Form.Item name="environment" label="环境">
                    <Select options={["production", "staging", "development"].map((value) => ({ value }))} />
                  </Form.Item>
                </Col>
                <Col xs={12} md={6}>
                  <Form.Item name="budget_profile" label="预算策略">
                    <Select options={[
                      { value: "production_safe", label: "生产安全" },
                      { value: "staging", label: "预发布" },
                      { value: "development", label: "开发" },
                    ]} />
                  </Form.Item>
                </Col>
              </Row>
              <Button type="primary" htmlType="submit" loading={loading} icon={<RobotOutlined />}>
                创建诊断会话
              </Button>
            </Form>
          </Card>
        </Col>

        <Col xs={24} xl={10}>
          <Card
            title="最近会话"
            extra={<Button size="small" icon={<ReloadOutlined />} onClick={refreshSessions}>刷新</Button>}
            bodyStyle={{ maxHeight: 470, overflow: "auto" }}
          >
            <List
              dataSource={sessions}
              locale={{ emptyText: "暂无 AI 诊断会话" }}
              renderItem={(item) => (
                <List.Item actions={[<Button key="open" type="link" onClick={() => openSession(item.diagnosis_id)}>查看</Button>]}>
                  <List.Item.Meta
                    title={<Space><Typography.Text>{item.target_scope?.target_service || "未绑定服务"}</Typography.Text><Status value={item.status} /></Space>}
                    description={<Typography.Text type="secondary" ellipsis>{item.raw_query}</Typography.Text>}
                  />
                </List.Item>
              )}
            />
          </Card>
        </Col>
      </Row>

      <Spin spinning={loading}>
        {selected ? <DiagnosisDetail detail={selected} onDecision={decideProbe} /> : <Card><Empty description="创建或打开一个诊断会话以查看假设、探针和证据" /></Card>}
      </Spin>
    </Space>
  );
}

function DiagnosisDetail({ detail, onDecision }) {
  const conclusion = detail.latest_conclusion;
  const candidates = conclusion?.root_cause_candidates || [];
  const hypotheses = detail.hypothesis_graph?.hypotheses || [];
  const probes = detail.probes || [];
  const evidence = detail.evidence || [];
  const evidenceMap = useMemo(() => new Map(evidence.map((item) => [item.evidence_id, item])), [evidence]);

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card title={<Space>诊断详情 <Status value={detail.status} /></Space>}>
        <Descriptions size="small" column={{ xs: 1, md: 3 }}>
          <Descriptions.Item label="诊断 ID"><Typography.Text copyable>{detail.diagnosis_id}</Typography.Text></Descriptions.Item>
          <Descriptions.Item label="目标服务">{detail.target_scope?.target_service || "未解析"}</Descriptions.Item>
          <Descriptions.Item label="拓扑快照">{detail.topology_snapshot_id}</Descriptions.Item>
          <Descriptions.Item label="症状">{detail.normalized_intent?.symptom}</Descriptions.Item>
          <Descriptions.Item label="模型">{detail.model_version}</Descriptions.Item>
          <Descriptions.Item label="规划器">{detail.planner_version}</Descriptions.Item>
        </Descriptions>
      </Card>

      {conclusion && (
        <Card title="最新结论">
          <Alert
            showIcon
            type={detail.status === "INSUFFICIENT_EVIDENCE" ? "warning" : "info"}
            message={conclusion.summary}
            description={`置信等级：${conclusion.confidence_level}`}
            style={{ marginBottom: 12 }}
          />
          <Table
            rowKey="candidate_id"
            size="small"
            pagination={false}
            dataSource={candidates}
            columns={[
              { title: "排名", dataIndex: "rank", width: 70 },
              { title: "候选", dataIndex: "candidate_id", width: 220 },
              { title: "置信等级", dataIndex: "confidence_level", width: 100, render: (value) => <Tag>{value}</Tag> },
              { title: "说明", dataIndex: "description" },
              {
                title: "证据",
                dataIndex: "evidence_refs",
                render: (refs = []) => <Space wrap>{refs.map((ref) => <Tag key={ref} color={evidenceMap.has(ref) ? "blue" : "red"}>{ref}</Tag>)}</Space>,
              },
            ]}
          />
          {conclusion.limitations?.length > 0 && (
            <Alert type="warning" message="限制与缺失证据" description={conclusion.limitations.join("；")} style={{ marginTop: 12 }} />
          )}
        </Card>
      )}

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={12}>
          <Card title="候选假设">
            <List
              dataSource={hypotheses}
              renderItem={(item) => (
                <List.Item>
                  <List.Item.Meta
                    title={<Space><Typography.Text>{item.type}</Typography.Text><Tag color={item.status === "SUPPORTED" ? "green" : "default"}>{item.status}</Tag></Space>}
                    description={item.description}
                  />
                </List.Item>
              )}
            />
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card title="受控探针与审批">
            <List
              dataSource={probes}
              locale={{ emptyText: "尚未规划探针" }}
              renderItem={(item) => (
                <List.Item
                  actions={item.status === "WAITING_APPROVAL" ? [
                    <Button key="approve" size="small" type="primary" icon={<CheckOutlined />} onClick={() => onDecision(item.step_id, "approve")}>单次批准</Button>,
                    <Button key="reject" size="small" danger icon={<CloseOutlined />} onClick={() => onDecision(item.step_id, "reject")}>拒绝</Button>,
                  ] : []}
                >
                  <List.Item.Meta
                    title={<Space><Typography.Text>{item.probe_id}</Typography.Text><Tag color={item.risk_level === "R2" ? "orange" : "green"}>{item.risk_level}</Tag><Status value={item.status} /></Space>}
                    description={`${item.reason} · ${item.parameters?.duration_sec || 0}s`}
                  />
                </List.Item>
              )}
            />
          </Card>
        </Col>
      </Row>

      <Card title={`证据血缘 (${evidence.length})`}>
        <Table
          rowKey="evidence_id"
          size="small"
          pagination={{ pageSize: 6 }}
          scroll={{ x: 900 }}
          dataSource={evidence}
          columns={[
            { title: "Evidence ID", dataIndex: "evidence_id", width: 210, render: (value) => <Typography.Text copyable>{value}</Typography.Text> },
            { title: "来源", dataIndex: "source_system", width: 170 },
            { title: "类型", dataIndex: "source_type", width: 150 },
            { title: "探针/查询", dataIndex: "query_or_probe", width: 150 },
            { title: "完整性 Hash", dataIndex: "integrity_hash", ellipsis: true },
          ]}
        />
      </Card>

      <Card title="状态事件">
        <Timeline
          items={(detail.events || []).map((event) => ({
            color: event.to_status === "FAILED" ? "red" : "blue",
            children: <Space><Typography.Text>{event.event_type}</Typography.Text><Status value={event.to_status} /></Space>,
          }))}
        />
      </Card>
    </Space>
  );
}
