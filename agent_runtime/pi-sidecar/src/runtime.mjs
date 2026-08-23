/**
 * Runtime Session lifecycle (E3, plan section 6.2).
 *
 * One active Case maps to one Pi AgentSession.  runtime_generation guards
 * against late events from a discarded session.  Sessions are held in memory;
 * the Case DB remains the authority.  On generation loss, a new session is
 * rebuilt from a CaseContextSnapshot.
 */

import {
  createAgentSession,
  SessionManager,
  defineTool,
} from "@earendil-works/pi-coding-agent";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { buildToolCatalog, fetchToolCatalog } from "./tools.mjs";
import { EventSpool } from "./event-spool.mjs";

const piEntry = fileURLToPath(import.meta.resolve("@earendil-works/pi-coding-agent"));
const piPackage = JSON.parse(readFileSync(join(dirname(piEntry), "..", "package.json"), "utf8"));
const PI_RUNTIME_VERSION = piPackage.version;

// Only normalized lifecycle records cross the runtime -> Control boundary.
// Sequence allocation uses the same allow-list as forwarding, so the public
// cursor counts persisted records rather than transient SDK deltas that are
// intentionally discarded.
const PERSISTED_RUNTIME_EVENT_TYPES = new Set([
  "message_start", "message_end", "tool_execution_start", "tool_execution_end",
  "turn_start", "turn_end", "agent_start", "agent_end", "agent_settled",
]);

function canonicalizeAudit(value) {
  if (Array.isArray(value)) return value.map(canonicalizeAudit);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, canonicalizeAudit(value[key])]),
    );
  }
  return value;
}

const DEFAULT_CONTEXT_MAX_CHARS = 24_000;
const MIN_CONTEXT_MAX_CHARS = 256;
const MAX_CONTEXT_MAX_CHARS = 200_000;


function boundedInteger(value, fallback, minimum, maximum) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(maximum, Math.max(minimum, parsed));
}

function contextMaxChars() {
  return boundedInteger(
    process.env.MINI_DROP_PI_CONTEXT_MAX_CHARS,
    DEFAULT_CONTEXT_MAX_CHARS,
    MIN_CONTEXT_MAX_CHARS,
    MAX_CONTEXT_MAX_CHARS,
  );
}

function resetSessionPerTurnEnabled() {
  const raw = String(process.env.MINI_DROP_PI_RESET_SESSION_PER_TURN || "").trim().toLowerCase();
  return raw === "1" || raw === "true" || raw === "yes" || raw === "on";
}

/** Runtime model setup is local-only until a turn invokes the selected model. */
export function modelRuntimeOptionsFromEnvironment() {
  const modelsPath = String(process.env.MINI_DROP_PI_MODELS_PATH || "").trim() || null;
  const authPath = String(process.env.MINI_DROP_PI_AUTH_PATH || "").trim() || undefined;
  // `allowModelNetwork` controls catalog refresh only. It must stay false here:
  // PI_OFFLINE may be set to suppress catalog traffic, but never suppresses the
  // provider request made by an actual Agent turn.
  const options = { modelsPath, allowModelNetwork: false };
  if (authPath) options.authPath = authPath;
  return options;
}

function jsonChars(value) {
  try {
    return JSON.stringify(value, null, 2).length;
  } catch {
    return String(value ?? "").length;
  }
}

function truncateText(value, limit) {
  const text = String(value ?? "");
  if (limit <= 0) return "";
  if (text.length <= limit) return text;
  const marker = `...[TRUNCATED ${text.length - limit} chars]`;
  if (marker.length >= limit) return text.slice(0, limit);
  const head = Math.max(0, limit - marker.length);
  return `${text.slice(0, head)}${marker}`;
}

function compactValue(value, {
  stringLimit = 600,
  listLimit = 16,
  depth = 0,
  maxDepth = 5,
} = {}) {
  if (value === null || value === undefined || typeof value === "boolean" || typeof value === "number") {
    return value;
  }
  if (typeof value === "string") return truncateText(value, stringLimit);
  if (depth >= maxDepth) return "[MAX_DEPTH_REACHED]";
  if (Array.isArray(value)) {
    return value.slice(0, listLimit).map((item) => compactValue(item, {
      stringLimit, listLimit, depth: depth + 1, maxDepth,
    }));
  }
  if (typeof value === "object") {
    const result = {};
    for (const [key, item] of Object.entries(value)) {
      result[key] = compactValue(item, {
        stringLimit, listLimit, depth: depth + 1, maxDepth,
      });
    }
    return result;
  }
  return String(value);
}

const EVIDENCE_REFERENCE_KEYS = [
  "evidence_id", "evidence_ids", "projection_hash", "projection_kind", "summary",
  "field_path", "artifact_type", "target_ref", "status", "freshness", "quality",
  "lifecycle_status", "trust_state", "review_revision", "time_window", "truncated",
  "attachment_id", "resource_ref",
];

function compactEvidenceItem(item, stringLimit = 600) {
  if (!item || typeof item !== "object" || Array.isArray(item)) {
    return { summary: truncateText(item, stringLimit) };
  }
  const result = {};
  for (const key of EVIDENCE_REFERENCE_KEYS) {
    if (!(key in item)) continue;
    const value = item[key];
    if (key === "summary") {
      result[key] = truncateText(value, stringLimit);
    } else if (key === "evidence_ids" && Array.isArray(value)) {
      result[key] = value.slice(0, 32).map((id) => truncateText(id, 160));
    } else if (key === "projection_hash" || key === "evidence_id" || key === "attachment_id") {
      result[key] = truncateText(value, 256);
    } else {
      result[key] = compactValue(value, { stringLimit: Math.min(stringLimit, 240), listLimit: 8 });
    }
  }
  // Keep the three audit fields explicit even when an older snapshot omitted
  // one of them. This makes any omission visible to the model and evaluator.
  if (!("evidence_id" in result) && item.evidence_id !== undefined) {
    result.evidence_id = truncateText(item.evidence_id, 256);
  }
  if (!("projection_hash" in result) && item.projection_hash !== undefined) {
    result.projection_hash = truncateText(item.projection_hash, 256);
  }
  if (!("summary" in result)) {
    result.summary = truncateText(item.summary || item.content?.summary || "", stringLimit);
  }
  return result;
}

function evidenceReference(item) {
  if (!item || typeof item !== "object") return { summary: truncateText(item, 160) };
  const reference = {};
  for (const key of ["evidence_id", "projection_hash", "attachment_id", "resource_ref"]) {
    if (item[key] !== undefined) reference[key] = truncateText(item[key], 256);
  }
  if (Array.isArray(item.evidence_ids)) {
    reference.evidence_ids = item.evidence_ids.slice(0, 32).map((id) => truncateText(id, 160));
  }
  if (item.summary !== undefined) reference.summary = truncateText(item.summary, 80);
  return reference;
}

function evidenceReferenceKey(item) {
  return [item?.evidence_id, item?.projection_hash, item?.attachment_id]
    .filter((value) => value !== undefined && value !== null && String(value) !== "")
    .join("|");
}

/**
 * Fit a CaseContext projection into a deterministic character budget.
 * Raw Evidence stays in Mini-Drop; this only controls the prompt projection.
 */
export function boundCaseContextPayload(payload, maxChars = contextMaxChars()) {
  // The public environment setting is bounded to a practical minimum, but the
  // prompt builder may reserve less after fixed labels/user text. Keep this
  // helper usable for that internal residual budget.
  const limit = boundedInteger(maxChars, DEFAULT_CONTEXT_MAX_CHARS, 128, MAX_CONTEXT_MAX_CHARS);
  const originalChars = jsonChars(payload);
  const source = payload && typeof payload === "object" ? payload : {};
  const evidenceSource = Array.isArray(source.evidence_summary) ? source.evidence_summary : [];
  const omittedEvidence = [];
  let result = compactValue(source, { stringLimit: 600, listLimit: 20 });
  result.evidence_summary = evidenceSource.map((item) => compactEvidenceItem(item, 600));

  const meta = {
    bounded: true,
    max_chars: limit,
    original_chars: originalChars,
    omitted_fields: [],
    omitted_evidence_refs: [],
    omitted_evidence_count: 0,
  };
  result._context_meta = meta;

  const trimLists = [
    "skills", "collection_requests", "collection_proposals", "evidence_analyses",
    "recommendations", "hypothesis_edges", "hypotheses", "information_goals",
    "running_task_ids", "current_support", "counterevidence",
  ];
  const dropFields = [
    "causal_graph", "directive", "strategy_guidance", "runtime_options",
    "runtime_policy", "budget", "skills", "collection_requests", "collection_proposals",
    "evidence_analyses", "recommendations", "hypothesis_edges", "hypotheses",
    "information_goals", "running_task_ids",
  ];

  const refreshMeta = () => {
    // Keep the omission ledger bounded as well; the count records references
    // beyond the small visible sample without allowing metadata to consume the
    // entire prompt budget.
    meta.omitted_evidence_count = omittedEvidence.length;
    meta.omitted_evidence_refs = omittedEvidence.slice(0, 8);
    result._context_meta = meta;
  };

  // First reduce long nested values. Evidence reference fields are compacted
  // separately, so this cannot erase their IDs or projection hashes.
  for (const stringLimit of [320, 160, 96]) {
    if (jsonChars(result) <= limit) break;
    result = compactValue(result, { stringLimit, listLimit: 20 });
    result.evidence_summary = evidenceSource.map((item) => compactEvidenceItem(item, stringLimit));
    result._context_meta = meta;
  }

  // Remove list tails in a fixed order, recording every Evidence reference
  // that was omitted instead of silently dropping it.
  for (const field of trimLists) {
    const list = result[field];
    if (!Array.isArray(list)) continue;
    while (list.length > 1 && jsonChars(result) > limit) {
      list.pop();
    }
    if (jsonChars(result) <= limit) break;
  }

  if (jsonChars(result) > limit && Array.isArray(result.evidence_summary)) {
    while (result.evidence_summary.length > 1 && jsonChars(result) > limit) {
      const removed = result.evidence_summary.pop();
      const ref = evidenceReference(removed);
      if (evidenceReferenceKey(ref)) omittedEvidence.push(ref);
      refreshMeta();
    }
  }

  for (const field of dropFields) {
    if (jsonChars(result) <= limit) break;
    if (!(field in result)) continue;
    delete result[field];
    meta.omitted_fields.push(field);
    refreshMeta();
  }

  // Keep a compact all-reference index when the full summary had to be
  // shortened. This gives the model/evaluator a visible, auditable omission.
  if (omittedEvidence.length) {
    result._context_meta.omitted_evidence_count = omittedEvidence.length;
    result._context_meta.omitted_evidence_refs = omittedEvidence.slice(0, 8);
  }

  // Last resort: retain the contract identity and compact Evidence references
  // only. This branch is deterministic for unusually small configured limits.
  if (jsonChars(result) > limit) {
    const refs = evidenceSource.map((item) => evidenceReference(item));
    const coreMeta = limit < 256
      ? { bounded: true }
      : {
        bounded: true,
        max_chars: limit,
        original_chars: originalChars,
        omitted_evidence_count: 0,
      };
    // Preserve the audit contract before optional evidence tails. These fields
    // explain what changed and what the Agent must re-check after compaction.
    const core = limit < 256 ? {} : { _context_meta: coreMeta };
    const compactPriorityField = (field, value) => {
      if (field === "intervention" && value && typeof value === "object") {
        return {
          intervention_id: value.intervention_id,
          kind: value.kind,
          required: Boolean(value.required),
          trust_state: value.trust_state,
          evidence_state_rechecked: Boolean(value.evidence_state_rechecked),
          revision_before: value.revision_before,
          revision_after: value.revision_after,
          affected_evidence_ids: (value.affected_evidence_ids || []).slice(0, 8),
        };
      }
      if (field === "conclusion" && value && typeof value === "object") {
        return {
          conclusion_id: value.conclusion_id,
          state: value.state,
          revision: value.revision,
          report_text: truncateText(value.report_text || value.summary || "", 120),
        };
      }
      return compactValue(value, { stringLimit: 120, listLimit: 4, maxDepth: 3 });
    };
    for (const field of [
      "intervention", "evidence_summary", "case_id", "runtime_generation", "control_revision", "scope_revision", "plan_revision",
      "evidence_watermark", "conclusion", "evidence_gaps",
      "target_scope", "current_support", "counterevidence",
    ]) {
      if (result[field] === undefined) continue;
      let fieldValue = compactPriorityField(field, result[field]);
      let candidate = { ...core, [field]: fieldValue };
      if (field === "intervention" && jsonChars(candidate) > limit) {
        fieldValue = {
          intervention_id: result.intervention?.intervention_id,
          required: Boolean(result.intervention?.required),
        };
        candidate = { ...core, [field]: fieldValue };
      }
      if (jsonChars(candidate) <= limit) Object.assign(core, { [field]: candidate[field] });
    }
    core.evidence_summary = [];
    for (const ref of refs) {
      const candidate = {
        ...core,
        evidence_summary: [...core.evidence_summary, ref],
      };
      if (jsonChars(candidate) <= limit) {
        core.evidence_summary.push(ref);
      } else {
        coreMeta.omitted_evidence_count += 1;
      }
    }
    for (const field of ["side_effect_policy"]) {
      if (result[field] === undefined) continue;
      const candidate = { ...core, [field]: truncateText(result[field], 320) };
      if (jsonChars(candidate) <= limit) Object.assign(core, { [field]: candidate[field] });
    }
    result = core;
  }

  // The normal operator range is hundreds of characters to a few hundred KiB.
  // Still make the helper total for callers that reserve a smaller internal
  // residual budget: a compact audit marker is preferable to returning an
  // over-budget object.
  if (jsonChars(result) > limit) {
    const omittedCount = Number(result?._context_meta?.omitted_evidence_count || evidenceSource.length);
    const candidates = [
      {
        _context_meta: { bounded: true, max_chars: limit },
        intervention: result.intervention && {
          intervention_id: result.intervention.intervention_id,
          required: Boolean(result.intervention.required),
        },
      },
      {
        _context_meta: {
          bounded: true,
          max_chars: limit,
          original_chars: originalChars,
          omitted_evidence_count: omittedCount,
        },
      },
      { _context_meta: { bounded: true, max_chars: limit } },
      { _context_meta: { bounded: true } },
      {},
    ];
    result = candidates.find((candidate) => jsonChars(candidate) <= limit) || {};
  }

  return result;
}

/** Build the user-visible dynamic prompt under MINI_DROP_PI_CONTEXT_MAX_CHARS. */
export function buildBoundedAgentPrompt({
  policy = "AUTO_READ_LOW",
  contextPayload = {},
  userMessage = "",
  maxChars = contextMaxChars(),
} = {}) {
  const limit = boundedInteger(maxChars, DEFAULT_CONTEXT_MAX_CHARS, MIN_CONTEXT_MAX_CHARS, MAX_CONTEXT_MAX_CHARS);
  const policyText =
    `[Policy Context]\nside_effect_policy=${policy}. ` +
    (policy === "READ_ONLY"
      ? "This is a READ_ONLY turn: use only read-only tools. Never request data collection, plan execution or any mutation."
      : "Choose the next information goal and action from observed Evidence and the live Collector Catalog. Do not follow a fixed collector order.");
  const contextLabel = "\n\n[CaseContext]\n";
  const userLabel = "\n\n[User]\n";
  const rawUser = String(userMessage ?? "");
  const fixedChars = policyText.length + contextLabel.length + userLabel.length;
  const available = Math.max(1, limit - fixedChars);
  // User instructions are kept intact whenever possible; the remainder is
  // allocated to the evidence-bearing CaseContext projection.
  const userBudget = Math.min(rawUser.length, Math.max(0, Math.floor(available * 0.2)));
  const boundedUser = truncateText(rawUser, userBudget);
  let contextBudget = Math.max(128, available - boundedUser.length);
  contextBudget = Math.min(contextBudget, MAX_CONTEXT_MAX_CHARS);
  let boundedContext = boundCaseContextPayload(contextPayload, contextBudget);
  let contextBlock = JSON.stringify(boundedContext, null, 2);
  let prompt = `${policyText}${contextLabel}${contextBlock}${userLabel}${boundedUser}`;

  // For a tight budget, reduce the user tail first, then refit the context.
  if (prompt.length > limit) {
    const overflow = prompt.length - limit;
    const reducedUser = truncateText(rawUser, Math.max(0, boundedUser.length - overflow));
    contextBudget = Math.max(128, limit - fixedChars - reducedUser.length);
    boundedContext = boundCaseContextPayload(contextPayload, contextBudget);
    contextBlock = JSON.stringify(boundedContext, null, 2);
    prompt = `${policyText}${contextLabel}${contextBlock}${userLabel}${reducedUser}`;
  }

  // The configured minimum keeps the fixed labels and audit marker representable.
  // If an operator deliberately chooses a smaller value, truncate only the
  // user text as a final deterministic fallback; never cut JSON mid-object.
  if (prompt.length > limit) {
    // Refit once more with no user text and the exact remaining budget. This
    // keeps the JSON valid and leaves the bounded Case/Evidence projection
    // intact even when an unusually small limit is configured.
    const noUserBudget = Math.max(128, limit - policyText.length - contextLabel.length - userLabel.length);
    boundedContext = boundCaseContextPayload(contextPayload, noUserBudget);
    contextBlock = JSON.stringify(boundedContext, null, 2);
    const prefix = `${policyText}${contextLabel}${contextBlock}${userLabel}`;
    const remaining = Math.max(0, limit - prefix.length);
    prompt = `${prefix}${truncateText(rawUser, remaining)}`;
    if (prompt.length > limit) {
      // Fixed labels are intentionally short; if metadata overhead still wins,
      // use compact labels and retain a valid minimal JSON context. Never cut
      // the serialized context in the middle of an object.
      const shortPolicy = `policy=${policy}`;
      const shortContextLabel = "\n[C]\n";
      const shortUserLabel = "\n[U]\n";
      const minimalBudget = Math.max(
        128,
        limit - shortPolicy.length - shortContextLabel.length - shortUserLabel.length,
      );
      let minimal = boundCaseContextPayload({
        case_id: contextPayload?.case_id || "",
        case_goal: contextPayload?.case_goal || "",
        runtime_generation: contextPayload?.runtime_generation,
        intervention: contextPayload?.intervention || {},
        evidence_summary: contextPayload?.evidence_summary || [],
      }, minimalBudget);
      let minimalBlock = JSON.stringify(minimal, null, 2);
      let minimalPrefix = `${shortPolicy}${shortContextLabel}${minimalBlock}${shortUserLabel}`;
      if (minimalPrefix.length > limit) {
        minimal = {};
        minimalBlock = "{}";
        minimalPrefix = `${shortPolicy}${shortContextLabel}${minimalBlock}${shortUserLabel}`;
      }
      boundedContext = minimal;
      contextBlock = minimalBlock;
      prompt = `${minimalPrefix}${truncateText(rawUser, Math.max(0, limit - minimalPrefix.length))}`;
    }
  }
  return { prompt, context: boundedContext, contextBlock, maxChars: limit };
}

export function buildEvidenceAgentSystemPrompt(promptVariant = "default") {
  return (
    "You are the Mini-Drop Evidence-native supervised diagnostic Agent. Investigate " +
    "only from registered Case, hypotheses, Evidence, causal state, Collector Catalog and analysis state; " +
    "never use shell/file access. " +
    "Always read the Case Snapshot and existing Evidence before answering. " +
    "When CaseContext.intervention.required is true, your first tool call MUST be acknowledge_intervention with the exact intervention_id; " +
    "set evidence_state_rechecked=true only after re-reading Evidence lifecycle and review_revision, and include revision_before and revision_after. " +
    "When historical procedures, operator knowledge, or prior Case memory may help, call search_knowledge with a precise query. " +
    "Treat returned Knowledge chunks as background only, cite their chunk_id separately, and never present Knowledge as current Evidence. " +
    "For READ_ONLY turns use only read-only tools and never request data " +
    "collection. If evidence is insufficient, report a precise Evidence " +
    "Gap, revise hypotheses/plan, and either propose one high-information Collector or abstain. " +
    "Treat deterministic parsing and verification as guardrails, never as a rule-based root-cause authority. Stop " +
    "when evidence is sufficient, budget is exhausted, scope/approval blocks " +
    "progress, or another collection would add no information. Never fabricate evidence. " +
    "Do not repeat a Collector whose request is already pending or completed. " +
    "After a Collector is accepted, end the current run and wait for Mini-Drop's durable Evidence wakeup; never poll for completion. " +
    "When the Case has a seed process but its dependencies are unknown, use discover_topology. " +
    "For its first call omit run_id entirely and never invent one; after COLLECTING, reuse only the exact discovery-* run_id returned by Mini-Drop. " +
    "A PROPOSED discovery result is not execution; COLLECTING means end this run and await the durable Evidence wakeup. " +
    "COMPLETED or PARTIAL discovery is not an investigation conclusion: continue with get_dependency_graph and Evidence, preserve coverage limitations, and never treat dependency edges as causal claims. " +
    "If Evidence proves only communication/dependency and no causal mechanism, do not call propose_causal_graph; keep the causal graph empty and finish with no asserted root cause. " +
    "For an investigative turn where finish_investigation is available, every terminal outcome, including insufficient evidence, " +
    "must be submitted through finish_investigation. Never substitute a plain-text final or stage conclusion. " +
    "Final claims must cite evidence_id, projection_hash and exact " +
    `field/span support. prompt_variant=${promptVariant}.`
  );
}

export class RuntimeManager {
  constructor({ modelRuntime, internalBase, noSkills = true, eventSpool } = {}) {
    this.modelRuntime = modelRuntime;
    this.internalBase = internalBase || "http://127.0.0.1:8191";
    this.sessions = new Map(); // case_id -> {session, generation, context}
    this.lastSeq = new Map(); // case_id -> event seq
    this.forwardedKeys = new Set(); // idempotency keys already POSTed
    this.lastAnswers = new Map(); // case_id -> last auditable final answer
    this.lastUsage = new Map(); // case_id -> cumulative SessionStats token/cost snapshot
    this.messageStarts = new Map(); // case_id -> message_start wall-clock ms
    this.recordedModelResponses = new Map(); // case_id -> response hashes already audited
    this.toolStarts = new Map(); // case_id:tool_call_id -> monotonic wall-clock audit timing
    this.noSkills = noSkills;
    this.toolCatalog = null;
    this.eventSpool = eventSpool || new EventSpool(
      process.env.MINI_DROP_PI_EVENT_SPOOL_PATH || join(process.cwd(), "data", "pi-runtime-events.jsonl"),
    );
  }

  async ensureModelRuntime() {
    if (this.modelRuntime) return this.modelRuntime;
    const { ModelRuntime } = await import("@earendil-works/pi-coding-agent");
    // A null path preserves the existing built-in model behavior. Operators
    // can opt into a local/proxy models.json without relying on ~/.pi files.
    this.modelRuntime = await ModelRuntime.create(modelRuntimeOptionsFromEnvironment());
    const provider = process.env.MINI_DROP_PI_MODEL_PROVIDER || "deepseek";
    const modelId = process.env.MINI_DROP_PI_MODEL || "deepseek-v4-flash";
    const apiKey = process.env.DEEPSEEK_API_KEY || process.env.MINI_DROP_AI_API_KEY || "";
    if (apiKey && typeof this.modelRuntime.setRuntimeApiKey === "function") {
      // setRuntimeApiKey normally refreshes remote catalogs. Keep that refresh
      // local even when PI_OFFLINE is omitted; this flag is an optimization,
      // never a gate on the actual provider completion request.
      await this.modelRuntime.setRuntimeApiKey(provider, apiKey, { allowNetwork: false });
    }
    this.selectedModel = typeof this.modelRuntime.getModel === "function"
      ? this.modelRuntime.getModel(provider, modelId)
      : undefined;
    return this.modelRuntime;
  }

  async startOrResume(caseContext) {
    const caseId = caseContext.case_id;
    const incomingGeneration = Number(caseContext?.runtime_generation) || 1;
    let generation = incomingGeneration;
    const catalog = await this._loadToolCatalog();
    const existing = this.sessions.get(caseId);
    if (existing) {
      // v6 7.1: a Cycle never reuses a stale context.  Refresh the binding
      // with the new Snapshot; if Server rotated generation, discard the old
      // in-memory Pi session and rebuild from the latest Snapshot.
      const optionsChanged = existing.optionSignature !== undefined
        && existing.optionSignature !== this._optionSignature(caseContext);
      if (incomingGeneration > existing.generation || optionsChanged) {
        if (optionsChanged && incomingGeneration <= existing.generation) {
          generation = existing.generation + 1;
        }
        // Stop the old SDK session before dropping it. This prevents an
        // in-flight turn from publishing tool results after Evidence review
        // rotated the generation, and guarantees no old transcript is reused.
        for (const method of ["abort", "close", "dispose"]) {
          if (typeof existing.session?.[method] !== "function") continue;
          try {
            await existing.session[method]();
          } catch {
            // Generation fencing remains the final authority if the SDK has
            // no graceful shutdown path for the current in-flight request.
          }
          break;
        }
        this.sessions.delete(caseId);
        this.lastSeq.delete(caseId);
        this.lastAnswers.delete(caseId);
        this.lastUsage.delete(caseId);
        this.messageStarts.delete(caseId);
        this.recordedModelResponses.delete(caseId);
      } else {
        existing.context = caseContext;
        existing.concluded = existing.concluded || Boolean(caseContext?.conclusion?.conclusion_id);
        existing.optionSignature = this._optionSignature(caseContext);
        existing.toolEnvelope.current = this._toolEnvelope(caseContext, existing.currentTurnId);
        const activeNames = buildToolCatalog({
          internalBase: this.internalBase,
          sideEffectPolicy: caseContext?.side_effect_policy || "AUTO_READ_LOW",
          catalog,
          runtimePolicy: caseContext?.runtime_policy,
          getEnvelope: () => existing.toolEnvelope.current,
          onAcceptedFinish: () => this._markConcluded(caseId),
          onInterventionAck: (ack) => this._markInterventionAck(caseId, ack),
        }).map((tool) => tool.name);
        existing.session.setActiveToolsByName(activeNames);
        await this.replayPending(caseId);
        return this._binding(caseId);
      }
    }
    if (generation !== incomingGeneration) {
      caseContext = { ...caseContext, runtime_generation: generation };
    }
    await this.ensureModelRuntime();
    const toolEnvelope = { current: this._toolEnvelope(caseContext) };
    const tools = buildToolCatalog({
      internalBase: this.internalBase,
      sideEffectPolicy: "AUTO_READ_LOW",
      catalog,
      getEnvelope: () => toolEnvelope.current,
      onAcceptedFinish: () => this._markConcluded(caseId),
      onCollectionScheduled: () => this._markCollectionScheduled(caseId),
      onDiscoveryCollecting: () => this._markDiscoveryCollecting(caseId),
      onInterventionAck: (ack) => this._markInterventionAck(caseId, ack),
    });
    const allowedNames = buildToolCatalog({
      internalBase: this.internalBase,
      sideEffectPolicy: caseContext?.side_effect_policy || "AUTO_READ_LOW",
      catalog,
      runtimePolicy: caseContext?.runtime_policy,
      getEnvelope: () => toolEnvelope.current,
      onAcceptedFinish: () => this._markConcluded(caseId),
      onInterventionAck: (ack) => this._markInterventionAck(caseId, ack),
    }).map((tool) => tool.name);
    const requestedEffort = caseContext?.runtime_options?.reasoning_effort
      || process.env.MINI_DROP_PI_THINKING_LEVEL || "high";
    const thinkingLevel = requestedEffort === "none" ? "off" : requestedEffort;
    const promptVariant = caseContext?.runtime_options?.prompt_variant || "default";
    const { session } = await createAgentSession({
      model: this._selectedModelFor(caseContext),
      thinkingLevel,
      // Security: built-in shell/file tools are disabled; ONLY Mini-Drop
      // allowlisted custom tools remain available.
      noTools: "builtin",
      tools: allowedNames,
      customTools: tools,
      sessionManager: SessionManager.inMemory(),
      systemPrompt: buildEvidenceAgentSystemPrompt(promptVariant),
      resourceLoader: null,
    });
    const entry = {
      session,
      generation,
      context: caseContext,
      subscribed: false,
      currentTurnId: null,
      lastError: "",
      toolEnvelope,
      optionSignature: this._optionSignature(caseContext),
      concluded: Boolean(caseContext?.conclusion?.conclusion_id),
      runStopRequested: false,
      pendingWakeup: "",
      wakeupWaitScheduled: false,
      terminalReminderCount: 0,
      terminalReminderToken: 0,
      activeTurnId: null,
      fencedTurnIds: new Set(),
    };
    this.sessions.set(caseId, entry);
    // Pi only honors terminate=true when every result in a parallel tool batch
    // terminates. This fence also stops mixed batches after an accepted finish
    // or collection/discovery schedule, before another provider request can start.
    session.agent.shouldStopAfterTurn = () => entry.runStopRequested;
    this.lastSeq.set(caseId, 0);
    await this.replayPending(caseId);
    return this._binding(caseId);
  }

  async _loadToolCatalog() {
    const catalog = await fetchToolCatalog({ internalBase: this.internalBase });
    if (catalog) this.toolCatalog = catalog;
    return this.toolCatalog;
  }

  _optionSignature(context) {
    return JSON.stringify({
      options: context?.runtime_options || {},
      side_effect_policy: context?.side_effect_policy || "AUTO_READ_LOW",
      effective_tools: [...(context?.runtime_policy?.effective_tools || [])].sort(),
    });
  }

  _selectedModelFor(context) {
    const requested = String(context?.runtime_options?.model || "").trim();
    if (!requested || typeof this.modelRuntime?.getModel !== "function") {
      return this.selectedModel || this.modelRuntime;
    }
    const parts = requested.split("/");
    const provider = parts.length > 1
      ? parts.shift()
      : process.env.MINI_DROP_PI_MODEL_PROVIDER || "deepseek";
    const model = this.modelRuntime.getModel(provider, parts.join("/"));
    if (!model) throw new Error(`MODEL_NOT_REGISTERED:${requested}`);
    return model;
  }

  _toolEnvelope(context, triggerTurnId = null) {
    return {
      case_id: context?.case_id,
      side_effect_policy: context?.side_effect_policy || "AUTO_READ_LOW",
      runtime_generation: Number(context?.runtime_generation) || 1,
      expected_control_revision: Number(context?.control_revision) || 1,
      expected_scope_revision: Number(context?.scope_revision) || 1,
      diagnostic_strategy_id: context?.diagnostic_strategy_id || "hybrid",
      strategy_params: context?.strategy_params || {},
      runtime_policy: context?.runtime_policy || {},
      runtime_options: context?.runtime_options || {},
      intervention_id: context?.intervention?.intervention_id || undefined,
      trigger_turn_id: triggerTurnId || undefined,
    };
  }

  get(case_id) {
    return this.sessions.get(case_id) || null;
  }

  _binding(case_id) {
    const entry = this.sessions.get(case_id);
    if (!entry) throw new Error(`no runtime session for ${case_id}`);
    return {
      case_id,
      runtime_type: "pi",
      runtime_version: `pi-${PI_RUNTIME_VERSION}`,
      runtime_session_id: case_id,
      runtime_generation: entry.generation,
      status: "READY",
      last_event_seq: this.lastSeq.get(case_id) || 0,
      last_context_snapshot_id: entry.context?.context_snapshot_id || null,
      lease_owner: "mini-drop-pi-sidecar",
    };
  }

  async submitShadowPlan(case_id, context) {
    // Shadow mode: return a structured plan proposal without creating Tasks.
    // This is intentionally credential-free: the contract surface must work
    // even when no model/provider key is configured. A later milestone can
    // replace the projection with a Pi prompt using the same schema.
    const goal = String(context?.case_goal || context?.goal || "").slice(0, 500) || "定位根因";
    const missingFacts = Array.isArray(context?.missing_facts) ? context.missing_facts : [];
    const steps = [];
    if (missingFacts.length === 0) {
      steps.push({
        collector_id: "sys_metrics",
        purpose: "收集基础系统指标以建立证据基线",
        risk: "READ_LOW",
        priority: 50,
        status: "QUEUED",
        target_refs: [],
        hypothesis_refs: [],
        selection_strategy: null,
      });
    } else {
      for (const fact of missingFacts.slice(0, 5)) {
        steps.push({
          collector_id: "sys_metrics",
          purpose: "验证缺失事实：" + String(fact),
          risk: "READ_LOW",
          priority: 60,
          status: "QUEUED",
          target_refs: [],
          hypothesis_refs: [],
          selection_strategy: null,
        });
      }
    }
    return { goal, steps, source: "pi_shadow" };
  }

  async submitTurn(case_id, input) {
    const entry = this.sessions.get(case_id);
    if (!entry) throw new Error(`session not started for ${case_id}`);
    // A repeated evaluation round must not resend the complete conversation
    // history to the Provider.  Keep the durable Case/Evidence history in
    // Mini-Drop, but start the Pi message context afresh for this turn.  The
    // event sequence remains monotonic for the same runtime generation, so the
    // Server's idempotency/fencing contract is unchanged.
    const requestedFreshSession = input?.runtime_options?.fresh_session === true
      || resetSessionPerTurnEnabled();
    if (requestedFreshSession && entry.session) {
      try {
        if (!entry.session.isIdle && typeof entry.session.waitForIdle === "function") {
          await entry.session.waitForIdle();
        }
        if (!entry.session.isIdle) throw new Error("session_busy");
        if (entry.session.sessionManager && typeof entry.session.sessionManager.newSession === "function") {
          entry.session.sessionManager.newSession();
        }
        if (entry.session.agent?.state && Array.isArray(entry.session.agent.state.messages)) {
          entry.session.agent.state.messages = [];
        }
        entry.concluded = false;
        entry.runStopRequested = false;
        entry.pendingWakeup = "";
        entry.terminalReminderCount = 0;
        this.lastAnswers.delete(case_id);
        this.lastUsage.delete(case_id);
        this.messageStarts.delete(case_id);
      } catch (err) {
        // A reset is a bandwidth/scope optimization, not a reason to make the
        // turn unavailable.  Preserve the existing session if the SDK version
        // does not expose a mutable session manager.
        entry.lastError = `session_reset_skipped:${String(err?.message || err)}`;
      }
    }
    if (entry.session.isStreaming || Number(entry.session.pendingMessageCount || 0) > 0) {
      if (typeof entry.session.waitForIdle === "function") {
        await Promise.race([
          entry.session.waitForIdle(),
          new Promise((_, reject) => setTimeout(() => reject(new Error("turn_busy_timeout")), 30_000)),
        ]);
      }
      if (entry.session.isStreaming || Number(entry.session.pendingMessageCount || 0) > 0) {
        throw new Error("TURN_BUSY");
      }
    }
    const turnId = input.client_command_id
      ? `turn-${case_id}-${input.client_command_id}`
      : `turn-${case_id}-${Date.now()}`;
    this._activateTurn(entry, turnId);
    // Explicit user work may reopen analysis. Evidence wakeups use followUp and
    // therefore cannot reopen a conclusion by themselves.
    entry.concluded = false;
    entry.runStopRequested = false;
    entry.pendingWakeup = "";
    entry.terminalReminderCount = 0;
    if (input.runtime_policy) entry.context.runtime_policy = input.runtime_policy;
    if (input.runtime_options) entry.context.runtime_options = input.runtime_options;
    entry.toolEnvelope.current = this._toolEnvelope(entry.context, turnId);
    const shadow = input.shadow === true;
    if (!shadow) {
      // One Session subscribes exactly once; subsequent turns reuse the same
      // observer and therefore never double-forward events.
      this._ensureSubscribed(case_id, entry);
      const context = entry.context || {};
      const policy = context.side_effect_policy || "AUTO_READ_LOW";
      const contextPayload = {
        case_id,
        case_goal: context.case_goal || "",
        target_scope: context.target_scope || {},
        case_command_revision: context.case_command_revision || 1,
        control_revision: context.control_revision || 1,
        plan_revision: context.plan_revision || 0,
        scope_revision: context.scope_revision || 1,
        side_effect_policy: policy,
        evidence_watermark: context.evidence_watermark || 0,
        evidence_summary: (context.evidence_summary || []).slice(0, 12),
        hypotheses: (context.hypotheses || []).slice(0, 20),
        hypothesis_edges: (context.hypothesis_edges || []).slice(0, 40),
        evidence_gaps: (context.evidence_gaps || []).slice(0, 20),
        current_support: (context.current_support || []).slice(0, 20),
        counterevidence: (context.counterevidence || []).slice(0, 20),
        causal_graph: context.causal_graph || {},
        conclusion: context.conclusion || {},
        recommendations: (context.recommendations || []).slice(0, 20),
        evidence_analyses: (context.evidence_analyses || []).slice(-10),
        information_goals: context.information_goals || context.missing_facts || [],
        collection_proposals: (context.collection_proposals || []).slice(-12),
        collection_requests: (context.collection_requests || []).slice(-12),
        running_task_ids: context.running_task_ids || [],
        budget: context.budget || {},
        strategy_guidance: context.strategy_guidance || "",
        skills: context.skill_context || [],
        knowledge_retrieval: {
          mode: "search_knowledge_on_demand",
          scope: "current_case_and_tenant",
          content_injected: false,
        },
        directive: context.investigation_directive || {},
        runtime_policy: context.runtime_policy || {},
        runtime_options: context.runtime_options || {},
        previous_answer: String(this.lastAnswers.get(case_id) || "").slice(0, 2000),
        intervention: context.intervention || {},
      };
      const boundedPrompt = buildBoundedAgentPrompt({
        policy,
        contextPayload,
        userMessage: input.message,
      });
      if (entry.session.isStreaming && typeof entry.session.waitForIdle === "function") {
        await entry.session.waitForIdle();
      }
      void entry.session.prompt(boundedPrompt.prompt).catch((err) => {
        entry.lastError = String(err);
      });
    }
    return {
      turn_id: turnId,
      accepted: true,
      mode: shadow ? "pi_shadow" : "pi",
      detail: shadow ? "已接受 Shadow Turn，不会创建 Task" : "已提交到 Pi Runtime",
    };
  }

  async steer(case_id, instruction) {
    const entry = this.sessions.get(case_id);
    if (!entry) throw new Error(`session not started for ${case_id}`);
    await entry.session.steer(instruction.instruction);
    return { accepted: true };
  }

  async followUp(case_id, note) {
    const entry = this.sessions.get(case_id);
    if (!entry) throw new Error(`session not started for ${case_id}`);
    if (entry.concluded) {
      return { accepted: false, reason: "CONCLUDED" };
    }
    if (note?.intervention && typeof note.intervention === "object") {
      entry.context.intervention = note.intervention;
      entry.toolEnvelope.current = this._toolEnvelope(entry.context, entry.currentTurnId);
    }
    // A wakeup can be the first prompt after a Sidecar restart or an option-
    // driven session rebuild. Subscribe here as well as in submitTurn so the
    // new generation cannot run invisibly and lose its tool/final events.
    this._ensureSubscribed(case_id, entry);
    if (entry.runStopRequested && entry.session.isStreaming) {
      const coalesced = Boolean(entry.pendingWakeup);
      entry.pendingWakeup = note.note;
      if (!entry.wakeupWaitScheduled) {
        entry.wakeupWaitScheduled = true;
        void entry.session.waitForIdle().then(async () => {
          entry.wakeupWaitScheduled = false;
          const pending = entry.pendingWakeup;
          entry.pendingWakeup = "";
          if (!pending || entry.concluded) return;
          entry.runStopRequested = false;
          entry.terminalReminderCount = 0;
          this._activateTurn(entry, `turn-${case_id}-wakeup-${Date.now()}`);
          if (entry.toolEnvelope) {
            entry.toolEnvelope.current = this._toolEnvelope(entry.context, entry.currentTurnId);
          }
          await entry.session.prompt(pending);
        }).catch((err) => {
          entry.wakeupWaitScheduled = false;
          entry.lastError = String(err);
        });
      }
      return { accepted: true, coalesced, started: false, deferred: true };
    }
    if (entry.session.isStreaming && entry.session.pendingMessageCount > 0) {
      return { accepted: true, coalesced: true };
    }
    if (entry.session.isStreaming) {
      await entry.session.followUp(note.note);
      return { accepted: true, coalesced: false, started: false };
    }
    entry.runStopRequested = false;
    entry.terminalReminderCount = 0;
    this._activateTurn(entry, `turn-${case_id}-wakeup-${Date.now()}`);
    if (entry.toolEnvelope) {
      entry.toolEnvelope.current = this._toolEnvelope(entry.context, entry.currentTurnId);
    }
    void entry.session.prompt(note.note).catch((err) => {
      entry.lastError = String(err);
    });
    return { accepted: true, coalesced: false, started: true };
  }

  _ensureSubscribed(case_id, entry) {
    if (entry.subscribed || typeof entry.session?.subscribe !== "function") return;
    entry.subscribed = true;
    entry.session.subscribe((event) => {
      this._observe(case_id, event);
      void this._forwardEvent(case_id, entry, event);
      if (event?.type === "agent_settled") {
        this._scheduleTerminalReminder(case_id, entry);
      }
    });
  }

  _activateTurn(entry, turnId) {
    if (entry.activeTurnId && entry.activeTurnId !== turnId) {
      entry.fencedTurnIds.add(entry.activeTurnId);
    }
    entry.currentTurnId = turnId;
    entry.activeTurnId = turnId;
    entry.terminalReminderToken = Number(entry.terminalReminderToken || 0) + 1;
  }

  _markConcluded(case_id) {
    const entry = this.sessions.get(case_id);
    if (!entry) return;
    entry.concluded = true;
    entry.runStopRequested = true;
    entry.pendingWakeup = "";
    entry.session.clearQueue();
  }

  _markCollectionScheduled(case_id) {
    this._markAwaitingEvidence(case_id);
  }

  _markInterventionAck(case_id, ack) {
    const entry = this.sessions.get(case_id);
    if (!entry || !entry.context?.intervention) return;
    entry.context.intervention = {
      ...entry.context.intervention,
      ...ack,
      acknowledged: true,
    };
    if (entry.toolEnvelope) {
      entry.toolEnvelope.current = this._toolEnvelope(entry.context, entry.currentTurnId);
    }
  }

  _markDiscoveryCollecting(case_id) {
    this._markAwaitingEvidence(case_id);
  }

  _markAwaitingEvidence(case_id) {
    const entry = this.sessions.get(case_id);
    if (!entry) return;
    entry.runStopRequested = true;
    entry.session.clearQueue();
  }

  _scheduleTerminalReminder(case_id, entry) {
    const policy = entry.context?.side_effect_policy || "AUTO_READ_LOW";
    if (
      entry.concluded
      || entry.runStopRequested
      || policy === "READ_ONLY"
      || Number(entry.terminalReminderCount || 0) >= 1
    ) return;
    entry.terminalReminderCount = Number(entry.terminalReminderCount || 0) + 1;
    const reminderToken = Number(entry.terminalReminderToken || 0);
    setTimeout(() => {
      const current = this.sessions.get(case_id);
      if (
        current !== entry
        || current.concluded
        || current.runStopRequested
        || current.session.isStreaming
        || Number(current.terminalReminderToken || 0) !== reminderToken
      ) return;
      this._activateTurn(current, `turn-${case_id}-terminal-${Date.now()}`);
      void current.session.prompt(
        "The prior run settled without a verified terminal outcome. Do not restate a plain-text conclusion. " +
        "Use finish_investigation now with evidence-bound claims, or submit INSUFFICIENT_EVIDENCE through that tool.",
      ).catch((err) => {
        current.lastError = String(err);
      });
    }, 0);
  }

  async abort(case_id, reason) {
    const entry = this.sessions.get(case_id);
    if (!entry) return { aborted: false };
    entry.session.abort(reason);
    return { aborted: true };
  }

  state(case_id) {
    const entry = this.sessions.get(case_id);
    if (!entry) {
      return { case_id, status: "NOT_STARTED", runtime_generation: 0, last_event_seq: 0, runtime_version: `pi-${PI_RUNTIME_VERSION}`, detail: "" };
    }
    return {
      case_id,
      status: "READY",
      runtime_generation: entry.generation,
      last_event_seq: this.lastSeq.get(case_id) || 0,
      runtime_version: `pi-${PI_RUNTIME_VERSION}`,
      detail: entry.lastError || "",
    };
  }

  /** Observe events, dropping private thinking. */
  _observe(case_id, event) {
    if (!event || typeof event.type !== "string") return;
    if (event.type.startsWith("thinking")) return; // never keep private reasoning
    if (!PERSISTED_RUNTIME_EVENT_TYPES.has(event.type)) return;
    const seq = (this.lastSeq.get(case_id) || 0) + 1;
    this.lastSeq.set(case_id, seq);
    if (event.type === "message_start") {
      this.messageStarts.set(case_id, Date.now());
    }
    if (event.type === "tool_execution_start") {
      const id = event.toolCallId || event.tool_call_id || event.details?.tool_call_id
        || `${event.toolName || event.tool_name || "unknown"}:${seq}`;
      this.toolStarts.set(`${case_id}:${id}`, {
        startedMs: Date.now(),
        startedAt: new Date().toISOString(),
      });
    }
  }

  _auditProjection(event) {
    const payload = {};
    const project = (value) => {
      if (value === undefined || value === null) return "";
      try {
        return JSON.stringify(value).slice(0, 4000);
      } catch {
        return String(value).slice(0, 4000);
      }
    };
    const stripThinking = (value) => {
      if (Array.isArray(value)) {
        return value
          .filter((item) => item?.type !== "thinking")
          .map((item) => stripThinking(item));
      }
      if (value && typeof value === "object") {
        const copy = { ...value };
        delete copy.thinkingSignature;
        if (Array.isArray(copy.content)) {
          copy.content = stripThinking(copy.content);
        }
        return copy;
      }
      return value;
    };
    const assistantProjection = (value) => {
      let message = value;
      if (typeof value === "string") {
        try { message = JSON.parse(value); } catch { return null; }
      }
      if (!message || typeof message !== "object" || String(message.role || "") !== "assistant") {
        return null;
      }
      const content = Array.isArray(message.content) ? message.content : [];
      const toolItems = content.filter((item) => {
        const type = String(item?.type || "").toLowerCase();
        return type.includes("tool") && !type.includes("result");
      });
      const toolNames = toolItems
        .map((item) => item?.name || item?.toolName || item?.tool_name)
        .filter(Boolean);
      const visibleText = content
        .filter((item) => item?.type === "text" && item?.text)
        .map((item) => String(item.text).trim())
        .filter(Boolean)
        .join("\n");
      return {
        visible_text: toolItems.length === 0 ? visibleText.slice(0, 12000) : "",
        has_tool_calls: toolItems.length > 0,
        tool_names: [...new Set(toolNames)],
      };
    };
    if (event.text !== undefined) payload.text = project(stripThinking(event.text));
    if (event.toolCallId !== undefined) payload.tool_call_id = event.toolCallId;
    if (event.toolName !== undefined) payload.tool_name = event.toolName;
    if (event.details && typeof event.details === "object") {
      // Tool proxies provide a credential-free audit envelope. Never copy the
      // raw arguments or full result body into the runtime event.
      payload.tool_audit = { ...event.details };
      if (!payload.tool_call_id && event.details.tool_call_id) {
        payload.tool_call_id = event.details.tool_call_id;
      }
      if (!payload.tool_name && event.details.tool_name) {
        payload.tool_name = event.details.tool_name;
      }
    }
    const rawArguments = event.arguments ?? event.args ?? event.params ?? event.input;
    if (rawArguments !== undefined) {
      const canonicalArguments = JSON.stringify(canonicalizeAudit(rawArguments));
      payload.tool_audit = {
        ...(payload.tool_audit || {}),
        arguments_hash: createHash("sha256").update(canonicalArguments).digest("hex"),
      };
    }
    const rawResult = event.result ?? event.output ?? event.toolResult;
    if (rawResult !== undefined) {
      const resultText = typeof rawResult === "string" ? rawResult : JSON.stringify(rawResult);
      payload.tool_audit = {
        ...(payload.tool_audit || {}),
        result_hash: createHash("sha256").update(resultText).digest("hex"),
        result_bytes: Buffer.byteLength(resultText),
        result_truncated: Boolean(event.truncated || event.result_truncated),
      };
    }
    if (event.message !== undefined) payload.message = project(stripThinking(event.message));
    if (event.content !== undefined) payload.content = project(stripThinking(event.content));
    if (event.role !== undefined) payload.role = event.role;
    if (event.final === true || event.type === "final") payload.final = true;
    const assistant = assistantProjection(event.message ?? event.content);
    if (assistant) Object.assign(payload, assistant);
    return payload;
  }

  /** Capture one credential-free audit record per terminal assistant response. */
  _modelAttemptForEvent(case_id, entry, event) {
    if (!new Set(["message_end", "turn_end"]).has(event.type)) return null;

    let message = event.message ?? event.content;
    if (typeof message === "string") {
      try { message = JSON.parse(message); } catch { return null; }
    }
    if (!message || typeof message !== "object" || String(message.role || "").toLowerCase() !== "assistant") {
      return null;
    }
    const stopReason = String(
      message.stopReason ?? message.stop_reason ?? message.rawStopReason
      ?? event.stopReason ?? event.stop_reason ?? "",
    ).trim().toLowerCase();
    if (["pending", "streaming", "in_progress", "in-progress"].includes(stopReason)) return null;

    const model = entry.session?.model || {};
    const provider = String(
      message.provider || model.provider || process.env.MINI_DROP_PI_MODEL_PROVIDER || "deepseek",
    );
    const modelId = String(
      message.model || model.id || process.env.MINI_DROP_PI_MODEL || "deepseek-v4-flash",
    );
    const modelName = String(model.name || modelId);
    const responseId = String(
      message.responseId ?? message.response_id ?? event.responseId ?? event.response_id ?? "",
    ).trim();
    const responseMaterial = responseId
      ? JSON.stringify({ provider, model: modelId, response_id: responseId })
      : JSON.stringify({ provider, model: modelId, message });
    const responseHash = createHash("sha256").update(responseMaterial).digest("hex");
    const seen = this.recordedModelResponses.get(case_id) || new Set();
    if (seen.has(responseHash)) return null;
    seen.add(responseHash);
    this.recordedModelResponses.set(case_id, seen);

    let stats = null;
    if (typeof entry.session?.getSessionStats === "function") {
      try { stats = entry.session.getSessionStats(); } catch { stats = null; }
    }
    const previous = this.lastUsage.get(case_id);
    const tokens = stats?.tokens || {};
    const usage = message.usage && typeof message.usage === "object" ? message.usage : {};
    const positiveNumber = (value) => {
      const number = Number(value);
      return Number.isFinite(number) && number > 0 ? number : null;
    };
    const statDelta = (key) => Math.max(
      0,
      Number(tokens[key] || 0) - Number(previous?.tokens?.[key] || 0),
    ) || null;
    const input = positiveNumber(usage.input ?? usage.input_tokens) ?? statDelta("input");
    const output = positiveNumber(usage.output ?? usage.output_tokens) ?? statDelta("output");
    const cacheRead = positiveNumber(usage.cacheRead ?? usage.cache_read_tokens) ?? statDelta("cacheRead");
    const cacheWrite = positiveNumber(usage.cacheWrite ?? usage.cache_write_tokens) ?? statDelta("cacheWrite");
    const reportedCost = usage.cost && typeof usage.cost === "object" ? usage.cost.total : usage.cost;
    const cost = positiveNumber(reportedCost)
      ?? (Math.max(0, Number(stats?.cost || 0) - Number(previous?.cost || 0)) || null);
    if (stats) {
      this.lastUsage.set(case_id, { tokens: { ...tokens }, cost: stats.cost || 0 });
    }

    const startedMs = this.messageStarts.get(case_id);
    const finishedAt = new Date().toISOString();
    const startedAt = startedMs ? new Date(startedMs).toISOString() : finishedAt;
    const latencyMs = startedMs ? Math.max(0, Date.now() - startedMs) : 0;
    this.messageStarts.delete(case_id);

    const configFingerprint = createHash("sha256")
      .update(JSON.stringify({
        provider,
        model: modelId,
        thinkingLevel: entry.context?.runtime_options?.reasoning_effort
          || process.env.MINI_DROP_PI_THINKING_LEVEL || "high",
        strategy: entry.context?.diagnostic_strategy_id || "hybrid",
        options: entry.context?.runtime_options || {},
      }))
      .digest("hex");

    return {
      model_attempt_id: `model_attempt_pi_${responseHash.slice(0, 24)}`,
      provider,
      model: modelId,
      model_snapshot: modelName,
      prompt_version: "pi-runtime.v1",
      output_schema: "runtime-event.v1",
      status: "SUCCEEDED",
      latency_ms: latencyMs,
      input_tokens: input,
      output_tokens: output,
      cache_read_tokens: cacheRead,
      cache_write_tokens: cacheWrite,
      cost,
      retry_count: 0,
      turn_id: entry.currentTurnId || null,
      context_packet_id: entry.context?.context_packet_id || null,
      context_snapshot_id: entry.context?.context_snapshot_id || null,
      config_fingerprint: configFingerprint,
      tool_catalog_version: this.toolCatalog?.schema_version || "tool-catalog.v1",
      started_at: startedAt,
      finished_at: finishedAt,
      response_hash: responseHash,
      error_code: null,
    };
  }

  async _forwardEvent(case_id, entry, event) {
    if (!event || typeof event.type !== "string") return;
    if (event.type.startsWith("thinking")) return;
    // Persist only normalized lifecycle/tool events, never streaming deltas
    // and never private reasoning chunks.
    if (!PERSISTED_RUNTIME_EVENT_TYPES.has(event.type)) return;
    const token = process.env.MINI_DROP_PI_INTERNAL_TOKEN || "";
    if (!token) return; // fail closed: without auth no tool/event call is made
    const seq = this.lastSeq.get(case_id) || 0;
    if (seq <= 0) return;
    const idempotencyKey = `runtime-event:${case_id}:${entry.generation}:${seq}:${event.type}`;
    if (this.forwardedKeys.has(idempotencyKey)) return;
    const pendingRecord = this.eventSpool.get(idempotencyKey);
    if (pendingRecord) {
      await this._deliverSpoolRecord(pendingRecord, entry);
      return;
    }
    const projection = this._auditProjection(event);
    if (event.type === "tool_execution_end") {
      const id = event.toolCallId || event.tool_call_id || event.details?.tool_call_id;
      const timing = id ? this.toolStarts.get(`${case_id}:${id}`) : null;
      if (timing) {
        const finishedMs = Date.now();
        projection.tool_audit = {
          ...(projection.tool_audit || {}),
          started_at: projection.tool_audit?.started_at || timing.startedAt,
          finished_at: projection.tool_audit?.finished_at || new Date(finishedMs).toISOString(),
          duration_ms: projection.tool_audit?.duration_ms ?? Math.max(0, finishedMs - timing.startedMs),
        };
        this.toolStarts.delete(`${case_id}:${id}`);
      }
    }
    const modelAttempt = this._modelAttemptForEvent(case_id, entry, event);
    if (modelAttempt) projection.model_attempt = modelAttempt;
    const eventTurnId = event.turnId || event.turn_id || event.metadata?.turn_id || null;
    const foreignTurn = Boolean(eventTurnId && eventTurnId !== entry.activeTurnId);
    if (event.type === "turn_end") {
      if (projection.visible_text) this.lastAnswers.set(case_id, projection.visible_text);
    }
    projection.trigger_turn_id = eventTurnId || entry.currentTurnId || null;
    projection.foreign_turn_event = foreignTurn;
    projection.side_effect_policy = entry.context?.side_effect_policy || null;
    projection.context_snapshot_id = entry.context?.context_snapshot_id || null;
    projection.diagnostic_strategy_id = entry.context?.diagnostic_strategy_id || "hybrid";
    projection.runtime_policy = entry.context?.runtime_policy || {};
    projection.runtime_options = entry.context?.runtime_options || {};
    projection.case_revision = {
      case_command_revision: Number(entry.context?.case_command_revision || 1),
      control_revision: Number(entry.context?.control_revision || 1),
      scope_revision: Number(entry.context?.scope_revision || 1),
      plan_revision: Number(entry.context?.plan_revision || 0),
      evidence_watermark: Number(entry.context?.evidence_watermark || 0),
    };
    const record = {
      case_id,
      runtime_generation: entry.generation,
      event_id: `evt-${case_id}-${entry.generation}-${seq}`,
      event_seq: seq,
      event_type: event.type,
      payload: projection,
      idempotency_key: idempotencyKey,
    };
    this.eventSpool.append(record);
    await this._deliverSpoolRecord(record, entry);
  }

  async replayPending(caseId = null) {
    for (const record of this.eventSpool.pending(caseId)) {
      const entry = this.sessions.get(record.case_id);
      if (!entry) continue;
      await this._deliverSpoolRecord(record, entry);
    }
  }

  async _deliverSpoolRecord(record, entry) {
    try {
      const token = process.env.MINI_DROP_PI_INTERNAL_TOKEN || "";
      if (!token) return;
      const resp = await fetch(`${this.internalBase}/internal/runtime/v1/cases/${record.case_id}/events`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Internal-Token": token,
        },
        body: JSON.stringify({
          runtime_generation: record.runtime_generation,
          events: [record],
        }),
      });
      if (resp.ok) {
        this.eventSpool.ack(record.idempotency_key);
        this.forwardedKeys.add(record.idempotency_key);
      } else if (resp.status === 409) {
        // A rotated generation is permanently fenced and must never replay.
        this.eventSpool.ack(record.idempotency_key);
        this.forwardedKeys.add(record.idempotency_key);
        entry.lastError = "event fenced by newer runtime generation";
      } else {
        entry.lastError = `event forward failed: ${resp.status}`;
      }
    } catch (err) {
      entry.lastError = `event forward failed: ${String(err)}`;
    }
  }
}
