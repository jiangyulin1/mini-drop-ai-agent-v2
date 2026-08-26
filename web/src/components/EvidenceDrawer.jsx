import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Descriptions, Divider, Drawer, Empty, Form, Input, List, Modal, Select, Space, Tag, Tooltip, Typography, message, InputNumber } from "antd";
import { CheckCircleOutlined, CloseCircleOutlined, DownloadOutlined, FileSearchOutlined, HistoryOutlined, RobotOutlined, WarningOutlined } from "@ant-design/icons";
import {
  createCaseEvidenceAnalysis,
  downloadCaseEvidence,
  getCaseEvidence,
  previewCaseEvidenceReview,
  previewCaseEvidence,
  reviewCaseEvidence,
  getEvidenceChainImpact,
  adjustEvidenceChainConfidence,
} from "../api/client";
import { formatArtifactSize } from "../utils/evidence";
import { evidenceTrust } from "../utils/opsMappings";
import { formatBeijingDateTime } from "../utils/time";
import ErrorAlert from "./ErrorAlert";
import styles from "./EvidenceDrawer.module.css";

function value(item, ...keys) { for (const key of keys) if (item?.[key] !== undefined && item?.[key] !== null && item?.[key] !== "") return item[key]; return null; }
function stringify(input) { return typeof input === "string" ? input : JSON.stringify(input, null, 2); }

function resolveFieldPath(input, fieldPath) {
  if (!input || typeof input !== "object" || !fieldPath) return { found: false, value: undefined };
  const normalized = String(fieldPath).replace(/^projection\./, "").replace(/\[(\d+)\]/g, ".$1");
  const segments = normalized.split(".").filter(Boolean);
  let current = input;
  for (const segment of segments) {
    if ((Array.isArray(current) || (current && typeof current === "object")) && Object.prototype.hasOwnProperty.call(current, segment)) current = current[segment];
    else return { found: false, value: undefined };
  }
  return { found: true, value: current };
}

function shortHash(value) {
  const text = String(value || "-");
  return text.length > 18 ? `${text.slice(0, 8)}…${text.slice(-6)}` : text;
}

const REOPEN_POLICY_META = {
  NO_REOPEN: { label: "仅影响展示", color: "default" },
  AUTO_RECALIBRATE: { label: "可自动重新校准", color: "blue" },
  BLOCKED_NEEDS_APPROVAL: { label: "需要人工审批", color: "red" },
};

const ANALYSIS_STATUS_LABELS = {
  QUEUED: "排队中", RUNNING: "分析中", COMPLETED: "已完成", FAILED: "分析失败",
};
const ANALYSIS_MODE_LABELS = { SINGLE: "单条 Evidence 分析", EVIDENCE: "Evidence 分析", BATCH: "批量 Evidence 分析" };
const REVIEW_LABELS = { TRUSTED: "可信", VALID: "有效", LOW_TRUST: "低可信", EXCLUDED: "已排除", UNREVIEWED: "未人工复核", RESTORE_AS_TRUSTED: "恢复为可信", RESTORE_AS_LOW_TRUST: "恢复为低可信" };
const COLLECTOR_LABELS = { runtime_snapshot: "运行时快照", process_scan: "进程扫描", sys_metrics: "系统指标", log_scan: "日志扫描", network_discovery: "网络拓扑快照", connection_probe: "下游连通性探针", perf_cpu: "CPU 热点采样", pyspy: "Python 热点采样" };
function labelOf(map, value, fallback = "未标注") { const key = String(value || "").toUpperCase(); return map[key] || map[String(value || "")] || fallback; }
function collectorLabel(value) { return COLLECTOR_LABELS[String(value || "")] || String(value || "未指定采集器"); }

export default function EvidenceDrawer({ open, onClose, caseId, evidence, focusCitation, onChanged, onExplain }) {
  const [form] = Form.useForm();
  const [reviewOpen, setReviewOpen] = useState(false);
  const [decision, setDecision] = useState("TRUSTED");
  const [reviews, setReviews] = useState([]);
  const [analyses, setAnalyses] = useState([]);
  const [detail, setDetail] = useState(null);
  const [preview, setPreview] = useState(null);
  const [reviewImpact, setReviewImpact] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [chainImpact, setChainImpact] = useState(null);
  const [adjustment, setAdjustment] = useState(null);
  const [adjustmentForm] = Form.useForm();
  const item = useMemo(() => detail || evidence || {}, [detail, evidence]);
  const evidenceId = evidence?.evidence_id || evidence?.id;
  const trustValue = item.review_trust_state || item.trust_status || item.review_decision || item.status || "UNKNOWN";
  const trust = evidenceTrust(trustValue);
  const raw = useMemo(() => preview?.content || preview?.text_preview || value(item, "raw_data", "content", "data", "summary", "projections"), [item, preview]);
  const citationProjection = useMemo(() => {
    if (!focusCitation) return null;
    const targetHash = focusCitation.projection_hash;
    const historical = targetHash
      ? (detail?.projections || []).find((projection) => projection?.projection_hash === targetHash)
      : null;
    if (historical) return historical;
    if (!targetHash || preview?.projection_hash === targetHash) return preview;
    return null;
  }, [detail?.projections, focusCitation, preview]);
  const citationValue = useMemo(
    () => resolveFieldPath(citationProjection?.content, focusCitation?.field_path),
    [citationProjection?.content, focusCitation?.field_path],
  );
  const citationProjectionMissing = Boolean(
    focusCitation?.projection_hash
    && detail
    && preview?.projection_hash
    && !citationProjection,
  );
  const citationProjectionHistorical = Boolean(
    citationProjection
    && focusCitation?.projection_hash
    && preview?.projection_hash
    && focusCitation.projection_hash !== preview.projection_hash,
  );

  useEffect(() => {
    if (!open || !caseId || !evidenceId) return;
    setError(null);
    setDetail(null);
    setPreview(null);
    setLoading(true);
    Promise.all([
      getCaseEvidence(caseId, evidenceId),
      previewCaseEvidence(caseId, evidenceId),
      typeof getEvidenceChainImpact === "function" ? getEvidenceChainImpact(caseId) : Promise.resolve(null),
    ]).then(([nextDetail, nextPreview, nextImpact]) => {
      setDetail(nextDetail);
      setPreview(nextPreview);
      setReviews(nextDetail?.reviews || []);
      setAnalyses(nextDetail?.analyses || []);
      setChainImpact(nextImpact);
    }).catch(setError).finally(() => setLoading(false));
  }, [caseId, evidenceId, open]);

  function defaultAssessment() {
    return {
      target_identity: "CONFIRMED",
      time_alignment: "FULL_WINDOW",
      data_integrity: String(item.completeness || "COMPLETE").toUpperCase() === "COMPLETE" ? "COMPLETE" : "TRUNCATED",
      source_reliability: item.source_type === "task_artifact" ? "NATIVE_COLLECTOR" : item.source_type === "source_gateway" ? "EXTERNAL_SYSTEM" : "MANUAL_UPLOAD",
      scope_fit: "CORRECT",
      corroboration: "NONE",
      freshness: String(item.freshness || "FRESH").toUpperCase() === "FRESH" ? "CURRENT_WINDOW" : "HISTORICAL",
    };
  }
  async function loadReviewImpact(nextDecision, values) {
    setLoading(true); setError(null); setReviewImpact(null);
    try {
      const impact = await previewCaseEvidenceReview(caseId, evidenceId, {
        decision: nextDecision,
        assessment: values.assessment || {},
      });
      setReviewImpact(impact);
    } catch (nextError) { setError(nextError); } finally { setLoading(false); }
  }
  function startReview(next) {
    const uiOnly = new Set(["HIDDEN", "VISIBLE", "ARCHIVED", "UNARCHIVED"]).has(next);
    const values = {
      assessment: uiOnly ? {} : defaultAssessment(),
      reason_code: next === "EXCLUDED" ? "USER_EXCLUDED" : next.includes("RESTORE") ? "RESTORED" : next === "LOW_TRUST" ? "QUALITY_CONCERN" : next === "HIDDEN" || next === "VISIBLE" ? "UI_ORGANIZATION" : next.includes("ARCHIVED") ? "ARCHIVE_MANAGEMENT" : "USER_VERIFIED",
      reason: "",
      override_reason: "",
    };
    setDecision(next); form.setFieldsValue(values); setReviewImpact(null); setReviewOpen(true);
    void loadReviewImpact(next, values);
  }
  async function submitReview() {
    const values = await form.validateFields(); setLoading(true); setError(null);
    try {
      if (!reviewImpact?.impact_token) {
        message.warning("请先重新预览影响");
        return;
      }
      await reviewCaseEvidence(caseId, evidenceId, {
        evidence_id: evidenceId,
        decision,
        ...values,
        expected_review_revision: reviewImpact.current_review_revision,
        impact_token: reviewImpact.impact_token,
      });
      message.success(decision === "EXCLUDED" ? "证据已从后续调查中排除" : "Evidence Trust 审查已提交");
      setReviewOpen(false); onChanged?.();
      const nextDetail = await getCaseEvidence(caseId, evidenceId);
      setDetail(nextDetail); setReviews(nextDetail?.reviews || []); setAnalyses(nextDetail?.analyses || []);
    } catch (nextError) { setError(nextError); } finally { setLoading(false); }
  }
  async function download(format) {
    setLoading(true);
    try { const { blob, filename } = await downloadCaseEvidence(caseId, evidenceId, format); const url=URL.createObjectURL(blob); const link=document.createElement("a"); link.href=url; link.download=filename; link.click(); URL.revokeObjectURL(url); } catch (nextError) { setError(nextError); } finally { setLoading(false); }
  }
  async function analyze() {
    setLoading(true); setError(null);
    try {
      const run = await createCaseEvidenceAnalysis(caseId, evidenceId);
      setAnalyses((items) => {
        const index = items.findIndex((item) => item.analysis_run_id === run.analysis_run_id);
        if (index < 0) return [...items, run];
        return items.map((item, itemIndex) => itemIndex === index ? run : item);
      });
      message.success(run.reused ? "已打开相同输入的分析记录" : "Evidence AI 分析已进入队列");
      onChanged?.();
    } catch (nextError) { setError(nextError); } finally { setLoading(false); }
  }
  async function submitAdjustment() {
    const values = await adjustmentForm.validateFields();
    setLoading(true);
    try {
      await adjustEvidenceChainConfidence(caseId, {
        chain_type: adjustment.chain_type,
        chain_id: adjustment.chain_id,
        confidence: values.confidence,
        expected_revision: adjustment.revision || 0,
        reason: values.reason,
      });
      message.success("链路置信度已记录，并保留人工调整审计");
      setAdjustment(null);
      const nextImpact = await getEvidenceChainImpact(caseId);
      setChainImpact(nextImpact);
    } catch (nextError) { setError(nextError); } finally { setLoading(false); }
  }
  return (
    <>
      <Drawer title={<Space><FileSearchOutlined />Evidence 详情</Space>} open={open} onClose={onClose} width={720} destroyOnHidden extra={<Tag color={trust.color}>{trust.label}</Tag>}>
        {!evidence ? <Empty description="请选择 Evidence" /> : <Space direction="vertical" size={16} style={{ width: "100%" }}>
          {String(trustValue).toUpperCase() === "EXCLUDED" && <Alert type="warning" showIcon message="已从后续调查中排除" description="该证据不会继续参与 Agent Prompt 和结论投影，但完整审查记录仍保留用于审计。" />}
          <ErrorAlert error={error} />
          {focusCitation && <section className={styles.citationFocus} data-testid="evidence-citation-focus" aria-label="当前事实引用">
            <div className={styles.citationHeader}>
              <strong>引用定位</strong>
              <Tag color={citationProjectionMissing ? "warning" : citationProjectionHistorical ? "gold" : "blue"}>{citationProjectionMissing ? "投影不可用" : citationProjectionHistorical ? "历史投影" : "引用投影"}</Tag>
            </div>
            <div className={styles.citationMeta}>
              <span><small>字段路径</small><Typography.Text code>{focusCitation.field_path || "未标注"}</Typography.Text></span>
              <span><small>Projection Hash</small><Tooltip title={focusCitation.projection_hash || "未标注"}><Typography.Text>{shortHash(focusCitation.projection_hash)}</Typography.Text></Tooltip></span>
              {(focusCitation.start != null || focusCitation.end != null) && <span><small>引用区间</small><Typography.Text>{focusCitation.start ?? "-"}–{focusCitation.end ?? "-"}</Typography.Text></span>}
            </div>
            {citationProjectionMissing ? <Alert type="warning" showIcon message="分析时固定的投影不可用" description="历史引用仍保留用于审计，当前字段值不会替代原分析输入。可下载证据包核对已归档投影。" /> : citationValue.found ? <div className={styles.citationValue}><small>引用字段值</small><pre>{stringify(citationValue.value)}</pre>{focusCitation.quote != null && <Typography.Text type="secondary">引用原文：“{String(focusCitation.quote)}”</Typography.Text>}</div> : <Alert type="warning" showIcon message="引用投影中无法定位该字段" description={citationProjection?.truncated ? "Evidence 投影已截断，可下载证据包核对完整投影。" : "字段可能已失效，或该 Evidence 未返回结构化投影。"} />}
          </section>}
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="Evidence ID" span={2}><Typography.Text copyable>{evidenceId}</Typography.Text></Descriptions.Item>
            <Descriptions.Item label="类型">{value(item, "evidence_type", "artifact_type", "type") || "—"}</Descriptions.Item>
            <Descriptions.Item label="采集器">{collectorLabel(value(item, "collector_id", "collector", "operation_id"))}</Descriptions.Item>
            <Descriptions.Item label="来源节点">{value(item, "source_node", "agent_id", "hostname", "target_ref") || "—"}</Descriptions.Item>
            <Descriptions.Item label="Agent ID">{value(item, "agent_id") || "—"}</Descriptions.Item>
            <Descriptions.Item label="Task ID"><Typography.Text copyable>{value(item, "task_id") || "—"}</Typography.Text></Descriptions.Item>
            <Descriptions.Item label="时间范围">{stringify(value(item, "time_range", "window") || "—")}</Descriptions.Item>
            <Descriptions.Item label="采集时间">{value(item, "collected_at", "created_at", "observed_at") ? formatBeijingDateTime(value(item, "collected_at", "created_at", "observed_at")) : "—"}</Descriptions.Item>
            <Descriptions.Item label="范围修订">r{value(item, "scope_revision") ?? "—"}</Descriptions.Item>
            <Descriptions.Item label="审查修订">r{value(item, "review_revision") ?? reviews.length}</Descriptions.Item>
            <Descriptions.Item label="生命周期">{item.lifecycle_status || "ACTIVE"}</Descriptions.Item>
            <Descriptions.Item label="人工信任">{labelOf(REVIEW_LABELS, item.review_trust_state || "UNREVIEWED", "待人工复核")} · {item.derived_trust_score ?? 50} 分</Descriptions.Item>
            <Descriptions.Item label="原始产物大小">{formatArtifactSize(value(item, "size_bytes", "artifact_size"))}</Descriptions.Item>
            <Descriptions.Item label="完整性">{value(item, "sha256", "content_hash", "integrity") ? <Tag color="success">已记录 Hash</Tag> : <Tag>未提供</Tag>}</Descriptions.Item>
            <Descriptions.Item label="是否被结论引用">{item.referenced_by_conclusion || item.claim_refs?.length ? <Tag color="purple">已引用</Tag> : <Tag>未引用</Tag>}</Descriptions.Item>
            <Descriptions.Item label="数据新鲜度">{value(item, "freshness", "freshness_status") || "由采集时间判断"}</Descriptions.Item>
          </Descriptions>
          <Space wrap>
            <Button icon={<CheckCircleOutlined />} onClick={() => startReview("TRUSTED")}>标记可信</Button>
            <Button icon={<WarningOutlined />} onClick={() => startReview("LOW_TRUST")}>标记低可信</Button>
            <Button danger icon={<CloseCircleOutlined />} onClick={() => startReview("EXCLUDED")}>排除</Button>
            {String(item.lifecycle_status || item.status).toUpperCase() === "EXCLUDED" && <><Button icon={<CheckCircleOutlined />} onClick={() => startReview("RESTORE_AS_TRUSTED")}>恢复为可信</Button><Button onClick={() => startReview("RESTORE_AS_LOW_TRUST")}>恢复为低可信</Button></>}
            <Button onClick={() => startReview(item.ui_hidden ? "VISIBLE" : "HIDDEN")}>{item.ui_hidden ? "取消隐藏" : "仅隐藏"}</Button>
            <Button onClick={() => startReview(item.ui_archived ? "UNARCHIVED" : "ARCHIVED")}>{item.ui_archived ? "取消归档" : "归档"}</Button>
            <Button icon={<RobotOutlined />} loading={loading} onClick={analyze}>AI 分析</Button>
            <Button icon={<DownloadOutlined />} loading={loading} onClick={() => download("raw")}>下载原始证据</Button>
            <Button icon={<DownloadOutlined />} loading={loading} onClick={() => download("bundle")}>下载证据包</Button>
            <Button onClick={() => onExplain?.(item)}>为什么被使用？</Button>
          </Space>
          <Divider orientation="left">数据摘要 / 原始投影</Divider>
          {raw ? <pre className={styles.rawProjection}>{stringify(raw)}</pre> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前 Evidence 投影未包含可预览原始数据；可通过 Artifact 下载查看。" />}
          <Divider orientation="left"><RobotOutlined /> AI 分析记录</Divider>
          <List size="small" bordered dataSource={analyses} locale={{ emptyText: "还没有 AI 分析记录" }} renderItem={(analysis) => <List.Item><List.Item.Meta title={<Space><Tag color={analysis.status === "COMPLETED" ? "green" : analysis.status === "FAILED" ? "red" : "processing"}>{ANALYSIS_STATUS_LABELS[String(analysis.status || "").toUpperCase()] || "处理中"}</Tag><span>{ANALYSIS_MODE_LABELS[String(analysis.mode || "").toUpperCase()] || "Evidence 分析"}</span>{analysis.input_state !== "CURRENT" && <Tag color="warning">{analysis.input_state === "STALE_INPUT" ? "输入已变更" : analysis.input_state === "EXCLUDED_INPUT" ? "输入已排除" : analysis.input_state}</Tag>}</Space>} description={(analysis.facts || []).map((fact) => fact.claim).filter(Boolean).join("；") || (analysis.limitations || []).join("；") || "分析结果尚未生成"} /><time>{analysis.created_at ? formatBeijingDateTime(analysis.created_at) : "—"}</time></List.Item>} />
          <Divider orientation="left"><HistoryOutlined /> 人工审查历史</Divider>
          <List size="small" bordered dataSource={reviews} locale={{ emptyText: "还没有人工审查记录" }} renderItem={(review) => <List.Item><List.Item.Meta title={<Space><Tag color={evidenceTrust(review.decision).color}>{labelOf(REVIEW_LABELS, review.decision, "状态未标注")}</Tag><span>审查修订 r{review.review_revision || "—"}</span></Space>} description={`${review.reason || "未填写审查说明"}`} /><time>{review.created_at ? formatBeijingDateTime(review.created_at) : "—"}</time></List.Item>} />
          <Divider orientation="left">证据链影响与可解释置信度</Divider>
          <List size="small" bordered dataSource={(chainImpact?.chains || []).filter((chain) => (chain.ledger || []).some((item) => item.evidence_id === evidenceId))} locale={{ emptyText: "当前证据尚未进入可解释链路" }} renderItem={(chain) => <List.Item actions={[<Button key="adjust" size="small" onClick={() => { adjustmentForm.resetFields(); setAdjustment(chain); }}>提高置信度</Button>]}><List.Item.Meta title={<Space><Tag color={chain.status === "ACTIVE" ? "green" : chain.status === "INVALIDATED" ? "red" : "gold"}>{chain.status}</Tag><Typography.Text code>{chain.chain_type}:{chain.chain_id}</Typography.Text><span>{Number(chain.computed_confidence || 0).toFixed(2)} → {Number(chain.effective_confidence || 0).toFixed(2)}</span></Space>} description={<Space direction="vertical" size={0}><span>{chain.confidence_reason}</span><span>失效：{(chain.invalidated_evidence_refs || []).join(", ") || "无"}；剩余支持：{(chain.remaining_active_support || []).join(", ") || "无"}</span><Typography.Text type="secondary">模型 {chain.calculation_version} · Revision {chain.revision || 0}</Typography.Text></Space>} /></List.Item>} />
        </Space>}
      </Drawer>
      <Modal title={`人在环证据治理：${evidenceId || "—"}`} open={reviewOpen} onCancel={() => setReviewOpen(false)} onOk={submitReview} okText="确认审查" confirmLoading={loading} okButtonProps={{ danger: decision === "EXCLUDED", disabled: !reviewImpact?.impact_token }} width={760}>
        <Alert type={decision === "EXCLUDED" ? "warning" : "info"} showIcon message={decision === "EXCLUDED" ? "排除会停止证据进入后续推理，并可能冻结恢复方案" : "人工只治理证据准入和可信状态，不能直接修改根因置信度"} style={{ marginBottom: 14 }} />
        <Form form={form} layout="vertical" onValuesChange={() => setReviewImpact(null)}>
          {!new Set(["HIDDEN", "VISIBLE", "ARCHIVED", "UNARCHIVED"]).has(decision) && <>
            <Divider orientation="left">结构化审查</Divider>
            <Form.Item name={["assessment", "target_identity"]} label="目标身份" rules={[{ required: true }]}><Select options={[{ value: "CONFIRMED", label: "已确认" }, { value: "POSSIBLE_PID_REUSE", label: "可能 PID 复用" }, { value: "INSTANCE_MISMATCH", label: "实例不匹配" }]} /></Form.Item>
            <Form.Item name={["assessment", "time_alignment"]} label="时间对齐" rules={[{ required: true }]}><Select options={[{ value: "FULL_WINDOW", label: "完全覆盖故障窗口" }, { value: "PARTIAL_WINDOW", label: "部分覆盖" }, { value: "MISMATCH", label: "时间不匹配" }]} /></Form.Item>
            <Form.Item name={["assessment", "data_integrity"]} label="数据完整性" rules={[{ required: true }]}><Select options={[{ value: "COMPLETE", label: "完整" }, { value: "TRUNCATED", label: "截断" }, { value: "FAILED", label: "采集失败" }]} /></Form.Item>
            <Form.Item name={["assessment", "source_reliability"]} label="来源可靠性" rules={[{ required: true }]}><Select options={[{ value: "NATIVE_COLLECTOR", label: "原生采集" }, { value: "EXTERNAL_SYSTEM", label: "外部系统" }, { value: "MANUAL_UPLOAD", label: "人工上传" }]} /></Form.Item>
            <Form.Item name={["assessment", "scope_fit"]} label="范围适配" rules={[{ required: true }]}><Select options={[{ value: "CORRECT", label: "正确服务和节点" }, { value: "PARTIAL", label: "部分匹配" }, { value: "WRONG_SCOPE", label: "错误范围" }]} /></Form.Item>
            <Form.Item name={["assessment", "corroboration"]} label="交叉佐证" rules={[{ required: true }]}><Select options={[{ value: "INDEPENDENT_SUPPORT", label: "独立证据支持" }, { value: "NONE", label: "无佐证" }, { value: "CONFLICT", label: "存在冲突" }]} /></Form.Item>
            <Form.Item name={["assessment", "freshness"]} label="新鲜度" rules={[{ required: true }]}><Select options={[{ value: "CURRENT_WINDOW", label: "当前故障窗口" }, { value: "HISTORICAL", label: "历史数据" }, { value: "EXPIRED", label: "已过期" }]} /></Form.Item>
          </>}
          <Form.Item name="reason_code" label="原因代码" rules={[{ required: true, message: "请选择原因代码" }]}><Select options={[{ value: "USER_VERIFIED", label: "USER_VERIFIED — 人工核验" },{ value: "QUALITY_CONCERN", label: "QUALITY_CONCERN — 质量存疑" },{ value: "STALE_DATA", label: "STALE_DATA — 数据过期" },{ value: "SCOPE_MISMATCH", label: "SCOPE_MISMATCH — 范围不匹配" },{ value: "USER_EXCLUDED", label: "USER_EXCLUDED — 人工排除" },{ value: "RESTORED", label: "RESTORED — 恢复参与调查" },{ value: "UI_ORGANIZATION", label: "UI_ORGANIZATION — 仅整理界面" },{ value: "ARCHIVE_MANAGEMENT", label: "ARCHIVE_MANAGEMENT — 归档管理" }]} /></Form.Item>
          <Form.Item name="reason" label="审查说明" rules={[{ required: true, min: 3, message: "请填写至少 3 个字符的审查说明" }]}><Input.TextArea rows={3} maxLength={1000} showCount /></Form.Item>
          {reviewImpact?.assessment_result?.recommended_decision && reviewImpact.assessment_result.recommended_decision !== decision.replace("RESTORE_AS_", "") && <Form.Item name="override_reason" label="覆盖系统建议的原因" rules={[{ required: true, min: 3 }]}><Input.TextArea rows={2} maxLength={1000} /></Form.Item>}
          <Button loading={loading} onClick={() => form.validateFields().then((values) => loadReviewImpact(decision, values))}>预览影响</Button>
        </Form>
        {reviewImpact && <Alert style={{ marginTop: 14 }} type={reviewImpact.requires_approval ? "warning" : "success"} showIcon message={<Space wrap><span>建议：{reviewImpact.assessment_result?.recommended_decision || "保持当前"} · 治理分 {reviewImpact.assessment_result?.derived_trust_score ?? 50}</span><Tag color={REOPEN_POLICY_META[reviewImpact.reopen_policy]?.color || "default"}>{REOPEN_POLICY_META[reviewImpact.reopen_policy]?.label || "影响待评估"}</Tag></Space>} description={<div><div>{(reviewImpact.assessment_result?.reasons || []).join("；") || "没有发现结构化质量警告"}</div><div>影响：分析 {reviewImpact.affected?.analysis_runs || 0}，假设 {reviewImpact.affected?.hypotheses || 0}，结论 {reviewImpact.affected?.conclusions || 0}，恢复方案 {reviewImpact.affected?.recovery_plans || 0}</div><div>预测结论状态：{reviewImpact.predicted_conclusion_state || "不变"}{reviewImpact.requires_approval ? "；本次操作需要审批角色" : ""}</div>{reviewImpact.blocked_reason && <div>{reviewImpact.blocked_reason}</div>}</div>} />}
      </Modal>
      <Modal title={`提高链路置信度：${adjustment?.chain_id || ""}`} open={Boolean(adjustment)} onCancel={() => setAdjustment(null)} onOk={submitAdjustment} confirmLoading={loading} okText="提交调整">
        <Alert type="info" showIcon message={`自动值 ${Number(adjustment?.computed_confidence || 0).toFixed(2)}，上限 ${Number(adjustment?.confidence_cap || 0).toFixed(2)}`} description="调整只会记录人工意图；被排除证据不能恢复为支持，低可信证据仍受上限约束。" style={{ marginBottom: 14 }} />
        <Form form={adjustmentForm} layout="vertical">
          <Form.Item name="confidence" label="目标置信度" rules={[{ required: true, type: "number", min: Number(adjustment?.effective_confidence || 0), max: Number(adjustment?.confidence_cap || 1) }]}><InputNumber min={0} max={1} step={0.01} style={{ width: "100%" }} /></Form.Item>
          <Form.Item name="reason" label="调整理由" rules={[{ required: true, min: 3 }]}><Input.TextArea rows={3} maxLength={1000} showCount /></Form.Item>
        </Form>
      </Modal>
    </>
  );
}
