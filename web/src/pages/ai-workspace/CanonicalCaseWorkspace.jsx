import { Empty, Tag } from "antd";

import styles from "../AIDiagnosis.module.css";

function count(value) {
  return Array.isArray(value) ? value.length : 0;
}

function compact(value, fallback = "—") {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "string" || typeof value === "number") return String(value);
  return value.summary || value.title || value.label || value.status || fallback;
}

function WorkspaceList({ items, empty, render }) {
  if (!items?.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={empty} />;
  return <div className={styles.workspaceList}>{items.slice(0, 8).map(render)}</div>;
}

/** Canonical Workspace Snapshot projection. Every card is backed by /workspace. */
export default function CanonicalCaseWorkspace({ workspace, connected }) {
  if (!workspace) return null;
  const graph = workspace.causal_graph || {};
  const graphNodes = graph.nodes || [];
  const graphEdges = graph.edges || [];
  const campaign = workspace.campaign || {};
  const conclusion = workspace.conclusion || null;
  const revisions = workspace.revisions || {};

  return (
    <section className={styles.canonicalWorkspace} aria-label="Case Workspace" data-testid="canonical-workspace">
      <header className={styles.workspaceHeader}>
        <div>
          <div className={styles.workspaceEyebrow}>Canonical Workspace Snapshot</div>
          <div className={styles.workspaceTitle}>调查事实与控制面</div>
        </div>
        <div className={styles.workspaceHeaderMeta}>
          <Tag color={connected ? "green" : "orange"}>{connected ? "实时同步" : "自动重连中"}</Tag>
          <span>事件序号 {workspace.last_event_seq || 0}</span>
          <span>投影 v{workspace.case_projection_version || 0}</span>
        </div>
      </header>

      <div className={styles.workspaceRevisionBar} aria-label="控制版本">
        <span>命令 r{revisions.case_command || 0}</span>
        <span>控制 r{revisions.control || 0}</span>
        <span>范围 r{revisions.scope || 0}</span>
        <span>计划 r{revisions.plan || 0}</span>
        <span>Runtime {workspace.engine?.state || "IDLE"}</span>
      </div>

      <div className={styles.workspaceGrid}>
        <article className={styles.workspaceCard} aria-label="Evidence 预览">
          <h3>Evidence <Tag>{count(workspace.evidence)}</Tag></h3>
          <WorkspaceList items={workspace.evidence} empty="暂无证据" render={(item) => (
            <div className={styles.workspaceRow} key={item.evidence_id}>
              <strong>{item.collector_id || item.artifact_type || "Evidence"}</strong>
              <span>{compact(item.summary || item.projections?.[0]?.content?.summary, item.evidence_id)}</span>
              <Tag color={item.status === "VALID" ? "green" : "default"}>{item.status || "UNKNOWN"}</Tag>
            </div>
          )} />
        </article>

        <article className={styles.workspaceCard} aria-label="Campaign 与 Execution">
          <h3>Campaign / Execution <Tag>{count(workspace.executions)}</Tag></h3>
          <div className={styles.workspaceFact}>
            <strong>{campaign.campaign_id || "暂无活动 Campaign"}</strong>
            <span>{campaign.status || "尚未创建采集矩阵"}</span>
          </div>
          <WorkspaceList items={workspace.executions} empty="暂无执行单元" render={(item) => (
            <div className={styles.workspaceRow} key={item.execution_unit_id || item.unit_id}>
              <strong>{item.operation_id || item.collector_id || item.execution_unit_id}</strong>
              <span>{compact(item.target_ref || item.target)}</span>
              <Tag>{item.status || "PENDING"}</Tag>
            </div>
          )} />
        </article>

        <article className={styles.workspaceCard} aria-label="Causal Graph">
          <h3>Causal Graph</h3>
          <div className={styles.workspaceGraphSummary}>
            <strong>{count(graphNodes)} 节点</strong><span>{count(graphEdges)} 条受验证边</span>
          </div>
          <WorkspaceList items={graphEdges} empty="尚未形成因果边" render={(edge, index) => (
            <div className={styles.workspaceRow} key={edge.edge_id || `${edge.from_node_id}-${edge.to_node_id}-${index}`}>
              <strong>{edge.from_node_id || edge.source}</strong>
              <span>→ {edge.to_node_id || edge.target}</span>
              <Tag color={edge.status === "SUPPORTED" ? "green" : "default"}>{edge.status || edge.relation || "候选"}</Tag>
            </div>
          )} />
        </article>

        <article className={styles.workspaceCard} aria-label="Evidence Gap 与 Conclusion">
          <h3>Gap / Conclusion <Tag color={workspace.evidence_gaps?.length ? "orange" : "green"}>{count(workspace.evidence_gaps)}</Tag></h3>
          {conclusion ? (
            <div className={styles.workspaceConclusion}>
              <Tag color={conclusion.status === "CONFIRMED" ? "green" : "blue"}>{conclusion.status || "DRAFT"}</Tag>
              <strong>{compact(conclusion.summary || conclusion.statement, "已有结论修订")}</strong>
            </div>
          ) : <div className={styles.workspaceFact}>尚未形成可验证结论</div>}
          <WorkspaceList items={workspace.evidence_gaps} empty="没有开放证据缺口" render={(gap) => (
            <div className={styles.workspaceRow} key={gap.gap_id}>
              <strong>{gap.gap_type || "Evidence Gap"}</strong>
              <span>{compact(gap.description || gap.question, gap.gap_id)}</span>
              <Tag color="orange">{gap.status || "OPEN"}</Tag>
            </div>
          )} />
        </article>
      </div>
    </section>
  );
}
