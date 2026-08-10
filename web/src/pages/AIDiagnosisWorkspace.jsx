import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Typography,
  message,
} from "antd";
import {
  DownOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  MessageOutlined,
  PlusOutlined,
  RightOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import {
  appendIncidentCaseMessage,
  approveDiagnosisProbe,
  correctIncidentCase,
  createCaseRecoveryPlan,
  createIncidentCase,
  createServiceChange,
  createTargetSession,
  decideCaseRecoveryPlan,
  dryRunCaseRecoveryPlan,
  executeCaseRecoveryPlan,
  getDiagnosisSession,
  getCaseCurrentUnderstanding,
  getIncidentCase,
  getTask,
  listAgents,
  listDiagnosisSessions,
  listIncidentCaseEvents,
  listIncidentCases,
  listCaseProposals,
  listCaseRecoveryPlans,
  listRegisteredActions,
  listTasks,
  listTargetSessions,
  runAIValidation,
  startIncidentCaseDiagnosis,
  transitionIncidentCase,
  verifyCaseRecoveryPlan,
  rollbackCaseRecoveryPlan,
} from "../api/client";
import { useSearchParams } from "react-router-dom";
import styles from "./AIDiagnosis.module.css";
import CaseConversation, { LegacyConversation } from "./ai-workspace/CaseConversation";
import DiagnosisDataConsole from "./ai-workspace/DiagnosisDataConsole";
import DiagnosisTechnicalDrawer from "./ai-workspace/DiagnosisTechnicalDrawer";
import ScopeEditorModal from "./ai-workspace/ScopeEditorModal";
import WorkerStatus from "./ai-workspace/WorkerStatus";
import {
  CASE_STATE_META,
  DIAGNOSIS_STATUS_META,
  TERMINAL_DIAGNOSIS,
  buildInstancesFromTasks,
  caseHasInstances,
  formatTime,
  shortTitle,
  uniqueInstances,
} from "./ai-workspace/workspaceUtils";

function statusClass(tone) {
  if (tone === "online") return styles.statusOnline;
  if (tone === "busy") return styles.statusBusy;
  if (tone === "waiting") return styles.statusWaiting;
  if (tone === "error") return styles.statusError;
  return "";
}

function createCaseTitle(problem, serviceId) {
  const summary = shortTitle(problem) || "新诊断";
  return serviceId ? `${serviceId} · ${summary}`.slice(0, 256) : summary;
}

export default function AIDiagnosisWorkspace() {
  const [mode, setMode] = useState("ai");
  const [agents, setAgents] = useState([]);
  const [cases, setCases] = useState([]);
  const [legacySessions, setLegacySessions] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [selectedKey, setSelectedKey] = useState("");
  const [caseDetail, setCaseDetail] = useState(null);
  const [events, setEvents] = useState([]);
  const [diagnosis, setDiagnosis] = useState(null);
  const [currentUnderstanding, setCurrentUnderstanding] = useState(null);
  const [proposals, setProposals] = useState([]);
  const [recoveryPlans, setRecoveryPlans] = useState([]);
  const [registeredActions, setRegisteredActions] = useState([]);
  const [targetSessions, setTargetSessions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [workerLoading, setWorkerLoading] = useState(false);
  const [validationLoading, setValidationLoading] = useState(false);
  const [messageText, setMessageText] = useState("");
  const [searchText, setSearchText] = useState("");
  const [legacyOpen, setLegacyOpen] = useState(false);
  const [newOpen, setNewOpen] = useState(false);
  const [scopeOpen, setScopeOpen] = useState(false);
  const [technicalOpen, setTechnicalOpen] = useState(false);
  const [changeOpen, setChangeOpen] = useState(false);
  const [recoveryOpen, setRecoveryOpen] = useState(false);
  const [targetOpen, setTargetOpen] = useState(false);
  const [focusCollectionId, setFocusCollectionId] = useState("");
  const [scopeAutoSearch, setScopeAutoSearch] = useState(false);
  const [newForm] = Form.useForm();
  const [changeForm] = Form.useForm();
  const [recoveryForm] = Form.useForm();
  const [targetForm] = Form.useForm();
  const [searchParams, setSearchParams] = useSearchParams();
  const handledFromTask = useRef("");

  const refreshLists = useCallback(async ({ quiet = false } = {}) => {
    if (!quiet) setLoading(true);
    const results = await Promise.allSettled([
      listAgents(),
      listIncidentCases({ limit: 200 }),
      listDiagnosisSessions({ limit: 50 }),
      listTasks({ limit: 200 }),
      listRegisteredActions(),
      listTargetSessions({ limit: 200 }),
    ]);
    const [agentResult, caseResult, sessionResult, taskResult, actionResult, targetResult] = results;
    if (agentResult.status === "fulfilled") setAgents(agentResult.value || []);
    if (caseResult.status === "fulfilled") setCases(caseResult.value?.items || []);
    if (sessionResult.status === "fulfilled") setLegacySessions(sessionResult.value || []);
    if (taskResult.status === "fulfilled") setTasks(taskResult.value || []);
    if (actionResult.status === "fulfilled") setRegisteredActions(
      (actionResult.value?.items || []).filter((item) => item.implementation_status === "executable"),
    );
    if (targetResult.status === "fulfilled") setTargetSessions(targetResult.value || []);
    const firstError = results.find((item) => item.status === "rejected");
    if (firstError && !quiet) message.error(`加载失败：${firstError.reason?.message || "请求失败"}`);

    const nextCases = caseResult.status === "fulfilled" ? caseResult.value?.items || [] : [];
    const nextSessions = sessionResult.status === "fulfilled" ? sessionResult.value || [] : [];
    setSelectedKey((current) => {
      if (current.startsWith("case:") && nextCases.some((item) => `case:${item.case_id}` === current)) return current;
      if (current.startsWith("diagnosis:") && nextSessions.some((item) => `diagnosis:${item.diagnosis_id}` === current)) return current;
      if (nextCases[0]) return `case:${nextCases[0].case_id}`;
      return "";
    });
    if (!quiet) setLoading(false);
  }, []);

  const loadSelection = useCallback(async (key, { quiet = false } = {}) => {
    if (!key) {
      setCaseDetail(null);
      setDiagnosis(null);
      setCurrentUnderstanding(null);
      setProposals([]);
      setRecoveryPlans([]);
      setEvents([]);
      return;
    }
    if (!quiet) setDetailLoading(true);
    try {
      if (key.startsWith("case:")) {
        const caseId = key.slice(5);
        const [detail, eventResult, understandingResult, proposalResult, recoveryResult] = await Promise.all([
          getIncidentCase(caseId),
          listIncidentCaseEvents(caseId, { limit: 300 }),
          getCaseCurrentUnderstanding(caseId),
          listCaseProposals(caseId),
          listCaseRecoveryPlans(caseId),
        ]);
        setCaseDetail(detail);
        setEvents(eventResult.items || []);
        setCurrentUnderstanding(understandingResult.current_understanding || null);
        setProposals(proposalResult.proposals || []);
        setRecoveryPlans(recoveryResult.items || []);
        if (detail.diagnosis_session_id) {
          setDiagnosis(await getDiagnosisSession(detail.diagnosis_session_id));
        } else {
          setDiagnosis(null);
        }
      } else {
        setCaseDetail(null);
        setEvents([]);
        setCurrentUnderstanding(null);
        setProposals([]);
        setRecoveryPlans([]);
        setDiagnosis(await getDiagnosisSession(key.slice(10)));
      }
    } catch (error) {
      if (!quiet) message.error(`加载会话失败：${error.message}`);
    } finally {
      if (!quiet) setDetailLoading(false);
    }
  }, []);

  useEffect(() => { refreshLists(); }, [refreshLists]);
  useEffect(() => { loadSelection(selectedKey); }, [loadSelection, selectedKey]);
  useEffect(() => {
    if (!selectedKey) return undefined;
    const timer = window.setInterval(() => {
      refreshLists({ quiet: true });
      loadSelection(selectedKey, { quiet: true });
    }, 5000);
    return () => window.clearInterval(timer);
  }, [loadSelection, refreshLists, selectedKey]);

  const filteredCases = useMemo(() => {
    const query = searchText.trim().toLowerCase();
    return cases.filter((item) => !query || [
      item.title,
      item.problem_description,
      item.target_scope?.service_id,
      item.environment,
    ].some((value) => String(value || "").toLowerCase().includes(query)));
  }, [cases, searchText]);
  const activeCases = filteredCases.filter((item) => !["RESOLVED", "STOPPED"].includes(item.state));
  const completedCases = filteredCases.filter((item) => ["RESOLVED", "STOPPED"].includes(item.state));
  const unlinkedSessions = legacySessions.filter((session) => !cases.some((item) => item.diagnosis_session_id === session.diagnosis_id));

  async function refreshAll() {
    await refreshLists();
    await loadSelection(selectedKey);
  }

  async function refreshWorkers() {
    setWorkerLoading(true);
    try { setAgents(await listAgents()); } catch (error) { message.error(error.message); } finally { setWorkerLoading(false); }
  }

  async function createCase() {
    const values = await newForm.validateFields();
    setActionLoading(true);
    try {
      const serviceId = values.service_id?.trim() || "";
      const selectedTarget = targetSessions.find(
        (item) => item.target_session_id === values.target_session_id,
      );
      const created = await createIncidentCase({
        title: createCaseTitle(values.problem_description, serviceId),
        problem_description: values.problem_description.trim(),
        recovery_goal: values.recovery_goal?.trim() || "确认原因并给出安全处置建议",
        run_mode: "COLLABORATE",
        environment: selectedTarget?.environment || values.environment || "production",
        target_scope: selectedTarget?.target_scope
          || (serviceId ? { service_id: serviceId, instances: [], dependencies: [] } : {}),
        target_session_id: selectedTarget?.target_session_id,
      });
      setNewOpen(false);
      newForm.resetFields();
      await refreshLists({ quiet: true });
      setSelectedKey(`case:${created.case_id}`);
      message.success("诊断会话已创建");
      // 无实例时自动弹出范围选择并自动搜索进程，基础用户只需确认候选
      if (!caseHasInstances(created)) {
        setScopeAutoSearch(true);
        window.setTimeout(() => setScopeOpen(true), 150);
      }
    } catch (error) {
      message.error(`创建失败：${error.message}`);
    } finally {
      setActionLoading(false);
    }
  }

  async function createLongLivedTarget() {
    const values = await targetForm.validateFields();
    setActionLoading(true);
    try {
      const created = await createTargetSession({
        service_id: values.service_id.trim(),
        environment: values.environment,
        display_name: values.display_name?.trim() || undefined,
        target_scope: {
          service_id: values.service_id.trim(),
          instances: [],
          dependencies: [],
        },
      });
      targetForm.resetFields();
      await refreshLists({ quiet: true });
      setTargetOpen(false);
      newForm.setFieldValue("target_session_id", created.target_session_id);
      setNewOpen(true);
      message.success("长期目标已创建，可直接发起诊断");
    } catch (error) {
      message.error(`创建长期目标失败：${error.message}`);
    } finally {
      setActionLoading(false);
    }
  }

  function openChangeRegistration() {
    if (!caseDetail) return;
    changeForm.setFieldsValue({
      service_id: caseDetail.target_scope?.service_id || "",
      environment: caseDetail.environment || "production",
      change_type: "release",
    });
    setChangeOpen(true);
  }

  async function registerChange() {
    const values = await changeForm.validateFields();
    setActionLoading(true);
    try {
      await createServiceChange({
        ...values,
        changed_at: new Date(values.changed_at).toISOString(),
      });
      setChangeOpen(false);
      changeForm.resetFields();
      await loadSelection(selectedKey, { quiet: true });
      message.success("变更已登记，后续诊断会作为待验证相关性使用");
    } catch (error) {
      message.error(`登记失败：${error.message}`);
    } finally {
      setActionLoading(false);
    }
  }

  function openRecoveryPlan() {
    const preferred = registeredActions.find(
      (item) => item.action_id === "mini-drop.cleanup-expired-cache",
    ) || registeredActions[0];
    recoveryForm.setFieldsValue({
      action_id: preferred?.action_id,
      retention_days: 7,
      value_after_fix: "释放过期诊断缓存占用，同时保留可恢复副本",
      verification_method: "由服务端确认源目录消失且隔离区副本存在",
    });
    setRecoveryOpen(true);
  }

  async function createRecoveryPlan() {
    if (!caseDetail) return;
    const values = await recoveryForm.validateFields();
    setActionLoading(true);
    try {
      await createCaseRecoveryPlan(caseDetail.case_id, {
        action_id: values.action_id,
        parameters: values.action_id === "mini-drop.cleanup-expired-cache"
          ? { retention_days: Number(values.retention_days || 7) }
          : {},
        value_after_fix: values.value_after_fix,
        verification_method: values.verification_method,
        expected_case_version: caseDetail.row_version,
      });
      setRecoveryOpen(false);
      recoveryForm.resetFields();
      await loadSelection(selectedKey);
      message.success("恢复方案已创建，请先执行只读预检");
    } catch (error) {
      message.error(`创建恢复方案失败：${error.message}`);
    } finally {
      setActionLoading(false);
    }
  }

  async function recoveryPlanAction(plan, action) {
    if (!caseDetail) return;
    setActionLoading(true);
    try {
      const version = { expected_plan_version: plan.row_version };
      if (action === "dry-run") await dryRunCaseRecoveryPlan(caseDetail.case_id, plan.recovery_plan_id, version);
      if (action === "approve") await decideCaseRecoveryPlan(caseDetail.case_id, plan.recovery_plan_id, { ...version, decision: "approve", reason: "用户已核对影响清单与回滚路径" });
      if (action === "reject") await decideCaseRecoveryPlan(caseDetail.case_id, plan.recovery_plan_id, { ...version, decision: "reject", reason: "用户拒绝本次恢复动作" });
      if (action === "execute") await executeCaseRecoveryPlan(caseDetail.case_id, plan.recovery_plan_id, version);
      if (action === "verify") await verifyCaseRecoveryPlan(caseDetail.case_id, plan.recovery_plan_id, version);
      if (action === "rollback") await rollbackCaseRecoveryPlan(caseDetail.case_id, plan.recovery_plan_id, version);
      await loadSelection(selectedKey);
      message.success("恢复方案状态已更新");
    } catch (error) {
      message.error(`恢复操作失败：${error.message}`);
    } finally {
      setActionLoading(false);
    }
  }

  // ── 从第一页“交给 AI 分析”进入 ────────────────────────

  async function createCaseFromTask(taskId) {
    setActionLoading(true);
    try {
      const task = await getTask(taskId);
      const agent = agents.find((item) => item.id === task.agent_id);
      const options = task.request_params?.options || task.options || {};
      const serviceId = options.service_id || "";
      const pid = Number(task.target_pid);
      if (!pid) throw new Error("该任务没有目标 PID，无法带入诊断范围");
      const instance = {
        service_id: serviceId || `${task.agent_id}-${pid}`,
        instance_id: `${serviceId || "service"}-${task.agent_id}-${pid}`.slice(0, 128),
        host_id: agent?.hostname || task.agent_id,
        agent_id: task.agent_id,
        pid,
        environment: "production",
      };
      const title = `分析采集任务：${(task.name || taskId).slice(0, 80)}`;
      const created = await createIncidentCase({
        title,
        problem_description: `对采集任务 ${taskId} 的结果进行归因分析。采集器 ${task.collector_type}，目标 PID ${pid}。`,
        recovery_goal: "确认原因并给出可验证的处理建议",
        run_mode: "COLLABORATE",
        environment: "production",
        target_scope: {
          service_id: instance.service_id,
          instances: [instance],
          dependencies: [],
        },
        initial_tasks: [taskId],
      });
      await refreshLists({ quiet: true });
      setSelectedKey(`case:${created.case_id}`);
      message.success("已从采集任务创建诊断会话");
      return created;
    } catch (error) {
      message.error(`从任务创建诊断失败：${error.message}`);
      return null;
    } finally {
      setActionLoading(false);
    }
  }

  // 处理来自第一页的 ?fromTask= 上下文（人工点击“交给 AI 分析”）
  useEffect(() => {
    const fromTask = searchParams.get("fromTask") || "";
    if (!fromTask || handledFromTask.current === fromTask) return;
    handledFromTask.current = fromTask;
    setSearchParams({}, { replace: true });
    // 等会话列表加载完成后再创建，避免与默认选中冲突
    window.setTimeout(() => { createCaseFromTask(fromTask); }, 300);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  async function startDiagnosis(base = caseDetail) {
    if (!base) return;
    if (!caseHasInstances(base)) {
      setScopeAutoSearch(true);
      setScopeOpen(true);
      message.info("请确认目标进程（已自动搜索候选）");
      return;
    }
    setActionLoading(true);
    try {
      await startIncidentCaseDiagnosis(base.case_id, {
        expected_row_version: base.row_version,
        analysis_strategy: "CONSTRAINED_HYBRID",
        budget_profile: "production_safe",
      });
      await Promise.all([refreshLists({ quiet: true }), loadSelection(`case:${base.case_id}`)]);
      message.success("诊断已开始");
    } catch (error) {
      message.error(`启动失败：${error.message}`);
      await loadSelection(`case:${base.case_id}`, { quiet: true });
    } finally {
      setActionLoading(false);
    }
  }

  async function saveScope(values, startAfter) {
    if (!caseDetail) return;
    setActionLoading(true);
    try {
      const updated = await correctIncidentCase(caseDetail.case_id, {
        target_scope: values.targetScope,
        environment: values.environment,
        recovery_goal: values.recoveryGoal,
        reason: "用户确认诊断范围和服务关系",
        expected_row_version: caseDetail.row_version,
      });
      setScopeOpen(false);
      setCaseDetail(updated);
      await refreshLists({ quiet: true });
      if (startAfter) {
        await startIncidentCaseDiagnosis(updated.case_id, {
          expected_row_version: updated.row_version,
          analysis_strategy: "CONSTRAINED_HYBRID",
          budget_profile: "production_safe",
        });
      }
      await loadSelection(`case:${updated.case_id}`);
      message.success(startAfter ? "范围已保存，诊断已开始" : "范围已保存");
    } catch (error) {
      message.error(`保存失败：${error.message}`);
      await loadSelection(`case:${caseDetail.case_id}`, { quiet: true });
    } finally {
      setActionLoading(false);
    }
  }

  async function sendAndAnalyze() {
    const content = messageText.trim();
    if (!content || !caseDetail) return;
    setActionLoading(true);
    try {
      await appendIncidentCaseMessage(caseDetail.case_id, { content, kind: "answer" });
      setMessageText("");
      let current = await getIncidentCase(caseDetail.case_id);
      if (!caseHasInstances(current)) {
        setCaseDetail(current);
        setScopeAutoSearch(true);
        setScopeOpen(true);
        await loadSelection(`case:${current.case_id}`);
        message.info("信息已保存。请确认目标进程（已自动搜索候选）");
        return;
      }
      current = await correctIncidentCase(current.case_id, {
        target_scope: current.target_scope,
        reason: "用户补充信息，重新分析",
        expected_row_version: current.row_version,
      });
      await startIncidentCaseDiagnosis(current.case_id, {
        expected_row_version: current.row_version,
        analysis_strategy: "CONSTRAINED_HYBRID",
        budget_profile: "production_safe",
      });
      await Promise.all([refreshLists({ quiet: true }), loadSelection(`case:${current.case_id}`)]);
    } catch (error) {
      message.error(`发送失败：${error.message}`);
      await loadSelection(`case:${caseDetail.case_id}`, { quiet: true });
    } finally {
      setActionLoading(false);
    }
  }

  async function decideProbe(stepId, decision) {
    if (!diagnosis) return;
    setActionLoading(true);
    try {
      setDiagnosis(await approveDiagnosisProbe(diagnosis.diagnosis_id, {
        step_id: stepId,
        decision,
        scope: "single_execution",
        approver_id: "web_user",
      }));
      message.success(decision === "approve" ? "已批准一次" : "已拒绝");
    } catch (error) {
      message.error(error.message);
    } finally {
      setActionLoading(false);
    }
  }

  async function transition(action) {
    if (!caseDetail) return;
    setActionLoading(true);
    const reason = action === "pause" ? "用户暂停诊断" : action === "resume" ? "用户继续诊断" : action === "resolve" ? "用户确认问题已解决" : "用户停止诊断";
    try {
      await transitionIncidentCase(caseDetail.case_id, action, { reason, expected_row_version: caseDetail.row_version });
      await Promise.all([refreshLists({ quiet: true }), loadSelection(`case:${caseDetail.case_id}`)]);
      message.success(action === "resolve" ? "已标记为解决" : "状态已更新");
    } catch (error) {
      message.error(`操作失败：${error.message}`);
      await loadSelection(`case:${caseDetail.case_id}`, { quiet: true });
    } finally {
      setActionLoading(false);
    }
  }

  async function analyzeCollection(group) {
    if (!caseDetail) return message.warning("请先选择一个诊断会话");
    setActionLoading(true);
    try {
      await appendIncidentCaseMessage(caseDetail.case_id, {
        content: `[collection:${group.collectionId}] 请比较这次多机采集数据并更新结论。`,
        kind: "explanation_request",
      });
      let current = await getIncidentCase(caseDetail.case_id);
      const linkedInstances = buildInstancesFromTasks(group.tasks, agents, current);
      current = await correctIncidentCase(current.case_id, {
        target_scope: {
          ...current.target_scope,
          instances: uniqueInstances([...(current.target_scope?.instances || []), ...linkedInstances]),
        },
        reason: `关联采集会话 ${group.collectionId}`,
        expected_row_version: current.row_version,
      });
      await startIncidentCaseDiagnosis(current.case_id, {
        expected_row_version: current.row_version,
        analysis_strategy: "CONSTRAINED_HYBRID",
        budget_profile: "production_safe",
      });
      setMode("ai");
      await Promise.all([refreshLists({ quiet: true }), loadSelection(`case:${current.case_id}`)]);
      message.success("采集数据已交给当前会话分析");
    } catch (error) {
      message.error(`分析失败：${error.message}`);
      await loadSelection(`case:${caseDetail.case_id}`, { quiet: true });
    } finally {
      setActionLoading(false);
    }
  }

  function openCollection(collectionId) {
    setFocusCollectionId(collectionId);
    setMode("data");
  }

  async function validateAIService() {
    setValidationLoading(true);
    try {
      const result = await runAIValidation();
      Modal.info({
        title: "服务检测",
        width: 620,
        content: <Alert type={result.status === "PASSED" ? "success" : "warning"} showIcon message={`${result.passed_count}/${result.total_count} 项通过`} description={`${result.provider} / ${result.model} · ${result.duration_ms} ms`} />,
      });
    } catch (error) {
      message.error(`检测失败：${error.message}`);
    } finally {
      setValidationLoading(false);
    }
  }

  function renderCaseItem(item) {
    const meta = CASE_STATE_META[item.state] || { label: item.state, tone: "idle" };
    const key = `case:${item.case_id}`;
    return (
      <button type="button" className={`${styles.caseItem} ${selectedKey === key ? styles.caseItemActive : ""}`} key={key} onClick={() => setSelectedKey(key)}>
        <div className={styles.caseTitleRow}><span className={`${styles.statusDot} ${statusClass(meta.tone)}`} /><span className={styles.caseTitle}>{item.title}</span><span className={styles.caseTime}>{formatTime(item.updated_at)}</span></div>
        <div className={styles.caseMeta}>{meta.label} · {item.target_scope?.service_id || "服务未确认"}</div>
      </button>
    );
  }

  return (
    <div className={styles.page}>
      <header className={styles.toolbar}>
        <div className={styles.modeSwitch} role="tablist" aria-label="工作模式">
          <button type="button" className={`${styles.modeButton} ${mode === "ai" ? styles.modeButtonActive : ""}`} onClick={() => setMode("ai")}><MessageOutlined /> AI 协作</button>
          <button type="button" className={`${styles.modeButton} ${mode === "data" ? styles.modeButtonActive : ""}`} onClick={() => setMode("data")}><DatabaseOutlined /> 诊断数据台</button>
        </div>
        <span className={styles.toolbarHint}>{mode === "ai" ? "持续会话" : "人工采集与原始数据"}</span>
        <div className={styles.toolbarSpacer} />
        <Button size="small" onClick={() => setTargetOpen(true)}>长期目标</Button>
        {caseDetail && <Button size="small" onClick={openChangeRegistration}>登记变更</Button>}
        {caseDetail && registeredActions.length > 0 && !["RESOLVED", "STOPPED"].includes(caseDetail.state) && (
          <Button size="small" onClick={openRecoveryPlan}>恢复方案</Button>
        )}
        <Button size="small" icon={<ExperimentOutlined />} loading={validationLoading} onClick={validateAIService}>服务检测</Button>
        <WorkerStatus agents={agents} loading={workerLoading} onRefresh={refreshWorkers} />
      </header>

      <div className={styles.body}>
        <aside className={styles.rail}>
          <div className={styles.railHeader}>
            <Button block icon={<PlusOutlined />} onClick={() => setNewOpen(true)}>新建诊断</Button>
            <Input className={styles.railSearch} allowClear prefix={<SearchOutlined />} value={searchText} onChange={(event) => setSearchText(event.target.value)} placeholder="搜索会话" />
          </div>
          <div className={styles.railList}>
            <div className={styles.railLabel}>进行中</div>
            {activeCases.length ? activeCases.map(renderCaseItem) : <Typography.Text type="secondary" style={{ padding: 8, display: "block" }}>没有进行中的会话</Typography.Text>}
            {completedCases.length > 0 && <><div className={styles.railLabel}>最近完成</div>{completedCases.map(renderCaseItem)}</>}
            {unlinkedSessions.length > 0 && (
              <>
                <button type="button" className={styles.railGroupButton} aria-expanded={legacyOpen} onClick={() => setLegacyOpen((value) => !value)}>
                  {legacyOpen ? <DownOutlined /> : <RightOutlined />} 旧诊断记录 <span>{unlinkedSessions.length}</span>
                </button>
                {legacyOpen && unlinkedSessions.slice(0, 20).map((item) => {
                  const key = `diagnosis:${item.diagnosis_id}`;
                  const meta = DIAGNOSIS_STATUS_META[item.status] || { label: item.status };
                  return (
                    <button type="button" className={`${styles.caseItem} ${selectedKey === key ? styles.caseItemActive : ""}`} key={key} onClick={() => setSelectedKey(key)}>
                      <div className={styles.caseTitleRow}><span className={`${styles.statusDot} ${TERMINAL_DIAGNOSIS.has(item.status) ? styles.statusOnline : styles.statusBusy}`} /><span className={styles.caseTitle}>{item.target_scope?.target_service || shortTitle(item.raw_query)}</span></div>
                      <div className={styles.caseMeta}>{meta.label} · 只读历史</div>
                    </button>
                  );
                })}
              </>
            )}
          </div>
          <div className={styles.railFooter}>{agents.filter((item) => item.status === "ONLINE").length}/{agents.length} 个 Worker 在线</div>
        </aside>

        {mode === "data" ? (
          <DiagnosisDataConsole
            agents={agents}
            tasks={tasks}
            currentCase={caseDetail}
            loading={loading}
            actionLoading={actionLoading}
            focusCollectionId={focusCollectionId}
            onRefresh={refreshAll}
            onAnalyze={analyzeCollection}
          />
        ) : caseDetail ? (
          <CaseConversation
            detail={caseDetail}
            events={events}
            diagnosis={diagnosis}
            currentUnderstanding={currentUnderstanding}
            proposals={proposals}
            recoveryPlans={recoveryPlans}
            loading={detailLoading}
            actionLoading={actionLoading}
            messageText={messageText}
            onMessageChange={setMessageText}
            onSend={sendAndAnalyze}
            onStart={() => startDiagnosis()}
            onOpenScope={() => setScopeOpen(true)}
            onOpenTechnical={() => setTechnicalOpen(true)}
            onSwitchData={() => setMode("data")}
            onOpenCollection={openCollection}
            onDecision={decideProbe}
            onTransition={transition}
            onOpenRecovery={openRecoveryPlan}
            onRecoveryAction={recoveryPlanAction}
          />
        ) : diagnosis ? (
          <LegacyConversation
            diagnosis={diagnosis}
            loading={detailLoading}
            onOpenScope={() => setNewOpen(true)}
            onOpenTechnical={() => setTechnicalOpen(true)}
            onSwitchData={() => setMode("data")}
          />
        ) : (
          <div className={styles.emptyState}>
            <div className={styles.emptyPanel}>
              <MessageOutlined style={{ fontSize: 34, color: "#2563eb" }} />
              <h1 className={styles.emptyTitle}>开始诊断</h1>
              <p className={styles.emptyDescription}>描述问题。创建后再确认 Worker 和 PID。</p>
              <Button type="primary" size="large" icon={<PlusOutlined />} onClick={() => setNewOpen(true)}>新建诊断</Button>
            </div>
          </div>
        )}
      </div>

      <Modal title="新建诊断" open={newOpen} onCancel={() => setNewOpen(false)} onOk={createCase} okText="创建" confirmLoading={actionLoading} width={660} destroyOnHidden>
        <Form form={newForm} layout="vertical" initialValues={{ environment: "production", recovery_goal: "确认原因并给出安全处置建议" }}>
          <Form.Item name="target_session_id" label="关联长期目标">
            <Select
              allowClear
              placeholder="可选：复用长期目标范围和历史信号"
              options={targetSessions.filter((item) => item.status !== "ARCHIVED").map((item) => ({
                value: item.target_session_id,
                label: `${item.display_name} · ${item.status === "ACTIVE" ? "监控中" : "已暂停"}`,
              }))}
            />
          </Form.Item>
          <Form.Item name="problem_description" label="发生了什么" rules={[{ required: true, min: 3, message: "请描述问题" }]}>
            <Input.TextArea rows={4} maxLength={2000} showCount placeholder="例如：service-x 从半小时前开始 CPU 持续超过 90%" autoFocus />
          </Form.Item>
          <Space size={12} align="start" style={{ width: "100%" }}>
            <Form.Item name="service_id" label="目标服务" style={{ flex: 1 }}><Input placeholder="例如 service-x，可稍后填写" /></Form.Item>
            <Form.Item name="environment" label="环境" style={{ width: 150 }}><Select options={[{ value: "production", label: "生产" }, { value: "staging", label: "预发布" }, { value: "development", label: "开发" }]} /></Form.Item>
          </Space>
          <Form.Item name="recovery_goal" label="完成条件"><Input placeholder="例如：确认原因并给出安全处置建议" /></Form.Item>
        </Form>
      </Modal>

      <Modal title="创建长期诊断目标" open={targetOpen} onCancel={() => setTargetOpen(false)} onOk={createLongLivedTarget} okText="创建并诊断" confirmLoading={actionLoading} width={620} destroyOnHidden>
        <Alert type="info" showIcon message="长期目标会积累信号、历史 profiling 窗口和关联 Case；高严重度信号可按策略自动开 Case。" style={{ marginBottom: 16 }} />
        <Form form={targetForm} layout="vertical" initialValues={{ environment: "production" }}>
          <Form.Item name="service_id" label="服务标识" rules={[{ required: true, min: 1 }]}><Input maxLength={128} placeholder="例如 checkoutservice" /></Form.Item>
          <Form.Item name="display_name" label="显示名称"><Input maxLength={256} placeholder="可选" /></Form.Item>
          <Form.Item name="environment" label="环境" rules={[{ required: true }]}><Select options={[{ value: "production", label: "生产" }, { value: "staging", label: "预发布" }, { value: "development", label: "开发" }]} /></Form.Item>
        </Form>
      </Modal>

      <Modal title="登记服务变更" open={changeOpen} onCancel={() => setChangeOpen(false)} onOk={registerChange} okText="登记" confirmLoading={actionLoading} width={620} destroyOnHidden>
        <Alert type="info" showIcon message="变更只作为待验证相关性，不会直接被当作根因。" style={{ marginBottom: 16 }} />
        <Form form={changeForm} layout="vertical">
          <Space size={12} align="start" style={{ width: "100%" }}>
            <Form.Item name="service_id" label="服务" rules={[{ required: true }]} style={{ flex: 1 }}><Input /></Form.Item>
            <Form.Item name="environment" label="环境" rules={[{ required: true }]} style={{ width: 150 }}><Select options={[{ value: "production", label: "生产" }, { value: "staging", label: "预发布" }, { value: "development", label: "开发" }]} /></Form.Item>
          </Space>
          <Form.Item name="change_type" label="变更类型" rules={[{ required: true }]}>
            <Select options={[{ value: "release", label: "发布" }, { value: "config", label: "配置" }, { value: "feature_flag", label: "功能开关" }, { value: "scale", label: "扩缩容" }, { value: "other", label: "其他" }]} />
          </Form.Item>
          <Form.Item name="title" label="变更标题" rules={[{ required: true, min: 3 }]}><Input maxLength={256} /></Form.Item>
          <Form.Item name="changed_at" label="变更时间" rules={[{ required: true }]}><Input type="datetime-local" /></Form.Item>
          <Form.Item name="description" label="说明"><Input.TextArea rows={3} maxLength={2000} /></Form.Item>
        </Form>
      </Modal>

      <Modal title="创建受控恢复方案" open={recoveryOpen} onCancel={() => setRecoveryOpen(false)} onOk={createRecoveryPlan} okText="创建方案" confirmLoading={actionLoading} width={660} destroyOnHidden>
        <Alert type="warning" showIcon message="创建后不会立即执行：必须先只读预检，再由你批准一次。" style={{ marginBottom: 16 }} />
        <Form form={recoveryForm} layout="vertical">
          <Form.Item name="action_id" label="注册动作" rules={[{ required: true }]}>
            <Select options={registeredActions.map((item) => ({ value: item.action_id, label: item.title }))} />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(previous, current) => previous.action_id !== current.action_id}>
            {({ getFieldValue }) => getFieldValue("action_id") === "mini-drop.cleanup-expired-cache" && (
              <Form.Item name="retention_days" label="保留天数" rules={[{ required: true }]}><Input type="number" min={1} max={365} /></Form.Item>
            )}
          </Form.Item>
          <Form.Item name="value_after_fix" label="预期价值" rules={[{ required: true, min: 3 }]}><Input.TextArea rows={2} maxLength={2000} /></Form.Item>
          <Form.Item name="verification_method" label="验证方法" rules={[{ required: true, min: 3 }]}><Input.TextArea rows={2} maxLength={2000} /></Form.Item>
        </Form>
      </Modal>

      <ScopeEditorModal open={scopeOpen} detail={caseDetail} agents={agents} saving={actionLoading} autoSearch={scopeAutoSearch} onClose={() => { setScopeOpen(false); setScopeAutoSearch(false); }} onSave={saveScope} />
      <DiagnosisTechnicalDrawer open={technicalOpen} onClose={() => setTechnicalOpen(false)} diagnosis={diagnosis} onDecision={decideProbe} />
    </div>
  );
}
