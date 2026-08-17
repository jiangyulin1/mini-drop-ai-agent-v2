import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Descriptions, Divider, Drawer, Empty, Form, Input, List, Modal, Select, Space, Tag, Typography, message } from "antd";
import { CheckCircleOutlined, CloseCircleOutlined, DownloadOutlined, FileSearchOutlined, HistoryOutlined, WarningOutlined } from "@ant-design/icons";
import { downloadTaskArtifact, listCaseEvidenceReviews, reviewCaseEvidence } from "../api/client";
import { evidenceArtifactTarget, formatArtifactSize } from "../utils/evidence";
import { evidenceTrust } from "../utils/opsMappings";
import ErrorAlert from "./ErrorAlert";

function value(item, ...keys) { for (const key of keys) if (item?.[key] !== undefined && item?.[key] !== null && item?.[key] !== "") return item[key]; return null; }
function stringify(input) { return typeof input === "string" ? input : JSON.stringify(input, null, 2); }

export default function EvidenceDrawer({ open, onClose, caseId, evidence, onChanged, onExplain }) {
  const [form] = Form.useForm();
  const [reviewOpen, setReviewOpen] = useState(false);
  const [decision, setDecision] = useState("TRUSTED");
  const [reviews, setReviews] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const item = evidence || {};
  const evidenceId = item.evidence_id || item.id;
  const trustValue = item.trust_status || item.review_decision || item.status || "UNKNOWN";
  const trust = evidenceTrust(trustValue);
  const artifactTarget = evidenceArtifactTarget(item);
  const raw = useMemo(() => value(item, "raw_data", "content", "data", "summary", "projections"), [item]);

  useEffect(() => {
    if (!open || !caseId || !evidenceId) return;
    setError(null);
    listCaseEvidenceReviews(caseId, { evidence_id: evidenceId }).then((response) => setReviews(response?.items || [])).catch(setError);
  }, [caseId, evidenceId, open]);

  function startReview(next) { setDecision(next); form.setFieldsValue({ reason_code: next === "EXCLUDED" ? "USER_EXCLUDED" : next === "LOW_TRUST" ? "QUALITY_CONCERN" : "USER_VERIFIED", reason: "" }); setReviewOpen(true); }
  async function submitReview() {
    const values = await form.validateFields(); setLoading(true); setError(null);
    try {
      await reviewCaseEvidence(caseId, evidenceId, { evidence_id: evidenceId, decision, ...values });
      message.success(decision === "EXCLUDED" ? "证据已从后续调查中排除" : "Evidence Trust 审查已提交");
      setReviewOpen(false); onChanged?.();
      const response = await listCaseEvidenceReviews(caseId, { evidence_id: evidenceId }); setReviews(response?.items || []);
    } catch (nextError) { setError(nextError); } finally { setLoading(false); }
  }
  async function download() {
    if (!artifactTarget) return;
    setLoading(true);
    try { const { blob, filename } = await downloadTaskArtifact(artifactTarget.taskId, artifactTarget.artifactType || item.artifact_type); const url=URL.createObjectURL(blob); const link=document.createElement("a"); link.href=url; link.download=filename; link.click(); URL.revokeObjectURL(url); } catch (nextError) { setError(nextError); } finally { setLoading(false); }
  }
  return (
    <>
      <Drawer title={<Space><FileSearchOutlined />Evidence 详情</Space>} open={open} onClose={onClose} width={720} destroyOnHidden extra={<Tag color={trust.color}>{trust.label}</Tag>}>
        {!evidence ? <Empty description="请选择 Evidence" /> : <Space direction="vertical" size={16} style={{ width: "100%" }}>
          {String(trustValue).toUpperCase() === "EXCLUDED" && <Alert type="warning" showIcon message="已从后续调查中排除" description="该证据不会继续参与 Agent Prompt 和结论投影，但完整审查记录仍保留用于审计。" />}
          <ErrorAlert error={error} />
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="Evidence ID" span={2}><Typography.Text copyable>{evidenceId}</Typography.Text></Descriptions.Item>
            <Descriptions.Item label="类型">{value(item, "evidence_type", "artifact_type", "type") || "—"}</Descriptions.Item>
            <Descriptions.Item label="Collector">{value(item, "collector_id", "collector", "operation_id") || "—"}</Descriptions.Item>
            <Descriptions.Item label="来源节点">{value(item, "source_node", "agent_id", "hostname", "target_ref") || "—"}</Descriptions.Item>
            <Descriptions.Item label="Agent ID">{value(item, "agent_id") || "—"}</Descriptions.Item>
            <Descriptions.Item label="Task ID"><Typography.Text copyable>{value(item, "task_id") || artifactTarget?.taskId || "—"}</Typography.Text></Descriptions.Item>
            <Descriptions.Item label="时间范围">{stringify(value(item, "time_range", "window") || "—")}</Descriptions.Item>
            <Descriptions.Item label="采集时间">{value(item, "collected_at", "created_at", "observed_at") ? new Date(value(item, "collected_at", "created_at", "observed_at")).toLocaleString() : "—"}</Descriptions.Item>
            <Descriptions.Item label="Scope Revision">r{value(item, "scope_revision") ?? "—"}</Descriptions.Item>
            <Descriptions.Item label="Review Revision">r{value(item, "review_revision") ?? reviews.length}</Descriptions.Item>
            <Descriptions.Item label="Artifact 大小">{formatArtifactSize(value(item, "size_bytes", "artifact_size"))}</Descriptions.Item>
            <Descriptions.Item label="完整性">{value(item, "sha256", "content_hash", "integrity") ? <Tag color="success">已记录 Hash</Tag> : <Tag>未提供</Tag>}</Descriptions.Item>
            <Descriptions.Item label="是否被结论引用">{item.referenced_by_conclusion || item.claim_refs?.length ? <Tag color="purple">已引用</Tag> : <Tag>未引用</Tag>}</Descriptions.Item>
            <Descriptions.Item label="数据新鲜度">{value(item, "freshness", "freshness_status") || "由采集时间判断"}</Descriptions.Item>
          </Descriptions>
          <Space wrap>
            <Button icon={<CheckCircleOutlined />} onClick={() => startReview("TRUSTED")}>标记可信</Button>
            <Button icon={<WarningOutlined />} onClick={() => startReview("LOW_TRUST")}>标记低可信</Button>
            <Button danger icon={<CloseCircleOutlined />} onClick={() => startReview("EXCLUDED")}>排除</Button>
            <Button disabled={!artifactTarget} icon={<DownloadOutlined />} loading={loading} onClick={download}>下载 Artifact</Button>
            <Button onClick={() => onExplain?.(item)}>为什么被使用？</Button>
          </Space>
          <Divider orientation="left">数据摘要 / 原始投影</Divider>
          {raw ? <pre style={{ maxHeight: 340, overflow: "auto", padding: 12, borderRadius: 8, color: "#d1d5db", background: "#111827", whiteSpace: "pre-wrap" }}>{stringify(raw)}</pre> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前 Evidence 投影未包含可预览原始数据；可通过 Artifact 下载查看。" />}
          <Divider orientation="left"><HistoryOutlined /> Review 历史</Divider>
          <List size="small" bordered dataSource={reviews} locale={{ emptyText: "还没有人工审查记录" }} renderItem={(review) => <List.Item><List.Item.Meta title={<Space><Tag color={evidenceTrust(review.decision).color}>{review.decision}</Tag><span>Revision {review.review_revision || "—"}</span></Space>} description={`${review.reason_code || "NO_REASON_CODE"} · ${review.reason || "未填写说明"}`} /><time>{review.created_at ? new Date(review.created_at).toLocaleString() : "—"}</time></List.Item>} />
        </Space>}
      </Drawer>
      <Modal title={`审查 Evidence：${evidenceId || "—"}`} open={reviewOpen} onCancel={() => setReviewOpen(false)} onOk={submitReview} okText="等待服务端确认并提交" confirmLoading={loading} okButtonProps={{ danger: decision === "EXCLUDED" }}>
        <Alert type={decision === "EXCLUDED" ? "warning" : "info"} showIcon message={decision === "EXCLUDED" ? "排除后将停止参与后续 Agent Prompt 与结论投影" : "关键 Trust 更新不会进行乐观更新"} style={{ marginBottom: 14 }} />
        <Form form={form} layout="vertical"><Form.Item name="reason_code" label="Reason Code" rules={[{ required: true, message: "请选择原因代码" }]}><Select options={[{ value: "USER_VERIFIED", label: "USER_VERIFIED — 人工核验" },{ value: "QUALITY_CONCERN", label: "QUALITY_CONCERN — 质量存疑" },{ value: "STALE_DATA", label: "STALE_DATA — 数据过期" },{ value: "SCOPE_MISMATCH", label: "SCOPE_MISMATCH — 范围不匹配" },{ value: "USER_EXCLUDED", label: "USER_EXCLUDED — 人工排除" },{ value: "RESTORED", label: "RESTORED — 恢复参与调查" }]} /></Form.Item><Form.Item name="reason" label="审查说明" rules={[{ required: true, min: 3, message: "请填写至少 3 个字符的审查说明" }]}><Input.TextArea rows={4} maxLength={1000} showCount /></Form.Item></Form>
      </Modal>
    </>
  );
}
