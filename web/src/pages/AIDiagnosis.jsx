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
  Modal,
  Row,
  Select,
  Space,
  Spin,
  Steps,
  Table,
  Tabs,
  Tag,
  Timeline,
  Typography,
  message,
} from "antd";
import {
  CheckOutlined,
  CloseOutlined,
  ExperimentOutlined,
  EyeOutlined,
  MinusCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import { Link } from "react-router-dom";
import {
  approveDiagnosisProbe,
  createDiagnosisSession,
  getDiagnosisSession,
  listAgents,
  listDiagnosisSessions,
  runAIValidation,
} from "../api/client";
import usePolling from "../hooks/usePolling";
import TaskVisualizationPreview from "../components/TaskVisualizationPreview";

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

const NODE_LABELS = {
  understand_intent: "意图理解",
  resolve_scope: "范围/拓扑",
  build_hypotheses: "候选假设",
  plan_evidence: "证据规划",
  risk_gate: "风险门禁",
  run_probes: "受控采集",
  normalize_evidence: "证据归一化",
  analyze_evidence: "领域分析",
  assess_cluster: "跨节点归因",
  retrieve_knowledge: "知识检索",
  generate_actions: "动作生成",
  verify_report: "报告校验",
};

function nodeStepStatus(value) {
  if (value === "FAILED") return "error";
  if (value === "COMPLETED" || value === "SKIPPED") return "finish";
  if (value === "RUNNING" || value === "WAITING") return "process";
  return "wait";
}

function Status({ value }) {
  return <Tag color={STATUS_COLORS[value] || "default"}>{value || "UNKNOWN"}</Tag>;
}

function taskIdFromArtifactRef(value) {
  const match = String(value || "").match(/^task:([^:]+)/);
  return match?.[1] || "";
}

function TaskResultLink({ taskId, label = "查看结果" }) {
  if (!taskId) return "-";
  return (
    <Link to={`/task/${taskId}`}>
      <Button type="link" size="small" icon={<EyeOutlined />}>
        {label}
      </Button>
    </Link>
  );
}

function ArtifactReference({ value }) {
  if (!value) return "-";
  const taskId = taskIdFromArtifactRef(value);
  return (
    <Space size={4}>
      <Typography.Text ellipsis style={{ maxWidth: 150 }} title={value}>
        {value}
      </Typography.Text>
      {taskId && <TaskResultLink taskId={taskId} label="任务" />}
    </Space>
  );
}

function formatTimeRange(value) {
  if (!value?.start && !value?.end) return "未指定";
  return `${value.start || "?"} → ${value.end || "?"}`;
}

export default function AIDiagnosis() {
  const [form] = Form.useForm();
  const [agents, setAgents] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);
  const [validationRunning, setValidationRunning] = useState(false);
  const [error, setError] = useState("");
  const watchedInstances = Form.useWatch("instances", form) || [];

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
        if (first) {
          const instances = form.getFieldValue("instances") || [{}];
          if (!instances[0]?.agent_id) {
            form.setFieldsValue({
              instances: [{
                ...instances[0],
                agent_id: first.id,
                host_id: first.hostname || first.id,
              }, ...instances.slice(1)],
            });
          }
        }
      })
      .catch((err) => setError(err.message));
  }, [form]);

  usePolling(async () => {
    const detail = await getDiagnosisSession(selected.diagnosis_id);
    setSelected(detail);
    await refreshSessions();
  }, {
    interval: 3000,
    enabled: Boolean(selected?.diagnosis_id && !TERMINAL.has(selected.status)),
  });

  async function submit(values) {
    setLoading(true);
    setError("");
    try {
      const instances = values.instances.map((item, index) => ({
        service_id: item.service_id,
        instance_id: item.instance_id || `${item.service_id}-${index + 1}`,
        host_id: item.host_id,
        agent_id: item.agent_id,
        pid: item.pid,
        environment: item.environment || values.environment,
      }));
      const detail = await createDiagnosisSession({
        query: values.query,
        context: {
          service_id: values.target_service,
          environment: values.environment,
          instances,
          dependencies: values.dependencies || [],
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

  async function executeAIValidation() {
    setValidationRunning(true);
    try {
      const result = await runAIValidation();
      const columns = [
        { title: "层级", dataIndex: "layer", width: 130 },
        { title: "验证项", dataIndex: "name", width: 210 },
        {
          title: "结果",
          dataIndex: "status",
          width: 90,
          render: (value) => <Tag color={value === "PASS" ? "green" : "red"}>{value === "PASS" ? "通过" : "失败"}</Tag>,
        },
        { title: "耗时", dataIndex: "duration_ms", width: 100, render: (value) => `${value} ms` },
        { title: "说明", dataIndex: "detail" },
      ];
      const open = result.status === "PASSED" ? Modal.success : Modal.warning;
      open({
        title: `Drop AI 服务检测：${result.passed_count}/${result.total_count} 通过`,
        width: 1000,
        content: (
          <Space direction="vertical" style={{ width: "100%" }}>
            <Alert
              type={result.status === "PASSED" ? "success" : "warning"}
              showIcon
              message={`${result.provider} / ${result.model} · 总耗时 ${result.duration_ms} ms`}
              description="结果不包含 AI Key、余额金额或原始思维链。"
            />
            <Table
              rowKey="check_id"
              columns={columns}
              dataSource={result.checks || []}
              pagination={false}
              size="small"
              scroll={{ x: 850 }}
            />
          </Space>
        ),
      });
    } catch (err) {
      setError(err.message);
    } finally {
      setValidationRunning(false);
    }
  }

  function requestAIValidation() {
    Modal.confirm({
      title: "运行 Drop AI 服务检测？",
      content: "将真实调用 Provider、NLP、集群意图、总结和 RCA，产生少量 Token 费用。",
      okText: "开始检测",
      cancelText: "取消",
      onOk: executeAIValidation,
    });
  }

  const agentOptions = agents.map((agent) => ({
    value: agent.id,
    label: `${agent.hostname || agent.id} · ${agent.status}`,
    disabled: agent.status !== "ONLINE",
  }));
  const serviceOptions = [...new Set(
    watchedInstances.map((item) => item?.service_id?.trim()).filter(Boolean),
  )].map((value) => ({ value, label: value }));

  function selectAgent(instanceIndex, agentId) {
    const agent = agents.find((item) => item.id === agentId);
    if (agent) {
      form.setFieldValue(["instances", instanceIndex, "host_id"], agent.hostname || agent.id);
    }
  }

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Space>
        <RobotOutlined style={{ fontSize: 22, color: "#722ed1" }} />
        <Typography.Title level={4} style={{ margin: 0 }}>AI 集群诊断</Typography.Title>
        <Tag color="purple">证据驱动</Tag>
        <Button
          size="small"
          icon={<ExperimentOutlined />}
          loading={validationRunning}
          onClick={requestAIValidation}
        >
          AI 服务检测
        </Button>
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
              initialValues={{
                environment: "production",
                budget_profile: "production_safe",
                target_service: "service-a",
                instances: [{
                  service_id: "service-a",
                  instance_id: "service-a-1",
                  environment: "production",
                }],
                dependencies: [],
              }}
              onFinish={submit}
            >
              <Form.Item name="query" label="问题描述" rules={[{ required: true, min: 3 }]}>
                <Input.TextArea rows={3} maxLength={2000} showCount placeholder="例如：service-a 从十点开始变慢，检查自身、同机服务和一跳下游" />
              </Form.Item>
              <Row gutter={12}>
                <Col xs={24} md={12}>
                  <Form.Item name="target_service" label="诊断入口服务" rules={[{ required: true }]}>
                    <Select showSearch options={serviceOptions} placeholder="先在下方添加服务实例" />
                  </Form.Item>
                </Col>
                <Col xs={24} md={12}>
                  <Form.Item name="environment" label="默认环境">
                    <Select options={["production", "staging", "development"].map((value) => ({ value }))} />
                  </Form.Item>
                </Col>
                <Col xs={24} md={12}>
                  <Form.Item name="budget_profile" label="预算策略">
                    <Select options={[
                      { value: "production_safe", label: "生产安全" },
                      { value: "staging", label: "预发布" },
                      { value: "development", label: "开发" },
                    ]} />
                  </Form.Item>
                </Col>
              </Row>

              <Typography.Title level={5}>服务实例 / Worker</Typography.Title>
              <Form.List name="instances">
                {(fields, { add, remove }) => (
                  <Space direction="vertical" style={{ width: "100%" }}>
                    {fields.map((field, index) => (
                      <Card
                        key={field.key}
                        size="small"
                        title={`实例 ${index + 1}`}
                        extra={fields.length > 1 ? (
                          <Button danger type="text" icon={<MinusCircleOutlined />} onClick={() => remove(field.name)}>
                            删除
                          </Button>
                        ) : null}
                      >
                        <Row gutter={12}>
                          <Col xs={24} md={8}>
                            <Form.Item name={[field.name, "service_id"]} label="服务 ID" rules={[{ required: true }]}>
                              <Input placeholder="service-a" />
                            </Form.Item>
                          </Col>
                          <Col xs={24} md={8}>
                            <Form.Item name={[field.name, "instance_id"]} label="实例 ID" rules={[{ required: true }]}>
                              <Input placeholder="service-a-1" />
                            </Form.Item>
                          </Col>
                          <Col xs={24} md={8}>
                            <Form.Item name={[field.name, "agent_id"]} label="目标 Agent" rules={[{ required: true }]}>
                              <Select
                                options={agentOptions}
                                placeholder="选择在线 Agent"
                                onChange={(value) => selectAgent(field.name, value)}
                              />
                            </Form.Item>
                          </Col>
                          <Col xs={24} md={8}>
                            <Form.Item name={[field.name, "host_id"]} label="宿主机 ID" rules={[{ required: true }]}>
                              <Input placeholder="worker-1" />
                            </Form.Item>
                          </Col>
                          <Col xs={24} md={8}>
                            <Form.Item name={[field.name, "pid"]} label="目标 PID" rules={[{ required: true }]}>
                              <InputNumber min={1} max={4194304} style={{ width: "100%" }} />
                            </Form.Item>
                          </Col>
                          <Col xs={24} md={8}>
                            <Form.Item name={[field.name, "environment"]} label="实例环境">
                              <Select options={["production", "staging", "development"].map((value) => ({ value }))} />
                            </Form.Item>
                          </Col>
                        </Row>
                      </Card>
                    ))}
                    <Button
                      block
                      type="dashed"
                      icon={<PlusOutlined />}
                      onClick={() => add({ environment: form.getFieldValue("environment") })}
                    >
                      添加 Worker 实例
                    </Button>
                  </Space>
                )}
              </Form.List>

              <Typography.Title level={5} style={{ marginTop: 20 }}>服务依赖关系</Typography.Title>
              <Form.List name="dependencies">
                {(fields, { add, remove }) => (
                  <Space direction="vertical" style={{ width: "100%", marginBottom: 20 }}>
                    {fields.map((field, index) => (
                      <Row key={field.key} gutter={8} align="middle">
                        <Col xs={24} md={6}>
                          <Form.Item name={[field.name, "source_service"]} label={index === 0 ? "上游服务" : ""} rules={[{ required: true }]}>
                            <Select options={serviceOptions} placeholder="source" />
                          </Form.Item>
                        </Col>
                        <Col xs={24} md={6}>
                          <Form.Item name={[field.name, "target_service"]} label={index === 0 ? "下游服务" : ""} rules={[{ required: true }]}>
                            <Select options={serviceOptions} placeholder="target" />
                          </Form.Item>
                        </Col>
                        <Col xs={18} md={8}>
                          <Form.Item name={[field.name, "relation"]} label={index === 0 ? "关系" : ""} rules={[{ required: true }]}>
                            <Select options={[
                              "CALLS", "READS_FROM", "WRITES_TO", "PUBLISHES_TO", "CONSUMES_FROM", "SHARES_DEPENDENCY",
                            ].map((value) => ({ value }))} />
                          </Form.Item>
                        </Col>
                        <Col xs={6} md={4}>
                          <Button danger type="text" icon={<MinusCircleOutlined />} onClick={() => remove(field.name)}>删除</Button>
                        </Col>
                      </Row>
                    ))}
                    <Button
                      type="dashed"
                      icon={<PlusOutlined />}
                      disabled={serviceOptions.length < 2}
                      onClick={() => add({ relation: "CALLS", confidence: "high", source: "request_context" })}
                    >
                      添加依赖边
                    </Button>
                  </Space>
                )}
              </Form.List>
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
  const assessment = conclusion?.cluster_assessment;
  const commands = conclusion?.actions || conclusion?.diagnostic_commands || [];
  const findings = conclusion?.findings || [];
  const knowledge = conclusion?.knowledge_context || [];
  const verification = conclusion?.verification;
  const pipelineNodes = detail.pipeline_nodes || [];
  const hypotheses = detail.hypothesis_graph?.hypotheses || [];
  const probes = detail.probes || [];
  const evidence = detail.evidence || [];
  const coverage = detail.coverage || [];
  const probeTasks = probes.filter((item) => item.task_id);
  const evidenceMap = useMemo(() => new Map(evidence.map((item) => [item.evidence_id, item])), [evidence]);

  function requestDecision(item, decision) {
    if (decision === "reject") {
      onDecision(item.step_id, decision);
      return;
    }
    Modal.confirm({
      title: `单次批准 ${item.probe_id}？`,
      width: 720,
      okText: "仅批准本次执行",
      cancelText: "取消",
      content: (
        <Descriptions size="small" bordered column={1} style={{ marginTop: 16 }}>
          <Descriptions.Item label="目标">{JSON.stringify(item.target || {})}</Descriptions.Item>
          <Descriptions.Item label="参数">{JSON.stringify(item.parameters || {})}</Descriptions.Item>
          <Descriptions.Item label="风险"><Tag color="orange">{item.risk_level}</Tag> {item.reason}</Descriptions.Item>
          <Descriptions.Item label="预计成本">{item.estimated_cost || `${item.parameters?.duration_sec || 0}s`}</Descriptions.Item>
        </Descriptions>
      ),
      onOk: () => onDecision(item.step_id, decision),
    });
  }

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      <Card title={<Space>诊断详情 <Status value={detail.status} /></Space>}>
        <Descriptions size="small" column={{ xs: 1, md: 3 }}>
          <Descriptions.Item label="诊断 ID"><Typography.Text copyable>{detail.diagnosis_id}</Typography.Text></Descriptions.Item>
          <Descriptions.Item label="目标服务">{detail.target_scope?.target_service || "未解析"}</Descriptions.Item>
          <Descriptions.Item label="拓扑快照">{detail.topology_snapshot_id}</Descriptions.Item>
          <Descriptions.Item label="症状">{detail.normalized_intent?.symptom}</Descriptions.Item>
          <Descriptions.Item label="诊断模式"><Tag>{detail.normalized_intent?.diagnosis_mode || "UNKNOWN"}</Tag></Descriptions.Item>
          <Descriptions.Item label="请求时间窗" span={2}>{formatTimeRange(detail.requested_time_range)}</Descriptions.Item>
          <Descriptions.Item label="有效时间窗" span={2}>{formatTimeRange(detail.effective_time_range)}</Descriptions.Item>
          <Descriptions.Item label="模型">{detail.model_version}</Descriptions.Item>
          <Descriptions.Item label="规划器">{detail.planner_version}</Descriptions.Item>
        </Descriptions>
      </Card>

      <Card title="诊断流水线" extra={<Typography.Text type="secondary">状态、重试和节点输出均持久化</Typography.Text>}>
        <Steps
          size="small"
          responsive
          labelPlacement="vertical"
          items={pipelineNodes.map((node) => ({
            title: NODE_LABELS[node.node_name] || node.node_name,
            status: nodeStepStatus(node.status),
            description: (
              <Space direction="vertical" size={0}>
                <Tag color={node.status === "FAILED" ? "red" : node.status === "WAITING" ? "purple" : "default"}>{node.status}</Tag>
                {node.attempt > 0 && <Typography.Text type="secondary">attempt {node.attempt}</Typography.Text>}
              </Space>
            ),
          }))}
        />
      </Card>

      {conclusion && (
        <Card title="最新结论">
          <Alert
            showIcon
            type={detail.status === "INSUFFICIENT_EVIDENCE" ? "warning" : "info"}
            message={conclusion.summary}
            description={`置信等级：${conclusion.confidence_level || "不可判断"}`}
            style={{ marginBottom: 12 }}
          />
          {assessment && (
            <Descriptions
              size="small"
              bordered
              column={{ xs: 1, md: 3 }}
              style={{ marginBottom: 12 }}
            >
              <Descriptions.Item label="跨节点判断">{assessment.classification}</Descriptions.Item>
              <Descriptions.Item label="判断置信等级">{assessment.confidence_level || conclusion.confidence_level}</Descriptions.Item>
              <Descriptions.Item label="对比目标">{assessment.compared_targets?.length || 0}</Descriptions.Item>
              <Descriptions.Item label="根因位置">{conclusion.root_location?.type || "unknown"} / {conclusion.root_location?.target_ref || "-"}</Descriptions.Item>
              <Descriptions.Item label="问题领域">{conclusion.domain_cause?.type || "unknown"} / {conclusion.domain_cause?.subtype || "unknown"}</Descriptions.Item>
              <Descriptions.Item label="证据引用" span={3}>
                <Space wrap>
                  {(assessment.evidence_refs || []).map((ref) => (
                    <Tag key={ref} color={evidenceMap.has(ref) ? "blue" : "red"}>{ref}</Tag>
                  ))}
                </Space>
              </Descriptions.Item>
            </Descriptions>
          )}
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
          {verification && (
            <Alert
              showIcon
              type={verification.status === "passed" ? "success" : "error"}
              message={`报告校验：${verification.status}`}
              description={verification.issues?.length ? verification.issues.join("；") : `已检查 ${verification.checked_evidence_refs} 个证据引用、${verification.checked_knowledge_refs} 个知识引用和 ${verification.checked_actions} 个动作。`}
              style={{ marginTop: 12 }}
            />
          )}
        </Card>
      )}

      {probeTasks.length > 0 && (
        <Card
          title={`采集可视化证据 (${probeTasks.length})`}
          extra={<Typography.Text type="secondary">火焰图、TopN 与原始任务状态保持一致</Typography.Text>}
        >
          <Tabs
            items={probeTasks.map((probe) => ({
              key: probe.task_id,
              label: (
                <Space size={4}>
                  <Typography.Text>{probe.probe_id}</Typography.Text>
                  <Status value={probe.status} />
                </Space>
              ),
              children: <TaskVisualizationPreview taskId={probe.task_id} />,
            }))}
          />
        </Card>
      )}

      {commands.length > 0 && (
        <Card title="结构化诊断动作">
          <Alert
            showIcon
            type="warning"
            message="以下命令仅供人工审核，不会由 AI 自动执行；R2/R3 操作必须单次确认。"
            style={{ marginBottom: 12 }}
          />
          <Table
            rowKey="action_id"
            size="small"
            pagination={false}
            dataSource={commands}
            columns={[
              { title: "类型", dataIndex: "action_type", width: 90, render: (value) => <Tag>{value}</Tag> },
              { title: "用途", dataIndex: "title", width: 170 },
              {
                title: "风险",
                dataIndex: "risk_level",
                width: 90,
                render: (value, record) => (
                  <Space>
                    <Tag color={value === "R2" || value === "R3" ? "orange" : "green"}>{value}</Tag>
                    {record.requires_approval && <Tag color="purple">需审批</Tag>}
                  </Space>
                ),
              },
              {
                title: "命令",
                dataIndex: "rendered_command",
                render: (value) => <Typography.Text copyable code>{value}</Typography.Text>,
              },
              { title: "审核注释", dataIndex: "comment" },
            ]}
          />
        </Card>
      )}

      {findings.length > 0 && (
        <Card title={`确定性领域发现 (${findings.length})`}>
          <Table
            rowKey="finding_id"
            size="small"
            pagination={false}
            dataSource={findings}
            columns={[
              { title: "领域", dataIndex: "category", width: 90, render: (value) => <Tag>{value}</Tag> },
              { title: "发现", dataIndex: "finding_type", width: 210 },
              { title: "分析器", dataIndex: "analyzer_id", width: 190 },
              { title: "置信等级", dataIndex: "confidence_level", width: 100 },
              { title: "说明", dataIndex: "summary" },
            ]}
          />
        </Card>
      )}

      {knowledge.length > 0 && (
        <Card title="系统知识引用">
          <List
            dataSource={knowledge}
            renderItem={(item) => (
              <List.Item>
                <List.Item.Meta
                  title={<Space><Typography.Text>{item.title}</Typography.Text><Tag color="geekblue">{item.knowledge_id}</Tag></Space>}
                  description={<Space direction="vertical" size={2}><span>{item.summary}</span>{item.caveats?.length > 0 && <Typography.Text type="secondary">限制：{item.caveats.join("；")}</Typography.Text>}</Space>}
                />
              </List.Item>
            )}
          />
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
              renderItem={(item) => {
                const actions = [];
                if (item.status === "WAITING_APPROVAL") {
                  actions.push(
                    <Button key="approve" size="small" type="primary" icon={<CheckOutlined />} onClick={() => requestDecision(item, "approve")}>单次批准</Button>,
                    <Button key="reject" size="small" danger icon={<CloseOutlined />} onClick={() => requestDecision(item, "reject")}>拒绝</Button>,
                  );
                }
                if (item.task_id) {
                  actions.push(<TaskResultLink key="result" taskId={item.task_id} />);
                }
                return (
                  <List.Item actions={actions}>
                    <List.Item.Meta
                      title={<Space><Typography.Text>{item.probe_id}</Typography.Text><Tag color={item.risk_level === "R2" ? "orange" : "green"}>{item.risk_level}</Tag><Status value={item.status} /></Space>}
                      description={`${item.reason} · ${item.parameters?.duration_sec || 0}s`}
                    />
                  </List.Item>
                );
              }}
            />
          </Card>
        </Col>
      </Row>

      <Card title={`覆盖矩阵 (${coverage.length})`}>
        <Table
          rowKey="step_id"
          size="small"
          pagination={false}
          dataSource={coverage}
          columns={[
            { title: "目标", dataIndex: "target" },
            { title: "证据需求", dataIndex: "requirement" },
            { title: "状态", dataIndex: "status", render: (value) => <Status value={value} /> },
            {
              title: "任务结果",
              dataIndex: "task_id",
              render: (value) => <TaskResultLink taskId={value} />,
            },
            { title: "错误码", dataIndex: "error_code", render: (value) => value || "-" },
          ]}
        />
      </Card>

      <Card title={`证据血缘 (${evidence.length})`}>
        <Table
          rowKey="evidence_id"
          size="small"
          pagination={{ pageSize: 6 }}
          scroll={{ x: 1600 }}
          dataSource={evidence}
          columns={[
            { title: "Evidence ID", dataIndex: "evidence_id", width: 210, render: (value) => <Typography.Text copyable>{value}</Typography.Text> },
            { title: "来源", dataIndex: "source_system", width: 170 },
            { title: "类型", dataIndex: "source_type", width: 150 },
            { title: "角色", dataIndex: "evidence_role", width: 110, render: (value) => <Tag>{value || "incident"}</Tag> },
            { title: "事件时间窗", dataIndex: "event_time_range", width: 310, render: formatTimeRange },
            { title: "目标", dataIndex: "target", width: 260, render: (value) => JSON.stringify(value || {}) },
            { title: "探针/查询", dataIndex: "query_or_probe", width: 150 },
            { title: "质量", dataIndex: "data_quality", width: 180, render: (value) => `${value?.completeness || "unknown"} / ${(value?.domains || []).join(",") || "-"}` },
            {
              title: "原始产物",
              dataIndex: "raw_artifact_ref",
              width: 240,
              render: (value) => <ArtifactReference value={value} />,
            },
            {
              title: "派生产物",
              dataIndex: "derived_artifact_ref",
              width: 240,
              render: (value) => <ArtifactReference value={value} />,
            },
            { title: "派生版本", dataIndex: "derivation_version", width: 130 },
            { title: "完整性 Hash", dataIndex: "integrity_hash", width: 260, ellipsis: true },
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
