import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Descriptions,
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
import EvidenceReference, { EvidenceDetailDrawer } from "./EvidenceReference";
import styles from "./DiagnosisWorkbench.module.css";
import { formatBeijingDateTime } from "../utils/time";

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

const PIPELINE_STATUS_LABELS = {
  PENDING: "待执行",
  RUNNING: "执行中",
  WAITING: "等待中",
  COMPLETED: "已完成",
  SKIPPED: "已跳过",
  FAILED: "失败",
};

function formatTime(value) {
  return value ? formatBeijingDateTime(value) : "-";
}

function formatDuration(start, end) {
  const value = (new Date(end).getTime() - new Date(start).getTime()) / 1000;
  return Number.isFinite(value) && value >= 0 ? `${value.toFixed(1)}s` : "-";
}

function compactRef(value) {
  const text = String(value || "");
  return text.length > 34 ? `${text.slice(0, 16)}…${text.slice(-12)}` : text;
}

function compactLabel(value, size = 22) {
  const text = String(value || "-");
  return text.length > size ? `${text.slice(0, size - 1)}…` : text;
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
  const evidenceMap = useMemo(
    () => new Map(evidence.map((item) => [item.evidence_id, item])),
    [evidence],
  );
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
        extra={<Tag color={current.status === "FAILED" ? "red" : "blue"} title={current.status}>{PIPELINE_STATUS_LABELS[current.status] || current.status}</Tag>}
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
                ? current.input_refs.map((ref) => (
                  evidenceMap.has(ref)
                    ? <EvidenceReference key={ref} evidence={evidenceMap.get(ref)} label={compactRef(ref)} />
                    : <Tag key={ref} title={ref}>{compactRef(ref)}</Tag>
                ))
                : "无"}
            </Space>
          </Descriptions.Item>
          <Descriptions.Item label="输出引用" span={3}>
            <Space wrap>
              {(current.output_refs || []).length
                ? current.output_refs.map((ref) => (
                  evidenceMap.has(ref)
                    ? <EvidenceReference key={ref} evidence={evidenceMap.get(ref)} label={compactRef(ref)} />
                    : <Tag color="blue" key={ref} title={ref}>{compactRef(ref)}</Tag>
                ))
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
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <Alert
            type={conclusion.verification?.status === "passed" ? "success" : "warning"}
            showIcon
            message={`最终结论：${conclusion.summary}`}
            description={`根因位置：${conclusion.root_location?.type || "unknown"} / ${conclusion.root_location?.target_ref || "-"}；报告校验：${conclusion.verification?.status || "unknown"}`}
          />
          <Card size="small" title="优化、缓解与验证建议">
            <Table
              rowKey={(item) => item.recommendation_id || item.title}
              size="small"
              pagination={false}
              dataSource={conclusion.recommendations || []}
              columns={[
                { title: "类别", dataIndex: "category", width: 110, render: (value) => <Tag color="blue">{value || "建议"}</Tag> },
                { title: "建议", dataIndex: "title", width: 210 },
                { title: "具体内容", dataIndex: "detail" },
                { title: "风险", dataIndex: "risk_level", width: 80, render: (value) => <Tag color={value === "R3" ? "red" : value === "R2" ? "orange" : "green"}>{value}</Tag> },
              ]}
            />
          </Card>
        </Space>
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
            {
              title: "证据",
              dataIndex: "evidence_id",
              width: 210,
              render: (value, record) => (
                <Button
                  type="link"
                  size="small"
                  title={value}
                  style={{ height: "auto", padding: 0, textDecoration: "underline", textUnderlineOffset: 3 }}
                  onClick={(event) => {
                    event.stopPropagation();
                    setSelected(record);
                  }}
                >
                  {compactRef(value)}
                </Button>
              ),
            },
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
      <EvidenceDetailDrawer
        open={Boolean(selected)}
        evidence={selected}
        onClose={() => setSelected(null)}
        relations={(relationMap.get(selected?.evidence_id) || []).map((edge) => ({
          ...edge,
          target: hypothesisMap.get(edge.target)?.type || edge.target,
        }))}
      />
    </>
  );
}

function OracleEvaluation({ evaluation }) {
  if (!evaluation) return null;
  return (
    <Card
      size="small"
      title={`独立 Oracle 评测 · ${evaluation.case_id || "未命名案例"}`}
      extra={(
        <Tag color={evaluation.exact_match ? "green" : "orange"}>
          {evaluation.exact_match ? "全部命中" : `${evaluation.matched_count}/${evaluation.specified_count} 命中`}
        </Tag>
      )}
    >
      <Alert
        type={evaluation.exact_match ? "success" : "warning"}
        showIcon
        message={`客观得分 ${evaluation.score_pct}%`}
        description="标准答案与 AI 输入隔离，只在报告生成并校验后评分；该得分不是模型自报置信度。"
        style={{ marginBottom: 12 }}
      />
      <Table
        size="small"
        pagination={false}
        rowKey="dimension"
        dataSource={evaluation.checks || []}
        columns={[
          { title: "评测维度", dataIndex: "dimension" },
          { title: "标准答案", dataIndex: "expected" },
          { title: "分析结果", dataIndex: "actual", render: (value) => value || "-" },
          {
            title: "结果",
            dataIndex: "matched",
            width: 90,
            render: (value) => <Tag color={value ? "green" : "red"}>{value ? "命中" : "未命中"}</Tag>,
          },
        ]}
      />
    </Card>
  );
}

function EvidenceGraph({ detail }) {
  const evidence = (detail.evidence || []).slice(0, 8);
  const hypotheses = (detail.hypothesis_graph?.hypotheses || []).slice(0, 5);
  const graphEdges = detail.hypothesis_graph?.edges || [];
  const conclusion = detail.latest_conclusion;
  const [selectedEvidence, setSelectedEvidence] = useState(null);
  const targetMap = new Map();
  evidence.forEach((item) => {
    const targetId = item.target?.instance_id || item.target?.host_id || item.target?.agent_id;
    if (targetId) targetMap.set(targetId, { id: targetId, label: targetId });
  });
  if (!targetMap.size) {
    (detail.target_scope?.instances || []).slice(0, 5).forEach((item) => {
      const targetId = item.instance_id || item.host_id;
      if (targetId) targetMap.set(targetId, { id: targetId, label: targetId });
    });
  }
  const targets = [...targetMap.values()].slice(0, 5);
  if (!evidence.length && !hypotheses.length) {
    return <Empty description="尚无可绘制的因果证据关系" />;
  }

  const width = 1160;
  const rowCount = Math.max(targets.length, evidence.length, hypotheses.length, 1);
  const height = Math.max(420, rowCount * 72 + 92);
  const columns = { target: 24, evidence: 310, hypothesis: 650, conclusion: 980 };
  const nodeWidth = { target: 170, evidence: 220, hypothesis: 220, conclusion: 156 };
  const positions = (items, x) => items.map((item, index) => ({
    ...item,
    x,
    y: 58 + ((height - 116) * (index + 0.5)) / Math.max(items.length, 1),
  }));
  const targetNodes = positions(targets, columns.target);
  const evidenceNodes = positions(evidence.map((item) => ({
    ...item,
    id: item.evidence_id,
    label: item.source_type || item.query_or_probe || item.evidence_id,
  })), columns.evidence);
  const hypothesisNodes = positions(hypotheses.map((item) => ({
    ...item,
    id: item.hypothesis_id,
    label: item.type || item.description,
  })), columns.hypothesis);
  const targetById = new Map(targetNodes.map((item) => [item.id, item]));
  const evidenceById = new Map(evidenceNodes.map((item) => [item.id, item]));
  const hypothesisById = new Map(hypothesisNodes.map((item) => [item.id, item]));
  const conclusionNode = conclusion ? {
    id: "verified-conclusion",
    label: conclusion.cluster_assessment?.classification || "已校验结论",
    x: columns.conclusion,
    y: height / 2,
  } : null;
  const line = (source, target, color, key, dashed = false) => (
    <path
      key={key}
      d={`M ${source.x + (nodeWidth[source.kind] || 220)} ${source.y} C ${source.x + 245} ${source.y}, ${target.x - 45} ${target.y}, ${target.x} ${target.y}`}
      fill="none"
      stroke={color}
      strokeWidth="2"
      strokeDasharray={dashed ? "6 5" : undefined}
      opacity="0.72"
    />
  );

  return (
    <>
      <Card
        size="small"
        title="全局因果证据图"
        extra={<Typography.Text type="secondary">点击证据节点读取观测与下载文件</Typography.Text>}
      >
        <Space wrap style={{ marginBottom: 10 }}>
          <Tag color="blue">同一目标/采集域</Tag>
          <Tag color="green">支持</Tag>
          <Tag color="red">反驳</Tag>
          <Tag>待判定</Tag>
          {(detail.evidence || []).length > evidence.length && <Tag color="gold">仅显示前 {evidence.length} 条证据</Tag>}
        </Space>
        <div className={styles.graphViewport}>
          <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="诊断因果证据图">
          <text x={columns.target} y="28" className={styles.graphHeading}>诊断目标</text>
          <text x={columns.evidence} y="28" className={styles.graphHeading}>结构化证据</text>
          <text x={columns.hypothesis} y="28" className={styles.graphHeading}>候选假设</text>
          <text x={columns.conclusion} y="28" className={styles.graphHeading}>最终结论</text>

          {evidenceNodes.map((item) => {
            const targetId = item.target?.instance_id || item.target?.host_id || item.target?.agent_id;
            const target = targetById.get(targetId);
            return target ? line({ ...target, kind: "target" }, item, "#1677ff", `scope-${item.id}`) : null;
          })}
          {graphEdges.map((edge, index) => {
            const source = evidenceById.get(edge.source);
            const target = hypothesisById.get(edge.target);
            if (!source || !target) return null;
            return line(
              { ...source, kind: "evidence" },
              target,
              edge.relation === "SUPPORTS" ? "#389e0d" : "#cf1322",
              `relation-${index}-${edge.source}-${edge.target}`,
              edge.relation !== "SUPPORTS",
            );
          })}
          {conclusionNode && hypothesisNodes.map((item) => line(
            { ...item, kind: "hypothesis" },
            conclusionNode,
            item.status === "SUPPORTED" ? "#389e0d" : item.status === "RULED_OUT" ? "#cf1322" : "#8c8c8c",
            `conclusion-${item.id}`,
            item.status !== "SUPPORTED",
          ))}

          {targetNodes.map((item) => (
            <g key={item.id} transform={`translate(${item.x}, ${item.y - 23})`}>
              <title>{item.label}</title>
              <rect width={nodeWidth.target} height="46" rx="8" className={styles.targetNode} />
              <text x="12" y="28" className={styles.graphText}>{compactLabel(item.label, 20)}</text>
            </g>
          ))}
          {evidenceNodes.map((item) => (
            <g
              key={item.id}
              transform={`translate(${item.x}, ${item.y - 25})`}
              role="button"
              tabIndex="0"
              style={{ cursor: "pointer" }}
              onClick={() => setSelectedEvidence(item)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") setSelectedEvidence(item);
              }}
            >
              <title>{`${item.id}\n${item.label}`}</title>
              <rect width={nodeWidth.evidence} height="50" rx="8" className={styles.evidenceNode} />
              <text x="12" y="22" className={styles.graphText}>{compactLabel(item.label, 24)}</text>
              <text x="12" y="39" className={styles.graphSubtext}>{compactLabel(item.id, 28)}</text>
            </g>
          ))}
          {hypothesisNodes.map((item) => (
            <g key={item.id} transform={`translate(${item.x}, ${item.y - 25})`}>
              <title>{item.description || item.label}</title>
              <rect
                width={nodeWidth.hypothesis}
                height="50"
                rx="8"
                className={item.status === "SUPPORTED" ? styles.supportedNode : item.status === "RULED_OUT" ? styles.ruledOutNode : styles.hypothesisNode}
              />
              <text x="12" y="22" className={styles.graphText}>{compactLabel(item.label, 24)}</text>
              <text x="12" y="39" className={styles.graphSubtext}>{HYPOTHESIS_STATUS[item.status]?.label || item.status} · {item.evidence_score ?? 0}/100</text>
            </g>
          ))}
          {conclusionNode && (
            <g transform={`translate(${conclusionNode.x}, ${conclusionNode.y - 31})`}>
              <title>{conclusion.summary}</title>
              <rect width={nodeWidth.conclusion} height="62" rx="10" className={styles.conclusionNode} />
              <text x="12" y="25" className={styles.graphText}>已校验结论</text>
              <text x="12" y="44" className={styles.graphSubtext}>{compactLabel(conclusionNode.label, 17)}</text>
            </g>
          )}
          </svg>
        </div>
      </Card>
      <EvidenceDetailDrawer
        evidence={selectedEvidence}
        open={Boolean(selectedEvidence)}
        onClose={() => setSelectedEvidence(null)}
        relations={graphEdges
          .filter((edge) => edge.source === selectedEvidence?.evidence_id)
          .map((edge) => ({
            ...edge,
            target: hypothesisById.get(edge.target)?.label || edge.target,
          }))}
      />
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
    oracle_score: conclusion?.evaluation?.score_pct,
    exact_match: conclusion?.evaluation?.exact_match,
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
        description="配置独立故障 Oracle 后，表格会显示客观正确率；Oracle 不进入分析上下文，前端不会把高置信度冒充正确率。"
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
          {
            title: "Oracle 得分",
            dataIndex: "oracle_score",
            width: 120,
            render: (value, row) => (
              value === undefined
                ? <Tag>未配置</Tag>
                : <Tag color={row.exact_match ? "green" : "orange"}>{value}%</Tag>
            ),
          },
        ]}
      />
    </Space>
  );
}

export default function DiagnosisWorkbench({ detail, sessions = [] }) {
  const [mode, setMode] = useState("证据与假设");
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
      title={(
        <Space direction="vertical" size={0}>
          <Typography.Text strong>诊断依据</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
            优先展示支撑结论的证据与假设，内部执行过程按需查看
          </Typography.Text>
        </Space>
      )}
      extra={(
        <Segmented
          value={mode}
          onChange={setMode}
          options={["证据与假设", "策略对比"]}
        />
      )}
    >
      <Row gutter={[12, 12]} className={styles.summary}>
        <Col xs={24} md={8}><Statistic title="分析策略" value={STRATEGY_LABELS[strategy] || strategy} valueStyle={{ fontSize: 17 }} /></Col>
        <Col xs={12} md={8}><Statistic title="有效证据" value={evidence.length} /></Col>
        <Col xs={12} md={8}><Statistic title="目标覆盖率" value={coveragePercent} suffix="%" /></Col>
      </Row>

      {mode === "证据与假设" && (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <HypothesisBoard hypotheses={detail.hypothesis_graph?.hypotheses || []} />
          <EvidenceChain detail={detail} />
          <OracleEvaluation evaluation={detail.latest_conclusion?.evaluation} />
          <Collapse
            ghost
            items={[
              {
                key: "graph",
                label: "完整因果关系图",
                children: <EvidenceGraph detail={detail} />,
              },
              {
                key: "replay",
                label: "内部过程记录（调试）",
                children: <ReplayView detail={detail} />,
              },
            ]}
          />
        </Space>
      )}
      {mode === "策略对比" && <ComparisonView detail={detail} sessions={sessions} />}
    </Card>
  );
}
