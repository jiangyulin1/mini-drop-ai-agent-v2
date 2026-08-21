import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import InvestigationWorkbench from "./InvestigationWorkbench";
import * as api from "../api/client";

const planResponse = {
  plan_id: "plan-1",
  plan_revision: 2,
  goal: "验证支付接口超时根因",
  steps: [
    { step_id: "step-run", collector_id: "sys_metrics", purpose: "采集基础指标",
      priority: 90, risk: "READ_LOW", status: "RUNNING" },
    { step_id: "step-queued", collector_id: "log_scan", purpose: "扫描错误日志",
      priority: 80, risk: "READ_LOW", status: "QUEUED",
      selection_strategy: "REPRESENTATIVE" },
    { step_id: "step-done", collector_id: "perf_cpu", purpose: "CPU 热点",
      priority: 70, risk: "READ_LOW", status: "COMPLETED" },
  ],
};

describe("InvestigationWorkbench", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    // client.js response interceptor unwraps body.data; component mocks must
    // use the real unwrapped wire shape to prevent double-.data false green.
    vi.spyOn(api, "getCaseInvestigationPlan").mockResolvedValue(planResponse);
    vi.spyOn(api, "listCaseEvidenceReviews").mockResolvedValue({
      items: [
        { review_id: "r1", evidence_id: "ev-1", decision: "TRUSTED", reason: "已复核" },
      ],
    });
    vi.spyOn(api, "listCaseFanoutRuns").mockResolvedValue({
      items: [
        { run_id: "fanout-1", strategy: "ALL_IN_SCOPE", coverage: 0.67,
          status: "COMPLETED", aggregate: { conclusion: "fault-domain" } },
      ],
    });
    vi.spyOn(api, "listAcquisitionOperations").mockResolvedValue({ items: [] });
    vi.spyOn(api, "cancelCasePlanStep").mockResolvedValue({});
    vi.spyOn(api, "removeCasePlanStep").mockResolvedValue({});
    vi.spyOn(api, "reprioritizeCasePlanStep").mockResolvedValue({});
    vi.spyOn(api, "createCaseFanout").mockResolvedValue({});
    vi.spyOn(api, "reviewCaseEvidence").mockResolvedValue({});
  });

  it("renders grouped plan steps with status and actions", async () => {
    render(<InvestigationWorkbench caseId="case-1" />);

    await waitFor(() => expect(screen.getByLabelText("调查工作台")).toBeInTheDocument());
    expect(screen.getByText("当前工作")).toBeInTheDocument();
    expect(screen.getByText("下一步")).toBeInTheDocument();
    expect(screen.getByText("历史任务")).toBeInTheDocument();
    expect(screen.getByText("sys_metrics")).toBeInTheDocument();
    expect(screen.getByText("log_scan")).toBeInTheDocument();
    expect(screen.getByText("perf_cpu")).toBeInTheDocument();
    // 状态标签
    expect(screen.getByText("执行中")).toBeInTheDocument();
    expect(screen.getByText("待执行")).toBeInTheDocument();
    expect(screen.getByText("证据已提交")).toBeInTheDocument();
    // 集群策略可见
    expect(screen.getByText("集群策略：REPRESENTATIVE")).toBeInTheDocument();
  });

  it("cancels and removes queued steps", async () => {
    render(<InvestigationWorkbench caseId="case-1" />);
    await waitFor(() => expect(screen.getByText("log_scan")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(api.cancelCasePlanStep).toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "移除" }));
    expect(api.removeCasePlanStep).toHaveBeenCalled();
  });

  it("fans out a cluster step via the workbench", async () => {
    render(<InvestigationWorkbench caseId="case-1" />);
    await waitFor(() => expect(screen.getByText("log_scan")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "集群扇出" }));
    expect(api.createCaseFanout).toHaveBeenCalledWith("case-1", {
      step_id: "step-queued",
      strategy: "REPRESENTATIVE",
    });
  });

  it("shows evidence reviews and fanout coverage", async () => {
    render(<InvestigationWorkbench caseId="case-1" />);
    await waitFor(() => expect(screen.getByText("证据审查（1/1）")).toBeInTheDocument());
    expect(screen.getByText("ev-1")).toBeInTheDocument();
    // Trust decisions render through the shared opsMappings vocabulary.
    expect(screen.getByText("可信")).toBeInTheDocument();
    expect(screen.getByText("ALL_IN_SCOPE")).toBeInTheDocument();
    expect(screen.getByText("覆盖率 67%")).toBeInTheDocument();
    expect(screen.getByText("fault-domain")).toBeInTheDocument();
  });

  it("counts the current review state once per Evidence", async () => {
    api.listCaseEvidenceReviews.mockResolvedValue({
      items: [
        { review_id: "r-new", evidence_id: "ev-1", decision: "TRUSTED" },
        { review_id: "r-old", evidence_id: "ev-1", decision: "LOW_TRUST" },
      ],
    });
    render(<InvestigationWorkbench caseId="case-1" />);

    await waitFor(() => expect(screen.getByText("证据审查（1/1）")).toBeInTheDocument());
    expect(screen.getAllByText("ev-1")).toHaveLength(1);
    expect(screen.getByText("可信")).toBeInTheDocument();
  });

  it("reconstructs the Pi activity timeline and exposes unreviewed Evidence", async () => {
    api.getCaseInvestigationPlan.mockResolvedValue({ plan_id: null, steps: [] });
    api.listCaseEvidenceReviews.mockResolvedValue({ items: [] });
    api.listCaseFanoutRuns.mockResolvedValue({ items: [] });
    const workspace = {
      case: { problem_description: "验证 CPU 偏高" },
      collection_proposals: [
        { proposal_id: "proposal-1", collector_id: "sys_metrics",
          information_goal: "建立 CPU 基线", status: "ACCEPTED", expected_risk: "READ_LOW" },
        { proposal_id: "proposal-2", collector_id: "perf_cpu",
          information_goal: "定位热点", status: "REJECTED", expected_risk: "READ_MEDIUM" },
      ],
      collection_requests: [
        { collection_request_id: "request-1", proposal_id: "proposal-1", status: "COMPLETED" },
      ],
      evidence: [
        { evidence_id: "ev-live-1", evidence_type: "sys_metrics" },
      ],
    };

    render(<InvestigationWorkbench caseId="case-1" workspace={workspace} />);

    await waitFor(() => expect(screen.getByText("即时调查轨迹")).toBeInTheDocument());
    expect(screen.getByText(/2 项活动/)).toBeInTheDocument();
    expect(screen.getByText("建立 CPU 基线")).toBeInTheDocument();
    expect(screen.getByText("定位热点")).toBeInTheDocument();
    expect(screen.getByText("证据审查（0/1）")).toBeInTheDocument();
    expect(screen.getByText("未人工审查")).toBeInTheDocument();
    expect(screen.getByText("本 Case 未触发跨节点采集")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "信任" }));
    await waitFor(() => expect(api.reviewCaseEvidence).toHaveBeenCalledWith(
      "case-1", "ev-live-1", expect.objectContaining({ decision: "TRUSTED" }),
    ));
  });

  it("shows offline banner when the plan request fails", async () => {
    api.getCaseInvestigationPlan.mockRejectedValue(new Error("network down"));
    render(<InvestigationWorkbench caseId="case-1" />);
    await waitFor(() => expect(screen.getByRole("status")).toBeInTheDocument());
    expect(screen.getByText(/连接中断/)).toBeInTheDocument();
  });
});
