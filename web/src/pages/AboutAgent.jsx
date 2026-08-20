import { Alert, Card, Steps, Tag, Typography } from "antd";
import { CheckCircleOutlined, FileSearchOutlined, SafetyCertificateOutlined } from "@ant-design/icons";

export default function AboutAgent() {
  return (
    <div style={{ maxWidth: 1080, margin: "0 auto", padding: 24 }}>
      <Typography.Text type="secondary">MINI-DROP AI AGENT V2</Typography.Text>
      <Typography.Title level={2}>Evidence-native AI Deep Collector</Typography.Title>
      <Typography.Paragraph>Mini-Drop 的特点不是用规则猜根因，而是让 AI 在受控预算内选择 Linux 深度采集器，把 raw artifact、hash、lineage、projection 和人工 Review 持久化为一等 Evidence，再输出可验证引用与明确限制。</Typography.Paragraph>
      <Alert type="info" showIcon message="模型提出目标，服务端拥有执行权" description="Scope、Revision、能力、风险、审批、预算、Task 创建和引用验证都由确定性系统负责。" />
      <Steps style={{ margin: "28px 0" }} responsive items={[{ title: "理解范围", description: "确认 Service / Node / Process / 时间窗" },{ title: "选择信息目标", description: "AI 读取 Evidence 与 Collector Catalog" },{ title: "受控采集", description: "Supervisor 校验后创建原生 Task" },{ title: "物化 Evidence", description: "保存 raw、hash、lineage 与 projection" },{ title: "引用分析", description: "事实绑定字段或文本跨度" },{ title: "继续或停止", description: "补采、拒答或证据充分时结束" }]} />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(260px,1fr))", gap: 16 }}>
        <Card title={<><FileSearchOutlined /> 可解释性</>}><p>结论引用 Evidence ID，展示节点、采集器、任务、时间范围、Trust Revision 与反证。</p><Tag color="purple">WHY DRAWER</Tag></Card>
        <Card title={<><SafetyCertificateOutlined /> 安全边界</>}><p>采集参数、目标范围、风险、审批、次数和累计时长都由服务端硬门禁。</p><Tag color="orange">SUPERVISED</Tag></Card>
        <Card title={<><CheckCircleOutlined /> 正确停止</>}><p>证据不足、预算耗尽或继续采集无信息增益时，Agent 必须停止或拒答。</p><Tag color="green">ABSTAIN</Tag></Card>
      </div>
      <Typography.Title level={4} style={{ marginTop: 28 }}>当前前端边界</Typography.Title>
      <Typography.Paragraph>页面只调用现有 OpenAPI 契约；没有后端接口的能力会标记为“待接入”或只读说明，不用假数据覆盖 API 错误，也不会在浏览器中暴露 Provider Key、API Key 或内部 Runtime URL。</Typography.Paragraph>
    </div>
  );
}
