import { Descriptions, Drawer, Empty, List, Space, Tag, Typography } from "antd";
import { AuditOutlined, BulbOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import { riskLevel } from "../utils/opsMappings";
import { formatBeijingDateTime } from "../utils/time";

function array(value) { return Array.isArray(value) ? value : value ? [value] : []; }
function text(value, fallback = "未提供") {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "string" || typeof value === "number") return String(value);
  return value.summary || value.statement || value.description || JSON.stringify(value);
}

export default function ExplainabilityDrawer({ open, onClose, decision }) {
  const item = decision || {};
  const risk = riskLevel(item.risk || item.risk_level);
  const evidence = array(item.evidence_refs || item.evidence || item.supporting_evidence);
  const supporting = array(item.supporting_factors || item.why || item.reasons);
  const opposing = array(item.opposing_factors || item.counter_evidence || item.contradictions);
  const missing = array(item.missing_information || item.evidence_gaps || item.gaps);
  return (
    <Drawer title={<Space><BulbOutlined style={{ color: "#7c3aed" }} />为什么 / Explainability</Space>} open={open} onClose={onClose} width={640} destroyOnHidden>
      {!decision ? <Empty description="选择一项 Agent 决策以查看解释" /> : <Space direction="vertical" size={18} style={{ width: "100%" }}>
        <section><Typography.Text type="secondary">决策结果</Typography.Text><Typography.Title level={4} style={{ margin: "4px 0" }}>{text(item.title || item.statement || item.summary || item.purpose, "Agent 决策")}</Typography.Title><Typography.Paragraph>{text(item.explanation || item.description || item.expected_information, "当前接口没有提供额外自然语言解释。")}</Typography.Paragraph></section>
        <Descriptions size="small" bordered column={1}>
          <Descriptions.Item label="决策时间">{item.decided_at || item.updated_at || item.created_at ? formatBeijingDateTime(item.decided_at || item.updated_at || item.created_at) : "未提供"}</Descriptions.Item>
          <Descriptions.Item label="决策主体">{item.actor_id || item.created_by || item.source || "Mini-Drop Agent"}</Descriptions.Item>
          <Descriptions.Item label="规则 / 策略">{text(item.policy || item.rule || item.priority_source, "服务端白名单与 Case Policy")}</Descriptions.Item>
          <Descriptions.Item label="风险约束"><Tag color={risk.color}>{risk.label}</Tag> {risk.description}</Descriptions.Item>
          <Descriptions.Item label="上下文版本">Scope r{item.scope_revision ?? "—"} · Plan r{item.plan_revision ?? "—"} · Control r{item.control_revision ?? "—"}</Descriptions.Item>
          <Descriptions.Item label="审计事件 ID"><Typography.Text copyable>{item.audit_event_id || item.event_id || item.decision_id || item.step_id || "未提供"}</Typography.Text></Descriptions.Item>
        </Descriptions>
        <section><Typography.Title level={5}><AuditOutlined /> 引用 Evidence</Typography.Title>{evidence.length ? <Space wrap>{evidence.map((ref) => <Tag color="blue" key={text(ref)}>{text(ref)}</Tag>)}</Space> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有引用 Evidence；该决策不应被视为已证实结论" />}</section>
        <List size="small" header={<strong>支持因素</strong>} bordered dataSource={supporting} locale={{ emptyText: "接口未返回支持因素" }} renderItem={(value) => <List.Item>{text(value)}</List.Item>} />
        <List size="small" header={<strong>反对因素 / 反证</strong>} bordered dataSource={opposing} locale={{ emptyText: "接口未返回反对因素" }} renderItem={(value) => <List.Item>{text(value)}</List.Item>} />
        <List size="small" header={<strong>缺失信息 / Evidence Gap</strong>} bordered dataSource={missing} locale={{ emptyText: "接口未返回证据缺口" }} renderItem={(value) => <List.Item>{text(value)}</List.Item>} />
        <section><Typography.Title level={5}><SafetyCertificateOutlined /> 下一步</Typography.Title><Typography.Paragraph>{text(item.next_action || item.recommendation || item.expected_information, "返回 Case 工作台，审查对应 Evidence 或等待计划步骤完成。")}</Typography.Paragraph></section>
      </Space>}
    </Drawer>
  );
}
