import { useCallback, useEffect, useMemo, useState } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { Badge, Button, ConfigProvider, Drawer, Grid, Input, Layout, List, Menu, Modal, Space, Tag, Tooltip, theme as antdTheme } from "antd";
import {
  ApiOutlined,
  AuditOutlined,
  BulbFilled,
  BulbOutlined,
  ClockCircleOutlined,
  CloudServerOutlined,
  CodeOutlined as CommandOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  InfoCircleOutlined,
  MenuUnfoldOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
  SettingOutlined,
  WifiOutlined,
} from "@ant-design/icons";
import {
  getAgentRuntimeConfig,
  getCurrentUser,
  healthz,
  listAgents,
  listIncidentCases,
  listSystemControls,
} from "../api/client";
import ErrorBoundary from "./ErrorBoundary";
import useSSE from "../hooks/useSSE";
import { isActiveCase } from "../utils/opsMappings";
import { COLORS, LAYOUT } from "../theme";
import styles from "./AppLayout.module.css";

const { Sider, Header, Content } = Layout;
const { useBreakpoint } = Grid;

const NAV_ITEMS = [
  { key: "/", icon: <DashboardOutlined />, label: "总览", title: "总览 / Overview", description: "系统健康、调查态势与待处理事项" },
  { key: "/cases", icon: <RobotOutlined />, label: "AI Evidence 调查", title: "AI Evidence 调查 / Cases", description: "受控采集、证据分析与人工治理" },
  { key: "/tasks", icon: <FileSearchOutlined />, label: "任务与证据", title: "任务与证据", description: "采集任务、状态机和 Artifact" },
  { key: "/agents", icon: <CloudServerOutlined />, label: "节点与 Agent", title: "节点与 Agent", description: "Control、Worker 与能力差异" },
  { key: "/diagnoses", icon: <ClockCircleOutlined />, label: "旧诊断记录", title: "旧诊断记录", description: "只读查看历史规则归因结果" },
  { key: "/audit", icon: <AuditOutlined />, label: "审计与安全", title: "审计与安全", description: "关键操作、状态变化和安全事件" },
  { key: "/runtime", icon: <SettingOutlined />, label: "Runtime 与设置", title: "Runtime 与设置", description: "Pi Runtime、功能边界和运行配置" },
  { key: "/about", icon: <InfoCircleOutlined />, label: "系统说明", title: "关于 Mini-Drop Agent", description: "证据驱动与有监督自治设计" },
];

const ROUTE_META = [
  { prefix: "/task/", title: "任务详情", description: "Artifact、状态机、租约与分析结果" },
  { prefix: "/agent/", title: "Worker 详情", description: "节点状态、能力与任务" },
  { prefix: "/settings", title: "访问与存储设置", description: "认证、Provider 与存储维护" },
];

function routeMeta(pathname) {
  return NAV_ITEMS.find((item) => pathname === item.key || (item.key !== "/" && pathname.startsWith(item.key))) || ROUTE_META.find((item) => pathname.startsWith(item.prefix)) || NAV_ITEMS[0];
}

export default function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const screens = useBreakpoint();
  const isMobile = screens.md === false;
  const isCaseWorkspace = location.pathname.startsWith("/cases") || location.pathname.startsWith("/ai-diagnosis");
  const currentMeta = useMemo(() => routeMeta(location.pathname), [location.pathname]);
  const [collapsed, setCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [commandOpen, setCommandOpen] = useState(false);
  const [commandSearch, setCommandSearch] = useState("");
  const [global, setGlobal] = useState({ health: null, runtime: null, agents: [], cases: [], controls: [], user: null });
  const [darkMode, setDarkMode] = useState(() => { try { return localStorage.getItem("mini-drop-theme") === "dark"; } catch { return false; } });
  const { connected: sseConnected, connectionState, reconnect } = useSSE();

  const loadGlobal = useCallback(async () => {
    const results = await Promise.allSettled([healthz(), getAgentRuntimeConfig(), listAgents(), listIncidentCases({ limit: 100 }), listSystemControls(), getCurrentUser()]);
    setGlobal((current) => ({
      health: results[0].status === "fulfilled" ? results[0].value : current.health,
      runtime: results[1].status === "fulfilled" ? results[1].value : current.runtime,
      agents: results[2].status === "fulfilled" ? results[2].value : current.agents,
      cases: results[3].status === "fulfilled" ? results[3].value?.items || [] : current.cases,
      controls: results[4].status === "fulfilled" ? results[4].value?.items || results[4].value || [] : current.controls,
      user: results[5].status === "fulfilled" ? results[5].value : current.user,
    }));
  }, []);

  useEffect(() => { void loadGlobal(); const timer=setInterval(loadGlobal, 30000); const auth=()=>loadGlobal(); window.addEventListener("mini-drop:auth-changed",auth); window.addEventListener("mini-drop:refresh",loadGlobal); return()=>{clearInterval(timer);window.removeEventListener("mini-drop:auth-changed",auth);window.removeEventListener("mini-drop:refresh",loadGlobal);}; }, [loadGlobal]);
  useEffect(() => { document.documentElement.dataset.theme=darkMode?"dark":"light"; document.title=`${currentMeta.title} · Mini-Drop`; }, [currentMeta.title,darkMode]);

  const goTo = useCallback((path) => { navigate(path); setMobileMenuOpen(false); setCommandOpen(false); }, [navigate]);
  useEffect(() => {
    const handler=(event)=>{
      const target=event.target; const typing=target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target?.isContentEditable;
      if ((event.ctrlKey||event.metaKey) && event.key.toLowerCase()==="k") { event.preventDefault(); setCommandOpen(true); return; }
      if (event.key==="Escape") { setCommandOpen(false); setMobileMenuOpen(false); return; }
      if (typing) return;
      if (event.key.toLowerCase()==="n") { event.preventDefault(); goTo("/cases?new=1"); }
      if (event.key==="/") { event.preventDefault(); setCommandOpen(true); }
      if (event.key.toLowerCase()==="r") { event.preventDefault(); window.dispatchEvent(new Event("mini-drop:refresh")); }
    };
    window.addEventListener("keydown",handler); return()=>window.removeEventListener("keydown",handler);
  }, [goTo]);

  const toggleDarkMode=()=>setDarkMode((previous)=>{const next=!previous;try{localStorage.setItem("mini-drop-theme",next?"dark":"light");}catch{}return next;});
  const selectedKey=NAV_ITEMS.find((item)=>location.pathname===item.key||(item.key!=="/"&&location.pathname.startsWith(item.key)))?.key || "/";
  const effectiveWorkers=global.agents.filter((agent)=>agent.id!=="demo-worker");
  const onlineWorkers=effectiveWorkers.filter((agent)=>agent.status==="ONLINE").length;
  const activeCases=global.cases.filter((item)=>isActiveCase(item.state));
  const approvals=activeCases.filter((item)=>item.state==="WAITING_APPROVAL"||item.summary?.need_you?.required).length;
  const controlTriggered=Array.isArray(global.controls)&&global.controls.some((item)=>item.enabled&&/pause|stop|red/i.test(item.control_name||item.name||""));
  const aiStatus=global.runtime?.ai_ready?"AI 已就绪":global.runtime?.mode==="deterministic"?"AI 未配置":"AI 异常";
  const commands=NAV_ITEMS.filter((item)=>!commandSearch||`${item.label} ${item.title} ${item.description}`.toLowerCase().includes(commandSearch.toLowerCase()));

  const renderSidebar=(compact=false)=><div className={styles.sidebarInner}>
    <button type="button" className={`${styles.brand} ${compact?styles.brandCompact:""}`} onClick={()=>goTo("/")} aria-label="返回 Mini-Drop 总览"><span className={styles.brandMark}><ApiOutlined /></span>{!compact&&<span className={styles.brandText}><strong>Mini-Drop</strong><small>AI AGENT V2</small></span>}</button>
    <nav className={styles.navigation} aria-label="主导航"><Menu theme="dark" mode="inline" selectedKeys={[selectedKey]} items={NAV_ITEMS.map(({key,icon,label})=>({key,icon,label}))} onClick={({key})=>goTo(key)} inlineCollapsed={compact}/></nav>
    {!compact&&<div className={styles.sidebarHelp}><span>安全原则</span><strong>Evidence before conclusion</strong><small>只有服务端验证通过，才能显示恢复成功。</small></div>}
  </div>;

  return <ConfigProvider theme={{algorithm:darkMode?antdTheme.darkAlgorithm:antdTheme.defaultAlgorithm,token:{colorPrimary:COLORS.primary,borderRadius:8,controlHeight:34}}}>
    <a className={styles.skipLink} href="#main-content">跳到主要内容</a>
    <Layout className={styles.shell} style={{"--app-header-height":`${LAYOUT.headerHeight}px`}}>
      {!isMobile&&<Sider className={styles.sider} collapsible collapsed={collapsed} onCollapse={setCollapsed} collapsedWidth={68} width={236} theme="dark">{renderSidebar(collapsed)}</Sider>}
      <Drawer open={isMobile&&mobileMenuOpen} onClose={()=>setMobileMenuOpen(false)} placement="left" width={264} closable={false} styles={{body:{padding:0,background:"#0b1220"}}}>{renderSidebar(false)}</Drawer>
      <Layout className={styles.mainLayout}>
        <Header className={styles.header}><div className={styles.headerMain}>{isMobile&&<Button type="text" icon={<MenuUnfoldOutlined/>} aria-label="打开导航菜单" onClick={()=>setMobileMenuOpen(true)}/>}<div className={styles.pageIdentity}><strong>{currentMeta.title}</strong>{!isCaseWorkspace&&<span>{currentMeta.description}</span>}</div></div><div className={styles.headerActions}><Button size="small" type="text" icon={<SearchOutlined/>} onClick={()=>setCommandOpen(true)} aria-label="打开命令面板"><span className={styles.actionLabel}>命令</span><kbd>⌘K</kbd></Button><Tooltip title={darkMode?"切换亮色模式":"切换暗色模式"}><Button size="small" type="text" aria-label={darkMode?"切换亮色模式":"切换暗色模式"} icon={darkMode?<BulbFilled className={styles.themeIconActive}/>:<BulbOutlined/>} onClick={toggleDarkMode}/></Tooltip></div></Header>
        <div className={styles.globalBar} aria-label="全局运行状态"><span><Badge status={global.health?.healthy?"success":"error"}/><b>{global.cases[0]?.environment||"production"}</b></span><Tooltip title="Server / Database / Storage / Analyzer 综合健康"><span><DatabaseOutlined/><b>服务 {global.health?.healthy?"健康":"异常"}</b></span></Tooltip><span><RobotOutlined/><b>{aiStatus}</b></span><span><CloudServerOutlined/><b>Worker {onlineWorkers}/{effectiveWorkers.length}</b></span><span><FileSearchOutlined/><b>Case {activeCases.length}</b></span><span className={approvals?styles.globalAttention:""}><SafetyCertificateOutlined/><b>审批 {approvals}</b></span><button type="button" onClick={sseConnected?undefined:reconnect} className={styles.streamButton}><WifiOutlined/><b>{sseConnected?"实时已连接":connectionState==="reconnecting"?"正在重连":"轮询降级"}</b></button><span className={controlTriggered?styles.globalDanger:""}><ApiOutlined/><b>{controlTriggered?"Red Button 已触发":"全局控制正常"}</b></span><button type="button" onClick={()=>goTo("/settings")}><SafetyCertificateOutlined/><b>{global.user?.role||"未认证"}</b></button></div>
        <Content id="main-content" role="main" className={isCaseWorkspace?styles.workspaceContent:styles.content}><ErrorBoundary key={location.pathname}><Outlet/></ErrorBoundary></Content>
      </Layout>
    </Layout>
    <Modal title={<Space><CommandOutlined/>命令面板</Space>} open={commandOpen} onCancel={()=>setCommandOpen(false)} footer={null} width={600} destroyOnHidden><Input autoFocus data-global-search aria-label="搜索页面和操作" prefix={<SearchOutlined/>} placeholder="搜索页面或操作…" value={commandSearch} onChange={(event)=>setCommandSearch(event.target.value)}/><List style={{marginTop:12}} dataSource={commands} renderItem={(item)=><List.Item className={styles.commandItem} onClick={()=>goTo(item.key)} actions={[<Tag key="shortcut">Enter</Tag>]}><List.Item.Meta avatar={item.icon} title={item.title} description={item.description}/></List.Item>}/><div className={styles.shortcuts}><span><kbd>N</kbd> 新建 Case</span><span><kbd>/</kbd> 搜索</span><span><kbd>R</kbd> 刷新</span><span><kbd>Esc</kbd> 关闭</span></div></Modal>
  </ConfigProvider>;
}
