import { Alert, Button, Collapse, Space, Typography } from "antd";

/**
 * 可复用的错误提示组件。
 *
 * 只在 error 非空时渲染，error 为空时返回 null。
 *
 * @param {{ error: string, style?: object, onClose?: () => void }} props
 */
export default function ErrorAlert({ error, style, onClose, onRetry }) {
  if (!error) return null;
  const value = typeof error === "string" ? { message: error } : error;
  const message = value.message || "请求失败";
  const requestId = value.requestId || value.request_id || "";
  const technical = value.detail || value.stack || "";
  return (
    <Alert
      type="error"
      message={value.isTimeout ? "请求超时" : value.status === 403 ? "权限不足" : "数据加载失败"}
      description={(
        <Space direction="vertical" size={6} style={{ width: "100%" }}>
          <Typography.Text>{message}</Typography.Text>
          {requestId && (
            <Typography.Text type="secondary" copyable={{ text: requestId }}>
              Request ID：{requestId}
            </Typography.Text>
          )}
          <Space size={8} wrap>
            {onRetry && <Button size="small" onClick={onRetry}>重试</Button>}
            <Typography.Text type="secondary">
              {value.retryable === false ? "请检查权限或输入后重试" : "可重试；若持续失败，请携带 Request ID 联系管理员"}
            </Typography.Text>
          </Space>
          {technical && (
            <Collapse
              ghost
              size="small"
              items={[{
                key: "technical",
                label: "技术详情",
                children: <Typography.Text code>{typeof technical === "string" ? technical : JSON.stringify(technical, null, 2)}</Typography.Text>,
              }]}
            />
          )}
        </Space>
      )}
      showIcon
      closable={!!onClose}
      onClose={onClose}
      style={style}
    />
  );
}
