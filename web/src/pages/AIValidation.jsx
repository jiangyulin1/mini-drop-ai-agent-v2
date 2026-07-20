import { useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Modal,
  Progress,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ExperimentOutlined,
  SafetyOutlined,
} from "@ant-design/icons";
import { runAIValidation } from "../api/client";
import ErrorAlert from "../components/ErrorAlert";
import { COLORS, SPACING } from "../theme";

const STATUS_COLORS = { PASS: "green", FAIL: "red" };

export default function AIValidation() {
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  function requestRun() {
    Modal.confirm({
      title: "运行完整 Drop AI 验证？",
      icon: <ExperimentOutlined />,
      content: "将真实调用 DeepSeek，验证 Provider、NLP、集群诊断意图、AI 总结和 RCA，产生少量 Token 费用。页面和日志不会显示 AI API Key。",
      okText: "开始验证",
      cancelText: "取消",
      onOk: executeRun,
    });
  }

  async function executeRun() {
    setRunning(true);
    setError("");
    try {
      const data = await runAIValidation();
      setResult(data);
      if (data.status === "PASSED") {
        message.success(`Drop AI 验证通过：${data.passed_count}/${data.total_count}`);
      } else {
        message.warning(`验证完成，${data.failed_count} 项未通过`);
      }
    } catch (err) {
      setError(err.message || "AI 验证请求失败");
    } finally {
      setRunning(false);
    }
  }

  const checks = result?.checks || [];
  const percent = result ? Math.round((result.passed_count / result.total_count) * 100) : 0;
  const columns = [
    {
      title: "层级",
      dataIndex: "layer",
      width: 150,
      render: (value) => <Tag color="blue">{value}</Tag>,
    },
    { title: "验证项", dataIndex: "name", width: 220 },
    {
      title: "结果",
      dataIndex: "status",
      width: 100,
      render: (value) => (
        <Tag
          color={STATUS_COLORS[value] || "default"}
          icon={value === "PASS" ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
        >
          {value === "PASS" ? "通过" : "失败"}
        </Tag>
      ),
    },
    {
      title: "耗时",
      dataIndex: "duration_ms",
      width: 110,
      render: (value) => `${value} ms`,
    },
    { title: "说明", dataIndex: "detail" },
  ];

  return (
    <Space direction="vertical" size={SPACING.lg} style={{ width: "100%" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <Space>
          <ExperimentOutlined style={{ fontSize: 22, color: COLORS.primary }} />
          <div>
            <Typography.Title level={4} style={{ margin: 0 }}>Drop AI 服务验证</Typography.Title>
            <Typography.Text type="secondary">从 Provider 到诊断输出的真实端到端验证</Typography.Text>
          </div>
        </Space>
        <Button type="primary" icon={<ExperimentOutlined />} loading={running} onClick={requestRun}>
          {running ? "正在验证…" : "开始完整验证"}
        </Button>
      </div>

      <Alert
        type="info"
        showIcon
        icon={<SafetyOutlined />}
        message="安全说明"
        description="AI Key 仅由 Control Server 从受保护的环境文件读取。浏览器只接收是否配置、模型名和验证结果；Key、余额金额、原始思维链均不会返回。"
      />

      <ErrorAlert error={error} onClose={() => setError("")} />

      {running && (
        <Card size="small">
          <Space direction="vertical" style={{ width: "100%" }}>
            <Typography.Text>正在依次验证 Provider、Drop NLP、集群意图、总结和 RCA，请勿重复提交。</Typography.Text>
            <Progress percent={100} status="active" showInfo={false} />
          </Space>
        </Card>
      )}

      {result ? (
        <>
          <Row gutter={[16, 16]}>
            <Col xs={24} sm={8}>
              <Card size="small"><Statistic title="通过项目" value={result.passed_count} suffix={`/ ${result.total_count}`} valueStyle={{ color: result.failed_count ? COLORS.warning : COLORS.success }} /></Card>
            </Col>
            <Col xs={24} sm={8}>
              <Card size="small"><Statistic title="总耗时" value={result.duration_ms} suffix="ms" /></Card>
            </Col>
            <Col xs={24} sm={8}>
              <Card size="small"><Statistic title="完成度" value={percent} suffix="%" valueStyle={{ color: percent === 100 ? COLORS.success : COLORS.error }} /></Card>
            </Col>
          </Row>

          <Card
            size="small"
            title="验证环境"
            extra={<Tag color={result.status === "PASSED" ? "green" : "red"}>{result.status === "PASSED" ? "全部通过" : "存在失败"}</Tag>}
          >
            <Descriptions size="small" bordered column={{ xs: 1, sm: 2, md: 3 }}>
              <Descriptions.Item label="Run ID"><Typography.Text copyable>{result.run_id}</Typography.Text></Descriptions.Item>
              <Descriptions.Item label="Provider">{result.provider}</Descriptions.Item>
              <Descriptions.Item label="模型"><Tag color="purple">{result.model}</Tag></Descriptions.Item>
              <Descriptions.Item label="Base URL">{result.base_url}</Descriptions.Item>
              <Descriptions.Item label="Key 暴露"><Tag color={result.security?.api_key_exposed ? "red" : "green"}>{result.security?.api_key_exposed ? "是" : "否"}</Tag></Descriptions.Item>
              <Descriptions.Item label="余额金额暴露"><Tag color={result.security?.balance_amount_exposed ? "red" : "green"}>{result.security?.balance_amount_exposed ? "是" : "否"}</Tag></Descriptions.Item>
            </Descriptions>
          </Card>

          <Card size="small" title="分项结果">
            <Table
              rowKey="check_id"
              columns={columns}
              dataSource={checks}
              pagination={false}
              scroll={{ x: 900 }}
              expandable={{
                expandedRowRender: (record) => (
                  <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                    {JSON.stringify(record.metrics || {}, null, 2)}
                  </pre>
                ),
              }}
            />
          </Card>
        </>
      ) : (
        !running && <Alert type="warning" showIcon message="尚未运行验证" description="点击右上角按钮后，你可以在此页面查看 Drop 每一层 AI 服务的实际结果。" />
      )}
    </Space>
  );
}
