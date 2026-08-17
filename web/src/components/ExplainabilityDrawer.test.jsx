import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ExplainabilityDrawer from "./ExplainabilityDrawer";

describe("ExplainabilityDrawer", () => {
  it("renders evidence, counter evidence, gaps, risk, and audit identity", () => {
    render(<ExplainabilityDrawer open onClose={() => {}} decision={{ title: "采集运行时快照", risk: "READ_LOW", evidence_refs: ["ev-1"], supporting_factors: ["CPU 连续升高"], opposing_factors: ["错误率未升高"], missing_information: ["线程阻塞快照"], audit_event_id: "audit-42" }} />);
    expect(screen.getByText("采集运行时快照")).toBeInTheDocument();
    expect(screen.getByText("ev-1")).toBeInTheDocument();
    expect(screen.getByText("错误率未升高")).toBeInTheDocument();
    expect(screen.getByText("线程阻塞快照")).toBeInTheDocument();
    expect(screen.getByText("audit-42")).toBeInTheDocument();
    expect(screen.getByText("低风险只读")).toBeInTheDocument();
  });
});
