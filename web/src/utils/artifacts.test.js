import { describe, expect, it } from "vitest";
import {
  artifactText,
  isArtifactAvailable,
  prepareAsyncProfilerHtml,
  unavailableVisualArtifacts,
} from "./artifacts";

describe("artifact presentation helpers", () => {
  it("accepts legacy and explicitly available artifacts", () => {
    expect(isArtifactAvailable({ artifact_type: "sys_metrics" })).toBe(true);
    expect(isArtifactAvailable({ availability: "available" })).toBe(true);
    expect(isArtifactAvailable({ availability: "missing" })).toBe(false);
  });

  it("normalizes text artifact API envelopes", () => {
    expect(artifactText("<svg />")).toBe("<svg />");
    expect(artifactText({ text: "<html />" })).toBe("<html />");
    expect(artifactText({ data: "wrong shape" })).toBe("");
  });

  it("stabilizes async-profiler canvas layout without changing scripts", () => {
    const source = '<html><head></head><body><canvas id="canvas"></canvas><script>run()</script></body></html>';
    const prepared = prepareAsyncProfilerHtml({ text: source });
    expect(prepared).toContain("#canvas{width:100vw!important");
    expect(prepared).toContain("<script>run()</script>");
    expect(prepareAsyncProfilerHtml(prepared)).toBe(prepared);
  });

  it("reports only unavailable artifacts that drive charts", () => {
    expect(unavailableVisualArtifacts([
      { artifact_type: "flamegraph_svg", availability: "missing" },
      { artifact_type: "raw", availability: "missing" },
      { artifact_type: "sys_metrics", availability: "available" },
    ])).toEqual([
      { artifact_type: "flamegraph_svg", availability: "missing" },
    ]);
  });
});
