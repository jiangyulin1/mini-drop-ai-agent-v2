import { describe, expect, it } from "vitest";
import {
  evidenceArtifactTarget,
  formatArtifactSize,
  parseTaskArtifactReference,
} from "./evidence";

describe("evidence artifact helpers", () => {
  it("parses a task artifact reference", () => {
    expect(parseTaskArtifactReference("task:task_123:artifact:sys_metrics")).toEqual({
      taskId: "task_123",
      artifactType: "sys_metrics",
    });
  });

  it("parses a task-only reference", () => {
    expect(parseTaskArtifactReference("task:task_123")).toEqual({
      taskId: "task_123",
      artifactType: "",
    });
  });

  it("uses the raw artifact reference as the download target", () => {
    expect(evidenceArtifactTarget({
      raw_artifact_ref: "task:t1:artifact:top_json",
      derived_artifact_ref: "diagnosis/t1/top.json",
    })).toEqual({ taskId: "t1", artifactType: "top_json" });
  });

  it("rejects storage paths that are not task references", () => {
    expect(parseTaskArtifactReference("diagnosis/t1/top.json")).toBeNull();
  });

  it("allows the evidence drawer to render before a record is selected", () => {
    expect(evidenceArtifactTarget(null)).toBeNull();
  });

  it("formats artifact sizes", () => {
    expect(formatArtifactSize(1536)).toBe("1.5 KB");
  });
});
