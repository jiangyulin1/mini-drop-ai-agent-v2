import { describe, expect, it } from "vitest";

import { AGENT_PHASE_META, agentErrorText, eventText, nextConversationScroll } from "./workspaceUtils";

describe("autonomous agent presentation", () => {
  it("uses short user-facing labels for durable phases", () => {
    expect(AGENT_PHASE_META.ACTION_DISPATCHING.label).toBe("正在执行修复");
    expect(AGENT_PHASE_META.ROLLBACK_DISPATCHING.label).toBe("正在回滚");
    expect(AGENT_PHASE_META.RESOLVED.label).toBe("已恢复");
  });

  it("translates common stop reasons without hiding executor details", () => {
    expect(agentErrorText("HIGH_QUALITY_EVIDENCE_CONFLICT")).toContain("证据相互冲突");
    expect(agentErrorText("ACTION_FAILED:service changed")).toBe("修复执行失败：service changed");
  });
});

describe("conversation scroll", () => {
  it("follows new content only when viewing the bottom", () => {
    expect(nextConversationScroll({
      caseChanged: false,
      nearBottom: true,
      previousTop: 300,
      scrollHeight: 1000,
      clientHeight: 400,
    })).toBe(600);
  });

  it("preserves the reading position when the user has scrolled up", () => {
    expect(nextConversationScroll({
      caseChanged: false,
      nearBottom: false,
      previousTop: 240,
      scrollHeight: 1200,
      clientHeight: 400,
    })).toBe(240);
  });
});


describe("runtime event text", () => {
  it("renders runtime turn accepted/rejected events", () => {
    expect(eventText({ event_type: "agent_runtime_turn_submitted", payload: { assistant_message: "已提交" } })).toBe("已提交");
    expect(eventText({ event_type: "agent_runtime_turn_rejected", payload: { assistant_message: "不可用" } })).toBe("不可用");
  });

  it("renders conclusion and query events", () => {
    expect(eventText({ event_type: "agent_finish_investigation", payload: { summary: "CPU 饱和" } })).toContain("CPU 饱和");
    expect(eventText({ event_type: "case_query_task_created", payload: { operation: "process.list" } })).toContain("process.list");
  });
});
