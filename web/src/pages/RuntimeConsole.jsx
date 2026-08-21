import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, Button, Card, Descriptions, Divider, Skeleton, Space, Tag, Tooltip, Typography } from "antd";
import { CheckCircleOutlined, CloseCircleOutlined, LockOutlined, ReloadOutlined, RobotOutlined, SettingOutlined } from "@ant-design/icons";
import { getAIConfig, getAgentRuntimeConfig, healthz, listSystemControls } from "../api/client";
import ErrorAlert from "../components/ErrorAlert";
import styles from "./RuntimeConsole.module.css";

const FLAG_CONTENT = [
  ["agent_auto_read_low", "自动 READ_LOW", "Agent 可以提出低风险采集计划，但不会在无人确认时自动创建采集任务。"],
  ["agent_mcp_enabled", "MCP", "Agent 不会连接 MCP 外部工具或数据源。"],
  ["agent_skills_enabled", "Skills", "Agent 不会加载扩展 Skills，仅使用内置白名单能力。"],
  ["agent_cluster_fanout_enabled", "Cluster Fanout", "Agent 不会自动把同一个诊断动作扩散到多个目标节点。"],
];

function enabled(value) { return value === true || value === 1 || value === "1" || value === "true" || value === "enabled"; }

export default function RuntimeConsole() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [runtime, setRuntime] = useState(null);
  const [provider, setProvider] = useState(null);
  const [health, setHealth] = useState(null);
  const [controls, setControls] = useState([]);
  const load = useCallback(async () => {
    setLoading(true); setError(null);
    const values = await Promise.allSettled([getAgentRuntimeConfig(), getAIConfig(), healthz(), listSystemControls()]);
    if (values[0].status === "fulfilled") setRuntime(values[0].value);
    if (values[1].status === "fulfilled") setProvider(values[1].value);
    if (values[2].status === "fulfilled") setHealth(values[2].value);
    if (values[3].status === "fulfilled") setControls(Array.isArray(values[3].value) ? values[3].value : values[3].value?.items || []);
    // A missing optional AI projection is a normal deployment state. Keep
    // actionable errors for actual request failures without surfacing a raw
    // configuration 404 in the workbench.
    const failure = values.find((item, index) => (
      item.status === "rejected" && !(index === 1 && item.reason?.status === 404)
    ));
    if (failure) setError(failure.reason);
    setLoading(false);
  }, []);
  useEffect(() => { void load(); }, [load]);
  if (loading) return <Skeleton active paragraph={{ rows: 16 }} />;
  const flags = runtime?.flags || {};
  const aiReady = runtime?.ai_ready === true;
  const aiNotConfigured = runtime?.ai_status === "NOT_CONFIGURED" || runtime?.mode === "deterministic";
  return (
    <div className={styles.page}>
      <header><div><span>RUNTIME & POLICY BOUNDARIES</span><Typography.Title level={2}>Runtime 与设置</Typography.Title><Typography.Paragraph>这里只显示无敏感运行投影；敏感凭据始终由部署环境管理。</Typography.Paragraph></div><Space><Button icon={<SettingOutlined />} onClick={() => navigate("/settings")}>访问与存储设置</Button><Button icon={<ReloadOutlined />} onClick={load}>刷新</Button></Space></header>
      <ErrorAlert error={error} onRetry={load} />
      {!aiReady && <Alert type={aiNotConfigured ? "info" : "error"} showIcon message={aiNotConfigured ? "AI 调查暂不可用" : "AI 运行服务需要检查"} description="采集、火焰图和 Evidence 工作台仍可继续使用；运行凭据由部署环境管理。" />}
      <div className={styles.grid}>
        <Card title={<Space><RobotOutlined />只读运行状态</Space>} extra={<Tag color={aiReady ? "success" : aiNotConfigured ? "default" : "error"}>{aiReady ? "AI READY" : aiNotConfigured ? "WORKBENCH" : "ERROR"}</Tag>}>
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="Runtime Type">{runtime?.runtime_type || "—"}</Descriptions.Item>
            <Descriptions.Item label="Runtime Version">{runtime?.runtime_version || flags.pi_runtime_version || "—"}</Descriptions.Item>
            <Descriptions.Item label="Runtime Mode"><Tag color="purple">{runtime?.mode || flags.runtime_mode || "—"}</Tag></Descriptions.Item>
            <Descriptions.Item label="Runtime 连接">{flags.pi_runtime_url ? <Tag color="success">已接入</Tag> : <Tag>按部署配置</Tag>}</Descriptions.Item>
            <Descriptions.Item label="AI 服务">{provider?.has_api_key || aiReady ? <Tag color="success">已接入</Tag> : <Tag>按部署配置</Tag>}</Descriptions.Item>
            <Descriptions.Item label="服务版本">{health?.version || "—"}</Descriptions.Item>
          </Descriptions>
        </Card>
        <Card title={<Space><LockOutlined />运行限制</Space>} extra={<Tag>重启后生效</Tag>}>
          {FLAG_CONTENT.map(([key, label, disabledMeaning]) => {
            const active = enabled(flags[key]);
            return <div className={styles.flag} key={key}><span className={active ? styles.flagOn : styles.flagOff}>{active ? <CheckCircleOutlined /> : <CloseCircleOutlined />}</span><div><strong>{label}</strong><small>{active ? "当前开启。执行仍受 Case Scope、风险策略和授权约束。" : disabledMeaning}</small></div><Tag color={active ? "blue" : "default"}>{active ? "开启" : "关闭"}</Tag></div>;
          })}
        </Card>
        <Card title="并发与扇出上限">
          <div className={styles.limits}><div><span>最大活跃 Case</span><strong>{flags.agent_max_active_cases ?? "—"}</strong></div><div><span>最大 Fanout Targets</span><strong>{flags.agent_max_fanout_targets ?? "—"}</strong></div><div><span>每 Turn 最大 Skills</span><strong>{flags.agent_skill_max_per_turn ?? "—"}</strong></div></div>
          <Divider />
          <Alert type="info" showIcon message="当前版本尚不支持在线修改这些 Runtime Flag" description="配置来自服务端环境并在启动时读取。更改需要走配置审计和服务重启流程。" />
        </Card>
        <Card title="全局安全控制" extra={<Tag>{controls.length}</Tag>}>
          {controls.length ? controls.map((item, index) => <div className={styles.control} key={item.control_name || item.name || index}><span><strong>{item.control_name || item.name}</strong><small>{item.reason || item.description || "由服务端策略控制"}</small></span><Tooltip title="状态来自 /api/v1/controls"><Tag color={item.enabled ? "orange" : "default"}>{item.enabled ? "已触发" : "未触发"}</Tag></Tooltip></div>) : <Alert type="info" showIcon message="服务端未返回全局控制项" description="这不是成功执行结果；仅表示当前 API 投影为空。" />}
        </Card>
        <Card title="Collector Agent 工具与预算" extra={<Tag color="purple">Evidence native</Tag>}>
          <Typography.Paragraph type="secondary">
            AI 选择信息目标并提出采集；Runtime Policy 和 Supervisor 决定工具、风险、范围、审批与预算，请求只能缩小服务端权限。
          </Typography.Paragraph>
          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="Canonical Tool Catalog">
              {runtime?.tool_catalog?.tools?.length ?? 0} 个受控工具
            </Descriptions.Item>
            <Descriptions.Item label="执行模式">normal / dry_run / sandbox / deny_write</Descriptions.Item>
            <Descriptions.Item label="每 Case 采集预算">最多 {runtime?.runtime_policy_schema?.properties?.max_collection_requests?.maximum ?? 8} 次 / {runtime?.runtime_policy_schema?.properties?.max_collection_duration_sec?.maximum ?? 240} 秒</Descriptions.Item>
            <Descriptions.Item label="生产审计">
              仅保存决策摘要、工具序列和 Evidence 引用，不保存私有思维链
            </Descriptions.Item>
          </Descriptions>
        </Card>
      </div>
    </div>
  );
}
