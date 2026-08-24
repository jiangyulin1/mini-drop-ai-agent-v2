/**
 * E3 sidecar tests: internal protocol surface and tool gating.
 * Run with: node --test test/
 */

import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { createServer, sidecarListenOptions } from "../src/server.mjs";
import {
  boundCaseContextPayload,
  buildBoundedAgentPrompt,
  buildEvidenceAgentSystemPrompt,
  modelRuntimeOptionsFromEnvironment,
  RuntimeManager,
} from "../src/runtime.mjs";
import { EventSpool } from "../src/event-spool.mjs";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { buildToolCatalog, fetchToolCatalog, ALLOWED_TOOL_NAMES } from "../src/tools.mjs";

let server;
let base;

before(async () => {
  server = await createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  base = `http://127.0.0.1:${server.address().port}`;
});

after(() => server.close());

test("health reports ready and model gating", async () => {
  const resp = await fetch(`${base}/internal/runtime/v1/health`);
  const body = await resp.json();
  assert.equal(body.ok, true);
  assert.equal(body.data.runtime_type, "pi");
  assert.equal(typeof body.data.model_ready, "boolean");
});

test("sidecar listen host is opt-in and preserves the historic default", () => {
  assert.deepEqual(sidecarListenOptions({}), { port: 8899 });
  assert.deepEqual(
    sidecarListenOptions({
      MINI_DROP_PI_SIDECAR_PORT: "9900",
      MINI_DROP_PI_SIDECAR_HOST: "127.0.0.1",
    }),
    { port: 9900, host: "127.0.0.1" },
  );
});

test("unknown internal route returns 404", async () => {
  const resp = await fetch(`${base}/internal/runtime/v1/cases/x/nope/extra`);
  assert.equal(resp.status, 404);
});

test("state for unknown case is NOT_STARTED", async () => {
  const resp = await fetch(`${base}/internal/runtime/v1/cases/unknown-case/state`);
  const body = await resp.json();
  assert.equal(body.data.status, "NOT_STARTED");
});

test("tool catalog exposes ONLY allowlisted read tools", () => {
  const tools = buildToolCatalog({ internalBase: "http://127.0.0.1:1" });
  const names = tools.map((t) => t.name);
  // no shell/file tools
  for (const forbidden of ["bash", "read", "write", "edit", "grep", "find", "ls"]) {
    assert.ok(!names.includes(forbidden), `forbidden tool present: ${forbidden}`);
  }
  assert.deepEqual(names.sort(), [...ALLOWED_TOOL_NAMES].sort());
  // every tool carries a parameter schema
  for (const tool of tools) {
    assert.ok(tool.parameters, `${tool.name} missing parameters schema`);
  }
});

test("no-tools session surface does not expose raw RPC", async () => {
  const resp = await fetch(`${base}/raw/rpc`, { method: "POST" });
  assert.equal(resp.status, 404);
});

test("shadow-plan route returns structured plan without creating task", async () => {
  const resp = await fetch(`${base}/internal/runtime/v1/cases/shadow-case/shadow-plan`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      context: {
        case_id: "shadow-case",
        case_goal: "定位支付超时",
        missing_facts: ["数据库连接池状态", "GC 频率"],
      },
    }),
  });
  assert.equal(resp.status, 200);
  const body = await resp.json();
  assert.equal(body.ok, true);
  assert.equal(body.data.source, "pi_shadow");
  assert.ok(Array.isArray(body.data.steps));
  assert.ok(body.data.steps.length >= 1);
  assert.ok(body.data.steps.every((step) => step.collector_id));
});

test("collection proposal schema includes goal, target and revision fences", () => {
  const tools = buildToolCatalog({ internalBase: "http://127.0.0.1:1" });
  const proposalTool = tools.find((t) => t.name === "propose_collection");
  assert.ok(proposalTool);
  const props = proposalTool.parameters.properties;
  assert.ok(props.collector_id);
  assert.ok(props.target_selector);
  assert.ok(props.parameters);
  assert.ok(props.information_goal);
  assert.ok(props.expected_control_revision);
  assert.ok(props.expected_scope_revision);
});

test("topology discovery fallback exposes the bounded start/advance contract", () => {
  const oldCatalog = {
    schema_version: "tool-catalog.v1",
    tools: ALLOWED_TOOL_NAMES
      .filter((name) => name !== "discover_topology")
      .map((name) => ({
        name,
        description: `old canonical ${name}`,
        parameters: { type: "object", additionalProperties: false },
        internal_path: `/internal/agent/tools/${name}`,
        enabled_by_default: true,
      })),
  };
  const tools = buildToolCatalog({ internalBase: "http://control", catalog: oldCatalog });
  const discovery = tools.find((tool) => tool.name === "discover_topology");
  assert.ok(discovery);
  assert.match(discovery.description, /Start or advance bounded Case-scoped/);
  assert.deepEqual(discovery.parameters.required, ["case_id"]);
  const props = discovery.parameters.properties;
  assert.equal(props.seed_pid.minimum, 1);
  assert.equal(props.run_id.pattern, "^discovery-[0-9a-f]{20}$");
  assert.match(props.run_id.description, /omit on first call/);
  assert.equal(props.max_hops.maximum, 4);
  assert.equal(props.max_parallel_tasks.maximum, 8);
  assert.equal(props.wait_timeout_sec.maximum, 45);
});

test("investigative system prompt requires a verified terminal tool outcome", () => {
  const prompt = buildEvidenceAgentSystemPrompt("eval");
  assert.match(prompt, /must be submitted through finish_investigation/);
  assert.match(prompt, /Never substitute a plain-text final or stage conclusion/);
  assert.match(prompt, /COLLECTING means end this run/);
  assert.match(prompt, /COMPLETED or PARTIAL discovery is not an investigation conclusion/);
  assert.match(prompt, /do not call propose_causal_graph/);
  assert.match(prompt, /prompt_variant=eval/);
});

test("bounded prompt keeps evidence identity fields under the configured budget", () => {
  const payload = {
    case_id: "case-budget",
    case_goal: "PR attribution",
    evidence_summary: [
      { evidence_id: "ev-1", projection_hash: "hash-1", summary: "first " + "x".repeat(5000) },
      { evidence_id: "ev-2", projection_hash: "hash-2", summary: "second " + "y".repeat(5000) },
    ],
    hypotheses: [{ statement: "h".repeat(5000) }],
    causal_graph: { noisy: "z".repeat(5000) },
  };
  const bounded = boundCaseContextPayload(payload, 1800);
  assert.ok(JSON.stringify(bounded).length <= bounded._context_meta.max_chars);
  assert.equal(bounded.evidence_summary[0].evidence_id, "ev-1");
  assert.equal(bounded.evidence_summary[0].projection_hash, "hash-1");
  assert.ok("summary" in bounded.evidence_summary[0]);
  const prompt = buildBoundedAgentPrompt({ contextPayload: payload, userMessage: "继续", maxChars: 2048 });
  assert.ok(prompt.prompt.length <= 2048);
  assert.doesNotThrow(() => JSON.parse(prompt.contextBlock));
  assert.match(prompt.prompt, /ev-1/);
  assert.match(prompt.prompt, /hash-1/);
});

test("context budget reads MINI_DROP_PI_CONTEXT_MAX_CHARS", () => {
  const original = process.env.MINI_DROP_PI_CONTEXT_MAX_CHARS;
  process.env.MINI_DROP_PI_CONTEXT_MAX_CHARS = "800";
  try {
    const prompt = buildBoundedAgentPrompt({
      contextPayload: { case_id: "case-env", evidence_summary: [] },
      userMessage: "short",
    });
    assert.equal(prompt.maxChars, 800);
    assert.ok(prompt.prompt.length <= 800);
  } finally {
    if (original === undefined) delete process.env.MINI_DROP_PI_CONTEXT_MAX_CHARS;
    else process.env.MINI_DROP_PI_CONTEXT_MAX_CHARS = original;
  }
});

test("fresh evaluation turns reset Pi conversation history without resetting event sequence", async () => {
  const calls = [];
  const session = {
    isIdle: true,
    sessionManager: { newSession: () => calls.push("new_session") },
    agent: { state: { messages: [{ role: "user", content: "old round" }] } },
    subscribe: () => { calls.push("subscribe"); return () => {}; },
    setActiveToolsByName: () => {},
    clearQueue: () => {},
    prompt: (text) => { calls.push(["prompt", text.length]); return Promise.resolve(); },
    isStreaming: false,
  };
  const manager = new RuntimeManager({ modelRuntime: {}, eventSpool: new EventSpool(null) });
  manager.sessions.set("fresh-case", {
    session,
    generation: 3,
    context: { case_id: "fresh-case", side_effect_policy: "READ_ONLY", runtime_options: {} },
    subscribed: false,
    currentTurnId: null,
    toolEnvelope: { current: {} },
    optionSignature: "",
    concluded: false,
    runStopRequested: false,
    pendingWakeup: "",
    terminalReminderCount: 0,
  });
  manager.lastSeq.set("fresh-case", 17);
  await manager.submitTurn("fresh-case", {
    message: "new round",
    runtime_options: { fresh_session: true },
  });
  assert.deepEqual(calls[0], "new_session");
  assert.equal(session.agent.state.messages.length, 0);
  assert.equal(manager.lastSeq.get("fresh-case"), 17);
  assert.equal(calls.at(-1)[0], "prompt");
});

test("tool envelope preserves the active investigation branch", () => {
  const manager = new RuntimeManager({ modelRuntime: {}, eventSpool: new EventSpool(null) });
  const envelope = manager._toolEnvelope({
    case_id: "case-branch",
    branch_id: "branch-a",
    runtime_generation: 4,
  });
  assert.equal(envelope.case_id, "case-branch");
  assert.equal(envelope.branch_id, "branch-a");
});

test("models path is opt-in and PI_OFFLINE does not disable provider turns", () => {
  const originalPath = process.env.MINI_DROP_PI_MODELS_PATH;
  const originalOffline = process.env.PI_OFFLINE;
  process.env.MINI_DROP_PI_MODELS_PATH = "/tmp/mini-drop-models.json";
  process.env.PI_OFFLINE = "1";
  try {
    const options = modelRuntimeOptionsFromEnvironment();
    assert.equal(options.modelsPath, "/tmp/mini-drop-models.json");
    assert.equal(options.allowModelNetwork, false);
    // PI_OFFLINE only affects catalog refresh inside the SDK; the sidecar still
    // creates a selected model and submits real turns when credentials exist.
    assert.equal(Object.hasOwn(options, "provider"), false);
  } finally {
    if (originalPath === undefined) delete process.env.MINI_DROP_PI_MODELS_PATH;
    else process.env.MINI_DROP_PI_MODELS_PATH = originalPath;
    if (originalOffline === undefined) delete process.env.PI_OFFLINE;
    else process.env.PI_OFFLINE = originalOffline;
  }
});

test("finish tool has a closed state enum and terminates an accepted run", async () => {
  let acceptedFinish = null;
  const tools = buildToolCatalog({
    internalBase: "http://127.0.0.1:1",
    sideEffectPolicy: "PROPOSE_ONLY",
    onAcceptedFinish: (result) => { acceptedFinish = result; },
  });
  const finish = tools.find((tool) => tool.name === "finish_investigation");
  assert.ok(finish);
  const stateSchema = finish.parameters.properties.state;
  assert.ok(JSON.stringify(stateSchema).includes("CONFIRMED"));
  assert.ok(!JSON.stringify(stateSchema).includes("CONCLUDED"));

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ ok: true, data: { accepted: true } }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
  try {
    const result = await finish.execute("finish-call", {
      case_id: "case-a", summary: "done", evidence_ids: ["ev-1"],
    });
    assert.equal(result.terminate, true);
    assert.equal(acceptedFinish.accepted, true);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rejected finish does not terminate the run", async () => {
  let acceptedFinish = null;
  const tools = buildToolCatalog({
    internalBase: "http://127.0.0.1:1",
    onAcceptedFinish: (result) => { acceptedFinish = result; },
  });
  const finish = tools.find((tool) => tool.name === "finish_investigation");
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({
    ok: true, data: { accepted: false, reason: "VERIFIER_REJECTED" },
  }), { status: 200, headers: { "Content-Type": "application/json" } });
  try {
    const result = await finish.execute("finish-rejected", {
      case_id: "case-a", summary: "unsupported", evidence_ids: [],
    });
    assert.equal(result.terminate, undefined);
    assert.equal(acceptedFinish, null);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("accepted collection terminates the current run instead of model polling", async () => {
  let scheduled = null;
  const tools = buildToolCatalog({
    internalBase: "http://127.0.0.1:1",
    onCollectionScheduled: (result) => { scheduled = result; },
  });
  const proposal = tools.find((tool) => tool.name === "propose_collection");
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({
    ok: true,
    data: {
      collection_request: { status: "DISPATCHED" },
      task: { id: "task-a" },
    },
  }), { status: 200, headers: { "Content-Type": "application/json" } });
  try {
    const result = await proposal.execute("collection-call", {
      case_id: "case-a",
      collector_id: "sys_metrics",
      target_selector: { agent_id: "agent-a" },
      parameters: {},
      information_goal: "CPU utilization over time",
    });
    assert.equal(result.terminate, true);
    assert.equal(scheduled.task.id, "task-a");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("topology discovery terminates only while collecting Evidence", async () => {
  let status = "PROPOSED";
  const collectingResults = [];
  const tools = buildToolCatalog({
    internalBase: "http://127.0.0.1:1",
    onDiscoveryCollecting: (result) => { collectingResults.push(result); },
  });
  const discovery = tools.find((tool) => tool.name === "discover_topology");
  assert.ok(discovery);
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({
    ok: true,
    data: { status, run_id: "discovery-a" },
  }), { status: 200, headers: { "Content-Type": "application/json" } });
  try {
    for (const nonTerminalStatus of ["PROPOSED", "PENDING", "PARTIAL", "COMPLETED"]) {
      status = nonTerminalStatus;
      const result = await discovery.execute(`discovery-${nonTerminalStatus}`, {
        case_id: "case-a",
      });
      assert.equal(result.terminate, undefined, `${nonTerminalStatus} must remain non-terminal`);
    }
    assert.equal(collectingResults.length, 0);

    status = "collecting";
    const collecting = await discovery.execute("discovery-collecting", {
      case_id: "case-a",
      run_id: "discovery-a",
    });
    assert.equal(collecting.terminate, true);
    assert.equal(collectingResults.length, 1);
    assert.equal(collectingResults[0].status, "collecting");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("finish and causal graph schemas expose required structured fields", () => {
  const tools = buildToolCatalog({ internalBase: "http://127.0.0.1:1" });
  const finish = tools.find((tool) => tool.name === "finish_investigation");
  const recommendation = finish.parameters.properties.recommendations.items;
  assert.deepEqual(
    new Set(recommendation.required),
    new Set(["cause_or_edge_ref", "target", "concrete_action"]),
  );
  const graph = tools.find((tool) => tool.name === "propose_causal_graph");
  assert.ok(graph.parameters.properties.nodes.items.properties.node_id);
  assert.ok(graph.parameters.properties.edges.items.properties.source_node_id);
  const plan = tools.find((tool) => tool.name === "propose_plan_revision");
  const step = plan.parameters.properties.steps.items;
  assert.ok(step.properties.collector_id);
  assert.ok(step.properties.purpose);
  assert.deepEqual(new Set(step.required), new Set(["collector_id", "purpose"]));
});

test("runtime coalesces queued follow-ups and rejects them after conclusion", async () => {
  const manager = new RuntimeManager({ modelRuntime: {} });
  const calls = [];
  const fakeSession = {
    isStreaming: true,
    pendingMessageCount: 0,
    followUp: async (note) => { calls.push(note); fakeSession.pendingMessageCount = 1; },
    clearQueue: () => { fakeSession.pendingMessageCount = 0; calls.push("cleared"); },
  };
  manager.sessions.set("case-coalesce", { session: fakeSession, concluded: false });

  assert.deepEqual(await manager.followUp("case-coalesce", { note: "evidence-1" }), {
    accepted: true, coalesced: false, started: false,
  });
  assert.deepEqual(await manager.followUp("case-coalesce", { note: "evidence-2" }), {
    accepted: true, coalesced: true,
  });
  assert.deepEqual(calls, ["evidence-1"]);

  manager._markConcluded("case-coalesce");
  assert.deepEqual(await manager.followUp("case-coalesce", { note: "late" }), {
    accepted: false, reason: "CONCLUDED",
  });
  assert.deepEqual(calls, ["evidence-1", "cleared"]);
});

test("runtime starts a fresh prompt when evidence arrives after the agent is idle", async () => {
  const manager = new RuntimeManager({ modelRuntime: {} });
  const calls = [];
  const fakeSession = {
    isStreaming: false,
    pendingMessageCount: 0,
    subscribe: () => { calls.push("subscribed"); },
    prompt: async (note) => { calls.push(note); },
  };
  manager.sessions.set("case-idle", {
    session: fakeSession,
    concluded: false,
    lastError: "",
  });

  assert.deepEqual(await manager.followUp("case-idle", { note: "new evidence" }), {
    accepted: true, coalesced: false, started: true,
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, ["subscribed", "new evidence"]);
  assert.match(manager.sessions.get("case-idle").currentTurnId, /-wakeup-/);
});

test("runtime defers a racing evidence wakeup until the scheduled collection run stops", async () => {
  const manager = new RuntimeManager({ modelRuntime: {} });
  const calls = [];
  let resolveIdle;
  const fakeSession = {
    isStreaming: true,
    pendingMessageCount: 0,
    clearQueue: () => { calls.push("cleared"); },
    waitForIdle: () => new Promise((resolve) => { resolveIdle = resolve; }),
    prompt: async (note) => { calls.push(note); },
  };
  manager.sessions.set("case-race", {
    session: fakeSession,
    concluded: false,
    runStopRequested: false,
    pendingWakeup: "",
    wakeupWaitScheduled: false,
    lastError: "",
  });

  manager._markCollectionScheduled("case-race");
  assert.equal(manager.sessions.get("case-race").runStopRequested, true);
  assert.deepEqual(await manager.followUp("case-race", { note: "durable evidence" }), {
    accepted: true, coalesced: false, started: false, deferred: true,
  });
  fakeSession.isStreaming = false;
  resolveIdle();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(calls, ["cleared", "durable evidence"]);
  assert.equal(manager.sessions.get("case-race").runStopRequested, false);
});

test("runtime issues at most one terminal reminder after an unverified settle", async () => {
  const manager = new RuntimeManager({ modelRuntime: {} });
  const prompts = [];
  const entry = {
    session: {
      isStreaming: false,
      prompt: async (note) => { prompts.push(note); },
    },
    context: { side_effect_policy: "AUTO_READ_LOW" },
    concluded: false,
    runStopRequested: false,
    terminalReminderCount: 0,
    lastError: "",
  };
  manager.sessions.set("case-terminal", entry);

  manager._scheduleTerminalReminder("case-terminal", entry);
  await new Promise((resolve) => setTimeout(resolve, 5));
  manager._scheduleTerminalReminder("case-terminal", entry);
  await new Promise((resolve) => setTimeout(resolve, 5));

  assert.equal(prompts.length, 1);
  assert.match(prompts[0], /finish_investigation/);
  assert.equal(entry.terminalReminderCount, 1);
});

test("READ_ONLY catalog contains no proposal tools", () => {
  const tools = buildToolCatalog({ internalBase: "http://127.0.0.1:1", sideEffectPolicy: "READ_ONLY" });
  const names = tools.map((t) => t.name);
  assert.ok(names.includes("get_evidence_projection"));
  assert.ok(names.includes("get_dependency_graph"));
  assert.ok(names.includes("list_case_evidence"));
  assert.ok(names.includes("compare_evidence"));
  assert.ok(!names.includes("propose_collection"));
  assert.ok(names.includes("submit_evidence_analysis"));
  assert.ok(!names.includes("finish_investigation"));
});

test("internal tool calls send X-Internal-Token when configured", async () => {
  const originalFetch = globalThis.fetch;
  let capturedHeaders = null;
  let capturedBody = null;
  globalThis.fetch = async (_url, options) => {
    capturedHeaders = options.headers;
    capturedBody = options.body;
    return new Response(JSON.stringify({ ok: true, data: {} }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    process.env.MINI_DROP_PI_INTERNAL_TOKEN = "test-token";
    const tools = buildToolCatalog({
      internalBase: "http://127.0.0.1:1",
      getEnvelope: () => ({
        side_effect_policy: "READ_ONLY",
        runtime_generation: 7,
        expected_control_revision: 3,
        expected_scope_revision: 4,
      }),
    });
    const tool = tools.find((t) => t.name === "get_case_snapshot");
    await tool.execute("call-1", { case_id: "case-a" });
    assert.equal(capturedHeaders["X-Internal-Token"], "test-token");
    const sent = JSON.parse(capturedBody);
    assert.equal(sent.side_effect_policy, "READ_ONLY");
    assert.equal(sent.runtime_generation, 7);
    assert.equal(sent.expected_control_revision, 3);
    assert.equal(sent.expected_scope_revision, 4);
  } finally {
    delete process.env.MINI_DROP_PI_INTERNAL_TOKEN;
    globalThis.fetch = originalFetch;
  }
});

test("tool audit retry_count increments for repeated identical calls", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ ok: true, data: {} }), {
    status: 200, headers: { "Content-Type": "application/json" },
  });
  try {
    const counts = new Map();
    const tools = buildToolCatalog({
      internalBase: "http://127.0.0.1:1",
      getEnvelope: () => ({
        __get_retry_count: (_name, hash) => {
          const current = counts.get(hash) || 0;
          counts.set(hash, current + 1);
          return current;
        },
      }),
    });
    const tool = tools.find((item) => item.name === "get_case_snapshot");
    const first = await tool.execute("call-1", { case_id: "case-retry" });
    const second = await tool.execute("call-2", { case_id: "case-retry" });
    assert.equal(first.details.retry_count, 0);
    assert.equal(second.details.retry_count, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("canonical catalog metadata builds tools but cannot elevate runtime policy", () => {
  const catalog = {
    schema_version: "tool-catalog.v1",
    tools: ALLOWED_TOOL_NAMES.map((name) => ({
      name,
      description: `canonical ${name}`,
      parameters: { type: "object", additionalProperties: false },
      internal_path: `/internal/agent/tools/${name}`,
      enabled_by_default: true,
    })),
  };
  const tools = buildToolCatalog({
    catalog,
    runtimePolicy: { effective_tools: ["get_case_snapshot"] },
  });
  assert.deepEqual(tools.map((tool) => tool.name), ["get_case_snapshot"]);
  assert.equal(tools[0].description, "canonical get_case_snapshot");
});

test("catalog fetch validates the complete compatibility set and auth", async () => {
  const originalFetch = globalThis.fetch;
  process.env.MINI_DROP_PI_INTERNAL_TOKEN = "catalog-test";
  let receivedHeaders;
  globalThis.fetch = async (_url, options) => {
    receivedHeaders = options.headers;
    return new Response(JSON.stringify({ ok: true, data: {
      schema_version: "tool-catalog.v1",
      tools: ALLOWED_TOOL_NAMES.map((name) => ({
        name,
        internal_path: `/internal/agent/tools/${name}`,
      })),
    } }), { status: 200, headers: { "Content-Type": "application/json" } });
  };
  try {
    const catalog = await fetchToolCatalog({ internalBase: "http://control" });
    assert.equal(catalog.tools.length, ALLOWED_TOOL_NAMES.length);
    assert.equal(receivedHeaders["X-Internal-Token"], "catalog-test");
  } finally {
    globalThis.fetch = originalFetch;
    delete process.env.MINI_DROP_PI_INTERNAL_TOKEN;
  }
});

test("event spool survives restart and deletes only after ACK", () => {
  const root = mkdtempSync(join(tmpdir(), "mini-drop-spool-"));
  const path = join(root, "events.jsonl");
  try {
    const first = new EventSpool(path);
    first.append({
      case_id: "case-spool",
      runtime_generation: 2,
      event_seq: 9,
      event_type: "turn_end",
      idempotency_key: "event-spool-key",
    });
    const restarted = new EventSpool(path);
    assert.equal(restarted.pending("case-spool").length, 1);
    restarted.ack("event-spool-key");
    assert.equal(new EventSpool(path).pending().length, 0);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("restarted runtime replays spooled event and ACKs exactly once", async () => {
  const root = mkdtempSync(join(tmpdir(), "mini-drop-spool-replay-"));
  const path = join(root, "events.jsonl");
  const originalFetch = globalThis.fetch;
  try {
    new EventSpool(path).append({
      case_id: "case-replay",
      runtime_generation: 4,
      event_id: "evt-replay",
      event_seq: 11,
      event_type: "turn_end",
      payload: { text: "durable answer" },
      idempotency_key: "runtime-event:case-replay:4:11:turn_end",
    });
    const requests = [];
    globalThis.fetch = async (url, options) => {
      requests.push({ url, options });
      return new Response(JSON.stringify({ ok: true, data: { accepted: 1 } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    };
    process.env.MINI_DROP_PI_INTERNAL_TOKEN = "test-token";
    const manager = new RuntimeManager({
      modelRuntime: {},
      internalBase: "http://127.0.0.1:8191",
      eventSpool: new EventSpool(path),
    });
    manager.sessions.set("case-replay", { generation: 4, lastError: "" });
    await manager.replayPending("case-replay");
    await manager.replayPending("case-replay");
    assert.equal(requests.length, 1);
    assert.equal(JSON.parse(requests[0].options.body).events[0].event_id, "evt-replay");
    assert.equal(new EventSpool(path).pending().length, 0);
  } finally {
    delete process.env.MINI_DROP_PI_INTERNAL_TOKEN;
    globalThis.fetch = originalFetch;
    rmSync(root, { recursive: true, force: true });
  }
});

test("snapshot refresh keeps one session and updates active tool catalog", async () => {
  const manager = new RuntimeManager({
    modelRuntime: {},
    eventSpool: new EventSpool(null),
  });
  const activated = [];
  const session = {
    setActiveToolsByName(names) { activated.push(names); },
  };
  manager.sessions.set("case-refresh", {
    session,
    generation: 3,
    subscribed: true,
    toolEnvelope: { current: {} },
  });
  const binding = await manager.startOrResume({
    case_id: "case-refresh",
    runtime_generation: 3,
    side_effect_policy: "READ_ONLY",
    control_revision: 8,
    scope_revision: 9,
  });
  assert.equal(binding.runtime_generation, 3);
  assert.equal(manager.get("case-refresh").session, session);
  assert.equal(manager.get("case-refresh").subscribed, true);
  assert.equal(activated.length, 1);
  assert.equal(activated[0].includes("request_operation"), false);
  assert.equal(manager.get("case-refresh").toolEnvelope.current.runtime_generation, 3);
});

test("permission changes rotate the session option signature", () => {
  const manager = new RuntimeManager({ modelRuntime: {}, eventSpool: new EventSpool(null) });
  const readOnly = manager._optionSignature({
    runtime_options: { reasoning_effort: "high" },
    side_effect_policy: "READ_ONLY",
    runtime_policy: { effective_tools: ["get_case_snapshot"] },
  });
  const investigative = manager._optionSignature({
    runtime_options: { reasoning_effort: "high" },
    side_effect_policy: "AUTO_READ_LOW",
    runtime_policy: { effective_tools: ["finish_investigation", "get_case_snapshot"] },
  });
  assert.notEqual(readOnly, investigative);
});

test("runtime forwards non-thinking events and drops private thinking", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    return new Response(JSON.stringify({ ok: true, data: {} }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    process.env.MINI_DROP_PI_INTERNAL_TOKEN = "test-token";
    const manager = new RuntimeManager({
      modelRuntime: {},
      internalBase: "http://127.0.0.1:8191",
      eventSpool: new EventSpool(null),
    });
    manager.sessions.set("case-x", { generation: 2 });
    manager.lastSeq.set("case-x", 0);
    manager._observe("case-x", { type: "thinking.private", text: "secret" });
    assert.equal(manager.lastSeq.get("case-x"), 0);
    manager._observe("case-x", { type: "text_delta", text: "transient" });
    assert.equal(manager.lastSeq.get("case-x"), 0); // discarded SDK delta is not a cursor item
    await manager._forwardEvent("case-x", { generation: 2 }, { type: "assistant_message", text: "hello" });
    assert.equal(requests.length, 0); // seq 0 never forwarded
    manager._observe("case-x", { type: "message_end", message: { role: "assistant", content: [{ type: "text", text: "hello" }] } });
    assert.equal(manager.lastSeq.get("case-x"), 1);
    await manager._forwardEvent("case-x", { generation: 2 }, { type: "message_end", message: { role: "assistant", content: [{ type: "text", text: "hello" }] } });
    await manager._forwardEvent("case-x", { generation: 2 }, { type: "message_end", message: { role: "assistant", content: [{ type: "text", text: "hello" }] } });
    assert.equal(requests.length, 1); // idempotency dedupe
    const body = JSON.parse(requests[0].options.body);
    assert.equal(body.runtime_generation, 2);
    assert.equal(body.events[0].event_type, "message_end");
    assert.equal(JSON.parse(body.events[0].payload.message).content[0].text, "hello");
    assert.equal(requests[0].options.headers["X-Internal-Token"], "test-token");
  } finally {
    delete process.env.MINI_DROP_PI_INTERNAL_TOKEN;
    globalThis.fetch = originalFetch;
  }
});

test("case routes require X-Internal-Token when configured", async () => {
  const originalToken = process.env.MINI_DROP_PI_INTERNAL_TOKEN;
  process.env.MINI_DROP_PI_INTERNAL_TOKEN = "sidecar-test-token";
  try {
    const noToken = await fetch(`${base}/internal/runtime/v1/cases/x/state`);
    assert.equal(noToken.status, 401);
    const withToken = await fetch(`${base}/internal/runtime/v1/cases/x/state`, {
      headers: { "X-Internal-Token": "sidecar-test-token" },
    });
    assert.equal(withToken.status, 200);
  } finally {
    if (originalToken === undefined) delete process.env.MINI_DROP_PI_INTERNAL_TOKEN;
    else process.env.MINI_DROP_PI_INTERNAL_TOKEN = originalToken;
  }
});

test("runtime caches last final answer for repeat-question stability", async () => {
  const manager = new RuntimeManager({
    modelRuntime: {},
    internalBase: "http://127.0.0.1:8191",
    eventSpool: new EventSpool(null),
  });
  manager.sessions.set("case-x", { generation: 1 });
  manager.lastSeq.set("case-x", 1);
  process.env.MINI_DROP_PI_INTERNAL_TOKEN = "test-token";
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ ok: true, data: {} }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
  try {
    await manager._forwardEvent("case-x", { generation: 1 }, {
      type: "turn_end",
      message: { role: "assistant", content: [{ type: "text", text: "结论：CPU 饱和" }] },
    });
    assert.equal(manager.lastAnswers.get("case-x"), "结论：CPU 饱和");
  } finally {
    delete process.env.MINI_DROP_PI_INTERNAL_TOKEN;
    globalThis.fetch = originalFetch;
  }
});

test("message_end event carries per-call model usage and cost delta", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    return new Response(JSON.stringify({ ok: true, data: {} }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    process.env.MINI_DROP_PI_INTERNAL_TOKEN = "test-token";
    const manager = new RuntimeManager({
      modelRuntime: {},
      internalBase: "http://127.0.0.1:8191",
      eventSpool: new EventSpool(null),
    });
    const firstStats = { tokens: { input: 1000, output: 200, cacheRead: 50, cacheWrite: 20 }, cost: 0.01 };
    manager.sessions.set("case-usage", {
      generation: 1,
      currentTurnId: "turn-u1",
      context: {
        context_packet_id: "packet-u1",
        context_snapshot_id: "snap-u1",
        diagnostic_strategy_id: "hybrid",
        runtime_options: { reasoning_effort: "high" },
      },
      session: {
        model: { provider: "deepseek", id: "deepseek-v4-flash", name: "DeepSeek V4 Flash" },
        getSessionStats: () => firstStats,
      },
    });
    manager.lastSeq.set("case-usage", 0);
    manager._observe("case-usage", { type: "message_start" });
    await manager._forwardEvent("case-usage", manager.sessions.get("case-usage"), {
      type: "message_end",
      message: { role: "assistant", content: [{ type: "text", text: "first answer" }] },
    });
    assert.equal(requests.length, 1);
    const firstBody = JSON.parse(requests[0].options.body);
    const firstAttempt = firstBody.events[0].payload.model_attempt;
    assert.ok(firstAttempt, "message_end should carry model_attempt");
    assert.equal(firstAttempt.provider, "deepseek");
    assert.equal(firstAttempt.model, "deepseek-v4-flash");
    assert.equal(firstAttempt.input_tokens, 1000);
    assert.equal(firstAttempt.output_tokens, 200);
    assert.equal(firstAttempt.cache_read_tokens, 50);
    assert.equal(firstAttempt.cache_write_tokens, 20);
    assert.equal(firstAttempt.cost, 0.01);
    assert.equal(firstAttempt.turn_id, "turn-u1");
    assert.equal(firstAttempt.context_packet_id, "packet-u1");
    assert.equal(firstAttempt.context_snapshot_id, "snap-u1");
    assert.equal(firstAttempt.tool_catalog_version, "tool-catalog.v1");
    assert.ok(firstAttempt.config_fingerprint);
    assert.ok(firstAttempt.started_at);
    assert.ok(firstAttempt.finished_at);

    // Second model response: stats are cumulative, so the audit must be delta.
    const secondStats = { tokens: { input: 1600, output: 500, cacheRead: 80, cacheWrite: 40 }, cost: 0.023 };
    manager.sessions.get("case-usage").session.getSessionStats = () => secondStats;
    manager._observe("case-usage", { type: "message_start" });
    await manager._forwardEvent("case-usage", manager.sessions.get("case-usage"), {
      type: "message_end",
      message: { role: "assistant", content: [{ type: "text", text: "second answer" }] },
    });
    assert.equal(requests.length, 2);
    const secondBody = JSON.parse(requests[1].options.body);
    const secondAttempt = secondBody.events[0].payload.model_attempt;
    assert.equal(secondAttempt.input_tokens, 600);
    assert.equal(secondAttempt.output_tokens, 300);
    assert.equal(secondAttempt.cache_read_tokens, 30);
    assert.equal(secondAttempt.cache_write_tokens, 20);
    assert.ok(Math.abs(secondAttempt.cost - 0.013) < 1e-9);
  } finally {
    delete process.env.MINI_DROP_PI_INTERNAL_TOKEN;
    globalThis.fetch = originalFetch;
  }
});

test("model attempts only audit terminal assistant responses and dedupe responseId", async () => {
  const originalFetch = globalThis.fetch;
  const originalToken = process.env.MINI_DROP_PI_INTERNAL_TOKEN;
  const originalKey = process.env.DEEPSEEK_API_KEY;
  const requests = [];
  globalThis.fetch = async (_url, options) => {
    requests.push(JSON.parse(options.body));
    return new Response(JSON.stringify({ ok: true, data: {} }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    process.env.MINI_DROP_PI_INTERNAL_TOKEN = "test-token";
    process.env.DEEPSEEK_API_KEY = "credential-must-never-be-forwarded";
    const manager = new RuntimeManager({
      modelRuntime: {},
      internalBase: "http://127.0.0.1:8191",
      eventSpool: new EventSpool(null),
    });
    const entry = {
      generation: 1,
      currentTurnId: "turn-main",
      context: {
        context_packet_id: "packet-main",
        diagnostic_strategy_id: "hybrid",
        runtime_options: { reasoning_effort: "low" },
      },
      session: {
        model: { provider: "deepseek", id: "deepseek-v4-flash", name: "DeepSeek V4 Flash" },
        getSessionStats: () => ({
          tokens: { input: 120, output: 30, cacheRead: 10, cacheWrite: 0 },
          cost: 0.001,
        }),
      },
    };
    manager.sessions.set("case-model-audit", entry);
    manager.lastSeq.set("case-model-audit", 0);

    const forward = async (event) => {
      manager._observe("case-model-audit", event);
      await manager._forwardEvent("case-model-audit", entry, event);
    };
    await forward({
      type: "message_end",
      message: { role: "user", content: [{ type: "text", text: "terminal reminder" }] },
    });
    const response = {
      role: "assistant",
      provider: "deepseek",
      model: "deepseek-v4-flash",
      responseId: "provider-response-1",
      stopReason: "stop",
      usage: {
        input: 120,
        output: 30,
        cacheRead: 10,
        cacheWrite: 0,
        cost: { total: 0.001 },
      },
      content: [{ type: "text", text: "verified answer" }],
    };
    await forward({ type: "message_end", message: response });
    await forward({ type: "turn_end", message: response });
    await forward({
      type: "message_end",
      message: {
        role: "assistant",
        responseId: "provider-response-pending",
        stopReason: "pending",
        content: [],
      },
    });

    const attempts = requests.flatMap((body) => body.events)
      .map((event) => event.payload.model_attempt)
      .filter(Boolean);
    assert.equal(attempts.length, 1);
    assert.equal(attempts[0].turn_id, "turn-main");
    assert.equal(attempts[0].input_tokens, 120);
    assert.equal(attempts[0].output_tokens, 30);
    assert.equal(attempts[0].response_hash.length, 64);
    assert.match(attempts[0].model_attempt_id, /^model_attempt_pi_[0-9a-f]{24}$/);
    assert.equal("response_id" in attempts[0], false);
    assert.equal(
      JSON.stringify(requests).includes("credential-must-never-be-forwarded"),
      false,
    );
  } finally {
    globalThis.fetch = originalFetch;
    if (originalToken === undefined) delete process.env.MINI_DROP_PI_INTERNAL_TOKEN;
    else process.env.MINI_DROP_PI_INTERNAL_TOKEN = originalToken;
    if (originalKey === undefined) delete process.env.DEEPSEEK_API_KEY;
    else process.env.DEEPSEEK_API_KEY = originalKey;
  }
});

test("internal tool error includes server response detail", async () => {
  const originalFetch = globalThis.fetch;
  let capturedBody = null;
  globalThis.fetch = async (_url, options) => {
    capturedBody = options.body;
    return new Response(JSON.stringify({ detail: "OPERATION_DISABLED_BY_RUNTIME_POLICY" }), {
      status: 403,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    process.env.MINI_DROP_PI_INTERNAL_TOKEN = "test-token";
    const tools = buildToolCatalog({
      internalBase: "http://127.0.0.1:1",
      getEnvelope: () => ({ side_effect_policy: "PROPOSE_ONLY" }),
    });
    const tool = tools.find((t) => t.name === "propose_collection");
    const result = await tool.execute("call-1", {
      case_id: "case-a",
      collector_id: "sys_metrics",
      target_selector: { agent_id: "agent-a", target_pid: 1 },
      parameters: {},
      information_goal: "主机和目标进程资源饱和度",
    });
    const text = result.content[0].text;
    assert.match(text, /HTTP 403/);
    assert.match(text, /OPERATION_DISABLED_BY_RUNTIME_POLICY/);
    assert.equal(result.details.http_status, 403);
  } finally {
    delete process.env.MINI_DROP_PI_INTERNAL_TOKEN;
    globalThis.fetch = originalFetch;
  }
});
