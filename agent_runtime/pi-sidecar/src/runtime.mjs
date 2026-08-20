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
    this.noSkills = noSkills;
    this.toolCatalog = null;
    this.eventSpool = eventSpool || new EventSpool(
      process.env.MINI_DROP_PI_EVENT_SPOOL_PATH || join(process.cwd(), "data", "pi-runtime-events.jsonl"),
    );
  }

  async ensureModelRuntime() {
    if (this.modelRuntime) return this.modelRuntime;
    const { ModelRuntime } = await import("@earendil-works/pi-coding-agent");
    // Constructed from allowlisted env vars only; never ~/.pi/auth.json.
    this.modelRuntime = await ModelRuntime.create({
      modelsPath: null,
      allowModelNetwork: false,
    });
    const provider = process.env.MINI_DROP_PI_MODEL_PROVIDER || "deepseek";
    const modelId = process.env.MINI_DROP_PI_MODEL || "deepseek-v4-flash";
    const apiKey = process.env.DEEPSEEK_API_KEY || process.env.MINI_DROP_AI_API_KEY || "";
    if (apiKey && typeof this.modelRuntime.setRuntimeApiKey === "function") {
      this.modelRuntime.setRuntimeApiKey(provider, apiKey);
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
        this.sessions.delete(caseId);
        this.lastSeq.delete(caseId);
        this.lastAnswers.delete(caseId);
        this.lastUsage.delete(caseId);
        this.messageStarts.delete(caseId);
      } else {
        existing.context = caseContext;
        existing.optionSignature = this._optionSignature(caseContext);
        existing.toolEnvelope.current = this._toolEnvelope(caseContext);
        const activeNames = buildToolCatalog({
          internalBase: this.internalBase,
          sideEffectPolicy: caseContext?.side_effect_policy || "AUTO_READ_LOW",
          catalog,
          runtimePolicy: caseContext?.runtime_policy,
          getEnvelope: () => existing.toolEnvelope.current,
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
    });
    const allowedNames = buildToolCatalog({
      internalBase: this.internalBase,
      sideEffectPolicy: caseContext?.side_effect_policy || "AUTO_READ_LOW",
      catalog,
      runtimePolicy: caseContext?.runtime_policy,
      getEnvelope: () => toolEnvelope.current,
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
      systemPrompt:
        "You are the Mini-Drop Evidence-native AI Collector Agent. Investigate " +
        "only from registered Case, Collector Catalog, Evidence and analysis state; " +
        "never use shell/file access. " +
        "Always read the Case Snapshot and existing Evidence before answering. " +
        "For READ_ONLY turns use only read-only tools and never request data " +
        "collection. If evidence is insufficient, report a precise Evidence " +
        "Gap and either propose one high-information Collector or abstain. Stop " +
        "when evidence is sufficient, budget is exhausted, scope/approval blocks " +
        "progress, or another collection would add no information. Never fabricate " +
        "evidence. Final claims must cite evidence_id, projection_hash and exact " +
        `field/span support. prompt_variant=${promptVariant}.`,
      resourceLoader: null,
    });
    this.sessions.set(caseId, {
      session,
      generation,
      context: caseContext,
      subscribed: false,
      currentTurnId: null,
      lastError: "",
      toolEnvelope,
      optionSignature: this._optionSignature(caseContext),
    });
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

  _toolEnvelope(context) {
    return {
      side_effect_policy: context?.side_effect_policy || "AUTO_READ_LOW",
      runtime_generation: Number(context?.runtime_generation) || 1,
      expected_control_revision: Number(context?.control_revision) || 1,
      expected_scope_revision: Number(context?.scope_revision) || 1,
      diagnostic_strategy_id: context?.diagnostic_strategy_id || "hybrid",
      strategy_params: context?.strategy_params || {},
      runtime_policy: context?.runtime_policy || {},
      runtime_options: context?.runtime_options || {},
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
    const turnId = input.client_command_id
      ? `turn-${case_id}-${input.client_command_id}`
      : `turn-${case_id}-${Date.now()}`;
    entry.currentTurnId = turnId;
    if (input.runtime_policy) entry.context.runtime_policy = input.runtime_policy;
    if (input.runtime_options) entry.context.runtime_options = input.runtime_options;
    entry.toolEnvelope.current = this._toolEnvelope(entry.context);
    const shadow = input.shadow === true;
    if (!shadow) {
      // One Session subscribes exactly once; subsequent turns reuse the same
      // observer and therefore never double-forward events.
      if (!entry.subscribed) {
        entry.subscribed = true;
        entry.session.subscribe((event) => {
          this._observe(case_id, event);
          void this._forwardEvent(case_id, entry, event);
        });
      }
      const context = entry.context || {};
      const policy = context.side_effect_policy || "AUTO_READ_LOW";
      const contextBlock = JSON.stringify({
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
        evidence_analyses: (context.evidence_analyses || []).slice(-10),
        information_goals: context.information_goals || context.missing_facts || [],
        collection_proposals: (context.collection_proposals || []).slice(-12),
        collection_requests: (context.collection_requests || []).slice(-12),
        running_task_ids: context.running_task_ids || [],
        budget: context.budget || {},
        directive: context.investigation_directive || {},
        runtime_policy: context.runtime_policy || {},
        runtime_options: context.runtime_options || {},
        previous_answer: String(this.lastAnswers.get(case_id) || "").slice(0, 2000),
      }, null, 2);
      void entry.session.prompt(
        `[Policy Context]\n` +
        `side_effect_policy=${policy}. ` +
        (policy === "READ_ONLY"
          ? "This is a READ_ONLY turn: use only read-only tools. Never request data collection, plan execution or any mutation."
          : "Choose the next information goal and action from observed Evidence and the live Collector Catalog. Do not follow a fixed collector order.") +
        `\n\n[CaseContext]\n${contextBlock}\n\n[User]\n${input.message}`,
      ).catch((err) => {
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
    await entry.session.followUp(note.note);
    return { accepted: true };
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
    const seq = (this.lastSeq.get(case_id) || 0) + 1;
    this.lastSeq.set(case_id, seq);
    if (event.type === "message_start") {
      this.messageStarts.set(case_id, Date.now());
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
    if (event.text !== undefined) payload.text = project(stripThinking(event.text));
    if (event.toolCallId !== undefined) payload.tool_call_id = event.toolCallId;
    if (event.toolName !== undefined) payload.tool_name = event.toolName;
    if (event.message !== undefined) payload.message = project(stripThinking(event.message));
    if (event.content !== undefined) payload.content = project(stripThinking(event.content));
    if (event.role !== undefined) payload.role = event.role;
    if (event.final === true || event.type === "final") payload.final = true;
    return payload;
  }

  /**
   * Capture auditable token/cost usage for a completed model response.
   *
   * The Pi SDK exposes cumulative session stats; per-call usage is derived as
   * the delta between the previous snapshot and the snapshot after message_end.
   * No private reasoning or raw credentials are included.
   */
  _modelAttemptForEvent(case_id, entry, event) {
    if (event.type !== "message_end") return null;
    if (typeof entry.session?.getSessionStats !== "function") return null;
    let stats;
    try {
      stats = entry.session.getSessionStats();
    } catch {
      return null;
    }
    const previous = this.lastUsage.get(case_id);
    const tokens = stats.tokens || {};
    const input = Math.max(0, (tokens.input || 0) - (previous?.tokens?.input || 0));
    const output = Math.max(0, (tokens.output || 0) - (previous?.tokens?.output || 0));
    const cacheRead = Math.max(0, (tokens.cacheRead || 0) - (previous?.tokens?.cacheRead || 0));
    const cacheWrite = Math.max(0, (tokens.cacheWrite || 0) - (previous?.tokens?.cacheWrite || 0));
    const cost = Math.max(0, (stats.cost || 0) - (previous?.cost || 0));
    this.lastUsage.set(case_id, { tokens: { ...tokens }, cost: stats.cost || 0 });

    const startedMs = this.messageStarts.get(case_id);
    const finishedAt = new Date().toISOString();
    const startedAt = startedMs ? new Date(startedMs).toISOString() : finishedAt;
    const latencyMs = startedMs ? Math.max(0, Date.now() - startedMs) : 0;
    this.messageStarts.delete(case_id);

    const model = entry.session.model;
    const provider = model?.provider || process.env.MINI_DROP_PI_MODEL_PROVIDER || "deepseek";
    const modelId = model?.id || process.env.MINI_DROP_PI_MODEL || "deepseek-v4-flash";
    const modelName = model?.name || modelId;
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
      provider,
      model: modelId,
      model_snapshot: modelName,
      prompt_version: "pi-runtime.v1",
      output_schema: "runtime-event.v1",
      status: "SUCCEEDED",
      latency_ms: latencyMs,
      input_tokens: input || null,
      output_tokens: output || null,
      cache_read_tokens: cacheRead || null,
      cache_write_tokens: cacheWrite || null,
      cost: cost || null,
      retry_count: 0,
      turn_id: entry.currentTurnId || null,
      context_packet_id: entry.context?.context_packet_id || null,
      context_snapshot_id: entry.context?.context_snapshot_id || null,
      config_fingerprint: configFingerprint,
      tool_catalog_version: this.toolCatalog?.schema_version || "tool-catalog.v1",
      started_at: startedAt,
      finished_at: finishedAt,
      response_hash: null,
      error_code: null,
    };
  }

  async _forwardEvent(case_id, entry, event) {
    if (!event || typeof event.type !== "string") return;
    if (event.type.startsWith("thinking")) return;
    // Persist only normalized lifecycle/tool events, never streaming deltas
    // and never private reasoning chunks.
    const persistedTypes = new Set([
      "message_start", "message_end", "tool_execution_start", "tool_execution_end",
      "turn_start", "turn_end", "agent_start", "agent_end", "agent_settled",
    ]);
    if (!persistedTypes.has(event.type)) return;
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
    const modelAttempt = this._modelAttemptForEvent(case_id, entry, event);
    if (modelAttempt) projection.model_attempt = modelAttempt;
    if (event.type === "turn_end") {
      try {
        const message = JSON.parse(projection.message || "{}");
        const text = (message.content || [])
          .filter((item) => item?.type === "text" && item?.text)
          .map((item) => item.text)
          .join(" ");
        if (text) this.lastAnswers.set(case_id, text);
      } catch {
        // malformed message is still persisted below; just no answer cache
      }
    }
    projection.trigger_turn_id = entry.currentTurnId || null;
    projection.side_effect_policy = entry.context?.side_effect_policy || null;
    projection.context_snapshot_id = entry.context?.context_snapshot_id || null;
    projection.diagnostic_strategy_id = entry.context?.diagnostic_strategy_id || "hybrid";
    projection.runtime_policy = entry.context?.runtime_policy || {};
    projection.runtime_options = entry.context?.runtime_options || {};
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
