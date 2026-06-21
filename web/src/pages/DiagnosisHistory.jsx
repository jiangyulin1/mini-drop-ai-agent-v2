import { useEffect, useState, useMemo, useCallback } from "react";
import {
  Button,
  Card,
  Col,
  Empty,
  Input,
  Progress,
  Row,
  Select,
  Skeleton,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import {
  ExperimentOutlined,
  ReloadOutlined,
  SearchOutlined,
  FilterOutlined,
} from "@ant-design/icons";
import { Link } from "react-router-dom";
import { listTasks, listTaskDiagnoses } from "../api/client";
import ErrorAlert from "../components/ErrorAlert";
import StatusTag from "../components/StatusTag";
import { COLORS, FONT_SIZES, SPACING } from "../theme";

const CONFIDENCE_COLORS = {
  high: COLORS.success,     // ≥ 0.7
  medium: COLORS.warning,   // ≥ 0.4
  low: COLORS.error,        // < 0.4
};

function confidenceLevel(v) {
  if (v >= 0.7) return "high";
  if (v >= 0.4) return "medium";
  return "low";
}

function confidenceLabel(v) {
  if (v >= 0.7) return "高";
  if (v >= 0.4) return "中";
  return "低";
}

export default function DiagnosisHistory() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [allTasks, setAllTasks] = useState([]);
  const [diagnoses, setDiagnoses] = useState([]);
  const [search, setSearch] = useState("");
  const [filterConfidence, setFilterConfidence] = useState("all");
  const [filterTaskId, setFilterTaskId] = useState("");

  const load = useCallback(async () => {
    setError("");
    setLoading(true);
    try {
      const tasks = await listTasks();
      setAllTasks(tasks || []);

      // 拉取所有任务的诊断（限制最近 50 个任务以控制请求数）
      const recent = (tasks || []).slice(0, 50);
      const results = await Promise.allSettled(
        recent.map((t) => listTaskDiagnoses(t.id))
      );

      const all = [];
      results.forEach((r, i) => {
        if (r.status === "fulfilled" && Array.isArray(r.value)) {
          r.value.forEach((d) => {
            // listTaskDiagnoses returns flat keys, getDiagnosis nests under .run
            const item = d.run ? d : { ...d, run: { task_id: d.task_id, status: d.status, model_name: d.model_name, created_at: d.created_at, summary: d.summary } };
            all.push({ ...item, _task_name: recent[i]?.name || recent[i]?.id });
          });
        }
      });

      all.sort(
        (a, b) =>
          new Date(b.created_at || 0).getTime() -
          new Date(a.created_at || 0).getTime()
      );
      setDiagnoses(all);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const filtered = useMemo(() => {
    let items = diagnoses;
    if (search.trim()) {
      const q = search.toLowerCase();
      items = items.filter(
        (d) =>
          (d._task_name || "").toLowerCase().includes(q) ||
          (d.id || "").toLowerCase().includes(q) ||
          (d.run?.summary || "").toLowerCase().includes(q) ||
          (d.run?.model_name || "").toLowerCase().includes(q)
      );
    }
    if (filterConfidence !== "all") {
      items = items.filter((d) => {
        const c = d.report?.ranked_causes?.[0]?.confidence || 0;
        return confidenceLevel(c) === filterConfidence;
      });
    }
    if (filterTaskId.trim()) {
      items = items.filter((d) =>
        (d.run?.task_id || "").includes(filterTaskId.trim())
      );
    }
    return items;
  }, [diagnoses, search, filterConfidence, filterTaskId]);

  const columns = useMemo(
    () => [
      {
        title: "诊断 ID",
        dataIndex: "id",
        width: 120,
        ellipsis: true,
        render: (value) => (
          <Typography.Text copyable={{ text: value }} style={{ fontSize: FONT_SIZES.sm }}>
            {value?.slice(0, 8)}…
          </Typography.Text>
        ),
      },
      {
        title: "关联任务",
        key: "task",
        width: 160,
        ellipsis: true,
        render: (_, record) => (
          <Link to={`/task/${record.run?.task_id}`}>
            {record._task_name || record.run?.task_id || "-"}
          </Link>
        ),
      },
      {
        title: "摘要",
        key: "summary",
        ellipsis: true,
        render: (_, record) =>
          record.run?.summary || record.report?.report?.summary || "-",
      },
      {
        title: "模型",
        dataIndex: ["run", "model_name"],
        width: 130,
        render: (value) => <Tag>{value || "unknown"}</Tag>,
      },
      {
        title: "状态",
        key: "status",
        width: 100,
        render: (_, record) => (
          <StatusTag status={record.run?.status === "DONE" ? "DONE" : "FAILED"} />
        ),
      },
      {
        title: "置信度",
        key: "confidence",
        width: 160,
        render: (_, record) => {
          const cause = record.report?.ranked_causes?.[0];
          const c = (cause?.confidence || 0) * 100;
          if (!cause) return <Tag>N/A</Tag>;
          return (
            <Space size={4}>
              <Progress
                percent={Math.round(c)}
                size="small"
                strokeColor={CONFIDENCE_COLORS[confidenceLevel(cause.confidence)]}
                style={{ width: 80, margin: 0 }}
              />
              <Tag
                color={confidenceLevel(cause.confidence)}
                style={{ fontSize: 10, margin: 0, lineHeight: "16px" }}
              >
                {confidenceLabel(cause.confidence)}
              </Tag>
            </Space>
          );
        },
      },
      {
        title: "反馈",
        key: "feedback",
        width: 110,
        render: (_, record) => {
          const fb = record.feedback;
          if (!fb)
            return (
              <Tag color="default" style={{ fontSize: 10 }}>
                未反馈
              </Tag>
            );
          const labels = { correct: ["green", "✓ 正确"], partial: ["orange", "◐ 部分"], wrong: ["red", "✗ 错误"] };
          const [color, text] = labels[fb.feedback_label] || ["default", fb.feedback_label];
          return (
            <Tag color={color} style={{ fontSize: 10 }}>
              {text}
            </Tag>
          );
        },
      },
      {
        title: "时间",
        dataIndex: ["run", "created_at"],
        width: 180,
        render: (value) =>
          value ? new Date(value).toLocaleString() : "-",
      },
    ],
    []
  );

  // ── 统计 ──────────────────────────────────────────────

  const stats = useMemo(() => {
    const total = diagnoses.length;
    const done = diagnoses.filter((d) => d.run?.status === "DONE").length;
    const highConf = diagnoses.filter(
      (d) => (d.report?.ranked_causes?.[0]?.confidence || 0) >= 0.7
    ).length;
    const withFeedback = diagnoses.filter((d) => d.feedback).length;
    return { total, done, highConf, withFeedback };
  }, [diagnoses]);

  if (loading) {
    return (
      <Space direction="vertical" size={SPACING.lg} style={{ width: "100%" }}>
        <Skeleton.Input active size="small" style={{ width: 160 }} />
        <Row gutter={SPACING.lg}>
          {[1, 2, 3, 4].map((i) => (
            <Col xs={12} md={6} key={i}>
              <Card size="small">
                <Skeleton active paragraph={{ rows: 1 }} />
              </Card>
            </Col>
          ))}
        </Row>
        <Card size="small">
          <Skeleton active paragraph={{ rows: 8 }} />
        </Card>
      </Space>
    );
  }

  return (
    <Space direction="vertical" size={SPACING.lg} style={{ width: "100%" }}>
      {/* 页头 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexWrap: "wrap",
          gap: 8,
        }}
      >
        <Space align="center">
          <ExperimentOutlined style={{ fontSize: 20, color: COLORS.primary }} />
          <Typography.Title level={4} style={{ margin: 0 }}>
            诊断历史
          </Typography.Title>
        </Space>
        <Button icon={<ReloadOutlined />} onClick={load}>
          刷新
        </Button>
      </div>

      <ErrorAlert error={error} onClose={() => setError("")} />

      {/* 统计卡片 */}
      <Row gutter={SPACING.lg}>
        {[
          { label: "总诊断", value: stats.total, icon: <ExperimentOutlined />, color: COLORS.primary },
          { label: "已完成", value: stats.done, color: COLORS.success },
          { label: "高置信", value: stats.highConf, color: COLORS.warning },
          { label: "有反馈", value: stats.withFeedback, color: "#722ed1" },
        ].map((s, i) => (
          <Col xs={12} md={6} key={i}>
            <Card
              size="small"
              style={{ textAlign: "center" }}
              bodyStyle={{ padding: "12px 16px" }}
            >
              <Typography.Text type="secondary" style={{ fontSize: FONT_SIZES.sm }}>
                {s.label}
              </Typography.Text>
              <Typography.Title
                level={3}
                style={{ margin: "4px 0 0", fontSize: 28, color: s.color }}
              >
                {s.value}
              </Typography.Title>
            </Card>
          </Col>
        ))}
      </Row>

      {/* 搜索 + 过滤 */}
      <Card size="small" bodyStyle={{ padding: "12px 16px" }}>
        <Space wrap size="middle">
          <Input
            placeholder="搜索任务名 / 诊断 ID / 摘要…"
            prefix={<SearchOutlined style={{ color: COLORS.textSecondary }} />}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            allowClear
            style={{ width: 260 }}
          />
          <Select
            value={filterConfidence}
            onChange={setFilterConfidence}
            style={{ width: 120 }}
            options={[
              { value: "all", label: "全部置信度" },
              { value: "high", label: "高 (≥70%)" },
              { value: "medium", label: "中 (40-70%)" },
              { value: "low", label: "低 (<40%)" },
            ]}
          />
          <Input
            placeholder="按 Task ID 筛选…"
            prefix={<FilterOutlined style={{ color: COLORS.textSecondary }} />}
            value={filterTaskId}
            onChange={(e) => setFilterTaskId(e.target.value)}
            allowClear
            style={{ width: 220 }}
          />
          <Tag>{filtered.length} / {diagnoses.length} 条</Tag>
        </Space>
      </Card>

      {/* 表格 */}
      <Card size="small">
        <Table
          rowKey="id"
          columns={columns}
          dataSource={filtered}
          pagination={{ pageSize: 15, showSizeChanger: true, showTotal: (t) => `共 ${t} 条诊断` }}
          size="middle"
          scroll={{ x: 1100 }}
          locale={{ emptyText: "暂无诊断记录，请先运行采集任务并触发智能归因" }}
        />
      </Card>
    </Space>
  );
}
