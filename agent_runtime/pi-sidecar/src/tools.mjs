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
function makeInternalTool(name, label, description, parameters, internalPath, getEnvelope) {
  return {
    name,
    label,
    description,
    parameters,
    execute: async (_toolCallId, params) => {
      const headers = { "Content-Type": "application/json" };
      const internalToken = process.env.MINI_DROP_PI_INTERNAL_TOKEN || "";
      if (internalToken) {
        headers["X-Internal-Token"] = internalToken;
      }
      const resp = await fetch(internalPath, {
        method: "POST",
        headers,
        body: JSON.stringify({ ...params, tool: name, ...(getEnvelope?.() || {}) }),
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

export function buildToolCatalog({
  internalBase = "http://127.0.0.1:8191",
  sideEffectPolicy = "AUTO_READ_LOW",
  catalog = null,
  runtimePolicy = null,
  getEnvelope = () => ({}),
} = {}) {
  const base = `${internalBase}/internal/agent/tools`;
  const fallbackTools = [
    makeInternalTool(
      "get_case_snapshot",
      "Get Case Snapshot",
      "Return Case goal, revisions, plan, canonical evidence inventory and projection hashes.",
      Type.Object({ case_id: Type.String({ description: "Case ID" }) }),
      `${base}/case-snapshot`, getEnvelope,
    ),
    makeInternalTool(
      "list_case_evidence",
      "List Case Evidence",
      "List canonical Case Evidence with projection hashes, target, window and quality.",
      Type.Object({
        case_id: Type.String({ description: "Case ID" }),
        filters: Type.Optional(Type.Object({}, { additionalProperties: true })),
        cursor: Type.Optional(Type.String({ description: "Page cursor" })),
      }),
      `${base}/list-case-evidence`, getEnvelope,
    ),
    makeInternalTool(
      "get_evidence_projection",
      "Get Evidence Projection",
      "Expand bounded EvidenceProjection content (signals/samples/log events/top items).",
      Type.Object({
        case_id: Type.String({ description: "Case ID" }),
        evidence_ids: Type.Array(Type.String({ description: "Evidence IDs" })),
        projection_kinds: Type.Optional(Type.Array(Type.String({ description: "Projection kinds" }))),
        max_bytes: Type.Optional(Type.Integer({ description: "Maximum bytes", default: 131072 })),
      }),
      `${base}/get-evidence-projection`, getEnvelope,
    ),
    makeInternalTool(
      "compare_evidence",
      "Compare Evidence",
      "Compare selected Evidence along signals/target/time/quality dimensions.",
      Type.Object({
        case_id: Type.String({ description: "Case ID" }),
        evidence_ids: Type.Array(Type.String({ description: "Evidence IDs" })),
        dimensions: Type.Optional(Type.Array(Type.String({ description: "Compare dimensions" }))),
      }),
      `${base}/compare-evidence`, getEnvelope,
    ),
    makeInternalTool(
      "search_knowledge",
      "Search Knowledge",
      "Search indexed Knowledge excerpts; Knowledge never counts as Current Evidence.",
      Type.Object({
        query: Type.String({ description: "Knowledge query" }),
        limit: Type.Optional(Type.Integer({ description: "Maximum results" })),
      }),
      `${base}/search-knowledge`, getEnvelope,
    ),
    makeInternalTool(
      "get_causal_graph",
      "Get Causal Graph",
      "Read current CausalGraphRevision with nodes and edges.",
      Type.Object({ case_id: Type.String({ description: "Case ID" }) }),
      `${base}/get-causal-graph`, getEnvelope,
    ),
    makeInternalTool(
      "get_evidence_gaps",
      "Get Evidence Gaps",
      "Read precise open EvidenceGap records.",
      Type.Object({ case_id: Type.String({ description: "Case ID" }) }),
      `${base}/get-evidence-gaps`, getEnvelope,
    ),
    makeInternalTool(
      "find_reusable_evidence",
      "Find Reusable Evidence",
      "Find canonical Evidence whose fingerprint, target and window cover a missing fact.",
      Type.Object({
        case_id: Type.String({ description: "Case ID" }),
        missing_fact: Type.String({ description: "The fact to verify" }),
        target: Type.String({ description: "Target resource reference" }),
      }),
      `${base}/reusable-evidence`, getEnvelope,
    ),
    makeInternalTool(
      "list_operations",
      "List Acquisition Operations",
      "List registered OperationSpec records that are enabled and auto-allowed.",
      Type.Object({ case_id: Type.Optional(Type.String({ description: "Case ID" })) }),
      `${base}/list-operations`, getEnvelope,
    ),
    makeInternalTool(
      "propose_plan_revision",
      "Propose Plan Revision",
      "Propose a new immutable Plan revision. Mini-Drop validates CAS and dependencies.",
      Type.Object({
        case_id: Type.String({ description: "Case ID" }),
        goal: Type.String({ description: "Investigation goal" }),
        expected_case_row_version: Type.Integer({ description: "Current case row version" }),
        expected_scope_revision: Type.Integer({ description: "Current scope revision" }),
        expected_plan_revision: Type.Integer({ description: "Current plan revision" }),
        steps: Type.Array(Type.Object({
          kind: Type.String({ description: "ACQUIRE_EVIDENCE/ANALYZE/ASK_USER/WAIT_EVENT/FINISH" }),
          collector_id: Type.String({ description: "Registered collector id" }),
          purpose: Type.String({ description: "Why this step" }),
          risk: Type.String({ description: "READ_LOW/READ_ELEVATED" }),
          priority: Type.Integer({ description: "0-1000" }),
          target_refs: Type.Optional(Type.Array(Type.String({ description: "Target resource refs" }))),
          hypothesis_refs: Type.Optional(Type.Array(Type.String({ description: "Hypothesis refs" }))),
          selection_strategy: Type.Optional(Type.String({ description: "Cluster selection strategy" })),
          depends_on: Type.Optional(Type.Array(Type.String({ description: "Step IDs this step depends on" }))),
        })),
      }),
      `${base}/plan`, getEnvelope,
    ),
    makeInternalTool(
      "request_operation",
      "Request Acquisition Operation",
      "Request a registered low-risk Collector/Query/Source operation. Only a Proposal; Supervisor compiles ExecutionUnit.",
      Type.Object({
        case_id: Type.String({ description: "Case ID" }),
        operation: Type.String({ description: "Registered operation id" }),
        target_selector: Type.Optional(Type.Object({}, { additionalProperties: true })),
        parameters: Type.Optional(Type.Object({}, { additionalProperties: true })),
        idempotency_key: Type.Optional(Type.String({ description: "Optional idempotency key" })),
        runtime_generation: Type.Optional(Type.Integer({ description: "Runtime generation from Snapshot" })),
        expected_control_revision: Type.Optional(Type.Integer({ description: "Control revision from Snapshot" })),
        expected_scope_revision: Type.Optional(Type.Integer({ description: "Scope revision from Snapshot" })),
      }),
      `${base}/query`, getEnvelope,
    ),
    makeInternalTool(
      "evaluate_hypotheses",
      "Evaluate Hypotheses",
      "Run deterministic analyzers + calibration over current evidence. Anti-hallucination check.",
      Type.Object({ case_id: Type.String({ description: "Case ID" }) }),
      `${base}/evaluate-hypotheses`, getEnvelope,
    ),
    makeInternalTool(
      "finish_investigation",
      "Finish Investigation",
      "Submit a structured Conclusion. Verifier owns final state; claims must bind Evidence + projection hash + field path.",
      Type.Object({
        case_id: Type.String({ description: "Case ID" }),
        summary: Type.String({ description: "Conclusion" }),
        evidence_ids: Type.Array(Type.String({ description: "Evidence IDs" })),
        state: Type.Optional(Type.String({ description: "CONFIRMED/PARTIALLY_CONFIRMED/INSUFFICIENT_EVIDENCE" })),
        claims: Type.Optional(Type.Array(Type.Object({
          evidence_id: Type.String({ description: "Evidence ID" }),
          projection_hash: Type.String({ description: "Projection hash" }),
          field_path: Type.Optional(Type.String({ description: "Field path inside projection content" })),
          predicate: Type.Optional(Type.Object({}, { additionalProperties: true })),
        }))),
        limitations: Type.Optional(Type.Array(Type.String({ description: "Limitations" }))),
      }),
      `${base}/finish`, getEnvelope,
    ),
    makeInternalTool(
      "rca_candidate_analysis",
      "RCA Candidate Analysis",
      "Run deterministic rule-based candidate attribution over structured evidence. Read-only; never fabricates evidence refs.",
      Type.Object({
        task_metadata: Type.Object({}, { additionalProperties: true }),
        top_functions: Type.Array(Type.Object({}, { additionalProperties: true })),
      }),
      `${base}/rca-analysis`, getEnvelope,
    ),
  ];
  const remoteTools = Array.isArray(catalog?.tools)
    ? catalog.tools
      .filter((spec) => spec?.enabled_by_default !== false && ALLOWED_TOOL_NAMES.includes(spec?.name))
      .map((spec) => makeInternalTool(
        spec.name,
        spec.name.split("_").map((part) => part[0]?.toUpperCase() + part.slice(1)).join(" "),
        String(spec.description || spec.name),
        spec.parameters || { type: "object", additionalProperties: false },
        `${internalBase}${spec.internal_path}`,
        getEnvelope,
      ))
    : [];
  const tools = remoteTools.length === ALLOWED_TOOL_NAMES.length ? remoteTools : fallbackTools;
  const effectiveNames = Array.isArray(runtimePolicy?.effective_tools)
    ? new Set(runtimePolicy.effective_tools)
    : null;
  if (effectiveNames) {
    return tools.filter((tool) => effectiveNames.has(tool.name));
  }
  if (sideEffectPolicy === "READ_ONLY") {
    return tools.filter((tool) => READ_ONLY_TOOL_NAMES.has(tool.name));
  }
  return tools;
}

/** Fetch the canonical catalog. Catalog metadata never grants authority. */
export async function fetchToolCatalog({
  internalBase = "http://127.0.0.1:8191",
  timeoutMs = 3000,
} = {}) {
  const token = process.env.MINI_DROP_PI_INTERNAL_TOKEN || "";
  if (!token) return null;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${internalBase}/internal/agent/tools/catalog`, {
      headers: { "X-Internal-Token": token },
      signal: controller.signal,
    });
    if (!response.ok) return null;
    const payload = await response.json();
    const catalog = payload?.data || payload;
    if (catalog?.schema_version !== "tool-catalog.v1" || !Array.isArray(catalog.tools)) return null;
    const names = new Set(catalog.tools.map((item) => item?.name));
    if (names.size !== ALLOWED_TOOL_NAMES.length || !ALLOWED_TOOL_NAMES.every((name) => names.has(name))) {
      return null;
    }
    if (catalog.tools.some((item) => typeof item?.internal_path !== "string" || !item.internal_path.startsWith("/internal/agent/tools/"))) {
      return null;
    }
    return catalog;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

const READ_ONLY_TOOL_NAMES = new Set([
  "get_case_snapshot",
  "list_case_evidence",
  "get_evidence_projection",
  "compare_evidence",
  "search_knowledge",
  "get_causal_graph",
  "get_evidence_gaps",
  "find_reusable_evidence",
  "list_operations",
  "evaluate_hypotheses",
  "rca_candidate_analysis",
]);

export const ALLOWED_TOOL_NAMES = [
  "get_case_snapshot",
  "list_case_evidence",
  "get_evidence_projection",
  "compare_evidence",
  "search_knowledge",
  "get_causal_graph",
  "get_evidence_gaps",
  "find_reusable_evidence",
  "list_operations",
  "propose_plan_revision",
  "request_operation",
  "evaluate_hypotheses",
  "finish_investigation",
  "rca_candidate_analysis",
];
