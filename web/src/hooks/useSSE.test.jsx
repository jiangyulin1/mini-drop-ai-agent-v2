import { render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import useSSE from "./useSSE";
import {
  createEventSource,
  ensureEventSourceAuthCookie,
} from "../api/client";

vi.mock("../api/client", () => ({
  createEventSource: vi.fn(),
  ensureEventSourceAuthCookie: vi.fn(),
}));

function Harness() {
  useSSE();
  return null;
}

describe("useSSE", () => {
  it("establishes the auth cookie before opening EventSource", async () => {
    const order = [];
    const source = {
      addEventListener: vi.fn(),
      close: vi.fn(),
      onopen: null,
      onerror: null,
      onmessage: null,
    };
    ensureEventSourceAuthCookie.mockImplementation(async () => {
      order.push("cookie");
    });
    createEventSource.mockImplementation(() => {
      order.push("event-source");
      return source;
    });

    const view = render(<Harness />);

    await waitFor(() => expect(createEventSource).toHaveBeenCalledTimes(1));
    expect(ensureEventSourceAuthCookie).toHaveBeenCalledTimes(1);
    expect(order).toEqual(["cookie", "event-source"]);

    view.unmount();
    expect(source.close).toHaveBeenCalledTimes(1);
  });
});
