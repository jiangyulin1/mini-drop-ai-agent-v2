import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import CaseConversation from "./CaseConversation";

const DETAIL = {
  case_id: "case-markdown",
  title: "检查 AI 回复排版",
  problem_description: "确认模型输出可读",
  environment: "production",
  run_mode: "COLLABORATE",
  state: "RESOLVED",
  diagnosis_session_id: "diag-1",
  target_scope: { service_id: "service-a", instances: [] },
  summary: {},
  created_at: "2026-08-21T00:00:00Z",
  updated_at: "2026-08-21T00:01:00Z",
};

describe("CaseConversation assistant messages", () => {
  it("renders persisted and completed model replies as Markdown without breaking references", () => {
    const onOpenEvidence = vi.fn();
    render(
      <MemoryRouter>
        <CaseConversation
          detail={DETAIL}
          events={[{
            event_id: "event-completed",
            event_type: "agent_turn_completed",
            created_at: "2026-08-21T00:01:00Z",
            payload: {
              assistant_message: "**本轮结论**：CPU 偏高。",
              evidence_refs: ["ev-cpu-1"],
            },
          }]}
          assistantMessages={[{
            message_id: "message-history",
            created_at: "2026-08-21T00:00:30Z",
            content: "## 历史分析\n\n- 已检查基线\n- 需要调用栈",
          }]}
          diagnosis={null}
          currentUnderstanding={null}
          proposals={[]}
          recoveryPlans={[]}
          loading={false}
          actionLoading={false}
          messageText=""
          onMessageChange={vi.fn()}
          onSend={vi.fn()}
          onStart={vi.fn()}
          onOpenScope={vi.fn()}
          onOpenTechnical={vi.fn()}
          onOpenCollection={vi.fn()}
          onDecision={vi.fn()}
          onTransition={vi.fn()}
          onAdvanceAgent={vi.fn()}
          onOpenRecovery={vi.fn()}
          onRecoveryAction={vi.fn()}
          onOpenEvidence={onOpenEvidence}
          onOpenKnowledge={vi.fn()}
        />
      </MemoryRouter>,
    );

    expect(screen.getByRole("heading", { name: "历史分析", level: 3 })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("本轮结论").tagName).toBe("STRONG");
    fireEvent.click(screen.getByRole("button", { name: /ev-cpu-1/ }));
    expect(onOpenEvidence).toHaveBeenCalledWith("ev-cpu-1");
  });
});
