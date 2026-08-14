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
import { buildToolCatalog } from "./tools.mjs";

export class RuntimeManager {
  constructor({ modelRuntime, internalBase, noSkills = true } = {}) {
    this.modelRuntime = modelRuntime;
    this.internalBase = internalBase || "http://127.0.0.1:8191";
    this.sessions = new Map(); // case_id -> {session, generation, context}
    this.lastSeq = new Map(); // case_id -> event seq
    this.noSkills = noSkills;
  }

  async ensureModelRuntime() {
    if (this.modelRuntime) return this.modelRuntime;
    const { ModelRuntime } = await import("@earendil-works/pi-coding-agent");
    // Constructed from allowlisted env vars only; never ~/.pi/auth.json.
    this.modelRuntime = await ModelRuntime.create();
    return this.modelRuntime;
  }

  async startOrResume(caseContext) {
    const existing = this.sessions.get(caseContext.case_id);
    if (existing) {
      return this._binding(caseContext.case_id);
    }
    const generation = (existing?.generation || 0) + 1;
    await this.ensureModelRuntime();
    const tools = buildToolCatalog({ internalBase: this.internalBase });
    const { session } = await createAgentSession({
      model: this.modelRuntime,
      // Security: no built-in tools, no extension/skill discovery.
      noTools: "all",
      customTools: tools,
      sessionManager: SessionManager.inMemory(),
      systemPrompt:
        "You are the Mini-Drop AI Investigator. You plan and reason over " +
        "evidence; you never run shell/file commands. Only use the provided " +
        "Mini-Drop tools. Never fabricate evidence IDs.",
      resourceLoader: null,
    });
    this.sessions.set(caseContext.case_id, {
      session, generation, context: caseContext,
    });
    this.lastSeq.set(caseContext.case_id, 0);
    return this._binding(caseContext.case_id);
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
      runtime_version: "pi-0.84.0",
      runtime_session_id: case_id,
      runtime_generation: entry.generation,
      status: "READY",
      last_event_seq: this.lastSeq.get(case_id) || 0,
      last_context_snapshot_id: entry.context?.context_snapshot_id || null,
      lease_owner: "mini-drop-pi-sidecar",
    };
  }

  async submitTurn(case_id, input) {
    const entry = this.sessions.get(case_id);
    if (!entry) throw new Error(`session not started for ${case_id}`);
    // prompt is async-fire; capture events into lastSeq.
    entry.session.subscribe((event) => this._observe(case_id, event));
    const turnId = `turn-${case_id}-${Date.now()}`;
    void entry.session.prompt(input.message).catch((err) => {
      entry.lastError = String(err);
    });
    return {
      turn_id: turnId,
      accepted: true,
      mode: "pi",
      detail: "已提交到 Pi Runtime",
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
      return { case_id, status: "NOT_STARTED", runtime_generation: 0, last_event_seq: 0, runtime_version: "pi-0.84.0", detail: "" };
    }
    return {
      case_id,
      status: "READY",
      runtime_generation: entry.generation,
      last_event_seq: this.lastSeq.get(case_id) || 0,
      runtime_version: "pi-0.84.0",
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
}
