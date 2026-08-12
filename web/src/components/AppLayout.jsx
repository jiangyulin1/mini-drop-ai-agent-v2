import { useCallback, useEffect, useMemo, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Button,
  ConfigProvider,
  Drawer,
  Grid,
  Layout,
  Menu,
  Tag,
  Tooltip,
  theme as antdTheme,
} from "antd";
import {
  ApiOutlined,
  BulbFilled,
  BulbOutlined,
  DashboardOutlined,
  MenuUnfoldOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  WifiOutlined,
} from "@ant-design/icons";
import { getCurrentUser } from "../api/client";
import ErrorBoundary from "./ErrorBoundary";
import useSSE from "../hooks/useSSE";
import { COLORS, LAYOUT } from "../theme";
import styles from "./AppLayout.module.css";

const { Sider, Header, Content } = Layout;
const { useBreakpoint } = Grid;

const NAV_ITEMS = [
  {
    key: "/",
    icon: <DashboardOutlined />,
    label: "采集分析",
    title: "采集与监控",
    description: "选择节点和进程，采集性能数据并查看结果",
  },
  {
    key: "/ai-diagnosis",
    icon: <RobotOutlined />,
    label: "AI 诊断",
    title: "AI 诊断",
    description: "围绕故障会话持续调查、解释与验证恢复",
  },
  {
    key: "/settings",
    icon: <SettingOutlined />,
    label: "系统设置",
    title: "系统设置",
    description: "检查服务、认证、AI 能力和审计记录",
  },
];

const ROUTE_META = [
  { prefix: "/task/", title: "采集结果", description: "查看任务产物、时间线和分析结果" },
  { prefix: "/agent/", title: "Worker 详情", description: "查看节点状态、能力与任务" },
  { prefix: "/diagnoses", title: "诊断历史", description: "查看已完成的归因记录" },
  { prefix: "/audit", title: "审计日志", description: "追踪关键操作与状态变化" },
];

function routeMeta(pathname) {
  return NAV_ITEMS.find((item) => (
    pathname === item.key || (item.key !== "/" && pathname.startsWith(item.key))
  )) || ROUTE_META.find((item) => pathname.startsWith(item.prefix)) || NAV_ITEMS[0];
}

export default function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const screens = useBreakpoint();
  const isMobile = screens.md === false;
  const isAIWorkspace = location.pathname.startsWith("/ai-diagnosis");
  const currentMeta = useMemo(() => routeMeta(location.pathname), [location.pathname]);
  const [collapsed, setCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [authState, setAuthState] = useState({ status: "checking", user: null });
  const [darkMode, setDarkMode] = useState(() => {
    try {
      return localStorage.getItem("mini-drop-theme") === "dark";
    } catch {
      return false;
    }
  });
  const { connected: sseConnected } = useSSE();

  useEffect(() => {
    let requestId = 0;
    const syncAuthState = async () => {
      const currentRequest = requestId + 1;
      requestId = currentRequest;
      setAuthState((current) => ({ ...current, status: "checking" }));
      try {
        const user = await getCurrentUser();
        if (requestId === currentRequest) setAuthState({ status: "ready", user });
      } catch (error) {
        if (requestId !== currentRequest) return;
        const requiresKey = error.status === 401 || String(error.message || "").includes("认证失败");
        setAuthState({ status: requiresKey ? "required" : "error", user: null });
      }
    };
    void syncAuthState();
    window.addEventListener("mini-drop:auth-changed", syncAuthState);
    return () => {
      requestId += 1;
      window.removeEventListener("mini-drop:auth-changed", syncAuthState);
    };
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = darkMode ? "dark" : "light";
    document.title = `${currentMeta.title} · Mini-Drop`;
  }, [currentMeta.title, darkMode]);

  const toggleDarkMode = useCallback(() => {
    setDarkMode((previous) => {
      const next = !previous;
      try {
        localStorage.setItem("mini-drop-theme", next ? "dark" : "light");
      } catch {
        // The selected theme still applies for the current session.
      }
      return next;
    });
  }, []);

  const selectedKey = NAV_ITEMS.find((item) => (
    location.pathname === item.key
      || (item.key !== "/" && location.pathname.startsWith(item.key))
  ))?.key || "/";

  function goTo(path) {
    navigate(path);
    setMobileMenuOpen(false);
  }

  const renderSidebar = (compact = false) => (
    <div className={styles.sidebarInner}>
      <button
        type="button"
        className={`${styles.brand} ${compact ? styles.brandCompact : ""}`}
        onClick={() => goTo("/")}
        aria-label="返回 Mini-Drop 首页"
      >
        <span className={styles.brandMark}><ApiOutlined /></span>
        {!compact && (
          <span className={styles.brandText}>
            <strong>Mini-Drop</strong>
            <small>性能诊断平台</small>
          </span>
        )}
      </button>

      <nav className={styles.navigation} aria-label="主导航">
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey]}
          items={NAV_ITEMS.map(({ key, icon, label }) => ({ key, icon, label }))}
          onClick={({ key }) => goTo(key)}
          inlineCollapsed={compact}
        />
      </nav>

      {!compact && (
        <div className={styles.sidebarHelp}>
          <span>推荐流程</span>
          <strong>先采集，再诊断</strong>
          <small>原始数据始终可独立查看，AI 用于关联证据与解释结论。</small>
        </div>
      )}
    </div>
  );

  return (
    <ConfigProvider
      theme={{
        algorithm: darkMode ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
        token: {
          colorPrimary: COLORS.primary,
          borderRadius: 10,
          controlHeight: 36,
        },
      }}
    >
      <a className={styles.skipLink} href="#main-content">跳到主要内容</a>
      <Layout
        className={styles.shell}
        style={{ "--app-header-height": `${LAYOUT.headerHeight}px` }}
      >
        {!isMobile && (
          <Sider
            className={styles.sider}
            collapsible
            collapsed={collapsed}
            onCollapse={setCollapsed}
            collapsedWidth={68}
            width={LAYOUT.siderWidth}
            theme="dark"
          >
            {renderSidebar(collapsed)}
          </Sider>
        )}

        <Drawer
          open={isMobile && mobileMenuOpen}
          onClose={() => setMobileMenuOpen(false)}
          placement="left"
          width={264}
          closable={false}
          styles={{ body: { padding: 0, background: "#0b1220" } }}
        >
          {renderSidebar(false)}
        </Drawer>

        <Layout className={styles.mainLayout}>
          <Header className={styles.header}>
            <div className={styles.headerMain}>
              {isMobile && (
                <Button
                  type="text"
                  icon={<MenuUnfoldOutlined />}
                  aria-label="打开导航菜单"
                  onClick={() => setMobileMenuOpen(true)}
                />
              )}
              <div className={styles.pageIdentity}>
                <strong>{currentMeta.title}</strong>
                {!isAIWorkspace && <span>{currentMeta.description}</span>}
              </div>
            </div>

            <div className={styles.headerActions}>
              <Tooltip title={sseConnected ? "实时事件已连接" : "实时事件未连接，页面将自动轮询"}>
                <Tag
                  className={styles.connectionTag}
                  icon={<WifiOutlined />}
                  color={sseConnected ? "success" : "default"}
                >
                  {sseConnected ? "实时" : "轮询"}
                </Tag>
              </Tooltip>

              <Tooltip title={
                authState.status === "ready"
                  ? `${authState.user?.name || "当前会话"}可正常访问，点击查看设置`
                  : authState.status === "required"
                    ? "配置访问密钥后才能调用受保护接口"
                    : authState.status === "error"
                      ? "无法确认服务连接，点击前往设置检查"
                      : "正在确认访问状态"
              }>
                <Button
                  className={authState.status === "required" ? styles.authButtonPending : ""}
                  size="small"
                  type={authState.status === "required" ? "primary" : "text"}
                  icon={<SafetyCertificateOutlined />}
                  onClick={() => goTo("/settings")}
                  aria-label={authState.status === "ready" ? "服务访问正常" : "检查访问认证"}
                >
                  <span className={styles.actionLabel}>
                    {authState.status === "ready" ? "访问正常" : authState.status === "required" ? "配置密钥" : authState.status === "error" ? "连接异常" : "检查中"}
                  </span>
                </Button>
              </Tooltip>

              <Tooltip title={darkMode ? "切换亮色模式" : "切换暗色模式"}>
                <Button
                  size="small"
                  type="text"
                  aria-label={darkMode ? "切换亮色模式" : "切换暗色模式"}
                  icon={darkMode ? <BulbFilled className={styles.themeIconActive} /> : <BulbOutlined />}
                  onClick={toggleDarkMode}
                />
              </Tooltip>
            </div>
          </Header>

          <Content
            id="main-content"
            role="main"
            className={isAIWorkspace ? styles.workspaceContent : styles.content}
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
