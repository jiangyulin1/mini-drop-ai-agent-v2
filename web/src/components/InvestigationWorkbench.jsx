import { useCallback, useEffect, useMemo, useState } from "react";

import "./InvestigationWorkbench.css";

import {
  cancelCasePlanStep,
  createCaseFanout,
  getCaseInvestigationPlan,
  listCaseEvidenceReviews,
  listCaseFanoutRuns,
  removeCasePlanStep,
  reprioritizeCasePlanStep,
  retargetCasePlanStep,
  reviewCaseEvidence,
} from "../api/client";

const RUNNING_STATES = new Set(["RUNNING", "DISPATCHING", "CANCEL_REQUESTED"]);
const NEXT_STATES = new Set(["QUEUED", "WAITING_APPROVAL", "DRAFT"]);
const HISTORY_STATES = new Set([
  "COMPLETED", "FAILED", "CANCELLED", "REMOVED_BY_USER", "SUPERSEDED",
  "SKIPPED_REUSED", "BLOCKED",
]);

const RISK_LABEL = { READ_LOW: "低风险", READ_ELEVATED: "中风险", WRITE: "高风险" };
const STATUS_LABEL = {
  DRAFT: "草稿", QUEUED: "待执行", WAITING_APPROVAL: "待审批",
  DISPATCHING: "派发中", RUNNING: "进行中", CANCEL_REQUESTED: "取消中",
  COMPLETED: "已完成", FAILED: "失败", CANCELLED: "已取消",
  REMOVED_BY_USER: "已移除", SUPERSEDED: "已取代", SKIPPED_REUSED: "复用跳过",
  BLOCKED: "受阻",
};

/**
 * E5 调查工作台：用户能看懂、参与和控制低风险调查计划。
 * - 当前工作 / 下一步 / 历史任务卡；
 * - 拖拽排序（reprioritize）、删除、改目标、取消、集群扇出；
 * - Evidence Trust/Exclude 审查；
 * - 断线状态与恢复提示。
 */
export default function InvestigationWorkbench({ caseId }) {
  const [plan, setPlan] = useState(null);
  const [reviews, setReviews] = useState([]);
  const [fanoutRuns, setFanoutRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const [dragIndex, setDragIndex] = useState(null);

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

  const steps = useMemo(() => (plan?.steps ?? []).slice(), [plan]);
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
    try {
      await fn();
      await refresh();
    } catch (err) {
      setError(String(err?.message || err));
    }
    if (successMessage) setError(successMessage);
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
      draggable={NEXT_STATES.has(step.status) || RUNNING_STATES.has(step.status)}
      onDragStart={() => setDragIndex(index)}
      onDragOver={(e) => e.preventDefault()}
      onDrop={() => handleDrop(index)}
    >
      <div className="iw-step-head">
        <span className="iw-step-collector">{step.collector_id || step.kind}</span>
        <span className="iw-tag">{RISK_LABEL[step.risk] || step.risk}</span>
        <span className="iw-tag" data-status={step.status}>
          {STATUS_LABEL[step.status] || step.status}
        </span>
        <span className="iw-priority">P{step.priority ?? 0}</span>
      </div>
      <div className="iw-step-purpose">{step.purpose || "（无说明）"}</div>
      {step.selection_strategy && (
        <div className="iw-step-meta">集群策略：{step.selection_strategy}</div>
      )}
      <div className="iw-step-actions">
        {NEXT_STATES.has(step.status) && (
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
        {RUNNING_STATES.has(step.status) && (
          <button onClick={() => runAction(
            () => cancelCasePlanStep(caseId, step.step_id, {}),
            `已请求取消：${step.collector_id}`,
          )}>停止</button>
        )}
        {NEXT_STATES.has(step.status) && (
          <>
            <button onClick={() => {
              const collector = window.prompt("新的采集器（collector_id）", step.collector_id);
              if (collector) {
                runAction(() => retargetCasePlanStep(caseId, step.step_id, {
                  collector_id: collector,
                }), `已改目标：${collector}`);
              }
            }}>改目标</button>
            <button onClick={() => fanoutStep(step)}>集群扇出</button>
          </>
        )}
        <span className="iw-drag-hint">拖拽排序</span>
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

      <div className="iw-plan-header">
        <strong>调查计划</strong>
        <span>Revision {plan?.plan_revision ?? "—"} · 目标：{plan?.goal || "—"}</span>
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
        <h4>证据审查（{reviews.length}）</h4>
        {reviews.length === 0 ? (
          <p className="iw-empty">暂无证据审查记录</p>
        ) : (
          reviews.map((review) => (
            <div className="iw-review-row" key={review.review_id}>
              <span className="iw-review-id">{review.evidence_id}</span>
              <span className="iw-tag" data-status={review.decision}>{review.decision}</span>
              <span className="iw-review-reason">{review.reason || review.reason_code || ""}</span>
            </div>
          ))
        )}
        <div className="iw-review-actions">
          <button onClick={() => reviewEvidence(prompt("证据 ID"), "TRUSTED")}>信任证据</button>
          <button onClick={() => reviewEvidence(prompt("证据 ID"), "LOW_TRUST")}>降信任</button>
          <button onClick={() => reviewEvidence(prompt("证据 ID"), "EXCLUDED")}>排除证据</button>
        </div>
      </div>

      <div className="iw-fanout" aria-label="集群扇出运行">
        <h4>集群扇出（{fanoutRuns.length}）</h4>
        {fanoutRuns.length === 0 ? (
          <p className="iw-empty">暂无扇出运行</p>
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
