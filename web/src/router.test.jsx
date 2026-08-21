import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import Router, { LegacyCaseRedirect } from "./router";

vi.mock("./components/AppLayout", async () => {
  const { Outlet } = await import("react-router-dom");
  return { default: () => <Outlet /> };
});
vi.mock("./pages/OperationsOverview", () => ({ default: () => <div>概览页面</div> }));

function CurrentLocation() {
  const location = useLocation();
  return <div>{`${location.pathname}${location.search}${location.hash}`}</div>;
}

describe("legacy Case routes", () => {
  it("preserves task context while redirecting to the canonical workspace", async () => {
    render(
      <MemoryRouter initialEntries={["/ai-diagnosis?fromTask=task-42#analysis"]}>
        <Routes>
          <Route path="/ai-diagnosis" element={<LegacyCaseRedirect />} />
          <Route path="/cases" element={<CurrentLocation />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("/cases?fromTask=task-42#analysis")).toBeInTheDocument();
  });
});

describe("hidden legacy routes", () => {
  afterEach(() => {
    cleanup();
    window.history.pushState({}, "", "/");
  });

  it("redirects old diagnosis history into the canonical workspace", async () => {
    window.history.pushState({}, "", "/diagnoses");
    render(<Router />);

    expect(window.location.pathname).toBe("/cases");
  });

  it("removes the obsolete system explanation page", async () => {
    window.history.pushState({}, "", "/about");
    render(<Router />);

    expect(window.location.pathname).toBe("/");
  });
});
