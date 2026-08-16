import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Empty,
  InputNumber,
  Modal,
  Select,
  Space,
  Spin,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import {
  DownloadOutlined,
  EyeOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
} from "@ant-design/icons";
import { Link } from "react-router-dom";
import {
  createTask,
  downloadTaskArtifact,
  getTaskArtifacts,
} from "../../api/client";
import TaskVisualizationPreview from "../../components/TaskVisualizationPreview";
import { COLLECTOR_OPTIONS, collectorMeta } from "../../utils/collectors";
import { ACTIVE_TASK_STATUSES } from "../../utils/status";
import styles from "../AIDiagnosis.module.css";
import {
  downloadBlob,
  formatTime,
  groupTasks,
  itemId,
  newCollectionId,
} from "./workspaceUtils";

const SUCCESS = new Set(["DONE"]);

function groupStatus(group) {
  if (group.tasks.some((task) => ACTIVE_TASK_STATUSES.has(task.status))) return { label: "进行中", color: "processing" };
  if (group.tasks.every((task) => SUCCESS.has(task.status))) return { label: "完整", color: "green" };
  if (group.tasks.some((task) => task.status === "FAILED")) return { label: "部分失败", color: "red" };
  if (group.tasks.some((task) => task.status === "CANCELLED")) return { label: "部分取消", color: "orange" };
  return { label: "已结束", color: "default" };
}

export default function DiagnosisDataConsole({
  agents,
  tasks,
  currentCase,
  loading,
  actionLoading,
  focusCollectionId,
  onFocusConsumed,
  onRefresh,
  onAnalyze,
}) {
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [downloading, setDownloading] = useState("");
  const [collector, setCollector] = useState("sys_metrics");
  const [duration, setDuration] = useState(30);
  const [targets, setTargets] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [caseOnly, setCaseOnly] = useState(Boolean(currentCase));
  const pendingCollectionId = useRef("");

  const groups = useMemo(() => groupTasks(tasks), [tasks]);
  const visibleGroups = useMemo(() => (
    caseOnly && currentCase ? groups.filter((group) => group.caseId === currentCase.case_id) : groups
  ), [caseOnly, currentCase, groups]);
  const selected = visibleGroups.find((group) => group.collectionId === selectedId) || visibleGroups[0] || null;

  useEffect(() => {
    if (focusCollectionId && groups.some((item) => item.collectionId === focusCollectionId)) {
      setCaseOnly(false);
      setSelectedId(focusCollectionId);
      onFocusConsumed?.();
    }
  }, [focusCollectionId, groups, onFocusConsumed]);

  useEffect(() => {
    if (selected && selected.collectionId !== selectedId) setSelectedId(selected.collectionId);
  }, [selected, selectedId]);

  function openCreate() {
    const scoped = new Map((currentCase?.target_scope?.instances || []).map((item) => [item.agent_id, item]));
    setTargets(agents.map((agent) => ({
      agent,
      checked: agent.status === "ONLINE" && (scoped.size === 0 || scoped.has(agent.id)),
      pid: scoped.get(agent.id)?.pid || null,
    })));
    pendingCollectionId.current = newCollectionId();
    setCreateOpen(true);
  }

  function updateTarget(agentId, patch) {
    setTargets((items) => items.map((item) => item.agent.id === agentId ? { ...item, ...patch } : item));
  }

  async function createCollection() {
    const selectedTargets = targets.filter((item) => item.checked);
    if (!selectedTargets.length) return message.warning("请选择 Worker");
    if (selectedTargets.some((item) => !Number(item.pid))) return message.warning("请填写每个 Worker 的 PID");
    const unsupported = selectedTargets.find((item) => !(item.agent.capabilities || []).includes(collector));
    if (unsupported) return message.error(`${unsupported.agent.hostname || unsupported.agent.id} 不支持 ${collectorMeta(collector).label}`);
    const collectionId = pendingCollectionId.current || newCollectionId();
    pendingCollectionId.current = collectionId;
    const serviceId = currentCase?.target_scope?.service_id || "manual";
    const meta = collectorMeta(collector);
    setCreating(true);
    try {
      const results = await Promise.allSettled(selectedTargets.map((item) => createTask({
        name: `${meta.label} · ${serviceId} · ${item.agent.hostname || item.agent.id}`,
        agent_id: item.agent.id,
        target_pid: Number(item.pid),
        collector_type: collector,
        sample_rate: meta.defaultSampleRate,
        duration_sec: duration,
        options: {
          source: "diagnosis_data_console",
          collection_session_id: collectionId,
          case_id: currentCase?.case_id || "",
          service_id: serviceId,
        },
      }, `collection-${collectionId}-${item.agent.id}-${collector}`)));
      const succeeded = results.filter((result) => result.status === "fulfilled").length;
      const failed = results.length - succeeded;
      if (!succeeded) {
        const reason = results.find((result) => result.status === "rejected")?.reason?.message || "请求失败";
        throw new Error(reason);
      }
      setCreateOpen(false);
      setCaseOnly(Boolean(currentCase));
      setSelectedId(collectionId);
      if (failed) message.warning(`已创建 ${succeeded} 个任务，${failed} 个节点失败；已保留为部分采集批次`);
      else message.success(`已创建 ${succeeded} 个采集任务`);
      await onRefresh();
    } catch (error) {
      message.error(`创建失败：${error.message}`);
    } finally {
      setCreating(false);
    }
  }

  async function downloadGroup(group) {
    setDownloading(group.collectionId);
    let count = 0;
    let failed = 0;
    for (const task of group.tasks) {
      const taskId = itemId(task);
      try {
        const artifacts = await getTaskArtifacts(taskId);
        for (const artifact of artifacts || []) {
          const availability = String(artifact.availability || "").toLowerCase();
          if (artifact.available === false || ["missing", "unavailable"].includes(availability)) continue;
          try {
            const result = await downloadTaskArtifact(taskId, artifact.artifact_type, {
              ...(artifact.metadata?.window_index !== undefined ? { index: artifact.metadata.window_index } : {}),
            });
            downloadBlob(result.blob, `${task.agent_id}-${result.filename}`);
            count += 1;
          } catch {
            failed += 1;
          }
        }
      } catch {
        failed += 1;
      }
    }
    if (!count && !failed) message.info("没有可下载的产物");
    else if (failed) message.warning(`已下载 ${count} 个产物，${failed} 项获取失败，可稍后重试`);
    setDownloading("");
  }

  const onlineAgents = agents.filter((agent) => agent.status === "ONLINE");

  return (
    <section className={styles.console}>
      <header className={styles.consoleHeader}>
        <div className={styles.consoleTitleRow}>
          <h1 className={styles.consoleTitle}>诊断数据台</h1>
          <Tag>人工操作</Tag>
          {currentCase && <Tag color="blue">当前会话：{currentCase.title}</Tag>}
          <div className={styles.consoleActions}>
            <Button icon={<ReloadOutlined />} loading={loading} onClick={onRefresh}>刷新</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新建多机采集</Button>
          </div>
        </div>
        <div className={styles.consoleFilters}>
          <div className={styles.filterField}>
            <div className={styles.filterLabel}>显示范围</div>
            <Select
              value={caseOnly && currentCase ? "case" : "all"}
              style={{ width: "100%" }}
              onChange={(value) => setCaseOnly(value === "case")}
              options={[
                ...(currentCase ? [{ value: "case", label: "当前会话的数据" }] : []),
                { value: "all", label: "全部采集数据" },
              ]}
            />
          </div>
          <Typography.Text type="secondary">{onlineAgents.length}/{agents.length} 个 Worker 在线</Typography.Text>
        </div>
      </header>

      <div className={styles.consoleBody}>
        <aside className={styles.consoleList}>
          <div className={styles.workerCards}>
            {agents.map((agent) => (
              <div className={styles.workerCard} key={agent.id}>
                <div className={styles.workerCardTop}>
                  <span className={`${styles.statusDot} ${agent.status === "ONLINE" ? styles.statusOnline : ""}`} />
                  <span className={styles.workerCardName}>{agent.hostname || agent.id}</span>
                  <Tag color={agent.status === "ONLINE" ? "green" : "default"}>{agent.status === "ONLINE" ? "在线" : "离线"}</Tag>
                </div>
                <div className={styles.workerCardMeta}>{agent.ip_addr || agent.id} · {(agent.capabilities || []).length} 项能力</div>
              </div>
            ))}
          </div>
          <Spin spinning={loading}>
            {visibleGroups.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={caseOnly ? "当前会话还没有人工采集" : "还没有采集任务"} />
            ) : visibleGroups.map((group) => {
              const state = groupStatus(group);
              const meta = [...new Set(group.tasks.map((task) => collectorMeta(task.collector_type).label))];
              return (
                <div
                  key={group.collectionId}
                  className={`${styles.sessionCard} ${selected?.collectionId === group.collectionId ? styles.sessionCardActive : ""}`}
                  onClick={() => setSelectedId(group.collectionId)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setSelectedId(group.collectionId);
                    }
                  }}
                  role="button"
                  tabIndex={0}
                >
                  <div className={styles.sessionCardTop}><span className={styles.sessionId}>{group.collectionId}</span><Tag color={state.color}>{state.label}</Tag></div>
                  <div className={styles.sessionMeta}>{meta.join("、")} · {group.tasks.length} 个节点 · {formatTime(group.createdAt, true)}</div>
                  <div className={styles.sessionActions} onClick={(event) => event.stopPropagation()}>
                    <Button size="small" icon={<EyeOutlined />} onClick={() => setSelectedId(group.collectionId)}>预览</Button>
                    <Button size="small" icon={<DownloadOutlined />} loading={downloading === group.collectionId} onClick={() => downloadGroup(group)}>下载</Button>
                    <Button
                      size="small"
                      type="primary"
                      icon={<RobotOutlined />}
                      disabled={!currentCase || !group.tasks.every((task) => SUCCESS.has(task.status))}
                      loading={actionLoading}
                      onClick={() => onAnalyze(group)}
                    >
                      用该批次更新诊断
                    </Button>
                  </div>
                </div>
              );
            })}
          </Spin>
        </aside>

        <main className={styles.consolePreview}>
          {!selected ? (
            <div className={styles.previewPanel}><Empty description="选择一个采集会话" /></div>
          ) : (
            <div className={styles.previewPanel}>
              <div className={styles.previewHeader}>
                <div className={styles.previewHeaderMain}>
                  <div className={styles.previewTitle}>{selected.collectionId}</div>
                  <div className={styles.previewMeta}>{selected.tasks.length} 个任务 · 原始数据、图表和火焰图均从任务产物读取</div>
                </div>
                <Button icon={<DownloadOutlined />} loading={downloading === selected.collectionId} onClick={() => downloadGroup(selected)}>下载全部</Button>
                <Button type="primary" icon={<RobotOutlined />} disabled={!currentCase || !selected.tasks.every((task) => SUCCESS.has(task.status))} loading={actionLoading} onClick={() => onAnalyze(selected)}>用该批次更新诊断</Button>
              </div>
              <Tabs items={selected.tasks.map((task) => ({
                key: itemId(task),
                label: `${task.agent_id} · ${collectorMeta(task.collector_type).label}`,
                children: (
                  <Space direction="vertical" size={10} style={{ width: "100%" }}>
                    <Space wrap>
                      <Tag>{task.status}</Tag>
                      <Typography.Text>PID {task.target_pid}</Typography.Text>
                      <Link to={`/task/${itemId(task)}`}>打开完整结果</Link>
                    </Space>
                    <TaskVisualizationPreview taskId={itemId(task)} revision={task.updated_at || task.status} />
                  </Space>
                ),
              }))} />
            </div>
          )}
        </main>
      </div>

      <Modal
        title="新建多机采集"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={createCollection}
        okText="创建采集"
        confirmLoading={creating}
        width={720}
        destroyOnHidden
      >
        <Alert type="info" showIcon message="每个 Worker 创建一个任务，并使用同一个采集会话 ID。" style={{ marginBottom: 14 }} />
        <Space direction="vertical" size={14} style={{ width: "100%" }}>
          <div>
            <Typography.Text strong>采集器</Typography.Text>
            <Select value={collector} onChange={setCollector} options={COLLECTOR_OPTIONS} style={{ width: "100%", marginTop: 5 }} />
            <Typography.Text type="secondary">{collectorMeta(collector).description}</Typography.Text>
          </div>
          <div>
            <Typography.Text strong>时长</Typography.Text>
            <InputNumber min={1} max={120} value={duration} onChange={(value) => setDuration(value || 15)} suffix="秒" style={{ width: 180, marginLeft: 10 }} />
          </div>
          <div>
            <Typography.Text strong>Worker 与 PID</Typography.Text>
            <div className={styles.targetList} style={{ marginTop: 7 }}>
              {targets.map((item) => (
                <div className={styles.targetRow} key={item.agent.id}>
                  <Checkbox checked={item.checked} disabled={item.agent.status !== "ONLINE"} onChange={(event) => updateTarget(item.agent.id, { checked: event.target.checked })} aria-label={`采集 ${item.agent.hostname || item.agent.id}`} />
                  <div><div className={styles.targetAgent}>{item.agent.hostname || item.agent.id}</div><div className={styles.targetMeta}>{item.agent.ip_addr || item.agent.id} · {item.agent.status === "ONLINE" ? "在线" : "离线"}</div></div>
                  <InputNumber min={1} max={4194304} placeholder="目标 PID" value={item.pid} disabled={!item.checked} onChange={(value) => updateTarget(item.agent.id, { pid: value })} style={{ width: "100%" }} aria-label={`${item.agent.hostname || item.agent.id} 采集 PID`} />
                </div>
              ))}
            </div>
          </div>
          <div className={styles.safetyNote}>只创建你确认的任务。需要审批的采集仍按后端策略处理。</div>
        </Space>
      </Modal>
    </section>
  );
}
