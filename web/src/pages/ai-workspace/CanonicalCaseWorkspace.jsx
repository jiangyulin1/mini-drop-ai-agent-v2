import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Badge, Button, Card, Empty, Skeleton, Space, Tag, Tooltip, Typography, message } from "antd";
import {
  AimOutlined,
  ArrowRightOutlined,
  BulbOutlined,
  CheckOutlined,
  CloseOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  NodeIndexOutlined,
  ReloadOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  ToolOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { decideCaseCollectionProposal, getCaseInvestigationPlan, listCaseEvidenceReviews } from "../../api/client";
import EvidenceDrawer from "../../components/EvidenceDrawer";
import ExplainabilityDrawer from "../../components/ExplainabilityDrawer";
import { evidenceTrust, planStatus, riskCode, riskLevel } from "../../utils/opsMappings";
import "./CanonicalCaseWorkspace.css";
import { formatBeijingDateTime } from "../../utils/time";

function array(value) { return Array.isArray(value) ? value : value?.items || []; }
function count(value) { return array(value).length; }
function compact(value, fallback = "-") {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "string" || typeof value === "number") return String(value);
  return value.summary || value.title || value.label || value.status || value.statement
    || value.mechanism || value.reason || value.concrete_action || value.required_fact || fallback;
}

const ANALYSIS_STATUS = {
  QUEUED: { label: "排队中", color: "blue" },
  RUNNING: { label: "分析中", color: "processing" },
  COMPLETED: { label: "已完成", color: "green" },
  FAILED: { label: "分析失败", color: "red" },
};

const INPUT_STATE = {
  CURRENT: { label: "当前输入", color: "green" },
  STALE_INPUT: { label: "输入已变更", color: "gold" },
  EXCLUDED_INPUT: { label: "输入已排除", color: "orange" },
};

const STAGE_STATE_LABEL = {
  empty: "暂无数据",
  attention: "需处理",
  active: "进行中",
  partial: "覆盖有限",
  ready: "已就绪",
  available: "可查看",
};

const COLLECTION_FAILURE_STATUSES = new Set(["FAILED", "DISPATCH_FAILED", "CANCELLED", "REJECTED"]);

const DEPENDENCY_NODE_TYPE_LABELS = {
  process: "进程",
  service: "服务",
  instance: "实例",
  host: "主机",
  agent: "Agent",
  ip_endpoint: "网络端点",
  virtual_endpoint: "虚拟端点",
  external_unmanaged_endpoint: "外部端点",
  managed_host_endpoint: "已托管端点",
};

const DEPENDENCY_RELATION_LABELS = {
  calls: "调用",
  connects_to: "连接",
  publishes_to: "发布到",
  consumes_from: "消费自",
};

const OBSERVATION_POINT_LABELS = {
  client: "客户端",
  server: "服务端",
  host: "主机",
  snapshot: "快照",
};

const DEPENDENCY_LIMITATION_LABELS = {
  dependency_edges_are_observations_not_causal_claims: "依赖边来自通信观测，不能直接作为因果或根因结论。",
  tcp_communication_does_not_prove_root_cause: "TCP 通信只能证明发生过连接，不能单独证明根因。",
  point_in_time_snapshot_misses_completed_short_connections: "当前为时间点快照，可能遗漏已经结束的短连接。",
  nat_load_balancer_and_proxy_backends_are_not_uniquely_resolved: "NAT、负载均衡或代理后的真实后端目前不能唯一确认。",
  application_protocol_and_request_outcomes_are_not_observed: "当前没有观测应用层协议和单次请求结果。",
  macos_lsof_does_not_expose_linux_netns_cgroup_or_socket_inode: "macOS 的 lsof 快照不提供 Linux 网络命名空间、cgroup 和 socket inode 信息。",
  membership_snapshot_missing_remote_agent_mapping_is_limited: "缺少成员快照时，远端 Agent 的身份映射能力有限。",
  external_unmanaged_endpoints_not_collectable: "外部未托管端点无法继续自动采集。",
  virtual_endpoints_require_orchestrator_or_trace_resolution: "虚拟端点仍需编排器或调用链信息来解析后端。",
  registered_hosts_without_listener_identity: "部分已注册主机尚未解析到明确的监听进程。",
  agent_reported_dropped_discovery_events: "Agent 报告存在丢失的网络发现事件。",
  agent_snapshot_coverage_partial: "Agent 当前只能提供部分网络快照。",
  agent_time_windows_not_aligned: "不同 Agent 的观测时间窗尚未对齐。",
  clock_quality_unknown: "Agent 时钟质量未知，跨节点时间关系需谨慎解释。",
  platform_lsof_fallback: "当前使用 macOS lsof 兼容采集，覆盖能力低于 Linux 原生快照。",
  network_namespace_unavailable: "当前平台无法获取网络命名空间信息。",
  socket_inode_unavailable: "当前平台无法获取 socket inode 信息。",
  cgroup_identity_unavailable: "当前平台无法获取 cgroup 身份信息。",
  process_start_time_unavailable: "部分进程缺少可验证的启动时间。",
  no_network_discovery_artifact_available: "当前没有可用的网络发现产物。",
};

function isCollectionFailure(status) {
  return COLLECTION_FAILURE_STATUSES.has(String(status || "").toUpperCase());
}

function latestReviewMap(reviews) {
  const latest = new Map();
  array(reviews).forEach((review) => {
    const evidenceId = review?.evidence_id;
    if (!evidenceId) return;
    const current = latest.get(evidenceId);
    const revision = Number(review.review_revision);
    const currentRevision = Number(current?.review_revision);
    if (!current || (Number.isFinite(revision) && (!Number.isFinite(currentRevision) || revision > currentRevision))) {
      latest.set(evidenceId, review);
    }
  });
  return latest;
}

function shortIdentifier(value) {
  const text = String(value || "-");
  return text.length > 24 ? `${text.slice(0, 11)}…${text.slice(-7)}` : text;
}

function formatDateTime(value) {
  return value ? formatBeijingDateTime(value) : "-";
}

function dependencyNodeLabel(node) {
  const displayName = String(node?.display_name || "").trim();
  if (displayName) return displayName.split("/").filter(Boolean).pop() || displayName;
  const executable = String(node?.process?.executable || "").trim();
  if (executable) return executable.split("/").filter(Boolean).pop() || executable;
  return node?.endpoint?.address && node?.endpoint?.port != null
    ? `${node.endpoint.address}:${node.endpoint.port}`
    : shortIdentifier(node?.entity_id);
}

function humanizeDependencyLimitation(value) {
  const key = String(value || "").trim();
  if (!key) return "未知限制";
  return DEPENDENCY_LIMITATION_LABELS[key]
    || key.replaceAll("_", " ").replace(/^./, (character) => character.toUpperCase());
}

function formatPercent(value) {
  if (value === null || value === undefined || value === "") return "未提供";
  const number = Number(value);
  if (!Number.isFinite(number)) return "未提供";
  return `${Math.round(Math.max(0, Math.min(1, number)) * 100)}%`;
}

function stageState({ count: total, attention, active, ready, available }) {
  if (!total) return "empty";
  if (attention) return "attention";
  if (active) return "active";
  if (ready) return "ready";
  return available ? "available" : "active";
}

function LoadFailed({ what, error, onRetry }) {
  return <Alert type="error" showIcon message={`${what}加载失败`} description={error} action={onRetry ? <Button size="small" onClick={onRetry}>重试</Button> : null} />;
}

function InformationGoalsTab({ goals, plan, error, onRetry, onExplain, onOpenEvidence, onNavigate, showInternals }) {
  const steps = goals.length ? goals : array(plan?.steps);
  if (error) return <LoadFailed what="信息目标" error={error} onRetry={onRetry} />;
  if (!steps.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="Agent 尚未提出信息目标。已有 Evidence 仍可直接查看。"><Button type="primary" ghost onClick={() => onNavigate("evidence")}>查看已有 Evidence</Button></Empty>;
  return <div className="ccw-plan">
    <div className="ccw-plan-head"><div><strong>{plan?.goal || "在预算内获取足够证据"}</strong>{showInternals && <small>计划修订 {plan?.plan_revision ?? "-"}</small>}</div><Tag>{steps.length} 个信息目标</Tag></div>
    {steps.map((step, index) => {
      const status = planStatus(step.status);
      const risk = String(step.risk || "").startsWith("R") ? riskCode(step.risk) : riskLevel(step.risk);
      return <div className="ccw-step" key={step.goal_id || step.step_id || index}>
        <span className="ccw-step-index">{String(index + 1).padStart(2, "0")}</span>
        <div className="ccw-step-main">
          <div><strong>{step.title || step.expected_information || step.purpose || `信息目标 ${index + 1}`}</strong><Space size={4} wrap><Tag color={status.color}>{status.label}</Tag>{step.risk && <Tooltip title={risk.description}><Tag color={risk.color}>{risk.label}</Tag></Tooltip>}<Tag>{step.source === "gap" ? "证据缺口" : step.source === "analysis" ? "分析建议" : step.source === "proposal" ? "采集提案" : "调查计划"}</Tag>{step.user_locked && <Tag icon={<SafetyCertificateOutlined />}>用户锁定</Tag>}</Space></div>
          <p>{step.reason || step.purpose || "等待 Agent 说明为什么需要补充这项信息"}</p>
          <div className="ccw-step-details"><span>采集器 <b>{step.collector_id || step.kind || "待选择"}</b></span><span>Request <b>{step.collection_request_id || "-"}</b></span><span>Evidence <b>{array(step.evidence_ids).length}</b></span>{showInternals && <span>Task <b>{step.task_id || "-"}</b></span>}</div>
          <Space wrap><Button size="small" type="link" onClick={() => onExplain(step)}>查看依据</Button>{step.proposal_id && <Button size="small" onClick={() => onNavigate("collections")}>{step.status === "WAITING_APPROVAL" ? "处理审批" : "查看采集"}</Button>}{array(step.evidence_ids).map((id) => <Button size="small" key={id} onClick={() => onOpenEvidence(id)}>查看 Evidence</Button>)}</Space>
        </div>
      </div>;
    })}
  </div>;
}

function CollectionActivityTab({ proposals, requests, evidence, showInternals, caseId, revisions, onChanged, onOpenEvidence }) {
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
    const evidenceIds = evidence.filter((item) => item.task_id && item.task_id === request?.task_id).map((item) => item.evidence_id);
    const color = isCollectionFailure(status) ? "red" : evidenceIds.length ? "green" : request?.task_id ? "processing" : "gold";
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
        {evidenceIds.length > 0 && <Space wrap>{evidenceIds.map((id) => <Button size="small" key={id} onClick={() => onOpenEvidence(id)}>查看产出 Evidence</Button>)}</Space>}
        {showInternals && <div className="ccw-step-details"><span>Proposal <b>{proposal.proposal_id}</b></span><span>Request <b>{request?.collection_request_id || "-"}</b></span><span>Task <b>{request?.task_id || "-"}</b></span></div>}
      </div>
    </div>;
  })}{requests.filter((request) => !proposals.some((proposal) => proposal.proposal_id === request.proposal_id)).map((request) => <div className="ccw-collection-row" key={request.collection_request_id}><div className="ccw-collection-icon"><DatabaseOutlined /></div><div className="ccw-collection-main"><div><strong>{request.collector_id}</strong><Space><Tag>CollectionRequest</Tag><Tag color={isCollectionFailure(request.status) ? "red" : "processing"}>{request.status}</Tag></Space></div><p>服务端已接受的权威采集请求</p><div className="ccw-step-details"><span>Request <b>{request.collection_request_id}</b></span><span>Task <b>{request.task_id || "-"}</b></span></div></div></div>)}</div>;
}

function EvidenceTab({ evidence, reviews, goals, hypothesisGraph, error, onRetry, onOpen, onExplain, showInternals }) {
  const latestReview = useMemo(() => latestReviewMap(reviews), [reviews]);
  if (error) return <LoadFailed what="证据审查记录" error={error} onRetry={onRetry} />;
  if (!evidence.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前 Case 还没有 canonical Evidence。可关联已有任务，或让 Agent 提出采集请求。" />;
  return <div className="ccw-evidence-grid">{evidence.map((item) => {
    const id = item.evidence_id || item.id;
    const review = latestReview.get(id);
    const trust = evidenceTrust(review?.decision || item.trust_status || item.status);
    const source = item.source_node || item.agent_id || item.target_ref || "-";
    const linkedGoals = goals.filter((goal) => array(goal.evidence_ids).includes(id));
    const linkedHypotheses = array(hypothesisGraph?.hypotheses).filter((hypothesis) => [
      ...array(hypothesis.supporting_evidence_refs), ...array(hypothesis.contradicting_evidence_refs),
    ].includes(id));
    return <Card size="small" key={id} className={`ccw-evidence-card ${review?.decision === "EXCLUDED" ? "is-excluded" : ""}`} title={<Space><FileSearchOutlined /><Typography.Text ellipsis={{ tooltip: id }}>{item.artifact_type || item.collector_id || id}</Typography.Text></Space>} extra={<Tag color={trust.color}>{trust.label}</Tag>}>
      <div className="ccw-evidence-meta"><span><small>来源节点</small><strong>{source}</strong></span>{showInternals && <><span><small>采集器</small><strong>{item.collector_id || item.collector || item.artifact_type || "-"}</strong></span><span><small>任务</small><strong>{item.task_id || "-"}</strong></span><span><small>证据 ID</small><strong>{id}</strong></span></> }</div>
      <Typography.Paragraph ellipsis={{ rows: 2, expandable: false }}>{compact(item.summary || item.projections?.[0]?.content?.summary, "尚无结构化摘要；打开查看确定性投影。")}</Typography.Paragraph>
      <div className="ccw-step-details"><span>解决目标 <b>{linkedGoals.length}</b></span><span>关联假设 <b>{linkedHypotheses.length}</b></span><span>分析运行 <b>{item.analysis_count ?? 0}</b></span></div>
      {review?.decision === "EXCLUDED" && <Alert type="warning" showIcon message="已从后续 Agent 上下文中排除" />}
      <Space wrap><Button size="small" onClick={() => onOpen(item)}>详情、分析与审查</Button><Button size="small" type="link" onClick={() => onExplain(item)}>查看引用状态</Button></Space>
    </Card>;
  })}</div>;
}

function AnalysisCard({ analysis, evidenceIds, onOpenEvidence, onOpenCitation, onNavigate }) {
  const statusKey = String(analysis.status || "QUEUED").toUpperCase();
  const inputStateKey = String(analysis.input_state || "CURRENT").toUpperCase();
  const status = ANALYSIS_STATUS[statusKey] || { label: statusKey, color: "default" };
  const inputState = INPUT_STATE[inputStateKey] || { label: inputStateKey, color: "default" };
  const inputs = array(analysis.evidence_inputs);
  const facts = array(analysis.facts);
  const annotations = [
    ["异常", analysis.anomalies],
    ["解释", analysis.interpretations],
  ];
  return <Card
    size="small"
    className="ccw-analysis-card"
    title={<Space><RobotOutlined /><span>{analysis.mode || "EVIDENCE"} 分析</span></Space>}
    extra={<Space size={4} wrap><Tag color={status.color}>{status.label}</Tag><Tag color={inputState.color}>{inputState.label}</Tag></Space>}
  >
    <div className="ccw-analysis-meta">
      <span><small>输入 Evidence</small><strong>{inputs.length}</strong></span>
      <span><small>事实</small><strong>{facts.length}</strong></span>
      <span><small>完成时间</small><strong>{formatDateTime(analysis.completed_at || analysis.created_at)}</strong></span>
      <span><small>耗时</small><strong>{analysis.latency_ms == null ? "-" : `${analysis.latency_ms} ms`}</strong></span>
    </div>

    {inputs.length > 0 && <div className="ccw-analysis-inputs" aria-label="本次分析输入">
      {inputs.map((input, index) => {
        const evidenceId = typeof input === "string" ? input : input.evidence_id;
        const available = evidenceIds.has(String(evidenceId || ""));
        return <button
          type="button"
          key={`${evidenceId || "input"}-${index}`}
          disabled={!available}
          title={available ? String(evidenceId) : `Evidence ${evidenceId || "-"} 当前不可用`}
          onClick={() => onOpenEvidence(evidenceId)}
        >
          <DatabaseOutlined />
          <span>{shortIdentifier(evidenceId)}</span>
          <small>{typeof input === "object" ? input.review_state || "-" : "-"}</small>
        </button>;
      })}
    </div>}

    {facts.map((fact, factIndex) => {
      const certainty = String(fact.certainty || "UNKNOWN").toUpperCase();
      const certaintyColor = certainty === "HIGH" ? "green" : certainty === "MEDIUM" ? "blue" : certainty === "LOW" ? "gold" : "default";
      const citations = array(fact.citations);
      return <div className="ccw-fact" key={`${analysis.analysis_run_id}-fact-${factIndex}`}>
        <div className="ccw-fact-claim"><strong>{fact.claim || "未命名事实"}</strong><Tag color={certaintyColor}>{certainty === "UNKNOWN" ? "确定性未标注" : `${certainty} 确定性`}</Tag></div>
        {citations.length > 0 ? <div className="ccw-citations">
          {citations.map((citation, citationIndex) => {
            const evidenceId = String(citation.evidence_id || "");
            const available = evidenceIds.has(evidenceId);
            const fieldPath = citation.field_path || "未标注字段";
            return <div className="ccw-citation-wrap" key={`${evidenceId}-${citation.projection_hash || "projection"}-${fieldPath}-${citationIndex}`}>
              <button
                type="button"
                className="ccw-citation"
                disabled={!available}
                aria-label={available ? `打开引用 ${fieldPath}（Evidence ${evidenceId}）` : `引用不可用（Evidence ${evidenceId || "未知"}）`}
                title={`${evidenceId || "Evidence 未知"}\n${fieldPath}\n${citation.projection_hash || "未标注 projection hash"}`}
                onClick={() => onOpenCitation(citation)}
              >
                <FileSearchOutlined />
                <span>{fieldPath}</span>
                <small>{available ? shortIdentifier(evidenceId) : "Evidence 已失效"}</small>
              </button>
              {citation.quote != null && <span className="ccw-citation-quote">“{String(citation.quote)}”</span>}
            </div>;
          })}
        </div> : <Alert type="warning" showIcon message="该事实没有可验证引用" />}
      </div>;
    })}

    {!facts.length && ["QUEUED", "RUNNING"].includes(statusKey) && <Alert type="info" showIcon message="分析结果尚未生成" />}
    {!facts.length && statusKey === "FAILED" && <Alert type="error" showIcon message="本次分析未完成" description="可保留该记录用于排查，并基于当前 Evidence 重新发起分析。" />}
    {array(analysis.conflicts).length > 0 && <Alert type="warning" showIcon message="Evidence 存在冲突" description={array(analysis.conflicts).map((item) => compact(item)).join("；")} />}
    {annotations.map(([label, values]) => array(values).length > 0 && <div className="ccw-analysis-notes" key={label}><strong>{label}</strong><ul>{array(values).map((item, index) => <li key={`${label}-${index}`}>{compact(item)}</li>)}</ul></div>)}
    {array(analysis.limitations).length > 0 && <div className="ccw-limitations"><strong>限制与不足</strong>{array(analysis.limitations).map((item, index) => <p key={index}>{compact(item)}</p>)}</div>}
    {array(analysis.next_collection_proposals).length > 0 && <div className="ccw-next-goals"><strong>下一信息目标</strong>{array(analysis.next_collection_proposals).map((item, index) => <Button size="small" key={index} onClick={() => onNavigate("goals")}>{compact(item.information_goal || item)}</Button>)}</div>}
  </Card>;
}

function AnalysisTab({ analyses, evidence, onOpenEvidence, onOpenCitation, onNavigate }) {
  if (!analyses.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无 AI Evidence 分析。分析不会创建 Task，也不会自动生成根因排名。" />;
  const evidenceIds = new Set(evidence.map((item) => String(item.evidence_id || item.id || "")));
  const newestFirst = [...analyses].reverse();
  const groups = [
    ["current", "当前有效", newestFirst.filter((item) => item.status === "COMPLETED" && (!item.input_state || item.input_state === "CURRENT"))],
    ["processing", "处理中", newestFirst.filter((item) => ["QUEUED", "RUNNING"].includes(item.status))],
    ["history", "历史与失效", newestFirst.filter((item) => !(["QUEUED", "RUNNING"].includes(item.status)) && !(item.status === "COMPLETED" && (!item.input_state || item.input_state === "CURRENT")))],
  ];
  return <div className="ccw-analysis-groups">{groups.filter(([, , items]) => items.length).map(([key, label, items]) => <section className="ccw-analysis-group" key={key}>
    <header><strong>{label}</strong><Badge count={items.length} showZero /></header>
    <div className="ccw-analysis-list">{items.map((analysis) => <AnalysisCard key={analysis.analysis_run_id} analysis={analysis} evidenceIds={evidenceIds} onOpenEvidence={onOpenEvidence} onOpenCitation={onOpenCitation} onNavigate={onNavigate} />)}</div>
  </section>)}</div>;
}

function HypothesesTab({ graph, conclusion, onOpenEvidence, onExplain }) {
  const hypotheses = array(graph?.hypotheses);
  if (!hypotheses.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未形成可审查假设。" />;
  return <div className="ccw-hypotheses">{hypotheses.map((item, index) => {
    const status = String(item.status || "PROPOSED");
    const statusColor = status === "CONFIRMED" ? "green" : status === "RULED_OUT" ? "red" : status === "WEAKENED" ? "orange" : "processing";
    const support = array(item.supporting_evidence_refs);
    const ruledOut = array(conclusion?.ruled_out).find((entry) => [entry.hypothesis_id, entry.hypothesis_ref, entry.id].includes(item.hypothesis_id));
    const contradict = array(item.contradicting_evidence_refs).length
      ? array(item.contradicting_evidence_refs)
      : array(ruledOut?.evidence_refs || ruledOut?.supporting_evidence_refs);
    return <Card size="small" key={item.hypothesis_id || index} title={<Space><BulbOutlined /><span>{item.statement || `假设 ${index + 1}`}</span></Space>} extra={<Tag color={statusColor}>{status}</Tag>}>
      <div className="ccw-kv"><span>对象<strong>{item.root_entity || item.target || "-"}</strong></span><span>机制<strong>{item.mechanism || item.description || item.statement || "-"}</strong></span><span>修订<strong>r{item.revision || 1}</strong></span></div>
      {array(item.missing_evidence).length > 0 && <div className="ccw-limitations"><strong>仍缺事实</strong>{array(item.missing_evidence).map((fact) => <p key={fact}>{fact}</p>)}</div>}
      <div className="ccw-ref-row"><span>支持 <Badge count={support.length} showZero color="#067647" /></span><span>反证 <Badge count={contradict.length} showZero color="#d92d20" /></span><Space>{[...support, ...contradict].slice(0, 2).map((id) => <Button type="link" size="small" key={id} onClick={() => onOpenEvidence(id)}>{id}</Button>)}<Button type="link" size="small" onClick={() => onExplain(item)}>依据</Button></Space></div>
    </Card>;
  })}</div>;
}

function GapsTab({ gaps, onOpenEvidence }) {
  if (!gaps.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有显式 Evidence 缺口。" />;
  return <div className="ccw-expert-list">{gaps.map((gap, index) => <div className="ccw-expert-row" key={gap.gap_id || index}>
    <WarningOutlined className="ccw-expert-icon" />
    <div><div><strong>{gap.required_fact || "未命名缺口"}</strong><Space wrap><Tag color={gap.status === "RESOLVED" ? "green" : gap.status === "BLOCKING" ? "red" : "gold"}>{gap.status}</Tag><Tag>{gap.reason_code}</Tag></Space></div><p>{gap.what_it_does_not_support || gap.blocked_claim || gap.next_best_action || "等待补证"}</p><div className="ccw-step-details"><span>目标 <b>{gap.target || "-"}</b></span><span>可重试 <b>{gap.retryable ? "是" : "否"}</b></span><span>已观察 Evidence <b>{array(gap.observed_evidence).length}</b></span></div><Space wrap>{array(gap.observed_evidence).map((id) => <Button size="small" key={id} onClick={() => onOpenEvidence(id)}>查看 {id}</Button>)}</Space></div>
  </div>)}</div>;
}

function DependencyGraphTab({ dependencyGraph, evidence, onOpenEvidence }) {
  const graph = dependencyGraph?.graph || {};
  const nodes = array(graph.nodes);
  const edges = array(graph.edges);
  const coverage = dependencyGraph?.coverage || {};
  const coverageItems = array(coverage.items);
  const limitations = array(dependencyGraph?.limitations);
  const evidenceRefs = array(dependencyGraph?.evidence_refs);
  const availableEvidenceIds = new Set(evidence.map((item) => String(item.evidence_id || item.id || "")));
  const nodeById = new Map(nodes.map((node) => [node.entity_id, node]));
  const coverageConclusion = String(coverage.conclusion || "").toLowerCase();
  const partial = coverageConclusion === "insufficient_coverage" || coverageItems.some((item) => {
    const status = String(item?.status || item?.conclusion || "").toLowerCase();
    return status === "partial" || status.startsWith("insufficient");
  });
  const managedFractions = [coverage.managed_fraction, ...coverageItems.map((item) => item?.managed_fraction)]
    .map(Number)
    .filter(Number.isFinite);
  const managedFraction = managedFractions.length ? managedFractions[managedFractions.length - 1] : null;
  const totalConnections = edges.reduce((sum, edge) => sum + Number(edge?.metrics?.connections || 0), 0);
  const observationPoints = [...new Set(edges.flatMap((edge) => array(edge.observation_points)))];
  const visibleNodes = nodes.slice(0, 12);
  const visibleEdges = edges.slice(0, 20);
  const visibleLimitations = limitations.slice(0, 8);

  return <div className="ccw-dependency">
    <Alert
      type={partial ? "warning" : "info"}
      showIcon
      message={partial ? "覆盖有限：依赖不等于因果" : "依赖不等于因果"}
      description={partial
        ? "当前结果可用于确认已观测到的通信方向，但仍有覆盖缺口，不能据此确认根因。"
        : "这里展示的是 Evidence 支撑的通信关系；根因仍需由受引用分析和反证共同确认。"}
    />

    {!nodes.length && !edges.length ? <Empty
      image={Empty.PRESENTED_IMAGE_SIMPLE}
      description="尚未发现可展示的通信依赖。空态不代表系统中没有依赖，网络发现 Evidence 就绪后会自动出现。"
    /> : <>
      <div className="ccw-dependency-summary" aria-label="依赖关系摘要">
        <span><small>节点</small><strong>{coverage.node_count ?? nodes.length}</strong></span>
        <span><small>通信边</small><strong>{coverage.edge_count ?? edges.length}</strong></span>
        <span><small>连接数</small><strong>{totalConnections}</strong></span>
        <span><small>观测位置</small><strong>{observationPoints.length ? observationPoints.map((item) => OBSERVATION_POINT_LABELS[item] || item).join("、") : "未标注"}</strong></span>
        <span><small>托管覆盖</small><strong>{formatPercent(managedFraction)}</strong></span>
      </div>

      <section className="ccw-dependency-section" aria-labelledby="ccw-dependency-nodes-title">
        <header><strong id="ccw-dependency-nodes-title">涉及节点</strong><small>{nodes.length > visibleNodes.length ? `先展示 ${visibleNodes.length}/${nodes.length} 个` : `${nodes.length} 个`}</small></header>
        <div className="ccw-dependency-nodes">{visibleNodes.map((node) => <div className="ccw-dependency-node" key={node.entity_id} title={node.entity_id}>
          <div><strong>{dependencyNodeLabel(node)}</strong><Tag>{DEPENDENCY_NODE_TYPE_LABELS[node.entity_type] || node.entity_type || "节点"}</Tag></div>
          <small>{node.process?.pid ? `PID ${node.process.pid}` : node.endpoint?.port != null ? `${node.endpoint.address}:${node.endpoint.port}` : shortIdentifier(node.entity_id)}</small>
          {node.agent_id && <small>Agent：{node.agent_id}</small>}
        </div>)}</div>
      </section>

      <section className="ccw-dependency-section" aria-labelledby="ccw-dependency-edges-title">
        <header><strong id="ccw-dependency-edges-title">调用方向</strong><small>{edges.length > visibleEdges.length ? `先展示 ${visibleEdges.length}/${edges.length} 条` : `${edges.length} 条`}</small></header>
        <div className="ccw-dependency-edges">{visibleEdges.map((edge, index) => {
          const source = nodeById.get(edge.source_entity);
          const target = nodeById.get(edge.target_entity);
          const points = array(edge.observation_points).map((item) => OBSERVATION_POINT_LABELS[item] || item);
          return <div className="ccw-dependency-edge" key={edge.edge_id || index}>
            <div className="ccw-dependency-side"><small>调用方</small><strong title={edge.source_entity}>{dependencyNodeLabel(source) || shortIdentifier(edge.source_entity)}</strong></div>
            <div className="ccw-dependency-direction"><ArrowRightOutlined /><small>{DEPENDENCY_RELATION_LABELS[edge.relation] || edge.relation || "连接"}</small></div>
            <div className="ccw-dependency-side"><small>被调用方</small><strong title={edge.target_entity}>{dependencyNodeLabel(target) || shortIdentifier(edge.target_entity)}</strong></div>
            <div className="ccw-dependency-edge-meta">
              <Tag>{Number(edge.metrics?.connections || 0)} 次连接</Tag>
              <Tag>{String(edge.protocol || "tcp").toUpperCase()} · 端口 {edge.destination_port ?? "-"}</Tag>
              <Tag>观测：{points.length ? points.join("、") : "未标注"}</Tag>
            </div>
          </div>;
        })}</div>
      </section>
    </>}

    <section className="ccw-dependency-section ccw-dependency-provenance" aria-labelledby="ccw-dependency-evidence-title">
      <header><strong id="ccw-dependency-evidence-title">支撑 Evidence</strong><small>{evidenceRefs.length} 条</small></header>
      {evidenceRefs.length ? <Space wrap>{evidenceRefs.map((id) => {
        const available = availableEvidenceIds.has(String(id));
        return <Button
          size="small"
          key={id}
          icon={<FileSearchOutlined />}
          disabled={!available}
          title={available ? String(id) : `Evidence ${id} 当前不可用`}
          onClick={() => onOpenEvidence(id)}
        >{shortIdentifier(id)}{available ? "" : "（不可用）"}</Button>;
      })}</Space> : <Typography.Text type="secondary">当前图尚未绑定可打开的 Evidence。</Typography.Text>}
    </section>

    {visibleLimitations.length > 0 && <section className="ccw-dependency-section ccw-dependency-limitations" aria-labelledby="ccw-dependency-limitations-title">
      <header><strong id="ccw-dependency-limitations-title">覆盖限制</strong><small>{limitations.length} 项</small></header>
      <ul>{visibleLimitations.map((item, index) => <li key={`${item}-${index}`}>{humanizeDependencyLimitation(item)}</li>)}</ul>
      {limitations.length > visibleLimitations.length && <small>另有 {limitations.length - visibleLimitations.length} 项限制未展开。</small>}
    </section>}
  </div>;
}

function CausalGraphTab({ graph, onOpenEvidence }) {
  const nodes = array(graph?.nodes);
  const edges = array(graph?.edges);
  if (!nodes.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未形成可验证因果图。" />;
  const labels = new Map(nodes.map((node) => [node.node_id, node.mechanism || node.entity_ref || node.node_id]));
  return <div className="ccw-causal"><div className="ccw-node-list">{nodes.map((node) => <div className="ccw-node" key={node.node_id}><div><strong>{node.mechanism || node.entity_ref}</strong><Tag color={node.verifier_role && node.verifier_role !== "UNVERIFIED" ? "green" : "default"}>{node.verifier_role || node.role}</Tag></div><small>{node.entity_ref}</small><Space wrap>{array(node.supporting_evidence_refs).map((id) => <Button type="link" size="small" key={id} onClick={() => onOpenEvidence(id)}>{id}</Button>)}</Space></div>)}</div><div className="ccw-edges">{edges.map((edge, index) => <div key={edge.edge_id || index}><strong>{labels.get(edge.source_node_id) || edge.source_node_id}</strong><NodeIndexOutlined /><strong>{labels.get(edge.target_node_id) || edge.target_node_id}</strong><Tag color={edge.verification_state === "SUPPORTED" ? "green" : "default"}>{edge.verification_state || edge.relation}</Tag></div>)}</div></div>;
}

function ConclusionTab({ conclusion, history, onOpenEvidence, onExplain }) {
  if (!conclusion) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未提交结论修订。" />;
  const groups = [
    ["主要原因", conclusion.primary_root_causes], ["促成因素", conclusion.contributing_factors],
    ["放大因素", conclusion.amplifiers], ["传播影响", conclusion.propagated_effects],
    ["已排除", conclusion.ruled_out],
  ];
  const priorRevisions = array(history).filter((item) => item.conclusion_id !== conclusion.conclusion_id);
  return <div className="ccw-conclusion"><div className="ccw-conclusion-head"><div><Tag color={conclusion.state === "CONFIRMED" ? "green" : conclusion.state === "INSUFFICIENT_EVIDENCE" ? "gold" : "processing"}>{conclusion.state}</Tag><strong>结论修订 r{conclusion.revision || 1}</strong></div><Button size="small" onClick={() => onExplain(conclusion)}>校验详情</Button></div><Typography.Paragraph>{conclusion.report_text || conclusion.abstention_reason || "-"}</Typography.Paragraph>{groups.map(([label, values]) => array(values).length > 0 && <div className="ccw-conclusion-group" key={label}><strong>{label}</strong>{array(values).map((value, index) => <Tag key={`${label}-${index}`}>{compact(value)}</Tag>)}</div>)}<div className="ccw-ref-row"><span>Claim bindings <Badge count={count(conclusion.claim_evidence_bindings)} showZero /></span><Space wrap>{array(conclusion.claim_evidence_bindings).slice(0, 4).map((binding) => <Button type="link" size="small" key={`${binding.claim_id}-${binding.evidence_id}`} onClick={() => onOpenEvidence(binding.evidence_id)}>{binding.evidence_id}</Button>)}</Space></div>{priorRevisions.length > 0 && <section className="ccw-conclusion-history" aria-label="历史结论修订"><header><strong>历史修订</strong><small>保留用于对照，不再作为当前依据</small></header>{priorRevisions.map((item) => <div className="ccw-conclusion-history-item" key={item.conclusion_id}><div><Tag color="default">{item.revision_status || "SUPERSEDED"}</Tag><strong>r{item.revision || "-"}</strong><Tag>{item.state || "-"}</Tag></div><Typography.Paragraph ellipsis={{ rows: 2 }}>{item.report_text || item.abstention_reason || "-"}</Typography.Paragraph><Space wrap>{array(item.claim_evidence_bindings).slice(0, 4).map((binding) => <Button type="link" size="small" key={`${item.conclusion_id}-${binding.claim_id}`} onClick={() => onOpenEvidence(binding.evidence_id)}>{binding.evidence_id}</Button>)}</Space></div>)}</section>}</div>;
}

function ExecutionTab({ units, fanoutRuns, requests }) {
  const items = [...requests.map((item) => ({ ...item, _kind: "CollectionRequest" })), ...units.map((item) => ({ ...item, _kind: "Execution" })), ...fanoutRuns.map((item) => ({ ...item, _kind: "Fanout" }))];
  if (!items.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有采集请求或执行记录。" />;
  return <div className="ccw-expert-list">{items.map((item, index) => <div className="ccw-expert-row" key={item.collection_request_id || item.execution_unit_id || item.run_id || index}><NodeIndexOutlined className="ccw-expert-icon" /><div><div><strong>{item.purpose || item.collector_id || item._kind}</strong><Space><Tag>{item._kind}</Tag><Tag color={["COMPLETED", "DONE"].includes(item.status) ? "green" : isCollectionFailure(item.status) ? "red" : "processing"}>{item.status}</Tag></Space></div><div className="ccw-step-details"><span>目标 <b>{item.target_ref || item.cluster_id || compact(item.resolved_target_identity)}</b></span><span>Task <b>{item.task_id || "-"}</b></span><span>覆盖 <b>{item.coverage_ratio ?? item.coverage ?? (item.task_id ? "已调度" : "待调度")}</b></span></div></div></div>)}</div>;
}

function RecommendationsTab({ recommendations, onDiscuss, onCreateRecovery }) {
  if (!recommendations.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无恢复建议。" />;
  return <div className="ccw-expert-list">{recommendations.map((item, index) => <div className="ccw-expert-row" key={item.recommendation_id || index}><ToolOutlined className="ccw-expert-icon" /><div><div><strong>{item.concrete_action || "恢复建议"}</strong><Space><Tag>{item.category}</Tag><Tag color={item.approval ? "gold" : "default"}>{item.risk || "未分级"}</Tag></Space></div><p>{item.rationale || item.expected_effect || "-"}</p><div className="ccw-step-details"><span>目标 <b>{item.target || "-"}</b></span><span>置信度 <b>{Math.round(Number(item.confidence || 0) * 100)}%</b></span><span>验证动作 <b>{array(item.verification_operations).length}</b></span></div><Space wrap><Button size="small" onClick={() => onDiscuss?.(item)}>在会话中讨论</Button><Button size="small" type="primary" onClick={() => onCreateRecovery?.(item)}>创建受控恢复方案</Button></Space></div></div>)}</div>;
}

/** Canonical supervised-investigation workspace backed by one aggregate snapshot. */
export default function CanonicalCaseWorkspace({ workspace, connected, caseId, focusEvidenceId, onFocusEvidenceConsumed, onRefresh, onDiscussRecommendation, onCreateRecovery }) {
  const [plan, setPlan] = useState(null);
  const [reviews, setReviews] = useState([]);
  const [loading, setLoading] = useState(false);
  const [selectedEvidence, setSelectedEvidence] = useState(null);
  const [selectedCitation, setSelectedCitation] = useState(null);
  const [explainDecision, setExplainDecision] = useState(null);
  const [loadErrors, setLoadErrors] = useState({});
  const [showInternals, setShowInternals] = useState(false);
  const [activeTab, setActiveTab] = useState("goals");
  const evidence = useMemo(() => array(workspace?.evidence), [workspace?.evidence]);
  const latestReviews = useMemo(() => latestReviewMap(reviews), [reviews]);
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
  useEffect(() => {
    if (!focusEvidenceId || !workspace) return;
    const target = evidence.find((item) => (item.evidence_id || item.id) === focusEvidenceId);
    setActiveTab("evidence");
    setSelectedCitation(null);
    if (target) setSelectedEvidence(target);
    else message.warning(`Evidence ${focusEvidenceId} 当前不可用`);
    onFocusEvidenceConsumed?.();
  }, [evidence, focusEvidenceId, onFocusEvidenceConsumed, workspace]);
  if (!workspace) return null;
  const proposals = array(workspace.collection_proposals);
  const requests = array(workspace.collection_requests);
  const analyses = array(workspace.evidence_analyses);
  const hypothesisGraph = workspace.hypothesis_graph || { hypotheses: workspace.hypotheses || [] };
  const dependencyGraph = workspace.dependency_graph || {};
  const dependencyNodes = array(dependencyGraph.graph?.nodes);
  const dependencyEdges = array(dependencyGraph.graph?.edges);
  const dependencyCoverageItems = array(dependencyGraph.coverage?.items);
  const dependencyPartial = String(dependencyGraph.coverage?.conclusion || "").toLowerCase() === "insufficient_coverage"
    || dependencyCoverageItems.some((item) => {
      const status = String(item?.status || item?.conclusion || "").toLowerCase();
      return status === "partial" || status.startsWith("insufficient");
    });
  const conclusion = workspace.conclusion || null;
  const recommendations = array(workspace.recommendations);
  const informationGoals = array(workspace.information_goals);
  const revisions = workspace.revisions || {};
  const openById = (id) => {
    const target = evidence.find((item) => (item.evidence_id || item.id) === id);
    setSelectedCitation(null);
    if (target) setSelectedEvidence(target);
    else message.warning(`Evidence ${id || "-"} 当前不可用`);
  };
  const openCitation = (citation) => {
    const target = evidence.find((item) => (item.evidence_id || item.id) === citation?.evidence_id);
    if (!target) {
      message.warning(`Evidence ${citation?.evidence_id || "-"} 当前不可用`);
      return;
    }
    setSelectedCitation(citation);
    setSelectedEvidence(target);
  };
  const items = [
    { key: "goals", label: <Space><AimOutlined />信息目标 <Badge count={informationGoals.length || count(plan?.steps)} showZero /></Space>, children: <InformationGoalsTab goals={informationGoals} plan={plan} error={loadErrors.plan} onRetry={load} onExplain={setExplainDecision} onOpenEvidence={openById} onNavigate={setActiveTab} showInternals={showInternals} /> },
    { key: "collections", label: <Space><DatabaseOutlined />采集活动 <Badge count={Math.max(proposals.length, requests.length)} showZero /></Space>, children: <CollectionActivityTab proposals={proposals} requests={requests} evidence={evidence} showInternals={showInternals} caseId={caseId} revisions={revisions} onChanged={onRefresh} onOpenEvidence={openById} /> },
    { key: "evidence", label: <Space><FileSearchOutlined />Evidence <Badge count={evidence.length} showZero /></Space>, children: <EvidenceTab evidence={evidence} reviews={reviews} goals={informationGoals} hypothesisGraph={hypothesisGraph} error={loadErrors.reviews} onRetry={load} onOpen={setSelectedEvidence} onExplain={setExplainDecision} showInternals={showInternals} /> },
    { key: "dependencies", label: <Space><NodeIndexOutlined />依赖关系 <Badge count={dependencyEdges.length} showZero /></Space>, children: <DependencyGraphTab dependencyGraph={dependencyGraph} evidence={evidence} onOpenEvidence={openById} /> },
    { key: "analyses", label: <Space><RobotOutlined />受引用分析 <Badge count={analyses.length} showZero /></Space>, children: <AnalysisTab analyses={analyses} evidence={evidence} onOpenEvidence={openById} onOpenCitation={openCitation} onNavigate={setActiveTab} /> },
    { key: "conclusion", label: <Space><SafetyCertificateOutlined />结论修订 <Badge count={array(workspace.conclusion_history).length || (conclusion ? 1 : 0)} showZero /></Space>, children: <ConclusionTab conclusion={conclusion} history={workspace.conclusion_history} onOpenEvidence={openById} onExplain={setExplainDecision} /> },
    { key: "recommendations", label: <Space><ToolOutlined />恢复建议 <Badge count={recommendations.length} showZero /></Space>, children: <RecommendationsTab recommendations={recommendations} onDiscuss={onDiscussRecommendation} onCreateRecovery={onCreateRecovery} /> },
  ];
  const goalItems = informationGoals.length ? informationGoals : array(plan?.steps);
  const goalStatuses = goalItems.map((item) => String(item.status || "PROPOSED").toUpperCase());
  const collectionItems = [...proposals, ...requests];
  const collectionStatuses = collectionItems.map((item) => String(item.status || "PROPOSED").toUpperCase());
  const analysisStatuses = analyses.map((item) => String(item.status || "QUEUED").toUpperCase());
  const stages = [
    { key: "goals", label: "信息目标", shortLabel: "目标", icon: <AimOutlined />, count: goalItems.length, state: stageState({ count: goalItems.length, attention: goalStatuses.some((status) => ["BLOCKED", "FAILED", "WAITING_APPROVAL"].includes(status)), active: goalStatuses.some((status) => ["PROPOSED", "COLLECTING", "RUNNING"].includes(status)), ready: goalStatuses.every((status) => ["RESOLVED", "EVIDENCE_READY", "COMPLETED"].includes(status)) }) },
    { key: "collections", label: "采集活动", shortLabel: "采集", icon: <DatabaseOutlined />, count: collectionItems.length, state: stageState({ count: collectionItems.length, attention: collectionStatuses.some(isCollectionFailure) || proposals.some((item) => item.status === "PROPOSED" && item.validation_result?.awaiting_execution_authority), active: collectionStatuses.some((status) => ["PROPOSED", "ACCEPTED", "DISPATCHED", "RUNNING"].includes(status)), ready: collectionStatuses.every((status) => ["COMPLETED", "DONE", "SUCCEEDED"].includes(status)), available: true }) },
    { key: "evidence", label: "Evidence", shortLabel: "Evidence", icon: <FileSearchOutlined />, count: evidence.length, state: stageState({ count: evidence.length, attention: evidence.some((item) => ["EXCLUDED", "LOW_TRUST", "STALE"].includes(String(item.status || item.trust_status || "").toUpperCase())) || [...latestReviews.values()].some((item) => ["EXCLUDED", "LOW_TRUST"].includes(String(item.decision || "").toUpperCase())), ready: evidence.length > 0 }) },
    { key: "dependencies", label: "依赖关系", shortLabel: "依赖", icon: <NodeIndexOutlined />, count: dependencyEdges.length, state: !dependencyNodes.length && !dependencyEdges.length ? "empty" : dependencyPartial ? "partial" : "ready" },
    { key: "analyses", label: "受引用分析", shortLabel: "分析", icon: <RobotOutlined />, count: analyses.length, state: stageState({ count: analyses.length, attention: analyses.some((item) => item.status === "FAILED" || (item.input_state && item.input_state !== "CURRENT")), active: analysisStatuses.some((status) => ["QUEUED", "RUNNING"].includes(status)), ready: analyses.some((item) => item.status === "COMPLETED" && (!item.input_state || item.input_state === "CURRENT")) }) },
    { key: "conclusion", label: "结论修订", shortLabel: "结论", icon: <SafetyCertificateOutlined />, count: conclusion ? 1 : 0, state: stageState({ count: conclusion ? 1 : 0, attention: conclusion?.state === "INSUFFICIENT_EVIDENCE", active: conclusion && !["CONFIRMED", "INSUFFICIENT_EVIDENCE"].includes(conclusion.state), ready: conclusion?.state === "CONFIRMED", available: Boolean(conclusion) }) },
    { key: "recommendations", label: "恢复建议", shortLabel: "恢复", icon: <ToolOutlined />, count: recommendations.length, state: stageState({ count: recommendations.length, available: recommendations.length > 0 }) },
  ];
  const activeItem = items.find((item) => item.key === activeTab) || items[0];
  const handleStageKeyDown = (event, index) => {
    let nextIndex = index;
    if (["ArrowRight", "ArrowDown"].includes(event.key)) nextIndex = (index + 1) % stages.length;
    else if (["ArrowLeft", "ArrowUp"].includes(event.key)) nextIndex = (index - 1 + stages.length) % stages.length;
    else if (event.key === "Home") nextIndex = 0;
    else if (event.key === "End") nextIndex = stages.length - 1;
    else return;
    event.preventDefault();
    const next = stages[nextIndex];
    setActiveTab(next.key);
    document.getElementById(`ccw-tab-${next.key}`)?.focus();
  };
  return <section className="ccw-shell" aria-label="Case Workspace" data-testid="canonical-workspace">
    <header className="ccw-header"><div><h2>AI 调查与 Evidence 工作区</h2></div><div className="ccw-status"><Tag color={connected ? "success" : "orange"}>{connected ? "实时同步" : "轮询同步"}</Tag><Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={() => { void load(); onRefresh?.(); }}>刷新</Button><Button size="small" type="text" onClick={() => setShowInternals((value) => !value)}>{showInternals ? "隐藏技术细节" : "技术细节"}</Button></div></header>
    <nav className="ccw-stagebar" aria-label="工作区导航" role="tablist">{stages.map((stage, index) => <button type="button" role="tab" id={`ccw-tab-${stage.key}`} aria-controls={`ccw-panel-${stage.key}`} aria-selected={activeTab === stage.key} tabIndex={activeTab === stage.key ? 0 : -1} aria-label={`${stage.label}，${STAGE_STATE_LABEL[stage.state]}，${stage.count} 项`} key={stage.key} className={`${activeTab === stage.key ? "is-active" : ""} is-${stage.state}`} onClick={() => setActiveTab(stage.key)} onKeyDown={(event) => handleStageKeyDown(event, index)}><span className="ccw-stage-icon">{stage.icon}</span><strong>{stage.shortLabel}</strong><span className="ccw-stage-state" aria-hidden="true" /><small>{stage.count}</small></button>)}</nav>
    {showInternals && <div className="ccw-revisions"><span>命令 r{revisions.case_command || 0}</span><span>控制 r{revisions.control || 0}</span><span>范围 r{revisions.scope || 0}</span><span>运行时 {workspace.engine?.state || "IDLE"}</span><span>事件 #{workspace.last_event_seq || 0}</span></div>}
    {loading && !plan ? <Skeleton active paragraph={{ rows: 6 }} /> : <div className="ccw-tab-panel" role="tabpanel" id={`ccw-panel-${activeItem.key}`} aria-labelledby={`ccw-tab-${activeItem.key}`} tabIndex={0}>{activeItem.children}</div>}
    <EvidenceDrawer open={Boolean(selectedEvidence)} onClose={() => { setSelectedEvidence(null); setSelectedCitation(null); }} caseId={caseId} evidence={selectedEvidence} focusCitation={selectedCitation} onChanged={() => { void load(); onRefresh?.(); }} onExplain={setExplainDecision} />
    <ExplainabilityDrawer open={Boolean(explainDecision)} onClose={() => setExplainDecision(null)} decision={explainDecision} />
  </section>;
}

export { CausalGraphTab, DependencyGraphTab, ExecutionTab, GapsTab, HypothesesTab };
