import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Descriptions,
  Drawer,
  Empty,
  List,
  message,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import {
  DownloadOutlined,
  EyeOutlined,
  FileTextOutlined,
  LinkOutlined,
} from "@ant-design/icons";
import { Link } from "react-router-dom";
import {
  downloadDiagnosisEvidence,
  downloadDiagnosisEvidenceBundle,
  downloadTaskArtifact,
  getTaskArtifacts,
} from "../api/client";
import {
  evidenceArtifactTarget,
  formatArtifactSize,
} from "../utils/evidence";
import styles from "./EvidenceReference.module.css";

function formatTimeRange(value) {
  if (!value?.start && !value?.end) return "未指定";
  return `${value.start || "?"} → ${value.end || "?"}`;
}

function JsonValue({ value, empty = "暂无结构化数据" }) {
  if (value === undefined || value === null) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={empty} />;
  return <pre className={styles.jsonBlock}>{JSON.stringify(value, null, 2)}</pre>;
}

function triggerBlobDownload(blob, filename) {
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}

export function EvidenceDetailDrawer({
  evidence,
  open,
  onClose,
  relations = [],
}) {
  const target = useMemo(() => evidenceArtifactTarget(evidence), [evidence]);
  const [artifacts, setArtifacts] = useState([]);
  const [loadingArtifacts, setLoadingArtifacts] = useState(false);
  const [artifactError, setArtifactError] = useState("");
  const [downloading, setDownloading] = useState("");
  const [downloadingEvidence, setDownloadingEvidence] = useState(false);
  const [downloadingBundle, setDownloadingBundle] = useState(false);
  const linkedArtifacts = useMemo(
    () => (Array.isArray(evidence?.artifact_links) ? evidence.artifact_links : []),
    [evidence],
  );

  useEffect(() => {
    let cancelled = false;
    if (!open || !target?.taskId) {
      setArtifacts([]);
      setArtifactError("");
      return undefined;
    }

    setLoadingArtifacts(true);
    setArtifactError("");
    getTaskArtifacts(target.taskId, { verify: true })
      .then((items) => {
        if (cancelled) return;
        const fetched = Array.isArray(items) ? items : [];
        if (linkedArtifacts.length === 0) {
          setArtifacts(fetched);
          return;
        }
        const linkedIds = new Set(linkedArtifacts.map((item) => item.artifact_id));
        setArtifacts(fetched.filter((item) => linkedIds.has(item.artifact_id)));
      })
      .catch((error) => {
        if (!cancelled) setArtifactError(error.message || "关联文件加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoadingArtifacts(false);
      });
    return () => { cancelled = true; };
  }, [open, target?.taskId, linkedArtifacts]);

  async function download(record) {
    const index = record.metadata?.window_index;
    const key = `${record.artifact_type}:${index ?? ""}`;
    setDownloading(key);
    try {
      const params = index === undefined ? {} : { index };
      const result = await downloadTaskArtifact(target.taskId, record.artifact_type, params);
      triggerBlobDownload(result.blob, result.filename);
    } catch (error) {
      message.error(`下载失败：${error.message}`);
    } finally {
      setDownloading("");
    }
  }

  async function downloadStructuredEvidence() {
    if (!evidence?.diagnosis_id || !evidence?.evidence_id) return;
    setDownloadingEvidence(true);
    try {
      const result = await downloadDiagnosisEvidence(
        evidence.diagnosis_id,
        evidence.evidence_id,
      );
      triggerBlobDownload(result.blob, result.filename);
    } catch (error) {
      message.error(`证据 JSON 下载失败：${error.message}`);
    } finally {
      setDownloadingEvidence(false);
    }
  }

  async function downloadEvidenceBundle() {
    if (!evidence?.diagnosis_id || !evidence?.evidence_id) return;
    setDownloadingBundle(true);
    try {
      const result = await downloadDiagnosisEvidenceBundle(
        evidence.diagnosis_id,
        evidence.evidence_id,
      );
      triggerBlobDownload(result.blob, result.filename);
    } catch (error) {
      message.error(`完整证据包下载失败：${error.message}`);
    } finally {
      setDownloadingBundle(false);
    }
  }

  const orderedArtifacts = useMemo(() => {
    if (!target?.artifactType) return artifacts;
    return [...artifacts].sort((left, right) => (
      Number(right.artifact_type === target.artifactType)
      - Number(left.artifact_type === target.artifactType)
    ));
  }, [artifacts, target?.artifactType]);

  return (
    <Drawer
      width={760}
      open={open}
      title={evidence ? `证据详情 · ${evidence.evidence_id}` : "证据详情"}
      onClose={onClose}
      extra={evidence?.evidence_id && (
        <Space>
          <Button
            type="primary"
            icon={<DownloadOutlined />}
            loading={downloadingBundle}
            disabled={!evidence.diagnosis_id}
            onClick={downloadEvidenceBundle}
          >
            下载完整证据包
          </Button>
          <Button
            icon={<DownloadOutlined />}
            loading={downloadingEvidence}
            disabled={!evidence.diagnosis_id}
            onClick={downloadStructuredEvidence}
          >
            下载证据 JSON
          </Button>
          <Typography.Text copyable={{ text: evidence.evidence_id }}>复制编号</Typography.Text>
        </Space>
      )}
    >
      {evidence && (
        <Space direction="vertical" size={16} style={{ width: "100%" }}>
          <Alert
            type="info"
            showIcon
            message="这是诊断结论实际引用的结构化证据"
            description="可在此核对来源、目标、时间范围与实际观测；存在关联采集文件时，可继续下载原始材料。"
          />

          <Descriptions bordered size="small" column={1}>
            <Descriptions.Item label="证据编号"><Typography.Text copyable>{evidence.evidence_id}</Typography.Text></Descriptions.Item>
            <Descriptions.Item label="来源">{evidence.source_system || "-"} / {evidence.source_type || "-"}</Descriptions.Item>
            <Descriptions.Item label="证据角色"><Tag>{evidence.evidence_role || "incident"}</Tag></Descriptions.Item>
            <Descriptions.Item label="采集目标"><JsonValue value={evidence.target || {}} /></Descriptions.Item>
            <Descriptions.Item label="事件时间窗">{formatTimeRange(evidence.event_time_range)}</Descriptions.Item>
            <Descriptions.Item label="探针或查询">{evidence.query_or_probe || "-"}</Descriptions.Item>
            <Descriptions.Item label="数据质量"><JsonValue value={evidence.data_quality || {}} /></Descriptions.Item>
            <Descriptions.Item label="派生版本">{evidence.derivation_version || "-"}</Descriptions.Item>
            <Descriptions.Item label="完整性 Hash"><Typography.Text copyable>{evidence.integrity_hash || "-"}</Typography.Text></Descriptions.Item>
          </Descriptions>

          {relations.length > 0 && (
            <Space wrap>
              <Typography.Text strong>在证据链中的作用：</Typography.Text>
              {relations.map((relation, index) => (
                <Tag
                  key={`${relation.target || "claim"}-${relation.relation || index}`}
                  color={relation.relation === "SUPPORTS" ? "green" : "red"}
                >
                  {relation.relation === "SUPPORTS" ? "支持" : "反驳"} · {relation.target || "相关假设"}
                </Tag>
              ))}
            </Space>
          )}

          <div>
            <Typography.Title level={5}>实际观测</Typography.Title>
            <JsonValue value={evidence.observed_value} />
          </div>

          {(evidence.baseline_value && Object.keys(evidence.baseline_value).length > 0) && (
            <div>
              <Typography.Title level={5}>基线对照</Typography.Title>
              <JsonValue value={evidence.baseline_value} />
            </div>
          )}

          {(evidence.anomaly_score && Object.keys(evidence.anomaly_score).length > 0) && (
            <div>
              <Typography.Title level={5}>异常评分</Typography.Title>
              <JsonValue value={evidence.anomaly_score} />
            </div>
          )}

          <div>
            <Space style={{ marginBottom: 8 }}>
              <Typography.Title level={5} style={{ margin: 0 }}>关联文件</Typography.Title>
              {target?.taskId && (
                <Link to={`/task/${target.taskId}`}>
                  <Button type="link" size="small" icon={<LinkOutlined />}>查看采集任务</Button>
                </Link>
              )}
            </Space>

            {!target?.taskId && (
              <Alert
                type="warning"
                showIcon
                message="仅结构化证据"
                description="该证据没有关联采集任务或文件，但上方实际观测仍可直接核验。"
              />
            )}
            {target?.taskId && loadingArtifacts && <Spin size="small" />}
            {artifactError && <Alert type="error" showIcon message={artifactError} />}
            {target?.taskId && !loadingArtifacts && !artifactError && orderedArtifacts.length === 0 && (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="关联任务没有可下载文件" />
            )}
            {orderedArtifacts.length > 0 && (
              <List
                bordered
                size="small"
                dataSource={orderedArtifacts}
                renderItem={(item) => {
                  const index = item.metadata?.window_index;
                  const key = `${item.artifact_type}:${index ?? ""}`;
                  const filename = item.filename || item.object_key || item.local_path || `${item.artifact_type}.bin`;
                  const downloadable = item.availability === "available";
                  const availability = {
                    available: { color: "green", label: "文件可用" },
                    missing: { color: "red", label: item.retention_state === "expired" ? "已过保留期 / 文件缺失" : "文件缺失" },
                    unavailable: { color: "orange", label: "存储暂不可达" },
                    unknown: { color: "default", label: "尚未检查" },
                  }[item.availability || "unknown"];
                  return (
                    <List.Item
                      actions={[
                        <Button
                          key="download"
                          type="primary"
                          ghost
                          size="small"
                          icon={<DownloadOutlined />}
                          loading={downloading === key}
                          disabled={!downloadable}
                          onClick={() => download(item)}
                        >
                          {downloadable ? "下载" : "不可下载"}
                        </Button>,
                      ]}
                    >
                      <List.Item.Meta
                        avatar={<FileTextOutlined />}
                        title={(
                          <Button
                            type="link"
                            className={styles.fileNameButton}
                            loading={downloading === key}
                            disabled={!downloadable}
                            onClick={() => download(item)}
                          >
                            {filename}
                          </Button>
                        )}
                        description={(
                          <Space wrap>
                            <Tag color={item.artifact_type === target.artifactType ? "blue" : "default"}>
                              {item.artifact_type}
                            </Tag>
                            <Tag color={availability.color}>{availability.label}</Tag>
                            {item.integrity_status === "mismatch" && <Tag color="red">完整性异常</Tag>}
                            <Typography.Text type="secondary">
                              {formatArtifactSize(item.actual_size_bytes ?? item.size_bytes)}
                            </Typography.Text>
                          </Space>
                        )}
                      />
                    </List.Item>
                  );
                }}
              />
            )}
          </div>
        </Space>
      )}
    </Drawer>
  );
}

export default function EvidenceReference({
  evidence,
  evidenceId,
  label,
  color,
}) {
  const [open, setOpen] = useState(false);
  const id = evidence?.evidence_id || evidenceId || "未知证据";

  if (!evidence) {
    return <Tag color="red" title="当前诊断快照中找不到该证据">{label || id} · 缺失</Tag>;
  }

  return (
    <>
      <Button
        type="link"
        size="small"
        icon={<EyeOutlined />}
        className={styles.referenceButton}
        style={color ? { color } : undefined}
        onClick={() => setOpen(true)}
      >
        {label || id}
      </Button>
      <EvidenceDetailDrawer evidence={evidence} open={open} onClose={() => setOpen(false)} />
    </>
  );
}
