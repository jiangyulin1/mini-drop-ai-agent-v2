import { useCallback, useEffect, useMemo, useState } from "react";

import "./InvestigationWorkbench.css";

import {
  cancelCasePlanStep,
  createCaseFanout,
  getCaseInvestigationPlan,
  listAcquisitionOperations,
  listCaseEvidenceReviews,
  listCaseFanoutRuns,
  removeCasePlanStep,
  reprioritizeCasePlanStep,
  retargetCasePlanStep,
  reviewCaseEvidence,
} from "../api/client";
import { EVIDENCE_TRUST, PLAN_STATUS, RISK_LEVEL } from "../utils/opsMappings";

const RUNNING_STATES = new Set(["RUNNING", "DISPATCHING", "CANCEL_REQUESTED"]);
const NEXT_STATES = new Set(["QUEUED", "WAITING_APPROVAL", "DRAFT"]);
const HISTORY_STATES = new Set([
  "COMPLETED", "FAILED", "CANCELLED", "REMOVED_BY_USER", "SUPERSEDED",
  "SKIPPED_REUSED", "BLOCKED",
]);

/**
 * E5 调查工作台：用户能看懂、参与和控制低风险调查计划。
 * - 当前工作 / 下一步 / 历史任务卡；
 * - 拖拽排序（reprioritize）、删除、改目标、取消、集群扇出；
 * - Evidence Trust/Exclude 审查；
 * - 断线状态与恢复提示。
 */
function normalizeActivityStatus(proposal, request) {
  const requestStatus = request?.status;
  if (requestStatus) return requestStatus;
  if (proposal?.status === "REJECTED") return "BLOCKED";
  if ((proposal?.validation_result || {}).awaiting_execution_authority) return "WAITING_APPROVAL";
  return proposal?.status === "PROPOSED" ? "DRAFT" : (proposal?.status || "DRAFT");
}

function buildActivityTimeline(workspace) {
  const proposals = workspace?.collection_proposals || [];
  const requests = workspace?.collection_requests || [];
  const requestsByProposal = new Map(
    requests.map((item) => [String(item.proposal_id || ""), item]),
  );
  const consumedRequests = new Set();
  const steps = proposals.map((proposal, index) => {
    const request = requestsByProposal.get(String(proposal.proposal_id || ""));
    if (request?.collection_request_id) consumedRequests.add(request.collection_request_id);
    return {
      step_id: `activity:${proposal.proposal_id || index}`,
      collector_id: proposal.collector_id || request?.collector_id || "采集提案",
      purpose: proposal.information_goal || proposal.reason_summary || "Agent 采集活动",
      priority: Math.max(0, proposals.length - index),
      risk: proposal.expected_risk || request?.risk_level,
      status: normalizeActivityStatus(proposal, request),
      _readOnly: true,
    };
  });
  for (const [index, request] of requests.entries()) {
    if (consumedRequests.has(request.collection_request_id)) continue;
    steps.push({
      step_id: `request:${request.collection_request_id || index}`,
      collector_id: request.collector_id || "采集请求",
      purpose: request.information_goal || request.reason_summary || "Agent 发起的采集请求",
      priority: 0,
      risk: request.risk_level,
      status: request.status || "DRAFT",
      _readOnly: true,
    });
  }
  return steps;
}

export default function InvestigationWorkbench({ caseId, workspace }) {
  const [plan, setPlan] = useState(null);
  const [reviews, setReviews] = useState([]);
  const [fanoutRuns, setFanoutRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [dragIndex, setDragIndex] = useState(null);
  const [collectors, setCollectors] = useState([]);

  useEffect(() => {
    let cancelled = false;
    Promise.resolve()
      .then(() => listAcquisitionOperations?.())
      .then((resp) => {
        if (cancelled) return;
        const items = resp?.items || resp?.data?.items || [];
        const ids = items
          .map((item) => item.collector_id || item.operation_id || item.id)
          .filter(Boolean);
        setCollectors([...new Set(ids)].sort());
      })
      .catch(() => {
        // Retargeting stays unavailable rather than falling back to free text.
        if (!cancelled) setCollectors([]);
      });
    return () => { cancelled = true; };
  }, []);

  const refresh = useCallback(async () => {
    if (!caseId) return;
    try {
      const [planResp, reviewsResp, fanoutResp] = await Promise.all([
        getCaseInvestigationPlan(caseId),
        listCaseEvidenceReviews(caseId),
        listCaseFanoutRuns(caseId),
      ]);
      // client.js interceptor already unwraps body.data.  Keep a narrow
      // compatibility branch for tests/legacy callers that still return an
      // Axios-shaped response; production never reads response.data.data.
      const planPayload = planResp?.data?.plan_id ? planResp.data : planResp;
      const reviewPayload = reviewsResp?.data?.items ? reviewsResp.data : reviewsResp;
      const fanoutPayload = fanoutResp?.data?.items ? fanoutResp.data : fanoutResp;
      setPlan(planPayload ?? null);
      setReviews(reviewPayload?.items ?? []);
      setFanoutRuns(fanoutPayload?.items ?? []);
      setOffline(false);
      setError("");
    } catch (err) {
      setOffline(true);
      setError(String(err?.message || err));
    } finally {
      setLoading(false);
    }
  }, [caseId]);

  useEffect(() => {
    setLoading(true);
    refresh();
    const timer = window.setInterval(refresh, 8000);
    return () => window.clearInterval(timer);
  }, [caseId, refresh]);

  const activitySteps = useMemo(() => buildActivityTimeline(workspace), [workspace]);
  const hasPersistedPlan = Boolean(plan?.plan_id);
  const steps = useMemo(
    () => hasPersistedPlan ? (plan?.steps ?? []).slice() : activitySteps,
    [activitySteps, hasPersistedPlan, plan],
  );
  const reviewRows = useMemo(() => {
    const latestReview = new Map();
    for (const review of reviews) {
      if (!latestReview.has(review.evidence_id)) latestReview.set(review.evidence_id, review);
    }
    const evidence = workspace?.evidence || [];
    const rows = evidence.map((item) => latestReview.get(item.evidence_id) || {
      review_id: `unreviewed:${item.evidence_id}`,
      evidence_id: item.evidence_id,
      decision: "UNREVIEWED",
      reason: `${item.evidence_type || item.collector_id || "Evidence"} · 尚未人工审查`,
    });
    const evidenceIds = new Set(evidence.map((item) => item.evidence_id));
    for (const review of latestReview.values()) {
      if (!evidenceIds.has(review.evidence_id)) rows.push(review);
    }
    return rows;
  }, [reviews, workspace]);
  const reviewedEvidenceCount = useMemo(
    () => reviewRows.filter((item) => item.decision !== "UNREVIEWED").length,
    [reviewRows],
  );
  const group = useMemo(() => {
    const buckets = { current: [], next: [], history: [] };
    for (const step of steps) {
      const s = step.status;
      if (RUNNING_STATES.has(s)) buckets.current.push(step);
      else if (NEXT_STATES.has(s)) buckets.next.push(step);
      else buckets.history.push(step);
    }
    return buckets;
  }, [steps]);

  const runAction = async (fn, successMessage) => {
    setNotice("");
    setError("");
    try {
      await fn();
      await refresh();
      if (successMessage) setNotice(successMessage);
    } catch (err) {
      setError(String(err?.message || err));
    }
  };

  const handleDrop = (targetIndex) => {
    if (dragIndex === null || dragIndex === targetIndex) {
      setDragIndex(null);
      return;
    }
    const ordered = [...steps].sort((a, b) => (b.priority ?? 0) - (a.priority ?? 0));
    const dragged = ordered[dragIndex];
    const target = ordered[targetIndex];
    const newPriority = Math.min(1000, (target?.priority ?? 0) + 1);
    setDragIndex(null);
    if (dragged?.step_id) {
      runAction(() => reprioritizeCasePlanStep(caseId, dragged.step_id, {
        priority: newPriority, user_locked: true,
      }), `已排序：${dragged.collector_id} 优先级 → ${newPriority}`);
    }
  };

  const fanoutStep = (step) => {
    if (!step?.step_id) return;
    runAction(() => createCaseFanout(caseId, {
      step_id: step.step_id,
      strategy: step.selection_strategy || "REPRESENTATIVE",
    }), `已创建扇出：${step.collector_id}`);
  };

  const reviewEvidence = (evidenceId, decision) => {
    if (!evidenceId) return;
    runAction(() => reviewCaseEvidence(caseId, evidenceId, {
      evidence_id: evidenceId, decision,
      reason_code: decision === "EXCLUDED" ? "USER_EXCLUDED" : undefined,
      reason: decision === "EXCLUDED" ? "用户从本调查中排除该证据" : "用户已复核该证据",
    }), `证据 ${evidenceId} → ${decision}`);
  };

  const StepCard = ({ step, index }) => (
    <div
      className="iw-step-card"
      data-status={step.status}
      draggable={!step._readOnly && (NEXT_STATES.has(step.status) || RUNNING_STATES.has(step.status))}
      onDragStart={() => setDragIndex(index)}
      onDragOver={(e) => e.preventDefault()}
      onDrop={() => handleDrop(index)}
    >
      <div className="iw-step-head">
        <span className="iw-step-collector">{step.collector_id || step.kind}</span>
        <span className="iw-tag">{RISK_LEVEL[step.risk]?.label || step.risk}</span>
        <span className="iw-tag" data-status={step.status}>
          {PLAN_STATUS[step.status]?.label || step.status}
        </span>
        <span className="iw-priority">P{step.priority ?? 0}</span>
      </div>
      <div className="iw-step-purpose">{step.purpose || "（无说明）"}</div>
      {step.selection_strategy && (
        <div className="iw-step-meta">集群策略：{step.selection_strategy}</div>
      )}
      <div className="iw-step-actions">
        {!step._readOnly && NEXT_STATES.has(step.status) && (
          <>
            <button onClick={() => runAction(
              () => cancelCasePlanStep(caseId, step.step_id, {}),
              `已取消：${step.collector_id}`,
            )}>取消</button>
            <button onClick={() => runAction(
              () => removeCasePlanStep(caseId, step.step_id, {}),
              `已移除：${step.collector_id}`,
            )}>移除</button>
          </>
        )}
        {!step._readOnly && RUNNING_STATES.has(step.status) && (
          <button onClick={() => runAction(
            () => cancelCasePlanStep(caseId, step.step_id, {}),
            `已请求取消：${step.collector_id}`,
          )}>停止</button>
        )}
        {!step._readOnly && NEXT_STATES.has(step.status) && (
          <>
            {/* Pick from the registered collectors instead of asking the user
                to recall and type a collector_id. */}
            <select
              className="iw-retarget"
              aria-label="改目标采集器"
              value=""
              onChange={(event) => {
                const collector = event.target.value;
                event.target.value = "";
                if (!collector || collector === step.collector_id) return;
                runAction(() => retargetCasePlanStep(caseId, step.step_id, {
                  collector_id: collector,
                }), `已改目标：${collector}`);
              }}
            >
              <option value="">改目标…</option>
              {collectors
                .filter((item) => item !== step.collector_id)
                .map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
            <button onClick={() => fanoutStep(step)}>集群扇出</button>
          </>
        )}
        <span className="iw-drag-hint">{step._readOnly ? "事件轨迹" : "拖拽排序"}</span>
      </div>
    </div>
  );

  if (loading) return <div className="iw-loading">正在加载调查计划…</div>;

  return (
    <section className="iw-workbench" aria-label="调查工作台">
      {offline && (
        <div className="iw-offline" role="status">
          ⚠️ 连接中断，正在自动恢复…{error ? `（${error}）` : ""}
        </div>
      )}
      {error && !offline && <div className="iw-error">{error}</div>}
      {notice && !error && <div className="iw-notice">{notice}</div>}

      <div className="iw-plan-header">
        <strong>{hasPersistedPlan ? "调查计划" : "即时调查轨迹"}</strong>
        <span>
          {hasPersistedPlan ? `Revision ${plan?.plan_revision ?? "—"}` : `${steps.length} 项活动`}
          {` · 目标：${plan?.goal || workspace?.case?.problem_description || workspace?.case?.title || "—"}`}
        </span>
        <button onClick={refresh}>刷新</button>
      </div>

      <div className="iw-groups">
        <div className="iw-group" aria-label="当前工作">
          <h4>当前工作</h4>
          {group.current.length === 0 ? <p className="iw-empty">没有进行中的步骤</p> : (
            group.current.map((step, i) => <StepCard key={step.step_id} step={step} index={i} />)
          )}
        </div>

        <div className="iw-group" aria-label="下一步">
          <h4>下一步</h4>
          {group.next.length === 0 ? <p className="iw-empty">没有待执行步骤</p> : (
            group.next.map((step, i) => <StepCard key={step.step_id} step={step} index={i + group.current.length} />)
          )}
        </div>

        <div className="iw-group" aria-label="历史任务">
          <h4>历史任务</h4>
          {group.history.length === 0 ? <p className="iw-empty">暂无历史步骤</p> : (
            group.history.map((step, i) => <StepCard key={step.step_id} step={step} index={i} />)
          )}
        </div>
      </div>

      <div className="iw-evidence" aria-label="证据审查">
        <h4>证据审查（{reviewedEvidenceCount}/{reviewRows.length}）</h4>
        {reviewRows.length === 0 ? (
          <p className="iw-empty">暂无可审查 Evidence</p>
        ) : (
          reviewRows.map((review) => (
            <div className="iw-review-row" key={review.review_id}>
              <span className="iw-review-id">{review.evidence_id}</span>
              <span className="iw-tag" data-status={review.decision}>
                {review.decision === "UNREVIEWED" ? "未人工审查" : (EVIDENCE_TRUST[review.decision]?.label || review.decision)}
              </span>
              <span className="iw-review-reason">{review.reason || review.reason_code || ""}</span>
              {/* Act on the evidence in front of you -- never ask the user to
                  retype an opaque evidence id. */}
              <span className="iw-review-actions">
                <button onClick={() => reviewEvidence(review.evidence_id, "TRUSTED")}>信任</button>
                <button onClick={() => reviewEvidence(review.evidence_id, "LOW_TRUST")}>降信任</button>
                <button onClick={() => reviewEvidence(review.evidence_id, "EXCLUDED")}>排除</button>
              </span>
            </div>
          ))
        )}
      </div>

      <div className="iw-fanout" aria-label="集群扇出运行">
        <h4>集群扇出（{fanoutRuns.length}）</h4>
        {fanoutRuns.length === 0 ? (
          <p className="iw-empty">本 Case 未触发跨节点采集</p>
        ) : (
          fanoutRuns.map((run) => (
            <div className="iw-fanout-row" key={run.run_id}>
              <span>{run.strategy}</span>
              <span>覆盖率 {Math.round((run.coverage || 0) * 100)}%</span>
              <span className="iw-tag" data-status={run.status}>{run.status}</span>
              <span className="iw-fanout-conclusion">{run.aggregate?.conclusion || "—"}</span>
            </div>
          ))
        )}
      </div>
    </section>
  );
}
