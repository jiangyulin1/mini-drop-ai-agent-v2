import { useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  Button,
  Dropdown,
  Input,
  Modal,
  Popconfirm,
  Spin,
  Tag,
  Tooltip,
  message,
} from "antd";
import { verifyCaseRecovery } from "../../api/client";
import {
  CheckOutlined,
  DatabaseOutlined,
  EyeOutlined,
  MoreOutlined,
  PauseOutlined,
  PlayCircleOutlined,
  SendOutlined,
} from "@ant-design/icons";
import styles from "../AIDiagnosis.module.css";
import { confidenceGuide, findingSummaries, humanDiagnosis } from "../../utils/diagnosisHumanize";
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

function CurrentResult({ diagnosis, caseId, onOpenTechnical, onOpenScope }) {
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
      <div className={styles.cardActions}>
        <Button type="primary" icon={<EyeOutlined />} onClick={onOpenTechnical}>查看证据与采集详情</Button>
      </div>
    </div>
  );
}

export default function CaseConversation({
  detail,
  events,
  diagnosis,
  loading,
  actionLoading,
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
}) {
  const state = CASE_STATE_META[detail.state] || { label: detail.state, color: "default", tone: "idle" };
  const status = DIAGNOSIS_STATUS_META[diagnosis?.status] || { label: diagnosis?.status, color: "default" };
  const userEvents = useMemo(() => (events || []).filter((event) => event.event_type !== "case_created"), [events]);
  const waitingProbes = (diagnosis?.probes || []).filter((probe) => probe.status === "WAITING_APPROVAL");
  const diagnosisRunning = diagnosis
    && !TERMINAL_DIAGNOSIS.has(diagnosis.status)
    && !["WAITING_APPROVAL", "PAUSED"].includes(diagnosis.status);
  const canStart = detail.state === "OPEN" && !detail.diagnosis_session_id;
  const readOnly = detail.state === "STOPPED" || detail.state === "RESOLVED";
  const agentLoop = detail.summary?.recovery?.agent_loop || detail.recovery?.agent_loop || {};
  const autonomous = detail.run_mode === "AUTHORIZED_AUTONOMY";
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
      if (messageText.trim() && !readOnly && !actionLoading) onSend();
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

            {diagnosisRunning && (
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
                    <div className={styles.cardDescription}>{probe.reason} · 约 {probe.parameters?.duration_sec || 0} 秒 · {probe.risk_level === "R2" ? "中风险，仅执行一次" : probe.risk_level}</div>
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
                <CurrentResult diagnosis={diagnosis} caseId={detail.case_id} onOpenTechnical={onOpenTechnical} onOpenScope={onOpenScope} />
              </Message>
            )}
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
            <span className={styles.composerHint}>补充现象、时间点或纠正结论 · Enter 发送</span>
            <Button className={styles.sendButton} type="primary" size="small" icon={<SendOutlined />} loading={actionLoading} disabled={!messageText.trim() || readOnly || actionLoading} onClick={onSend}>发送并分析</Button>
          </div>
        </div>
      </footer>}
    </div>
  );
}

export function LegacyConversation({ diagnosis, loading, onOpenScope, onOpenTechnical }) {
  const status = DIAGNOSIS_STATUS_META[diagnosis.status] || { label: diagnosis.status, color: "default" };
  return (
    <div className={styles.main}>
      <header className={styles.caseHeader}>
        <div className={styles.caseHeaderMain}><h1 className={styles.caseHeaderTitle}>{diagnosis.target_scope?.target_service || "历史诊断"}</h1><div className={styles.caseHeaderMeta}><Tag color={status.color}>{status.label}</Tag><span>旧诊断会话</span></div></div>
        {!diagnosis.latest_conclusion && <div className={styles.caseHeaderActions}><Button onClick={onOpenTechnical}>详情</Button></div>}
      </header>
      <div className={styles.conversationScroll}>
        <Spin spinning={loading}>
          <div className={styles.conversation}>
            <div className={styles.dayDivider}>历史诊断</div>
            <Message author="你" time={diagnosis.created_at}><p className={styles.messageText}>{diagnosis.raw_query}</p></Message>
            <Message ai author="Mini-Drop" time={diagnosis.updated_at}>
              <CurrentResult diagnosis={diagnosis} caseId="" onOpenTechnical={onOpenTechnical} onOpenScope={onOpenScope} />
            </Message>
          </div>
        </Spin>
      </div>
    </div>
  );
}
