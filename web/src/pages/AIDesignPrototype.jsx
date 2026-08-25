import { useMemo, useState } from "react";
import {
  Alert,
  Button,
  Input,
  Modal,
  Segmented,
  Space,
  Tag,
  Tooltip,
  message,
} from "antd";
import {
  AimOutlined,
  ArrowRightOutlined,
  CheckOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  DeploymentUnitOutlined,
  ExclamationCircleOutlined,
  EyeOutlined,
  FileSearchOutlined,
  GlobalOutlined,
  InfoCircleOutlined,
  MessageOutlined,
  MoreOutlined,
  PauseCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  SendOutlined,
  ShareAltOutlined,
  ThunderboltOutlined,
  UnorderedListOutlined,
} from "@ant-design/icons";
import styles from "./AIDesignPrototype.module.css";

const STATUS = {
  RUNNING: { label: "进行中", tone: "blue" },
  WAITING_USER: { label: "需要确认", tone: "gold" },
  WAITING_APPROVAL: { label: "等待批准", tone: "orange" },
  COMPLETED: { label: "已完成", tone: "green" },
  RULED_OUT: { label: "已排除", tone: "default" },
  OPEN: { label: "开放", tone: "blue" },
  SUPPORTED: { label: "支持中", tone: "blue" },
  WAITING_EVIDENCE: { label: "等待证据", tone: "gold" },
  INVALIDATED: { label: "已失效", tone: "red" },
  ABANDONED: { label: "已放弃", tone: "default" },
  INSUFFICIENT_EVIDENCE: { label: "证据不足", tone: "gold" },
  STALE: { label: "需要重新验证", tone: "red" },
};

const BRANCHES = [
  {
    id: "global",
    title: "Case 全局观察",
    subtitle: "3 个调查方向",
    status: "RUNNING",
    icon: <GlobalOutlined />,
  },
  {
    id: "network",
    title: "网络重传",
    subtitle: "验证 checkout 延迟的网络路径",
    status: "RUNNING",
    icon: <DeploymentUnitOutlined />,
    badge: "2/3",
  },
  {
    id: "cpu",
    title: "进程 CPU 热点",
    subtitle: "确认 api-2 的请求处理热点",
    status: "WAITING_USER",
    icon: <ThunderboltOutlined />,
    badge: "需要范围",
  },
  {
    id: "db",
    title: "数据库锁等待",
    subtitle: "检查数据库是否为传播末端",
    status: "RULED_OUT",
    icon: <CloseCircleOutlined />,
    badge: "已排除",
  },
];

const INITIAL_TREE_NODES = [
  { id: "cycle-2", nodeType: "CYCLE", title: "第 2 轮调查", source: "Evidence 变化后重新开始", status: "OPEN", summary: "围绕 checkout 延迟升高重新验证当前有效依据。", parentId: null },
  { id: "hyp-network", nodeType: "HYPOTHESIS", hypothesisId: "H-12", title: "网络重传导致请求等待", source: "候选解释", status: "SUPPORTED", summary: "已有跨节点重传与延迟时间窗支持，但仍缺时间和实例对齐。", parentId: "cycle-2", evidenceRefs: ["E-204", "E-188"] },
  { id: "obligation-network", nodeType: "OBLIGATION", title: "对齐 api-2 与 checkout 的时间和实例身份", source: "证据缺口", status: "WAITING_EVIDENCE", summary: "该信息决定网络观测能否支持当前解释。", parentId: "hyp-network", evidenceRefs: ["E-204", "E-188"] },
  { id: "hyp-cpu", nodeType: "HYPOTHESIS", hypothesisId: "H-09", title: "api-2 进程 CPU 争用", source: "候选解释", status: "OPEN", summary: "当前只有相关延迟时间窗，尚未获得调用栈或热点证据。", parentId: "cycle-2", evidenceRefs: ["E-188"] },
  { id: "obligation-cpu", nodeType: "OBLIGATION", title: "确认请求处理路径上的 CPU 热点", source: "证据缺口", status: "OPEN", summary: "需要调用栈或 Profile 结果。", parentId: "hyp-cpu", evidenceRefs: [] },
  { id: "hyp-db", nodeType: "HYPOTHESIS", hypothesisId: "H-03", title: "数据库锁等待是主要根因", source: "候选解释", status: "RULED_OUT", summary: "锁等待观测未超过基线，当前不支持该解释。", parentId: "cycle-2", evidenceRefs: ["E-171"] },
];

const INITIAL_EVIDENCE_ITEMS = [
  { id: "E-204", title: "跨节点重传观测", source: "network_discovery · api-2", summary: "api-2 → checkout 的重传在故障窗口明显升高。", lifecycle: "ACTIVE", trust: "当前依据", quality: "时间窗部分对齐" },
  { id: "E-188", title: "服务延迟时间窗", source: "sys_metrics · checkout", summary: "延迟升高与重传峰值存在重叠。", lifecycle: "ACTIVE", trust: "当前依据", quality: "时钟质量待确认" },
  { id: "E-171", title: "数据库锁等待", source: "query · database", summary: "锁等待未超过基线。", lifecycle: "ACTIVE", trust: "反向依据", quality: "当前窗口" },
];

const INITIAL_HYPOTHESIS_RELATIONS = [
  { id: "rel-network-cpu", from: "hyp-network", to: "hyp-cpu", relation: "ALTERNATIVE_TO", label: "备选解释" },
  { id: "rel-network-db", from: "hyp-network", to: "hyp-db", relation: "ALTERNATIVE_TO", label: "备选解释" },
];

const BRANCH_MESSAGES = {
  network: [
    { who: "ai", time: "10:06", text: "我目前更支持网络重传方向，但还不能把通信观测直接当作根因。下一步需要补齐时间对齐和目标实例身份。", refs: ["E-204", "E-188"], actions: ["查看依据", "要求补证"] },
    { who: "user", time: "10:07", text: "api-2 在 10:05 左右刚做过一次发布，优先确认发布前后网络指标是否变化。", effective: "已纳入当前分支" },
    { who: "ai", time: "10:08", text: "收到。我会把发布前后对比加入本分支的下一个信息目标，不改变其他分支的调查范围。", refs: ["发布前后差分"], actions: ["查看调查目标"] },
  ],
  cpu: [
    { who: "ai", time: "10:02", text: "当前分支还缺少已确认的 Worker 和 PID。为了避免采集错误进程，请先确认 api-2 的实例范围。", actions: ["确认范围"] },
  ],
  db: [
    { who: "ai", time: "09:58", text: "数据库锁等待没有超过基线，当前证据支持将它标记为已排除，但保留历史记录供回放。", refs: ["E-171"], actions: ["查看依据"] },
  ],
  global: [
    { who: "ai", time: "10:08", text: "当前有两个方向仍在推进。网络方向证据最多，但时钟和实例身份尚未完全对齐；CPU 方向等待范围确认；数据库锁等待已排除。", refs: ["网络重传", "进程 CPU 热点", "数据库锁等待"], actions: ["查看调查方向"] },
    { who: "user", time: "10:09", text: "从全局看，先不要创建新的高风险动作，只继续低风险读操作。", effective: "已纳入 Case 控制" },
  ],
};

function statusMeta(status) {
  return STATUS[status] || { label: status, tone: "default" };
}

function StatusTag({ status }) {
  const meta = statusMeta(status);
  return <Tag color={meta.tone}>{meta.label}</Tag>;
}

function SectionHeader({ icon, title, subtitle, action }) {
  return <div className={styles.sectionHeader}>
    <div className={styles.sectionTitle}><span className={styles.sectionIcon}>{icon}</span><div><strong>{title}</strong>{subtitle && <small>{subtitle}</small>}</div></div>
    {action}
  </div>;
}

function BranchRail({ activeId, onChange, onCreate }) {
  return <aside className={styles.branchRail}>
    <div className={styles.railHeading}><div><span className={styles.eyebrow}>CASE WORKSPACE</span><h2>调查上下文</h2></div><Tooltip title="创建隔离调查分支"><Button type="text" icon={<PlusOutlined />} aria-label="创建隔离调查分支" onClick={onCreate} /></Tooltip></div>
    <div className={styles.caseSummary}><span className={styles.caseStatusDot} /><div><strong>checkout 延迟升高</strong><small>production · 更新于刚刚</small></div></div>
    <div className={styles.railLabel}>观察视角</div>
    <div className={styles.branchList}>
      {BRANCHES.map((branch) => {
        const active = branch.id === activeId;
        return <button type="button" className={`${styles.branchItem} ${active ? styles.branchItemActive : ""}`} key={branch.id} onClick={() => onChange(branch.id)}>
          <span className={`${styles.branchIcon} ${branch.id === "global" ? styles.branchIconGlobal : ""}`}>{branch.icon}</span>
          <span className={styles.branchCopy}><strong>{branch.title}</strong><small>{branch.subtitle}</small></span>
          {branch.badge && <span className={styles.branchBadge}>{branch.badge}</span>}
        </button>;
      })}
    </div>
    <div className={styles.railFooter}><span className={styles.liveDot} />实时事件同步 <span className={styles.railFooterMeta}>Case #checkout-042</span></div>
  </aside>;
}

function OverviewStrip({ scope, onPause, onOpenControl }) {
  return <div className={styles.overviewStrip}>
    <div className={styles.overviewLead}><span className={styles.livePulse} /><div><span className={styles.eyebrow}>{scope === "global" ? "CASE 全局观察" : "当前分支调查"}</span><strong>{scope === "global" ? "2 个方向正在推进，1 个方向已排除" : "网络重传 · 正在验证"}</strong></div></div>
    <div className={styles.overviewMetric}><span>Evidence</span><strong>2 条当前依据</strong></div>
    <div className={styles.overviewMetric}><span>判断</span><strong>部分支持</strong></div>
    <div className={styles.overviewMetric}><span>用户动作</span><strong className={styles.metricWarning}>需要 1 项确认</strong></div>
    <Space size={6}><Button size="small" icon={<PauseCircleOutlined />} onClick={onPause}>暂停</Button><Button size="small" icon={<MoreOutlined />} aria-label="调查控制" onClick={onOpenControl} /></Space>
  </div>;
}

function EvidenceTree({ nodes, evidence, relations, selected, onSelect, onEnterLine, onImpact }) {
  const root = nodes.find((item) => item.nodeType === "CYCLE") || nodes[0];
  const hypotheses = nodes.filter((item) => item.nodeType === "HYPOTHESIS");
  const children = (parentId) => nodes.filter((item) => item.parentId === parentId);
  const evidenceMap = new Map(evidence.map((item) => [item.id, item]));
  return <section className={`${styles.sectionBlock} ${styles.treeSection}`}>
    <SectionHeader icon={<UnorderedListOutlined />} title="调查树" subtitle="真实树结构由调查代际、假设、信息义务和声明组成；Evidence 作为依赖引用挂在节点上" action={<Space size={6}><span className={styles.treeLegend}><i className={styles.legendNode} />调查节点 <i className={styles.legendEdge} />假设关系</span><Button type="link" size="small" onClick={() => onSelect({ type: "tree", id: root.id })}>回到调查根</Button></Space>} />
    <Alert className={styles.treeSemantics} type="info" showIcon message="关系语义已分层" description="服务依赖图只表示观测关系，不直接证明因果；只有经过引用校验的 Causal Graph 才会显示因果边。" />
    <div className={styles.treeCanvas} role="tree" aria-label="Case 调查树">
      <button type="button" className={`${styles.treeRoot} ${selected?.id === root.id ? styles.treeNodeSelected : ""}`} onClick={() => onSelect({ type: "tree", id: root.id })}>
        <span className={styles.nodeGlyph}><GlobalOutlined /></span><span><small>调查代际</small><strong>{root.title}</strong><em>{root.summary}</em></span><StatusTag status={root.status} />
      </button>
      <div className={styles.treeTrunk} />
      <div className={styles.treeBranches}>
        {hypotheses.map((hypothesis) => {
          const obligations = children(hypothesis.id);
          const selectedHypothesis = selected?.id === hypothesis.id;
          return <div className={styles.treeBranch} key={hypothesis.id}>
            <span className={styles.branchConnector} />
            <button type="button" className={`${styles.treeEdge} ${selectedHypothesis ? styles.treeEdgeSelected : ""}`} onClick={() => onSelect({ type: "hypothesis", id: hypothesis.id })} aria-label={`打开候选解释 ${hypothesis.title}`}>
              <span className={`${styles.edgeState} ${styles[`edgeState${hypothesis.status}`]}`} /><span><small>候选解释</small><strong>{hypothesis.title}</strong><em>{hypothesis.summary}</em></span><StatusTag status={hypothesis.status} />
            </button>
            <div className={styles.obligationStack}>
              {obligations.map((obligation) => <button type="button" className={`${styles.obligationNode} ${selected?.id === obligation.id ? styles.treeNodeSelected : ""}`} key={obligation.id} onClick={() => onSelect({ type: "tree", id: obligation.id })}>
                <span className={styles.nodeGlyph}><AimOutlined /></span><span><small>信息义务</small><strong>{obligation.title}</strong><em>{obligation.summary}</em></span><StatusTag status={obligation.status} />
              </button>)}
            </div>
            <div className={styles.evidenceRefs}><small>当前引用依据</small><div>{(hypothesis.evidenceRefs || []).map((id) => { const item = evidenceMap.get(id); return item ? <button type="button" key={id} onClick={() => onSelect({ type: "evidence", id })}>{item.title}</button> : null; })}{!hypothesis.evidenceRefs?.length && <span>尚无 Evidence</span>}</div></div>
          </div>;
        })}
      </div>
    </div>
    <div className={styles.relationStrip}><span className={styles.relationLabel}>候选解释之间</span>{relations.map((relation) => { const from = nodes.find((item) => item.id === relation.from); const to = nodes.find((item) => item.id === relation.to); return <button type="button" key={relation.id} className={`${styles.relationLink} ${selected?.id === relation.id ? styles.treeEdgeSelected : ""}`} onClick={() => onSelect({ type: "relation", id: relation.id })}><strong>{from?.title}</strong><span>{relation.label}</span><strong>{to?.title}</strong></button>; })}</div>
    {selected && <SelectionInspector selected={selected} nodes={nodes} evidence={evidence} relations={relations} onEnterLine={onEnterLine} onImpact={onImpact} onClear={() => onSelect(null)} />}
  </section>;
}

function SelectionInspector({ selected, nodes, evidence, relations, onEnterLine, onImpact, onClear }) {
  const item = selected.type === "evidence" ? evidence.find((entry) => entry.id === selected.id) : selected.type === "relation" ? relations.find((entry) => entry.id === selected.id) : nodes.find((entry) => entry.id === selected.id);
  if (!item) return null;
  const isEvidence = selected.type === "evidence";
  const isHypothesis = selected.type === "hypothesis";
  const isRelation = selected.type === "relation";
  const title = isEvidence ? item.title : isRelation ? item.label : item.title;
  const summary = isEvidence ? item.summary : isRelation ? "这是候选解释之间的结构关系，不等于因果证明。" : item.summary;
  return <div className={styles.selectionInspector}>
    <div className={styles.inspectorLead}><span className={isEvidence ? styles.nodeInspectorIcon : styles.edgeInspectorIcon}>{isEvidence ? <FileSearchOutlined /> : <AimOutlined />}</span><div><span className={styles.eyebrow}>{isEvidence ? "当前依据" : isHypothesis ? "候选解释" : isRelation ? "假设关系" : "调查节点"}</span><strong>{title}</strong><small>{isEvidence ? item.source : item.nodeType || item.relation || ""}</small></div><Button type="text" size="small" icon={<CloseCircleOutlined />} aria-label="关闭选中对象" onClick={onClear} /></div>
    <p className={styles.inspectorSummary}>{summary}</p>
    <div className={styles.inspectorActions}>{isEvidence ? <><Button type="primary" size="small" icon={<MessageOutlined />} onClick={() => onEnterLine(item.id)}>进入证据线</Button><Button size="small" icon={<EyeOutlined />} onClick={() => onImpact(item, "inspect")}>查看投影与引用</Button><Button danger type="text" size="small" icon={<CloseCircleOutlined />} onClick={() => onImpact(item, "exclude")}>排除当前依据</Button></> : isHypothesis ? <><Button size="small" icon={<CheckOutlined />} onClick={() => onImpact(item, "support")}>采纳解释</Button><Button size="small" icon={<CloseCircleOutlined />} onClick={() => onImpact(item, "disprove")}>标记不成立</Button><Button type="link" size="small" onClick={() => onImpact(item, "ask")}>要求 AI 补证</Button></> : <><Button type="primary" size="small" icon={<MessageOutlined />} onClick={() => onImpact(item, "discuss")}>围绕此节点对话</Button><Button size="small" onClick={() => onImpact(item, "transition")}>修改节点状态</Button></>}</div>
  </div>;
}

function EvidenceLinePanel({ node, evidence, treeNodes }) {
  const item = evidence.find((entry) => entry.id === node.id);
  const related = treeNodes.filter((entry) => entry.evidenceRefs?.includes(node.id) && entry.nodeType === "HYPOTHESIS");
  return <section className={styles.linePanel}>
    <SectionHeader icon={<FileSearchOutlined />} title="证据线" subtitle="从当前依据回溯到投影、审查、引用它的推理和下一步缺口" action={<Tag color="green">当前输入</Tag>} />
    <div className={styles.lineSteps}><div><small>依据</small><strong>{item?.title}</strong><span>{item?.source}</span></div><ArrowRightOutlined /><div><small>质量与审查</small><strong>{item?.trust}</strong><span>{item?.quality}</span></div><ArrowRightOutlined /><div><small>推理用途</small><strong>{related.length ? "已绑定候选解释" : "尚未绑定推理"}</strong><span>排除会触发局部失效传播</span></div></div>
    <div className={styles.lineEvidenceMeta}><span>生命周期：{item?.lifecycle || "ACTIVE"}</span><span>原始 Artifact：保留</span><span>历史 Projection：可回放</span><Button type="link" size="small">打开完整 EvidenceDrawer</Button></div>
  </section>;
}

function Conversation({ messages, scope, draft, setDraft, onSend, onImpact, evidenceLineNode, evidence }) {
  const [sendMode, setSendMode] = useState("queue");
  return <section className={styles.conversationSection}>
    <SectionHeader icon={<MessageOutlined />} title={evidenceLineNode ? `AI 对话 · ${evidenceLineNode.title}` : scope === "global" ? "Case 全局观察" : "当前分支会话"} subtitle={evidenceLineNode ? "AI 只会围绕当前证据线回答，并可将有效沟通写回 Case 修订" : scope === "global" ? "默认只读取各方向摘要，可明确进入分支" : "消息可以只讨论，也可以纳入当前调查"} action={<Tag icon={<ClockCircleOutlined />} color="blue">实时</Tag>} />
    <div className={styles.messageList} aria-live="polite">
      {messages.map((item, index) => <div className={`${styles.messageRow} ${item.who === "user" ? styles.messageRowUser : ""}`} key={`${item.time}-${index}`}>
        <span className={`${styles.avatar} ${item.who === "ai" ? styles.avatarAi : ""}`}>{item.who === "ai" ? "AI" : "我"}</span>
        <div className={styles.messageBody}><div className={styles.messageMeta}><strong>{item.who === "ai" ? "Mini-Drop" : "你"}</strong><span>{item.time}</span>{item.effective && <Tag color="green">{item.effective}</Tag>}</div><p>{item.text}</p>{item.refs && <div className={styles.referenceChips}>{item.refs.map((ref) => { const linked = evidence.find((entry) => entry.id === ref); return <button type="button" key={ref} onClick={() => onImpact({ id: ref, title: linked?.title || ref }, "inspect")}>{linked?.title || ref}</button>; })}</div>}{item.actions && <div className={styles.messageActions}>{item.actions.map((action) => <Button type="link" size="small" key={action} onClick={() => onImpact(item, action)}>{action}</Button>)}{item.who === "ai" && <Button type="link" size="small" onClick={() => onImpact(item, "纳入调查")}>纳入调查</Button>}</div>}</div>
      </div>)}
    </div>
    <div className={styles.composer}><Input.TextArea value={draft} onChange={(event) => setDraft(event.target.value)} onPressEnter={(event) => { if (!event.shiftKey) { event.preventDefault(); onSend(sendMode); } }} autoSize={{ minRows: 2, maxRows: 5 }} placeholder={scope === "global" ? "从全局观察提问，或指定一个分支进行修正…" : "补充事实、纠正判断，或要求 AI 重新规划…"} /><div className={styles.composerFooter}><span className={styles.composerHint}><InfoCircleOutlined /> Enter 发送 · Shift+Enter 换行</span><Space size={6}><Segmented size="small" value={sendMode} onChange={setSendMode} options={[{ label: "排队", value: "queue" }, { label: "立即转向", value: "steer" }]} /><Button type="primary" icon={<SendOutlined />} onClick={() => onSend(sendMode)}>发送</Button></Space></div></div>
  </section>;
}

function ImpactModal({ impact, onClose, onApply }) {
  if (!impact) return null;
  const isEvidence = impact.kind === "evidence";
  const isTreeNode = impact.kind === "tree-node";
  const title = isTreeNode ? `修改调查节点：${impact.item.title || "当前节点"}` : isEvidence ? `处理当前依据：${impact.item.title || impact.item.id}` : `修改 ${impact.item.title || "当前判断"}`;
  return <Modal open title={title} onCancel={onClose} footer={null} width={620} className={styles.impactModal}>
    <div className={styles.impactLead}><span className={styles.impactIcon}><ExclamationCircleOutlined /></span><div><strong>{impact.actionLabel}</strong><p>{impact.description}</p></div></div>
    <div className={styles.impactGrid}>
      <div><small>直接影响</small><strong>{isTreeNode ? "通过 InvestigationTree 状态机迁移" : isEvidence ? "当前依据从有效输入中排除" : "创建新的判断修订，不覆盖历史"}</strong></div>
      <div><small>判断影响</small><strong>{isTreeNode ? "后代义务和声明可能被标记为放弃" : isEvidence ? "引用它的假设、分析和结论进入失效传播" : "当前假设状态变为需要重新验证"}</strong></div>
      <div><small>调查影响</small><strong>{isTreeNode ? "保留旧代际，重新打开可行的调查前沿" : isEvidence ? "新建 revision / generation，旧写入拒绝" : "下一信息目标将重新计算"}</strong></div>
      <div><small>不会影响</small><strong>原始 Artifact、其他分支和历史时间线</strong></div>
    </div>
    <Alert type="info" showIcon message="服务端会以当前 revision 再次校验影响范围" description="确认后会生成新的调查代际；如果页面已过期，系统会保留你的草稿并要求重新加载。" />
    <div className={styles.impactActions}><Button onClick={onClose}>取消</Button>{!isTreeNode && <Button onClick={() => onApply("branch")}>只在当前分支应用</Button>}<Button type="primary" danger={isEvidence} onClick={() => onApply("apply")}>{isTreeNode ? "提交状态迁移" : isEvidence ? "确认排除并回溯" : "确认并重新调查"}</Button></div>
  </Modal>;
}

export default function AIDesignPrototype() {
  const [activeId, setActiveId] = useState("network");
  const [draft, setDraft] = useState("");
  const [impact, setImpact] = useState(null);
  const [messages, setMessages] = useState(BRANCH_MESSAGES.network);
  const [treeNodes, setTreeNodes] = useState(INITIAL_TREE_NODES);
  const [evidenceItems, setEvidenceItems] = useState(INITIAL_EVIDENCE_ITEMS);
  const [hypothesisRelations] = useState(INITIAL_HYPOTHESIS_RELATIONS);
  const [selectedGraphItem, setSelectedGraphItem] = useState({ type: "evidence", id: "E-204" });
  const [evidenceLineId, setEvidenceLineId] = useState(null);

  const activeBranch = useMemo(() => BRANCHES.find((item) => item.id === activeId) || BRANCHES[1], [activeId]);
  const scope = activeId === "global" ? "global" : "branch";

  function selectBranch(id) {
    setActiveId(id);
    setMessages(BRANCH_MESSAGES[id] || BRANCH_MESSAGES.global);
    setDraft("");
  }

  function openImpact(item, action) {
    if (action === "inspect" || action === "查看依据" || action === "查看调查目标") {
      message.info("原型中会打开对应 EvidenceDrawer，展示来源、质量、时间窗和引用关系");
      return;
    }
    if (action === "discuss") {
      message.info("真实实现会把当前调查节点写入对话 scope，并保持现有 Case revision");
      return;
    }
    if (action === "transition") {
      setImpact({ item, kind: "tree-node", actionLabel: "修改调查节点状态", description: "调查树节点只能通过服务端状态机迁移，不能从前端物理删除。" });
      return;
    }
    const kind = action === "exclude" ? "evidence" : item.hypothesisId || action === "support" || action === "disprove" || action === "ask" ? "hypothesis" : "hypothesis";
    const labels = { exclude: "排除当前依据", low: "降低可信度", restore: "恢复使用", disprove: "标记解释不成立", support: "采纳当前解释", ask: "要求 AI 补充依据", "纳入调查": "将这段沟通纳入当前调查" };
    setImpact({ item, kind, actionLabel: labels[action] || action, description: kind === "evidence" ? "这不是删除原始数据，而是改变它是否能继续支持当前分支推理。" : "这会把对话里的判断转成结构化 Hypothesis 修订，并触发下一步重规划。" });
  }

  function applyImpact(mode) {
    if (impact?.kind === "tree-node") {
      setTreeNodes((items) => items.map((item) => item.id === impact.item.id ? { ...item, status: "ABANDONED" } : item));
      setImpact(null);
      message.success("已提交调查节点状态迁移，历史事件仍可回放");
      setImpact(null);
      return;
    }
    if (impact?.kind === "evidence" && impact.item.id) {
      setEvidenceItems((items) => items.map((item) => item.id === impact.item.id ? { ...item, lifecycle: "EXCLUDED", trust: "已排除" } : item));
      setTreeNodes((items) => items.map((item) => item.evidenceRefs?.includes(impact.item.id) ? { ...item, status: item.nodeType === "HYPOTHESIS" ? "INVALIDATED" : "ABANDONED" } : item));
    }
    if (impact?.kind === "hypothesis" && impact.item.id) {
      const nextStatus = impact.actionLabel.includes("不成立") ? "RULED_OUT" : impact.actionLabel.includes("采纳") ? "SUPPORTED" : "OPEN";
      setTreeNodes((items) => items.map((item) => item.id === impact.item.id ? { ...item, status: nextStatus } : item));
    }
    setMessages((items) => [...items, { who: "user", time: "刚刚", text: `${impact.actionLabel}：${impact.item.title || impact.item.id}`, effective: mode === "branch" ? "已纳入当前分支" : "已生成新调查修订" }]);
    setImpact(null);
    message.success(mode === "branch" ? "已在当前分支应用" : "已生成新的调查修订");
  }

  function sendMessage(mode) {
    const text = draft.trim();
    if (!text) return;
    setMessages((items) => [...items, { who: "user", time: "刚刚", text, effective: mode === "steer" ? "立即转向" : "已排队" }]);
    setDraft("");
    message.info(mode === "steer" ? "已请求立即转向当前调查" : "已加入当前调查队列");
  }

  function enterEvidenceLine(nodeId) {
    setEvidenceLineId(nodeId);
    setSelectedGraphItem({ type: "evidence", id: nodeId });
    message.success("已进入当前依据的证据线会话");
  }

  const evidenceLineNode = evidenceItems.find((item) => item.id === evidenceLineId);

  return <div className={styles.page}>
    <header className={styles.topbar}><div className={styles.brand}><span className={styles.brandMark}><ThunderboltOutlined /></span><div><strong>Mini-Drop</strong><small>Evidence Investigation Prototype</small></div></div><div className={styles.topbarTitle}><span>交互方案预览</span><Tag color="blue">本地模拟数据</Tag></div><div className={styles.topbarActions}><Button size="small" icon={<ReloadOutlined />} onClick={() => message.info("原型状态已保留，真实页面会从 Workspace Snapshot 刷新")}>刷新状态</Button><Button size="small" icon={<ShareAltOutlined />} onClick={() => message.info("真实实现将生成 Case 快照分享链接")}>分享观察</Button></div></header>
    <div className={styles.layout}>
      <BranchRail activeId={activeId} onChange={selectBranch} onCreate={() => message.info("真实实现将创建新的隔离 Branch Workspace")} />
      <main className={styles.main}>
        <div className={styles.contextBar}><div><span className={styles.crumb}>Case / checkout-042</span><h1>{activeBranch.title}</h1><p>{activeBranch.subtitle}</p></div><Space size={8}><StatusTag status={activeBranch.status} /><Button size="small" icon={<EyeOutlined />} onClick={() => selectBranch("global")}>退出到全局观察</Button></Space></div>
        <OverviewStrip scope={scope} onPause={() => message.info("真实实现将提交 PAUSE Command，并等待服务端事件确认")} onOpenControl={() => message.info("真实实现将打开调查控制抽屉：暂停、停止、改范围、锁定步骤")}/>
        <div className={styles.contentScroll}>
          {scope === "global" && <div className={styles.globalNotice}><GlobalOutlined /><div><strong>你正在看 Case 的全局摘要</strong><span>跨分支内容默认只以摘要呈现。点击某个方向后，才会进入它的分支会话。</span></div><Button size="small" onClick={() => selectBranch("network")}>进入网络分支</Button></div>}
          <EvidenceTree nodes={treeNodes} evidence={evidenceItems} relations={hypothesisRelations} selected={selectedGraphItem} onSelect={setSelectedGraphItem} onEnterLine={enterEvidenceLine} onImpact={openImpact} />
          {evidenceLineNode && <><div className={styles.evidenceLineBanner}><span className={styles.linePulse} /><div><span className={styles.eyebrow}>当前证据线</span><strong>{evidenceLineNode.title}</strong><small>当前会话只围绕此依据的投影、审查和引用关系展开</small></div><Button size="small" onClick={() => setEvidenceLineId(null)}>退出证据线</Button></div><EvidenceLinePanel node={evidenceLineNode} evidence={evidenceItems} treeNodes={treeNodes} /></>}
          <Conversation messages={messages} scope={scope} draft={draft} setDraft={setDraft} onSend={sendMessage} onImpact={openImpact} evidenceLineNode={evidenceLineNode} evidence={evidenceItems} />
          <div className={styles.prototypeFooter}><InfoCircleOutlined /><span>这是交互原型：按钮只改变本地演示状态，不会创建真实 Task、Evidence 或 Case Command。</span></div>
        </div>
      </main>
    </div>
    <ImpactModal impact={impact} onClose={() => setImpact(null)} onApply={applyImpact} />
  </div>;
}
