/**
 * Mini-Drop Tool Catalog for the Pi Runtime (E3).
 *
 * Security boundary: the model can ONLY see these tools.  No bash/read/write/
 * edit/grep/find/ls.  Read tools expose bounded Case/Evidence projections;
 * proposal tools are compiled by Mini-Drop before any execution.
 */

import { Type } from "typebox";
import { createHash } from "node:crypto";

function sha256Text(value) {
  return createHash("sha256").update(String(value ?? "")).digest("hex");
}

function canonicalize(value) {
  if (Array.isArray(value)) return value.map(canonicalize);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, canonicalize(value[key])]),
    );
  }
  return value;
}

function evidenceRefsFrom(value) {
  const refs = new Set();
  const visit = (item) => {
    if (!item || typeof item !== "object") return;
    if (Array.isArray(item)) return item.forEach(visit);
    for (const [key, child] of Object.entries(item)) {
      if (key === "evidence_id" && typeof child === "string") refs.add(child);
      if (key === "evidence_ids" && Array.isArray(child)) child.forEach((id) => refs.add(String(id)));
      visit(child);
    }
  };
  visit(value);
  return [...refs].filter(Boolean).slice(0, 64);
}

/** Proxy that forwards a tool call back to Mini-Drop FastAPI over internal HTTP. */
function makeInternalTool(
  name,
  label,
  description,
  parameters,
  internalPath,
  getEnvelope,
  onAcceptedFinish,
  onCollectionScheduled,
  onDiscoveryCollecting,
  onInterventionAck,
) {
  return {
    name,
    label,
    description,
    parameters,
    execute: async (_toolCallId, params) => {
      const startedMs = Date.now();
      const startedAt = new Date(startedMs).toISOString();
      const argumentsJson = JSON.stringify(canonicalize(params ?? {}));
      const argumentsHash = sha256Text(argumentsJson);
      const auditBase = {
        tool_call_id: _toolCallId || null,
        tool_name: name,
        arguments_hash: argumentsHash,
        started_at: startedAt,
        retry_count: 0,
      };
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
        let bodyText = "";
        try {
          bodyText = await resp.text();
        } catch {
          bodyText = "";
        }
        const detail = bodyText.slice(0, 1000);
        const finishedAt = new Date().toISOString();
        return {
          content: [{
            type: "text",
            text: `tool ${name} failed: HTTP ${resp.status}${detail ? `: ${detail}` : ""}`,
          }],
          details: {
            ...auditBase,
            http_status: resp.status,
            error_body: detail,
            finished_at: finishedAt,
            duration_ms: Math.max(0, Date.now() - startedMs),
            result_hash: sha256Text(bodyText),
            result_bytes: Buffer.byteLength(bodyText),
            result_truncated: bodyText.length > detail.length,
            evidence_refs: evidenceRefsFrom(bodyText),
          },
        };
      }
      const payload = await resp.json();
      const resultText = JSON.stringify(payload);
      const finishedAt = new Date().toISOString();
      if (name === "acknowledge_intervention" && payload?.data?.accepted === true) {
        onInterventionAck?.(payload.data);
      }
      const finishAccepted = name === "finish_investigation" && payload?.data?.accepted === true;
      if (finishAccepted) {
        onAcceptedFinish?.(payload.data);
      }
      const collectionStatus = String(payload?.data?.collection_request?.status || "");
      const collectionScheduled = name === "propose_collection" && (
        Boolean(payload?.data?.task)
        || ["ACCEPTED", "DISPATCHED", "RUNNING"].includes(collectionStatus)
      );
      if (collectionScheduled) {
        onCollectionScheduled?.(payload.data);
      }
      const discoveryStatus = String(payload?.data?.status || "").trim().toUpperCase();
      const discoveryCollecting = name === "discover_topology" && discoveryStatus === "COLLECTING";
      if (discoveryCollecting) {
        onDiscoveryCollecting?.(payload.data);
      }
      return {
        content: [{ type: "text", text: JSON.stringify(payload).slice(0, 24000) }],
        details: {
          ...auditBase,
          finished_at: finishedAt,
          duration_ms: Math.max(0, Date.now() - startedMs),
          http_status: resp.status,
          result_hash: sha256Text(resultText),
          result_bytes: Buffer.byteLength(resultText),
          result_truncated: resultText.length > 24000,
          evidence_refs: evidenceRefsFrom(payload),
          projection_bytes: resultText.length,
        },
        // A verified finish is the structured output of this Agent run. A
        // scheduled collection/discovery run must instead await durable
        // Evidence. PROPOSED, COMPLETED and PARTIAL discovery results remain
        // non-terminal so the model can continue from the returned graph.
        ...(finishAccepted || collectionScheduled || discoveryCollecting ? { terminate: true } : {}),
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
  onAcceptedFinish = null,
  onCollectionScheduled = null,
  onDiscoveryCollecting = null,
  onInterventionAck = null,
} = {}) {
  const base = `${internalBase}/internal/agent/tools`;
  const fallbackTools = [
    makeInternalTool(
      "acknowledge_intervention",
      "Acknowledge Intervention",
      "Record the mandatory Evidence lifecycle recheck before continuing after an operator intervention.",
      Type.Object({
        case_id: Type.String({ description: "Case ID" }),
        intervention_id: Type.String({ description: "Active intervention ID" }),
        trust_state: Type.String({ description: "Observed trust/lifecycle state after recheck" }),
        evidence_state_rechecked: Type.Literal(true),
        revision_before: Type.Optional(Type.Union([Type.Integer(), Type.Null()])),
        revision_after: Type.Integer({ minimum: 0 }),
      }),
      `${base}/acknowledge-intervention`, getEnvelope,
      null, null, null, onInterventionAck,
    ),
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
      "Read the latest model-proposed and verifier-owned causal graph revision.",
      Type.Object({ case_id: Type.String({ description: "Case ID" }) }),
      `${base}/get-causal-graph`, getEnvelope,
    ),
    makeInternalTool(
      "get_dependency_graph",
      "Get Dependency Graph",
      "Read the evidence-backed communication graph. Dependency edges are not causal claims.",
      Type.Object({ case_id: Type.String({ description: "Case ID" }) }),
      `${base}/get-dependency-graph`, getEnvelope,
    ),
    makeInternalTool(
      "get_evidence_gaps",
      "Get Evidence Gaps",
      "Read explicit unresolved and resolved Evidence gaps.",
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
      "list_collectors",
      "List Collectors",
      "List versioned CollectorSpecs with information goals, risk and cost.",
      Type.Object({ case_id: Type.Optional(Type.String({ description: "Case ID" })) }),
      `${base}/collectors`, getEnvelope,
    ),
    makeInternalTool(
      "propose_collection",
      "Propose Collection",
      "Propose one catalog-backed collection. Mini-Drop rechecks scope, risk, capability and budget before Task creation. Once accepted, this run stops and resumes through an Evidence wakeup; do not poll collection status.",
      Type.Object({
        case_id: Type.String({ description: "Case ID" }),
        collector_id: Type.String({ description: "CollectorSpec collector_id" }),
        target_selector: Type.Object({}, { additionalProperties: true }),
        parameters: Type.Object({}, { additionalProperties: true }),
        information_goal: Type.String({ description: "Exact information goal from CollectorSpec" }),
        reason_summary: Type.Optional(Type.String({ description: "Auditable selection reason" })),
        time_window: Type.Optional(Type.Object({}, { additionalProperties: true })),
        input_evidence_refs: Type.Optional(Type.Array(Type.String({ description: "Evidence that motivated the proposal" }))),
        discovery_run_id: Type.Optional(Type.String({
          description: "Exact discovery-* run ID required whenever the target agent_id + pid pair is outside the original Case process scope, including a newly discovered PID on the same Agent",
          pattern: "^discovery-[0-9a-f]{20}$",
        })),
        discovery_evidence_refs: Type.Optional(Type.Array(
          Type.String({ description: "Active canonical dependency Evidence proving the discovered agent_id + pid" }),
          { maxItems: 32 },
        )),
        idempotency_key: Type.Optional(Type.String({ description: "Optional idempotency key" })),
        runtime_generation: Type.Optional(Type.Integer({ description: "Runtime generation from Snapshot" })),
        expected_control_revision: Type.Optional(Type.Integer({ description: "Control revision from Snapshot" })),
        expected_scope_revision: Type.Optional(Type.Integer({ description: "Scope revision from Snapshot" })),
      }),
      `${base}/collection-proposal`, getEnvelope,
      null,
      onCollectionScheduled,
    ),
    makeInternalTool(
      "discover_topology",
      "Discover Topology",
      "Start or advance bounded Case-scoped network topology discovery. On the first call omit run_id; never invent one. On later calls reuse only the exact discovery-* run_id returned by Mini-Drop. COLLECTING waits for an Evidence wakeup; COMPLETED or PARTIAL returns an evidence-backed dependency graph for continued investigation.",
      Type.Object({
        case_id: Type.String({ description: "Case ID" }),
        run_id: Type.Optional(Type.String({
          description: "Exact discovery-* run ID returned by an earlier call; omit on first call",
          pattern: "^discovery-[0-9a-f]{20}$",
        })),
        seed_agent_id: Type.Optional(Type.String({ description: "Seed Agent ID", maxLength: 128 })),
        seed_pid: Type.Optional(Type.Integer({ description: "Seed process ID", minimum: 1, maximum: 4194304 })),
        max_hops: Type.Optional(Type.Integer({ description: "Maximum discovery hops", minimum: 0, maximum: 4 })),
        max_hosts: Type.Optional(Type.Integer({ description: "Maximum hosts", minimum: 1, maximum: 32 })),
        max_processes: Type.Optional(Type.Integer({ description: "Maximum processes", minimum: 1, maximum: 200 })),
        max_edges: Type.Optional(Type.Integer({ description: "Maximum dependency edges", minimum: 1, maximum: 1000 })),
        max_parallel_tasks: Type.Optional(Type.Integer({ description: "Maximum parallel discovery Tasks", minimum: 1, maximum: 8 })),
        include_loopback: Type.Optional(Type.Boolean({ description: "Include loopback connections" })),
        collect_registered_peers: Type.Optional(Type.Boolean({ description: "Collect snapshots from registered peer Agents" })),
        wait_timeout_sec: Type.Optional(Type.Integer({ description: "Bounded server wait in seconds", minimum: 0, maximum: 45 })),
      }),
      `${base}/topology-discovery`, getEnvelope,
      null,
      null,
      onDiscoveryCollecting,
    ),
    makeInternalTool(
      "propose_plan_revision",
      "Propose Plan Revision",
      "Propose a revision-locked investigation plan. Mini-Drop owns dispatch and execution.",
      Type.Object({
        case_id: Type.String(),
        goal: Type.String(),
        steps: Type.Array(Type.Object({
          step_id: Type.Optional(Type.String({ description: "Optional client reference; server assigns canonical ID" })),
          kind: Type.Optional(Type.Union([Type.Literal("COLLECTION")])),
          collector_id: Type.String({ description: "Collector ID from list_collectors" }),
          target_refs: Type.Optional(Type.Array(Type.String())),
          purpose: Type.String({ description: "Evidence question this step answers" }),
          hypothesis_refs: Type.Optional(Type.Array(Type.String())),
          expected_information: Type.Optional(Type.String()),
          priority: Type.Optional(Type.Integer()),
          priority_source: Type.Optional(Type.Union([
            Type.Literal("AI"), Type.Literal("USER"), Type.Literal("SYSTEM"),
          ])),
          user_locked: Type.Optional(Type.Boolean()),
          depends_on: Type.Optional(Type.Array(Type.String())),
          risk: Type.Optional(Type.Union([
            Type.Literal("READ_LOW"), Type.Literal("READ_HIGH"), Type.Literal("WRITE"),
            Type.Literal("R0"), Type.Literal("R1"), Type.Literal("R2"), Type.Literal("R3"),
          ])),
          selection_strategy: Type.Optional(Type.String()),
          status: Type.Optional(Type.Union([
            Type.Literal("DRAFT"), Type.Literal("QUEUED"), Type.Literal("WAITING_APPROVAL"),
          ])),
        })),
        expected_case_row_version: Type.Integer(),
        expected_scope_revision: Type.Integer(),
        expected_plan_revision: Type.Integer(),
        source: Type.Optional(Type.String()),
      }),
      `${base}/plan`, getEnvelope,
    ),
    makeInternalTool(
      "propose_hypothesis_revision",
      "Propose Hypothesis Revision",
      "Persist evidence-bound hypotheses, contradictions and explicit alternatives.",
      Type.Object({
        case_id: Type.String(),
        hypotheses: Type.Array(Type.Object({
          hypothesis_id: Type.String({ description: "Stable hypothesis ID, for example H1" }),
          statement: Type.String({ description: "The precise causal hypothesis" }),
          status: Type.Union([
            Type.Literal("PROPOSED"), Type.Literal("ACTIVE"), Type.Literal("SUPPORTED"),
            Type.Literal("WEAKENED"), Type.Literal("RULED_OUT"),
            Type.Literal("CONFIRMED"), Type.Literal("UNKNOWN"),
          ]),
          supporting_evidence_refs: Type.Optional(Type.Array(Type.String())),
          contradicting_evidence_refs: Type.Optional(Type.Array(Type.String())),
          missing_evidence: Type.Optional(Type.Array(Type.String())),
          alternatives: Type.Optional(Type.Array(Type.String())),
          confidence: Type.Optional(Type.Number({ minimum: 0, maximum: 1 })),
        })),
        edges: Type.Optional(Type.Array(Type.Object({
          source_hypothesis_id: Type.String(),
          target_hypothesis_id: Type.String(),
          relation: Type.String(),
        }))),
        expected_scope_revision: Type.Integer(),
      }),
      `${base}/hypotheses`, getEnvelope,
    ),
    makeInternalTool(
      "record_evidence_gaps",
      "Record Evidence Gaps",
      "Persist concrete missing facts and collection failures instead of guessing.",
      Type.Object({
        case_id: Type.String(),
        gaps: Type.Array(Type.Object({
          gap_id: Type.Optional(Type.String({ description: "Stable gap ID; server generates one when omitted" })),
          required_fact: Type.String({ description: "Concrete missing fact needed to decide a claim" }),
          status: Type.Union([Type.Literal("OPEN"), Type.Literal("BLOCKING"), Type.Literal("RESOLVED")]),
          blocked_claim: Type.Optional(Type.String()),
          target: Type.Optional(Type.String()),
          reason_code: Type.Optional(Type.String()),
          observed_evidence: Type.Optional(Type.Array(Type.String())),
          conflicting_evidence_refs: Type.Optional(Type.Array(Type.String())),
          what_it_supports: Type.Optional(Type.String()),
          what_it_does_not_support: Type.Optional(Type.String()),
          retryable: Type.Optional(Type.Boolean()),
          next_best_action: Type.Optional(Type.String()),
        })),
        expected_scope_revision: Type.Integer(),
      }),
      `${base}/evidence-gaps`, getEnvelope,
    ),
    makeInternalTool(
      "propose_causal_graph",
      "Propose Causal Graph",
      "Propose causal nodes and edges with active Evidence references. Do not use for dependency-only observations; if no causal mechanism is evidenced, keep the causal graph empty.",
      Type.Object({
        case_id: Type.String(),
        nodes: Type.Array(Type.Object({
          node_id: Type.String(),
          entity_ref: Type.Optional(Type.String()),
          mechanism: Type.String(),
          role: Type.Union([
            Type.Literal("PRIMARY_CAUSE"), Type.Literal("PRIMARY_ROOT_CAUSE"),
            Type.Literal("CONTRIBUTING_FACTOR"), Type.Literal("AMPLIFIER"),
            Type.Literal("PROPAGATED_EFFECT"), Type.Literal("SYMPTOM"),
            Type.Literal("COINCIDENTAL_ANOMALY"), Type.Literal("UNKNOWN"),
          ]),
          supporting_evidence_refs: Type.Optional(Type.Array(Type.String())),
          opposing_evidence_refs: Type.Optional(Type.Array(Type.String())),
        })),
        edges: Type.Array(Type.Object({
          source_node_id: Type.String(),
          target_node_id: Type.String(),
          relation: Type.Union([
            Type.Literal("CAUSES"), Type.Literal("CONTRIBUTES_TO"),
            Type.Literal("AMPLIFIES"), Type.Literal("PROPAGATES_TO"),
            Type.Literal("CORRELATES_WITH"),
          ]),
          supporting_evidence_refs: Type.Optional(Type.Array(Type.String())),
        })),
        expected_scope_revision: Type.Integer(),
        expected_evidence_watermark: Type.Integer(),
        investigation_run_id: Type.Optional(Type.String()),
      }),
      `${base}/causal-graph`, getEnvelope,
    ),
    makeInternalTool(
      "get_collection_status",
      "Get Collection Status",
      "Read authoritative CollectionProposal and CollectionRequest status.",
      Type.Object({ case_id: Type.String({ description: "Case ID" }) }),
      `${base}/collection-status`, getEnvelope,
    ),
    makeInternalTool(
      "submit_evidence_analysis",
      "Submit Evidence Analysis",
      "Persist structured facts after field/span citation verification.",
      Type.Object({
        case_id: Type.String({ description: "Case ID" }),
        analysis_run_id: Type.String({ description: "Queued EvidenceAnalysisRun ID" }),
        facts: Type.Array(Type.Object({
          claim: Type.String(),
          certainty: Type.Union([Type.Literal("HIGH"), Type.Literal("MEDIUM"), Type.Literal("LOW")]),
          citations: Type.Array(Type.Object({
            evidence_id: Type.String(),
            projection_hash: Type.String(),
            field_path: Type.String(),
          }, { additionalProperties: true })),
        })),
        anomalies: Type.Optional(Type.Array(Type.Object({}, { additionalProperties: true }))),
        interpretations: Type.Optional(Type.Array(Type.Object({}, { additionalProperties: true }))),
        conflicts: Type.Optional(Type.Array(Type.Object({}, { additionalProperties: true }))),
        limitations: Type.Optional(Type.Array(Type.String())),
        next_collection_proposals: Type.Optional(Type.Array(Type.Object({}, { additionalProperties: true }))),
        token_usage: Type.Optional(Type.Object({}, { additionalProperties: true })),
        latency_ms: Type.Optional(Type.Integer()),
      }),
      `${base}/evidence-analysis`, getEnvelope,
    ),
    makeInternalTool(
      "get_evidence_analyses",
      "Get Evidence Analyses",
      "Read persisted EvidenceAnalysisRuns and stale-input state.",
      Type.Object({
        case_id: Type.String({ description: "Case ID" }),
        evidence_id: Type.Optional(Type.String({ description: "Optional Evidence ID" })),
      }),
      `${base}/evidence-analyses`, getEnvelope,
    ),
    makeInternalTool(
      "finish_investigation",
      "Finish Investigation",
      "Submit a structured Conclusion. Verifier owns final state. Recommendations are optional; if supplied each requires concrete_action, target and cause_or_edge_ref.",
      Type.Object({
        case_id: Type.String({ description: "Case ID" }),
        summary: Type.String({ description: "Conclusion" }),
        evidence_ids: Type.Array(Type.String({ description: "Evidence IDs" })),
        state: Type.Optional(Type.Union([
          Type.Literal("CONFIRMED"),
          Type.Literal("PARTIALLY_CONFIRMED"),
          Type.Literal("INSUFFICIENT_EVIDENCE"),
        ])),
        claims: Type.Optional(Type.Array(Type.Object({
          claim: Type.Optional(Type.String({ description: "Claim text" })),
          evidence_id: Type.Optional(Type.String({ description: "Evidence ID" })),
          evidence_ids: Type.Optional(Type.Array(Type.String({ description: "Evidence IDs" }))),
          projection_hash: Type.Optional(Type.String({ description: "Projection hash; server fills it when unambiguous" })),
          field_path: Type.Optional(Type.String({ description: "Field path inside projection content" })),
          predicate: Type.Optional(Type.Object({}, { additionalProperties: true })),
          confidence: Type.Optional(Type.Number({ minimum: 0, maximum: 1 })),
        }))),
        limitations: Type.Optional(Type.Array(Type.String({ description: "Limitations" }))),
        abstention_reason: Type.Optional(Type.String({ description: "Required when abstaining without Evidence" })),
        primary_root_causes: Type.Optional(Type.Array(Type.Object({}, { additionalProperties: true }))),
        contributing_factors: Type.Optional(Type.Array(Type.Object({}, { additionalProperties: true }))),
        amplifiers: Type.Optional(Type.Array(Type.Object({}, { additionalProperties: true }))),
        propagated_effects: Type.Optional(Type.Array(Type.Object({}, { additionalProperties: true }))),
        symptoms: Type.Optional(Type.Array(Type.Object({}, { additionalProperties: true }))),
        coincidental_anomalies: Type.Optional(Type.Array(Type.Object({}, { additionalProperties: true }))),
        ruled_out: Type.Optional(Type.Array(Type.Object({}, { additionalProperties: true }))),
        recommendations: Type.Optional(Type.Array(Type.Object({
          recommendation_id: Type.Optional(Type.String()),
          cause_or_edge_ref: Type.String(),
          target: Type.String(),
          concrete_action: Type.String(),
          rationale: Type.Optional(Type.String()),
          evidence_refs: Type.Optional(Type.Array(Type.String())),
          confidence: Type.Optional(Type.Number({ minimum: 0, maximum: 1 })),
          verification_operations: Type.Optional(Type.Array(Type.String())),
          success_criteria: Type.Optional(Type.Array(Type.String())),
          rollback_or_failure_condition: Type.Optional(Type.String()),
        }))),
      }),
      `${base}/finish`, getEnvelope,
      onAcceptedFinish,
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
        onAcceptedFinish,
        onCollectionScheduled,
        onDiscoveryCollecting,
        onInterventionAck,
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
  "get_dependency_graph",
  "get_evidence_gaps",
  "find_reusable_evidence",
  "list_collectors",
  "get_collection_status",
  "get_evidence_analyses",
  "submit_evidence_analysis",
]);

export const ALLOWED_TOOL_NAMES = [
  "acknowledge_intervention",
  "get_case_snapshot",
  "list_case_evidence",
  "get_evidence_projection",
  "compare_evidence",
  "search_knowledge",
  "get_causal_graph",
  "get_dependency_graph",
  "get_evidence_gaps",
  "find_reusable_evidence",
  "list_collectors",
  "propose_collection",
  "discover_topology",
  "propose_plan_revision",
  "propose_hypothesis_revision",
  "record_evidence_gaps",
  "propose_causal_graph",
  "get_collection_status",
  "submit_evidence_analysis",
  "get_evidence_analyses",
  "finish_investigation",
];
