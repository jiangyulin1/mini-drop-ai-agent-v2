import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ErrorAlert from "./ErrorAlert";

describe("ErrorAlert", () => {
  it("does not render an empty error", () => {
    const { container } = render(<ErrorAlert error="" />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the operational error message", () => {
    render(<ErrorAlert error="Agent 暂时离线" />);
    expect(screen.getByText("Agent 暂时离线")).toBeInTheDocument();
  });
});
