import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Col,
  Form,
  Input,
  InputNumber,
  Modal,
  Radio,
  Row,
  Select,
  Space,
  Spin,
  Typography,
  message,
} from "antd";
import { MinusCircleOutlined, PlusOutlined, SearchOutlined } from "@ant-design/icons";
import { scanAgentProcesses } from "../../api/client";
import styles from "../AIDiagnosis.module.css";
import { RELATION_OPTIONS } from "./workspaceUtils";

/** 把扫描候选渲染为可读的单行摘要。 */
function describeProcess(proc) {
  const cmdline = proc.cmdline || proc.comm || `pid-${proc.pid}`;
  const parts = [proc.pid, proc.comm || ""];
  return { title: parts.filter(Boolean).join(" · "), detail: `${cmdline} · ${proc.rss_mb || 0} MB · CPU ${proc.cpu_percent ?? "-"}%` };
}

export default function ScopeEditorModal({ open, detail, agents, saving, onClose, onSave, autoSearch = false }) {
  const [form] = Form.useForm();
  const [targets, setTargets] = useState([]);
  const [scanKeyword, setScanKeyword] = useState("");
  const [scanning, setScanning] = useState(false);
  const [scanResults, setScanResults] = useState({}); // agentId -> { status, processes, message }
  const [autoSearchDone, setAutoSearchDone] = useState(false);

  useEffect(() => {
    if (!open || !detail) return;
    const currentInstances = detail.target_scope?.instances || [];
    const currentByAgent = new Map(currentInstances.map((item) => [item.agent_id, item]));
    setTargets(agents.map((agent) => {
      const current = currentByAgent.get(agent.id);
      return {
        agent,
        checked: Boolean(current) || (currentInstances.length === 0 && agent.status === "ONLINE"),
        pid: current?.pid || null,
        instanceId: current?.instance_id || "",
      };
    }));
    setScanKeyword(detail.target_scope?.service_id || "");
    setScanResults({});
    setAutoSearchDone(false);
    form.setFieldsValue({
      service_id: detail.target_scope?.service_id || detail.target_scope?.service_ids?.[0] || "",
      environment: detail.environment || "production",
      recovery_goal: detail.recovery_goal || "确认原因并给出安全处置建议",
      dependencies: detail.target_scope?.dependencies || [],
    });
  }, [agents, detail, form, open]);

  // 创建会话后自动搜索：基础用户无需手动点击，直接看到候选进程
  useEffect(() => {
    if (!open || !autoSearch || autoSearchDone || scanning) return;
    setAutoSearchDone(true);
    const timer = window.setTimeout(() => { searchProcesses(); }, 250);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, autoSearch, autoSearchDone]);

  function updateTarget(agentId, patch) {
    setTargets((items) => items.map((item) => (
      item.agent.id === agentId ? { ...item, ...patch } : item
    )));
  }

  /** 在勾选的在线 Worker 上搜索进程，返回每个 Agent 的候选列表。 */
  async function searchProcesses() {
    const keyword = scanKeyword.trim();
    const selectedOnline = targets.filter((item) => item.checked && item.agent.status === "ONLINE");
    if (!selectedOnline.length) {
      message.warning("请先勾选至少一个在线 Worker");
      return;
    }
    if (!keyword) {
      message.warning("请输入进程名或服务名关键字（例如 service-x）");
      return;
    }
    setScanning(true);
    setScanResults({});
    const settled = await Promise.allSettled(selectedOnline.map((item) =>
      scanAgentProcesses(item.agent.id, { query: keyword, timeoutSec: 15 }),
    ));
    const next = {};
    settled.forEach((result, index) => {
      const agentId = selectedOnline[index].agent.id;
      if (result.status === "fulfilled") {
        next[agentId] = {
          status: "DONE",
          processes: result.value?.processes || [],
          message: result.value?.message || "",
        };
      } else {
        next[agentId] = { status: "ERROR", processes: [], message: result.reason?.message || "扫描失败" };
      }
    });
    setScanResults(next);
    setScanning(false);

    // 每个 Worker 有唯一候选时自动选中，减少一次点击
    setTargets((items) => items.map((item) => {
      const found = next[item.agent.id];
      if (!found || found.status !== "DONE") return item;
      const exact = found.processes.filter((proc) =>
        (proc.comm || "").toLowerCase() === keyword.toLowerCase()
        || (proc.cmdline || "").toLowerCase().includes(keyword.toLowerCase()),
      );
      const picked = exact.length === 1 ? exact[0] : found.processes.length === 1 ? found.processes[0] : null;
      return picked ? { ...item, pid: picked.pid, instanceId: `${form.getFieldValue("service_id") || "service"}-${picked.pid}` } : item;
    }));
  }

  const checkedOnlineCount = useMemo(
    () => targets.filter((item) => item.checked && item.agent.status === "ONLINE").length,
    [targets],
  );

  async function submit(startAfter) {
    const values = await form.validateFields();
    const instances = targets.filter((item) => item.checked && Number(item.pid) > 0).map((item, index) => ({
      service_id: values.service_id,
      instance_id: item.instanceId || `${values.service_id}-${item.agent.hostname || item.agent.id}-${index + 1}`,
      host_id: item.agent.hostname || item.agent.id,
      agent_id: item.agent.id,
      pid: Number(item.pid),
      environment: values.environment,
    }));
    if (startAfter && instances.length === 0) {
      form.setFields([{ name: "service_id", errors: ["开始诊断前，请至少选择一个进程（搜索候选或手动填写 PID）"] }]);
      return;
    }
    const dependencies = (values.dependencies || []).filter(
      (item) => item?.source_service && item?.target_service,
    ).map((item) => ({
      ...item,
      confidence: item.confidence || "medium",
      source: item.source || "user_confirmed",
    }));
    await onSave({
      environment: values.environment,
      recoveryGoal: values.recovery_goal,
      targetScope: {
        ...detail.target_scope,
        service_id: values.service_id,
        instances,
        dependencies,
      },
    }, startAfter);
  }

  return (
    <Modal
      title="诊断范围"
      open={open}
      width={860}
      onCancel={onClose}
      destroyOnHidden
      footer={[
        <Button key="cancel" onClick={onClose}>取消</Button>,
        <Button key="save" loading={saving} onClick={() => submit(false)}>保存</Button>,
        <Button key="start" type="primary" loading={saving} onClick={() => submit(true)}>保存并开始诊断</Button>,
      ]}
    >
      <Alert
        type="info"
        showIcon
        message="不知道 PID 也没关系：输入服务名或进程名，系统会扫描在线 Worker 并列出候选。只有你确认的进程才会被采集。"
        style={{ marginBottom: 14 }}
      />
      <Form form={form} layout="vertical">
        <Row gutter={12}>
          <Col xs={24} md={12}>
            <Form.Item name="service_id" label="目标服务" rules={[{ required: true, message: "请输入目标服务" }]}>
              <Input placeholder="例如 service-x" />
            </Form.Item>
          </Col>
          <Col xs={24} md={12}>
            <Form.Item name="environment" label="环境" rules={[{ required: true }]}>
              <Select options={[
                { value: "production", label: "生产" },
                { value: "staging", label: "预发布" },
                { value: "development", label: "开发" },
              ]} />
            </Form.Item>
          </Col>
        </Row>

        <div className={styles.scanBar}>
          <Input
            allowClear
            prefix={<SearchOutlined />}
            value={scanKeyword}
            onChange={(event) => setScanKeyword(event.target.value)}
            onPressEnter={searchProcesses}
            placeholder="进程名或服务名关键字，例如 service-x"
            style={{ maxWidth: 360 }}
            aria-label="进程搜索关键字"
          />
          <Button type="primary" icon={<SearchOutlined />} loading={scanning} onClick={searchProcesses}>
            搜索候选进程
          </Button>
          <Typography.Text type="secondary">
            将在 {checkedOnlineCount} 个在线 Worker 上扫描
          </Typography.Text>
        </div>

        <Typography.Text strong>Worker 与目标进程</Typography.Text>
        <Typography.Paragraph type="secondary" style={{ margin: "3px 0 8px" }}>
          从候选中选择一个进程，或手动填写 PID。取消勾选可排除节点。
        </Typography.Paragraph>
        <div className={styles.targetList}>
          {targets.map((item) => {
            const result = scanResults[item.agent.id];
            return (
              <div className={styles.targetRow} key={item.agent.id}>
                <Checkbox
                  checked={item.checked}
                  disabled={item.agent.status !== "ONLINE"}
                  onChange={(event) => updateTarget(item.agent.id, { checked: event.target.checked })}
                  aria-label={`选择 ${item.agent.hostname || item.agent.id}`}
                />
                <div className={styles.targetAgentCol}>
                  <div className={styles.targetAgent}>{item.agent.hostname || item.agent.id}</div>
                  <div className={styles.targetMeta}>
                    {item.agent.ip_addr || item.agent.id} · {item.agent.status === "ONLINE" ? "在线" : "离线"}
                  </div>
                </div>
                <div className={styles.targetPicker}>
                  {item.agent.status !== "ONLINE" ? (
                    <Typography.Text type="secondary">离线</Typography.Text>
                  ) : result && result.status === "DONE" && result.processes.length ? (
                    <Radio.Group
                      value={item.pid}
                      onChange={(event) => updateTarget(item.agent.id, { pid: event.target.value })}
                      aria-label={`${item.agent.hostname || item.agent.id} 候选进程`}
                    >
                      <Space direction="vertical" size={2}>
                        {result.processes.slice(0, 8).map((proc) => {
                          const { title, detail } = describeProcess(proc);
                          return (
                            <Radio key={proc.pid} value={proc.pid}>
                              <span className={styles.candidateTitle}>{title}</span>
                              <span className={styles.candidateDetail}> {detail}</span>
                            </Radio>
                          );
                        })}
                        {result.processes.length > 8 && (
                          <Typography.Text type="secondary">… 还有 {result.processes.length - 8} 个候选，可手动输入 PID</Typography.Text>
                        )}
                      </Space>
                    </Radio.Group>
                  ) : result && result.status === "DONE" ? (
                    <Typography.Text type="secondary">没有匹配进程，可手动填写 PID</Typography.Text>
                  ) : result && result.status === "ERROR" ? (
                    <Typography.Text type="danger">{result.message}</Typography.Text>
                  ) : scanning ? (
                    <Spin size="small" />
                  ) : (
                    <Typography.Text type="secondary">点击“搜索候选进程”列出目标</Typography.Text>
                  )}
                  <InputNumber
                    min={1}
                    max={4194304}
                    value={item.pid}
                    disabled={!item.checked}
                    placeholder="或手动填 PID"
                    style={{ width: 150, marginTop: 6 }}
                    onChange={(value) => updateTarget(item.agent.id, { pid: value })}
                    aria-label={`${item.agent.hostname || item.agent.id} 手动 PID`}
                  />
                </div>
              </div>
            );
          })}
        </div>

        <Form.Item
          name="recovery_goal"
          label="完成条件"
          rules={[{ required: true, min: 3, message: "请输入完成条件" }]}
          style={{ marginTop: 14 }}
        >
          <Input placeholder="例如：确认 CPU 根因并给出可验证的处理建议" />
        </Form.Item>

        <Typography.Text strong>服务关系</Typography.Text>
        <Typography.Paragraph type="secondary" style={{ margin: "3px 0 8px" }}>
          可选。用于判断上下游影响。
        </Typography.Paragraph>
        <Form.List name="dependencies">
          {(fields, { add, remove }) => (
            <Space direction="vertical" style={{ width: "100%" }}>
              {fields.map((field) => (
                <div className={styles.relationRow} key={field.key}>
                  <Form.Item name={[field.name, "source_service"]} style={{ marginBottom: 0 }}>
                    <Input placeholder="上游服务" />
                  </Form.Item>
                  <Form.Item name={[field.name, "relation"]} initialValue="CALLS" style={{ marginBottom: 0 }}>
                    <Select options={RELATION_OPTIONS} />
                  </Form.Item>
                  <Form.Item name={[field.name, "target_service"]} style={{ marginBottom: 0 }}>
                    <Input placeholder="下游服务" />
                  </Form.Item>
                  <Button type="text" danger icon={<MinusCircleOutlined />} onClick={() => remove(field.name)} aria-label="删除服务关系" />
                </div>
              ))}
              <Button type="dashed" icon={<PlusOutlined />} onClick={() => add({ relation: "CALLS", confidence: "medium", source: "user_confirmed" })}>
                添加关系
              </Button>
            </Space>
          )}
        </Form.List>
        <div className={styles.safetyNote}>保存服务关系不会授权采集关联服务；扫描只读取进程列表，不采集任何数据。</div>
      </Form>
    </Modal>
  );
}
