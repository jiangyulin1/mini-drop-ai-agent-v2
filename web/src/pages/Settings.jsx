import { useEffect, useState, useCallback } from "react";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Input,
  message,
  Skeleton,
  Space,
  Tag,
  Typography,
} from "antd";
import {
  SettingOutlined,
  RobotOutlined,
  SafetyOutlined,
  CloudServerOutlined,
  ReloadOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
} from "@ant-design/icons";
import {
  healthz,
  getAIConfig,
  getCurrentUser,
  getStoredApiKey,
  saveApiKey,
} from "../api/client";
import ErrorAlert from "../components/ErrorAlert";
import AuditLogs from "./AuditLogs";
import StorageMaintenance from "../components/StorageMaintenance";
import { COLORS, FONT_SIZES, SPACING } from "../theme";

const CONFIG_DETAIL_PATTERN = /(api[\s_-]?key|token|secret|credential|password|authorization|not[\s_-]?configured|missing)/i;

function aiConfigErrorMessage(reason) {
  if (reason?.status === 404) return "AI 服务由部署环境管理";
  const detail = String(reason?.message || "").trim();
  if (!detail) return "AI 服务请求失败，请刷新重试。";
  if (CONFIG_DETAIL_PATTERN.test(detail)) {
    const status = reason?.status ? `（HTTP ${reason.status}）` : "";
    return `AI 服务请求失败${status}，请刷新重试或联系部署管理员。`;
  }
  return detail;
}

export default function Settings() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [health, setHealth] = useState(null);
  const [aiConfig, setAiConfig] = useState(null);
  const [aiConfigError, setAiConfigError] = useState("");
  const [currentUser, setCurrentUser] = useState(null);
  const [apiKey, setApiKey] = useState(getStoredApiKey() || "");
  const [savingKey, setSavingKey] = useState(false);

  const load = useCallback(async () => {
    setError("");
    setLoading(true);
    setCurrentUser(null);
    setAiConfigError("");
    try {
      const results = await Promise.allSettled([
        healthz(),
        getAIConfig(),
        getCurrentUser(),
      ]);
      if (results[0].status === "fulfilled") setHealth(results[0].value);
      if (results[1].status === "fulfilled") {
        setAiConfig(results[1].value);
      } else {
        setAiConfig(null);
        setAiConfigError(aiConfigErrorMessage(results[1].reason));
      }
      if (results[2].status === "fulfilled") setCurrentUser(results[2].value);
      const failures = [
        results[0].status === "rejected" ? `健康检查失败：${results[0].reason?.message || "请求失败"}` : "",
        results[2].status === "rejected" && results[2].reason?.status !== 401
          ? `认证状态检查失败：${results[2].reason?.message || "请求失败"}`
          : "",
      ].filter(Boolean);
      if (failures.length) setError([...new Set(failures)].join("；"));
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
      setApiKey("");
      message.success(apiKey.trim() ? "访问凭据已保存" : "访问凭据已清除");
      window.dispatchEvent(new Event("mini-drop:auth-changed"));
      await load();
    } catch (err) {
      message.error(err.message);
    } finally {
      setSavingKey(false);
    }
  }

  async function handleClearKey() {
    setSavingKey(true);
    try {
      await saveApiKey("");
      setApiKey("");
      window.dispatchEvent(new Event("mini-drop:auth-changed"));
      message.success("浏览器认证已清除");
      await load();
    } catch (clearError) {
      message.error(`清除失败：${clearError.message}`);
    } finally {
      setSavingKey(false);
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
            AI 服务
          </Space>
        }
        size="small"
        extra={
          aiConfig?.enabled && aiConfig.enabled !== "none" ? (
            <Tag color="orange">AI 已接入</Tag>
          ) : (
            <Tag>按部署配置</Tag>
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
            <Descriptions.Item label="策略模式">
              <Tag color="purple">{aiConfig.enabled || "none"}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="功能开关" span={2}>
              <Space wrap>
                {featureStatus(aiConfig.features?.summarize)}
                <Typography.Text style={{ fontSize: FONT_SIZES.sm }}>AI 总结</Typography.Text>
              </Space>
            </Descriptions.Item>
          </Descriptions>
        ) : (
          <Alert
            type="info"
            message={aiConfigError || "AI 服务按部署配置"}
            description="这里仅展示能力状态；即使 AI 暂不可用，采集、火焰图和证据工作台仍可继续使用。"
            showIcon
          />
        )}
      </Card>

      {/* API 认证 */}
      <Card
        title={
          <Space>
            <SafetyOutlined style={{ color: COLORS.primary }} />
            访问设置
          </Space>
        }
        size="small"
        extra={
          currentUser ? (
            <Tag color="green">访问正常</Tag>
          ) : (
            <Tag color="orange">需要认证</Tag>
          )
        }
      >
        <Space direction="vertical" style={{ width: "100%" }} size={12}>
          <Alert
            type="info"
            message="当前访问正常时无需重复设置；凭据由部署环境和安全 Cookie 管理。"
            showIcon
          />
          <Input.Password
            placeholder="输入访问凭据（留空清除）"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
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
            {(currentUser || apiKey) && (
              <Button
                size="small"
                danger
                loading={savingKey}
                onClick={handleClearKey}
              >
                清除浏览器认证
              </Button>
            )}
          </Space>
          <Typography.Text type="secondary" style={{ fontSize: FONT_SIZES.sm }}>
            仅在访问异常或更换环境时重新设置。
          </Typography.Text>
        </Space>
      </Card>

      {/* 存储维护（低风险可回滚修复） */}
      <StorageMaintenance />

      {/* 操作记录属于低频管理能力，不占用主导航。 */}
      <Card
        title={
          <Space>
            <SafetyOutlined style={{ color: COLORS.textSecondary }} />
            审计与存档
          </Space>
        }
        size="small"
      >
        <AuditLogs />
      </Card>
    </Space>
  );
}
