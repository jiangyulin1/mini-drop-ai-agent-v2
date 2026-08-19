import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Badge, Button, Card, Col, Empty, Progress, Row, Skeleton, Space, Tabs, Tag, Tooltip, Typography } from "antd";
import { ApartmentOutlined, BulbOutlined, FileSearchOutlined, NodeIndexOutlined, ReloadOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import { getCaseHypotheses, getCaseInvestigationPlan, listCaseEvidenceReviews } from "../../api/client";
import EvidenceDrawer from "../../components/EvidenceDrawer";
import ExplainabilityDrawer from "../../components/ExplainabilityDrawer";
import { evidenceTrust, planStatus, riskLevel } from "../../utils/opsMappings";
import styles from "../AIDiagnosis.module.css";
import "./CanonicalCaseWorkspace.css";

function array(value) { return Array.isArray(value) ? value : value?.items || []; }
function count(value) { return array(value).length; }
function compact(value, fallback = "—") {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "string" || typeof value === "number") return String(value);
  return value.summary || value.title || value.label || value.status || value.statement || fallback;
}

function EmptyEvidence() {
  return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前 Case 还没有可用证据。你可以关联已有任务，或批准 Agent 创建采集任务。" />;
}

const HYPOTHESIS_STATE = {
  PENDING: "待验证",
  SUPPORTED: "有支持证据",
  CONFIRMED: "已确认",
  REJECTED: "已排除",
  REFUTED: "已推翻",
};

function EvidenceTab({ evidence, reviews, error, onRetry, onOpen, onExplain, showInternals }) {
  const latestReview = useMemo(() => {
    const map = new Map();
    reviews.forEach((review) => map.set(review.evidence_id, review));
    return map;
  }, [reviews]);
  if (error) return <LoadFailed what="证据审查记录" error={error} onRetry={onRetry} />;
  if (!evidence.length) return <EmptyEvidence />;
  return <div className="ccw-evidence-grid">{evidence.map((item) => {
    const id = item.evidence_id || item.id;
    const review = latestReview.get(id);
    const trust = evidenceTrust(review?.decision || item.trust_status || item.status);
    const source = item.source_node || item.agent_id || item.target_ref || "—";
    return <Card size="small" key={id} className={`ccw-evidence-card ${review?.decision === "EXCLUDED" ? "is-excluded" : ""}`} title={<Space><FileSearchOutlined /><Typography.Text ellipsis={{ tooltip: id }}>{item.artifact_type || item.collector_id || id}</Typography.Text></Space>} extra={<Tag color={trust.color}>{trust.label}</Tag>}>
      <div className="ccw-evidence-meta">
        <span><small>来源节点</small><strong>{source}</strong></span>
        {showInternals && <><span><small>采集器</small><strong>{item.collector_id || item.collector || item.artifact_type || "—"}</strong></span><span><small>任务</small><strong>{item.task_id || "—"}</strong></span><span><small>证据 ID</small><strong>{id}</strong></span></>}
      </div>
      <Typography.Paragraph ellipsis={{ rows: 2, expandable: false }}>{compact(item.summary || item.projections?.[0]?.content?.summary, "尚无结构化摘要；打开查看原始投影。")}</Typography.Paragraph>
      {review?.decision === "EXCLUDED" && <Alert type="warning" showIcon message="已从后续调查中排除" />}
      <Space wrap><Button size="small" onClick={() => onOpen(item)}>详情与审查</Button><Button size="small" type="link" onClick={() => onExplain(item)}>为什么被引用？</Button></Space>
    </Card>;
  })}</div>;
}

/** A failed request must never be shown as a business empty-state. */
function LoadFailed({ what, error, onRetry }) {
  return (
    <Alert
      type="error"
      showIcon
      message={`${what}加载失败`}
      description={error}
      action={onRetry ? <Button size="small" onClick={onRetry}>重试</Button> : null}
    />
  );
}

function PlanTab({ plan, error, onRetry, onExplain, onOpenEvidence, showInternals }) {
  const steps = array(plan?.steps);
  if (error) return <LoadFailed what="调查计划" error={error} onRetry={onRetry} />;
  if (!steps.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Agent 尚未生成调查计划。你可以在输入区请求生成计划。" />;
  return <div className="ccw-plan"><div className="ccw-plan-head"><div><strong>{plan.goal || "定位根因"}</strong>{showInternals && <small>第 {plan.plan_revision ?? "—"} 版 · {plan.source || "server"}</small>}</div><Tag>{steps.length} 个步骤</Tag></div>{steps.map((step, index) => { const status=planStatus(step.status); const risk=riskLevel(step.risk); return <div className="ccw-step" key={step.step_id || index}><span className="ccw-step-index">{String(index + 1).padStart(2,"0")}</span><div className="ccw-step-main"><div><strong>{step.purpose || step.expected_information || step.collector_id || `步骤 ${index + 1}`}</strong><Space size={4} wrap><Tag color={status.color}>{status.label}</Tag><Tooltip title={risk.description}><Tag color={risk.color}>{risk.label}</Tag></Tooltip>{step.user_locked && <Tag icon={<SafetyCertificateOutlined />}>用户锁定</Tag>}</Space></div><p>{step.expected_information || "等待该步骤补充预期信息说明"}</p>{showInternals && <div className="ccw-step-details"><span>采集器 <b>{step.collector_id || step.kind || "—"}</b></span><span>目标 <b>{array(step.target_refs).join("、") || "—"}</b></span><span>假设 <b>{array(step.hypothesis_refs).join("、") || "—"}</b></span><span>优先级 <b>{step.priority ?? "—"}</b></span></div>}<Space><Button size="small" type="link" onClick={() => onExplain(step)}>为什么执行</Button>{step.evidence_id && <Button size="small" type="link" onClick={() => onOpenEvidence(step.evidence_id)}>查看产出 Evidence</Button>}</Space></div></div>; })}</div>;
}

function HypothesisTab({ graph, workspace, error, onRetry, onExplain }) {
  const nodes = array(graph?.nodes || graph?.hypotheses || workspace?.hypotheses);
  if (error) return <LoadFailed what="假设" error={error} onRetry={onRetry} />;
  if (!nodes.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未形成可审查假设。Agent 会在获得初始 Evidence 后建立并验证假设。" />;
  return <div className="ccw-hypotheses">{nodes.map((node, index) => { const confidence=Number(node.confidence ?? node.score ?? 0); const state=String(node.status || node.state || "PENDING"); const stateLabel=HYPOTHESIS_STATE[state] || state; const color=state === "CONFIRMED" ? "green" : state === "REJECTED" || state === "REFUTED" ? "red" : state === "SUPPORTED" ? "purple" : "gold"; return <Card size="small" key={node.hypothesis_id || node.node_id || index} title={<Space><BulbOutlined style={{ color: "#7c3aed" }} /><span>{compact(node.description || node.statement || node.title, `假设 ${index + 1}`)}</span></Space>} extra={<Tag color={color}>{stateLabel}</Tag>}><div className="ccw-confidence"><Progress type="dashboard" percent={Math.round(confidence <= 1 ? confidence * 100 : confidence)} size={74} strokeColor="#7c3aed" /><div><p><strong>置信度上升，因为：</strong>{compact(node.confidence_up_reason || node.support_summary, "尚无足够支持证据")}</p><p><strong>置信度下降，因为：</strong>{compact(node.confidence_down_reason || node.counter_summary, "尚未记录明确反证")}</p><p><strong>还缺少：</strong>{compact(node.evidence_gap || node.missing_evidence || node.next_action, "等待 Agent 评估")}</p></div></div><div className="ccw-ref-row"><span>支持 Evidence <Badge count={count(node.supporting_evidence_refs)} showZero color="#7c3aed" /></span><span>反对 Evidence <Badge count={count(node.opposing_evidence_refs)} showZero color="#d92d20" /></span><Button size="small" type="link" onClick={() => onExplain(node)}>为什么这样判断</Button></div></Card>; })}</div>;
}

function CausalTab({ workspace, onExplain }) {
  const graph = workspace?.causal_graph || {};
  const edges = array(graph.edges);
  const conclusion = workspace?.conclusion;
  return <div className="ccw-causal"><Card title="结论 / Conclusion" extra={conclusion && <Button size="small" onClick={() => onExplain(conclusion)}>为什么</Button>}>{conclusion ? <><Tag color={conclusion.status === "CONFIRMED" ? "green" : "purple"}>{conclusion.status || "DRAFT"}</Tag><Typography.Title level={4}>{compact(conclusion.summary || conclusion.statement, "结论修订")}</Typography.Title><Typography.Paragraph>{compact(conclusion.description || conclusion.reasoning, "打开‘为什么’查看 Evidence、反证和缺口。")}</Typography.Paragraph><Space wrap>{array(conclusion.evidence_refs).map((ref) => <Tag color="blue" key={ref}>{ref}</Tag>)}</Space></> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前证据还不足以下结论。" />}</Card><Card title={<Space><ApartmentOutlined />因果链 Causal Chain</Space>} extra={<Tag>{count(graph.nodes)} 节点 · {edges.length} 边</Tag>}>{edges.length ? <div className="ccw-edges">{edges.map((edge,index)=><div key={edge.edge_id || index}><strong>{edge.from_node_id || edge.source}</strong><NodeIndexOutlined /><strong>{edge.to_node_id || edge.target}</strong><Tag color={edge.status === "SUPPORTED" ? "purple" : "default"}>{edge.status || edge.relation || "待验证"}</Tag></div>)}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未形成可验证因果链" />}</Card></div>;
}

/** Canonical Workspace Snapshot projection backed only by existing Case APIs. */
export default function CanonicalCaseWorkspace({ workspace, connected, caseId, onRefresh }) {
  const [plan, setPlan] = useState(null);
  const [hypotheses, setHypotheses] = useState(null);
  const [reviews, setReviews] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selectedEvidence, setSelectedEvidence] = useState(null);
  const [explainDecision, setExplainDecision] = useState(null);
  // Distinguish "the agent has produced nothing yet" from "we could not load
  // it".  Rendering a business empty-state for a failed request hides outages.
  const [loadErrors, setLoadErrors] = useState({});
  const [showInternals, setShowInternals] = useState(false);
  const load = useCallback(async () => {
    if (!caseId) return;
    setLoading(true);
    const [planResult, hypothesisResult, reviewsResult] = await Promise.allSettled([getCaseInvestigationPlan(caseId), getCaseHypotheses(caseId), listCaseEvidenceReviews(caseId)]);
    if (planResult.status === "fulfilled") setPlan(planResult.value);
    if (hypothesisResult.status === "fulfilled") setHypotheses(hypothesisResult.value);
    if (reviewsResult.status === "fulfilled") setReviews(reviewsResult.value?.items || []);
    setLoadErrors({
      plan: planResult.status === "rejected" ? (planResult.reason?.message || "加载失败") : "",
      hypotheses: hypothesisResult.status === "rejected" ? (hypothesisResult.reason?.message || "加载失败") : "",
      reviews: reviewsResult.status === "rejected" ? (reviewsResult.reason?.message || "加载失败") : "",
    });
    setLoading(false);
  }, [caseId]);
  useEffect(() => { void load(); }, [load, workspace?.last_event_seq]);
  const evidence = array(workspace?.evidence);
  const revisions = workspace?.revisions || {};
  if (!workspace) return null;
  const openById = (id) => setSelectedEvidence(evidence.find((item) => (item.evidence_id || item.id) === id) || null);
  const items = [
    { key: "plan", label: <Space><NodeIndexOutlined />调查计划 <Badge count={count(plan?.steps)} showZero /></Space>, children: <PlanTab plan={plan} error={loadErrors.plan} onRetry={load} onExplain={setExplainDecision} onOpenEvidence={openById} showInternals={showInternals} /> },
    { key: "hypotheses", label: <Space><BulbOutlined />假设 <Badge count={count(hypotheses?.nodes || hypotheses?.hypotheses)} showZero /></Space>, children: <HypothesisTab graph={hypotheses} workspace={workspace} error={loadErrors.hypotheses} onRetry={load} onExplain={setExplainDecision} /> },
    { key: "evidence", label: <Space><FileSearchOutlined />证据 <Badge count={evidence.length} showZero /></Space>, children: <EvidenceTab evidence={evidence} reviews={reviews} error={loadErrors.reviews} onRetry={load} onOpen={setSelectedEvidence} onExplain={setExplainDecision} showInternals={showInternals} /> },
    { key: "causal", label: <Space><ApartmentOutlined />因果链与结论</Space>, children: <CausalTab workspace={workspace} onExplain={setExplainDecision} /> },
  ];
  return <section className={`${styles.canonicalWorkspace} ccw-shell`} aria-label="Case Workspace" data-testid="canonical-workspace">
    <header className="ccw-header">
      <div><h2>调查事实、假设与证据</h2></div>
      <div className="ccw-status">
        <Tag color={connected ? "success" : "orange"}>{connected ? "实时同步" : "已降级为轮询，正在重连"}</Tag>
        <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={() => { void load(); onRefresh?.(); }}>刷新</Button>
        {/* Version counters and runtime state are diagnostics, not workflow. */}
        <Button size="small" type="text" onClick={() => setShowInternals((value) => !value)}>
          {showInternals ? "隐藏技术细节" : "技术细节"}
        </Button>
      </div>
    </header>
    {showInternals && <div className="ccw-revisions"><span>命令 r{revisions.case_command || 0}</span><span>控制 r{revisions.control || 0}</span><span>范围 r{revisions.scope || 0}</span><span>计划 r{revisions.plan || plan?.plan_revision || 0}</span><span>运行时 {workspace.engine?.state || "IDLE"}</span><span>事件 #{workspace.last_event_seq || 0}</span></div>}
    {loading && !plan ? <Skeleton active paragraph={{ rows: 6 }} /> : <Tabs items={items} defaultActiveKey="plan" />}
    <EvidenceDrawer open={Boolean(selectedEvidence)} onClose={() => setSelectedEvidence(null)} caseId={caseId} evidence={selectedEvidence} onChanged={() => { void load(); onRefresh?.(); }} onExplain={setExplainDecision} />
    <ExplainabilityDrawer open={Boolean(explainDecision)} onClose={() => setExplainDecision(null)} decision={explainDecision} />
  </section>;
}
