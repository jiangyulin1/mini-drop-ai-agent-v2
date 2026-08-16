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
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { buildToolCatalog } from "./tools.mjs";
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
    this.noSkills = noSkills;
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
    const existing = this.sessions.get(caseId);
    if (existing) {
      // v6 7.1: a Cycle never reuses a stale context.  Refresh the binding
      // with the new Snapshot; if Server rotated generation, discard the old
      // in-memory Pi session and rebuild from the latest Snapshot.
      if (incomingGeneration > existing.generation) {
        this.sessions.delete(caseId);
        this.lastSeq.delete(caseId);
        this.lastAnswers.delete(caseId);
      } else {
        existing.context = caseContext;
        existing.toolEnvelope.current = this._toolEnvelope(caseContext);
        const activeNames = buildToolCatalog({
          internalBase: this.internalBase,
          sideEffectPolicy: caseContext?.side_effect_policy || "AUTO_READ_LOW",
          getEnvelope: () => existing.toolEnvelope.current,
        }).map((tool) => tool.name);
        existing.session.setActiveToolsByName(activeNames);
        await this.replayPending(caseId);
        return this._binding(caseId);
      }
    }
    await this.ensureModelRuntime();
    const generation = incomingGeneration;
    const toolEnvelope = { current: this._toolEnvelope(caseContext) };
    const tools = buildToolCatalog({
      internalBase: this.internalBase,
      sideEffectPolicy: "AUTO_READ_LOW",
      getEnvelope: () => toolEnvelope.current,
    });
    const allowedNames = buildToolCatalog({
      internalBase: this.internalBase,
      sideEffectPolicy: caseContext?.side_effect_policy || "AUTO_READ_LOW",
      getEnvelope: () => toolEnvelope.current,
    }).map((tool) => tool.name);
    const thinkingLevel = process.env.MINI_DROP_PI_THINKING_LEVEL || "high";
    const { session } = await createAgentSession({
      model: this.selectedModel || this.modelRuntime,
      thinkingLevel,
      // Security: built-in shell/file tools are disabled; ONLY Mini-Drop
      // allowlisted custom tools remain available.
      noTools: "builtin",
      tools: allowedNames,
      customTools: tools,
      sessionManager: SessionManager.inMemory(),
      systemPrompt:
        "You are the Mini-Drop AI Investigator. You must investigate from " +
        "registered Case/Evidence/Plan state, never from shell/file access. " +
        "Always read the Case Snapshot and existing Evidence before answering. " +
        "For READ_ONLY turns use only read-only tools and never request data " +
        "collection. If evidence is insufficient, report a precise Evidence " +
        "Gap and abstain instead of offering multiple speculative directions. " +
        "Never fabricate evidence. Final answers must be concise and cite " +
        "evidence_id and projection_hash when evidence is used.",
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
    });
    this.lastSeq.set(caseId, 0);
    await this.replayPending(caseId);
    return this._binding(caseId);
  }

  _toolEnvelope(context) {
    return {
      side_effect_policy: context?.side_effect_policy || "AUTO_READ_LOW",
      runtime_generation: Number(context?.runtime_generation) || 1,
      expected_control_revision: Number(context?.control_revision) || 1,
      expected_scope_revision: Number(context?.scope_revision) || 1,
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
        missing_facts: context.missing_facts || [],
        skills: (context.skill_context || []).slice(0, 3),
        knowledge: (context.knowledge_context || []).slice(0, 3),
        directive: context.investigation_directive || {},
        previous_answer: String(this.lastAnswers.get(case_id) || "").slice(0, 2000),
      }, null, 2);
      void entry.session.prompt(
        `[Policy Context]\n` +
        `side_effect_policy=${policy}. ` +
        (policy === "READ_ONLY"
          ? "This is a READ_ONLY turn: use only read-only tools. Never request data collection, plan execution or any mutation."
          : "Choose the next action from observed Evidence/Gap/Skill and registered Operations. Do not follow a fixed collector order.") +
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
