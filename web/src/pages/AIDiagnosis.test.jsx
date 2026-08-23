import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import AIDiagnosis from "./AIDiagnosis";
import {
  createIncidentCase,
  getCaseWorkspace,
  getCaseHypotheses,
  getCaseInvestigationPlan,
  getCaseCurrentUnderstanding,
  listAgents,
  listIncidentCaseEvents,
  listIncidentCases,
  listTasks,
  listCaseProposals,
  listCaseRecoveryPlans,
  listCaseEvidenceReviews,
  listAcquisitionOperations,
  listRegisteredActions,
  listTargetSessions,
  runIncidentCaseAgentTurn,
} from "../api/client";

vi.mock("../api/client", () => ({
  appendIncidentCaseMessage: vi.fn(),
  approveDiagnosisProbe: vi.fn(),
  correctIncidentCase: vi.fn(),
  createIncidentCase: vi.fn(),
  createCaseRecoveryPlan: vi.fn(),
  createServiceChange: vi.fn(),
  createTargetSession: vi.fn(),
  createCaseEventSource: vi.fn(() => ({
    addEventListener: vi.fn(),
    close: vi.fn(),
    onopen: null,
    onerror: null,
  })),
  decideCaseRecoveryPlan: vi.fn(),
  dryRunCaseRecoveryPlan: vi.fn(),
  executeCaseRecoveryPlan: vi.fn(),
  createTask: vi.fn(),
  downloadTaskArtifact: vi.fn(),
  getDiagnosisSession: vi.fn(),
  getCaseCurrentUnderstanding: vi.fn(),
  getIncidentCase: vi.fn(),
  getCaseWorkspace: vi.fn(),
  getCaseHypotheses: vi.fn(),
  getCaseInvestigationPlan: vi.fn(),
  getTask: vi.fn(),
  getTaskArtifactContent: vi.fn(),
  getTaskArtifacts: vi.fn(),
  listAgents: vi.fn(),
  listIncidentCaseEvents: vi.fn(),
  listIncidentCases: vi.fn(),
  listCaseProposals: vi.fn(),
  listCaseRecoveryPlans: vi.fn(),
  listCaseEvidenceReviews: vi.fn(),
  listAcquisitionOperations: vi.fn(),
  listRegisteredActions: vi.fn(),
  listTargetSessions: vi.fn(),
  listTasks: vi.fn(),
  ensureEventSourceAuthCookie: vi.fn(() => Promise.resolve()),
  runAIValidation: vi.fn(),
  runIncidentCaseAgentTurn: vi.fn(),
  startIncidentCaseDiagnosis: vi.fn(),
  transitionIncidentCase: vi.fn(),
  verifyCaseRecoveryPlan: vi.fn(),
  rollbackCaseRecoveryPlan: vi.fn(),
}));

const CASE = {
  case_id: "case-service-x",
  title: "service-x CPU 飙高",
  problem_description: "service-x CPU 持续超过 90%",
  recovery_goal: "确认原因并给出安全处置建议",
  run_mode: "COLLABORATE",
  environment: "production",
  target_scope: { service_id: "service-x", instances: [], dependencies: [] },
  state: "NEEDS_SCOPE_CONFIRMATION",
  row_version: 0,
  scope_revision: 1,
  created_at: "2026-08-06T00:00:00Z",
  updated_at: "2026-08-06T00:01:00Z",
  summary: {
    need_you: { required: true, question: "请确认目标范围" },
  },
};

describe("AIDiagnosis workspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockImplementation(() => ({
        matches: false,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      })),
    });
    listAgents.mockResolvedValue([{
      id: "linux-worker-1",
      hostname: "worker1",
      ip_addr: "192.168.10.11",
      status: "ONLINE",
      capabilities: ["sys_metrics", "perf_cpu"],
    }]);
    listIncidentCases.mockResolvedValue({ items: [CASE], total: 1 });
    getCaseWorkspace.mockResolvedValue({
      case_projection_version: 1,
      revisions: { case_command: 1, control: 1, scope: 1, plan: 0 },
      case: CASE,
      engine: { state: "IDLE" },
      plan: {}, campaign: {}, collection_proposals: [], collection_requests: [],
      evidence: [], evidence_analyses: [], messages: [],
      last_event_seq: 0,
    });
    getCaseCurrentUnderstanding.mockResolvedValue({
      current_understanding: {
        understanding: "OTHER_UNKNOWN：尚无活跃候选解释",
        confirmed: [],
        missing: [],
      },
    });
    getCaseInvestigationPlan.mockResolvedValue({ plan_id: null, steps: [] });
    getCaseHypotheses.mockResolvedValue({ nodes: [], edges: [] });
    listCaseEvidenceReviews.mockResolvedValue({ items: [] });
    listAcquisitionOperations.mockResolvedValue({ items: [] });
    listCaseProposals.mockResolvedValue({ proposals: [] });
    listCaseRecoveryPlans.mockResolvedValue({ items: [] });
    listRegisteredActions.mockResolvedValue({ items: [] });
    listTargetSessions.mockResolvedValue([]);
    listIncidentCaseEvents.mockResolvedValue({ items: [], total: 0 });
    listTasks.mockResolvedValue([]);
  });

  it("shows one conversation, scope action, worker status and the data console", async () => {
    render(<MemoryRouter><AIDiagnosis /></MemoryRouter>);

    expect((await screen.findAllByText("service-x CPU 飙高")).length).toBeGreaterThan(0);
    expect(await screen.findByTestId("canonical-workspace")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /信息目标/ })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /受引用分析/ })).toBeInTheDocument();
    expect(await screen.findByText("设置诊断范围")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "查看 Worker 状态" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /更多/ })).toBeInTheDocument();
    // Setup actions moved behind the settings menu to keep the toolbar focused.
    expect(screen.getByRole("button", { name: "设置与检测" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /范围与服务关系/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^诊断数据$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /能力与评测状态/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/规则不再生成在线根因候选或排名/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /发送$/ })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "设置范围" }));
    expect(await screen.findByText("Worker 与目标进程")).toBeInTheDocument();
    expect(screen.getByLabelText("worker1 手动 PID")).toBeInTheDocument();
    expect(screen.getByText("服务关系")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Evidence 数据台/ }));
    expect(await screen.findByRole("heading", { name: "Evidence 数据台" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /新建多机采集/ })).toBeInTheDocument();

    await waitFor(() => expect(getCaseWorkspace).toHaveBeenCalledWith(CASE.case_id));
  }, 15_000);

  it("opens change registration from the selected case", async () => {
    render(<MemoryRouter><AIDiagnosis /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: "登记变更" }));
    expect(await screen.findByText("变更只作为待验证相关性，不会直接被当作根因。"))
      .toBeInTheDocument();
    expect(screen.getByLabelText("服务")).toHaveValue("service-x");
  });

  it("opens long-lived target creation", async () => {
    render(<MemoryRouter><AIDiagnosis /></MemoryRouter>);
    fireEvent.mouseEnter(await screen.findByRole("button", { name: "设置与检测" }));
    fireEvent.click(await screen.findByRole("menuitem", { name: "长期目标" }));
    expect(await screen.findByText(/长期目标会积累信号/)).toBeInTheDocument();
    expect(screen.getByLabelText("服务标识")).toBeInTheDocument();
  });

  it("creates a diagnosis with multi-process scope, service edges, evidence and autonomy policy", async () => {
    listAgents.mockResolvedValue([
      {
        id: "linux-worker-1",
        hostname: "worker1",
        ip_addr: "192.168.10.11",
        status: "ONLINE",
        capabilities: ["sys_metrics", "perf_cpu"],
      },
      {
        id: "linux-worker-2",
        hostname: "worker2",
        ip_addr: "192.168.10.12",
        status: "ONLINE",
        capabilities: ["sys_metrics", "perf_cpu"],
      },
    ]);
    listTasks.mockResolvedValue([{
      id: "task-existing-1",
      name: "service-x CPU 基线",
      status: "DONE",
      agent_id: "linux-worker-1",
      target_pid: 1201,
      visibility: "USER_VISIBLE",
    }]);
    createIncidentCase.mockResolvedValue({
      ...CASE,
      case_id: "case-created",
      title: "service-x · CPU 持续升高",
      target_scope: {
        service_id: "service-x",
        instances: [
          { service_id: "service-x", agent_id: "linux-worker-1", pid: 1201 },
          { service_id: "service-x", agent_id: "linux-worker-2", pid: 2202 },
        ],
        dependencies: [],
      },
    });

    render(<MemoryRouter><AIDiagnosis /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: /新建诊断/ }));
    fireEvent.change(screen.getByLabelText("发生了什么"), { target: { value: "service-x CPU 持续升高" } });
    fireEvent.change(screen.getByLabelText("目标服务"), { target: { value: "service-x" } });

    fireEvent.click(screen.getByText("按需配置目标机器、进程与服务关系"));
    fireEvent.click(await screen.findByRole("button", { name: /添加机器 \/ 进程/ }));
    fireEvent.click(screen.getByRole("button", { name: /添加机器 \/ 进程/ }));
    const instanceServices = screen.getAllByLabelText("服务");
    fireEvent.change(instanceServices[0], { target: { value: "service-x" } });
    fireEvent.change(instanceServices[1], { target: { value: "service-x" } });
    const workerFields = screen.getAllByLabelText("Worker");
    fireEvent.mouseDown(workerFields[0]);
    fireEvent.click(await screen.findByText("worker1 · 在线"));
    fireEvent.mouseDown(workerFields[1]);
    const worker2Options = await screen.findAllByText("worker2 · 在线");
    fireEvent.click(worker2Options.at(-1));
    const pidFields = screen.getAllByLabelText("PID");
    fireEvent.change(pidFields[0], { target: { value: "1201" } });
    fireEvent.change(pidFields[1], { target: { value: "2202" } });

    fireEvent.click(screen.getByRole("button", { name: /添加服务关系/ }));
    fireEvent.change(screen.getByLabelText("上游服务"), { target: { value: "service-x" } });
    fireEvent.change(screen.getByLabelText("下游服务"), { target: { value: "redis-y" } });

    fireEvent.click(screen.getByText("按需配置事件时间与已有证据"));
    fireEvent.change(screen.getByLabelText("事件开始时间"), { target: { value: "2026-08-21T18:00" } });
    fireEvent.change(screen.getByLabelText("事件结束时间"), { target: { value: "2026-08-21T18:30" } });
    fireEvent.mouseDown(screen.getByLabelText("关联已有采集任务"));
    fireEvent.click(await screen.findByText(/service-x CPU 基线/));

    fireEvent.click(screen.getByLabelText("持续接管"));
    fireEvent.click(await screen.findByText("配置持续接管的预算、授权与恢复验证"));
    fireEvent.change(screen.getByLabelText("最多调查轮次"), { target: { value: "12" } });
    fireEvent.change(screen.getByLabelText("最多自动处置次数"), { target: { value: "4" } });
    fireEvent.change(screen.getByLabelText("Swarm 服务名"), { target: { value: "service-x-stack_service-x" } });
    fireEvent.change(screen.getByLabelText("恢复检查 URL"), { target: { value: "https://service-x.example.com/health" } });
    fireEvent.click(screen.getByLabelText("自动批准 CPU / I/O 深度采集"));
    fireEvent.click(screen.getByLabelText("授权重启已登记的无状态 Swarm 服务"));
    fireEvent.click(document.querySelector(".ant-modal-footer .ant-btn-primary"));

    await waitFor(() => expect(createIncidentCase).toHaveBeenCalledTimes(1));
    const payload = createIncidentCase.mock.calls[0][0];
    expect(payload).toMatchObject({
      problem_description: "service-x CPU 持续升高",
      run_mode: "AUTHORIZED_AUTONOMY",
      environment: "production",
      initial_tasks: ["task-existing-1"],
      time_range: {
        start: new Date("2026-08-21T18:00").toISOString(),
        end: new Date("2026-08-21T18:30").toISOString(),
        source: "user_expression",
      },
      target_scope: {
        service_id: "service-x",
        service_ids: ["service-x"],
        instances: [
          expect.objectContaining({ service_id: "service-x", host_id: "worker1", agent_id: "linux-worker-1", pid: 1201 }),
          expect.objectContaining({ service_id: "service-x", host_id: "worker2", agent_id: "linux-worker-2", pid: 2202 }),
        ],
        dependencies: [{
          source_service: "service-x",
          target_service: "redis-y",
          relation: "CALLS",
          confidence: "medium",
          source: "user_confirmed",
        }],
        verification: {
          http_checks: [expect.objectContaining({ url: "https://service-x.example.com/health" })],
        },
        orchestration: expect.objectContaining({ swarm_service: "service-x-stack_service-x" }),
        autonomy_policy: expect.objectContaining({
          max_iterations: 12,
          max_actions: 4,
          max_auto_impact: "I2",
          allowed_action_ids: ["swarm.restart-stateless-service"],
          auto_approve_probe_ids: ["process_cpu_profile", "process_io_latency"],
        }),
      },
    });
  }, 15_000);

  it("inherits scope from a long-lived target instead of offering conflicting scope fields", async () => {
    listTargetSessions.mockResolvedValue([{
      target_session_id: "target-checkout",
      display_name: "checkout 长期目标",
      status: "ACTIVE",
      environment: "staging",
      target_scope: {
        service_id: "checkout",
        instances: [{ service_id: "checkout", agent_id: "linux-worker-1", pid: 8080 }],
        dependencies: [],
      },
    }]);

    render(<MemoryRouter><AIDiagnosis /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: /新建诊断/ }));
    fireEvent.mouseDown(screen.getByLabelText("关联长期目标"));
    fireEvent.click(await screen.findByText("checkout 长期目标 · 监控中"));

    expect(await screen.findByText(/将继承「checkout 长期目标」/)).toBeInTheDocument();
    expect(screen.queryByText("按需配置目标机器、进程与服务关系")).not.toBeInTheDocument();
    expect(screen.getByLabelText("目标服务")).toBeDisabled();
    expect(screen.getByLabelText("环境")).toBeDisabled();
    expect(screen.getByText("按需配置事件时间与已有证据")).toBeInTheDocument();
  });

  it("keeps ordinary conversation read-only by default", async () => {
    runIncidentCaseAgentTurn.mockResolvedValue({ status: "runtime_turn_accepted", turn_id: "turn-1" });
    render(<MemoryRouter><AIDiagnosis /></MemoryRouter>);

    fireEvent.change(await screen.findByPlaceholderText("补充事实、纠正结论，或要求重新分析"), {
      target: { value: "解释当前 Evidence 能证明什么" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发送$/ }));

    await waitFor(() => expect(runIncidentCaseAgentTurn).toHaveBeenCalledWith(CASE.case_id, {
      message: "解释当前 Evidence 能证明什么",
      execute_safe_tools: true,
      requested_disposition: "ANSWER_ONLY",
    }));
  });

  it("only starts writable investigation after the user selects that mode", async () => {
    runIncidentCaseAgentTurn.mockResolvedValue({ status: "runtime_turn_accepted", turn_id: "turn-1" });
    render(<MemoryRouter><AIDiagnosis /></MemoryRouter>);

    fireEvent.click(await screen.findByText("继续调查"));
    fireEvent.change(await screen.findByPlaceholderText("补充事实、纠正结论，或要求重新分析"), {
      target: { value: "继续调查并持久化结论" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发送并调查/ }));

    await waitFor(() => expect(runIncidentCaseAgentTurn).toHaveBeenCalledWith(CASE.case_id, {
      message: "继续调查并持久化结论",
      execute_safe_tools: true,
      requested_disposition: "INVESTIGATE",
    }));
  });
});
