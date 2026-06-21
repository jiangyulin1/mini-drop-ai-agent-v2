import { useEffect, useState, useCallback, useMemo } from "react";
import { Card, Skeleton, Space, Table, Tag, Typography, Button, Input, Select } from "antd";
import { ReloadOutlined, AuditOutlined, SearchOutlined, FilterOutlined } from "@ant-design/icons";
import { listAuditLogs } from "../api/client";
import ErrorAlert from "../components/ErrorAlert";
import { COLORS, SPACING } from "../theme";

const EVENT_COLORS = {
  agent_registered: "green",
  agent_online: "green",
  agent_offline: "red",
  task_created: "blue",
  task_pending: "default",
  task_running: "blue",
  task_done: "green",
  task_failed: "red",
  task_deleted: "volcano",
  diagnosis_done: "purple",
  diagnosis_failed: "red",
};

export default function AuditLogs() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [logs, setLogs] = useState([]);
  const [search, setSearch] = useState("");
  const [eventFilter, setEventFilter] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      setLogs(await listAuditLogs());
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const eventTypes = useMemo(() => {
    const types = new Set(logs.map((l) => l.event_type));
    return [...types].sort();
  }, [logs]);

  const filtered = useMemo(() => {
    let items = logs;
    if (search.trim()) {
      const q = search.toLowerCase();
      items = items.filter(
        (l) =>
          (l.event_type || "").toLowerCase().includes(q) ||
          (l.message || "").toLowerCase().includes(q) ||
          (l.agent_id || "").toLowerCase().includes(q) ||
          (l.task_id || "").toLowerCase().includes(q)
      );
    }
    if (eventFilter) {
      items = items.filter((l) => l.event_type === eventFilter);
    }
    // 默认按时间倒序
    items = [...items].sort(
      (a, b) =>
        new Date(b.created_at || 0).getTime() -
        new Date(a.created_at || 0).getTime()
    );
    return items;
  }, [logs, search, eventFilter]);

  const columns = [
    {
      title: "事件",
      dataIndex: "event_type",
      width: 170,
      render: (value) => (
        <Tag color={EVENT_COLORS[value] || "default"}>{value}</Tag>
      ),
    },
    { title: "消息", dataIndex: "message", ellipsis: true },
    { title: "Agent", dataIndex: "agent_id", width: 140, ellipsis: true,
      render: (v) => v || "-" },
    { title: "任务", dataIndex: "task_id", width: 200, ellipsis: true,
      render: (v) => v ? <Typography.Text copyable={{ text: v }} style={{ fontSize: 11 }}>{v.slice(-12)}</Typography.Text> : "-" },
    {
      title: "时间",
      dataIndex: "created_at",
      width: 180,
      render: (v) => (v ? new Date(v).toLocaleString() : "-"),
    },
  ];

  if (loading) {
    return (
      <Space direction="vertical" size={SPACING.lg} style={{ width: "100%" }}>
        <Skeleton.Input active size="small" style={{ width: 140 }} />
        <Card size="small">
          <Skeleton active paragraph={{ rows: 8 }} />
        </Card>
      </Space>
    );
  }

  return (
    <Space direction="vertical" size={SPACING.lg} style={{ width: "100%" }}>
      {/* 页头 */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 8 }}>
        <Space align="center">
          <AuditOutlined style={{ fontSize: 20, color: COLORS.primary }} />
          <Typography.Title level={4} style={{ margin: 0 }}>
            审计日志
          </Typography.Title>
          <Tag>{filtered.length}</Tag>
        </Space>
        <Space size={8} wrap>
          <Input
            size="small"
            style={{ width: 200 }}
            placeholder="搜索事件/消息/Agent…"
            prefix={<SearchOutlined />}
            allowClear
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <Select
            size="small"
            style={{ width: 160 }}
            placeholder="事件类型"
            allowClear
            value={eventFilter || undefined}
            onChange={(v) => setEventFilter(v || "")}
            suffixIcon={<FilterOutlined />}
          >
            {eventTypes.map((t) => (
              <Select.Option key={t} value={t}>
                <Tag color={EVENT_COLORS[t] || "default"} style={{ margin: 0 }}>{t}</Tag>
              </Select.Option>
            ))}
          </Select>
          <Button icon={<ReloadOutlined />} size="small" onClick={load}>
            刷新
          </Button>
        </Space>
      </div>

      <ErrorAlert error={error} onClose={() => setError("")} />

      <Card size="small">
        <Table
          rowKey={(record, index) => `${record.event_type}-${record.created_at}-${index}`}
          columns={columns}
          dataSource={filtered}
          pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `共 ${t} 条` }}
          size="middle"
          scroll={{ x: 900 }}
          locale={{ emptyText: search || eventFilter ? "无匹配的审计日志" : "暂无审计日志记录" }}
        />
      </Card>
    </Space>
  );
}
