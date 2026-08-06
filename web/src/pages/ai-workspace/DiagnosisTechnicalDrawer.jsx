import {
  Button,
  Collapse,
  Descriptions,
  Drawer,
  Empty,
  List,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { Link } from "react-router-dom";
import DiagnosisWorkbench from "../../components/DiagnosisWorkbench";
import EvidenceReference from "../../components/EvidenceReference";
import TaskVisualizationPreview from "../../components/TaskVisualizationPreview";
import styles from "../AIDiagnosis.module.css";
import { DIAGNOSIS_STATUS_META, PROBE_LABELS } from "./workspaceUtils";

export default function DiagnosisTechnicalDrawer({ open, onClose, diagnosis, onDecision }) {
  if (!diagnosis) {
    return <Drawer open={open} onClose={onClose} title="诊断详情"><Empty description="尚无诊断数据" /></Drawer>;
  }
  const evidence = diagnosis.evidence || [];
  const probes = diagnosis.probes || [];
  const probeTasks = probes.filter((item) => item.task_id);
  const conclusion = diagnosis.latest_conclusion || {};
  const commands = conclusion.actions || conclusion.diagnostic_commands || [];
  const status = DIAGNOSIS_STATUS_META[diagnosis.status] || { label: diagnosis.status, color: "default" };

  const items = [
    {
      key: "evidence",
      label: `证据（${evidence.length}）`,
      children: evidence.length ? (
        <Space direction="vertical" size={12} style={{ width: "100%" }}>
          <List
            size="small"
            dataSource={evidence}
            renderItem={(item) => (
              <List.Item>
                <List.Item.Meta
                  title={<EvidenceReference evidence={item} evidenceId={item.evidence_id} />}
                  description={`${item.source_type || "未知来源"} · ${item.target?.instance_id || item.target?.agent_id || "未知目标"}`}
                />
              </List.Item>
            )}
          />
          <DiagnosisWorkbench detail={diagnosis} sessions={[diagnosis]} />
        </Space>
      ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无证据" />,
    },
    {
      key: "collection",
      label: `采集与火焰图（${probeTasks.length}）`,
      children: probeTasks.length ? (
        <Tabs items={probeTasks.map((probe) => ({
          key: probe.task_id,
          label: PROBE_LABELS[probe.probe_id] || probe.probe_id,
          children: <TaskVisualizationPreview taskId={probe.task_id} />,
        }))} />
      ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无采集任务" />,
    },
    {
      key: "probes",
      label: `探针（${probes.length}）`,
      children: probes.length ? (
        <List
          dataSource={probes}
          renderItem={(probe) => (
            <List.Item actions={[
              probe.status === "WAITING_APPROVAL" ? <Button key="approve" size="small" type="primary" onClick={() => onDecision(probe.step_id, "approve")}>批准一次</Button> : null,
              probe.status === "WAITING_APPROVAL" ? <Button key="reject" size="small" danger onClick={() => onDecision(probe.step_id, "reject")}>拒绝</Button> : null,
              probe.task_id ? <Link key="task" to={`/task/${probe.task_id}`}>任务</Link> : null,
            ].filter(Boolean)}>
              <List.Item.Meta
                title={<Space><Typography.Text>{PROBE_LABELS[probe.probe_id] || probe.probe_id}</Typography.Text><Tag>{probe.risk_level}</Tag><Tag>{probe.status}</Tag></Space>}
                description={probe.reason}
              />
            </List.Item>
          )}
        />
      ) : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚无探针" />,
    },
    commands.length ? {
      key: "commands",
      label: `诊断动作（${commands.length}）`,
      children: (
        <Table
          size="small"
          rowKey="action_id"
          pagination={false}
          scroll={{ x: 620 }}
          dataSource={commands}
          columns={[
            { title: "用途", dataIndex: "title", width: 160 },
            { title: "风险", dataIndex: "risk_level", width: 70, render: (value) => <Tag>{value}</Tag> },
            { title: "命令", dataIndex: "rendered_command", render: (value) => <Typography.Text code copyable>{value}</Typography.Text> },
          ]}
        />
      ),
    } : null,
    {
      key: "meta",
      label: "会话信息",
      children: (
        <Descriptions bordered size="small" column={1}>
          <Descriptions.Item label="状态"><Tag color={status.color}>{status.label}</Tag></Descriptions.Item>
          <Descriptions.Item label="诊断 ID"><Typography.Text copyable>{diagnosis.diagnosis_id}</Typography.Text></Descriptions.Item>
          <Descriptions.Item label="拓扑快照">{diagnosis.topology_snapshot_id || "-"}</Descriptions.Item>
          <Descriptions.Item label="模型">{diagnosis.model_version || "-"}</Descriptions.Item>
          <Descriptions.Item label="规划器">{diagnosis.planner_version || "-"}</Descriptions.Item>
          <Descriptions.Item label="原始问题">{diagnosis.raw_query || "-"}</Descriptions.Item>
        </Descriptions>
      ),
    },
  ].filter(Boolean);

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={<Space>诊断详情 <Tag color={status.color}>{status.label}</Tag></Space>}
      width={720}
      styles={{ body: { padding: 16 } }}
    >
      <div className={styles.technicalSection}>
        <Collapse defaultActiveKey={evidence.length ? ["evidence"] : ["meta"]} items={items} />
      </div>
    </Drawer>
  );
}
