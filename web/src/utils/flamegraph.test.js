import { describe, expect, it } from "vitest";

import {
  extractFlamegraphTreeFromSvg,
  extractTopFunctionsFromSvg,
} from "./flamegraph";

describe("extractTopFunctionsFromSvg", () => {
  it("derives and aggregates TopN entries from py-spy SVG titles", () => {
    const result = extractTopFunctionsFromSvg(`
      <svg xmlns="http://www.w3.org/2000/svg">
        <g><title>all (200 samples, 100.00%)</title></g>
        <g><title>0x7f123abc (libc.so.6) (190 samples, 95.00%)</title></g>
        <g><title>run (threading.py:1010) (180 samples, 90.00%)</title></g>
        <g><title>cpu_hotspot (work.py:10) (115 samples, 9.30%)</title></g>
        <g><title>cpu_hotspot (work.py:10) (43 samples, 3.48%)</title></g>
        <g><title>&lt;module&gt; (work.py:20) (4 samples, 0.32%)</title></g>
      </svg>
    `);

    expect(result[0]).toEqual({
      name: "cpu_hotspot (work.py:10)",
      samples: 158,
      percent: 12.78,
      source: "flamegraph_svg",
    });
    expect(result[1].name).toBe("<module> (work.py:20)");
  });

  it("reconstructs the SVG rectangle hierarchy for the d3 viewer", () => {
    const tree = extractFlamegraphTreeFromSvg(`
      <svg xmlns="http://www.w3.org/2000/svg">
        <g><title>all (100 samples, 100.00%)</title><rect x="0%" y="10" width="100%" height="15"/></g>
        <g><title>worker (80 samples, 80.00%)</title><rect x="0%" y="26" width="80%" height="15"/></g>
        <g><title>hotspot (60 samples, 60.00%)</title><rect x="10%" y="42" width="60%" height="15"/></g>
        <g><title>idle (20 samples, 20.00%)</title><rect x="80%" y="26" width="20%" height="15"/></g>
      </svg>
    `);

    expect(tree.name).toBe("all");
    expect(tree.children.map((item) => item.name)).toEqual(["worker", "idle"]);
    expect(tree.children[0].children[0].name).toBe("hotspot");
  });
});
