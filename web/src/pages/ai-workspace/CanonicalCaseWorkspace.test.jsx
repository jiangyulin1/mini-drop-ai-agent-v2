import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import CanonicalCaseWorkspace from "./CanonicalCaseWorkspace";
import { getCaseInvestigationPlan, listCaseEvidenceReviews } from "../../api/client";

vi.mock("../../api/client", () => ({
  decideCaseCollectionProposal: vi.fn(),
  getCaseInvestigationPlan: vi.fn(),
  listCaseEvidenceReviews: vi.fn(),
}));

vi.mock("../../components/EvidenceDrawer", () => ({
  default: ({ open, focusCitation }) => open ? <div data-testid="mock-evidence-drawer">{focusCitation?.field_path || "Evidence 详情"}</div> : null,
}));

vi.mock("../../components/ExplainabilityDrawer", () => ({
  default: () => null,
}));

const WORKSPACE = {
  last_event_seq: 3,
  revisions: { case_command: 1, control: 1, scope: 1 },
  engine: { state: "IDLE" },
  information_goals: [{
    goal_id: "goal-1",
    title: "确认 CPU 热点",
    status: "WAITING_APPROVAL",
    source: "proposal",
  }],
  collection_proposals: [{
    proposal_id: "proposal-1",
    status: "PROPOSED",
    information_goal: "采集 CPU profile",
    validation_result: { awaiting_execution_authority: true },
  }],
  collection_requests: [],
  evidence: [{ evidence_id: "ev-current-123456789", status: "ACTIVE", artifact_type: "process_scan" }],
  dependency_graph: {
    graph_semantics: "dependency_only_not_causal",
    graph: {
      nodes: [{
        entity_id: "process:agent-local:boot-local:87453:1770000000",
        entity_type: "process",
        display_name: "/usr/bin/tcp-client",
        agent_id: "agent-local",
        process: { pid: 87453, executable: "/usr/bin/tcp-client" },
      }, {
        entity_id: "process:agent-local:boot-local:87445:1770000001",
        entity_type: "process",
        display_name: "/usr/bin/tcp-server",
        agent_id: "agent-local",
        process: { pid: 87445, executable: "/usr/bin/tcp-server" },
      }],
      edges: [{
        edge_id: "dep-local",
        source_entity: "process:agent-local:boot-local:87453:1770000000",
        target_entity: "process:agent-local:boot-local:87445:1770000001",
        relation: "calls",
        protocol: "tcp",
        destination_port: 19090,
        metrics: { connections: 1 },
        observation_points: ["client", "server"],
      }],
    },
    coverage: {
      projection_count: 3,
      node_count: 2,
      edge_count: 1,
      conclusion: "insufficient_coverage",
      items: [{ status: "partial", managed_fraction: 1 }],
    },
    limitations: [
      "macos_lsof_does_not_expose_linux_netns_cgroup_or_socket_inode",
      "dependency_edges_are_observations_not_causal_claims",
    ],
    evidence_refs: ["ev-current-123456789"],
  },
  evidence_analyses: [{
    analysis_run_id: "analysis-current",
    mode: "SINGLE",
    status: "COMPLETED",
    input_state: "CURRENT",
    evidence_inputs: [{ evidence_id: "ev-current-123456789", projection_hash: "hash-current", review_state: "ACTIVE" }],
    latency_ms: 420,
    completed_at: "2026-08-20T12:00:00Z",
    facts: [{
      claim: "CPU 热点集中在目标进程",
      certainty: "HIGH",
      citations: [{
        evidence_id: "ev-current-123456789",
        projection_hash: "hash-current",
        field_path: "summary",
        quote: "CPU hot",
        start: 0,
        end: 7,
      }, {
        evidence_id: "ev-missing",
        projection_hash: "hash-old",
        field_path: "signals.cpu",
      }],
    }],
    anomalies: [{ summary: "CPU 占用异常" }],
    interpretations: [{ summary: "需要结合调用栈" }],
    limitations: ["单一时间窗"],
    next_collection_proposals: [{ information_goal: "补充调用栈" }],
  }, {
    analysis_run_id: "analysis-running",
    mode: "BATCH",
    status: "RUNNING",
    input_state: "CURRENT",
    evidence_inputs: [],
    facts: [],
  }, {
    analysis_run_id: "analysis-stale",
    mode: "SINGLE",
    status: "COMPLETED",
    input_state: "STALE_INPUT",
    evidence_inputs: [],
    facts: [],
  }],
  conclusion: { state: "INSUFFICIENT_EVIDENCE", revision: 1 },
  conclusion_history: [{ conclusion_id: "conclusion-current", state: "INSUFFICIENT_EVIDENCE", revision: 1, revision_status: "CURRENT" }],
  recommendations: [{ recommendation_id: "rec-1", concrete_action: "继续只读采集" }],
};

describe("CanonicalCaseWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getCaseInvestigationPlan.mockResolvedValue({ goal: "确认根因", steps: [] });
    listCaseEvidenceReviews.mockResolvedValue({ items: [] });
  });

  it("uses one state-aware workspace navigation without completion checks", async () => {
    const { container } = render(<CanonicalCaseWorkspace workspace={WORKSPACE} caseId="case-1" connected onRefresh={vi.fn()} />);

    expect(await screen.findByText("当前调查路径")).toBeInTheDocument();
    expect(screen.getByText("当前假设")).toBeInTheDocument();
    expect(screen.getByText("失效传播")).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "信息目标，需处理，1 项" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Evidence，已就绪，1 项" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "依赖关系，覆盖有限，1 项" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "受引用分析，需处理，3 项" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "恢复建议，可查看，1 项" })).toBeInTheDocument();
    expect(screen.getAllByRole("tab")).toHaveLength(7);
    expect(container.querySelector(".ccw-stagebar .is-done")).toBeNull();
  });

  it("shows superseded conclusion revisions without hiding their Evidence links", async () => {
    const workspace = {
      ...WORKSPACE,
      conclusion: {
        conclusion_id: "conclusion-current",
        state: "PARTIALLY_CONFIRMED",
        revision: 2,
        report_text: "新 Evidence 到达后重新探索",
        claim_evidence_bindings: [],
      },
      conclusion_history: [
        {
          conclusion_id: "conclusion-current",
          state: "PARTIALLY_CONFIRMED",
          revision: 2,
          revision_status: "CURRENT",
          report_text: "新 Evidence 到达后重新探索",
          claim_evidence_bindings: [],
        },
        {
          conclusion_id: "conclusion-old",
          state: "CONFIRMED",
          revision: 1,
          revision_status: "SUPERSEDED",
          report_text: "旧结论已被新 Evidence 重新检查",
          claim_evidence_bindings: [{ claim_id: "claim-old", evidence_id: "ev-current-123456789" }],
        },
      ],
    };
    render(<CanonicalCaseWorkspace workspace={workspace} caseId="case-1" connected onRefresh={vi.fn()} />);
    fireEvent.click(await screen.findByRole("tab", { name: /结论修订/ }));
    expect(await screen.findByText("历史修订")).toBeInTheDocument();
    expect(screen.getByText("SUPERSEDED")).toBeInTheDocument();
    expect(screen.getByText("旧结论已被新 Evidence 重新检查")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /ev-current/ })).toBeInTheDocument();
  });

  it("shows the observed dependency direction, partial coverage and opens supporting Evidence", async () => {
    render(<CanonicalCaseWorkspace workspace={WORKSPACE} caseId="case-1" connected onRefresh={vi.fn()} />);
    fireEvent.click(await screen.findByRole("tab", { name: "依赖关系，覆盖有限，1 项" }));

    expect(await screen.findByText("覆盖有限：依赖不等于因果")).toBeInTheDocument();
    expect(screen.getAllByText("tcp-client")).toHaveLength(2);
    expect(screen.getAllByText("tcp-server")).toHaveLength(2);
    expect(screen.getByText("PID 87453")).toBeInTheDocument();
    expect(screen.getByText("1 次连接")).toBeInTheDocument();
    expect(screen.getByText("TCP · 端口 19090")).toBeInTheDocument();
    expect(screen.getByText("观测：客户端、服务端")).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
    expect(screen.getByText(/macOS 的 lsof 快照不提供/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /ev-current/ }));
    await waitFor(() => expect(screen.getByTestId("mock-evidence-drawer")).toHaveTextContent("Evidence 详情"));
  });

  it("explains that an empty dependency graph is not proof of no dependency", async () => {
    const emptyWorkspace = {
      ...WORKSPACE,
      dependency_graph: {
        graph: { nodes: [], edges: [] },
        coverage: { conclusion: "insufficient_coverage", items: [] },
        limitations: ["no_network_discovery_artifact_available"],
        evidence_refs: [],
      },
    };
    render(<CanonicalCaseWorkspace workspace={emptyWorkspace} caseId="case-1" connected onRefresh={vi.fn()} />);
    fireEvent.click(await screen.findByRole("tab", { name: "依赖关系，暂无数据，0 项" }));

    expect(await screen.findByText(/空态不代表系统中没有依赖/)).toBeInTheDocument();
    expect(screen.getByText("当前没有可用的网络发现产物。")).toBeInTheDocument();
  });

  it("groups analysis states and opens the complete citation context", async () => {
    render(<CanonicalCaseWorkspace workspace={WORKSPACE} caseId="case-1" connected onRefresh={vi.fn()} />);
    fireEvent.click(await screen.findByRole("tab", { name: /受引用分析/ }));

    expect(await screen.findByText("当前有效")).toBeInTheDocument();
    expect(screen.getByText("处理中")).toBeInTheDocument();
    expect(screen.getByText("历史与失效")).toBeInTheDocument();
    expect(screen.getByText("HIGH 确定性")).toBeInTheDocument();
    expect(screen.getByText("CPU 占用异常")).toBeInTheDocument();

    const unavailable = screen.getByRole("button", { name: /引用不可用/ });
    expect(unavailable).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /打开引用 summary/ }));
    await waitFor(() => expect(screen.getByTestId("mock-evidence-drawer")).toHaveTextContent("summary"));
  });

  it("uses only the newest Evidence review for trust and stage state", async () => {
    listCaseEvidenceReviews.mockResolvedValue({ items: [{
      evidence_id: "ev-current-123456789",
      decision: "RESTORED",
      review_revision: 2,
    }, {
      evidence_id: "ev-current-123456789",
      decision: "EXCLUDED",
      review_revision: 1,
    }] });

    const { container } = render(<CanonicalCaseWorkspace workspace={WORKSPACE} caseId="case-1" connected onRefresh={vi.fn()} />);
    const evidenceTab = await screen.findByRole("tab", { name: "Evidence，已就绪，1 项" });
    fireEvent.click(evidenceTab);

    expect(await screen.findByText("RESTORED")).toBeInTheDocument();
    expect(container.querySelector(".ccw-evidence-card.is-excluded")).toBeNull();
    expect(screen.queryByText("已从后续 Agent 上下文中排除")).not.toBeInTheDocument();
  });

  it("surfaces review exclusion as a path-level invalidation", async () => {
    listCaseEvidenceReviews.mockResolvedValue({ items: [{
      evidence_id: "ev-current-123456789",
      decision: "EXCLUDED",
      review_revision: 2,
    }] });

    render(<CanonicalCaseWorkspace workspace={WORKSPACE} caseId="case-1" connected onRefresh={vi.fn()} />);

    expect(await screen.findByText("证据链发生变化")).toBeInTheDocument();
    expect(screen.getByText("2 项需要回溯")).toBeInTheDocument();
  });

  it("treats dispatch failures as collection attention", async () => {
    const failedWorkspace = {
      ...WORKSPACE,
      collection_proposals: [{
        ...WORKSPACE.collection_proposals[0],
        status: "FAILED",
        validation_result: {},
      }],
      collection_requests: [{
        collection_request_id: "request-failed",
        proposal_id: "proposal-1",
        collector_id: "sys_metrics",
        status: "DISPATCH_FAILED",
      }],
    };

    render(<CanonicalCaseWorkspace workspace={failedWorkspace} caseId="case-1" connected onRefresh={vi.fn()} />);

    const collectionTab = await screen.findByRole("tab", { name: "采集活动，需处理，2 项" });
    fireEvent.click(collectionTab);
    expect(await screen.findByText("DISPATCH_FAILED")).toBeInTheDocument();
  });

  it("links tabs to their panels and supports arrow-key navigation", async () => {
    render(<CanonicalCaseWorkspace workspace={WORKSPACE} caseId="case-1" connected onRefresh={vi.fn()} />);

    const goalsTab = await screen.findByRole("tab", { name: /信息目标/ });
    const collectionsTab = screen.getByRole("tab", { name: /采集活动/ });
    expect(goalsTab).toHaveAttribute("aria-controls", "ccw-panel-goals");
    expect(goalsTab).toHaveAttribute("tabindex", "0");
    expect(collectionsTab).toHaveAttribute("tabindex", "-1");

    fireEvent.keyDown(goalsTab, { key: "ArrowRight" });

    expect(collectionsTab).toHaveFocus();
    expect(collectionsTab).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", "ccw-tab-collections");
  });
});
