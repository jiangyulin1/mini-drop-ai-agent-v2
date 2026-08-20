import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Badge, Button, Card, Empty, Skeleton, Space, Tabs, Tag, Tooltip, Typography, message } from "antd";
import {
  AimOutlined,
  CheckOutlined,
  CloseOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  ReloadOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
} from "@ant-design/icons";
import { decideCaseCollectionProposal, getCaseInvestigationPlan, listCaseEvidenceReviews } from "../../api/client";
import EvidenceDrawer from "../../components/EvidenceDrawer";
import ExplainabilityDrawer from "../../components/ExplainabilityDrawer";
import { evidenceTrust, planStatus, riskLevel } from "../../utils/opsMappings";
import styles from "../AIDiagnosis.module.css";
import "./CanonicalCaseWorkspace.css";

function array(value) { return Array.isArray(value) ? value : value?.items || []; }
function count(value) { return array(value).length; }
function compact(value, fallback = "-") {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "string" || typeof value === "number") return String(value);
  return value.summary || value.title || value.label || value.status || value.statement || fallback;
}

function LoadFailed({ what, error, onRetry }) {
  return <Alert type="error" showIcon message={`${what}加载失败`} description={error} action={onRetry ? <Button size="small" onClick={onRetry}>重试</Button> : null} />;
}

function InformationGoalsTab({ plan, error, onRetry, onExplain, onOpenEvidence, showInternals }) {
  const steps = array(plan?.steps);
  if (error) return <LoadFailed what="信息目标" error={error} onRetry={onRetry} />;
  if (!steps.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Agent 尚未提出信息目标。已有 Evidence 仍可独立预览和分析。" />;
  return <div className="ccw-plan">
    <div className="ccw-plan-head"><div><strong>{plan.goal || "在预算内获取足够证据"}</strong>{showInternals && <small>计划修订 {plan.plan_revision ?? "-"}</small>}</div><Tag>{steps.length} 个信息目标</Tag></div>
    {steps.map((step, index) => {
      const status = planStatus(step.status);
      const risk = riskLevel(step.risk);
      return <div className="ccw-step" key={step.step_id || index}>
        <span className="ccw-step-index">{String(index + 1).padStart(2, "0")}</span>
        <div className="ccw-step-main">
          <div><strong>{step.expected_information || step.purpose || `信息目标 ${index + 1}`}</strong><Space size={4} wrap><Tag color={status.color}>{status.label}</Tag><Tooltip title={risk.description}><Tag color={risk.color}>{risk.label}</Tag></Tooltip>{step.user_locked && <Tag icon={<SafetyCertificateOutlined />}>用户锁定</Tag>}</Space></div>
          <p>{step.purpose || "等待 Agent 说明为什么需要补充这项信息"}</p>
          {showInternals && <div className="ccw-step-details"><span>候选采集器 <b>{step.collector_id || step.kind || "-"}</b></span><span>目标 <b>{array(step.target_refs).join("、") || "-"}</b></span><span>优先级 <b>{step.priority ?? "-"}</b></span></div>}
          <Space><Button size="small" type="link" onClick={() => onExplain(step)}>查看依据</Button>{step.evidence_id && <Button size="small" type="link" onClick={() => onOpenEvidence(step.evidence_id)}>查看产出 Evidence</Button>}</Space>
        </div>
      </div>;
    })}
  </div>;
}

function CollectionActivityTab({ proposals, requests, showInternals, caseId, revisions, onChanged }) {
  const [deciding, setDeciding] = useState("");
  const requestByProposal = useMemo(() => new Map(requests.map((item) => [item.proposal_id, item])), [requests]);
  async function decide(proposal, decision) {
    setDeciding(proposal.proposal_id);
    try {
      await decideCaseCollectionProposal(caseId, proposal.proposal_id, {
        decision,
        reason: decision === "APPROVE" ? "Operator approved the bounded collection" : "Operator rejected the collection",
        expected_control_revision: revisions.control,
        expected_scope_revision: revisions.scope,
      });
      message.success(decision === "APPROVE" ? "采集提案已批准并进入调度" : "采集提案已拒绝");
      await onChanged?.();
    } catch (error) {
      message.error(`操作失败：${error.message}`);
    } finally {
      setDeciding("");
    }
  }
  if (!proposals.length && !requests.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无 Agent 采集提案。模型只能提案，Task 由服务端校验后创建。" />;
  return <div className="ccw-collection-list">{proposals.map((proposal) => {
    const request = requestByProposal.get(proposal.proposal_id);
    const errors = array(proposal.validation_result?.errors);
    const status = request?.status || proposal.status || "PROPOSED";
    const color = proposal.status === "REJECTED" ? "red" : request?.task_id ? "processing" : "gold";
    const awaitingApproval = proposal.status === "PROPOSED" && proposal.validation_result?.awaiting_execution_authority;
    return <div className="ccw-collection-row" key={proposal.proposal_id}>
      <div className="ccw-collection-icon"><DatabaseOutlined /></div>
      <div className="ccw-collection-main">
        <div><strong>{proposal.information_goal || "未声明信息目标"}</strong><Space wrap><Tag color={color}>{status}</Tag><Tag>{proposal.collector_id}</Tag><Tag color={proposal.expected_risk === "R2" ? "orange" : "default"}>{proposal.expected_risk}</Tag></Space></div>
        <p>{proposal.reason_summary || "未提供提案理由"}</p>
        {errors.length > 0 && <Alert type="warning" showIcon message={errors.join(" / ")} />}
        {awaitingApproval && <Space wrap>
          <Button size="small" type="primary" icon={<CheckOutlined />} loading={deciding === proposal.proposal_id} onClick={() => void decide(proposal, "APPROVE")}>批准采集</Button>
          <Button size="small" danger icon={<CloseOutlined />} disabled={Boolean(deciding)} onClick={() => void decide(proposal, "REJECT")}>拒绝</Button>
        </Space>}
        {showInternals && <div className="ccw-step-details"><span>Proposal <b>{proposal.proposal_id}</b></span><span>Request <b>{request?.collection_request_id || "-"}</b></span><span>Task <b>{request?.task_id || "-"}</b></span></div>}
      </div>
    </div>;
  })}</div>;
}

function EvidenceTab({ evidence, reviews, error, onRetry, onOpen, onExplain, showInternals }) {
  const latestReview = useMemo(() => {
    const map = new Map();
    reviews.forEach((review) => map.set(review.evidence_id, review));
    return map;
  }, [reviews]);
  if (error) return <LoadFailed what="证据审查记录" error={error} onRetry={onRetry} />;
  if (!evidence.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前 Case 还没有 canonical Evidence。可关联已有任务，或让 Agent 提出采集请求。" />;
  return <div className="ccw-evidence-grid">{evidence.map((item) => {
    const id = item.evidence_id || item.id;
    const review = latestReview.get(id);
    const trust = evidenceTrust(review?.decision || item.trust_status || item.status);
    const source = item.source_node || item.agent_id || item.target_ref || "-";
    return <Card size="small" key={id} className={`ccw-evidence-card ${review?.decision === "EXCLUDED" ? "is-excluded" : ""}`} title={<Space><FileSearchOutlined /><Typography.Text ellipsis={{ tooltip: id }}>{item.artifact_type || item.collector_id || id}</Typography.Text></Space>} extra={<Tag color={trust.color}>{trust.label}</Tag>}>
      <div className="ccw-evidence-meta"><span><small>来源节点</small><strong>{source}</strong></span>{showInternals && <><span><small>采集器</small><strong>{item.collector_id || item.collector || item.artifact_type || "-"}</strong></span><span><small>任务</small><strong>{item.task_id || "-"}</strong></span><span><small>证据 ID</small><strong>{id}</strong></span></> }</div>
      <Typography.Paragraph ellipsis={{ rows: 2, expandable: false }}>{compact(item.summary || item.projections?.[0]?.content?.summary, "尚无结构化摘要；打开查看确定性投影。")}</Typography.Paragraph>
      {review?.decision === "EXCLUDED" && <Alert type="warning" showIcon message="已从后续 Agent 上下文中排除" />}
      <Space wrap><Button size="small" onClick={() => onOpen(item)}>详情、分析与审查</Button><Button size="small" type="link" onClick={() => onExplain(item)}>查看引用状态</Button></Space>
    </Card>;
  })}</div>;
}

function AnalysisTab({ analyses, onOpenEvidence }) {
  if (!analyses.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无 AI Evidence 分析。分析不会创建 Task，也不会自动生成根因排名。" />;
  return <div className="ccw-analysis-list">{[...analyses].reverse().map((analysis) => {
    const stale = analysis.input_state && analysis.input_state !== "CURRENT";
    return <Card size="small" key={analysis.analysis_run_id} title={<Space><RobotOutlined /><span>{analysis.mode || "EVIDENCE"} 分析</span></Space>} extra={<Space><Tag color={analysis.status === "COMPLETED" ? "green" : "processing"}>{analysis.status}</Tag>{stale && <Tag color="warning">输入已变更</Tag>}</Space>}>
      {array(analysis.facts).map((fact, index) => <div className="ccw-fact" key={`${analysis.analysis_run_id}-fact-${index}`}><strong>{fact.claim}</strong><Space wrap>{array(fact.citations).map((citation) => <Button type="link" size="small" key={`${citation.evidence_id}-${citation.field_path}`} onClick={() => onOpenEvidence(citation.evidence_id)}>{citation.evidence_id} · {citation.field_path}</Button>)}</Space></div>)}
      {array(analysis.conflicts).length > 0 && <Alert type="warning" showIcon message="Evidence 存在冲突" description={array(analysis.conflicts).map((item) => compact(item)).join("；")} />}
      {array(analysis.limitations).length > 0 && <div className="ccw-limitations"><strong>限制与不足</strong>{array(analysis.limitations).map((item, index) => <p key={index}>{compact(item)}</p>)}</div>}
      {array(analysis.next_collection_proposals).length > 0 && <div className="ccw-next-goals"><strong>下一信息目标</strong>{array(analysis.next_collection_proposals).map((item, index) => <Tag key={index}>{compact(item.information_goal || item)}</Tag>)}</div>}
    </Card>;
  })}</div>;
}

/** Evidence-native workspace. Legacy RCA and causal graph data are intentionally not rendered. */
export default function CanonicalCaseWorkspace({ workspace, connected, caseId, onRefresh }) {
  const [plan, setPlan] = useState(null);
  const [reviews, setReviews] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selectedEvidence, setSelectedEvidence] = useState(null);
  const [explainDecision, setExplainDecision] = useState(null);
  const [loadErrors, setLoadErrors] = useState({});
  const [showInternals, setShowInternals] = useState(false);
  const load = useCallback(async () => {
    if (!caseId) return;
    setLoading(true);
    const [planResult, reviewsResult] = await Promise.allSettled([getCaseInvestigationPlan(caseId), listCaseEvidenceReviews(caseId)]);
    if (planResult.status === "fulfilled") setPlan(planResult.value);
    if (reviewsResult.status === "fulfilled") setReviews(reviewsResult.value?.items || []);
    setLoadErrors({
      plan: planResult.status === "rejected" ? (planResult.reason?.message || "加载失败") : "",
      reviews: reviewsResult.status === "rejected" ? (reviewsResult.reason?.message || "加载失败") : "",
    });
    setLoading(false);
  }, [caseId]);
  useEffect(() => { void load(); }, [load, workspace?.last_event_seq]);
  if (!workspace) return null;
  const evidence = array(workspace.evidence);
  const proposals = array(workspace.collection_proposals);
  const requests = array(workspace.collection_requests);
  const analyses = array(workspace.evidence_analyses);
  const revisions = workspace.revisions || {};
  const openById = (id) => setSelectedEvidence(evidence.find((item) => (item.evidence_id || item.id) === id) || null);
  const items = [
    { key: "goals", label: <Space><AimOutlined />信息目标 <Badge count={count(plan?.steps)} showZero /></Space>, children: <InformationGoalsTab plan={plan} error={loadErrors.plan} onRetry={load} onExplain={setExplainDecision} onOpenEvidence={openById} showInternals={showInternals} /> },
    { key: "collections", label: <Space><DatabaseOutlined />采集活动 <Badge count={proposals.length} showZero /></Space>, children: <CollectionActivityTab proposals={proposals} requests={requests} showInternals={showInternals} caseId={caseId} revisions={revisions} onChanged={onRefresh} /> },
    { key: "evidence", label: <Space><FileSearchOutlined />Evidence <Badge count={evidence.length} showZero /></Space>, children: <EvidenceTab evidence={evidence} reviews={reviews} error={loadErrors.reviews} onRetry={load} onOpen={setSelectedEvidence} onExplain={setExplainDecision} showInternals={showInternals} /> },
    { key: "analyses", label: <Space><RobotOutlined />受引用分析 <Badge count={analyses.length} showZero /></Space>, children: <AnalysisTab analyses={analyses} onOpenEvidence={openById} /> },
  ];
  return <section className={`${styles.canonicalWorkspace} ccw-shell`} aria-label="Case Workspace" data-testid="canonical-workspace">
    <header className="ccw-header"><div><h2>AI 调查与 Evidence 工作区</h2></div><div className="ccw-status"><Tag color={connected ? "success" : "orange"}>{connected ? "实时同步" : "轮询同步"}</Tag><Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={() => { void load(); onRefresh?.(); }}>刷新</Button><Button size="small" type="text" onClick={() => setShowInternals((value) => !value)}>{showInternals ? "隐藏技术细节" : "技术细节"}</Button></div></header>
    {showInternals && <div className="ccw-revisions"><span>命令 r{revisions.case_command || 0}</span><span>控制 r{revisions.control || 0}</span><span>范围 r{revisions.scope || 0}</span><span>运行时 {workspace.engine?.state || "IDLE"}</span><span>事件 #{workspace.last_event_seq || 0}</span></div>}
    {loading && !plan ? <Skeleton active paragraph={{ rows: 6 }} /> : <Tabs items={items} defaultActiveKey="goals" />}
    <EvidenceDrawer open={Boolean(selectedEvidence)} onClose={() => setSelectedEvidence(null)} caseId={caseId} evidence={selectedEvidence} onChanged={() => { void load(); onRefresh?.(); }} onExplain={setExplainDecision} />
    <ExplainabilityDrawer open={Boolean(explainDecision)} onClose={() => setExplainDecision(null)} decision={explainDecision} />
  </section>;
}
