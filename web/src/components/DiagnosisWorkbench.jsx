import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Empty,
  Progress,
  Row,
  Segmented,
  Select,
  Space,
  Statistic,
  Steps,
  Table,
  Tag,
  Timeline,
  Typography,
} from "antd";
import {
  CaretRightOutlined,
  FileSearchOutlined,
  PauseOutlined,
  RedoOutlined,
  StepBackwardOutlined,
  StepForwardOutlined,
} from "@ant-design/icons";
import { getDiagnosisSession } from "../api/client";
import styles from "./DiagnosisWorkbench.module.css";

const NODE_LABELS = {
  understand_intent: "意图理解",
  resolve_scope: "范围与拓扑",
  build_hypotheses: "候选假设",
  plan_evidence: "证据规划",
  risk_gate: "风险门禁",
  run_probes: "并发采集",
  normalize_evidence: "证据归一化",
  analyze_evidence: "领域分析",
  assess_cluster: "集群归因",
  retrieve_knowledge: "知识检索",
  generate_actions: "优化建议",
  verify_report: "报告校验",
};

const NODE_NARRATIVES = {
  understand_intent: "把自然语言问题转换为受约束的症状、模式和时间策略。",
  resolve_scope: "固定服务、实例、主机、Agent、PID 和诊断时间窗，防止跨目标取证。",
  build_hypotheses: "基于症状与拓扑生成候选原因；此时仅是待验证假设，不是结论。",
  plan_evidence: "选择能够区分候选假设的低成本证据，并记录预期输出。",
  risk_gate: "核对探针注册、目标范围、预算和审批要求。",
  run_probes: "在预算允许的并行度内下发采集；同一轮全部结束后才进入跨节点分析。",
  normalize_evidence: "将任务和产物转换为带目标、时间、质量与完整性标识的证据。",
  analyze_evidence: "确定性分析器从结构化观测中提取领域发现和根因候选。",
  assess_cluster: "比较目标实例、同宿主实例和下游节点，形成支持与反驳关系。",
  retrieve_knowledge: "只检索与已确认领域发现相关的系统知识，不把知识当成事实证据。",
  generate_actions: "生成可审阅的采集或人工优化动作；不会自动执行高风险变更。",
  verify_report: "验证证据引用、时间域、目标域和动作策略后才持久化结论。",
};

const STRATEGY_LABELS = {
  CONSTRAINED_HYBRID: "受约束混合路径",
  DECISION_TREE: "固定决策树",
  EXPLORATORY: "广度探索路径",
};

const HYPOTHESIS_STATUS = {
  SUPPORTED: { color: "green", label: "支持" },
  RULED_OUT: { color: "red", label: "已排除" },
  INCONCLUSIVE: { color: "gold", label: "证据不足" },
  UNTESTED: { color: "default", label: "待验证" },
};

function formatTime(value) {
  if (!value) return "-";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
}

function formatDuration(start, end) {
  const value = (new Date(end).getTime() - new Date(start).getTime()) / 1000;
  return Number.isFinite(value) && value >= 0 ? `${value.toFixed(1)}s` : "-";
}

function compactRef(value) {
  const text = String(value || "");
  return text.length > 34 ? `${text.slice(0, 16)}…${text.slice(-12)}` : text;
}

function stepStatus(node, replayIndex, nodeIndex) {
  if (nodeIndex < replayIndex) return "finish";
  if (nodeIndex === replayIndex) {
    if (node.status === "FAILED") return "error";
    return "process";
  }
  return "wait";
}

function metricEntries(metrics) {
  return Object.entries(metrics || {}).filter(([, value]) => (
    typeof value === "string" || typeof value === "number" || typeof value === "boolean"
  ));
}

function evidenceQuality(item) {
  const completeness = item?.data_quality?.completeness || "unknown";
  const reasons = item?.data_quality?.reasons || [];
  return reasons.length ? `${completeness} · ${reasons.join(", ")}` : completeness;
}

function ReplayView({ detail }) {
  const nodes = detail.pipeline_nodes || [];
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const current = nodes[index];
  const hypotheses = detail.hypothesis_graph?.hypotheses || [];
  const probes = detail.probes || [];
  const evidence = detail.evidence || [];
  const conclusion = detail.latest_conclusion;

  useEffect(() => {
    setIndex(0);
    setPlaying(false);
  }, [detail.diagnosis_id]);

  useEffect(() => {
    if (!playing || nodes.length === 0) return undefined;
    const timer = window.setInterval(() => {
      setIndex((value) => {
        if (value >= nodes.length - 1) {
          setPlaying(false);
          return value;
        }
        return value + 1;
      });
    }, 2400);
    return () => window.clearInterval(timer);
  }, [playing, nodes.length]);

  if (!current) return <Empty description="该历史会话没有流水线记录" />;

  const showHypotheses = index >= 2;
  const showProbes = index >= 3;
  const showEvidence = index >= 6;
  const showConclusion = index >= nodes.length - 1 && conclusion;

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Space wrap>
        <Button
          icon={playing ? <PauseOutlined /> : <CaretRightOutlined />}
          type="primary"
          onClick={() => setPlaying((value) => !value)}
        >
          {playing ? "暂停" : "自动播放"}
        </Button>
        <Button
          icon={<StepBackwardOutlined />}
          disabled={index === 0}
          onClick={() => setIndex((value) => Math.max(0, value - 1))}
        >
          上一步
        </Button>
        <Button
          icon={<StepForwardOutlined />}
          disabled={index >= nodes.length - 1}
          onClick={() => setIndex((value) => Math.min(nodes.length - 1, value + 1))}
        >
          下一步
        </Button>
        <Button icon={<RedoOutlined />} onClick={() => { setIndex(0); setPlaying(false); }}>
          从头演示
        </Button>
        <Typography.Text type="secondary">
          第 {index + 1}/{nodes.length} 步 · 以下内容来自本次真实会话快照
        </Typography.Text>
      </Space>

      <Steps
        size="small"
        responsive
        current={index}
        onChange={(value) => { setIndex(value); setPlaying(false); }}
        items={nodes.map((node, nodeIndex) => ({
          title: NODE_LABELS[node.node_name] || node.node_name,
          status: stepStatus(node, index, nodeIndex),
        }))}
      />

      <Card
        className={styles.stageCard}
        title={<Space><FileSearchOutlined />当前步骤：{NODE_LABELS[current.node_name] || current.node_name}</Space>}
        extra={<Tag color={current.status === "FAILED" ? "red" : "blue"}>{current.status}</Tag>}
      >
        <Alert
          type="info"
          showIcon
          message={NODE_NARRATIVES[current.node_name]}
          description="页面展示的是结构化决策摘要、输入输出和证据引用，不展示模型原始思维链。"
          style={{ marginBottom: 16 }}
        />
        <Descriptions size="small" bordered column={{ xs: 1, md: 3 }}>
          <Descriptions.Item label="执行次数">{current.attempt}</Descriptions.Item>
          <Descriptions.Item label="开始">{formatTime(current.started_at)}</Descriptions.Item>
          <Descriptions.Item label="结束">{formatTime(current.finished_at)}</Descriptions.Item>
          <Descriptions.Item label="输入引用" span={3}>
            <Space wrap>
              {(current.input_refs || []).length
                ? current.input_refs.map((ref) => <Tag key={ref} title={ref}>{compactRef(ref)}</Tag>)
                : "无"}
            </Space>
          </Descriptions.Item>
          <Descriptions.Item label="输出引用" span={3}>
            <Space wrap>
              {(current.output_refs || []).length
                ? current.output_refs.map((ref) => <Tag color="blue" key={ref} title={ref}>{compactRef(ref)}</Tag>)
                : "无"}
            </Space>
          </Descriptions.Item>
          {metricEntries(current.metrics).map(([key, value]) => (
            <Descriptions.Item label={key} key={key}>{String(value)}</Descriptions.Item>
          ))}
          {current.error_message && (
            <Descriptions.Item label="失败原因" span={3}>
              <Typography.Text type="danger">{current.error_code}: {current.error_message}</Typography.Text>
            </Descriptions.Item>
          )}
        </Descriptions>
      </Card>

      {showHypotheses && <HypothesisBoard hypotheses={hypotheses} compact={index < 8} />}
      {showProbes && (
        <Card size="small" title={`本次探针执行 (${probes.length})`}>
          <Table
            rowKey="step_id"
            size="small"
            pagination={false}
            dataSource={probes}
            columns={[
              { title: "探针", dataIndex: "probe_id" },
              { title: "目标", dataIndex: ["target", "instance_id"] },
              { title: "原因", dataIndex: "reason" },
              { title: "风险", dataIndex: "risk_level", render: (value) => <Tag color={value === "R2" ? "orange" : "green"}>{value}</Tag> },
              { title: "状态", dataIndex: "status", render: (value) => <Tag>{value}</Tag> },
            ]}
          />
        </Card>
      )}
      {showEvidence && <EvidenceChain detail={detail} compact />}
      {showConclusion && (
        <Alert
          type={conclusion.verification?.status === "passed" ? "success" : "warning"}
          showIcon
          message={`最终结论：${conclusion.summary}`}
          description={`根因位置：${conclusion.root_location?.type || "unknown"} / ${conclusion.root_location?.target_ref || "-"}；报告校验：${conclusion.verification?.status || "unknown"}`}
        />
      )}
    </Space>
  );
}

function HypothesisBoard({ hypotheses, compact = false }) {
  if (!hypotheses.length) return <Empty description="尚未生成候选假设" />;
  return (
    <Card size="small" title={`假设看板 (${hypotheses.length})`}>
      <Row gutter={[12, 12]}>
        {hypotheses.map((item) => {
          const status = HYPOTHESIS_STATUS[item.status] || HYPOTHESIS_STATUS.UNTESTED;
          const history = item.history || [];
          return (
            <Col xs={24} lg={12} xl={8} key={item.hypothesis_id}>
              <Card
                size="small"
                className={styles.hypothesisCard}
                title={<Typography.Text ellipsis title={item.type}>{item.type}</Typography.Text>}
                extra={<Tag color={status.color}>{status.label}</Tag>}
              >
                <Typography.Paragraph type="secondary" ellipsis={compact ? { rows: 2 } : false}>
                  {item.description}
                </Typography.Paragraph>
                <Space direction="vertical" size={4} style={{ width: "100%" }}>
                  <Typography.Text>证据评分 {item.evidence_score ?? 0}/100</Typography.Text>
                  <Progress
                    percent={item.evidence_score ?? 0}
                    status={item.status === "RULED_OUT" ? "exception" : "normal"}
                    strokeColor={item.status === "SUPPORTED" ? "#52c41a" : undefined}
                    showInfo={false}
                  />
                  <Typography.Text type="secondary">
                    支持证据 {(item.supporting_evidence_refs || []).length} ·
                    反驳证据 {(item.contradicting_evidence_refs || []).length}
                  </Typography.Text>
                </Space>
                {!compact && (
                  <Timeline
                    className={styles.hypothesisTimeline}
                    items={history.map((event, eventIndex) => ({
                      color: event.status === "SUPPORTED" ? "green" : event.status === "RULED_OUT" ? "red" : "blue",
                      children: (
                        <Space direction="vertical" size={0}>
                          <Typography.Text strong>{event.stage} · {event.evidence_score}/100</Typography.Text>
                          <Typography.Text type="secondary">{event.reason}</Typography.Text>
                        </Space>
                      ),
                      key: `${event.stage}-${eventIndex}`,
                    }))}
                  />
                )}
              </Card>
            </Col>
          );
        })}
      </Row>
    </Card>
  );
}

function EvidenceChain({ detail, compact = false }) {
  const evidence = detail.evidence || [];
  const hypotheses = detail.hypothesis_graph?.hypotheses || [];
  const edges = detail.hypothesis_graph?.edges || [];
  const [selected, setSelected] = useState(null);
  const hypothesisMap = useMemo(
    () => new Map(hypotheses.map((item) => [item.hypothesis_id, item])),
    [hypotheses],
  );
  const relationMap = useMemo(() => {
    const values = new Map();
    edges.forEach((edge) => {
      const items = values.get(edge.source) || [];
      items.push(edge);
      values.set(edge.source, items);
    });
    return values;
  }, [edges]);

  if (!evidence.length) return <Empty description="尚未产生结构化证据" />;

  return (
    <>
      <Card size="small" title={`证据链 (${evidence.length})`} extra={<Typography.Text type="secondary">点击一行查看原始观测和完整性信息</Typography.Text>}>
        <Table
          rowKey="evidence_id"
          size="small"
          pagination={compact ? false : { pageSize: 8 }}
          dataSource={compact ? evidence.slice(0, 6) : evidence}
          onRow={(record) => ({ onClick: () => setSelected(record), style: { cursor: "pointer" } })}
          columns={[
            { title: "证据", dataIndex: "evidence_id", width: 190, render: (value) => <Typography.Text title={value}>{compactRef(value)}</Typography.Text> },
            { title: "目标", dataIndex: "target", width: 150, render: (value) => value?.instance_id || value?.host_id || value?.agent_id || "-" },
            { title: "时间窗", dataIndex: "event_time_range", width: 210, render: (value) => `${formatTime(value?.start)} → ${formatTime(value?.end)}` },
            { title: "质量", width: 180, render: (_, record) => evidenceQuality(record) },
            {
              title: "证明关系",
              render: (_, record) => (
                <Space wrap>
                  {(relationMap.get(record.evidence_id) || []).map((edge) => (
                    <Tag key={`${edge.target}-${edge.relation}`} color={edge.relation === "SUPPORTS" ? "green" : "red"}>
                      {edge.relation === "SUPPORTS" ? "支持" : "反驳"} · {hypothesisMap.get(edge.target)?.type || edge.target}
                    </Tag>
                  ))}
                  {!(relationMap.get(record.evidence_id) || []).length && <Tag>背景事实</Tag>}
                </Space>
              ),
            },
          ]}
        />
      </Card>
      <Drawer
        width={720}
        open={Boolean(selected)}
        title="证据详情"
        onClose={() => setSelected(null)}
      >
        {selected && (
          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="Evidence ID"><Typography.Text copyable>{selected.evidence_id}</Typography.Text></Descriptions.Item>
            <Descriptions.Item label="来源">{selected.source_system} / {selected.source_type}</Descriptions.Item>
            <Descriptions.Item label="目标"><pre className={styles.jsonBlock}>{JSON.stringify(selected.target || {}, null, 2)}</pre></Descriptions.Item>
            <Descriptions.Item label="事件时间">{formatTime(selected.event_time_range?.start)} → {formatTime(selected.event_time_range?.end)}</Descriptions.Item>
            <Descriptions.Item label="探针">{selected.query_or_probe}</Descriptions.Item>
            <Descriptions.Item label="数据质量"><pre className={styles.jsonBlock}>{JSON.stringify(selected.data_quality || {}, null, 2)}</pre></Descriptions.Item>
            <Descriptions.Item label="观测值"><pre className={styles.jsonBlock}>{JSON.stringify(selected.observed_value || {}, null, 2)}</pre></Descriptions.Item>
            <Descriptions.Item label="完整性 Hash"><Typography.Text copyable>{selected.integrity_hash}</Typography.Text></Descriptions.Item>
          </Descriptions>
        )}
      </Drawer>
    </>
  );
}

function runMetrics(detail) {
  const probes = detail.probes || [];
  const evidence = detail.evidence || [];
  const nodes = detail.pipeline_nodes || [];
  const coverage = detail.coverage || [];
  const completedCoverage = coverage.filter((item) => item.status === "COMPLETED").length;
  const conclusion = detail.latest_conclusion;
  return {
    key: detail.diagnosis_id,
    diagnosis_id: detail.diagnosis_id,
    strategy: detail.normalized_intent?.analysis_strategy || "CONSTRAINED_HYBRID",
    status: detail.status,
    duration: formatDuration(detail.created_at, detail.updated_at),
    probes: probes.length,
    deep_probes: probes.filter((item) => item.risk_level === "R2").length,
    evidence: evidence.length,
    coverage: coverage.length ? Math.round((completedCoverage / coverage.length) * 100) : 0,
    steps: nodes.filter((item) => item.status !== "PENDING").length,
    failed_steps: nodes.filter((item) => item.status === "FAILED").length,
    confidence: conclusion?.confidence_level || "不可判断",
    root: conclusion
      ? `${conclusion.root_location?.type || "unknown"} / ${conclusion.domain_cause?.type || "unknown"}`
      : "尚无结论",
    model_calls: detail.budget_used?.model_calls ?? 0,
  };
}

function ComparisonView({ detail, sessions }) {
  const candidates = useMemo(() => {
    const byId = new Map([[detail.diagnosis_id, detail]]);
    sessions.forEach((item) => byId.set(item.diagnosis_id, item));
    return [...byId.values()];
  }, [detail, sessions]);
  const initialIds = useMemo(() => {
    const values = [detail.diagnosis_id];
    for (const item of candidates) {
      if (values.length >= 3) break;
      if (!values.includes(item.diagnosis_id)) values.push(item.diagnosis_id);
    }
    return values;
  }, [candidates, detail.diagnosis_id]);
  const [ids, setIds] = useState(initialIds);
  const [rows, setRows] = useState([runMetrics(detail)]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setIds(initialIds);
  }, [initialIds]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all(ids.map((id) => (
      id === detail.diagnosis_id ? Promise.resolve(detail) : getDiagnosisSession(id)
    )))
      .then((items) => {
        if (!cancelled) setRows(items.map(runMetrics));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [ids, detail]);

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Alert
        type="info"
        showIcon
        message="这里比较真实会话的路径成本、覆盖率和结论稳定性"
        description="若要评估“根因是否正确”，演示案例还需配置独立故障 Oracle；前端不会把高置信度冒充正确率。"
      />
      <Select
        mode="multiple"
        style={{ width: "100%" }}
        value={ids}
        onChange={(values) => setIds(values.slice(-3))}
        options={candidates.map((item) => ({
          value: item.diagnosis_id,
          label: `${STRATEGY_LABELS[item.normalized_intent?.analysis_strategy] || "受约束混合路径"} · ${item.target_scope?.target_service || "-"} · ${item.diagnosis_id}`,
        }))}
        placeholder="选择最多三个诊断会话"
      />
      <Table
        rowKey="diagnosis_id"
        loading={loading}
        pagination={false}
        scroll={{ x: 1250 }}
        dataSource={rows}
        columns={[
          { title: "分析路径", dataIndex: "strategy", width: 160, render: (value) => <Tag color="purple">{STRATEGY_LABELS[value] || value}</Tag> },
          { title: "状态", dataIndex: "status", width: 150 },
          { title: "耗时", dataIndex: "duration", width: 90 },
          { title: "步骤", dataIndex: "steps", width: 70 },
          { title: "探针", dataIndex: "probes", width: 70 },
          { title: "R2", dataIndex: "deep_probes", width: 60 },
          { title: "证据", dataIndex: "evidence", width: 70 },
          { title: "覆盖率", dataIndex: "coverage", width: 110, render: (value) => <Progress percent={value} size="small" /> },
          { title: "模型调用", dataIndex: "model_calls", width: 90 },
          { title: "失败步骤", dataIndex: "failed_steps", width: 90 },
          { title: "置信等级", dataIndex: "confidence", width: 90 },
          { title: "根因位置 / 领域", dataIndex: "root", width: 190 },
        ]}
      />
    </Space>
  );
}

export default function DiagnosisWorkbench({ detail, sessions = [] }) {
  const [mode, setMode] = useState("演示回放");
  const evidence = detail.evidence || [];
  const coverage = detail.coverage || [];
  const completedCoverage = coverage.filter((item) => item.status === "COMPLETED").length;
  const coveragePercent = coverage.length
    ? Math.round((completedCoverage / coverage.length) * 100)
    : 0;
  const strategy = detail.normalized_intent?.analysis_strategy || "CONSTRAINED_HYBRID";

  return (
    <Card
      className={styles.workbench}
      title="AI 诊断工作台"
      extra={(
        <Segmented
          value={mode}
          onChange={setMode}
          options={["演示回放", "假设与证据", "路径对比"]}
        />
      )}
    >
      <Row gutter={[12, 12]} className={styles.summary}>
        <Col xs={12} md={6}><Statistic title="分析策略" value={STRATEGY_LABELS[strategy] || strategy} valueStyle={{ fontSize: 17 }} /></Col>
        <Col xs={12} md={6}><Statistic title="证据数量" value={evidence.length} /></Col>
        <Col xs={12} md={6}><Statistic title="目标覆盖率" value={coveragePercent} suffix="%" /></Col>
        <Col xs={12} md={6}><Statistic title="结论版本" value={(detail.conclusion_versions || []).length} /></Col>
      </Row>

      {mode === "演示回放" && <ReplayView detail={detail} />}
      {mode === "假设与证据" && (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <HypothesisBoard hypotheses={detail.hypothesis_graph?.hypotheses || []} />
          <EvidenceChain detail={detail} />
        </Space>
      )}
      {mode === "路径对比" && <ComparisonView detail={detail} sessions={sessions} />}
    </Card>
  );
}
