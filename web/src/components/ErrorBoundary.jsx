import { Component } from "react";
import { Alert, Button, Result, Space, Typography } from "antd";
import { ReloadOutlined, HomeOutlined } from "@ant-design/icons";

/**
 * 全局错误边界。
 *
 * 捕获渲染阶段的未处理异常，防止整个 SPA 白屏崩溃。
 * 降级为一个友好的错误提示，支持"重试"和"返回首页"操作。
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    this.setState({ errorInfo });
    console.error("[ErrorBoundary]", error, errorInfo?.componentStack);
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  handleGoHome = () => {
    this.handleReset();
    window.location.href = "/";
  };

  render() {
    if (this.state.hasError) {
      const isDev = import.meta.env.DEV;
      return (
        <Result
          status="500"
          title="页面渲染异常"
          subTitle="组件渲染时发生未预期的错误，请尝试刷新页面或返回首页。"
          extra={
            <Space size="middle" direction="vertical">
              <Space size="small">
                <Button
                  type="primary"
                  icon={<ReloadOutlined />}
                  onClick={this.handleReset}
                >
                  重试
                </Button>
                <Button
                  icon={<HomeOutlined />}
                  onClick={this.handleGoHome}
                >
                  返回首页
                </Button>
              </Space>
              {isDev && this.state.error && (
                <Alert
                  type="error"
                  message={this.state.error?.message || "未知错误"}
                  description={
                    <Typography.Paragraph
                      code
                      ellipsis={{ rows: 6, expandable: true, symbol: "展开" }}
                      style={{ fontSize: 11, margin: 0 }}
                    >
                      {this.state.error?.stack}
                      {this.state.errorInfo?.componentStack}
                    </Typography.Paragraph>
                  }
                />
              )}
            </Space>
          }
        />
      );
    }

    return this.props.children;
  }
}
