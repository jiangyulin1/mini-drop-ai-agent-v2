import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  Button,
  Dropdown,
  Input,
  Modal,
  Popconfirm,
  Segmented,
  Spin,
  Tag,
  Tooltip,
  message,
} from "antd";
import { verifyCaseRecovery } from "../../api/client";
import { useNavigate } from "react-router-dom";
import { riskCode } from "../../utils/opsMappings";
import {
  CheckOutlined,
  DatabaseOutlined,
  EditOutlined,
  EyeOutlined,
  MoreOutlined,
  PauseOutlined,
  PlayCircleOutlined,
  SendOutlined,
} from "@ant-design/icons";
import styles from "../AIDiagnosis.module.css";
import { confidenceGuide, findingSummaries, humanDiagnosis } from "../../utils/diagnosisHumanize";
import AssistantMessageContent from "./AssistantMessageContent";
import {
  CASE_STATE_META,
  AGENT_PHASE_META,
  DIAGNOSIS_STATUS_META,
  PROBE_LABELS,
  TERMINAL_DIAGNOSIS,
  eventText,
  agentErrorText,
  formatTime,
  nextConversationScroll,
} from "./workspaceUtils";

function Avatar({ ai = false }) {
  return <div className={`${styles.avatar} ${ai ? styles.avatarAi : ""}`}>{ai ? "AI" : "我"}</div>;
}

function Message({ ai = false, author, time, children }) {
  return (
    <article className={styles.message}>
      <Avatar ai={ai} />
      <div className={styles.messageBody}>
        <div className={styles.messageAuthor}>{author}<span className={styles.messageTime}>{formatTime(time)}</span></div>
        {children}
      </div>
    </article>
  );
}

function collectionIdFromText(value) {
  return String(value || "").match(/\[collection:([^\]]+)\]/)?.[1] || "";
}

function knowledgeChunkIds(payload) {
  const explicit = payload?.knowledge_refs || payload?.knowledge_chunk_refs || [];
  const content = `${payload?.content || ""} ${payload?.assistant_message || ""}`;
  const parsed = content.match(/chunk-[a-f0-9]{12,}/gi) || [];
  return [...new Set([...explicit.map((item) => typeof item === "string" ? item : item.chunk_id), ...parsed].filter(Boolean))];
}

function ReferenceChips({ payload, onOpenEvidence, onOpenKnowledge, onOpenTask }) {
  const evidence = (payload?.evidence_refs || payload?.evidence_chain || []).map(
    (item) => typeof item === "string" ? item : item.evidence_id,
  ).filter(Boolean);
  const knowledge = knowledgeChunkIds(payload);
  const content = `${payload?.content || ""} ${payload?.assistant_message || ""}`;
  const tasks = [...new Set(content.match(/task-[a-z0-9_-]{6,}/gi) || [])];
  if (!evidence.length && !knowledge.length && !tasks.length) return null;
  return <div className={styles.referenceChips}>
    {evidence.map((id) => <Button key={`ev:${id}`} size="small" icon={<DatabaseOutlined />} onClick={() => onOpenEvidence?.(id)}>{id}</Button>)}
    {knowledge.map((id) => <Button key={`kn:${id}`} size="small" onClick={() => onOpenKnowledge?.(id)}>知识片段 {id.slice(-8)}</Button>)}
    {tasks.map((id) => <Button key={`task:${id}`} size="small" onClick={() => onOpenTask?.(id)}>任务 {id.slice(-8)}</Button>)}
  </div>;
}

function CurrentResult({
  diagnosis,
  caseId,
  currentUnderstanding,
  proposals,
  onOpenTechnical,
  onOpenScope,
  onSwitchData,
}) {
  const conclusion = diagnosis?.latest_conclusion;
  const [verifying, setVerifying] = useState(false);
  const [verification, setVerification] = useState(null);
  if (!conclusion) return null;
  const evidence = diagnosis.evidence || [];
  const recommendations = conclusion.recommendations || [];
  const nextAction = conclusion.next_best_action || null;
  const human = humanDiagnosis(conclusion);
  const confidence = confidenceGuide(conclusion);
  const keyFindings = findingSummaries(conclusion);
  const scopeMissing = conclusion.cluster_assessment?.classification === "scope_unresolved"
    || (conclusion.limitations || []).includes("service_instance_mapping");
  if (scopeMissing) {
    return (
      <div className={styles.actionCard}>
        <div className={styles.cardBody}>
          <div className={styles.cardEyebrow}>需要处理</div>
          <div className={styles.cardTitle}>补充 Worker 和 PID</div>
          <div className={styles.cardDescription}>系统目前只知道服务名称，还不知道它运行在哪台 Worker、对应哪个容器或进程。为了避免采集错误进程，系统不会猜测 PID。</div>
          <div className={styles.cardDescription}>请先选择 Worker，再从自动发现的进程中确认目标；如果服务有多个实例，应把相关实例都加入范围。完成后系统会重新开始诊断。</div>
        </div>
        <div className={styles.cardActions}>
          <Button type="primary" onClick={onOpenScope}>补充范围</Button>
        </div>
      </div>
    );
  }
  const riskLabel = {
    R1: { text: "低风险", color: "green" },
    R2: { text: "需评估", color: "orange" },
    R3: { text: "需人工", color: "red" },
  };
  const verificationMeta = {
    recovered: { text: "已恢复", color: "green" },
    partially_recovered: { text: "部分恢复", color: "orange" },
    not_recovered: { text: "未恢复", color: "red" },
    degraded: { text: "出现退化", color: "red" },
    indeterminate: { text: "无法判定", color: "default" },
  };
  async function runVerify() {
    if (!caseId) return;
    setVerifying(true);
    setVerification(null);
    try {
      const result = await verifyCaseRecovery(caseId, { diagnosis_id: diagnosis.diagnosis_id });
      setVerification(result);
    } catch (error) {
      message.error(`验证失败：${error.message}`);
    } finally {
      setVerifying(false);
    }
  }
  return (
    <div className={styles.resultCard}>
      <div className={styles.resultHead}>
        <span className={styles.resultIcon}><CheckOutlined /></span>
        <div className={styles.resultContent}>
          <div className={styles.cardTitle}>{human.title}</div>
          <div className={styles.cardDescription}>{conclusion.summary || "尚未形成结论"}</div>
        </div>
        <span className={styles.confidence}>{confidence.label}</span>
      </div>
      <div className={styles.resultSection}>
        <div className={styles.resultLabel}>这意味着什么</div>
        <div className={styles.cardDescription}>{human.meaning}</div>
        <div className={styles.cardDescription}>{human.impact}</div>
      </div>
      {currentUnderstanding && (
        <div className={styles.resultSection}>
          <div className={styles.resultLabel}>当前理解</div>
          <div className={styles.cardDescription}>{currentUnderstanding.understanding}</div>
          {(currentUnderstanding.confirmed || []).slice(0, 3).map((item) => (
            <div className={styles.evidenceLine} key={item}>{item}</div>
          ))}
          {(currentUnderstanding.missing || []).length > 0 && (
            <div className={styles.cardDescription} style={{ marginTop: 6 }}>
              仍缺：{currentUnderstanding.missing.slice(0, 3).join("；")}
            </div>
          )}
        </div>
      )}
      {keyFindings.length > 0 ? (
        <div className={styles.resultSection}>
          <div className={styles.resultLabel}>为什么这样判断</div>
          {keyFindings.map((item, index) => (
            <div className={styles.evidenceLine} key={item.id}>
              <span>证据 {index + 1}：{item.text}{item.evidenceCount ? `（由 ${item.evidenceCount} 条原始证据支持）` : ""}</span>
            </div>
          ))}
          {keyFindings.some((item) => item.missing.length) && (
            <div className={styles.cardDescription}>仍需补充：{[...new Set(keyFindings.flatMap((item) => item.missing))].slice(0, 3).join("、")}</div>
          )}
        </div>
      ) : evidence.length > 0 ? (
        <div className={styles.resultSection}>
          <div className={styles.resultLabel}>已经取得的数据</div>
          <div className={styles.cardDescription}>已取得 {evidence.length} 条结构化证据，但暂时没有足够明确的事实可以向你解释为根因。</div>
        </div>
      ) : null}
      <div className={styles.resultSection}>
        <div className={styles.resultLabel}>这个结论有多可靠</div>
        <div className={styles.cardDescription}>{confidence.explanation}</div>
      </div>
      {recommendations.length > 0 && (
        <div className={styles.resultSection}>
          <div className={styles.resultLabel}>建议处理顺序</div>
          <div className={styles.recommendationList}>
            {recommendations.slice(0, 3).map((item) => {
              const risk = riskLabel[item.risk_level] || { text: item.risk_level, color: "default" };
              return (
                <div className={styles.recommendationCard} key={item.recommendation_id || item.title}>
                  <div className={styles.recommendationHead}>
                    <span className={styles.recommendationTitle}>{item.title}</span>
                    <Tag color={risk.color}>{risk.text}</Tag>
                  </div>
                  <div className={styles.cardDescription}>{item.detail}</div>
                  {(item.evidence_refs || []).length > 0 && (
                    <div className={styles.recommendationRefs}>{item.evidence_refs.length} 条证据支撑</div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
      {nextAction && (
        <div className={styles.resultSection}>
          <div className={styles.resultLabel}>建议的下一步</div>
          {nextAction.type === "verify" ? (
            <div className={styles.recommendationCard}>
              <div className={styles.recommendationHead}>
                <span className={styles.recommendationTitle}>{nextAction.title}</span>
                <Tag color="purple">验证恢复</Tag>
              </div>
              <div className={styles.cardDescription}>{nextAction.description}</div>
              <div className={styles.recommendationRefs}>
                <Button size="small" type="primary" loading={verifying} onClick={runVerify}>触发验证采集</Button>
              </div>
              {verification && (
                <div className={styles.verificationResult}>
                  <Tag color={(verificationMeta[verification.status] || {}).color || "default"}>
                    {(verificationMeta[verification.status] || {}).text || verification.status}
                  </Tag>
                  <span>{verification.reason || ""}</span>
                  {Object.entries(verification.metrics || {}).map(([key, item]) => (
                    <div key={key} className={styles.verificationLine}>
                      {key}: {item.baseline} → {item.current}（{item.verdict}）
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className={styles.recommendationCard}>
              <div className={styles.recommendationHead}>
                <span className={styles.recommendationTitle}>{nextAction.title}</span>
                <Tag color={nextAction.needs_approval ? "orange" : "green"}>
                  {nextAction.needs_approval ? "需确认" : "自动执行"}
                </Tag>
              </div>
              <div className={styles.cardDescription}>{nextAction.description}</div>
              <div className={styles.cardDescription} style={{ marginTop: 6, color: "#8a6116" }}>
                {nextAction.reason || ""}
              </div>
            </div>
          )}
        </div>
      )}
      {(proposals || []).length > 0 && (
        <div className={styles.resultSection}>
          <div className={styles.resultLabel}>动作提案</div>
          <div className={styles.recommendationList}>
            {proposals.slice(0, 4).map((item) => (
              <div className={styles.recommendationCard} key={item.action_id}>
                <div className={styles.recommendationHead}>
                  <span className={styles.recommendationTitle}>{item.predicted_effect}</span>
                  <Tag color={item.requires_approval ? "orange" : "green"}>
                    {item.requires_approval ? "需确认" : "自动"} · {item.impact}
                  </Tag>
                </div>
                <div className={styles.cardDescription}>{item.rationale}</div>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className={styles.cardActions}>
        <Button type="primary" icon={<EyeOutlined />} onClick={onOpenTechnical}>查看证据与采集详情</Button>
      </div>
    </div>
  );
}

function RecoveryPlanCards({ plans, loading, onAction }) {
  const [confirmation, setConfirmation] = useState(null);
  const [confirmationText, setConfirmationText] = useState("");
  if (!(plans || []).length) return null;
  const statusMeta = {
    PROPOSED: ["等待预检", "blue"],
    DRY_RUN_COMPLETED: ["等待批准", "orange"],
    DRY_RUN_EMPTY: ["无需执行", "default"],
    APPROVED: ["已批准", "purple"],
    EXECUTED: ["等待验证", "cyan"],
    VERIFIED: ["验证通过", "green"],
    VERIFICATION_FAILED: ["验证失败", "red"],
    ROLLED_BACK: ["已回滚", "gold"],
    REJECTED: ["已拒绝", "default"],
    FAILED: ["执行失败", "red"],
  };
  const expectedConfirmation = confirmation
    ? `确认执行 ${confirmation.plan.action_id}`
    : "";
  const submitRiskAction = async () => {
    if (!confirmation || confirmationText !== expectedConfirmation) return;
    await onAction(confirmation.plan, confirmation.action);
    setConfirmation(null);
    setConfirmationText("");
  };
  const openRiskConfirmation = (plan, action) => {
    setConfirmation({ plan, action });
    setConfirmationText("");
  };
  return (
    <>
    <Message ai author="Mini-Drop">
      <div className={styles.resultCard}>
        <div className={styles.resultLabel}>受控恢复方案</div>
        <div className={styles.recommendationList}>
          {plans.slice(0, 3).map((plan) => {
            const meta = statusMeta[plan.status] || [plan.status, "default"];
            return (
              <div className={styles.recommendationCard} key={plan.recovery_plan_id}>
                <div className={styles.recommendationHead}>
                  <span className={styles.recommendationTitle} title={plan.action_id}>
                    {plan.title || plan.summary || plan.action_name || plan.action_id}
                  </span>
                  <Tag color={meta[1]}>{meta[0]}</Tag>
                </div>
                <div className={styles.cardDescription}>{plan.value_after_fix}</div>
                <div className={styles.cardDescription}>验证：{plan.verification_method}</div>
                {plan.dry_run?.candidate_count !== undefined && (
                  <div className={styles.recommendationRefs}>预检影响 {plan.dry_run.candidate_count} 项</div>
                )}
                <div className={styles.cardActions}>
                  {plan.status === "PROPOSED" && <Button size="small" loading={loading} onClick={() => onAction(plan, "dry-run")}>只读预检</Button>}
                  {plan.status === "DRY_RUN_COMPLETED" && <>
                    <Button size="small" type="primary" loading={loading} onClick={() => onAction(plan, "approve")}>批准一次</Button>
                    <Button size="small" danger onClick={() => onAction(plan, "reject")}>拒绝</Button>
                  </>}
                  {plan.status === "APPROVED" && (
                    <Button size="small" danger loading={loading} onClick={() => openRiskConfirmation(plan, "execute")}>进入受控执行</Button>
                  )}
                  {plan.status === "EXECUTED" && <>
                    <Button size="small" type="primary" loading={loading} onClick={() => onAction(plan, "verify")}>服务端验证</Button>
                    <Popconfirm title="确认回滚本次恢复动作？" onConfirm={() => onAction(plan, "rollback")}>
                      <Button size="small" danger loading={loading}>回滚</Button>
                    </Popconfirm>
                  </>}
                  {plan.status === "VERIFICATION_FAILED" && (
                    <Button size="small" danger loading={loading} onClick={() => onAction(plan, "rollback")}>立即回滚</Button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </Message>
    <Modal
      title="高风险动作确认"
      open={Boolean(confirmation)}
      okText="确认并提交执行"
      cancelText="取消"
      okButtonProps={{
        danger: true,
        disabled: confirmationText !== expectedConfirmation,
        loading,
        "aria-label": "确认并提交高风险恢复动作",
      }}
      onOk={submitRiskAction}
      onCancel={() => {
        setConfirmation(null);
        setConfirmationText("");
      }}
      destroyOnHidden
    >
      <div className={styles.recommendationList}>
        <div><strong>动作：</strong>{confirmation?.plan.action_id || "-"}</div>
        <div><strong>风险：</strong>{confirmation?.plan.risk_level || "高风险受控操作"}</div>
        <div><strong>预计影响：</strong>{confirmation?.plan.value_after_fix || "以服务端计划为准"}</div>
        <div><strong>Dry Run：</strong>影响 {confirmation?.plan.dry_run?.candidate_count ?? "未返回"} 项</div>
        <div><strong>验证方式：</strong>{confirmation?.plan.verification_method || "需服务端验证"}</div>
        <div><strong>回滚：</strong>{confirmation?.plan.rollback_method || confirmation?.plan.policy?.rollback || "使用计划绑定的服务端回滚动作"}</div>
        <div><strong>授权：</strong>{confirmation?.plan.policy?.approval_binding ? "已绑定本次审批摘要" : "等待服务端校验本次授权"}</div>
        <Input
          value={confirmationText}
          onChange={(event) => setConfirmationText(event.target.value)}
          placeholder={expectedConfirmation}
          aria-label="高风险动作确认文本"
        />
        <div className={styles.cardDescription}>请输入“{expectedConfirmation}”。提交后仅表示已受理，必须完成服务端验证后才能标记恢复成功。</div>
      </div>
    </Modal>
    </>
  );
}

/**
 * Live placeholder for a submitted turn.
 *
 * The turn endpoint only acknowledges receipt; the runtime keeps working for
 * another 60-120s.  Without this the send button simply stops spinning and the
 * conversation looks like the message vanished.
 */
function PendingTurnMessage({ pendingTurn, streamConnected }) {
  const [elapsed, setElapsed] = useState(() => Math.max(0, Math.round((Date.now() - pendingTurn.startedAt) / 1000)));

  useEffect(() => {
    setElapsed(Math.max(0, Math.round((Date.now() - pendingTurn.startedAt) / 1000)));
    const timer = window.setInterval(() => {
      setElapsed(Math.max(0, Math.round((Date.now() - pendingTurn.startedAt) / 1000)));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [pendingTurn.startedAt]);

  const hint = elapsed < 20
    ? "正在读取案件上下文并选择探针…"
    : elapsed < 60
      ? "正在采集证据，通常需要 1-2 分钟。"
      : "仍在调查中。采集与推理较慢时可继续等待，期间可以随时补充信息。";

  return (
    <Message ai author="Mini-Drop" time={new Date(pendingTurn.startedAt).toISOString()}>
      <p className={styles.messageText}>已收到，正在调查。</p>
      <div className={styles.progressCard}>
        <Spin size="small" />
        <span className={styles.progressText}>{hint}</span>
        <Tag color="processing">已用时 {elapsed} 秒</Tag>
        {!streamConnected && <Tag color="warning">实时连接中断，正在轮询</Tag>}
      </div>
    </Message>
  );
}

export default function CaseConversation({
  detail,
  events,
  assistantMessages = [],
  diagnosis,
  currentUnderstanding,
  proposals,
  recoveryPlans,
  loading,
  actionLoading,
  pendingTurn = null,
  streamConnected = false,
  messageText,
  onMessageChange,
  onSend,
  onStart,
  onOpenScope,
  onOpenTechnical,
  onOpenCollection,
  onDecision,
  onTransition,
  onAdvanceAgent,
  onOpenRecovery,
  onRecoveryAction,
  onOpenEvidence,
  onOpenKnowledge,
}) {
  const navigate = useNavigate();
  const state = CASE_STATE_META[detail.state] || { label: detail.state, color: "default", tone: "idle" };
  const status = DIAGNOSIS_STATUS_META[diagnosis?.status] || { label: diagnosis?.status, color: "default" };
  const userEvents = useMemo(() => {
    const timeline = (events || []).filter((event) => event.event_type !== "case_created");
    const representedMessageIds = new Set(
      timeline.map((event) => event.payload?.message_id).filter(Boolean),
    );
    for (const item of assistantMessages || []) {
      if (representedMessageIds.has(item.message_id)) continue;
      timeline.push({
        event_id: `persisted:${item.message_id}`,
        event_type: "assistant.message",
        created_at: item.created_at,
        payload: {
          message_id: item.message_id,
          content: item.content,
          evidence_refs: item.evidence_refs || [],
        },
      });
    }
    return timeline.sort((a, b) => {
      const timeOrder = String(a.created_at || "").localeCompare(String(b.created_at || ""));
      return timeOrder || Number(a.case_event_seq || 0) - Number(b.case_event_seq || 0);
    });
  }, [assistantMessages, events]);
  const waitingProbes = (diagnosis?.probes || []).filter((probe) => probe.status === "WAITING_APPROVAL");
  const diagnosisRunning = diagnosis
    && !TERMINAL_DIAGNOSIS.has(diagnosis.status)
    && !["WAITING_APPROVAL", "PAUSED"].includes(diagnosis.status);
  const canStart = detail.state === "OPEN" && !detail.diagnosis_session_id;
  const readOnly = detail.state === "STOPPED" || detail.state === "RESOLVED";
  const agentLoop = detail.summary?.recovery?.agent_loop || detail.recovery?.agent_loop || {};
  const autonomous = detail.run_mode === "AUTHORIZED_AUTONOMY";
  const [sendMode, setSendMode] = useState("answer");
  const scrollRef = useRef(null);
  const scrollState = useRef({ caseId: "", scrollHeight: 0, scrollTop: 0, nearBottom: true });
  const agentPhase = AGENT_PHASE_META[agentLoop.phase] || {
    label: agentLoop.phase || "等待诊断",
    detail: "系统会继续保存调查和恢复进度。",
  };

  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (!element) return;
    const previous = scrollState.current;
    element.scrollTop = nextConversationScroll({
      caseChanged: previous.caseId !== detail.case_id,
      nearBottom: previous.nearBottom,
      previousTop: previous.scrollTop,
      scrollHeight: element.scrollHeight,
      clientHeight: element.clientHeight,
    });
    scrollState.current = {
      caseId: detail.case_id,
      scrollHeight: element.scrollHeight,
      scrollTop: element.scrollTop,
      nearBottom: element.scrollHeight - element.scrollTop - element.clientHeight < 80,
    };
  }, [detail.case_id, events?.length, diagnosis?.status, diagnosis?.updated_at, agentLoop.phase]);

  useEffect(() => {
    setSendMode("answer");
  }, [detail.case_id]);

  function rememberScroll(event) {
    const element = event.currentTarget;
    scrollState.current = {
      caseId: detail.case_id,
      scrollHeight: element.scrollHeight,
      scrollTop: element.scrollTop,
      nearBottom: element.scrollHeight - element.scrollTop - element.clientHeight < 80,
    };
  }

  const moreItems = [
    ...(!readOnly ? [{ key: "scope", label: "修改范围和服务关系" }] : []),
    ...(diagnosis && !diagnosis.latest_conclusion ? [{ key: "technical", label: "查看技术详情" }] : []),
    ...(autonomous && !readOnly ? [{ key: "advance", label: "立即推进一次" }] : []),
    ...(!readOnly ? [{ key: "stop", label: "停止会话", danger: true }] : []),
  ];

  function handleMore({ key }) {
    if (key === "scope") onOpenScope();
    if (key === "technical") onOpenTechnical();
    if (key === "advance") onAdvanceAgent();
    if (key === "stop") {
      Modal.confirm({
        title: "停止当前会话？",
        content: "停止后不会继续诊断或执行动作。已经采集的数据仍会保留。",
        okText: "停止会话",
        okButtonProps: { danger: true },
        cancelText: "取消",
        onOk: () => onTransition("stop"),
      });
    }
  }

  function handleKeyDown(event) {
    if (event.isComposing || event.nativeEvent?.isComposing) return;
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (messageText.trim() && !readOnly && !actionLoading) onSend(sendMode);
    }
  }

  return (
    <div className={styles.main}>
      <header className={styles.caseHeader}>
        <div className={styles.caseHeaderMain}>
          <h1 className={styles.caseHeaderTitle}>{detail.title}</h1>
          <div className={styles.caseHeaderMeta}>
            <Tag color={state.color} style={{ margin: 0 }}>{state.label}</Tag>
            <span>{detail.target_scope?.service_id || "服务未确认"}</span><span>·</span><span>{detail.environment}</span>
          </div>
        </div>
        <div className={styles.caseHeaderActions}>
          <Button icon={<EditOutlined />} onClick={onOpenScope} disabled={readOnly}>范围</Button>
          {!readOnly && <Button onClick={onOpenRecovery}>恢复</Button>}
          {diagnosis && <Button icon={<EyeOutlined />} onClick={onOpenTechnical}>详情</Button>}
          {moreItems.length > 0 && (
            <Dropdown menu={{ items: moreItems, onClick: handleMore }} trigger={["click"]}>
              <Button icon={<MoreOutlined />}>更多</Button>
            </Dropdown>
          )}
          {!readOnly && detail.state === "PAUSED" ? (
            <Button icon={<PlayCircleOutlined />} loading={actionLoading} onClick={() => onTransition("resume")}>继续</Button>
          ) : !readOnly ? (
            <Tooltip title="暂停后不会启动新的动作">
              <Button icon={<PauseOutlined />} loading={actionLoading} onClick={() => onTransition("pause")}>暂停</Button>
            </Tooltip>
          ) : null}
          {detail.state !== "RESOLVED" && detail.state !== "STOPPED" && (
            <Popconfirm title="确认问题已经恢复？" onConfirm={() => onTransition("resolve")}>
              <Button type="primary" icon={<CheckOutlined />} loading={actionLoading}>已解决</Button>
            </Popconfirm>
          )}
        </div>
      </header>

      <div className={styles.conversationScroll} ref={scrollRef} onScroll={rememberScroll}>
        <Spin spinning={loading}>
          <div className={styles.conversation}>
            {autonomous && (
              <div className={styles.actionCard}>
                <div className={styles.cardBody}>
                  <div className={styles.cardEyebrow}>持续接管</div>
                  <div className={styles.cardTitle}>{agentPhase.label}</div>
                  <div className={styles.cardDescription}>{agentPhase.detail}</div>
                  <div className={styles.cardDescription}>
                    调查 {agentLoop.iteration || 0} 轮 · 已执行 {agentLoop.actions_executed || 0} 个动作 · 恢复验证 {agentLoop.stable_verifications || 0} 次
                  </div>
                </div>
                {agentLoop.last_error && <div className={styles.cardDescription}>{agentErrorText(agentLoop.last_error)}</div>}
              </div>
            )}
            <div className={styles.dayDivider}>{formatTime(detail.created_at, true)} · 会话开始</div>
            <Message author="你" time={detail.created_at}>
              <p className={styles.messageText}>{detail.problem_description}</p>
            </Message>

            {userEvents.map((event) => {
              if (event.event_type === "user_message") {
                const content = event.payload?.content || "";
                const collectionId = collectionIdFromText(content);
                return (
                  <Message key={event.event_id} author="你" time={event.created_at}>
                    <p className={styles.messageText}>{content.replace(/\[collection:[^\]]+\]\s*/, "")}</p>
                    {collectionId && (
                      <div className={styles.dataLinkCard}>
                        <div className={styles.cardBody}>
                          <div className={styles.cardTitle}><DatabaseOutlined /> {collectionId}</div>
                          <div className={styles.cardDescription}>人工采集会话</div>
                        </div>
                        <div className={styles.cardActions}><Button size="small" onClick={() => onOpenCollection(collectionId)}>查看数据</Button></div>
                      </div>
                    )}
                  </Message>
                );
              }
              if (event.event_type === "agent_turn_completed") {
                const payload = event.payload || {};
                return (
                  <Message key={event.event_id} ai author="Mini-Drop" time={event.created_at}>
                    <AssistantMessageContent content={payload.assistant_message || "本轮处理完成。"} />
                    <ReferenceChips payload={payload} onOpenEvidence={onOpenEvidence} onOpenKnowledge={onOpenKnowledge} onOpenTask={(id) => navigate(`/task/${encodeURIComponent(id)}`)} />
                    {(payload.limitations || []).length > 0 && (
                      <div className={styles.cardDescription}>仍缺：{payload.limitations.slice(0, 3).join("；")}</div>
                    )}
                  </Message>
                );
              }
              if (event.event_type === "assistant.message") {
                const payload = event.payload || {};
                return (
                  <Message key={event.event_id} ai author="Mini-Drop" time={event.created_at}>
                    <AssistantMessageContent content={payload.content || ""} />
                    <ReferenceChips payload={payload} onOpenEvidence={onOpenEvidence} onOpenKnowledge={onOpenKnowledge} onOpenTask={(id) => navigate(`/task/${encodeURIComponent(id)}`)} />
                  </Message>
                );
              }
              if (event.event_type === "agent_runtime_turn_submitted") {
                const payload = event.payload || {};
                return (
                  <Message key={event.event_id} ai author="Mini-Drop" time={event.created_at}>
                    <AssistantMessageContent content={payload.assistant_message || "已提交给 Agent Runtime 处理。"} />
                    {(payload.next_actions || []).length > 0 && (
                      <div className={styles.cardDescription}>Turn {payload.turn_id || ""}</div>
                    )}
                  </Message>
                );
              }
              if (event.event_type === "agent_runtime_turn_rejected") {
                const payload = event.payload || {};
                return (
                  <Message key={event.event_id} ai author="Mini-Drop" time={event.created_at}>
                    <AssistantMessageContent content={payload.assistant_message || "Agent Runtime 不可用，本轮未启动调查。"} />
                  </Message>
                );
              }
              const text = eventText(event);
              return text ? <div className={styles.systemEvent} key={event.event_id}>{formatTime(event.created_at)} · {text}</div> : null;
            })}

            {detail.state === "NEEDS_SCOPE_CONFIRMATION" && (
              <Message ai author="Mini-Drop" time={detail.updated_at}>
                <p className={styles.messageText}>先确认目标服务、Worker 和 PID。</p>
                <div className={styles.actionCard}>
                  <div className={styles.cardBody}>
                    <div className={styles.cardEyebrow}>需要处理</div>
                    <div className={styles.cardTitle}>设置诊断范围</div>
                    <div className={styles.cardDescription}>系统会列出在线 Worker。只有你确认的实例会被采集。</div>
                  </div>
                  <div className={styles.cardActions}><Button type="primary" onClick={onOpenScope}>设置范围</Button></div>
                </div>
              </Message>
            )}

            {canStart && (
              <Message ai author="Mini-Drop" time={detail.updated_at}>
                <p className={styles.messageText}>范围已保存，可以开始诊断。</p>
                <div className={styles.actionCard}>
                  <div className={styles.cardBody}>
                    <div className={styles.cardTitle}>检查当前范围</div>
                    <div className={styles.scopeChips}>
                      {(detail.target_scope?.instances || []).map((item) => <span className={styles.scopeChip} key={`${item.agent_id}:${item.pid}`}>{item.host_id} · PID {item.pid}</span>)}
                    </div>
                  </div>
                  <div className={styles.cardActions}>
                    <Button type="primary" icon={<PlayCircleOutlined />} loading={actionLoading} onClick={onStart}>开始诊断</Button>
                    <Button onClick={onOpenScope}>修改范围</Button>
                    <span className={styles.cardAside}>只读探针优先</span>
                  </div>
                </div>
              </Message>
            )}

            {pendingTurn && (
              <PendingTurnMessage pendingTurn={pendingTurn} streamConnected={streamConnected} />
            )}

            {diagnosisRunning && !pendingTurn && (
              <Message ai author="Mini-Drop" time={detail.updated_at}>
                <p className={styles.messageText}>诊断正在进行。</p>
                <div className={styles.progressCard}><Spin size="small" /><span className={styles.progressText}>{status.label}</span><Tag color={status.color}>{diagnosis.evidence?.length || 0} 条证据</Tag></div>
              </Message>
            )}

            {waitingProbes.map((probe) => (
              <Message ai author="Mini-Drop" time={detail.updated_at} key={probe.step_id}>
                <p className={styles.messageText}>需要你确认一次短时采集。</p>
                <div className={styles.actionCard}>
                  <div className={styles.cardBody}>
                    <div className={styles.cardEyebrow}>等待确认</div>
                    <div className={styles.cardTitle}>{PROBE_LABELS[probe.probe_id] || probe.probe_id}</div>
                    <div className={styles.cardDescription}>{probe.reason} · 约 {probe.parameters?.duration_sec || 0} 秒 · {riskCode(probe.risk_level).label}{probe.risk_level === "R2" ? "，仅执行一次" : ""}</div>
                  </div>
                  <div className={styles.cardActions}>
                    {probe.risk_level === "R2" ? (
                      <Popconfirm
                        title="批准这次中风险采集？"
                        description={`将在当前诊断范围执行约 ${probe.parameters?.duration_sec || 0} 秒，只授权本次。`}
                        okText="批准一次"
                        cancelText="取消"
                        onConfirm={() => onDecision(probe.step_id, "approve")}
                      >
                        <Button type="primary" loading={actionLoading}>查看影响并批准</Button>
                      </Popconfirm>
                    ) : (
                      <Button type="primary" loading={actionLoading} onClick={() => onDecision(probe.step_id, "approve")}>批准一次</Button>
                    )}
                    <Button danger disabled={actionLoading} onClick={() => onDecision(probe.step_id, "reject")}>拒绝</Button>
                  </div>
                </div>
              </Message>
            ))}

            {diagnosis?.latest_conclusion && (
              <Message ai author="Mini-Drop" time={detail.updated_at}>
              <CurrentResult
                diagnosis={diagnosis}
                caseId={detail.case_id}
                currentUnderstanding={currentUnderstanding}
                proposals={proposals}
                onOpenTechnical={onOpenTechnical}
                onOpenScope={onOpenScope}
              />
              </Message>
            )}
            <RecoveryPlanCards plans={recoveryPlans} loading={actionLoading} onAction={onRecoveryAction} />
          </div>
        </Spin>
      </div>

      {!readOnly && <footer className={styles.composerWrap}>
        <div className={styles.composer}>
          <Input.TextArea
            autoSize={{ minRows: 1, maxRows: 4 }}
            value={messageText}
            disabled={readOnly}
            maxLength={2000}
            placeholder={detail.state === "RESOLVED" ? "会话已解决" : detail.state === "STOPPED" ? "会话已停止" : "补充事实、纠正结论，或要求重新分析"}
            onChange={(event) => onMessageChange(event.target.value)}
            onKeyDown={handleKeyDown}
            aria-label="会话输入"
          />
          <div className={styles.composerFooter}>
            <Segmented
              size="small"
              value={sendMode}
              onChange={setSendMode}
              aria-label="消息处理方式"
              options={[
                { label: "仅回答", value: "answer" },
                { label: "继续调查", value: "investigate" },
              ]}
            />
            <span className={styles.composerHint}>补充现象、时间点或纠正结论 · Enter 发送</span>
            <Button
              className={styles.sendButton}
              type="primary"
              size="small"
              icon={<SendOutlined />}
              loading={actionLoading}
              disabled={!messageText.trim() || readOnly || actionLoading}
              onClick={() => onSend(sendMode)}
            >
              {sendMode === "investigate" ? "发送并调查" : "发送"}
            </Button>
          </div>
        </div>
      </footer>}
    </div>
  );
}
