import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Col,
  Empty,
  Row,
  Skeleton,
  Space,
  Tag,
  Typography,
} from "antd";
import { EyeOutlined } from "@ant-design/icons";
import { Link } from "react-router-dom";
import {
  getTask,
  getTaskArtifactContent,
  getTaskArtifacts,
} from "../api/client";
import { collectorMeta } from "../utils/collectors";
import {
  artifactText,
  isArtifactAvailable,
  prepareAsyncProfilerHtml,
  unavailableVisualArtifacts,
} from "../utils/artifacts";
import EBPFHistogram from "./EBPFHistogram";
import FlamegraphViewer from "./FlamegraphViewer";
import SandboxedArtifactFrame from "./SandboxedArtifactFrame";
import TopNChart from "./TopNChart";

function artifactIndex(artifact) {
  return artifact?.metadata?.window_index ?? null;
}

export default function TaskVisualizationPreview({ taskId }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [task, setTask] = useState(null);
  const [artifacts, setArtifacts] = useState([]);
  const [top, setTop] = useState([]);
  const [embeddedDocument, setEmbeddedDocument] = useState("");
  const [ebpfData, setEbpfData] = useState(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      setLoading(true);
      setError("");
      setTask(null);
      setArtifacts([]);
      setTop([]);
      setEmbeddedDocument("");
      setEbpfData(null);
      try {
        const [taskData, artifactItems] = await Promise.all([
          getTask(taskId),
          getTaskArtifacts(taskId),
        ]);
        if (cancelled) return;
        setTask(taskData);
        setArtifacts(artifactItems || []);

        const availableItems = (artifactItems || []).filter(isArtifactAvailable);
        const types = new Set(availableItems.map((item) => item.artifact_type));
        const contentJobs = [];
        if (types.has("top_json")) {
          contentJobs.push(
            getTaskArtifactContent(taskId, "top_json")
              .then((value) => { if (!cancelled) setTop(Array.isArray(value) ? value : []); })
              .catch(() => { if (!cancelled) setTop([]); }),
          );
        }
        const documentType = types.has("java_flamegraph_html")
          ? "java_flamegraph_html"
          : types.has("flamegraph_svg")
          ? "flamegraph_svg"
          : null;
        if (documentType) {
          contentJobs.push(
            getTaskArtifactContent(taskId, documentType)
              .then((value) => {
                if (!cancelled) {
                  setEmbeddedDocument(
                    documentType === "java_flamegraph_html"
                      ? prepareAsyncProfilerHtml(value)
                      : artifactText(value),
                  );
                }
              })
              .catch(() => { if (!cancelled) setEmbeddedDocument(""); }),
          );
        }
        if (types.has("ebpf_metrics")) {
          contentJobs.push(
            getTaskArtifactContent(taskId, "ebpf_metrics")
              .then((value) => { if (!cancelled) setEbpfData(value); })
              .catch(() => { if (!cancelled) setEbpfData(null); }),
          );
        }
        await Promise.all(contentJobs);
      } catch (err) {
        if (!cancelled) setError(err.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    load();
    return () => {
      cancelled = true;
    };
  }, [taskId]);

  if (loading) {
    return <Skeleton active paragraph={{ rows: 6 }} />;
  }
  if (error) {
    return <Alert type="warning" showIcon message="无法加载任务可视化" description={error} />;
  }

  const meta = collectorMeta(task?.collector_type);
  const flameArtifact = artifacts.find(
    (item) => item.artifact_type === "flamegraph_json" && isArtifactAvailable(item),
  );
  const continuousArtifact = artifacts.find(
    (item) => item.artifact_type === "continuous_flamegraph_json" && isArtifactAvailable(item),
  );
  const unavailableVisuals = unavailableVisualArtifacts(artifacts);
  const hasInlineVisualization = Boolean(
    flameArtifact || continuousArtifact || embeddedDocument || top.length || ebpfData,
  );

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Space wrap style={{ width: "100%", justifyContent: "space-between" }}>
        <Space wrap>
          <Tag color={meta.color}>{meta.label}</Tag>
          <Typography.Text>PID {task?.target_pid}</Typography.Text>
          <Typography.Text type="secondary">{meta.resultLabel}</Typography.Text>
        </Space>
        <Link to={`/task/${taskId}`}>
          <Button size="small" type="primary" icon={<EyeOutlined />}>
            打开完整结果
          </Button>
        </Link>
      </Space>

      {(flameArtifact || continuousArtifact || embeddedDocument || top.length > 0) && (
        <Row gutter={[16, 16]}>
          <Col xs={24} xl={top.length > 0 ? 16 : 24}>
            {flameArtifact && (
              <FlamegraphViewer taskId={taskId} height={360} />
            )}
            {!flameArtifact && continuousArtifact && (
              <FlamegraphViewer
                taskId={taskId}
                artifactType="continuous_flamegraph_json"
                artifactIndex={artifactIndex(continuousArtifact)}
                height={360}
              />
            )}
            {!flameArtifact && !continuousArtifact && embeddedDocument && (
              <SandboxedArtifactFrame
                html={embeddedDocument}
                title={`${meta.label}预览`}
                style={{
                  width: "100%",
                  height: 360,
                  border: "1px solid #f0f0f0",
                  borderRadius: 6,
                  background: "#fff",
                }}
              />
            )}
          </Col>
          {top.length > 0 && (
            <Col xs={24} xl={8}>
              <TopNChart data={top.slice(0, 10)} height={360} />
            </Col>
          )}
        </Row>
      )}

      {ebpfData && <EBPFHistogram data={ebpfData} height={320} />}

      {!hasInlineVisualization && unavailableVisuals.length > 0 && (
        <Alert
          type="warning"
          showIcon
          message="可视化产物文件不可用"
          description={unavailableVisuals
            .map((item) => `${item.artifact_type}：${item.availability_reason || "文件不存在"}`)
            .join("；")}
        />
      )}

      {!hasInlineVisualization && unavailableVisuals.length === 0 && (
        <Empty
          description={
            task?.status === "DONE"
              ? `该任务已完成，预期结果为“${meta.resultLabel}”；请打开完整结果查看产物和状态原因`
              : `任务状态为 ${task?.status || "UNKNOWN"}，完成后将在此显示可视化`
          }
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        />
      )}

      {artifacts.length > 0 && (
        <Space wrap>
          <Typography.Text type="secondary">产物：</Typography.Text>
          {artifacts.map((item, index) => (
            <Tag key={`${item.artifact_type}-${index}`}>{item.artifact_type}</Tag>
          ))}
        </Space>
      )}
    </Space>
  );
}
