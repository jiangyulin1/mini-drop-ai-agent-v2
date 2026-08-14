/**
 * Mini-Drop Tool Catalog for the Pi Runtime (E3).
 *
 * Security boundary: the model can ONLY see these tools.  No bash/read/write/
 * edit/grep/find/ls.  Every tool is a read-only projection over Case/Evidence/
 * Plan data; mutations happen through Mini-Drop's deterministic services after
 * the model proposes a plan revision.
 */

import { Type } from "typebox";

/** Proxy that forwards a tool call back to Mini-Drop FastAPI over internal HTTP. */
function makeInternalTool(name, label, description, parameters, internalPath) {
  return {
    name,
    label,
    description,
    parameters,
    execute: async (_toolCallId, params) => {
      const resp = await fetch(internalPath, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...params, tool: name }),
      });
      if (!resp.ok) {
        return { content: [{ type: "text", text: `tool ${name} failed: ${resp.status}` }], details: {} };
      }
      const payload = await resp.json();
      return {
        content: [{ type: "text", text: JSON.stringify(payload).slice(0, 24000) }],
        details: { projection_bytes: JSON.stringify(payload).length },
      };
    },
  };
}

export function buildToolCatalog({ internalBase = "http://127.0.0.1:8191" } = {}) {
  const tools = [
    makeInternalTool(
      "get_case_snapshot",
      "Get Case Snapshot",
      "Return target, time range, constraints, active hypotheses, current plan revision and budget. Only a projection, never raw artifacts.",
      Type.Object({
        case_id: Type.String({ description: "Case ID" }),
      }),
      `${internalBase}/internal/agent/tools/case-snapshot`,
    ),
    makeInternalTool(
      "find_reusable_evidence",
      "Find Reusable Evidence",
      "Deterministically find reusable Task/Evidence for a missing fact and time window.",
      Type.Object({
        case_id: Type.String({ description: "Case ID" }),
        missing_fact: Type.String({ description: "The fact to verify" }),
        target: Type.String({ description: "Target resource reference" }),
      }),
      `${internalBase}/internal/agent/tools/reusable-evidence`,
    ),
    makeInternalTool(
      "upsert_investigation_plan",
      "Upsert Investigation Plan",
      "Propose a new plan revision. Runs sequential and must carry plan/scope revision. The model cannot create Tasks directly.",
      Type.Object({
        case_id: Type.String({ description: "Case ID" }),
        goal: Type.String({ description: "Investigation goal" }),
        expected_plan_revision: Type.Integer({ description: "Current plan revision" }),
        steps: Type.Array(Type.Object({
          collector_id: Type.String({ description: "Registered collector id" }),
          purpose: Type.String({ description: "Why this step" }),
          risk: Type.String({ description: "READ_LOW/READ_ELEVATED" }),
          priority: Type.Integer({ description: "0-1000" }),
        })),
      }),
      `${internalBase}/internal/agent/tools/plan`,
    ),
    makeInternalTool(
      "evaluate_hypotheses",
      "Evaluate Hypotheses",
      "Run deterministic analyzers + calibration over current evidence. Anti-hallucination check.",
      Type.Object({
        case_id: Type.String({ description: "Case ID" }),
      }),
      `${internalBase}/internal/agent/tools/evaluate-hypotheses`,
    ),
    makeInternalTool(
      "finish_investigation",
      "Finish Investigation",
      "Submit a structured conclusion draft. Must reference real evidence IDs.",
      Type.Object({
        case_id: Type.String({ description: "Case ID" }),
        summary: Type.String({ description: "Conclusion" }),
        evidence_ids: Type.Array(Type.String({ description: "Evidence IDs" })),
      }),
      `${internalBase}/internal/agent/tools/finish`,
    ),
    makeInternalTool(
      "rca_candidate_analysis",
      "RCA Candidate Analysis",
      "Run deterministic rule-based candidate attribution over structured evidence. Read-only; never fabricates evidence refs.",
      Type.Object({
        task_metadata: Type.Object({}, { additionalProperties: true }),
        top_functions: Type.Array(Type.Object({}, { additionalProperties: true })),
      }),
      `${internalBase}/internal/agent/tools/rca-analysis`,
    ),
  ];
  return tools;
}

export const ALLOWED_TOOL_NAMES = [
  "get_case_snapshot",
  "find_reusable_evidence",
  "upsert_investigation_plan",
  "evaluate_hypotheses",
  "finish_investigation",
  "rca_candidate_analysis",
];
