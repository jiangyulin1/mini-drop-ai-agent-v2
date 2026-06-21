import { useEffect, useState, useCallback } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Input,
  message,
  Row,
  Skeleton,
  Space,
  Tag,
  Typography,
} from "antd";
import {
  SettingOutlined,
  RobotOutlined,
  MessageOutlined,
  SafetyOutlined,
  CloudServerOutlined,
  ReloadOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  SendOutlined,
  ExperimentOutlined,
} from "@ant-design/icons";
import axios from "axios";
import {
  healthz,
  getAIConfig,
  getStoredApiKey,
  saveApiKey,
} from "../api/client";
import ErrorAlert from "../components/ErrorAlert";
import { COLORS, FONT_SIZES, SPACING } from "../theme";

export default function Settings() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [health, setHealth] = useState(null);
  const [aiConfig, setAiConfig] = useState(null);
  const [apiKey, setApiKey] = useState(getStoredApiKey() || "");
  const [savingKey, setSavingKey] = useState(false);
  const [testingChatops, setTestingChatops] = useState(false);
  const [testingAI, setTestingAI] = useState(false);

  const load = useCallback(async () => {
    setError("");
    setLoading(true);
    try {
      const results = await Promise.allSettled([
        healthz(),
        getAIConfig().catch(() => null),
      ]);
      if (results[0].status === "fulfilled") setHealth(results[0].value);
      if (results[1].status === "fulfilled") setAiConfig(results[1].value);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const checks = health?.checks || {};
  const featureStatus = (enabled) =>
    enabled ? (
      <Tag icon={<CheckCircleOutlined />} color="green">已启用</Tag>
    ) : (
      <Tag icon={<CloseCircleOutlined />} color="default">已禁用</Tag>
    );

  async function handleSaveKey() {
    setSavingKey(true);
    try {
      await saveApiKey(apiKey.trim());
      setApiKey(apiKey.trim());
      message.success(apiKey.trim() ? "API Key 已保存" : "API Key 已清除");
    } catch (err) {
      message.error(err.message);
    } finally {
      setSavingKey(false);
    }
  }

  async function testChatops() {
    setTestingChatops(true);
    try {
      const resp = await axios.post("/api/chatops/test");
      if (resp.data?.data?.ok) {
        message.success("ChatOps 测试消息已发送");
      } else {
        message.warning(resp.data?.message || "ChatOps 可能未启用");
      }
    } catch (err) {
      message.error(err.response?.data?.detail || err.message || "ChatOps 测试失败");
    } finally {
      setTestingChatops(false);
    }
  }

  async function testAI() {
    setTestingAI(true);
    try {
      const resp = await axios.post("/api/nlp/parse", { query: "hello" });
      if (resp.data?.code === 0) {
        message.success(`AI Provider 响应正常（策略: ${resp.data.data?.reasoning?.slice(0, 30) || "关键词匹配"}…）`);
      } else {
        message.warning(resp.data?.message || "AI 响应异常");
      }
    } catch (err) {
      message.error(err.response?.data?.detail || err.message || "AI 测试失败");
    } finally {
      setTestingAI(false);
    }
  }

  if (loading) {
    return (
      <Space direction="vertical" size={SPACING.lg} style={{ width: "100%" }}>
        <Skeleton.Input active size="small" style={{ width: 160 }} />
        {[1, 2, 3].map((i) => (
          <Card key={i} size="small">
            <Skeleton active paragraph={{ rows: 5 }} />
          </Card>
        ))}
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
          <SettingOutlined style={{ fontSize: 20, color: COLORS.primary }} />
          <Typography.Title level={4} style={{ margin: 0 }}>
            系统设置
          </Typography.Title>
        </Space>
        <Button icon={<ReloadOutlined />} onClick={load}>
          刷新
        </Button>
      </div>

      <ErrorAlert error={error} onClose={() => setError("")} />

      {/* 服务健康 */}
      <Card
        title={
          <Space>
            <CloudServerOutlined style={{ color: COLORS.primary }} />
            服务健康
          </Space>
        }
        size="small"
        extra={
          <Tag color={health?.healthy ? "green" : "red"}>
            {health?.healthy ? "健康" : "异常"}
          </Tag>
        }
      >
        <Descriptions column={{ xs: 1, sm: 2 }} size="small" bordered>
          <Descriptions.Item label="服务名">
            {health?.service || "mini-drop-server"}
          </Descriptions.Item>
          <Descriptions.Item label="版本">
            <Tag>{health?.version || "0.1.0"}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="数据库">
            {checks.database ? (
              <Tag color={checks.database.status === "ok" ? "green" : "red"}>
                {checks.database.status === "ok" ? "✓ 连通" : "✗ 不可用"}
              </Tag>
            ) : (
              <Tag>未知</Tag>
            )}
          </Descriptions.Item>
          <Descriptions.Item label="对象存储">
            {checks.storage ? (
              <Tag color={checks.storage.status === "ok" ? "green" : "red"}>
                {checks.storage.status === "ok" ? "✓ 连通" : "✗ 不可用"}
              </Tag>
            ) : (
              <Tag>未知</Tag>
            )}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      {/* AI 配置 */}
      <Card
        title={
          <Space>
            <RobotOutlined style={{ color: COLORS.warning }} />
            AI Provider 配置
          </Space>
        }
        size="small"
        extra={
          aiConfig?.enabled && aiConfig.enabled !== "none" ? (
            <Tag color="orange">AI: {aiConfig.enabled}</Tag>
          ) : (
            <Tag>AI 未启用</Tag>
          )
        }
      >
        {aiConfig ? (
          <Descriptions column={{ xs: 1, sm: 2, md: 3 }} size="small" bordered>
            <Descriptions.Item label="厂商">
              <Tag color="blue">{aiConfig.provider || "unknown"}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="模型">
              <Tag>{aiConfig.model || "N/A"}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="API 端点">
              <Typography.Text
                copyable
                ellipsis
                style={{ maxWidth: 240, fontSize: FONT_SIZES.sm }}
              >
                {aiConfig.base_url || "N/A"}
              </Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="API Key">
              <Tag color={aiConfig.has_api_key ? "green" : "red"}>
                {aiConfig.has_api_key ? "已配置" : "未配置"}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="策略模式">
              <Tag color="purple">{aiConfig.enabled || "none"}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="功能开关" span={2}>
              <Space wrap>
                {featureStatus(aiConfig.features?.nlp)}
                <Typography.Text style={{ fontSize: FONT_SIZES.sm }}>NLP 自然语言</Typography.Text>
                {featureStatus(aiConfig.features?.rca)}
                <Typography.Text style={{ fontSize: FONT_SIZES.sm }}>RCA 智能归因</Typography.Text>
                {featureStatus(aiConfig.features?.summarize)}
                <Typography.Text style={{ fontSize: FONT_SIZES.sm }}>AI 总结</Typography.Text>
              </Space>
            </Descriptions.Item>
          </Descriptions>
        ) : (
          <Alert
            type="warning"
            message="无法获取 AI 配置"
            description="请确认已设置 MINI_DROP_AI_ENABLED 及相关环境变量"
            showIcon
          />
        )}
      </Card>

      {/* ChatOps */}
      <Card
        title={
          <Space>
            <MessageOutlined style={{ color: COLORS.success }} />
            ChatOps 通知
          </Space>
        }
        size="small"
        extra={
          <Button
            size="small"
            icon={<SendOutlined />}
            loading={testingChatops}
            onClick={testChatops}
          >
            发送测试消息
          </Button>
        }
      >
        <Alert
          type="info"
          message="ChatOps 通知配置"
          description={
            <span>
              通过环境变量 MINI_DROP_CHATOPS_ENABLED / MINI_DROP_CHATOPS_PROVIDER /
              MINI_DROP_CHATOPS_WEBHOOK_URL 配置 IM 通知。
              支持：<Tag>wecom</Tag> <Tag>feishu</Tag> <Tag>dingtalk</Tag> <Tag>slack</Tag> <Tag>qqbot</Tag>
              <br />
              服务启动后，任务状态变更、Agent 上下线、诊断完成将自动推送到配置的 IM 渠道。
            </span>
          }
          showIcon
        />
      </Card>

      {/* AI 测试 */}
      <Card
        title={
          <Space>
            <RobotOutlined style={{ color: COLORS.warning }} />
            AI 连通性测试
          </Space>
        }
        size="small"
        extra={
          <Button
            size="small"
            icon={<ExperimentOutlined />}
            loading={testingAI}
            onClick={testAI}
          >
            测试 AI 响应
          </Button>
        }
      >
        <Alert
          type={aiConfig?.has_api_key ? "success" : "warning"}
          message={aiConfig?.has_api_key ? `AI Provider: ${aiConfig.provider} / ${aiConfig.model}` : "AI Provider 未配置"}
          description={
            aiConfig?.has_api_key
              ? `策略模式: ${aiConfig.enabled} · NLP: ${aiConfig.features?.nlp ? "开" : "关"} · RCA: ${aiConfig.features?.rca ? "开" : "关"} · 摘要: ${aiConfig.features?.summarize ? "开" : "关"}`
              : "请设置 MINI_DROP_AI_API_KEY 等环境变量启用 AI 功能"
          }
          showIcon
        />
      </Card>

      {/* API 认证 */}
      <Card
        title={
          <Space>
            <SafetyOutlined style={{ color: COLORS.primary }} />
            API 认证
          </Space>
        }
        size="small"
        extra={
          apiKey ? (
            <Tag color="green">Key 已设置</Tag>
          ) : (
            <Tag color="default">未设置</Tag>
          )
        }
      >
        <Space direction="vertical" style={{ width: "100%" }} size={12}>
          <Alert
            type="info"
            message="API Key 同时保存在 HttpOnly Cookie（优先）和 localStorage（降级）中"
            showIcon
          />
          <Input.Password
            placeholder="输入 API Key（留空清除）"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            onPressEnter={handleSaveKey}
            allowClear
          />
          <Space size={8}>
            <Button
              type="primary"
              size="small"
              loading={savingKey}
              onClick={handleSaveKey}
            >
              保存
            </Button>
            {apiKey && (
              <Button
                size="small"
                danger
                onClick={async () => {
                  setApiKey("");
                  await saveApiKey("");
                  message.success("API Key 已清除");
                }}
              >
                清除 Key
              </Button>
            )}
          </Space>
          <Typography.Text type="secondary" style={{ fontSize: FONT_SIZES.sm }}>
            清除 Key 后需要重新设置才能访问受保护的 API。
          </Typography.Text>
        </Space>
      </Card>
    </Space>
  );
}
