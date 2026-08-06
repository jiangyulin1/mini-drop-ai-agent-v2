import { useCallback, useState } from "react";
import { Outlet, useNavigate, useLocation } from "react-router-dom";
import {
  Button,
  ConfigProvider,
  Drawer,
  Grid,
  Input,
  Layout,
  Menu,
  message,
  Space,
  Tag,
  Tooltip,
  Typography,
  theme as antdTheme,
} from "antd";
import {
  DashboardOutlined,
  SettingOutlined,
  MenuUnfoldOutlined,
  KeyOutlined,
  BulbOutlined,
  BulbFilled,
  ApiOutlined,
  WifiOutlined,
  RobotOutlined,
} from "@ant-design/icons";
import { getStoredApiKey, saveApiKey } from "../api/client";
import ErrorBoundary from "../components/ErrorBoundary";
import useSSE from "../hooks/useSSE";
import { COLORS, LAYOUT, SPACING, FONT_SIZES } from "../theme";

const { Sider, Header, Content } = Layout;
const { useBreakpoint } = Grid;

const MENU_ITEMS = [
  { key: "/", icon: <DashboardOutlined />, label: "采集与监控" },
  { key: "/ai-diagnosis", icon: <RobotOutlined />, label: "AI 诊断" },
  { key: "/settings", icon: <SettingOutlined />, label: "设置" },
];

// ── 暗色主题 tokens ───────────────────────────────────────────

const DARK_TOKENS = {
  bgLayout: "#141414",
  bgContent: "#1f1f1f",
  bgHeader: "#1f1f1f",
  borderColor: "#303030",
  textPrimary: "rgba(255,255,255,0.85)",
  textSecondary: "rgba(255,255,255,0.65)",
  textTertiary: "rgba(255,255,255,0.45)",
  cardBg: "#1f1f1f",
};

const LIGHT_TOKENS = {
  bgLayout: "#f5f5f5",
  bgContent: COLORS.cardBackground,
  bgHeader: COLORS.cardBackground,
  borderColor: COLORS.border,
  textPrimary: COLORS.textPrimary,
  textSecondary: COLORS.textSecondary,
  textTertiary: COLORS.textTertiary,
  cardBg: COLORS.cardBackground,
};

export default function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const screens = useBreakpoint();
  const isMobile = screens.md === false;
  const [collapsed, setCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [apiKey, setApiKey] = useState(getStoredApiKey() || "");
  const [savingApiKey, setSavingApiKey] = useState(false);
  const [darkMode, setDarkMode] = useState(() => {
    try {
      return localStorage.getItem("mini-drop-theme") === "dark";
    } catch {
      return false;
    }
  });

  // SSE 连接状态
  const [sseConnected, setSseConnected] = useState(false);

  const T = darkMode ? DARK_TOKENS : LIGHT_TOKENS;

  // ── 暗色模式持久化 ──────────────────────────────────────

  const toggleDarkMode = useCallback(() => {
    setDarkMode((prev) => {
      const next = !prev;
      try {
        localStorage.setItem("mini-drop-theme", next ? "dark" : "light");
      } catch {
        // ignore
      }
      return next;
    });
  }, []);

  useSSE({ onConnectionChange: setSseConnected });

  // ── 路由激活 key ─────────────────────────────────────────

  const path = location.pathname;
  const isAIWorkspace = path.startsWith("/ai-diagnosis");
  const selectedKey = MENU_ITEMS.find(
    (item) => path === item.key || (item.key !== "/" && path.startsWith(item.key))
  )?.key || "/";

  // ── 保存 API Key ─────────────────────────────────────────

  async function handleSaveKey() {
    const token = apiKey.trim();
    setSavingApiKey(true);
    try {
      await saveApiKey(token);
      message.success(token ? "API Key 已验证并保存" : "API Key 已清除");
      window.dispatchEvent(new Event("mini-drop:auth-changed"));
    } catch (error) {
      message.error(`API Key 验证失败：${error.message}`);
    } finally {
      setSavingApiKey(false);
    }
  }

  const renderSidebarContent = (compact = false) => (
    <div style={{ position: "relative", height: "100%" }}>
        {/* Logo */}
        <div
          style={{
            height: LAYOUT.headerHeight,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            borderBottom: "1px solid rgba(255,255,255,0.12)",
            gap: compact ? 0 : 8,
          }}
        >
          <ApiOutlined
            style={{
              fontSize: compact ? 20 : 18,
              color: COLORS.primary,
              transition: "transform 0.3s",
            }}
          />
          {!compact && (
            <Typography.Text
              strong
              style={{
                color: "#fff",
                fontSize: 16,
                letterSpacing: 0.5,
                whiteSpace: "nowrap",
              }}
            >
              Mini-Drop
            </Typography.Text>
          )}
        </div>

        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey]}
          items={MENU_ITEMS}
          onClick={({ key }) => {
            navigate(key);
            setMobileMenuOpen(false);
          }}
          style={{ marginTop: SPACING.sm }}
        />

        {/* SSE 指示器 */}
        <div
          style={{
            position: "absolute",
            bottom: 80,
            left: 0,
            right: 0,
            padding: "0 16px",
            textAlign: "center",
          }}
        >
          <Tooltip
            title={
              sseConnected ? "实时事件推送已连接" : "实时事件推送断开（轮询兜底）"
            }
          >
            <Tag
              icon={<WifiOutlined />}
              color={sseConnected ? "green" : "default"}
              style={{
                width: "100%",
                textAlign: "center",
                border: "none",
                background: sseConnected
                  ? "rgba(82,196,26,0.15)"
                  : "rgba(255,255,255,0.06)",
                color: sseConnected ? "#52c41a" : "rgba(255,255,255,0.3)",
                fontSize: 11,
              }}
            >
              {compact ? "" : sseConnected ? "SSE 已连接" : "SSE 断开"}
            </Tag>
          </Tooltip>
        </div>
    </div>
  );

  return (
    <ConfigProvider
      theme={{
        algorithm: darkMode ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
        token: { colorPrimary: COLORS.primary, borderRadius: 8 },
      }}
    >
      <Layout
        style={{
          minHeight: "100vh",
          background: T.bgLayout,
          transition: "background 0.3s ease",
        }}
      >
        {!isMobile && (
          <Sider
            collapsible
            collapsed={collapsed}
            onCollapse={setCollapsed}
            collapsedWidth={64}
            width={LAYOUT.siderWidth}
            theme="dark"
            style={{
              overflow: "auto",
              height: "100vh",
              position: "sticky",
              top: 0,
              left: 0,
            }}
          >
            {renderSidebarContent(collapsed)}
          </Sider>
        )}

        <Drawer
          open={isMobile && mobileMenuOpen}
          onClose={() => setMobileMenuOpen(false)}
          placement="left"
          width={240}
          closable={false}
          styles={{
            body: { padding: 0, background: "#001529" },
          }}
        >
          {renderSidebarContent(false)}
        </Drawer>

        {/* ── 主区域 ─────────────────────────────────────────── */}
        <Layout style={{ minWidth: 0 }}>
        {/* 顶栏 */}
        <Header
          style={{
            height: LAYOUT.headerHeight,
            lineHeight: `${LAYOUT.headerHeight}px`,
            padding: isMobile ? `0 ${SPACING.sm}px` : `0 ${SPACING.lg}px`,
            background: T.bgHeader,
            borderBottom: `1px solid ${T.borderColor}`,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            position: "sticky",
            top: 0,
            zIndex: 10,
            transition: "background 0.3s ease, border-color 0.3s ease",
          }}
        >
          <Space size={isMobile ? "small" : "middle"}>
            {isMobile && (
              <Button
                type="text"
                icon={<MenuUnfoldOutlined />}
                aria-label="打开导航菜单"
                onClick={() => setMobileMenuOpen(true)}
                style={{ color: T.textSecondary }}
              />
            )}
            <Typography.Text
              strong
              style={{
                fontSize: FONT_SIZES.lg,
                whiteSpace: "nowrap",
                color: T.textPrimary,
              }}
            >
              {isMobile ? "Mini-Drop" : "Mini-Drop 性能诊断平台"}
            </Typography.Text>
            {!isMobile && (
              <Tag
                color={sseConnected ? "green" : "default"}
                style={{ fontSize: 10, lineHeight: "16px" }}
              >
                {sseConnected ? "实时连接" : "轮询模式"}
              </Tag>
            )}
          </Space>

          <Space size="small" style={{ flexShrink: 0 }}>
            {/* 暗色模式切换 */}
            <Tooltip title={darkMode ? "切换亮色模式" : "切换暗色模式"}>
              <Button
                size="small"
                type="text"
                aria-label={darkMode ? "切换亮色模式" : "切换暗色模式"}
                icon={
                  darkMode ? (
                    <BulbFilled style={{ color: COLORS.warning }} />
                  ) : (
                    <BulbOutlined />
                  )
                }
                onClick={toggleDarkMode}
                style={{ color: T.textSecondary }}
              />
            </Tooltip>

            {isMobile ? (
              <Tooltip title="前往系统设置管理 API Key">
                <Button
                  size="small"
                  type="text"
                  aria-label="管理 API Key"
                  icon={<KeyOutlined />}
                  onClick={() => navigate("/settings")}
                  style={{ color: T.textSecondary }}
                />
              </Tooltip>
            ) : (
              <>
                <Input.Password
                  placeholder="Mini-Drop API Key（必填）"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  onPressEnter={handleSaveKey}
                  size="small"
                  style={{ width: 200, maxWidth: "40vw" }}
                  prefix={<KeyOutlined style={{ color: T.textSecondary }} />}
                  disabled={savingApiKey}
                />
                <Button
                  size="small"
                  type="primary"
                  loading={savingApiKey}
                  onClick={handleSaveKey}
                >
                  保存
                </Button>
              </>
            )}
          </Space>
        </Header>

        {/* 内容 */}
        <Content
          style={{
            margin: isAIWorkspace ? 0 : isMobile ? SPACING.sm : SPACING.lg,
            padding: isAIWorkspace ? 0 : isMobile ? SPACING.md : SPACING.xl,
            background: T.bgContent,
            borderRadius: isAIWorkspace ? 0 : 8,
            minHeight: isAIWorkspace
              ? `calc(100vh - ${LAYOUT.headerHeight}px)`
              : `calc(100vh - ${LAYOUT.headerHeight}px - ${SPACING.lg * 2}px)`,
            border: isAIWorkspace ? "none" : `1px solid ${T.borderColor}`,
            overflow: isAIWorkspace ? "hidden" : "visible",
            transition: "background 0.3s ease, border-color 0.3s ease",
          }}
        >
          <ErrorBoundary key={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </Content>
      </Layout>
      </Layout>
    </ConfigProvider>
  );
}
