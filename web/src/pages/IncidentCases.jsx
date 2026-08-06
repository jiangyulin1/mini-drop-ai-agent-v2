import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Empty,
  Form,
  Input,
  List,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Table,
  Tabs,
  Tag,
  Timeline,
  Tooltip,
  Typography,
  message,
} from "antd";
import {
  CaretRightOutlined,
  EditOutlined,
  MessageOutlined,
  PauseOutlined,
  PlusOutlined,
  ReloadOutlined,
  SearchOutlined,
  StopOutlined,
} from "@ant-design/icons";
import {
  appendIncidentCaseMessage,
  correctIncidentCase,
  createIncidentCase,
  getIncidentCase,
  getCaseHypotheses,
  listCaseContextPackets,
  listCaseModelAttempts,
  listCaseIterations,
  listIncidentCaseEvents,
  listIncidentCases,
  startIncidentCaseDiagnosis,
  transitionIncidentCase,
} from "../api/client";
import styles from "./IncidentCases.module.css";

const STATE_META = {
  NEEDS_SCOPE_CONFIRMATION: ["需要范围", "orange"],
  OPEN: ["就绪", "blue"],
  INVESTIGATING: ["调查中", "processing"],
  WAITING_USER: ["等待用户", "orange"],
  RECOVERY_PLANNING: ["恢复规划", "purple"],
  VERIFYING: ["验证中", "cyan"],
  PAUSED: ["已暂停", "default"],
  RESOLVED: ["已恢复", "green"],
  INSUFFICIENT_EVIDENCE: ["证据不足", "gold"],
  STOPPED: ["已停止", "red"],
};

const MODE_LABEL = {
  ASSIST: "辅助",
  COLLABORATE: "协作",
  AUTHORIZED_AUTONOMY: "授权自治",
};

function StateTag({ value }) {
  const [label, color] = STATE_META[value] || [value, "default"];
  return <Tag color={color}>{label}</Tag>;
}

function textValue(value, fallback = "尚未确认") {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "string" || typeof value === "number") return String(value);
  return JSON.stringify(value, null, 2);
}

function formatTime(value) {
  if (!value) return "-";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
}

export default function IncidentCases() {
  const [cases, setCases] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState(null);
  const [events, setEvents] = useState([]);
  const [packets, setPackets] = useState([]);
  const [attempts, setAttempts] = useState([]);
  const [hypothesisGraph, setHypothesisGraph] = useState({ hypotheses: [], edges: [] });
  const [iterations, setIterations] = useState([]);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [secondaryLoading, setSecondaryLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [correctOpen, setCorrectOpen] = useState(false);
  const [messageText, setMessageText] = useState("");
  const [searchText, setSearchText] = useState("");
  const [stateFilter, setStateFilter] = useState("ACTIVE");
  const [activeTab, setActiveTab] = useState("activity");
  const [createForm] = Form.useForm();
  const [correctForm] = Form.useForm();

  const loadCases = useCallback(async ({ quiet = false } = {}) => {
    if (!quiet) setLoading(true);
    try {
      const result = await listIncidentCases({ limit: 200 });
      const items = result.items || [];
      setCases(items);
      setSelectedId((current) => (
        current && items.some((item) => item.case_id === current)
          ? current
          : items[0]?.case_id || ""
      ));
    } catch (error) {
      if (!quiet) message.error(`加载 Case 失败：${error.message}`);
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  const loadDetail = useCallback(async (caseId, { quiet = false } = {}) => {
    if (!caseId) {
      setDetail(null);
      return;
    }
    if (!quiet) setDetailLoading(true);
    try {
      const [caseDetail, eventResult] = await Promise.all([
        getIncidentCase(caseId),
        listIncidentCaseEvents(caseId, { limit: 200 }),
      ]);
      setDetail(caseDetail);
      setEvents(eventResult.items || []);
    } catch (error) {
      if (!quiet) message.error(`加载 Case 详情失败：${error.message}`);
    } finally {
      if (!quiet) setDetailLoading(false);
    }
  }, []);

  const loadSecondary = useCallback(async (caseId, tab, { quiet = false } = {}) => {
    if (!caseId || !["investigation", "audit"].includes(tab)) return;
    if (!quiet) setSecondaryLoading(true);
    try {
      if (tab === "investigation") {
        const [graphResult, iterationResult] = await Promise.all([
          getCaseHypotheses(caseId),
          listCaseIterations(caseId, { limit: 50 }),
        ]);
        setHypothesisGraph(graphResult || { hypotheses: [], edges: [] });
        setIterations(iterationResult.items || []);
      } else {
        const [packetResult, attemptResult] = await Promise.all([
          listCaseContextPackets(caseId, { limit: 50 }),
          listCaseModelAttempts(caseId, { limit: 50 }),
        ]);
        setPackets(packetResult.items || []);
        setAttempts(attemptResult.items || []);
      }
    } catch (error) {
      if (!quiet) message.error(`加载${tab === "audit" ? "审计" : "调查"}详情失败：${error.message}`);
    } finally {
      if (!quiet) setSecondaryLoading(false);
    }
  }, []);

  useEffect(() => {
    loadCases();
    const timer = window.setInterval(() => loadCases({ quiet: true }), 5000);
    return () => window.clearInterval(timer);
  }, [loadCases]);

  useEffect(() => {
    setActiveTab("activity");
    setPackets([]);
    setAttempts([]);
    setHypothesisGraph({ hypotheses: [], edges: [] });
    setIterations([]);
    loadDetail(selectedId);
    if (!selectedId) return undefined;
    const timer = window.setInterval(() => loadDetail(selectedId, { quiet: true }), 5000);
    return () => window.clearInterval(timer);
  }, [loadDetail, selectedId]);

  useEffect(() => {
    if (!selectedId || activeTab === "activity" || activeTab === "scope") return undefined;
    loadSecondary(selectedId, activeTab);
    const timer = window.setInterval(
      () => loadSecondary(selectedId, activeTab, { quiet: true }),
      10000,
    );
    return () => window.clearInterval(timer);
  }, [activeTab, loadSecondary, selectedId]);

  const activeCount = useMemo(
    () => cases.filter((item) => !["RESOLVED", "STOPPED", "INSUFFICIENT_EVIDENCE"].includes(item.state)).length,
    [cases],
  );

  const filteredCases = useMemo(() => {
    const query = searchText.trim().toLowerCase();
    return cases.filter((item) => {
      const isTerminal = ["RESOLVED", "STOPPED", "INSUFFICIENT_EVIDENCE"].includes(item.state);
      if (stateFilter === "ACTIVE" && isTerminal) return false;
      if (stateFilter !== "ALL" && stateFilter !== "ACTIVE" && item.state !== stateFilter) return false;
      if (!query) return true;
      return [
        item.title,
        item.problem_description,
        item.environment,
        item.target_scope?.service_id,
      ].some((value) => String(value || "").toLowerCase().includes(query));
    });
  }, [cases, searchText, stateFilter]);

  async function refresh() {
    await Promise.all([
      loadCases(),
      selectedId ? loadDetail(selectedId) : Promise.resolve(),
      selectedId ? loadSecondary(selectedId, activeTab) : Promise.resolve(),
    ]);
  }

  async function createCase() {
    const values = await createForm.validateFields();
    setActionLoading(true);
    try {
      const created = await createIncidentCase({
        title: values.title,
        problem_description: values.problem_description,
        recovery_goal: values.recovery_goal,
        run_mode: values.run_mode,
        environment: values.environment,
        target_scope: values.service_id ? { service_id: values.service_id } : {},
      });
      message.success("Case 已创建");
      setCreateOpen(false);
      createForm.resetFields();
      await loadCases();
      setSelectedId(created.case_id);
    } catch (error) {
      message.error(`创建失败：${error.message}`);
    } finally {
      setActionLoading(false);
    }
  }

  async function transition(action, reason) {
    if (!detail) return;
    setActionLoading(true);
    try {
      await transitionIncidentCase(detail.case_id, action, {
        reason,
        expected_row_version: detail.row_version,
      });
      message.success(action === "pause" ? "Case 已暂停" : action === "resume" ? "Case 已恢复" : "Case 已停止");
      await refresh();
    } catch (error) {
      message.error(`操作失败：${error.message}`);
      await refresh();
    } finally {
      setActionLoading(false);
    }
  }

  async function startDiagnosis() {
    setActionLoading(true);
    try {
      await startIncidentCaseDiagnosis(detail.case_id, {
        expected_row_version: detail.row_version,
      });
      message.success("诊断已启动，ContextPacket 已进入审计轨迹");
      await refresh();
    } catch (error) {
      message.error(`启动失败：${error.message}`);
      await refresh();
    } finally {
      setActionLoading(false);
    }
  }

  async function sendMessage() {
    const content = messageText.trim();
    if (!content || !detail) return;
    setActionLoading(true);
    try {
      await appendIncidentCaseMessage(detail.case_id, { content, kind: "message" });
      setMessageText("");
      await refresh();
    } catch (error) {
      message.error(`发送失败：${error.message}`);
    } finally {
      setActionLoading(false);
    }
  }

  function openCorrection() {
    correctForm.setFieldsValue({
      service_id: detail.target_scope?.service_id || "",
      recovery_goal: detail.recovery_goal,
      reason: "用户修正 Case 范围或恢复目标",
    });
    setCorrectOpen(true);
  }

  async function submitCorrection() {
    const values = await correctForm.validateFields();
    setActionLoading(true);
    try {
      await correctIncidentCase(detail.case_id, {
        target_scope: values.service_id ? { service_id: values.service_id } : {},
        recovery_goal: values.recovery_goal,
        reason: values.reason,
        expected_row_version: detail.row_version,
      });
      message.success("修正已保存，旧范围下的待执行计划已失效");
      setCorrectOpen(false);
      await refresh();
    } catch (error) {
      message.error(`修正失败：${error.message}`);
    } finally {
      setActionLoading(false);
    }
  }

  const summary = detail?.summary || {};
  const terminal = ["RESOLVED", "STOPPED", "INSUFFICIENT_EVIDENCE"].includes(detail?.state);
  const canStart = detail
    && detail.run_mode !== "ASSIST"
    && detail.state === "OPEN"
    && !detail.diagnosis_session_id;
  const startBlockedByMode = detail
    && detail.state === "OPEN"
    && !detail.diagnosis_session_id
    && detail.run_mode === "ASSIST";

  return (
    <Space direction="vertical" size={16} className={styles.page}>
      <div className={styles.header}>
        <div>
          <Typography.Title level={3} style={{ margin: 0 }}>AI Case 工作台</Typography.Title>
          <Typography.Text type="secondary">集中查看当前判断、待办事项和恢复目标；技术细节按需展开。</Typography.Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={refresh}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setCreateOpen(true)}>新建 Case</Button>
        </Space>
      </div>

      <Spin spinning={loading}>
        <Row gutter={[16, 16]} align="stretch">
          <Col xs={24} lg={7}>
            <Card
              title={<Space>Case <Tag>{filteredCases.length}/{cases.length}</Tag></Space>}
              extra={<Typography.Text type="secondary">进行中 {activeCount}</Typography.Text>}
              className={styles.caseListCard}
              styles={{ body: { padding: 0 } }}
            >
              <div className={styles.caseFilters}>
                <Input
                  allowClear
                  prefix={<SearchOutlined />}
                  value={searchText}
                  onChange={(event) => setSearchText(event.target.value)}
                  placeholder="搜索标题、服务或环境"
                  aria-label="搜索 Case"
                />
                <Select
                  value={stateFilter}
                  onChange={setStateFilter}
                  aria-label="筛选 Case 状态"
                  options={[
                    { value: "ACTIVE", label: "进行中" },
                    { value: "ALL", label: "全部状态" },
                    ...Object.entries(STATE_META).map(([value, [label]]) => ({ value, label })),
                  ]}
                />
              </div>
              <List
                dataSource={filteredCases}
                locale={{ emptyText: <Empty description={cases.length ? "没有匹配的 Case" : "尚无 Case"} /> }}
                renderItem={(item) => (
                  <List.Item
                    className={`${styles.caseItem} ${selectedId === item.case_id ? styles.selected : ""}`}
                    onClick={() => setSelectedId(item.case_id)}
                  >
                    <List.Item.Meta
                      title={<Space><Typography.Text strong>{item.title}</Typography.Text><StateTag value={item.state} /></Space>}
                      description={(
                        <Space direction="vertical" size={0}>
                          <Typography.Text type="secondary">
                            {item.target_scope?.service_id || "待补充服务"} · {item.environment} · {MODE_LABEL[item.run_mode] || item.run_mode}
                          </Typography.Text>
                          <Typography.Text type="secondary">更新于 {formatTime(item.updated_at)}</Typography.Text>
                        </Space>
                      )}
                    />
                  </List.Item>
                )}
              />
            </Card>
          </Col>

          <Col xs={24} lg={17}>
            <Spin spinning={detailLoading}>
              {!detail ? (
                <Card><Empty description="请选择或创建一个 Case" /></Card>
              ) : (
                <Space direction="vertical" size={16} style={{ width: "100%" }}>
                <Card>
                  <div className={styles.caseHeader}>
                    <div>
                      <Space wrap>
                        <Typography.Title level={4} style={{ margin: 0 }}>{detail.title}</Typography.Title>
                        <StateTag value={detail.state} />
                        <Tag>{MODE_LABEL[detail.run_mode] || detail.run_mode}</Tag>
                      </Space>
                      <Typography.Paragraph type="secondary" style={{ margin: "8px 0 0" }}>
                        {detail.problem_description}
                      </Typography.Paragraph>
                    </div>
                    <Space wrap>
                      <Button icon={<EditOutlined />} disabled={terminal} onClick={openCorrection}>修正</Button>
                      {canStart && <Button type="primary" icon={<CaretRightOutlined />} loading={actionLoading} onClick={startDiagnosis}>开始调查</Button>}
                      {startBlockedByMode && (
                        <Tooltip title="辅助模式不会自动启动诊断；请先将运行模式修正为“协作”或“授权自治”">
                          <Button type="primary" icon={<CaretRightOutlined />} disabled>开始调查</Button>
                        </Tooltip>
                      )}
                      {detail.state === "PAUSED" ? (
                        <Button icon={<CaretRightOutlined />} loading={actionLoading} onClick={() => transition("resume", "用户从 Case 工作台恢复调查")}>恢复</Button>
                      ) : (
                        <Button icon={<PauseOutlined />} disabled={terminal} loading={actionLoading} onClick={() => transition("pause", "用户从 Case 工作台暂停调查")}>暂停</Button>
                      )}
                      <Popconfirm title="停止后将取消关联诊断并撤销 Case Grant，确认继续？" onConfirm={() => transition("stop", "用户从 Case 工作台停止") }>
                        <Button danger icon={<StopOutlined />} disabled={terminal} loading={actionLoading}>停止</Button>
                      </Popconfirm>
                    </Space>
                  </div>
                </Card>

                {detail.state === "NEEDS_SCOPE_CONFIRMATION" && (
                  <Alert showIcon type="warning" message="需要确认调查范围" description={summary.need_you?.question || "请补充目标服务或资源。"} />
                )}

                <CaseOverview detail={detail} summary={summary} />

                <Card>
                  <Tabs activeKey={activeTab} onChange={setActiveTab} items={[
                    {
                      key: "activity",
                      label: `动态与沟通 (${events.length})`,
                      children: (
                        <CaseActivity
                          events={events}
                          messageText={messageText}
                          onMessageChange={setMessageText}
                          onSend={sendMessage}
                          sending={actionLoading}
                          disabled={detail.state === "STOPPED"}
                        />
                      ),
                    },
                    {
                      key: "investigation",
                      label: "调查依据",
                      children: <Spin spinning={secondaryLoading}><InvestigationDetail graph={hypothesisGraph} iterations={iterations} /></Spin>,
                    },
                    {
                      key: "audit",
                      label: "技术审计",
                      children: <Spin spinning={secondaryLoading}><ModelAudit packets={packets} attempts={attempts} /></Spin>,
                    },
                    {
                      key: "scope",
                      label: "范围与恢复目标",
                      children: (
                        <Descriptions bordered size="small" column={1}>
                          <Descriptions.Item label="环境">{detail.environment}</Descriptions.Item>
                          <Descriptions.Item label="目标范围"><pre className={styles.pre}>{textValue(detail.target_scope)}</pre></Descriptions.Item>
                          <Descriptions.Item label="时间范围"><pre className={styles.pre}>{textValue(detail.time_range)}</pre></Descriptions.Item>
                          <Descriptions.Item label="恢复目标">{detail.recovery_goal}</Descriptions.Item>
                          <Descriptions.Item label="范围版本">v{detail.scope_revision}</Descriptions.Item>
                        </Descriptions>
                      ),
                    },
                  ]} />
                </Card>
              </Space>
              )}
            </Spin>
          </Col>
        </Row>
      </Spin>

      <Modal title="新建 Incident Case" open={createOpen} onCancel={() => setCreateOpen(false)} onOk={createCase} confirmLoading={actionLoading} width={680} destroyOnHidden>
        <Form form={createForm} layout="vertical" initialValues={{ run_mode: "COLLABORATE", environment: "production" }}>
          <Form.Item name="title" label="标题" rules={[{ required: true, min: 3 }]}><Input maxLength={256} /></Form.Item>
          <Form.Item name="problem_description" label="问题描述" rules={[{ required: true, min: 3 }]}><Input.TextArea rows={4} maxLength={4000} /></Form.Item>
          <Form.Item name="recovery_goal" label="恢复目标" rules={[{ required: true, min: 3 }]}><Input.TextArea rows={2} maxLength={2000} /></Form.Item>
          <Row gutter={12}>
            <Col xs={24} md={12}><Form.Item name="run_mode" label="运行模式" extra="协作模式可启动受控诊断；辅助模式仅记录人工信息。"><Select options={Object.entries(MODE_LABEL).map(([value, label]) => ({ value, label }))} /></Form.Item></Col>
            <Col xs={24} md={12}><Form.Item name="environment" label="环境" rules={[{ required: true }]}><Select options={["production", "staging", "development"].map((value) => ({ value }))} /></Form.Item></Col>
          </Row>
          <Form.Item name="service_id" label="目标服务（可稍后补充）"><Input placeholder="例如 checkout" /></Form.Item>
        </Form>
      </Modal>

      <Modal title="修正 Case 范围" open={correctOpen} onCancel={() => setCorrectOpen(false)} onOk={submitCorrection} confirmLoading={actionLoading} width={620}>
        <Alert type="info" showIcon message="修正会提升 scope_revision，并使旧范围下尚未执行的计划失效。" style={{ marginBottom: 16 }} />
        <Form form={correctForm} layout="vertical">
          <Form.Item name="service_id" label="目标服务"><Input /></Form.Item>
          <Form.Item name="recovery_goal" label="恢复目标" rules={[{ required: true, min: 3 }]}><Input.TextArea rows={2} /></Form.Item>
          <Form.Item name="reason" label="修正原因" rules={[{ required: true }]}><Input.TextArea rows={2} /></Form.Item>
        </Form>
      </Modal>
    </Space>
  );
}

function CaseOverview({ detail, summary }) {
  const finding = summary.current_finding?.statement || summary.current_finding;
  const needsUser = Boolean(summary.need_you?.required);
  return (
    <Card
      className={styles.overviewCard}
      title="当前判断"
      extra={summary.current_finding?.status ? <Tag>{summary.current_finding.status}</Tag> : null}
    >
      <Typography.Paragraph className={styles.finding}>
        {textValue(finding, "尚未形成判断")}
      </Typography.Paragraph>
      {needsUser && (
        <Alert
          showIcon
          type="warning"
          message="需要你确认"
          description={summary.need_you.question || "请补充调查所需的信息。"}
          className={styles.needUser}
        />
      )}
      <div className={styles.summaryGrid}>
        <SummaryItem label="影响" value={summary.impact?.message || summary.impact} />
        <SummaryItem label="AI 当前动作" value={summary.what_ai_is_doing?.message || summary.what_ai_is_doing} />
        <SummaryItem label="恢复目标" value={summary.recovery?.goal || detail.recovery_goal} />
      </div>
    </Card>
  );
}

function SummaryItem({ label, value }) {
  return (
    <div className={styles.summaryItem}>
      <Typography.Text type="secondary">{label}</Typography.Text>
      <Typography.Paragraph ellipsis={{ rows: 3, expandable: true, symbol: "展开" }}>
        {textValue(value)}
      </Typography.Paragraph>
    </div>
  );
}

function CaseActivity({ events, messageText, onMessageChange, onSend, sending, disabled }) {
  return (
    <Space direction="vertical" size={20} style={{ width: "100%" }}>
      <div className={styles.messageComposer}>
        <Space align="center"><MessageOutlined /><Typography.Text strong>补充信息</Typography.Text></Space>
        <Input.TextArea
          value={messageText}
          onChange={(event) => onMessageChange(event.target.value)}
          rows={3}
          maxLength={8000}
          showCount
          disabled={disabled}
          aria-label="补充 Case 信息"
          placeholder="补充发布、影响、人工操作，或希望 AI 解释的问题"
        />
        <div className={styles.composerActions}>
          <Typography.Text type="secondary">信息会记录到 Case 时间线</Typography.Text>
          <Button type="primary" disabled={!messageText.trim() || disabled} loading={sending} onClick={onSend}>发送</Button>
        </div>
      </div>
      <CaseTimeline events={events} />
    </Space>
  );
}

function CaseTimeline({ events }) {
  if (!events.length) return <Empty description="尚无时间线事件" />;
  return (
    <Timeline items={[...events].reverse().map((event) => ({
      key: event.event_id,
      color: event.event_type.includes("stopped") ? "red" : event.event_type.includes("corrected") ? "orange" : "blue",
      children: (
        <Space direction="vertical" size={2}>
          <Space wrap><Typography.Text strong>{event.event_type}</Typography.Text><Typography.Text type="secondary">{formatTime(event.created_at)}</Typography.Text></Space>
          {event.payload?.content && <Typography.Paragraph style={{ margin: 0 }}>{event.payload.content}</Typography.Paragraph>}
          {!event.payload?.content && <Typography.Text type="secondary">{textValue(event.payload, "")}</Typography.Text>}
        </Space>
      ),
    }))} />
  );
}

function ModelAudit({ packets, attempts }) {
  if (!packets.length && !attempts.length) {
    return <Empty description="尚未产生 Case 模型调用；规则降级不会伪造模型审计记录" />;
  }
  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Alert type="info" showIcon message="这里只展示 Context 投影和调用元数据，不展示 API Key、原始思维链或完整模型响应。" />
      <List
        header={<Typography.Text strong>ContextPacket</Typography.Text>}
        dataSource={packets}
        renderItem={(item) => (
          <List.Item>
            <List.Item.Meta
              title={<Space><Tag color="purple">{item.schema_version}</Tag><Typography.Text>{item.purpose}</Typography.Text></Space>}
              description={`iteration ${item.iteration_no} · ${item.content_hash.slice(0, 16)}… · ${formatTime(item.created_at)} · ${item.projection_stats?.optimized_chars || 0} chars`}
            />
          </List.Item>
        )}
      />
      <List
        header={<Typography.Text strong>ModelAttempt</Typography.Text>}
        dataSource={attempts}
        locale={{ emptyText: "本次使用规则降级，未调用模型" }}
        renderItem={(item) => (
          <List.Item>
            <List.Item.Meta
              title={<Space><Tag color={item.status === "SUCCEEDED" ? "green" : "red"}>{item.status}</Tag><Typography.Text>{item.provider} / {item.model_snapshot || item.model}</Typography.Text></Space>}
              description={`${item.prompt_version} → ${item.output_schema} · ${item.latency_ms}ms · ${item.input_tokens ?? "?"}/${item.output_tokens ?? "?"} tokens · response ${item.response_hash?.slice(0, 16) || "-"}…`}
            />
          </List.Item>
        )}
      />
    </Space>
  );
}

function InvestigationDetail({ graph, iterations }) {
  const hypotheses = graph.hypotheses || [];
  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Table
        rowKey="hypothesis_id"
        size="small"
        pagination={false}
        locale={{ emptyText: "尚未形成 Case 候选图" }}
        dataSource={hypotheses}
        columns={[
          { title: "候选", dataIndex: "statement" },
          { title: "机制", dataIndex: "mechanism", width: 150, render: (value) => value || "-" },
          { title: "状态", dataIndex: "status", width: 110, render: (value) => <Tag>{value}</Tag> },
          { title: "支持/反证", width: 110, render: (_, item) => `${item.supporting_evidence_refs?.length || 0}/${item.contradicting_evidence_refs?.length || 0}` },
          { title: "缺口", width: 80, render: (_, item) => item.missing_evidence?.length || 0 },
        ]}
      />
      <List
        header={<Typography.Text strong>InvestigationIteration</Typography.Text>}
        locale={{ emptyText: "尚无调查迭代" }}
        dataSource={iterations}
        renderItem={(item) => (
          <List.Item>
            <List.Item.Meta
              title={<Space><Tag color="blue">#{item.iteration_no}</Tag><Typography.Text>{item.selected_action?.action_id || "未选择动作"}</Typography.Text></Space>}
              description={`utility ${item.selected_action?.utility ?? "-"} · policy ${item.policy_decision?.decision || "-"} · ${item.stop_decision?.reason || "CONTINUE"}`}
            />
          </List.Item>
        )}
      />
    </Space>
  );
}
