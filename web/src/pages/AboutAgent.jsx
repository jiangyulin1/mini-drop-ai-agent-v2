import { Alert, Card, Steps, Tag, Typography } from "antd";
import { CheckCircleOutlined, FileSearchOutlined, SafetyCertificateOutlined } from "@ant-design/icons";

export default function AboutAgent() {
  return (
    <div style={{ maxWidth: 1080, margin: "0 auto", padding: 24 }}>
      <Typography.Text type="secondary">MINI-DROP AI AGENT V2</Typography.Text>
      <Typography.Title level={2}>证据驱动、受策略约束、有监督自治</Typography.Title>
      <Typography.Paragraph>Mini-Drop 不是普通聊天机器人。每个 Incident Case 持久化问题、范围、计划、Evidence、假设、决策与恢复验证；Pi Runtime 只能选择服务端白名单工具，所有执行仍受 Scope、Revision、审批、租约、fencing 与审计约束。</Typography.Paragraph>
      <Alert type="info" showIcon message="绿色只代表经过验证的健康或恢复状态" description="Agent 的自然语言声明不会被直接渲染为恢复成功；必须由验证任务或健康检查确认。" />
      <Steps style={{ margin: "28px 0" }} responsive items={[{ title: "理解范围", description: "确认 Service / Node / Process / 时间窗" },{ title: "制定计划", description: "按假设选择白名单诊断工具" },{ title: "采集 Evidence", description: "Worker 带来源与完整性提交证据" },{ title: "更新假设", description: "同时保留支持、反证与缺口" },{ title: "审批与恢复", description: "Proposal → Dry Run → Approval → Execute" },{ title: "服务端验证", description: "Verify 通过后才能标记恢复" }]} />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(260px,1fr))", gap: 16 }}>
        <Card title={<><FileSearchOutlined /> 可解释性</>}><p>结论引用 Evidence ID，展示节点、采集器、任务、时间范围、Trust Revision 与反证。</p><Tag color="purple">WHY DRAWER</Tag></Card>
        <Card title={<><SafetyCertificateOutlined /> 安全边界</>}><p>高风险动作必须有明确作用范围、预检、人工审批、验证条件和回滚方案。</p><Tag color="orange">SUPERVISED</Tag></Card>
        <Card title={<><CheckCircleOutlined /> 验证语义</>}><p>提交、执行与成功是三个不同阶段。只有验证通过才使用绿色成功语义。</p><Tag color="green">VERIFIED ONLY</Tag></Card>
      </div>
      <Typography.Title level={4} style={{ marginTop: 28 }}>当前前端边界</Typography.Title>
      <Typography.Paragraph>页面只调用现有 OpenAPI 契约；没有后端接口的能力会标记为“待接入”或只读说明，不用假数据覆盖 API 错误，也不会在浏览器中暴露 Provider Key、API Key 或内部 Runtime URL。</Typography.Paragraph>
    </div>
  );
}
