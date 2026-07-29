import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ErrorBoundary from "./ErrorBoundary";

function BrokenView() {
  throw new Error("render failed");
}

describe("ErrorBoundary", () => {
  beforeEach(() => {
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("turns an unexpected render failure into a usable fallback", () => {
    render(
      <ErrorBoundary>
        <BrokenView />
      </ErrorBoundary>,
    );

    expect(screen.getByText("页面渲染异常")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重试/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /返回首页/ })).toBeEnabled();
  });

  it("renders healthy children without a fallback", () => {
    render(
      <ErrorBoundary>
        <div>任务面板正常</div>
      </ErrorBoundary>,
    );

    expect(screen.getByText("任务面板正常")).toBeInTheDocument();
    expect(screen.queryByText("页面渲染异常")).not.toBeInTheDocument();
  });
});
