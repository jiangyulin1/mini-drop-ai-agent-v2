import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Collapse,
  Col,
  Dropdown,
  Form,
  Input,
  InputNumber,
  Modal,
  Radio,
  Row,
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
  MinusCircleOutlined,
  MoreOutlined,
  PlusOutlined,
  RightOutlined,
  SearchOutlined,
} from "@ant-design/icons";
import {
  appendIncidentCaseMessage,
  advanceAutonomousCase,
  approveDiagnosisProbe,
  attachCaseResources,
  correctIncidentCase,
  createCaseRecoveryPlan,
  createIncidentCase,
  createServiceChange,
  createTargetSession,
  createCaseEventSource,
  decideCaseRecoveryPlan,
  dryRunCaseRecoveryPlan,
  executeCaseRecoveryPlan,
  getDiagnosisSession,
  getCaseCurrentUnderstanding,
  getIncidentCase,
  getCaseWorkspace,
  getTask,
  listAgents,
  listIncidentCaseEvents,
  listIncidentCases,
  listCaseProposals,
  listCaseRecoveryPlans,
  listRegisteredActions,
  listTasks,
  listTargetSessions,
  ensureEventSourceAuthCookie,
  runAIValidation,
  transitionIncidentCase,
  verifyCaseRecoveryPlan,
  rollbackCaseRecoveryPlan,
  runIncidentCaseAgentTurn,
} from "../api/client";
import { useSearchParams } from "react-router-dom";
import styles from "./AIDiagnosis.module.css";
import { isUserVisibleTask, taskDisplayName } from "../utils/taskNames";
import CaseConversation from "./ai-workspace/CaseConversation";
import DiagnosisDataConsole from "./ai-workspace/DiagnosisDataConsole";
import DiagnosisTechnicalDrawer from "./ai-workspace/DiagnosisTechnicalDrawer";
import ScopeEditorModal from "./ai-workspace/ScopeEditorModal";
import WorkerStatus from "./ai-workspace/WorkerStatus";
import CanonicalCaseWorkspace from "./ai-workspace/CanonicalCaseWorkspace";
import KnowledgeMemoryDrawer from "./ai-workspace/KnowledgeMemoryDrawer";
import InvestigationWorkbench from "../components/InvestigationWorkbench";
import {
  CASE_STATE_META,
  RELATION_OPTIONS,
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

function buildInitialTargetScope(values, agents) {
  const primaryService = values.service_id?.trim() || "";
  const agentById = new Map(agents.map((agent) => [agent.id, agent]));
  const instances = (values.instances || []).filter(
    (item) => item?.agent_id && Number(item?.pid) > 0 && item?.service_id?.trim(),
  ).map((item) => {
    const agent = agentById.get(item.agent_id);
    const serviceId = item.service_id.trim();
    const pid = Number(item.pid);
    return {
      service_id: serviceId,
      instance_id: `${serviceId}-${item.agent_id}-${pid}`
        .replace(/[^a-zA-Z0-9_.:-]+/g, "-")
        .slice(0, 128),
      host_id: agent?.hostname || item.agent_id,
      agent_id: item.agent_id,
      pid,
      environment: values.environment || "production",
    };
  });
  const dependencies = (values.dependencies || []).filter(
    (item) => item?.source_service?.trim() && item?.target_service?.trim(),
  ).map((item) => ({
    source_service: item.source_service.trim(),
    target_service: item.target_service.trim(),
    relation: item.relation || "CALLS",
    confidence: item.confidence || "medium",
    source: "user_confirmed",
  }));
  const verificationUrl = values.verification_url?.trim() || "";
  const autonomous = values.run_mode === "AUTHORIZED_AUTONOMY";
  const maxIterations = Number(values.max_iterations);
  const maxActions = Number(values.max_actions);
  return {
    ...(primaryService ? { service_id: primaryService } : {}),
    service_ids: Array.from(new Set([
      primaryService,
      ...instances.map((item) => item.service_id),
    ].filter(Boolean))),
    instances,
    dependencies,
    ...(verificationUrl ? {
      verification: {
        http_checks: [{
          name: "恢复检查",
          url: verificationUrl,
          method: "GET",
          expected_statuses: [200],
          samples: 3,
          timeout_sec: 5,
        }],
      },
    } : {}),
    ...(autonomous ? {
      orchestration: {
        swarm_service: values.swarm_service?.trim() || "",
        manager_agent_id: values.manager_agent_id || "",
        replicas: 1,
      },
      autonomy_policy: {
        max_iterations: Number.isFinite(maxIterations) ? maxIterations : 8,
        max_actions: Number.isFinite(maxActions) ? maxActions : 3,
        stable_verification_count: 2,
        max_auto_impact: values.authorize_restart ? "I2" : "I1",
        allowed_action_ids: values.authorize_restart
          ? ["swarm.restart-stateless-service"]
          : [],
        auto_approve_probe_ids: values.auto_approve_profilers
          ? ["process_cpu_profile", "process_io_latency"]
          : [],
      },
    } : {}),
  };
}

function buildTimeRange(values) {
  if (!values.event_start && !values.event_end) return undefined;
  if (!values.event_start || !values.event_end) {
    throw new Error("事件时间范围需要同时填写开始和结束时间");
  }
  const start = new Date(values.event_start);
  const end = new Date(values.event_end);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end <= start) {
    throw new Error("事件结束时间必须晚于开始时间");
  }
  return { start: start.toISOString(), end: end.toISOString(), source: "user_expression" };
}

export default function AIDiagnosisWorkspace() {
  const [agents, setAgents] = useState([]);
  const [cases, setCases] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [selectedKey, setSelectedKey] = useState("");
  const [caseDetail, setCaseDetail] = useState(null);
  const [events, setEvents] = useState([]);
  const [workspace, setWorkspace] = useState(null);
  const [workspaceConnected, setWorkspaceConnected] = useState(false);
  const [workspaceStreamSeed, setWorkspaceStreamSeed] = useState(null);
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
  const [messageDrafts, setMessageDrafts] = useState({});
  // A submitted turn only returns an acknowledgement; the real answer arrives
  // 60-120s later over the event stream.  Track it so the conversation can show
  // that the agent is still working instead of looking like nothing happened.
  const [pendingTurn, setPendingTurn] = useState(null);
  const [searchText, setSearchText] = useState("");
  const [evaluationOpen, setEvaluationOpen] = useState(false);
  const [newOpen, setNewOpen] = useState(false);
  const [scopeOpen, setScopeOpen] = useState(false);
  const [scopeCase, setScopeCase] = useState(null);
  const [technicalOpen, setTechnicalOpen] = useState(false);
  const [knowledgeOpen, setKnowledgeOpen] = useState(false);
  const [knowledgeChunkId, setKnowledgeChunkId] = useState("");
  const [focusEvidenceId, setFocusEvidenceId] = useState("");
  const [changeOpen, setChangeOpen] = useState(false);
  const [recoveryOpen, setRecoveryOpen] = useState(false);
  const [targetOpen, setTargetOpen] = useState(false);
  const [focusCollectionId, setFocusCollectionId] = useState("");
  const [scopeAutoSearch, setScopeAutoSearch] = useState(false);
  const [newForm] = Form.useForm();
  const [changeForm] = Form.useForm();
  const [recoveryForm] = Form.useForm();
  const [targetForm] = Form.useForm();
  const newRunMode = Form.useWatch("run_mode", newForm);
  const selectedNewTargetId = Form.useWatch("target_session_id", newForm);
  const [searchParams, setSearchParams] = useSearchParams();
  // Keep the view in the URL so a refresh, a bookmark, or a shared link all
  // land back on the same pane instead of silently reverting.
  const mode = searchParams.get("view") === "data" ? "data" : "ai";
  const setMode = useCallback((next) => {
    setSearchParams((current) => {
      const params = new URLSearchParams(current);
      if (next === "data") params.set("view", "data");
      else params.delete("view");
      return params;
    }, { replace: true });
  }, [setSearchParams]);
  const handledFromTask = useRef("");
  const handledCaseLink = useRef("");
  const listRequestSequence = useRef(0);
  const selectionRequestSequence = useRef(0);
  const selectedKeyRef = useRef(selectedKey);
  selectedKeyRef.current = selectedKey;
  const messageText = messageDrafts[selectedKey] || "";

  const chooseSelection = useCallback((key) => {
    selectedKeyRef.current = key;
    setSelectedKey(key);
    // Picking another case should land on its conversation; staying on the raw
    // data console makes the click look like it did nothing.
    setMode("ai");
  }, [setMode]);

  const updateMessageText = useCallback((value) => {
    const key = selectedKeyRef.current;
    if (!key) return;
    setMessageDrafts((drafts) => ({ ...drafts, [key]: value }));
  }, []);

  const refreshLists = useCallback(async ({ quiet = false } = {}) => {
    const requestId = listRequestSequence.current + 1;
    listRequestSequence.current = requestId;
    if (!quiet) setLoading(true);
    const results = await Promise.allSettled([
      listAgents(),
      listIncidentCases({ limit: 200 }),
      listTasks({ limit: 200 }),
      listRegisteredActions(),
      listTargetSessions({ limit: 200 }),
    ]);
    if (listRequestSequence.current !== requestId) return;
    const [agentResult, caseResult, taskResult, actionResult, targetResult] = results;
    if (agentResult.status === "fulfilled") setAgents(agentResult.value || []);
    if (caseResult.status === "fulfilled") setCases(caseResult.value?.items || []);
    if (taskResult.status === "fulfilled") setTasks((taskResult.value || []).filter(isUserVisibleTask));
    if (actionResult.status === "fulfilled") setRegisteredActions(
      (actionResult.value?.items || []).filter((item) => item.implementation_status === "executable"),
    );
    if (targetResult.status === "fulfilled") setTargetSessions(targetResult.value || []);
    const firstError = results.find((item) => item.status === "rejected");
    if (firstError && !quiet) message.error(`加载失败：${firstError.reason?.message || "请求失败"}`);

    const nextCases = caseResult.status === "fulfilled" ? caseResult.value?.items || [] : null;
    setSelectedKey((current) => {
      let next = current;
      if (current.startsWith("case:") && nextCases === null) return current;
      if (current.startsWith("case:") && nextCases?.some((item) => `case:${item.case_id}` === current)) return current;
      if (nextCases?.[0]) next = `case:${nextCases[0].case_id}`;
      else if (nextCases !== null) next = "";
      selectedKeyRef.current = next;
      return next;
    });
    if (!quiet) setLoading(false);
  }, []);

  const loadSelection = useCallback(async (key, { quiet = false } = {}) => {
    const requestId = selectionRequestSequence.current + 1;
    selectionRequestSequence.current = requestId;
    const isCurrent = () => (
      selectionRequestSequence.current === requestId
      && selectedKeyRef.current === key
    );
    if (!key) {
      if (isCurrent()) {
        setCaseDetail(null);
        setWorkspace(null);
        setWorkspaceStreamSeed(null);
        setDiagnosis(null);
        setCurrentUnderstanding(null);
        setProposals([]);
        setRecoveryPlans([]);
        setEvents([]);
      }
      return;
    }
    if (!quiet) setDetailLoading(true);
    if (!quiet && isCurrent()) {
      setCaseDetail(null);
      setWorkspace(null);
      setWorkspaceStreamSeed(null);
      setEvents([]);
      setDiagnosis(null);
    }
    try {
      if (key.startsWith("case:")) {
        const caseId = key.slice(5);
        const [workspaceResult, eventResult, understandingResult, proposalResult, recoveryResult] = await Promise.all([
          getCaseWorkspace(caseId),
          listIncidentCaseEvents(caseId, { limit: 300 }),
          getCaseCurrentUnderstanding(caseId),
          listCaseProposals(caseId),
          listCaseRecoveryPlans(caseId),
        ]);
        if (!isCurrent()) return;
        const detail = workspaceResult.case;
        let nextDiagnosis = null;
        if (detail.diagnosis_session_id) {
          nextDiagnosis = await getDiagnosisSession(detail.diagnosis_session_id);
        }
        if (!isCurrent()) return;
        setWorkspace(workspaceResult);
        setWorkspaceStreamSeed((current) => (
          current?.caseId === caseId
            ? current
            : { caseId, afterSeq: Number(workspaceResult.last_event_seq || 0) }
        ));
        setCaseDetail(detail);
        setEvents(eventResult.items || []);
        setCurrentUnderstanding(understandingResult.current_understanding || null);
        setProposals(proposalResult.proposals || []);
        setRecoveryPlans(recoveryResult.items || []);
        setDiagnosis(nextDiagnosis);
      }
    } catch (error) {
      if (!quiet && isCurrent()) message.error(`加载会话失败：${error.message}`);
    } finally {
      if (!quiet && isCurrent()) setDetailLoading(false);
    }
  }, []);

  useEffect(() => { refreshLists(); }, [refreshLists]);
  useEffect(() => { loadSelection(selectedKey); }, [loadSelection, selectedKey]);
  // Retire the pending marker when the runtime's answer lands, when the case
  // reaches a terminal state, or when the user navigates away.
  useEffect(() => {
    if (!pendingTurn) return;
    if (!selectedKey.startsWith("case:") || selectedKey.slice(5) !== pendingTurn.caseId) {
      setPendingTurn(null);
      return;
    }
    const turn = (workspace?.runtime_turns || []).find((item) => item.turn_id === pendingTurn.turnId);
    const answered = (workspace?.messages || []).some((item) => {
      const created = Date.parse(item.created_at || item.timestamp || "") || 0;
      return (item.trigger_turn_id === pendingTurn.turnId || created >= pendingTurn.startedAt - 1000)
        && Boolean(item.content);
    });
    const terminalTurn = turn && ["COMPLETED", "FAILED", "REJECTED", "CANCELLED"].includes(turn.status);
    const settled = terminalTurn || (!workspace?.active_turn && answered);
    if (settled) setPendingTurn(null);
  }, [pendingTurn, selectedKey, workspace]);
  useEffect(() => {
    const caseId = searchParams.get("caseId") || "";
    if (!caseId || loading || handledCaseLink.current === caseId) return;
    if (!cases.some((item) => item.case_id === caseId)) return;
    handledCaseLink.current = caseId;
    chooseSelection(`case:${caseId}`);
  }, [cases, chooseSelection, loading, searchParams]);
  useEffect(() => {
    if (!selectedKey.startsWith("case:") || workspaceStreamSeed?.caseId !== selectedKey.slice(5)) return undefined;
    let closed = false;
    let source = null;
    let retryTimer;
    let refreshTimer;
    let retryCount = 0;
    // Resume from the highest sequence we have rendered so a reconnect does not
    // replay or skip events.
    let afterSeq = Number(workspaceStreamSeed.afterSeq || 0);
    const key = selectedKey;
    const caseId = workspaceStreamSeed.caseId;

    const scheduleReconnect = () => {
      if (closed) return;
      // Native EventSource stops retrying once the server closes the stream (or
      // an idle proxy times it out), so reconnect explicitly with backoff.
      const delay = Math.min(1000 * (2 ** retryCount), 30000);
      retryCount += 1;
      window.clearTimeout(retryTimer);
      retryTimer = window.setTimeout(connect, delay);
    };

    const connect = () => {
      if (closed) return;
      void ensureEventSourceAuthCookie().then(() => {
        if (closed) return;
        source = createCaseEventSource(caseId, afterSeq);
        source.onopen = () => {
          if (closed) return;
          retryCount = 0;
          setWorkspaceConnected(true);
        };
        source.onerror = () => {
          if (closed) return;
          setWorkspaceConnected(false);
          source?.close();
          source = null;
          scheduleReconnect();
        };
        source.addEventListener("case_event", (event) => {
          if (closed) return;
          try {
            const item = JSON.parse(event.data);
            const seq = Number(item.case_event_seq || event.lastEventId || 0);
            if (seq > afterSeq) afterSeq = seq;
            setEvents((current) => {
              if (current.some((entry) => (
                (item.event_id && entry.event_id === item.event_id)
                || (seq > 0 && Number(entry.case_event_seq || 0) === seq)
              ))) return current;
              return [...current, item].sort((a, b) => Number(a.case_event_seq || 0) - Number(b.case_event_seq || 0));
            });
            window.clearTimeout(refreshTimer);
            refreshTimer = window.setTimeout(() => {
              void loadSelection(key, { quiet: true });
            }, 220);
          } catch {
            // A malformed frame must not tear down the durable stream.
          }
        });
      }).catch(() => {
        if (closed) return;
        setWorkspaceConnected(false);
        scheduleReconnect();
      });
    };

    connect();
    return () => {
      closed = true;
      window.clearTimeout(retryTimer);
      window.clearTimeout(refreshTimer);
      source?.close();
      setWorkspaceConnected(false);
    };
  }, [loadSelection, selectedKey, workspaceStreamSeed]);
  useEffect(() => {
    if (!selectedKey) return undefined;
    let cancelled = false;
    let timer;
    // Poll as a safety net for the event stream.  It must keep running while an
    // action is in flight -- that is exactly when the runtime is producing the
    // answer the user is waiting for.  A live stream only needs a slow heartbeat.
    const interval = () => (workspaceConnected ? 30000 : 5000);
    const poll = async () => {
      if (document.visibilityState === "hidden") {
        if (!cancelled) timer = window.setTimeout(poll, interval());
        return;
      }
      await Promise.allSettled([
        refreshLists({ quiet: true }),
        loadSelection(selectedKey, { quiet: true }),
      ]);
      if (!cancelled) timer = window.setTimeout(poll, interval());
    };
    timer = window.setTimeout(poll, interval());
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [loadSelection, refreshLists, selectedKey, workspaceConnected]);

  const filteredCases = useMemo(() => {
    const query = searchText.trim().toLowerCase();
    return cases.filter((item) => !query || [
      item.title,
      item.problem_description,
      item.target_scope?.service_id,
      item.environment,
    ].some((value) => String(value || "").toLowerCase().includes(query)));
  }, [cases, searchText]);
  const sortedCases = [...filteredCases].sort((a, b) => String(b.updated_at || b.created_at || "").localeCompare(String(a.updated_at || a.created_at || "")));
  const evaluationCases = sortedCases.filter((item) => /pi-agent-eval/i.test(`${item.title || ""} ${item.problem_description || ""}`));
  const regularCases = sortedCases.filter((item) => !evaluationCases.includes(item));
  const activeCases = regularCases.filter((item) => !["RESOLVED", "STOPPED"].includes(item.state)).slice(0, searchText ? 100 : 15);
  const completedCases = regularCases.filter((item) => ["RESOLVED", "STOPPED"].includes(item.state)).slice(0, searchText ? 100 : 10);

  async function refreshAll() {
    await refreshLists();
    await loadSelection(selectedKey);
  }

  async function refreshWorkers() {
    setWorkerLoading(true);
    try { setAgents(await listAgents()); } catch (error) { message.error(error.message); } finally { setWorkerLoading(false); }
  }

  function openScopeEditor(base = caseDetail, { autoSearch = false } = {}) {
    if (!base) return;
    setScopeCase(base);
    setScopeAutoSearch(autoSearch);
    setScopeOpen(true);
  }

  function closeScopeEditor() {
    setScopeOpen(false);
    setScopeAutoSearch(false);
    setScopeCase(null);
  }

  async function createCase() {
    let values;
    try {
      values = await newForm.validateFields();
    } catch {
      return;
    }
    setActionLoading(true);
    try {
      const selectedTarget = targetSessions.find(
        (item) => item.target_session_id === values.target_session_id,
      );
      const serviceId = selectedTarget?.target_scope?.service_id
        || selectedTarget?.target_scope?.service_ids?.[0]
        || values.service_id?.trim()
        || "";
      const timeRange = buildTimeRange(values);
      const created = await createIncidentCase({
        title: createCaseTitle(values.problem_description, serviceId),
        problem_description: values.problem_description.trim(),
        recovery_goal: values.recovery_goal?.trim() || "确认原因并给出安全处置建议",
        run_mode: values.run_mode || "COLLABORATE",
        environment: selectedTarget?.environment || values.environment || "production",
        target_scope: selectedTarget?.target_scope
          || buildInitialTargetScope(values, agents),
        ...(timeRange ? { time_range: timeRange } : {}),
        initial_tasks: values.initial_tasks || [],
        target_session_id: selectedTarget?.target_session_id,
      });
      setNewOpen(false);
      newForm.resetFields();
      const key = `case:${created.case_id}`;
      chooseSelection(key);
      setCaseDetail(created);
      setEvents([]);
      setDiagnosis(null);
      await refreshLists({ quiet: true });
      message.success("诊断会话已创建");
      if (!selectedTarget && !caseHasInstances(created)) {
        openScopeEditor(created, { autoSearch: Boolean(serviceId) });
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

  function openRecoveryPlan(recommendation = null) {
    const preferred = registeredActions.find(
      (item) => item.action_id === "mini-drop.cleanup-expired-cache",
    ) || registeredActions[0];
    recoveryForm.setFieldsValue({
      action_id: preferred?.action_id,
      retention_days: 7,
      value_after_fix: recommendation?.expected_effect || recommendation?.rationale || "释放过期诊断缓存占用，同时保留可恢复副本",
      verification_method: (recommendation?.verification_operations || []).map((item) => item.title || item.operation || item).join("；") || "由服务端确认目标指标恢复且未出现新的退化",
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
      if (action === "approve") await decideCaseRecoveryPlan(caseDetail.case_id, plan.recovery_plan_id, { ...version, decision: "approve", reason: "用户已核对影响清单与回滚路径", approval_digest: plan.policy?.approval_binding?.proposal_digest });
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

  // ── 从第一页"交给 AI 分析"进入 ────────────────────────

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
        source_task_id: taskId,
        target_scope: {
          service_id: instance.service_id,
          instances: [instance],
          dependencies: [],
        },
        initial_tasks: [taskId],
      });
      await refreshLists({ quiet: true });
      chooseSelection(`case:${created.case_id}`);
      message.success("已用该采集目标创建诊断会话");
      return created;
    } catch (error) {
      message.error(`从任务创建诊断失败：${error.message}`);
      return null;
    } finally {
      setActionLoading(false);
    }
  }

  // 处理来自采集页的显式跳转。等待首批列表加载完成，不使用固定延时。
  useEffect(() => {
    const fromTask = searchParams.get("fromTask") || "";
    if (!fromTask || loading || handledFromTask.current === fromTask) return undefined;
    let cancelled = false;
    handledFromTask.current = fromTask;
    void createCaseFromTask(fromTask).then((created) => {
      if (cancelled) return;
      if (!created) {
        handledFromTask.current = "";
        return;
      }
      const nextParams = new URLSearchParams(searchParams);
      nextParams.delete("fromTask");
      setSearchParams(nextParams, { replace: true });
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, searchParams]);

  async function startDiagnosis(base = caseDetail) {
    if (!base) return;
    if (!caseHasInstances(base)) {
      const canAutoSearch = Boolean(base.target_scope?.service_id);
      openScopeEditor(base, { autoSearch: canAutoSearch });
      message.info(canAutoSearch ? "请确认候选目标进程" : "请先搜索并确认目标进程");
      return;
    }
    setActionLoading(true);
    try {
      const turn = await runIncidentCaseAgentTurn(base.case_id, {
        message: "请基于当前目标和已有 Evidence，识别最重要的信息缺口并提出下一项受控采集。证据不足时请明确停止或拒答。",
        execute_safe_tools: true,
      });
      const turnId = turn?.next_actions?.find((item) => item.turn_id)?.turn_id || turn?.turn_id || "";
      setPendingTurn({ caseId: base.case_id, turnId, message: "开始 Evidence 调查", startedAt: Date.now() });
      await Promise.all([refreshLists({ quiet: true }), loadSelection(`case:${base.case_id}`)]);
      message.success("AI Evidence 调查已提交");
    } catch (error) {
      message.error(`启动失败：${error.message}`);
      await loadSelection(`case:${base.case_id}`, { quiet: true });
    } finally {
      setActionLoading(false);
    }
  }

  async function saveScope(values, startAfter) {
    const base = scopeCase;
    if (!base) return;
    setActionLoading(true);
    try {
      const updated = await correctIncidentCase(base.case_id, {
        target_scope: values.targetScope,
        environment: values.environment,
        recovery_goal: values.recoveryGoal,
        reason: "用户确认诊断范围和服务关系",
        expected_row_version: base.row_version,
      });
      closeScopeEditor();
      setCaseDetail(updated);
      await refreshLists({ quiet: true });
      if (startAfter) {
        await runIncidentCaseAgentTurn(updated.case_id, {
          message: "范围已确认。请评估现有 Evidence，提出最有信息价值的下一项受控采集。",
          execute_safe_tools: true,
        });
      }
      await loadSelection(`case:${updated.case_id}`);
      message.success(startAfter ? "范围已保存，AI Evidence 调查已提交" : "范围已保存");
    } catch (error) {
      message.error(`保存失败：${error.message}`);
      await loadSelection(`case:${base.case_id}`, { quiet: true });
    } finally {
      setActionLoading(false);
    }
  }

  async function sendAndAnalyze(sendMode = "answer") {
    const content = messageText.trim();
    if (!content || !caseDetail) return;
    const caseId = caseDetail.case_id;
    setActionLoading(true);
    try {
      const turn = await runIncidentCaseAgentTurn(caseId, {
        message: content,
        execute_safe_tools: true,
        requested_disposition: sendMode === "investigate" ? "INVESTIGATE" : "ANSWER_ONLY",
      });
      updateMessageText("");
      // The runtime answers asynchronously.  Keep a pending marker so the
      // conversation renders live progress until the turn completes.
      const turnId = turn?.next_actions?.find((item) => item.turn_id)?.turn_id || turn?.turn_id || "";
      if (turn?.status !== "needs_user") {
        setPendingTurn({ caseId, turnId, message: content, startedAt: Date.now() });
      }
      await Promise.all([refreshLists({ quiet: true }), loadSelection(`case:${caseId}`)]);
      if (turn.status === "needs_user" && !caseHasInstances(caseDetail)) {
        const current = await getIncidentCase(caseId);
        setCaseDetail(current);
        openScopeEditor(current, { autoSearch: Boolean(current.target_scope?.service_id) });
      }
    } catch (error) {
      setPendingTurn(null);
      message.error(`发送失败：${error.message}`);
      await loadSelection(`case:${caseId}`, { quiet: true });
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

  async function advanceAgent() {
    if (!caseDetail) return;
    setActionLoading(true);
    try {
      const result = await advanceAutonomousCase(caseDetail.case_id);
      message.success(result.outcome === "BUSY" ? "Agent 正在处理" : "已推进一步");
      await loadSelection(`case:${caseDetail.case_id}`, { quiet: true });
    } catch (error) {
      message.error(`推进失败：${error.message}`);
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
      const evidenceTaskIds = group.tasks.map((task) => task.id || task.task_id).filter(Boolean);
      current = await correctIncidentCase(current.case_id, {
        target_scope: {
          ...current.target_scope,
          instances: uniqueInstances([...(current.target_scope?.instances || []), ...linkedInstances]),
        },
        reason: `关联采集会话 ${group.collectionId}`,
        expected_row_version: current.row_version,
      });
      // E1 统一数据入口：批次 Task 经统一 Attachment API 绑定，不再写 evidence_task_ids
      const attachResult = await attachCaseResources(current.case_id, {
        references: [{
          type: "collection",
          id: group.collectionId,
          source: "collection_batch",
          member_task_ids: evidenceTaskIds,
        }],
        purpose: `关联采集批次 ${group.collectionId}`,
      });
      const rejected = attachResult.items.filter((item) => item.result !== "ACCEPTED");
      if (rejected.length) {
        message.warning(`批次 ${group.collectionId} 有 ${rejected.length} 项未接受：` +
          rejected.map((item) => item.rejection_reason || item.result).join(", "));
      }
      await runIncidentCaseAgentTurn(current.case_id, {
        message: `请分析刚关联的采集批次 ${group.collectionId}，只输出有字段引用的事实、冲突、限制和下一信息目标。`,
        execute_safe_tools: false,
      });
      setMode("ai");
      await Promise.all([refreshLists({ quiet: true }), loadSelection(`case:${current.case_id}`)]);
      message.success("已关联该批次 Evidence，并提交 AI 分析");
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
      <button type="button" className={`${styles.caseItem} ${selectedKey === key ? styles.caseItemActive : ""}`} key={key} onClick={() => chooseSelection(key)}>
        <div className={styles.caseTitleRow}><span className={`${styles.statusDot} ${statusClass(meta.tone)}`} /><span className={styles.caseTitle}>{item.title}</span><span className={styles.caseTime}>{formatTime(item.updated_at)}</span></div>
        <div className={styles.caseMeta}>{meta.label} · {item.target_scope?.service_id || "服务未确认"}</div>
      </button>
    );
  }

  const selectedNewTarget = targetSessions.find(
    (item) => item.target_session_id === selectedNewTargetId,
  );
  const completedTaskOptions = tasks.filter((task) => (
    ["DONE", "COMPLETED", "SUCCEEDED"].includes(String(task.status || "").toUpperCase())
  )).map((task) => {
    const taskId = task.id || task.task_id;
    const target = [task.agent_id, Number(task.target_pid) > 0 ? `PID ${task.target_pid}` : ""]
      .filter(Boolean)
      .join(" · ");
    return {
      value: taskId,
      label: target ? `${taskDisplayName(task)} · ${target}` : taskDisplayName(task),
    };
  }).filter((item) => item.value);

  return (
    <div className={styles.page}>
      <header className={styles.toolbar}>
        <div className={styles.modeSwitch} role="tablist" aria-label="工作模式">
          <button type="button" className={`${styles.modeButton} ${mode === "ai" ? styles.modeButtonActive : ""}`} onClick={() => setMode("ai")}><MessageOutlined /> AI 调查</button>
          <button type="button" className={`${styles.modeButton} ${mode === "data" ? styles.modeButtonActive : ""}`} onClick={() => setMode("data")}><DatabaseOutlined /> Evidence 数据台</button>
        </div>
        <span className={styles.toolbarHint}>{mode === "ai" ? "持续会话" : "人工采集与原始数据"}</span>
        <div className={styles.toolbarSpacer} />
        {caseDetail && <Button size="small" onClick={openChangeRegistration}>登记变更</Button>}
        {/* Setup and self-check actions are rare next to the investigation
            controls, so they live behind one menu instead of the top bar. */}
        <Dropdown
          menu={{
            items: [
              { key: "knowledge", label: "记忆与知识", icon: <DatabaseOutlined />, disabled: !caseDetail },
              { key: "targets", label: "长期目标" },
              { key: "validate", label: validationLoading ? "服务检测中…" : "服务连通性检测", icon: <ExperimentOutlined />, disabled: validationLoading },
            ],
            onClick: ({ key }) => {
              if (key === "knowledge") { setKnowledgeChunkId(""); setKnowledgeOpen(true); }
              if (key === "targets") setTargetOpen(true);
              if (key === "validate") void validateAIService();
            },
          }}
        >
          <Button size="small" icon={<MoreOutlined />} aria-label="设置与检测" />
        </Dropdown>
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
            {evaluationCases.length > 0 && <>
              <button type="button" className={styles.railGroupButton} aria-expanded={evaluationOpen} onClick={() => setEvaluationOpen((value) => !value)}>
                {evaluationOpen ? <DownOutlined /> : <RightOutlined />} 评测记录 <span>{evaluationCases.length}</span>
              </button>
              {evaluationOpen && evaluationCases.slice(0, 20).map(renderCaseItem)}
            </>}
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
            onFocusConsumed={() => setFocusCollectionId("")}
            onRefresh={refreshAll}
            onAnalyze={analyzeCollection}
          />
        ) : caseDetail ? (
          <div className={styles.caseWorkspaceShell}>
            <div className={styles.conversationPane}>
              <CaseConversation
              detail={caseDetail}
              events={events}
              assistantMessages={workspace?.messages || []}
              diagnosis={diagnosis}
              currentUnderstanding={currentUnderstanding}
              proposals={proposals}
              recoveryPlans={recoveryPlans}
              loading={detailLoading}
              actionLoading={actionLoading}
              pendingTurn={pendingTurn && pendingTurn.caseId === caseDetail.case_id ? pendingTurn : null}
              streamConnected={workspaceConnected}
              messageText={messageText}
              onMessageChange={updateMessageText}
              onSend={sendAndAnalyze}
              onStart={() => startDiagnosis()}
              onOpenScope={() => openScopeEditor(caseDetail)}
              onOpenTechnical={() => setTechnicalOpen(true)}
              onOpenCollection={openCollection}
              onDecision={decideProbe}
              onTransition={transition}
              onAdvanceAgent={advanceAgent}
              onOpenRecovery={openRecoveryPlan}
              onRecoveryAction={recoveryPlanAction}
              onOpenEvidence={setFocusEvidenceId}
              onOpenKnowledge={(chunkId) => { setKnowledgeChunkId(chunkId); setKnowledgeOpen(true); }}
              />
            </div>
            <aside className={styles.contextPane} aria-label="Case 调查上下文">
              <CanonicalCaseWorkspace
                workspace={workspace}
                connected={workspaceConnected}
                caseId={caseDetail.case_id}
                focusEvidenceId={focusEvidenceId}
                onFocusEvidenceConsumed={() => setFocusEvidenceId("")}
                onRefresh={refreshAll}
                onDiscussRecommendation={(item) => {
                  updateMessageText(`请基于恢复建议“${item.concrete_action || item.title || "未命名建议"}”说明适用条件、风险、回滚和验证步骤。`);
                  message.info("已将恢复建议放入会话输入框");
                }}
                onCreateRecovery={openRecoveryPlan}
              />
              <details className={styles.advancedControls}>
                <summary>高级控制</summary>
                <InvestigationWorkbench caseId={caseDetail.case_id} workspace={workspace} />
              </details>
            </aside>
          </div>
        ) : (
          <div className={styles.emptyState}>
            <div className={styles.emptyPanel}>
              <MessageOutlined style={{ fontSize: 34, color: "#2563eb" }} />
              <h1 className={styles.emptyTitle}>开始 Evidence 调查</h1>
              <p className={styles.emptyDescription}>描述需要了解的问题，创建后确认 Worker、PID 和采集范围。</p>
              <Button type="primary" size="large" icon={<PlusOutlined />} onClick={() => setNewOpen(true)}>新建调查</Button>
            </div>
          </div>
        )}
      </div>

      <KnowledgeMemoryDrawer
        open={knowledgeOpen}
        onClose={() => { setKnowledgeOpen(false); setKnowledgeChunkId(""); }}
        caseId={caseDetail?.case_id || ""}
        initialChunkId={knowledgeChunkId}
        onOpenEvidence={(evidenceId) => { setKnowledgeOpen(false); setFocusEvidenceId(evidenceId); }}
      />

      <Modal
        title="新建诊断"
        open={newOpen}
        onCancel={() => setNewOpen(false)}
        onOk={createCase}
        okText="创建"
        confirmLoading={actionLoading}
        width={900}
        styles={{ body: { maxHeight: "72vh", overflowY: "auto" } }}
        destroyOnHidden
      >
        <Form
          form={newForm}
          layout="vertical"
          initialValues={{
            environment: "production",
            recovery_goal: "确认原因并给出安全处置建议",
            run_mode: "COLLABORATE",
            instances: [],
            dependencies: [],
            initial_tasks: [],
            max_iterations: 8,
            max_actions: 3,
            authorize_restart: false,
            auto_approve_profilers: false,
          }}
        >
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
          {selectedNewTarget && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
              message={`将继承「${selectedNewTarget.display_name || selectedNewTarget.target_scope?.service_id || "长期目标"}」的环境、机器进程范围、服务关系、接管授权和历史信号`}
              description="为避免配置冲突，本次创建不再覆盖长期目标范围与授权策略；事件时间和已有任务证据仍可单独指定。"
            />
          )}
          <Form.Item name="problem_description" label="发生了什么" rules={[{ required: true, min: 3, message: "请描述问题" }]}>
            <Input.TextArea rows={4} maxLength={2000} showCount placeholder="例如：service-x 从半小时前开始 CPU 持续超过 90%" autoFocus />
          </Form.Item>
          <Row gutter={12}>
            <Col xs={24} sm={17}>
              <Form.Item name="service_id" label="目标服务">
                <Input disabled={Boolean(selectedNewTarget)} placeholder={selectedNewTarget ? "由长期目标继承" : "例如 service-x，可稍后填写"} />
              </Form.Item>
            </Col>
            <Col xs={24} sm={7}>
              <Form.Item name="environment" label="环境">
                <Select disabled={Boolean(selectedNewTarget)} options={[{ value: "production", label: "生产" }, { value: "staging", label: "预发布" }, { value: "development", label: "开发" }]} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="recovery_goal" label="完成条件"><Input placeholder="例如：确认原因并给出安全处置建议" /></Form.Item>
          <Form.Item name="run_mode" label="处理方式">
            <Radio.Group optionType="button" buttonStyle="solid" options={[
              { value: "COLLABORATE", label: "协作诊断" },
              { value: "AUTHORIZED_AUTONOMY", label: "持续接管" },
            ]} />
          </Form.Item>

          <Collapse
            items={[
              !selectedNewTarget && {
                key: "scope",
                label: "按需配置目标机器、进程与服务关系",
                children: (
                  <>
                    <Alert
                      type="info"
                      showIcon
                      style={{ marginBottom: 16 }}
                      message="可一次登记多个 Worker / PID；不填写时，创建后仍可扫描和确认范围。"
                    />
                    <Form.List name="instances">
                      {(fields, { add, remove }) => (
                        <Space direction="vertical" size={8} style={{ width: "100%" }}>
                          {fields.map(({ key, ...field }) => (
                            <Row gutter={8} align="middle" key={key}>
                              <Col xs={24} md={7}>
                                <Form.Item {...field} name={[field.name, "service_id"]} label="服务" rules={[{ required: true, message: "填写服务" }]}>
                                  <Input placeholder="service-x" />
                                </Form.Item>
                              </Col>
                              <Col xs={24} md={10}>
                                <Form.Item {...field} name={[field.name, "agent_id"]} label="Worker" rules={[{ required: true, message: "选择 Worker" }]}>
                                  <Select
                                    showSearch
                                    optionFilterProp="label"
                                    placeholder="选择目标机器"
                                    options={agents.map((agent) => ({
                                      value: agent.id,
                                      label: `${agent.hostname || agent.id} · ${agent.status === "ONLINE" ? "在线" : "离线"}`,
                                      disabled: agent.status !== "ONLINE",
                                    }))}
                                  />
                                </Form.Item>
                              </Col>
                              <Col xs={20} md={5}>
                                <Form.Item {...field} name={[field.name, "pid"]} label="PID" rules={[{ required: true, message: "填写 PID" }]}>
                                  <InputNumber min={1} precision={0} style={{ width: "100%" }} placeholder="1234" />
                                </Form.Item>
                              </Col>
                              <Col xs={4} md={2}>
                                <Button type="text" danger aria-label="删除目标进程" icon={<MinusCircleOutlined />} onClick={() => remove(field.name)} />
                              </Col>
                            </Row>
                          ))}
                          <Button type="dashed" icon={<PlusOutlined />} onClick={() => add({ service_id: newForm.getFieldValue("service_id") || "" })}>
                            添加机器 / 进程
                          </Button>
                        </Space>
                      )}
                    </Form.List>

                    <div style={{ marginTop: 20, marginBottom: 8, fontWeight: 600 }}>服务关系</div>
                    <Form.List name="dependencies">
                      {(fields, { add, remove }) => (
                        <Space direction="vertical" size={8} style={{ width: "100%" }}>
                          {fields.map(({ key, ...field }) => (
                            <Row gutter={8} align="middle" key={key}>
                              <Col xs={24} md={8}>
                                <Form.Item {...field} name={[field.name, "source_service"]} label="上游服务" rules={[{ required: true, message: "填写上游服务" }]}>
                                  <Input placeholder="service-x" />
                                </Form.Item>
                              </Col>
                              <Col xs={24} md={6}>
                                <Form.Item {...field} name={[field.name, "relation"]} label="关系">
                                  <Select options={RELATION_OPTIONS} />
                                </Form.Item>
                              </Col>
                              <Col xs={20} md={8}>
                                <Form.Item {...field} name={[field.name, "target_service"]} label="下游服务" rules={[{ required: true, message: "填写下游服务" }]}>
                                  <Input placeholder="database-y" />
                                </Form.Item>
                              </Col>
                              <Col xs={4} md={2}>
                                <Button type="text" danger aria-label="删除服务关系" icon={<MinusCircleOutlined />} onClick={() => remove(field.name)} />
                              </Col>
                            </Row>
                          ))}
                          <Button type="dashed" icon={<PlusOutlined />} onClick={() => add({ source_service: newForm.getFieldValue("service_id") || "", relation: "CALLS" })}>
                            添加服务关系
                          </Button>
                        </Space>
                      )}
                    </Form.List>
                  </>
                ),
              },
              {
                key: "evidence",
                label: "按需配置事件时间与已有证据",
                children: (
                  <>
                    <Row gutter={12}>
                      <Col xs={24} md={12}>
                        <Form.Item name="event_start" label="事件开始时间"><Input type="datetime-local" /></Form.Item>
                      </Col>
                      <Col xs={24} md={12}>
                        <Form.Item name="event_end" label="事件结束时间"><Input type="datetime-local" /></Form.Item>
                      </Col>
                    </Row>
                    <Form.Item
                      name="initial_tasks"
                      label="关联已有采集任务"
                      extra="仅列出已完成、用户可见的任务；服务端还会校验任务与当前实例范围是否一致。"
                    >
                      <Select
                        mode="multiple"
                        maxCount={16}
                        allowClear
                        showSearch
                        optionFilterProp="label"
                        placeholder={completedTaskOptions.length ? "选择最多 16 个任务作为初始证据" : "当前没有可复用的已完成任务"}
                        options={completedTaskOptions}
                      />
                    </Form.Item>
                  </>
                ),
              },
              newRunMode === "AUTHORIZED_AUTONOMY" && !selectedNewTarget && {
                key: "autonomy",
                label: "配置持续接管的预算、授权与恢复验证",
                children: (
                  <>
                    <Alert
                      type="warning"
                      showIcon
                      style={{ marginBottom: 16 }}
                      message="Agent 会持续诊断、执行本次明确授权的动作并验证；未登记的动作不会执行。"
                    />
                    <Row gutter={12}>
                      <Col xs={24} md={12}>
                        <Form.Item name="max_iterations" label="最多调查轮次">
                          <InputNumber min={1} max={30} precision={0} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                      <Col xs={24} md={12}>
                        <Form.Item name="max_actions" label="最多自动处置次数">
                          <InputNumber min={0} max={10} precision={0} style={{ width: "100%" }} />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Row gutter={12}>
                      <Col xs={24} md={12}>
                        <Form.Item
                          name="swarm_service"
                          label="Swarm 服务名"
                          rules={[({ getFieldValue }) => ({
                            validator(_, value) {
                              if (getFieldValue("authorize_restart") && !value?.trim()) {
                                return Promise.reject(new Error("授权重启时必须填写 Swarm 服务名"));
                              }
                              return Promise.resolve();
                            },
                          })]}
                        >
                          <Input placeholder="可选：允许恢复动作时填写" />
                        </Form.Item>
                      </Col>
                      <Col xs={24} md={12}>
                        <Form.Item name="manager_agent_id" label="Manager Worker">
                          <Select
                            allowClear
                            placeholder="可选"
                            options={agents.map((agent) => ({ value: agent.id, label: agent.hostname || agent.id, disabled: agent.status !== "ONLINE" }))}
                          />
                        </Form.Item>
                      </Col>
                    </Row>
                    <Form.Item name="verification_url" label="恢复检查 URL" extra="自动处置后以 GET 连续采样验证是否恢复。">
                      <Input type="url" placeholder="例如 https://service-x.example.com/health" />
                    </Form.Item>
                    <Space direction="vertical" size={4}>
                      <Form.Item name="auto_approve_profilers" valuePropName="checked" noStyle>
                        <Checkbox>自动批准 CPU / I/O 深度采集</Checkbox>
                      </Form.Item>
                      <Form.Item name="authorize_restart" valuePropName="checked" noStyle>
                        <Checkbox>授权重启已登记的无状态 Swarm 服务</Checkbox>
                      </Form.Item>
                    </Space>
                  </>
                ),
              },
            ].filter(Boolean)}
          />
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

      <ScopeEditorModal open={scopeOpen} detail={scopeCase} agents={agents} saving={actionLoading} autoSearch={scopeAutoSearch} onClose={closeScopeEditor} onSave={saveScope} />
      <DiagnosisTechnicalDrawer open={technicalOpen} onClose={() => setTechnicalOpen(false)} diagnosis={diagnosis} onDecision={decideProbe} />
    </div>
  );
}
