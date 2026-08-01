import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import EvidenceReference from "./EvidenceReference";
import {
  downloadDiagnosisEvidence,
  downloadDiagnosisEvidenceBundle,
  downloadTaskArtifact,
  getTaskArtifacts,
} from "../api/client";

vi.mock("../api/client", () => ({
  getTaskArtifacts: vi.fn(),
  downloadTaskArtifact: vi.fn(),
  downloadDiagnosisEvidence: vi.fn(),
  downloadDiagnosisEvidenceBundle: vi.fn(),
}));

const evidence = {
  evidence_id: "ev_download_test",
  diagnosis_id: "diag_download_test",
  source_system: "mini_drop_analyzer",
  source_type: "derived_artifact",
  evidence_role: "incident",
  target: { agent_id: "linux-worker-1", pid: 7827 },
  event_time_range: {
    start: "2026-07-29T10:00:00Z",
    end: "2026-07-29T10:00:15Z",
  },
  query_or_probe: "go_pprof",
  data_quality: { completeness: "high", domains: ["process"] },
  observed_value: { hotspot: "runtime.fib", cpu_percent: 87.5 },
  raw_artifact_ref: "task:task_download_test:artifact:top_json",
  derived_artifact_ref: "tasks/task_download_test/top.json",
  derivation_version: "planner-v1",
  integrity_hash: "sha256:test",
  artifact_links: [
    {
      artifact_id: "art_test",
      task_id: "task_download_test",
      artifact_type: "top_json",
    },
  ],
};

describe("EvidenceReference", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockImplementation((query) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
    const nativeGetComputedStyle = window.getComputedStyle;
    vi.spyOn(window, "getComputedStyle").mockImplementation((element) => (
      nativeGetComputedStyle(element)
    ));
    getTaskArtifacts.mockResolvedValue([
      {
        artifact_id: "art_test",
        artifact_type: "top_json",
        filename: "evidence-report.json",
        size_bytes: 128,
        actual_size_bytes: 128,
        availability: "available",
        integrity_status: "not_checked",
        content_type: "application/json",
        metadata: {},
      },
    ]);
    downloadTaskArtifact.mockResolvedValue({
      blob: new Blob(['{"hotspot":"runtime.fib","cpu_percent":87.5}'], {
        type: "application/json",
      }),
      filename: "evidence-report.json",
    });
    downloadDiagnosisEvidence.mockResolvedValue({
      blob: new Blob(['{"evidence_id":"ev_download_test","observed_value":{"hotspot":"runtime.fib"}}'], {
        type: "application/json",
      }),
      filename: "evidence-ev_download_test.json",
    });
    downloadDiagnosisEvidenceBundle.mockResolvedValue({
      blob: new Blob(["PK-test-evidence-bundle"], {
        type: "application/zip",
      }),
      filename: "evidence-ev_download_test-bundle.zip",
    });
    window.URL.createObjectURL = vi.fn(() => "blob:evidence-download");
    window.URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  it("opens actual evidence and downloads the referenced non-empty artifact", async () => {
    render(
      <MemoryRouter>
        <EvidenceReference evidence={evidence} />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("button", { name: /ev_download_test/ }));

    expect(await screen.findByText(/"hotspot": "runtime\.fib"/)).toBeInTheDocument();
    expect(await screen.findByText("evidence-report.json")).toBeInTheDocument();
    expect(getTaskArtifacts).toHaveBeenCalledWith("task_download_test", { verify: true });

    fireEvent.click(screen.getByRole("button", { name: /下载完整证据包/ }));
    await waitFor(() => {
      expect(downloadDiagnosisEvidenceBundle).toHaveBeenCalledWith(
        "diag_download_test",
        "ev_download_test",
      );
    });

    fireEvent.click(screen.getByRole("button", { name: /下载证据 JSON/ }));
    await waitFor(() => {
      expect(downloadDiagnosisEvidence).toHaveBeenCalledWith(
        "diag_download_test",
        "ev_download_test",
      );
    });

    fireEvent.click(screen.getByRole("button", { name: "evidence-report.json" }));

    await waitFor(() => {
      expect(downloadTaskArtifact).toHaveBeenCalledWith(
        "task_download_test",
        "top_json",
        {},
      );
    });
    expect(window.URL.createObjectURL).toHaveBeenCalledTimes(3);
    for (const [blob] of window.URL.createObjectURL.mock.calls) {
      expect(blob).toBeInstanceOf(Blob);
      expect(blob.size).toBeGreaterThan(0);
    }
  });
});
